from __future__ import annotations


from collections import defaultdict, deque
import logging
from typing import Any, Dict, List, Set, Tuple, Optional, TYPE_CHECKING

from .domain import UnionFind, ColumnDomainPool, ValuePool, DomainSpec, InConsistency
from .adapter import SolverAdapter, SolverResult, ValueAssignment
from .smt_solver import SMTSolver
from src.parseval.symbol import Variable, Symbol, Condition

logger = logging.getLogger(__name__)


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
        vars_ = list(constraint.find_all(Variable))
        logging.info(f"finding vars : {vars_}")
        self.constraints.append(constraint)
        self.variables.update({v.name: v for v in vars_})
        for v in vars_:
            self.uf.find(v.name)
            self.var_to_constraints.setdefault(v.name, set()).add(constraint)
        for i in range(1, len(vars_)):
            self.uf.union(vars_[0].name, vars_[i].name)

    def clusters(self) -> List[List[str]]:
        """
        Yield clusters of variable names  based on constraint variables connectivity.
        """
        # for varnames in self.uf.groups():
        #     for var_name in varnames:
        #         yield from self.var_to_constraints.get(var_name, set())
        # for constraint in self.var_to_constraints.get(var_name, set()):
        #     yield constraint
        return self.uf.groups()

    def select_adapter(self, cluster: List[Condition], context) -> SolverAdapter:
        # For simplicity, we use SpeculativeSolver for all clusters
        return SMTSolver("smt_solver")

    def solve(self) -> SolverResult:

        context = {}
        status = "sat"
        assignments = []
        for cluster in self.clusters():
            constraints = []
            for var_name in cluster:
                constraints.extend(self.var_to_constraints.get(var_name, set()))
            adapter = self.select_adapter(constraints, context)
            solve_result = adapter.solve(
                variables=cluster, constraints=constraints, context=context
            )
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
