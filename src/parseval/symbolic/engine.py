"""The symbolic engine — orchestrates branch-coverage-driven generation.

This is the top-level entry point for ParSEval's test-database generation.
Given an Instance and a SQL query, the engine:

1. Builds the Plan.
2. Evaluates the plan against the current instance to discover branches.
3. Identifies uncovered atom-outcome targets.
4. For each target: checks infeasibility, generates constraints, invokes
   the solver, materializes results, re-evaluates.
5. Repeats until coverage thresholds are met or budget is exhausted.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Set

from sqlglot import exp

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.plan.planner import Aggregate, Filter, Join, Project, Scan
from parseval.plan.rex import Variable
from parseval.query import preprocess_sql

from .constraints import ConstraintGenerator, SolverConstraint
from .evaluator import PlanEvaluator
from .infeasibility import is_infeasible
from .types import (
    BranchTree,
    BranchType,
    CoverageTarget,
    CoverageThresholds,
    GenerationResult,
)


def _get_inner_query_values(
    in_node: exp.In,
    instance: Instance,
    alias_map,
) -> list:
    """Evaluate the inner query of an IN expression against current instance.

    Pure query execution — no Z3 involved. Returns list of concrete values
    from the inner subquery's projected column.
    """
    from parseval.plan.rex import concrete, Environment
    from parseval.helper import normalize_name

    subq = in_node.find(exp.Subquery)
    if not subq:
        return []
    inner_select = subq.this
    if not isinstance(inner_select, exp.Select):
        return []
    from_clause = inner_select.args.get("from")
    if not from_clause:
        return []
    from_table = from_clause.this
    if not isinstance(from_table, exp.Table):
        return []
    table_name = normalize_name(from_table.alias_or_name)
    table_name = alias_map.get(table_name, table_name)
    if table_name not in instance.tables:
        return []
    projections = inner_select.expressions
    if not projections:
        return []
    proj_col = None
    for col in projections[0].find_all(exp.Column):
        proj_col = normalize_name(col.name)
        break
    if not proj_col:
        return []
    rows = instance.get_rows(table_name)
    where = inner_select.args.get("where")
    values = []
    for row in rows:
        if where:
            env = Environment({c: s.concrete for c, s in row.items()})
            if concrete(where.this, env) is not True:
                continue
        if proj_col in row.columns:
            v = row[proj_col].concrete
            if v is not None:
                values.append(v)
    return values


def _translate_non_subquery_parts(
    smt, condition: exp.Expression, ctx: dict
) -> None:
    """Translate parts of a condition that don't contain subqueries."""
    if isinstance(condition, exp.And):
        _translate_non_subquery_parts(smt, condition.left, ctx)
        _translate_non_subquery_parts(smt, condition.right, ctx)
    elif isinstance(condition, exp.Paren):
        _translate_non_subquery_parts(smt, condition.this, ctx)
    elif not condition.find(exp.Subquery):
        z3_p = smt.translate(condition, ctx=ctx)
        if z3_p is not None:
            smt.add_raw(z3_p)


def _add_not_in_constraints(
    smt, condition: exp.Expression, ctx: dict, instance: Instance, alias_map
) -> None:
    """Add NOT IN anti-value constraints from a condition."""
    from parseval.helper import normalize_name
    from parseval.solver.smt import encode_literal
    from sqlglot.expressions import DataType

    for in_node in condition.find_all(exp.In):
        if not in_node.find(exp.Subquery):
            continue
        parent = in_node.parent
        is_not_in = isinstance(parent, exp.Not)
        if not is_not_in:
            continue
        outer_col = in_node.this
        if not isinstance(outer_col, exp.Column):
            continue
        col_name = normalize_name(outer_col.name)
        alias_key = (
            f"{normalize_name(outer_col.table)}.{col_name}"
            if outer_col.table
            else None
        )
        var = ctx.get(alias_key) if alias_key else None
        if var is None:
            for k, v in ctx.items():
                if k.endswith(f".{col_name}"):
                    var = v
                    break
        if var is None:
            continue
        inner_vals = _get_inner_query_values(in_node, instance, alias_map)
        col_type = DataType.build(str(instance.tables.get(
            alias_map.get(normalize_name(outer_col.table or ""), ""),
            {}
        ).get(col_name, "TEXT")))
        for v in inner_vals:
            try:
                const = encode_literal(col_type, v, smt.z3ctx).expr
                smt.add_raw(var != const)
            except Exception:
                pass


