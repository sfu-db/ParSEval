from __future__ import annotations
from typing import TYPE_CHECKING, Any, Dict
import operator

from src.parseval.plan.expression import ColumnRef
from src.parseval.plan.planner import ExpressionEncoder

# logger = logging.info(__name__)


class Evaluator(ExpressionEncoder):

    def __init__(self, context: Dict[str, Any]):
        super().__init__(None, ignore_nulls=True)
        self.context = context
        self.smt_conditions
        self.sql_conditions

    def visit_columnref(self, expr: ColumnRef):
        return self.context[expr.qualified_name]
