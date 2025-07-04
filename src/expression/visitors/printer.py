from typing import Any, TYPE_CHECKING
from ..types import DataType
if TYPE_CHECKING:
    from ..symbol.base import Expr

from .base import ExprVisitor

class PrinterVistor(ExprVisitor):
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
    # SYMBOLS = {
    #     'Add': '+',
    #     'Sub': '-',
    #     'Mul': '*',
    #     'Div': '/',
    #     'EQ': '=',
    #     'NEQ': '≠',
    #     'GT': '>',
    #     'GTE': '≥',
    #     'LT': '<',
    #     'LTE': '≤',
    #     'And': 'AND',
    #     'Or': 'OR',
    #     'Not': 'NOT',
    #     'Neg': '-',
    # }
    SYMBOLS = {}
    def _get_precedence(self, expr) -> int:
        """Get the precedence level of an expression"""
        return self.PRECEDENCE.get(expr.__class__.__name__, 10)

    def _parenthesize_if_needed(self, expr, child_expr, child_str: str) -> str:
        """Add parentheses if needed based on operator precedence"""
        if self._get_precedence(expr) > self._get_precedence(child_expr):
            return f"({child_str})"
        return child_str

    def visit_Variable(self, expr) -> str:
        return str(expr.this)
    def visit_Literal(self, expr) -> str:
        if expr.dtype is None:
            return str(expr.this)
        
        if expr.dtype.is_type(*DataType.TEXT_TYPES):
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
        return f"{op_symbol}({left}, {right})"

    def visit_And(self, expr) -> str:
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
            
        return f"AND({', '.join(parts)})"

    def visit_Or(self, expr) -> str:
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
            
        return f"OR({', '.join(parts)})"
    
    def visit_Not(self, expr) -> str:
        """Format NOT operation"""
        inner = self.visit(expr.this)
        inner = self._parenthesize_if_needed(expr, expr.this, inner)
        return f"NOT {inner}"

    def visit_Neg(self, expr) -> str:
        """Format negation operation"""
        inner = self.visit(expr.this)
        inner = self._parenthesize_if_needed(expr, expr.this, inner)
        return f"-{inner}"
    def visit_Is_Null(self, expr) -> str:
        """Format IS NULL check"""
        inner = self.visit(expr.this)
        return f"{inner} IS NULL"

    def visit_Is_NotNull(self, expr) -> str:
        """Format IS NOT NULL check"""
        inner = self.visit(expr.this)
        return f"{inner} IS NOT NULL"
    def visit_Distinct(self, expr) -> str:
        """Format DISTINCT operation"""
        parts = []
        for op in expr.operands:
            part = self.visit(op)
            part = self._parenthesize_if_needed(expr, op, part)
            parts.append(part)
        return f"DISTINCT({', '.join(parts)})"
    def visit_ITE(self, expr):
        """Format IF-THEN-ELSE operation"""
        condition = self.visit(expr.this)
        operand = self.visit(expr.operand)
        else_ = self.visit(expr.args.get('else_'))
        return f"CASE WHEN {condition} THEN {operand} ELSE {else_} END"
    
    def visit_StrToInt(self, expr):
        return f"StrToInt({self.visit(expr.this)})"
    def visit_IntToStr(self, expr):
        return f"IntToStr({self.visit(expr.this)})"
    def visit_LIKE(self, expr):
        return f"{self.visit(expr.this)} LIKE {self.visit(expr.operand)}"
    
    def visit_Strftime(self, expr):
        return f"STRFTIME({self.visit(expr.this)})"
    
BINARY_OPS =  {
        'Add': '+',
        'Sub': '-',
        'Mul': '*',
        'Div': '/',
        'EQ': '=',
        'NEQ': '≠',
        'GT': '>',
        'GTE': '≥',
        'LT': '<',
        'LTE': '≤'
    }
for op in BINARY_OPS:
    setattr(PrinterVistor, f'visit_{op}' , getattr(PrinterVistor, 'visit_Binary'))