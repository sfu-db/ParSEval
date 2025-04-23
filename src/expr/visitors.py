from typing import Any, Dict, Optional, Union
from .exprs import *

class ExprVisitor:
    """Base visitor class for traversing and transforming expressions"""
    
    def visit(self, expr: Expr) -> Any:
        """Visit an expression node"""
        method = f'visit_{expr.__class__.__name__}'
        visitor = getattr(self, method, self.generic_visit)
        return visitor(expr)
    
    def generic_visit(self, expr: Expr) -> Any:
        """Default visit method for unhandled expression types"""
        raise NotImplementedError(
            f"No visit method for {expr.__class__.__name__}"
        )


class Z3Visitor(ExprVisitor):
    """Visitor that converts expressions to Z3 formulas"""
    
    def __init__(self):
        import z3
        self.z3 = z3
        self.var_cache: Dict[str, Any] = {}
        
    def visit_Variable(self, expr: Variable) -> Any:
        if expr.this in self.var_cache:
            return self.var_cache[expr.this]
            
        # Create Z3 variable based on type
        if expr.dtype.is_type(*DataType.INTEGER_TYPES):
            z3_var = self.z3.Int(expr.this)
        elif expr.dtype.is_type(*DataType.REAL_TYPES):
            z3_var = self.z3.Real(expr.this)
        elif expr.dtype.is_type("BOOLEAN"):
            z3_var = self.z3.Bool(expr.this)
        else:
            raise TypeError(f"Unsupported type for Z3: {expr.dtype}")
            
        self.var_cache[expr.this] = z3_var
        return z3_var
        
    def visit_Literal(self, expr: Literal) -> Any:
        if expr.value is None:
            return None
        if expr.dtype.is_type(*DataType.INTEGER_TYPES):
            return self.z3.IntVal(expr.value)
        if expr.dtype.is_type(*DataType.REAL_TYPES):
            return self.z3.RealVal(expr.value)
        if expr.dtype.is_type("BOOLEAN"):
            return self.z3.BoolVal(expr.value)
        raise TypeError(f"Unsupported literal type: {expr.dtype}")
        
    def visit_And(self, expr: And) -> Any:
        operands = [self.visit(op) for op in expr.operands]
        return self.z3.And(*operands)
        
    def visit_Or(self, expr: Or) -> Any:
        operands = [self.visit(op) for op in expr.operands]
        return self.z3.Or(*operands)
        
    def visit_Not(self, expr: Not) -> Any:
        return self.z3.Not(self.visit(expr.this))
        
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

class ExpressionSimplifier(ExprVisitor):
    """Visitor that performs basic expression simplification"""
    
    def visit_And(self, expr: And) -> Expr:
        # Remove duplicate operands
        unique_ops = []
        seen = set()
        for op in expr.operands:
            op_str = str(op)
            if op_str not in seen:
                seen.add(op_str)
                unique_ops.append(op)
                
        if len(unique_ops) == 1:
            return unique_ops[0]
        
        return And(context=expr.context, operands=unique_ops)

class TypeChecker(ExprVisitor):
    """Visitor that validates expression types"""
    
    def visit_Binary(self, expr: Binary) -> None:
        left_type = expr.left.accept(self)
        right_type = expr.right.accept(self)
        
        if not can_coerce(left_type, right_type):
            raise TypeMismatchError(
                f"Incompatible types in {expr.__class__.__name__}: "
                f"{left_type} and {right_type}"
            ) 