from .base import ExprVisitor
from typing import Any, Dict, Optional, Union
from ..symbol.base import *
import z3

class Z3Visitor(ExprVisitor):
    """Visitor that converts expressions to Z3 formulas"""
    
    def __init__(self):
        self.var_cache: Dict[str, Any] = {}
        
    def visit_Variable(self, expr: Variable) -> Any:
        if expr.this in self.var_cache:
            return self.var_cache[expr.this]
            
        # Create Z3 variable based on type
        if expr.dtype.is_type(*DataType.INTEGER_TYPES):
            z3_var = z3.Int(expr.this)
        elif expr.dtype.is_type(*DataType.REAL_TYPES):
            z3_var = z3.Real(expr.this)
        elif expr.dtype.is_type("BOOLEAN"):
            z3_var = z3.Bool(expr.this)
        else:
            raise TypeError(f"Unsupported type for Z3: {expr.dtype}")
            
        self.var_cache[expr.this] = z3_var
        return z3_var
        
    def visit_Literal(self, expr: Literal) -> Any:
        if expr.value is None:
            return None
        if expr.dtype.is_type(*DataType.INTEGER_TYPES):
            return z3.IntVal(expr.value)
        if expr.dtype.is_type(*DataType.REAL_TYPES):
            return z3.RealVal(expr.value)
        if expr.dtype.is_type("BOOLEAN"):
            return z3.BoolVal(expr.value)
        raise TypeError(f"Unsupported literal type: {expr.dtype}")
        
    def visit_And(self, expr: And) -> Any:
        operands = [self.visit(op) for op in expr.operands]
        return z3.And(*operands)
        
    def visit_Or(self, expr: Or) -> Any:
        operands = [self.visit(op) for op in expr.operands]
        return z3.Or(*operands)
        
    def visit_Not(self, expr: Not) -> Any:
        return z3.Not(self.visit(expr.this))
        
    def visit_EQ(self, expr: EQ) -> Any:
        return self.visit(expr.left) == self.visit(expr.right)
        
    def visit_NEQ(self, expr: NEQ) -> Any:
        return self.visit(expr.left) != self.visit(expr.right)
        
    def visit_LT(self, expr: LT) -> Any:
        return self.visit(expr.left) < self.visit(expr.right)
        
    def visit_LTE(self, expr: LTE) -> Any:
        return self.visit(expr.left) <= self.visit(expr.right)
        
    def visit_GT(self, expr: GT) -> Any:
        return self.visit(expr.left) > self.visit(expr.right)
        
    def visit_GTE(self, expr: GTE) -> Any:
        return self.visit(expr.left) >= self.visit(expr.right)
        
    def visit_Add(self, expr: Add) -> Any:
        return self.visit(expr.left) + self.visit(expr.right)
        
    def visit_Sub(self, expr: Sub) -> Any:
        return self.visit(expr.left) - self.visit(expr.right)
        
    def visit_Mul(self, expr: Mul) -> Any:
        return self.visit(expr.left) * self.visit(expr.right)
        
    def visit_Div(self, expr: Div) -> Any:
        return self.visit(expr.left) / self.visit(expr.right)
