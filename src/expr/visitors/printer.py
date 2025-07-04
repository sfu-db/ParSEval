from typing import Any, Dict, Optional
from ..base import Expr
from ..visitors.base import ExprVisitor
from ..operators.logical import And, Or, Not, EQ, NEQ, GT, GTE, LT, LTE, Is_Null, Is_NotNull
from ..operators.arithmetic import Add, Sub, Mul, Div
from ..operators.literal import Variable, Literal

class PrinterVisitor(ExprVisitor):
    """Visitor that creates readable string representations of expressions"""

    # Define operator precedence (higher number = higher precedence)
    PRECEDENCE = {
        'Or': 1,
        'And': 2,
        'Not': 3,
        'EQ': 4, 'NEQ': 4,
        'GT': 5, 'GTE': 5, 'LT': 5, 'LTE': 5,
        'Add': 6, 'Sub': 6,
        'Mul': 7, 'Div': 7,
        'Neg': 8,
        'Is_Null': 9, 'Is_NotNull': 9,
    }

    # Define operator symbols
    SYMBOLS = {
        'Add': '+',
        'Sub': '-',
        'Mul': '*',
        'Div': '/',
        'EQ': '=',
        'NEQ': '≠',
        'GT': '>',
        'GTE': '≥',
        'LT': '<',
        'LTE': '≤',
        'And': 'AND',
        'Or': 'OR',
        'Not': 'NOT',
        'Neg': '-',
    }

    def __init__(self, use_symbols: bool = True):
        """
        Initialize the printer visitor
        
        Args:
            use_symbols: Whether to use symbols (≠, ≥, ≤) or text (!=, >=, <=)
        """
        self.use_symbols = use_symbols
        if not use_symbols:
            self.SYMBOLS.update({
                'NEQ': '!=',
                'GTE': '>=',
                'LTE': '<='
            })

    def _get_precedence(self, expr: Expr) -> int:
        """Get the precedence level of an expression"""
        return self.PRECEDENCE.get(expr.__class__.__name__, 10)

    def _parenthesize_if_needed(self, expr: Expr, child_expr: Expr, child_str: str) -> str:
        """Add parentheses if needed based on operator precedence"""
        if self._get_precedence(expr) > self._get_precedence(child_expr):
            return f"({child_str})"
        return child_str

    def visit_Variable(self, expr: Variable) -> str:
        """Format variable reference"""
        return str(expr.this)

    def visit_Literal(self, expr: Literal) -> str:
        """Format literal value based on its type"""
        if expr.dtype is None:
            return str(expr.this)
        
        if expr.dtype.is_text:
            return f"'{expr.value}'"
        elif expr.value is None:
            return "NULL"
        elif expr.dtype.is_type("BOOLEAN"):
            return str(expr.value).lower()
        
        return str(expr.value)

    def visit_Binary(self, expr: Any) -> str:
        """Format binary operation with proper operator and parentheses"""
        op_symbol = self.SYMBOLS.get(expr.__class__.__name__, expr.__class__.__name__)
        
        left = self.visit(expr.left)
        right = self.visit(expr.right)
        
        left = self._parenthesize_if_needed(expr, expr.left, left)
        right = self._parenthesize_if_needed(expr, expr.right, right)
        
        return f"{left} {op_symbol} {right}"

    def visit_And(self, expr: And) -> str:
        """Format AND operation"""
        if not expr.operands:
            return "TRUE"
        if len(expr.operands) == 1:
            return self.visit(expr.operands[0])
            
        parts = []
        for op in expr.operands:
            part = self.visit(op)
            part = self._parenthesize_if_needed(expr, op, part)
            parts.append(part)
            
        return f"{' AND '.join(parts)}"

    def visit_Or(self, expr: Or) -> str:
        """Format OR operation"""
        if not expr.operands:
            return "FALSE"
        if len(expr.operands) == 1:
            return self.visit(expr.operands[0])
            
        parts = []
        for op in expr.operands:
            part = self.visit(op)
            part = self._parenthesize_if_needed(expr, op, part)
            parts.append(part)
            
        return f"{' OR '.join(parts)}"

    def visit_Not(self, expr: Not) -> str:
        """Format NOT operation"""
        inner = self.visit(expr.this)
        inner = self._parenthesize_if_needed(expr, expr.this, inner)
        return f"NOT {inner}"

    def visit_Neg(self, expr: Not) -> str:
        """Format negation operation"""
        inner = self.visit(expr.this)
        inner = self._parenthesize_if_needed(expr, expr.this, inner)
        return f"-{inner}"

    def visit_Is_Null(self, expr: Is_Null) -> str:
        """Format IS NULL check"""
        inner = self.visit(expr.this)
        return f"{inner} IS NULL"

    def visit_Is_NotNull(self, expr: Is_NotNull) -> str:
        """Format IS NOT NULL check"""
        inner = self.visit(expr.this)
        return f"{inner} IS NOT NULL"

    def generic_visit(self, expr: Expr) -> str:
        """Default format for unhandled expression types"""
        return str(expr) 