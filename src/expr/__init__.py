"""
Expression system for symbolic computation and analysis.

This package provides a comprehensive expression system with:
- Rich type system
- N-ary operations
- Visitor pattern support
- Symbolic engine integration
"""

# Import base classes first
from .base import Expr, Condition, Predicate
from .types import DataType

# Import operators
from .operators.arithmetic import Add, Sub, Mul, Div
from .operators.logical import (
    And, Or, Not,
    EQ, NEQ, GT, GTE, LT, LTE,
    Is_Null, Is_NotNull
)
from .operators.literal import Variable, Literal

# Import visitors
from .visitors.base import ExprVisitor
from .visitors.symbolic import Z3Visitor, CVC5Visitor
from .visitors.utils import ExpressionSimplifier, TypeChecker

# Import utilities
from .utils import convert, to_variable, can_coerce, validate_value

__all__ = [
    'Expr', 'Condition', 'Predicate', 'DataType',
    'Add', 'Sub', 'Mul', 'Div',
    'And', 'Or', 'Not',
    'EQ', 'NEQ', 'GT', 'GTE', 'LT', 'LTE',
    'Is_Null', 'Is_NotNull',
    'Variable', 'Literal',
    'ExprVisitor', 'Z3Visitor', 'CVC5Visitor',
    'ExpressionSimplifier', 'TypeChecker',
    'convert', 'to_variable', 'can_coerce', 'validate_value'
]