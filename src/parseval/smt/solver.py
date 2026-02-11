from __future__ import annotations


from collections import defaultdict, deque
import logging
from typing import Any, Dict, List, Set, Tuple, Optional, TYPE_CHECKING

from .domain import UnionFind, ColumnDomainPool, ValuePool, DomainSpec, InConsistency
from .adapter import SolverAdapter, SolverResult, ValueAssignment
from .smt_solver import SMTSolver
from src.parseval.symbol import Variable, Symbol, Condition, IS_NULL, EQ, NEQ

logger = logging.getLogger("parseval.smt.solver")


class Solver:
    def __init__(self, column_domain_pool: ColumnDomainPool):
        self.pool_mgr = column_domain_pool
        self.uf = UnionFind()
        self.variables: Dict[str, Variable] = {}
        self.var_to_constraints: Dict[str, Set[Condition]] = defaultdict(set)
        self.constraints: List[Condition] = []

    def add_variable(self, variable: Variable):
        self.variables[variable.name] = variable
        self.uf.find(variable.name)

    def add_constraint(self, constraint: Condition):
        if not isinstance(constraint, Symbol):
            raise NotImplementedError(
                "Function constraints are not supported in this solver."
            )

        vars_ = list(constraint.find_all(Variable))
        self.constraints.append(constraint)
        self.variables.update({v.name: v for v in vars_})
        for v in vars_:
            self.uf.find(v.name)
            self.var_to_constraints.setdefault(v.name, set()).add(constraint)
        for i in range(1, len(vars_)):
            self.uf.union(vars_[0].name, vars_[i].name)

    def clusters(self) -> List[List[str]]:
        """
        Yield clusters of variable names based on constraint variables connectivity.
        """
        return self.uf.groups()

    def _assert_inconsistency(self):
        for constraint in self.constraints:
            if (
                isinstance(constraint, EQ)
                and isinstance(constraint.left, Variable)
                and isinstance(constraint.right, Variable)
            ):
                pools = [
                    self.pool_mgr.get_pool(var.name)
                    for var in [constraint.left, constraint.right]
                ]
                logger.info(
                    f"Adding equality constraint between {pools[0].alias} {pools[0]} {pools[1]}"
                )

                if not self.pool_mgr.add_equality(pools[0], pools[1]):
                    raise InConsistency(
                        "Inconsistent constraints detected.",
                        variables=[constraint.left, constraint.right],
                    )
            if (
                isinstance(constraint, NEQ)
                and isinstance(constraint.left, Variable)
                and isinstance(constraint.right, Variable)
            ):
                pools = [
                    self.pool_mgr.get_pool(var.name)
                    for var in [constraint.left, constraint.right]
                ]
                if not self.pool_mgr.add_inequality(pools[0], pools[1]):
                    raise InConsistency(
                        "Inconsistent constraints detected.",
                        variables=[constraint.left, constraint.right],
                    )

    def select_adapter(self, cluster: List[Condition], context) -> SolverAdapter:
        # For simplicity, we use SpeculativeSolver for all clusters
        return SMTSolver

    def solve(self) -> SolverResult:

        context = {}
        status = "sat"
        assignments = []
        try:
            self._assert_inconsistency()
        except InConsistency as e:
            return SolverResult(status="unsat", assignments=[])
        for cluster in self.clusters():
            constraints = []
            for var_name in cluster:
                for constraint in self.var_to_constraints.get(var_name, set()):
                    if constraint.find_all(IS_NULL):
                        if constraint.key == "not":
                            ...
                        else:
                            assignments.append(
                                ValueAssignment(
                                    column=var_name,
                                    value=None,
                                    alias=None,
                                    data_type=None,
                                )
                            )
                            logger.info(
                                f"Assigning NULL to {var_name} due to IS NULL constraint"
                            )

                    else:
                        constraints.append(constraint)
            adapter = self.select_adapter(constraints, context)
            adapter = adapter(str(cluster))
            solve_result = adapter.solve(
                variables=cluster, constraints=constraints, context=context
            )
            # logger.info(f"solve {cluster} with constraints: {constraints}")
            model = {}
            status = solve_result.status
            if status != "sat":
                break
            if solve_result.status == "sat":
                assignments.extend(solve_result.assignments)
                for assignment in solve_result.assignments:
                    model[assignment.column] = assignment.value
            context.setdefault("models", {}).update(model)

        return SolverResult(status=status, assignments=assignments)
