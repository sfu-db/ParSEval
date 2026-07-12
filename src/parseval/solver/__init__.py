"""ParSEval constraint solver.

Public API::

    from parseval.solver import Solver, Problem, Result, SolverVar

    age = SolverVar(key="t.age", dtype=DataType.build("INT"))
    solver = Solver(dialect="sqlite")
    result = solver.solve(Problem(constraints=[exp.GT(this=age, expression=...)]))
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .types import (
    Problem,
    Result,
    SolverVar,
    collect_problem_variables,
)

if TYPE_CHECKING:
    from .api import Solver as Solver

__all__ = [
    "Problem",
    "Result",
    "Solver",
    "SolverVar",
    "collect_problem_variables",
]


def __getattr__(name: str) -> Any:
    if name == "Solver":
        from .api import Solver

        return Solver
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
