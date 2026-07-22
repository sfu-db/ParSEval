"""SMT backend: Z3 solve against a Problem."""

from __future__ import annotations

from typing import Dict

import z3

from .smt_solver import Z3SmtSession
from .smt_types import UnsupportedSMTError
from .types import Problem, Result, SolverVar, collect_problem_variables


class SmtBackend:
    """Z3-backed solver implementing the Backend protocol."""

    def __init__(
        self,
        *,
        timeout_ms: int = 5000,
        dialect: str = "sqlite",
        seed: int = 42,
    ) -> None:
        self.timeout_ms = timeout_ms
        self.dialect = dialect
        self.seed = seed

    def solve(self, problem: Problem) -> Result:
        if not problem.constraints and not problem.equalities:
            return Result(status="sat", assignments={})

        smt = Z3SmtSession(
            timeout_ms=self.timeout_ms,
            dialect=self.dialect,
            seed=self.seed,
        )
        variables = collect_problem_variables(problem)
        encoded_names = {
            variable: f"sv_{index}_{variable.var_key}".replace(".", "_").replace(
                "#", "_"
            )
            for index, variable in enumerate(variables)
        }
        smt.context["solver_var_to_name"] = encoded_names
        reverse_names = {name: variable for variable, name in encoded_names.items()}

        for variable in variables:
            smt.declare_variable(encoded_names[variable], variable.dtype)

        try:
            for expr in problem.constraints:
                smt.add(smt.translate(expr))

            for left_var, right_var in problem.equalities:
                left_z3 = smt.context["variable_to_z3"][encoded_names[left_var]]
                right_z3 = smt.context["variable_to_z3"][encoded_names[right_var]]
                smt.add_raw(left_z3 == right_z3)

            status, solutions = smt.solve()
        except UnsupportedSMTError:
            return Result(status="unknown", reason="unsupported_smt_expression")
        except z3.Z3Exception:
            return Result(status="unknown", reason="unsupported_smt_expression")

        if status == "unsat":
            return Result(status="unsat", reason="unsat")
        if status != "sat":
            return Result(status="unknown", reason="z3_unknown")

        assignments: Dict[SolverVar, object] = {}
        for var_name, value in solutions.items():
            variable = reverse_names.get(var_name)
            if variable is not None:
                assignments[variable] = value
        return Result(status="sat", assignments=assignments)


__all__ = ["SmtBackend"]
