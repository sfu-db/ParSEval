from .base import ExprVisitor
from typing import Any, Dict, Optional, Union
from ..symbol.base import *
import z3
from datetime import date, datetime

LABELED_NULL = {
    z3.IntSort() : 6789,
    z3.RealSort() : datetime(1970, 1, 1, 0, 0, 0).timestamp(),
    z3.StringSort() : 'NULL',
    z3.BoolSort() : 'NULL',
    # 'DATETIME' : datetime(1970, 1, 1, 0, 0, 0),
    # 'DATE' : date(1970, 1, 1),
}

def coerce_to_same_sort(e1, e2):
    if e1.sort() == e2.sort():
        return e1, e2
    if e1.sort() == z3.StringSort() and e2.sort() == z3.IntSort():
        return z3.StrToInt(e1), e2
    if e2.sort() == z3.StringSort() and e1.sort() == z3.IntSort():
        return e1, z3.StrToInt(e2)
    if e1.sort() == z3.IntSort() and e2.sort() == z3.RealSort():
        return e1, e2
    if e1.sort() == z3.RealSort() and e2.sort() == z3.IntSort():
        return e1, e2
    
    raise TypeError(f"Cannot coerce: {e1.sort()} vs {e2.sort()}")
class Z3Visitor(ExprVisitor):
    """Visitor that converts expressions to Z3 formulas"""
    
    def __init__(self, variables: Optional[Dict[str, z3.ExprRef]] = None, symbols: Optional[Dict[str, z3.ExprRef]] = None):
        self.var_cache: Dict[str, Variable] = variables if variables is not None else {}
        self.symbol_cache:  Dict[str, z3.ExprRef] = symbols if symbols is not None else {}

        
    def visit_Variable(self, expr: Variable) -> Any:
        if expr.this in self.symbol_cache:
            return self.symbol_cache[expr.this]
            
        # Create Z3 variable based on type
        if expr.dtype.is_type(*DataType.INTEGER_TYPES):
            z3_var = z3.Int(expr.this)
        elif expr.dtype.is_type(*DataType.REAL_TYPES):
            z3_var = z3.Real(expr.this)
        elif expr.dtype.is_type("BOOLEAN"):
            z3_var = z3.Bool(expr.this)
        elif expr.dtype.is_type(*DataType.TEXT_TYPES):
            z3_var = z3.String(expr.this)
        elif expr.dtype.is_type(*DataType.TEMPORAL_TYPES):
            z3_var = z3.String(expr.this) # Use String for temporal types
        else:
            raise TypeError(f"Unsupported type for Z3: {expr.dtype}")
        self.symbol_cache[expr.this] = z3_var
        self.var_cache[expr.this] = expr
        return z3_var
        
    def visit_Literal(self, expr: Literal) -> Any:
        # if expr.value is None:
        #     logging.info(f'value is {expr.value} for {expr}, {expr.dtype}')
        #     return None
        if expr.dtype.is_type(*DataType.INTEGER_TYPES):
            return z3.IntVal(expr.value or LABELED_NULL[z3.IntSort()])
        if expr.dtype.is_type(*DataType.REAL_TYPES):
            return z3.RealVal(expr.value or LABELED_NULL[z3.RealSort()])
        if expr.dtype.is_type("BOOLEAN"):
            return z3.BoolVal(expr.value or LABELED_NULL[z3.BoolSort()])
        if expr.dtype.is_type(*DataType.TEXT_TYPES):
            return z3.StringVal(expr.value or LABELED_NULL[z3.StringSort()])
        
        raise TypeError(f"Unsupported literal type: {expr.dtype}")
        
    def visit_And(self, expr: And) -> Any:
        # for op in expr.operands:
        #     print(op)
        operands = [self.visit(op) for op in expr.operands]
        return z3.And(*operands)
        
    def visit_Or(self, expr: Or) -> Any:
        operands = [self.visit(op) for op in expr.operands]
        return z3.Or(*operands)
        
    def visit_Not(self, expr: Not) -> Any:
        this = expr.this
        # if isinstance(expr.this, Variable):
        #     this = this.is_null()
        # logging.info(f'visit not {this}')
        return z3.Not(self.visit(this))
        
    def visit_EQ(self, expr: EQ) -> Any:
        left = self.visit(expr.left)
        right = self.visit(expr.right)
        left, right = coerce_to_same_sort(left, right)
        return left == right
        
    def visit_NEQ(self, expr: NEQ) -> Any:
        left = self.visit(expr.left)
        right = self.visit(expr.right)
        left, right = coerce_to_same_sort(left, right)
        return left != right
        # return self.visit(expr.left) != self.visit(expr.right)
        
    def visit_LT(self, expr: LT) -> Any:
        left = self.visit(expr.left)
        right = self.visit(expr.right)
        left, right = coerce_to_same_sort(left, right)
        return left < right
        # return self.visit(expr.left) < self.visit(expr.right)
        
    def visit_LTE(self, expr: LTE) -> Any:
        # print(f'visit LTE {expr.left}, {type(self.visit(expr.left))} <= {expr.right}')
        left = self.visit(expr.left)
        right = self.visit(expr.right)
        left, right = coerce_to_same_sort(left, right)
        return left <= right
        
    def visit_GT(self, expr: GT) -> Any:
        left = self.visit(expr.left)
        right = self.visit(expr.right)
        left, right = coerce_to_same_sort(left, right)
        return left > right
        
    def visit_GTE(self, expr: GTE) -> Any:
        left = self.visit(expr.left)
        right = self.visit(expr.right)
        left, right = coerce_to_same_sort(left, right)
        return left >= right
            
    def visit_Add(self, expr: Add) -> Any:
        return self.visit(expr.left) + self.visit(expr.right)
        
    def visit_Sub(self, expr: Sub) -> Any:
        return self.visit(expr.left) - self.visit(expr.right)
        
    def visit_Mul(self, expr: Mul) -> Any:
        return self.visit(expr.left) * self.visit(expr.right)
        
    def visit_Div(self, expr: Div) -> Any:
        return self.visit(expr.left) / self.visit(expr.right)
    
    def visit_Is_Null(self, expr: Is_Null) -> Any:
        symbol = self.visit(expr.this)
        return symbol == LABELED_NULL[symbol.sort()]

    def visit_Distinct(self, expr: Distinct) -> Any:

        operands = [self.visit(op) for op in expr.operands]
        return z3.Distinct(*operands)
    
    
    
    def visit_ITE(self, expr):
        condition = self.visit(expr.this)
        true_branch = self.visit(expr.operand)
        else_branch = self.visit(expr.args.get('else_'))
        dtype = expr.args.get('else_').args.get('datatype')
        # if true_branch is None or else_branch is None or dtype is not None:
        #     if dtype.is_type(*DataType.INTEGER_TYPES):
        #         true_branch = z3.IntVal(true_branch or LABELED_NULL[z3.IntSort()])
        #         else_branch = z3.IntVal(else_branch or LABELED_NULL[z3.IntSort()])
        #     elif dtype.is_type(*DataType.REAL_TYPES):
        #         true_branch = z3.RealVal(true_branch or LABELED_NULL[z3.RealSort()])
        #         else_branch = z3.RealVal(else_branch or LABELED_NULL[z3.RealSort()])
        #     elif dtype.is_type("BOOLEAN"):
        #         true_branch = z3.BoolVal(true_branch)
        #         else_branch = z3.BoolVal(else_branch)
        #     elif dtype.is_type(*DataType.TEXT_TYPES):
        #         true_branch = z3.StringVal(true_branch)
        #         else_branch = z3.StringVal(else_branch)
        return z3.If(condition, true_branch, else_branch)
    def visit_StrToInt(self, expr):
        return z3.StrToInt(self.visit(expr.this))
    def visit_IntToStr(self, expr):
        # z3.Re
        return z3.IntToStr(self.visit(expr.this))
    
    def visit_LIKE(self, expr):
        """Convert LIKE expression to Z3 format"""
        left = self.visit(expr.left)
        pattern = expr.operand.value

        segments = []
        current_literal = []
        for c in pattern:
            if c in ('%', '_'):
                if current_literal:
                    segments.append(''.join(current_literal))
                    current_literal = []
                segments.append(c)
            else:
                current_literal.append(c)
        if current_literal:
            segments.append(''.join(current_literal))
        # Build constraints
        constraints = []
        pos = 0
        for seg in segments:
            if seg == '%':
                # Zero or more of any character
                # No constraint needed, just advances matching
                pass
            elif seg == '_':
                # Exactly one character
                constraints.append(z3.Length(z3.SubString(left, pos, 1)) == 1)
                pos += 1
            else:
                # Literal segment must match
                constraints.append(z3.SubString(left, pos, len(seg)) == seg)
                pos += len(seg)
        logging.info( z3.And(*constraints))
        return z3.And(*constraints)
    
    def visit_Strftime(self, expr):
        import re
        fmt = expr.args.get('format', '%Y-%m-%d %H:%M:%S')

        this = self.visit( expr.this )

        # year    = z3.SubString(s, 0, 4)
        # month   = z3.SubString(s, 5, 2)
        # day     = z3.SubString(s, 8, 2)
        # hour    = z3.SubString(s, 11, 2)
        # minute  = z3.SubString(s, 14, 2)
        # second  = z3.SubString(s, 17, 2)
        # ms      = z3.SubString(s, 20, 3)


        token_map = {
            "%Y": (0, 4, "year"),
            "%m": (5, 2, "month"),
            "%d": (8, 2, "day"),
            "%H": (11, 2, "hour"),
            "%M": (14, 2, "minute"),
            "%S": (17, 2, "second"),
            "%f": (20, 3, "microsecond"),  # Assuming milliseconds
        }
        # print(type(fmt))

        # Parse format into sequence of (type, token or literal)
        pattern = re.compile(r"%[YmdHMSf]|[^%]+")
        tokens = pattern.findall(fmt.value)
        idx = 0
        values = None

        for tok in tokens:
            if tok in token_map:
                start, length, label = token_map[tok]
                part = z3.StrToInt(z3.SubString(this, start, length))
                if values is None:
                    values = part
        return values