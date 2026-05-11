"""ParSEval solver module — unified constraint solving with tiered resolution.

Public API::

    from parseval.solver import Solver, SolveResult

    solver = Solver(instance, dialect="sqlite")
    result = solver.solve(constraint)
    if result.sat:
        for table, values in result.assignments.items():
            instance.place_row(table, values)
"""

from .unified import Solver, SolveResult

__all__ = ["Solver", "SolveResult"]
