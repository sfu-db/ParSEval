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
        evaluator = PlanEvaluator(self.plan, self.instance, self.dialect)
        constraint_gen = ConstraintGenerator(self.plan, self.instance, self.dialect)

        rows_before = self._total_rows()

        # Phase 0: Speculate all branches (positive + negatives) at once.
        self._speculate_all_branches()

        # Phase 0b: Ensure every plan-referenced table has at least one row.
        self._ensure_base_rows()

        # Phase 0c: If query has OFFSET, generate enough rows.
        self._seed_for_offset()

        # Phase 0d: Handle subquery predicates.
        self._resolve_subquery_predicates()

        # Phase 0e: SMT-based WHERE repair — use Z3 to solve the full WHERE
        # clause and update rows that don't satisfy it.
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

        # Phase 2: Ensure non-empty results via UExprToConstraint.
        self._ensure_nonempty_via_uexpr()

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

        For each Filter step, if existing rows don't satisfy the predicate,
        invoke the SMT solver on the full predicate to get valid assignments
        and update the first row's values accordingly.
        """
        from parseval.plan.planner import Filter
        from parseval.plan.rex import concrete, Environment
        from parseval.helper import normalize_name

        for step in self.plan.ordered_steps:
            if not isinstance(step, Filter) or step.condition is None:
                continue
            # Skip predicates with subqueries (handled separately)
            if step.condition.find(exp.Subquery):
                continue

            condition = step.condition
            # Check if any existing row satisfies the full predicate
            satisfied = False
            tables_in_condition = set()
            for col in condition.find_all(exp.Column):
                table = self.alias_map.get(normalize_name(col.table or ""), col.table or "")
                table = normalize_name(table)
                if table in self.instance.tables:
                    tables_in_condition.add(table)

            if not tables_in_condition:
                continue

            # Build environment from first row of each table and check
            env = Environment()
            for table in tables_in_condition:
                rows = self.instance.get_rows(table)
                if rows:
                    for col_name, sym in rows[0].items():
                        env.bind(f"{table}.{col_name}", sym.concrete)
                        env.bind(col_name, sym.concrete)

            result = concrete(condition, env)
            if result is True:
                continue  # Already satisfied

            # Not satisfied — try SMT solver
            try:
                from parseval.solver.unified import _try_smt
                from parseval.symbolic.constraints import SolverConstraint
                from parseval.symbolic.types import BranchType

                smt_constraint = SolverConstraint(
                    target_tables=tuple(tables_in_condition),
                    atom=condition,
                    target_outcome=BranchType.ATOM_TRUE,
                    path_predicates=[],
                    join_equalities=[],
                )
                smt_result = _try_smt(smt_constraint, self.instance, self.dialect, timeout_ms=3000)
                if smt_result:
                    # Apply SMT solution to existing rows
                    for table, col_values in smt_result.items():
                        real_table = self.alias_map.get(table, table)
                        real_table = normalize_name(real_table)
                        if real_table not in self.instance.tables:
                            continue
                        rows = self.instance.get_rows(real_table)
                        if not rows:
                            continue
                        for col, val in col_values.items():
                            matched_col = next(
                                (c for c in self.instance.tables[real_table] if c.lower() == col.lower()),
                                None
                            )
                            if matched_col and matched_col in rows[0].columns and val is not None:
                                rows[0][matched_col].set("concrete", val)
                                rows[0][matched_col].set("is_bound", True)
                                rows[0][matched_col].set("is_null", False)
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
        """Resolve a subquery atom using our own plan evaluator (not SQLite).

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
        """Ensure every table referenced in the Plan has at least one row."""
        for alias, real_table in self.alias_map.items():
            if real_table not in self.instance.tables:
                continue
            if self.instance.get_rows(real_table):
                continue
            try:
                values = self._fill_fk_values(real_table, {})
                self.instance.create_row(real_table, values=values)
            except Exception:
                pass

    def _ensure_nonempty_via_uexpr(self) -> None:
        """Use UExprToConstraint to ensure the query returns non-empty results.
        
        Only invoked if the query currently returns empty results.
        """
        # Quick check: does the query already return non-empty?
        if self._query_produces_rows():
            return
        try:
            from .uexpr import UExprToConstraint
            cp = self.instance.checkpoint()
            uexpr = UExprToConstraint(self.plan, self.instance, self.dialect)
            if not uexpr.ensure_nonempty():
                self.instance.rollback(cp)
            elif not self._query_produces_rows():
                # Z3 solved but result still empty — rollback
                self.instance.rollback(cp)
        except Exception:
            pass

    def _query_produces_rows(self) -> bool:
        """Check if the query returns non-empty results against current Instance."""
        import sqlite3
        conn = sqlite3.connect(":memory:")
        try:
            for ddl in self.instance.ddls.split(";"):
                ddl = ddl.strip()
                if ddl:
                    try:
                        conn.execute(ddl)
                    except Exception:
                        pass
            for table_name in self.instance.tables:
                rows = self.instance.get_rows(table_name)
                if not rows:
                    continue
                cols = list(self.instance.tables[table_name].keys())
                placeholders = ",".join(["?"] * len(cols))
                col_names = ",".join(f'"{c}"' for c in cols)
                stmt = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
                for row in rows:
                    values = []
                    for c in cols:
                        v = row[c].concrete if c in row.columns else None
                        if v is not None and not isinstance(v, (int, float, str, bytes)):
                            v = str(v)
                        values.append(v)
                    try:
                        conn.execute(stmt, values)
                    except Exception:
                        pass
            conn.commit()
            result = conn.execute(self.sql).fetchone()
            return result is not None
        except Exception:
            return False
        finally:
            conn.close()


# =============================================================================
# Alias resolution
# =============================================================================


def _build_alias_map(plan: Plan) -> Dict[str, str]:
    """Build alias → real table name mapping from the Plan's Scan steps.

    Walks all steps in the plan (including SubPlan inner plans) to find
    every base table reference. For FROM-subquery patterns, the real
    tables are inside the SubPlan's inner plan.
    """
    from parseval.helper import normalize_name
    alias_map: Dict[str, str] = {}

    def _walk_steps(steps):
        for step in steps:
            if isinstance(step, Scan) and step.source is not None:
                if isinstance(step.source, exp.Table):
                    alias = step.source.alias_or_name
                    real = step.source.name
                    if alias and real:
                        alias_map[alias] = real
                        alias_map[normalize_name(alias)] = normalize_name(real)
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
                        alias_map[alias] = real
                        alias_map[normalize_name(alias)] = normalize_name(real)
            for dep in current.dependencies:
                stack.append(dep)

    _walk_steps(plan.ordered_steps)
    return alias_map


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
