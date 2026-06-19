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

import logging
from typing import Any, Dict, List, Optional

from sqlglot import exp

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.plan.planner import Aggregate, Join, Project
from parseval.query import preprocess_sql
from parseval.solver import Solver, SolverConstraint

from .constraints import PlausibleBranch, ConstraintGenerator
from .evaluator import PlanEvaluator
from .infeasibility import is_infeasible
from .types import (
    BranchTree,
    BranchType,
    CoverageTarget,
    CoverageThresholds,
    GenerationResult,
)

logger = logging.getLogger("parseval.engine")


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
        solver=None,
        max_iterations: int = 50,
        max_rows_per_table: Optional[int] = None,
    ):
        self.instance = instance
        self.sql = sql
        self.dialect = dialect
        self.expr = preprocess_sql(sql, instance, dialect=dialect)
        self.plan = Plan(self.expr, self.instance)
        self.solver = solver or Solver(dialect=dialect)
        self.max_iterations = max_iterations
        if max_rows_per_table is not None:
            self.max_rows_per_table = max_rows_per_table
        else:
            self.max_rows_per_table = _compute_row_budget(self.plan)

    def generate(
        self,
        thresholds: Optional[CoverageThresholds] = None,
    ) -> GenerationResult:
        """Run the generation loop until coverage is met or budget exhausted."""
        thresholds = thresholds or CoverageThresholds()
        evaluator = PlanEvaluator(self.plan, self.instance, self.dialect)
        constraint_gen = ConstraintGenerator(
            self.plan, self.instance, self.dialect
        )

        rows_before = self._total_rows()

        # Phase 1: Build tree structure and run initial evaluation.
        from .evaluator import build_branch_tree
        tree = build_branch_tree(self.plan, self.instance, thresholds)
        tree = evaluator.evaluate(tree)

        # Phase 2: Targeted gap-filling.
        iteration = 0
        for iteration in range(self.max_iterations):
            if tree.fully_covered:
                break

            targets = tree.root_witness_targets or tree.uncovered_targets
            if not targets:
                break

            # Check row budget.
            if self._over_budget(rows_before):
                break

            # Process one target per iteration.
            target = self._prioritize(targets)

            # Quick infeasibility check.
            reason = is_infeasible(
                target.node, target.atom_id, target.target_outcome, self.instance
            )
            if reason is not None:
                tree.mark_infeasible(target.node, target.atom_id, target.target_outcome)
                continue

            # Generate complete constraints (including DB constraints).
            constraint = constraint_gen.compile(PlausibleBranch.from_coverage_target(target))

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

    def _over_budget(self, rows_before: int) -> bool:
        """Check if we've exceeded the row budget."""
        return (
            self._total_rows() - rows_before
            >= self.max_rows_per_table * len(self.instance.tables)
        )

    def _prioritize(self, targets: List[CoverageTarget]) -> CoverageTarget:
        """Select the highest-priority uncovered target.

        Priority:
        1. ATOM_TRUE / ATOM_FALSE (basic branch coverage)
        2. ATOM_NULL (3VL edge cases)
        3. Filter sites before Join before Having before Case
        """
        site_priority = {
            "root_result": -1,
            "filter": 0,
            "join_on": 1,
            "having": 2,
            "case_arm": 3,
            "group": 4,
        }
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
        """Invoke the unified solver and materialize results into the instance."""
        from parseval.solver import SolverVar

        result = self.solver.solve(constraint)
        if not result.sat:
            return False

        # Group assignments by table and row scope so multi-row obligations
        # materialize as distinct physical rows.
        rows_by_table: Dict[tuple[Any, str], Dict[str, Any]] = {}
        for var, value in result.assignments.items():
            if not isinstance(var, SolverVar):
                continue
            storage_relation = constraint.storage_relations.get(var)
            storage_column = var.column_id.source_column_id or var.column_id
            if storage_relation is None:
                source_relation = storage_column.relation
                if source_relation is not None and source_relation.name is not None:
                    storage_relation = source_relation
                else:
                    storage_relation = var.relation_id
            if storage_relation.name is None:
                continue
            try:
                self.instance._table_key_for_storage(storage_relation)
            except Exception:
                continue
            col_name = storage_column.name.normalized
            row_scope = var.row_scope or "r0"
            rows_by_table.setdefault((storage_relation, row_scope), {})[col_name] = value

        relations = []
        seen_relations = set()
        for relation, _row_scope in rows_by_table:
            if relation in seen_relations:
                continue
            seen_relations.add(relation)
            relations.append(relation)
        ordered_relations = self.instance._creation_order(
            {relation: {} for relation in relations}
        )
        for relation in ordered_relations:
            scoped_rows = [
                (row_scope, row_values)
                for (row_relation, row_scope), row_values in rows_by_table.items()
                if row_relation == relation
            ]
            for _row_scope, row_values in scoped_rows:
                try:
                    self.instance.create_row(relation, values=row_values)
                except Exception:
                    return False
        return True

    def _total_rows(self) -> int:
        return sum(len(self.instance.get_rows(t)) for t in self.instance.tables)


# =============================================================================
# Dynamic row budget
# =============================================================================


def _compute_row_budget(plan: Plan) -> int:
    """Compute a per-table row budget based on query complexity.

    Heuristic:
    - Base: 3 rows per table (minimum for meaningful coverage).
    - +2 per JOIN (need match + left-unmatched + right-unmatched).
    - +2 if GROUP BY present (need >=2 groups, one passing HAVING, one failing).
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
