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

from typing import Any, Dict, List, Optional

from sqlglot import exp

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.plan.planner import Aggregate, Filter, Join, Project
from parseval.query import preprocess_sql

from .constraints import ConstraintGenerator
from parseval.solver.unified import SolverConstraint
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
    smt, condition: exp.Expression, ctx: Dict[str, Any]
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
    smt, condition: exp.Expression, ctx: Dict[str, Any], instance: Instance, alias_map
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
        self.plan = Plan(self.expr, self.instance)
        from parseval.solver.unified import Solver; self.solver = solver or Solver(instance, dialect=dialect)
        self.max_iterations = max_iterations
        # Alias → real table name mapping from the Plan's Scan steps.
        self.alias_map = self.plan.alias_map
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
        thresholds = thresholds or CoverageThresholds()
        evaluator = PlanEvaluator(self.plan, self.instance, self.dialect)
        constraint_gen = ConstraintGenerator(self.plan, self.instance, self.dialect)

        rows_before = self._total_rows()

        # Phase 0: Speculate all branches (positive + negatives) at once.
        # This is the primary generation path — handles self-joins, HAVING
        # COUNT, NOT IN, and all common patterns via heuristics.
        self._speculate_all_branches()

        # Phase 0f: SMT repair — for self-joins, NOT IN, and complex OR
        # predicates that the speculative layer cannot handle.
        self._smt_repair_where()

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
                    tables_in_condition = set(
                        normalize_name(self.alias_map.get(a, a)) for a in aliases_in_condition
                    ) & set(self.instance.tables.keys())
                    smt_constraint = SolverConstraint(
                        target_tables=tuple(tables_in_condition),
                        constraints=[condition],
                        atom=condition,
                    )
                    smt_result = self.solver._try_smt(smt_constraint)
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
