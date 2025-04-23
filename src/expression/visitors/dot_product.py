from typing import List, Union
from ..symbol.base import Expr, Binary, Add, Mul
from .base import ExprVisitor

class DotProductExtender(ExprVisitor):
    """
    A visitor that extends dot product expressions into their expanded form.
    For example, [a,b,c]·[x,y,z] -> a*x + b*y + c*z
    """
    
    def visit_Binary(self, expr: Binary) -> Expr:
        """
        Extend a dot product expression into its expanded form.
        """
        # Check if this is a dot product operation
        if expr.key == "Dot":
            left = expr.left
            right = expr.right
            
            # Ensure both operands are lists
            if not (isinstance(left, List) and isinstance(right, List)):
                raise ValueError("Dot product requires two list operands")
            
            # Ensure lists have same length
            if len(left) != len(right):
                raise ValueError("Dot product requires lists of equal length")
            
            # Create the expanded form: a1*b1 + a2*b2 + ... + an*bn
            terms = []
            for a, b in zip(left, right):
                terms.append(Mul(this=a, operand=b))
            
            # Combine terms with addition
            result = terms[0]
            for term in terms[1:]:
                result = Add(this=result, operand=term)
            
            return result
        
        # For non-dot product expressions, visit children normally
        return super().visit_Binary(expr)

def extend_dot_product(expr: Expr) -> Expr:
    """
    Extend a dot product expression into its expanded form.
    
    Args:
        expr: The expression to transform
        
    Returns:
        The expanded form of the expression
    """
    visitor = DotProductExtender()
    return expr.transform(visitor) 