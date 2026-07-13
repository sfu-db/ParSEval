"""Thin Solver orchestrator: partition → CSP → SMT cascade."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from sqlglot import exp

from .csp import CspBackend
from .normalization import normalize_problem
from .partition import partition
from .smt import SmtBackend
from .types import Problem, Result, SolverVar


class Solver:
    """Partition by SolverVar, then CSP with SMT fallback per component."""

    def __init__(
        self,
        dialect: str = "sqlite",
        *,
        timeout_ms: int = 5000,
        seed: int = 42,
    ) -> None:
        del seed
        self.dialect = dialect
        self.timeout_ms = timeout_ms
        self._csp = CspBackend()
        self._smt = SmtBackend(timeout_ms=timeout_ms, dialect=dialect)

    def solve(self, problem: Problem) -> Result:
        if not isinstance(problem, Problem):
            raise TypeError("Solver.solve() expects a Problem")

        if not problem.constraints and not problem.equalities:
            return Result(status="sat", assignments={})

        ok, reason = self._validate(problem)
        if not ok:
            return Result(status="unsat", reason=reason)

        problem = normalize_problem(problem)

        assignments: Dict[SolverVar, Any] = {}
        for component in partition(problem):
            csp_result = self._csp.solve(component)
            if csp_result.status == "sat":
                assignments.update(csp_result.assignments)
                continue
            if csp_result.status == "unsat":
                return Result(
                    status="unsat",
                    reason=csp_result.reason or "unsat",
                )
            smt_result = self._smt.solve(component)
            if smt_result.status == "sat":
                assignments.update(smt_result.assignments)
                continue
            return Result(
                status=smt_result.status,
                reason=smt_result.reason or "all_tiers_exhausted",
            )
        return Result(status="sat", assignments=assignments)

    def _validate(self, problem: Problem) -> Tuple[bool, str]:
        for expr in problem.constraints:
            # Bare SQL columns are not solver variables.
            if any(True for _ in expr.find_all(exp.Column)):
                return False, "missing_solver_var"
        return True, ""


__all__ = ["Solver"]
