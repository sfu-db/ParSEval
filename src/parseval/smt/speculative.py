from __future__ import annotations

from .adapter import SolverAdapter, SolverResult, ValueAssignment
import z3
from typing import List, Dict, Any, Optional, TYPE_CHECKING, Tuple
from datetime import datetime
from collections import deque


from src.parseval.dtype import DataType
from src.parseval.symbol import Variable, Symbol, Condition, Const, EQ, NE, Function
from .exceptions import InConsistency

import logging

logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    from src.parseval.smt.domain import ColumnDomainPool


class SpeculativeSolver(SolverAdapter):
    def __init__(self, name, pool_mgr: ColumnDomainPool):
        super().__init__(name)
        self.pool_mgr = pool_mgr
        self.pair_to_constraints: Dict[Tuple[str, str], List[Condition]] = {}
        self.variables: Dict[str, Variable] = {}

    def supports(self, variables, constraints, context):
        for constraint in constraints:
            if len(list(constraint.find_all(Variable))) > 2 or list(
                constraint.find_all(Function)
            ):
                return False
        return True

    def solve(self, variables: List[Symbol], constraints: List[Condition], context):
        """
        AC-3 + Backtracking to find a consistent assignment of values to variables.
        """
        context = context or {}
        max_attempts = context.get("max_attempts", 2)
        domain_size = context.get("domain_size", 50)
        self._initialize_domain(constraints=constraints)
        for _ in range(max_attempts):
            try:
                for var_name, var in self.variables.items():
                    pool = self.pool_mgr.get_pool(var_name)
                    pool.expand_domain(additional_samples=domain_size)

                    logging.info(
                        f"Variable {var_name} domain size: {self._domain_size(var_name)}"
                    )

                if not self.propagate(constraints):
                    logging.info("Propagation detected inconsistency.")
                    return SolverResult(status="UNSAT")
                assignment = {}
                result = self._backtrack(constraints=constraints, assignment=assignment)
                if result:
                    break
            except InConsistency as e:
                logging.info(f"Inconsistency detected: {e}")
                return SolverResult(status="UNSAT")
        status = "SAT" if result else "UNSAT"
        return SolverResult(status=status, assignments=result)

    def _domain_size(self, var_name: str) -> int:
        pool_i = self.pool_mgr.get_pool(var_name)
        return len(pool_i.get_domain_values())

    def _initialize_domain(self, constraints: List[Condition]):

        for constraint in constraints:
            if (
                isinstance(constraint, EQ)
                and isinstance(constraint.left, Variable)
                and isinstance(constraint.right, Variable)
            ):
                pools = [
                    self.pool_mgr.get_pool(var.name)
                    for var in [constraint.left, constraint.right]
                ]

                if not self.pool_mgr.add_equality(pools[0], pools[1]):
                    raise InConsistency(
                        "Inconsistent constraints detected.",
                        variables=[constraint.left, constraint.right],
                    )
            if (
                isinstance(constraint, NE)
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
            variables = constraint.find_all(Variable)
            if len(variables) == 1:
                for var in variables:
                    pool = self.pool_mgr.get_pool(var.name)
                    pool.apply_constraints(constraint)

    def _initialize_arcs(self, constraints) -> List[Tuple[str, str, Condition]]:
        arcs = []
        for constraint in constraints:
            variables = list(constraint.find_all(Variable))
            if len(variables) > 1:
                names = [v.name for v in variables]
                for i in range(len(names)):
                    for j in range(len(names)):
                        if i == j:
                            continue
                        self.pair_to_constraints.setdefault(
                            (names[i], names[j]), []
                        ).append(constraint)
                        self.pair_to_constraints.setdefault(
                            (names[j], names[i]), []
                        ).append(constraint)
                        arcs.append((names[i], names[j], constraint))
                        arcs.append((names[j], names[i], constraint))
        return arcs

    def propagate(self, constraints) -> bool:
        queue = deque(self._initialize_arcs(constraints))

        while queue:
            xi, xj, constraint = queue.popleft()
            revised = self._revise(xi, xj, constraint)
            if revised:
                ### check if domain becomes empty
                pool_i = self.pool_mgr.get_pool(xi)
                logger.info(self._domain_size(xi))
                if not pool_i.get_domain_values():
                    return False

                for (a, b), constraints in self.pair_to_constraints.items():
                    if b == xi and a != xj:
                        for c in constraints:
                            queue.append((a, b, c))

        return True

    def _revise(self, xi: str, xj: str, constraint: Condition) -> bool:
        """
        Remove values from domain(xi) that have no supporting value in domain(xj).
        Returns True if domain(xi) was revised.
        """
        pool_i = self.pool_mgr.get_pool(xi)
        pool_j = self.pool_mgr.get_pool(xj)

        removed_any = False
        pool_i_values = pool_i.get_domain_values()
        pool_j_values = pool_j.get_domain_values()
        for vi in pool_i_values:
            has_support = False

            other_vars = [
                v.name for v in constraint.find_all(Variable) if v.name not in (xi, xj)
            ]

            for vj in pool_j_values:
                assignment = {xi: vi, xj: vj}
                if not other_vars:
                    sat = constraint.evaluate(assignment).concrete
                    if sat is True:
                        has_support = True
                        break
                else:
                    raise NotImplementedError

            if not has_support:
                pool_i.add_excluded(vi)
                removed_any = True
        return removed_any

    def _forward_check(
        self,
        constraints: List[Condition],
        var_name: str,
        value: Any,
        assignment: Dict[str, Any],
    ) -> bool:
        """
        Forward checking: check if assigning var=value makes any future variable's domain empty.
        """
        for constraint in constraints:
            variables = list(constraint.find_all(Variable))
            constraint_var_names = [v.name for v in variables]
            if var_name not in constraint_var_names:
                continue
            unassigned_vars = [
                v for v in variables if v.name not in assignment and v.name != var_name
            ]

            for other_var in unassigned_vars:
                pool_other = self.pool_mgr.get_pool(other_var.name)
                domain_values = list(pool_other.get_domain_values())
                values_to_exclude = []
                for v in domain_values:
                    test_assignment = assignment.copy()
                    test_assignment[other_var.name] = v
                    test_assignment[var_name] = value
                    can_evaluate = all(
                        v in test_assignment for v in constraint_var_names
                    )
                    if can_evaluate:
                        if not constraint.evaluate(test_assignment).concrete:
                            values_to_exclude.append(v)
                for val in values_to_exclude:
                    pool_other.add_excluded(val)
                remaining = list(pool_other.get_domain_values())
                if not remaining:
                    return False
        return True

    def _is_consistent(
        self, constraints, var_name, value, assignment: Dict[str, Any]
    ) -> bool:
        """Check if assigning value to var is consistent with constraints."""
        assignment[var_name] = value
        consistent = True
        for constraint in constraints:
            # Get variable names in this constraint
            variables = constraint.find_all(Variable)
            constraint_vars = [v.name for v in variables]
            if var_name not in constraint_vars:
                continue
            # If all variables in constraint are assigned, check it
            if all(v in assignment for v in constraint_vars):
                if not constraint.evaluate(assignment):
                    consistent = False
        del assignment[var_name]
        return consistent

    def _backtrack(
        self, constraints, assignment: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if len(assignment) == len(self.variables):
            return assignment
        unassigned = [v for v in self.variables if v not in assignment]
        var_name = min(unassigned, key=lambda v: self._domain_size(v))

        value_pool = self.pool_mgr.get_pool(var_name)
        domain_values = list(value_pool.get_domain_values())
        if not domain_values:
            return
        for value in domain_values:
            if not self._is_consistent(constraints, var_name, value, assignment):
                continue
            saved_state = {
                v_name: {
                    "local_values": self.pool_mgr.get_pool(v_name).local_values.copy(),
                    "excluded": self.pool_mgr.get_pool(v_name).local_excluded.copy(),
                }
                for v_name in self.variables
                if v_name not in assignment
            }
            assignment[var_name] = value
            if self._forward_check(constraints, var_name, value, assignment):
                result = self._backtrack(assignment)
                if result:
                    return result
            for v_name in self.variables:
                if v_name not in assignment:
                    pool_v = self.pool_mgr.get_pool(v_name)
                    pool_v.local_values = saved_state[v_name]["local_values"]
                    pool_v.local_excluded = saved_state[v_name]["excluded"]
            del assignment[var_name]
        return None
