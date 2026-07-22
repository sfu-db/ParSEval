from __future__ import annotations

from typing import Mapping, Sequence, TYPE_CHECKING

from sqlglot import exp

from parseval.solver.api import Solver
from parseval.solver.types import Problem, Result

from .config import GenerationConfig

if TYPE_CHECKING:
    from parseval.instance import Instance


class GenerationBudget:
    """Shared solver-call budget for bootstrap and target-directed generation."""

    def __init__(self, config: GenerationConfig) -> None:
        self.config = config
        self.solver_calls = 0
        self.last_problem: Problem | None = None
        self.last_result: Result | None = None

    def solve(self, problem: Problem, *, dialect: str) -> Result:
        if self.solver_calls >= self.config.max_solver_calls:
            return Result(status="unknown", reason="solver_call_budget_exhausted")
        self.solver_calls += 1
        self.last_problem = problem
        self.last_result = Solver(
            dialect=dialect,
            timeout_ms=self.config.solver_timeout_ms,
            seed=self.config.seed,
        ).solve(problem)
        return self.last_result

    def row_reason(
        self,
        instance: "Instance",
        rows_by_table: Mapping[exp.Table, Sequence[object]],
    ) -> str:
        requested: dict[exp.Table, int] = {}
        for table, rows in rows_by_table.items():
            resolved = instance.resolve_table(table)
            requested[resolved] = requested.get(resolved, 0) + len(rows)
        current = {
            table: len(instance.get_rows(table))
            for table in instance.schema.fk_safe_table_order()
        }
        for table, count in requested.items():
            available = self.config.max_rows_per_table - current.get(table, 0)
            if count > available:
                return (
                    "row_budget_exhausted:"
                    f"table={table.name},requested={count},available={max(available, 0)}"
                )
        total_available = self.config.max_total_rows - sum(current.values())
        total_requested = sum(requested.values())
        if total_requested > total_available:
            return (
                "row_budget_exhausted:"
                f"requested={total_requested},available={max(total_available, 0)}"
            )
        return ""


__all__ = ["GenerationBudget"]
