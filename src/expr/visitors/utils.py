from .base import ExprVisitor
from ..base import Expr
from ..operators.binary import Binary
from ..operators.nary import And

class ExpressionSimplifier(ExprVisitor):
    """Visitor that performs basic expression simplification"""
    # ... (existing ExpressionSimplifier implementation)

class TypeChecker(ExprVisitor):
    """Visitor that validates expression types"""
    # ... (existing TypeChecker implementation) 