class SymbolicEngine:
    """Drive test-database generation to cover all branches of a query plan.

    Usage::

        engine = SymbolicEngine(instance, sql, dialect="sqlite")
        result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
        print(result.coverage, result.rows_generated)
    """

    def __init__(
        self,
        instance: Instance,
        sql: str,
        dialect: str = "sqlite",
        *,
        solver = None,
        max_iterations: int = 50,
        max_rows_per_table: Optional[int] = None,
    ):
        self.instance = instance
        self.sql = sql
        self.dialect = dialect
        self.expr = preprocess_sql(sql, instance, dialect=dialect)
        self.plan = Plan(self.expr)
        from parseval.solver.unified import Solver; self.solver = solver or Solver(instance, dialect=dialect)
        self.max_iterations = max_iterations
        # Build alias → real table name mapping from the Plan's Scan steps.
        self.alias_map = _build_alias_map(self.plan)
        # Dynamic row budget: scale with query complexity if not specified.
        if max_rows_per_table is not None:
            self.max_rows_per_table = max_rows_per_table
        else:
            self.max_rows_per_table = _compute_row_budget(self.plan)

    def generate(
        self,
        thresholds: Optional[CoverageThresholds] = None,
    ) -> GenerationResult:
        """Run the generation loop until coverage is met or budget exhausted.

        Flow:
        Phase 0: Build + resolve a witness (coordinated rows that make the
                 query return non-empty results). Handles JOINs, FKs, WHERE,
                 SELECT-list, subqueries all at once.
        Phase 1: Evaluate coverage on the witness rows.
        Phase 2: For each uncovered atom-outcome, build a targeted witness
                 and materialize it.
        Phase 3: Re-evaluate. Repeat Phase 2 until covered or budget exhausted.
        """
        from .speculate import build_spec, resolve_spec

        thresholds = thresholds or CoverageThresholds()
        self._thresholds = thresholds
        evaluator = PlanEvaluator(self.plan, self.instance, self.dialect)
        constraint_gen = ConstraintGenerator(self.plan, self.instance, self.dialect)

        rows_before = self._total_rows()

        # Phase 0: Speculate all branches (positive + negatives) at once.
        # This is the primary generation path — handles self-joins, HAVING
        # COUNT, NOT IN, and all common patterns via heuristics.
        self._speculate_all_branches()

        # Phase 0b: Ensure every alias has a row (self-join aware).
        self._ensure_base_rows()

        # Phase 0c: If query has OFFSET, generate enough rows.
        self._seed_for_offset()

        # Phase 0d: Handle subquery predicates.
        self._resolve_subquery_predicates()

        # Phase 0e: Create coordinated rows for HAVING COUNT > N.
        self._create_having_count_rows()

        # Phase 0f: SMT repair — for self-joins, NOT IN, and complex OR
        # predicates that the speculative layer cannot handle.
        self._smt_repair_where()

        # Phase 0g: Targeted enrichment for DISTINCT/GROUP BY/aggregate patterns.
        self._enrich_for_semantics()

        # Phase 1: Initial evaluation.
        tree = BranchTree(thresholds=thresholds)
        tree = evaluator.evaluate(tree)

        iteration = 0

        for iteration in range(self.max_iterations):
            if tree.fully_covered:
                break

            targets = tree.uncovered_targets
            if not targets:
                break

            target = self._prioritize(targets)

            # Quick infeasibility check.
            reason = is_infeasible(
                target.node, target.atom_id, target.target_outcome, self.instance
            )
            if reason is not None:
                tree.mark_infeasible(target.node, target.atom_id, target.target_outcome)
                continue

            # Check row budget.
            if self._total_rows() - rows_before >= self.max_rows_per_table * len(self.instance.tables):
                break

            # Generate constraints.
            constraint = constraint_gen.generate(target)

            # Solve and materialize.
            cp = self.instance.checkpoint()
            success = self._solve_and_materialize(constraint)

            if success:
                # Re-evaluate to discover newly covered branches.
                tree = evaluator.evaluate(tree)
            else:
                self.instance.rollback(cp)
                tree.mark_infeasible(target.node, target.atom_id, target.target_outcome)

        return GenerationResult(
            tree=tree,
            iterations=iteration + 1,
            rows_generated=self._total_rows() - rows_before,
        )

    def _prioritize(self, targets: List[CoverageTarget]) -> CoverageTarget:
        """Select the highest-priority uncovered target.

        Priority:
        1. ATOM_TRUE / ATOM_FALSE (basic branch coverage)
        2. ATOM_NULL (3VL edge cases)
        3. Filter sites before Join before Having before Case
        """
        site_priority = {"filter": 0, "join_on": 1, "having": 2, "case_arm": 3, "group": 4}
        outcome_priority = {
            BranchType.ATOM_TRUE: 0,
            BranchType.ATOM_FALSE: 1,
            BranchType.ATOM_NULL: 2,
        }

        def key(t: CoverageTarget) -> tuple:
            return (
                outcome_priority.get(t.target_outcome, 9),
                site_priority.get(t.node.site, 9),
            )

        return min(targets, key=key)

    def _solve_and_materialize(self, constraint: SolverConstraint) -> bool:
        """Invoke the unified solver and materialize results into the instance.

        All materialization goes through ``create_row`` which enforces
        database constraints (NOT NULL, UNIQUE, FK). FK columns are
        pre-filled from existing parent rows to ensure referential
        integrity. If constraints can't be satisfied, returns False.
        """
        result = self.solver.solve(constraint)
        if not result.sat:
            return False
        for table_name, row_values in result.assignments.items():
            real_table = self.alias_map.get(table_name, table_name)
            if real_table not in self.instance.tables:
                continue
            # Pre-fill FK columns from existing parent rows.
            row_values = self._fill_fk_values(real_table, row_values)
            try:
                self.instance.create_row(real_table, values=row_values)
            except Exception:
                return False
        return True

    def _fill_fk_values(self, table_name: str, values: Dict[str, Any]) -> Dict[str, Any]:
        """Ensure FK columns reference existing parent rows.

        For each FK on ``table_name``, if the FK column isn't already in
        ``values``, pick a value from an existing parent row. If no parent
        rows exist, create one first.
        """
        from parseval.helper import normalize_name

        values = dict(values)  # don't mutate the original
        for fk in self.instance.get_foreign_key(table_name):
            local_col = normalize_name(fk.expressions[0].name)
            if local_col in values and values[local_col] is not None:
                continue  # Already provided.
            ref = fk.args.get("reference")
            if ref is None:
                continue
            ref_table_node = ref.find(exp.Table)
            if ref_table_node is None:
                continue
            ref_table = normalize_name(ref_table_node.name)
            ref_col = self.instance.resolve_fk_ref_column(fk)
            if ref_col is None:
                continue

            # Get existing parent values.
            parent_rows = self.instance.get_rows(ref_table)
            if not parent_rows:
                # Create a parent row first.
                try:
                    self.instance.create_row(ref_table, values={})
                    parent_rows = self.instance.get_rows(ref_table)
                except Exception:
                    continue

            if parent_rows:
                # Pick the last parent's key value.
                parent_key = parent_rows[-1][ref_col].concrete
                if parent_key is not None:
                    values[local_col] = parent_key

        return values

    def _speculate(self, target: str = "positive", negate_atom=None) -> bool:
        """Backward-compat single-branch speculate."""
        from .speculate import build_spec, resolve_spec
        spec = build_spec(self.plan, self.instance, alias_map=self.alias_map, target_outcome=target, negate_atom=negate_atom)
        if not spec.requirements:
            return False
        assignments = resolve_spec(spec, self.instance, self.dialect)
        success = False
        for table, row_values in assignments.items():
            if table not in self.instance.tables:
                continue
            fk_fill = self._fill_fk_values(table, {})
            for col, val in fk_fill.items():
                if col not in row_values:
                    row_values[col] = val
            try:
                self.instance.create_row(table, values=row_values)
                success = True
            except Exception:
                pass
        return success

    def _speculate_all_branches(self) -> None:
        """Generate rows for ALL branches (positive + negatives) at once."""
        from .speculate import speculate
        branch_results = speculate(self.plan, self.instance, self.alias_map, self.dialect)
        for branch_name, rows_per_table in branch_results:
            for table, row_list in rows_per_table.items():
                if table not in self.instance.tables:
                    continue
                for row_values in row_list:
                    fk_fill = self._fill_fk_values(table, {})
                    for col, val in fk_fill.items():
                        if col not in row_values:
                            row_values[col] = val
                    try:
                        self.instance.create_row(table, values=row_values)
                    except Exception:
                        pass

    def _total_rows(self) -> int:
        return sum(len(self.instance.get_rows(t)) for t in self.instance.tables)

    def _seed_for_offset(self) -> None:
        """If the query has LIMIT with OFFSET, generate enough rows."""
        from parseval.plan.planner import Limit as LimitStep
        for step in self.plan.ordered_steps:
            if isinstance(step, LimitStep):
                offset = getattr(step, "offset", 0) or 0
                if offset > 0:
                    needed = offset + int(step.limit if step.limit != float("inf") else 1)
                    main_table = next(
                        (v for v in self.alias_map.values() if v in self.instance.tables), ""
                    )
                    if not main_table:
                        return
                    current = len(self.instance.get_rows(main_table))
                    # Cap at 500 rows to avoid excessive generation time.
                    to_create = min(needed - current, 500 - current)
                    for _ in range(max(to_create, 0)):
                        try:
                            self.instance.create_row(main_table, values={})
                        except Exception:
                            break


    def _smt_repair_where(self) -> None:
        """Use Z3 to solve the full WHERE clause and repair rows.

        Self-join aware: when multiple aliases reference the same table,
        ensures separate rows exist and solves per-alias constraints
        independently.
        """
        from parseval.plan.planner import Filter, Join
        from parseval.plan.rex import concrete, Environment
        from parseval.helper import normalize_name

        # Ensure rows exist for self-joins
        if self.alias_map.self_join_tables():
            self.alias_map.ensure_rows_exist(self.instance)

        for step in self.plan.ordered_steps:
            if not isinstance(step, Filter) or step.condition is None:
                continue

            condition = step.condition
            has_subquery = bool(condition.find(exp.Subquery))

            # Quick satisfaction check: if predicate is already satisfied, skip
            condition_aliases = set()
            for col in condition.find_all(exp.Column):
                if col.table:
                    condition_aliases.add(normalize_name(col.table))

            if not condition_aliases:
                continue

            # Build environment from current rows and check
            env = Environment()
            all_satisfied = False
            for alias in condition_aliases:
                table = normalize_name(self.alias_map.get(alias, alias))
                if table not in self.instance.tables:
                    continue
                row_idx = self.alias_map.row_index(alias)
                rows = self.instance.get_rows(table)
                if row_idx < len(rows):
                    for col_name, sym in rows[row_idx].items():
                        env.bind(f"{alias}.{col_name}", sym.concrete)
                        env.bind(col_name, sym.concrete)
            if not has_subquery:
                result = concrete(condition, env)
                if result is True:
                    continue  # Already satisfied, skip SMT

            # Check if this specific condition involves self-join aliases
            self_join_tables = self.alias_map.self_join_tables()
            condition_has_self_join = any(
                len([a for a in aliases if a in condition_aliases]) >= 2
                for aliases in self_join_tables.values()
            )

            # Skip conditions with subqueries unless it's NOT IN
            if has_subquery:
                has_not_in = any(
                    isinstance(n.parent, exp.Not) for n in condition.find_all(exp.In)
                    if n.find(exp.Subquery)
                )
                if has_not_in:
                    self._repair_not_in_simple(condition)
                continue

            # Collect aliases referenced in the condition
            aliases_in_condition = set()
            for col in condition.find_all(exp.Column):
                if col.table:
                    aliases_in_condition.add(normalize_name(col.table))

            if not aliases_in_condition:
                continue

            # For non-self-join conditions, use the simple SMT path
            if not condition_has_self_join:
                # Simple path: check if satisfied, if not use _try_smt
                env = Environment()
                for alias in aliases_in_condition:
                    table = normalize_name(self.alias_map.get(alias, alias))
                    if table not in self.instance.tables:
                        continue
                    rows = self.instance.get_rows(table)
                    if rows:
                        for col_name, sym in rows[0].items():
                            env.bind(f"{alias}.{col_name}", sym.concrete)
                            env.bind(f"{table}.{col_name}", sym.concrete)
                            env.bind(col_name, sym.concrete)
                if concrete(condition, env) is True:
                    continue
                try:
                    from parseval.solver.unified import _try_smt
                    from parseval.symbolic.constraints import SolverConstraint
                    from parseval.symbolic.types import BranchType
                    tables_in_condition = set(
                        normalize_name(self.alias_map.get(a, a)) for a in aliases_in_condition
                    ) & set(self.instance.tables.keys())
                    smt_constraint = SolverConstraint(
                        target_tables=tuple(tables_in_condition),
                        atom=condition,
                        target_outcome=BranchType.ATOM_TRUE,
                        path_predicates=[], join_equalities=[],
                    )
                    smt_result = _try_smt(smt_constraint, self.instance, self.dialect, timeout_ms=3000)
                    if smt_result:
                        for table, col_values in smt_result.items():
                            real_table = normalize_name(self.alias_map.get(table, table))
                            if real_table not in self.instance.tables:
                                continue
                            rows = self.instance.get_rows(real_table)
                            if not rows:
                                continue
                            for col, val in col_values.items():
                                matched_col = next(
                                    (c for c in self.instance.tables[real_table] if c.lower() == col.lower()), None
                                )
                                if matched_col and matched_col in rows[0].columns and val is not None:
                                    rows[0][matched_col].set("concrete", val)
                                    rows[0][matched_col].set("is_bound", True)
                                    rows[0][matched_col].set("is_null", False)
                except Exception:
                    pass
                continue

            # Self-join path: use per-alias Z3 context
            env = Environment()
            for alias in aliases_in_condition:
                table = self.alias_map.get(alias, alias)
                table = normalize_name(table)
                if table not in self.instance.tables:
                    continue
                row_idx = self.alias_map.row_index(alias)
                rows = self.instance.get_rows(table)
                if row_idx >= len(rows):
                    continue
                row = rows[row_idx]
                for col_name, sym in row.items():
                    env.bind(f"{alias}.{col_name}", sym.concrete)
                    env.bind(f"{table}.{col_name}", sym.concrete)
                    if alias == normalize_name(list(aliases_in_condition)[0]):
                        env.bind(col_name, sym.concrete)

            result = concrete(condition, env)
            if result is True:
                continue

            # Not satisfied — use SMTSolver with per-alias awareness
            try:
                from parseval.solver.smt import SMTSolver

                smt = SMTSolver(variables=[], timeout_ms=10000, instance=self.instance)
                ctx: Dict[str, Any] = {}
                var_symbols: Dict[str, Any] = {}

                # Include all aliases involved in JOINs with WHERE aliases
                all_aliases = set(aliases_in_condition)
                for jstep in self.plan.ordered_steps:
                    if not isinstance(jstep, Join):
                        continue
                    src_alias = normalize_name(jstep.source_name or jstep.name)
                    for jn in (jstep.joins or {}):
                        jn_alias = normalize_name(jn)
                        if src_alias in aliases_in_condition or jn_alias in aliases_in_condition:
                            all_aliases.add(src_alias)
                            all_aliases.add(jn_alias)

                # Declare per-alias variables
                for alias in all_aliases:
                    table = normalize_name(self.alias_map.get(alias, alias))
                    if table not in self.instance.tables:
                        continue
                    row_idx = self.alias_map.row_index(alias)
                    rows = self.instance.get_rows(table)
                    if row_idx >= len(rows):
                        continue
                    row = rows[row_idx]
                    for col_name, sym in row.items():
                        var_name = f"{alias}[{row_idx}].{col_name}"
                        datatype = smt.col_sort_datatype(table, col_name)
                        var = smt.declare_variable(var_name, datatype)
                        ctx[f"{alias}.{col_name}"] = var
                        var_symbols[var_name] = sym

                # Translate and solve
                if has_subquery:
                    _translate_non_subquery_parts(smt, condition, ctx)
                    _add_not_in_constraints(smt, condition, ctx, self.instance, self.alias_map)
                else:
                    z3_pred = smt.translate(condition, ctx=ctx)
                    if z3_pred is not None:
                        smt.add_raw(z3_pred)

                # Add JOIN constraints between aliases
                for jstep in self.plan.ordered_steps:
                    if not isinstance(jstep, Join):
                        continue
                    src_alias = normalize_name(jstep.source_name or jstep.name)
                    for jn, jd in (jstep.joins or {}).items():
                        jn_alias = normalize_name(jn)
                        for sk, jk in zip(jd.get("source_key", []), jd.get("join_key", [])):
                            sk_name = normalize_name(sk.name if hasattr(sk, "name") else str(sk))
                            jk_name = normalize_name(jk.name if hasattr(jk, "name") else str(jk))
                            sk_key = f"{src_alias}.{sk_name}"
                            jk_key = f"{jn_alias}.{jk_name}"
                            if sk_key in ctx and jk_key in ctx:
                                try:
                                    smt.add_raw(ctx[sk_key] == ctx[jk_key])
                                except Exception:
                                    pass

                # Self-join: distinct PK values
                for table, aliases in self.alias_map.self_join_tables().items():
                    active = [a for a in aliases if a in aliases_in_condition]
                    if len(active) < 2:
                        continue
                    pk_col = next(
                        (c for c in self.instance.tables.get(table, {}) if 'id' in c.lower()),
                        None
                    )
                    if pk_col:
                        for i in range(len(active)):
                            for j in range(i + 1, len(active)):
                                ki = f"{active[i]}.{pk_col}"
                                kj = f"{active[j]}.{pk_col}"
                                if ki in ctx and kj in ctx:
                                    try:
                                        smt.add_raw(ctx[ki] != ctx[kj])
                                    except Exception:
                                        pass

                # Solve and apply
                status, solution = smt.solve_raw(var_symbols)
                if status == "sat":
                    SMTSolver.apply_solution(var_symbols, solution)
            except Exception:
                pass

    def _resolve_subquery_predicates(self) -> None:
        """Evaluate subquery predicates against the instance and adjust rows.

        For predicates like ``outer_expr > (SELECT AVG(...) FROM ...)``:
        1. Execute the subquery against the current instance data.
        2. Get the scalar result.
        3. Create/update an outer row that satisfies the comparison.

        This handles the case where the witness can't statically determine
        what value the subquery will produce — we generate data first,
        then adapt.
        """

        for step in self.plan.ordered_steps:
            if not isinstance(step, Filter) or step.condition is None:
                continue

            # Find atoms that contain subqueries.
            for atom in self._iter_subquery_atoms(step.condition):
                self._resolve_one_subquery_atom(atom)

    def _iter_subquery_atoms(self, predicate: exp.Expression):
        """Yield atoms that contain a subquery comparison."""
        if isinstance(predicate, exp.And):
            yield from self._iter_subquery_atoms(predicate.left)
            yield from self._iter_subquery_atoms(predicate.right)
        elif isinstance(predicate, exp.Paren):
            yield from self._iter_subquery_atoms(predicate.this)
        elif isinstance(predicate, exp.Or):
            yield from self._iter_subquery_atoms(predicate.left)
        else:
            # Check if this atom has a subquery
            if predicate.find(exp.Subquery):
                yield predicate

    def _resolve_one_subquery_atom(self, atom: exp.Expression) -> None:
        """Resolve a subquery atom using our own plan evaluator.

        For col OP (SELECT ...): evaluate the subquery against the current
        instance using concrete(), then adjust the outer row to satisfy.
        """
        if not isinstance(atom, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
            return

        left, right = atom.this, atom.expression
        subq_side, outer_side = None, None
        if right and right.find(exp.Subquery):
            subq_side, outer_side = right, left
        elif left and left.find(exp.Subquery):
            subq_side, outer_side = left, right
        if subq_side is None:
            return

        # Evaluate the subquery against the current instance.
        subq_value = self._evaluate_subquery(subq_side)
        if subq_value is None:
            return

        # Find the outer column(s).
        outer_columns = list(outer_side.find_all(exp.Column))
        if not outer_columns:
            return

        target_col = outer_columns[0]
        table = (target_col.table or "").lower()
        table = self.alias_map.get(table, table)
        if table not in self.instance.tables:
            return

        col_name = target_col.name.lower()
        matched_col = next(
            (s for s in self.instance.tables[table] if s.lower() == col_name), None
        )
        if not matched_col:
            return

        # Determine needed value based on comparison operator.
        if isinstance(atom, exp.GT):
            needed = subq_value + 1 if isinstance(subq_value, (int, float)) else subq_value
        elif isinstance(atom, exp.GTE):
            needed = subq_value
        elif isinstance(atom, exp.LT):
            needed = subq_value - 1 if isinstance(subq_value, (int, float)) else subq_value
        elif isinstance(atom, exp.LTE):
            needed = subq_value
        elif isinstance(atom, exp.EQ):
            needed = subq_value
        else:
            return

        # For compound expressions (col1 - col2): set col1 high, col2 low.
        if len(outer_columns) >= 2 and isinstance(atom, (exp.GT, exp.GTE)):
            values = {matched_col: abs(subq_value) + 1000 if isinstance(subq_value, (int, float)) else 1000}
            col2 = outer_columns[1]
            col2_matched = next(
                (s for s in self.instance.tables[table] if s.lower() == col2.name.lower()), None
            )
            if col2_matched:
                values[col2_matched] = 0
        else:
            values = {matched_col: needed}

        # Update existing row or create new one.
        existing_rows = self.instance.get_rows(table)
        if existing_rows:
            row = existing_rows[0]
            for col, val in values.items():
                if col in row.columns and val is not None:
                    row[col].set("concrete", val)
            # Also create a second row with low values for aggregate subqueries.
            if len(outer_columns) >= 2:
                low_values = {col: 0 for col, val in values.items() if isinstance(val, (int, float))}
                if low_values:
                    low_values = self._fill_fk_values(table, low_values)
                    try:
                        self.instance.create_row(table, values=low_values)
                    except Exception:
                        pass

    def _evaluate_subquery(self, subq_expr: exp.Expression) -> Any:
        """Evaluate a subquery expression against the current instance.

        Uses concrete() with an Environment built from instance rows.
        Falls back to direct row scanning for simple patterns.
        """
        from parseval.plan.rex import concrete, Environment
        from parseval.plan.context import build_context_from_instance

        subq_node = subq_expr.find(exp.Subquery) or subq_expr
        inner_select = subq_node.this if isinstance(subq_node, exp.Subquery) else subq_node

        if not isinstance(inner_select, exp.Select):
            return None

        # Get the FROM table.
        from_clause = inner_select.args.get("from")
        if not from_clause:
            return None
        from_table = from_clause.this
        if not isinstance(from_table, exp.Table):
            return None

        table_name = from_table.alias_or_name.lower()
        table_name = self.alias_map.get(table_name, table_name)
        if table_name not in self.instance.tables:
            return None

        rows = self.instance.get_rows(table_name)
        if not rows:
            return None

        # Get the SELECT expression.
        projections = inner_select.expressions
        if not projections:
            return None
        select_expr = projections[0]
        if isinstance(select_expr, exp.Alias):
            select_expr = select_expr.this

        # Evaluate the WHERE clause to filter rows.
        where = inner_select.args.get("where")
        passing_rows = []
        for row in rows:
            env = Environment({
                col: sym.concrete for col, sym in row.items()
            })
            # Also add table-qualified names.
            for col, sym in row.items():
                env.bind(f"{table_name}.{col}", sym.concrete)
            if where:
                cond_val = concrete(where.this, env)
                if cond_val is not True:
                    continue
            passing_rows.append(row)

        if not passing_rows:
            return None

        # Evaluate the SELECT expression on passing rows.
        values = []
        for row in passing_rows:
            env = Environment({col: sym.concrete for col, sym in row.items()})
            for col, sym in row.items():
                env.bind(f"{table_name}.{col}", sym.concrete)
            val = concrete(select_expr, env)
            if val is not None:
                values.append(val)

        if not values:
            return None

        # Handle aggregates.
        if select_expr.find(exp.AggFunc):
            if select_expr.find(exp.Max):
                return max(values)
            elif select_expr.find(exp.Min):
                return min(values)
            elif select_expr.find(exp.Avg):
                return sum(values) / len(values)
            elif select_expr.find(exp.Sum):
                return sum(values)
            elif select_expr.find(exp.Count):
                return len(values)
            # Compound: MAX(...) - MIN(...)
            return values[0]

        # Non-aggregate: return first value (for LIMIT 1 patterns).
        # Handle ORDER BY + LIMIT.
        order = inner_select.args.get("order")
        if order:
            # Sort values (simplified: just return first/last based on DESC).
            desc = any(o.args.get("desc") for o in order.expressions)
            values.sort(reverse=bool(desc))

        return values[0] if values else None

    def _ensure_base_rows(self) -> None:
        """Ensure every alias has a row (self-join aware).
        
        For self-joins, creates separate rows per alias so each alias
        binds to a distinct row in the physical table.
        """
        self.alias_map.ensure_rows_exist(self.instance)
        # Also ensure FK-linked rows exist
        for alias, real_table in self.alias_map.items():
            if real_table not in self.instance.tables:
                continue
            if not self.instance.get_rows(real_table):
                try:
                    values = self._fill_fk_values(real_table, {})
                    self.instance.create_row(real_table, values=values)
                except Exception:
                    pass

    def _repair_not_in_simple(self, condition: exp.Expression) -> None:
        """Simple NOT IN repair: set the outer column to a fresh value not in inner results."""
        from parseval.helper import normalize_name
        from parseval.plan.rex import concrete, Environment

        for in_node in condition.find_all(exp.In):
            if not in_node.find(exp.Subquery):
                continue
            if not isinstance(in_node.parent, exp.Not):
                continue
            outer_col = in_node.this
            if not isinstance(outer_col, exp.Column):
                continue

            # Find the outer column's table and row
            alias = normalize_name(outer_col.table or "")
            table = normalize_name(self.alias_map.get(alias, alias))
            if table not in self.instance.tables:
                continue
            col_name = normalize_name(outer_col.name)
            if col_name not in self.instance.tables[table]:
                continue

            rows = self.instance.get_rows(table)
            if not rows:
                continue

            # Get inner query values
            inner_vals = set(_get_inner_query_values(in_node, self.instance, self.alias_map))

            # Find a value not in inner_vals and set it on the first row
            row = rows[0]
            current = row[col_name].concrete if col_name in row.columns else None
            if current not in inner_vals:
                continue  # Already satisfies NOT IN

            # Generate a fresh value
            col_type = str(self.instance.tables[table].get(col_name, "TEXT")).upper()
            if "INT" in col_type:
                fresh = max((v for v in inner_vals if isinstance(v, int)), default=0) + 9999
            else:
                fresh = f"__fresh_{col_name}__"
                i = 0
                while fresh in inner_vals:
                    i += 1
                    fresh = f"__fresh_{col_name}_{i}__"

            row[col_name].set("concrete", fresh)
            row[col_name].set("is_bound", True)

        # Also fix non-subquery parts of the condition (e.g., year = 2017)
        # by applying them directly
        self._apply_non_subquery_literals(condition)

    def _apply_non_subquery_literals(self, condition: exp.Expression) -> None:
        """Apply simple literal equality constraints from a condition."""
        from parseval.helper import normalize_name
        from parseval.plan.rex import concrete as _concrete

        if isinstance(condition, exp.And):
            self._apply_non_subquery_literals(condition.left)
            self._apply_non_subquery_literals(condition.right)
        elif isinstance(condition, exp.Paren):
            self._apply_non_subquery_literals(condition.this)
        elif isinstance(condition, exp.EQ) and not condition.find(exp.Subquery):
            left, right = condition.this, condition.expression
            col = left if isinstance(left, exp.Column) else (right if isinstance(right, exp.Column) else None)
            lit = right if isinstance(left, exp.Column) else left
            if col and not isinstance(lit, exp.Column):
                val = _concrete(lit)
                if val is not None:
                    alias = normalize_name(col.table or "")
                    table = normalize_name(self.alias_map.get(alias, alias))
                    col_name = normalize_name(col.name)
                    if table in self.instance.tables and col_name in self.instance.tables[table]:
                        rows = self.instance.get_rows(table)
                        if rows:
                            rows[0][col_name].set("concrete", val)
                            rows[0][col_name].set("is_bound", True)

    def _create_having_count_rows(self) -> None:
        """Create coordinated rows for HAVING COUNT > N requirements."""
        from parseval.plan.planner import Aggregate
        from parseval.plan.rex import concrete as _concrete
        from parseval.helper import normalize_name        # Find HAVING COUNT threshold from Aggregate.aggregations
        count_threshold = 0
        for step in self.plan.ordered_steps:
            if not isinstance(step, Aggregate):
                continue
            for agg_expr in step.aggregations:
                for node in agg_expr.find_all((exp.GT, exp.GTE)):
                    if node.this.find(exp.Count):
                        val = _concrete(node.expression)
                        if isinstance(val, (int, float)):
                            t = int(val) + (1 if isinstance(node, exp.GT) else 0)
                            count_threshold = max(count_threshold, t)

        if count_threshold <= 1:
            return

        # Find the joined (child) table and its FK to the parent
        join_info = []
        for step in self.plan.ordered_steps:
            if not isinstance(step, Join):
                continue
            source_table = self.alias_map.get(
                normalize_name(step.source_name or step.name), ""
            )
            for join_name, join_data in (step.joins or {}).items():
                join_table = self.alias_map.get(normalize_name(join_name), "")
                for sk, jk in zip(join_data.get("source_key", []), join_data.get("join_key", [])):
                    sk_name = normalize_name(sk.name if hasattr(sk, "name") else str(sk))
                    jk_name = normalize_name(jk.name if hasattr(jk, "name") else str(jk))
                    join_info.append((join_table, jk_name, source_table, sk_name))

        if not join_info:
            return

        # Use the first join relationship
        child_table, child_fk, parent_table, parent_pk = join_info[0]
        if child_table not in self.instance.tables:
            return

        # Get the parent key value (from the first parent row)
        parent_rows = self.instance.get_rows(parent_table)
        if not parent_rows:
            return
        fk_value = parent_rows[0][parent_pk].concrete if parent_pk in parent_rows[0].columns else None
        if fk_value is None:
            return

        # Create enough child rows with the same FK value
        existing = len(self.instance.get_rows(child_table))
        for _ in range(max(0, count_threshold - existing)):
            try:
                self.instance.create_row(child_table, values={child_fk: fk_value})
            except Exception:
                pass

    def _enrich_for_semantics(self) -> None:
        """Generate additional rows with duplicates and NULLs to expose semantic differences."""
        from collections import defaultdict
        from .enrichment import analyze_plan_for_enrichment

        targets = analyze_plan_for_enrichment(self.plan)
        if not targets.duplicate_columns and not targets.null_columns:
            return

        dup_count = getattr(self._thresholds, 'atom_dup', 1)

        # Generate rows with NULLs for COUNT/SUM/AVG columns
        for table, column in targets.null_columns:
            real_table = self.alias_map.get(table, table)
            if real_table not in self.instance.tables:
                continue
            if not self.instance.nullable(real_table, column):
                continue
            self._generate_null_row(real_table, column)

        # Group duplicate targets by resolved table name
        dup_by_table: Dict[str, List[str]] = defaultdict(list)
        for table, column in targets.duplicate_columns:
            real_table = self.alias_map.get(table, table)
            if real_table in self.instance.tables:
                dup_by_table[real_table].append(column)

        # For each table, generate atom_dup rows with ALL target columns
        # duplicated at once — one create_row call per iteration, not per column.
        for real_table, columns in dup_by_table.items():
            existing_rows = self.instance.get_rows(real_table)
            if not existing_rows:
                continue
            source = existing_rows[0]
            dup_values = {}
            for col in columns:
                try:
                    val = source[col]
                except KeyError:
                    continue
                if val is not None and val.concrete is not None:
                    dup_values[col] = val.concrete
            if not dup_values:
                continue
            for _ in range(dup_count):
                try:
                    self.instance.create_row(real_table, values=dup_values)
                except Exception:
                    pass

    def _generate_null_row(self, table: str, null_column: str) -> None:
        """Generate a row with NULL in the specified column."""
        try:
            self.instance.create_row(table, values={null_column: None})
        except Exception:
            pass  # Schema constraint may prevent NULL


# =============================================================================
# Alias resolution
# =============================================================================


class AliasMap(dict):
    """Alias → physical table mapping that also tracks per-alias row indices.

    Backward-compatible with Dict[str, str] (alias → table_name).
    Additionally tracks which row index each alias should bind to when
    multiple aliases reference the same physical table (self-joins).

    Usage:
        alias_map['t1']  → 'superhero'  (dict-compatible)
        alias_map['t2']  → 'colour'
        alias_map['t3']  → 'colour'
        alias_map.row_index('t2')  → 0  (first row of colour)
        alias_map.row_index('t3')  → 1  (second row of colour)
        alias_map.self_join_aliases('colour')  → ['t2', 't3']
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._row_indices: Dict[str, int] = {}
        self._compute_row_indices()

    def _compute_row_indices(self):
        """Assign row indices: each alias to the same table gets a unique index."""
        table_counters: Dict[str, int] = {}
        # Sort aliases for determinism
        for alias in sorted(self.keys()):
            table = self[alias]
            idx = table_counters.get(table, 0)
            self._row_indices[alias] = idx
            table_counters[table] = idx + 1

    def row_index(self, alias: str) -> int:
        """Return the row index this alias binds to within its physical table."""
        return self._row_indices.get(alias, 0)

    def self_join_aliases(self, table: str) -> List[str]:
        """Return all aliases that reference the given physical table."""
        return [a for a, t in self.items() if t == table]

    def has_self_join(self, table: str) -> bool:
        """True if multiple aliases reference the same physical table."""
        return sum(1 for t in self.values() if t == table) > 1

    def self_join_tables(self) -> Dict[str, List[str]]:
        """Return {table: [aliases]} for tables with multiple aliases."""
        from collections import defaultdict
        groups: Dict[str, List[str]] = defaultdict(list)
        for alias, table in self.items():
            groups[table].append(alias)
        return {t: aliases for t, aliases in groups.items() if len(aliases) > 1}

    def ensure_rows_exist(self, instance) -> None:
        """Ensure the Instance has enough rows for all aliases (self-joins need multiple rows)."""
        from collections import Counter
        table_needs = Counter(self.values())
        for table, needed in table_needs.items():
            if table not in instance.tables:
                continue
            existing = len(instance.get_rows(table))
            for _ in range(max(0, needed - existing)):
                try:
                    instance.create_row(table, values={})
                except Exception:
                    pass


def _build_alias_map(plan: Plan) -> AliasMap:
    """Build alias → real table name mapping from the Plan's Scan steps.

    Walks all steps in the plan (including SubPlan inner plans) to find
    every base table reference. For FROM-subquery patterns, the real
    tables are inside the SubPlan's inner plan.
    """
    from parseval.helper import normalize_name
    raw: Dict[str, str] = {}

    def _walk_steps(steps):
        for step in steps:
            if isinstance(step, Scan) and step.source is not None:
                if isinstance(step.source, exp.Table):
                    alias = step.source.alias_or_name
                    real = step.source.name
                    if alias and real:
                        raw[alias] = real
                        raw[normalize_name(alias)] = normalize_name(real)
            # Walk into SubPlan inner plans.
            for sub in step.subplan_dependencies:
                if sub.inner:
                    _walk_inner(sub.inner)

    def _walk_inner(step):
        """Recursively walk an inner plan's steps."""
        visited = set()
        stack = [step]
        while stack:
            current = stack.pop()
            if id(current) in visited:
                continue
            visited.add(id(current))
            if isinstance(current, Scan) and current.source is not None:
                if isinstance(current.source, exp.Table):
                    alias = current.source.alias_or_name
                    real = current.source.name
                    if alias and real:
                        raw[alias] = real
                        raw[normalize_name(alias)] = normalize_name(real)
            for dep in current.dependencies:
                stack.append(dep)

    _walk_steps(plan.ordered_steps)
    return AliasMap(raw)


# =============================================================================
# Dynamic row budget
# =============================================================================


def _compute_row_budget(plan: Plan) -> int:
    """Compute a per-table row budget based on query complexity.

    Heuristic:
    - Base: 3 rows per table (minimum for meaningful coverage).
    - +2 per JOIN (need match + left-unmatched + right-unmatched).
    - +2 if GROUP BY present (need ≥2 groups, one passing HAVING, one failing).
    - +1 per CASE arm (each arm needs a row exercising it).
    - Cap at 20 to avoid runaway generation.
    """
    budget = 3
    for step in plan.ordered_steps:
        if isinstance(step, Join):
            budget += 2 * len(step.joins)
        elif isinstance(step, Aggregate) and step.group:
            budget += 2
        elif isinstance(step, Project):
            for proj in step.projections:
                if isinstance(proj, exp.Expression):
                    budget += len(list(proj.find_all(exp.Case)))
    return min(budget, 20)

__all__ = ["SymbolicEngine"]
