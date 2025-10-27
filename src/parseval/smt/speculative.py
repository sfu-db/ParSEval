from __future__ import annotations

from .adapter import SolverAdapter, SolverResult, ValueAssignment
import z3
from typing import List, Dict, Any, Optional, TYPE_CHECKING
from datetime import datetime
from src.parseval.dtype import DataType
from src.parseval.symbol import Variable, Symbol, Condition, Const, EQ, NE
from .exceptions import InConsistency

import logging

logger = logging.getLogger(__name__)
if TYPE_CHECKING:
    from src.parseval.smt.domain import ColumnDomainPool


class SpeculativeSolver(SolverAdapter):
    def __init__(self, name, pool_mgr: ColumnDomainPool):
        super().__init__(name)
        self.pool_mgr = pool_mgr

    def supports(self, variables, constraints, context):
        return super().supports(variables, constraints, context)

    def solve(self, variables: List[Symbol], constraints: List[Condition], context):
        return super().solve(variables, constraints, context)

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

        for var_name, var in self.variables.items():
            pool = self.pool_mgr.get_pool(var_name)
            pool.expand_domain(additional_samples=30)
            logger.info(f"initialize value pool for {var_name}: {pool}")
