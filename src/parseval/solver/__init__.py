"""ParSEval constraint solver.

The public ``Solver`` is a pure, two-tier solver. It first asks the
``DomainSolver`` for a sound tri-state result: ``sat`` is trusted,
``unsat`` short-circuits, and ``unknown`` falls back to the full SMT solver.
The SMT fallback is strict and fails closed when any expression cannot be
translated.

Public API::

    from parseval.solver import Solver, SolveResult, SolverConstraint

    solver = Solver(dialect="sqlite")
    result = solver.solve(constraint)
"""

from .unified import Solver, SolveResult, SolverConstraint

__all__ = ["Solver", "SolveResult", "SolverConstraint"]
