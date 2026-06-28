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
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from sqlglot import exp

from parseval.instance import Instance
from parseval.identity import ColumnKind, RelationId
from parseval.domain.exceptions import ConstraintConflict
from parseval.plan import Plan
from parseval.plan.planner import Aggregate, Join, Limit, Project
from parseval.query import preprocess_sql
from parseval.solver import Solver, SolverConstraint

from .branch_tree import BranchTreeBuilder, CoverageAnalyzer
from .constraints import ConstraintGenerator
from .evaluator import PlanEvaluator
from .types import (
    BranchTree,
    BranchType,
    CoverageTarget,
    CoverageThresholds,
    GenerationResult,
)

logger = logging.getLogger("parseval.engine")


@dataclass(frozen=True)
class _LogicalRowKey:
    relation: RelationId
    row_scope: str


def _materialized_rows(
    constraint: SolverConstraint,
    assignments: Dict[Any, Any],
) -> Dict[_LogicalRowKey, tuple[RelationId, Dict[str, Any]]]:
    from parseval.solver import SolverVar

    rows: Dict[_LogicalRowKey, tuple[RelationId, Dict[str, Any]]] = {}
    for variable, value in assignments.items():
        if not isinstance(variable, SolverVar):
            continue
        storage_relation = constraint.storage_relations.get(variable)
        storage_column = variable.column_id.source_column_id or variable.column_id
        if storage_relation is None or storage_column.kind is not ColumnKind.PHYSICAL:
            raise ConstraintConflict(f"missing_physical_lineage:{variable.display}")
        key = _LogicalRowKey(
            relation=variable.relation_id,
            row_scope=variable.row_scope or "r0",
        )
        existing = rows.get(key)
        if existing is None:
            values: Dict[str, Any] = {}
            rows[key] = (storage_relation, values)
        else:
            existing_relation, values = existing
            if existing_relation != storage_relation:
                raise ConstraintConflict(
                    f"conflicting_storage_relation:{variable.display}"
                )
        column_name = storage_column.name.normalized
        if column_name in values and values[column_name] != value:
            raise ConstraintConflict(
                f"conflicting_materialized_assignment:{variable.display}"
            )
        values[column_name] = value
    return rows


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
        *,
        speculate_first: bool = True,
    ) -> GenerationResult:
        """Run the generation loop until coverage is met or budget exhausted."""
        thresholds = thresholds or CoverageThresholds()
        evaluator = PlanEvaluator(self.plan, self.instance, self.dialect)
        constraint_gen = ConstraintGenerator(
            self.plan, self.instance, self.dialect
        )

        rows_before = self._total_rows()

        # Phase 1: Speculate to seed the instance with initial data.
        if speculate_first:
            from .speculate import speculate, SpeculateConfig
            spec_config = (
                SpeculateConfig.from_thresholds(thresholds)
                if thresholds
                else SpeculateConfig.gold_non_empty()
            )
            speculate(
                self.plan, self.instance,
                dialect=self.dialect, config=spec_config,
            )

        # Phase 2: Build tree structure and run initial evaluation.
        tree = BranchTreeBuilder(self.plan, self.instance, thresholds).build()
        tree = evaluator.evaluate(tree)
        analyzer = CoverageAnalyzer(tree)

        # Phase 2: Targeted gap-filling.
        iteration = 0
        for iteration in range(self.max_iterations):
            if analyzer.fully_covered:
                break

            targets = analyzer.root_witness_targets or analyzer.uncovered_targets
            if not targets:
                break

            # Check row budget.
            if self._over_budget(rows_before):
                break

            # Process one target per iteration.
            target = self._prioritize(targets)

            # Generate complete constraints (including DB constraints).
            constraint = constraint_gen.compile_target(target)

            # Solve and materialize.
            cp = self.instance.checkpoint()
            try:
                success = self._solve_and_materialize(constraint)
            except ConstraintConflict:
                success = False

            if success:
                # Re-evaluate to discover newly covered branches.
                tree = evaluator.evaluate(tree)
                analyzer = CoverageAnalyzer(tree)
            else:
                self.instance.rollback(cp)
                tree.mark_target_infeasible(target)
                analyzer = CoverageAnalyzer(tree)

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
        result = self.solver.solve(constraint)
        if not result.sat:
            return False

        materialized = _materialized_rows(constraint, result.assignments)

        storage_relations = []
        seen_relations = set()
        for storage_relation, _row_values in materialized.values():
            if storage_relation in seen_relations:
                continue
            seen_relations.add(storage_relation)
            storage_relations.append(storage_relation)
        ordered_relations = self.instance._creation_order(
            {relation: {} for relation in storage_relations}
        )
        for storage_relation in ordered_relations:
            logical_rows = [
                row_values
                for row_storage_relation, row_values in materialized.values()
                if row_storage_relation == storage_relation
            ]
            for row_values in logical_rows:
                try:
                    self.instance.create_row(storage_relation, values=row_values)
                except Exception as exc:
                    raise ConstraintConflict(
                        f"materialization_failed:{storage_relation.display}"
                    ) from exc
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
    - Raise to root LIMIT offset+limit when the final result requires more rows.
    """
    budget = 3
    for step in plan.ordered_steps:
        if isinstance(step, Join):
            budget += 2 * len(step.joins)
        elif isinstance(step, Aggregate) and step.group:
            budget += 3
        elif isinstance(step, Project):
            for proj in step.projections:
                if isinstance(proj, exp.Expression):
                    budget += len(list(proj.find_all(exp.Case)))
        elif isinstance(step, Limit):
            offset = max(int(getattr(step, "offset", 0) or 0), 0)
            limit = getattr(step, "limit", 1)
            limit_value = 1 if limit == float("inf") else max(int(limit or 0), 1)
            budget = max(budget, offset + limit_value)
    return budget


__all__ = ["SymbolicEngine"]
