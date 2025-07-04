from typing import Any, Dict, Optional, Union
from ..symbol.base import Expr, Or, And, Not, Distinct, distinct, Variable
from .base import ExprVisitor

class SubstitutionVisitor(ExprVisitor):
    """
    Visitor that substitutes expressions with new expressions.
    
    Usage:
        # Variable substitution
        visitor = SubstitutionVisitor({
            Variable(this='old_var'): Variable(this='new_var')
        }, inplace=True)
        
        # Expression substitution
        visitor = SubstitutionVisitor({
            old_expr: new_expr
        }, inplace=True)
        
        new_expr = old_expr.transform(visitor)
    """
    
    def __init__(self, substitutions: Dict[Expr, Expr], inplace: bool = False):
        """
        Initialize with a mapping of expressions to their replacements.
        
        Args:
            substitutions: Dictionary mapping expressions to their replacements
            inplace: If True, modify expressions in place instead of creating new ones
        """
        self.substitutions = substitutions
        self.inplace = inplace
    
    def _should_substitute(self, expr: Expr) -> Optional[Expr]:
        """
        Check if an expression should be substituted.
        Returns the replacement expression if substitution should occur, None otherwise.
        """
        for old_expr, new_expr in self.substitutions.items():
            if expr.equals(old_expr):
                # If the replacement is an expression, transform it to handle nested substitutions
                if isinstance(new_expr, Expr):
                    return new_expr.transform(self)
                return new_expr
                
        return None
    
    def visit(self, expr: Expr) -> Expr:
        """
        Visit an expression and potentially substitute it.
        If no substitution occurs, visit its children.
        """
        # First check if this expression should be substituted
        replacement = self._should_substitute(expr)
        if replacement is not None:
            return replacement
            
        # If no substitution needed, visit children
        return super().visit(expr)
    
    def visit_Binary(self, expr: Expr) -> Expr:
        """Transform binary expressions by visiting their operands"""
        left = expr.left.transform(self)
        right = expr.right.transform(self)
        
        if self.inplace:
            # Update the existing expression in place
            expr.set('this', left)
            expr.set('operand', right)
            return expr
        else:
            # Create new expression with transformed operands
            new_expr = expr.__class__(
                this=left,
                operand=right,
                value=expr.value
            )
            # Set parent and arg_key relationships
            left.parent = new_expr
            left.arg_key = 'this'
            right.parent = new_expr
            right.arg_key = 'operand'
            return new_expr
    
    def visit_Nary(self, expr: Expr) -> Expr:
        """Transform n-ary expressions by visiting all operands"""
        transformed_ops = [op.transform(self) for op in expr.operands]
        
        if self.inplace:
            # Update the existing expression in place
            expr.set('operands', transformed_ops)
            return expr
        else:
            # Create new expression with transformed operands
            new_expr = expr.__class__(
                operands=transformed_ops,
                value=expr.value
            )
            # Set parent and arg_key relationships
            for i, op in enumerate(transformed_ops):
                op.parent = new_expr
                op.arg_key = f'operands[{i}]'
            return new_expr
    
    def visit_Unary(self, expr: Expr) -> Expr:
        """Transform unary expressions by visiting their operand"""
        operand = expr.this.transform(self)
        
        if self.inplace:
            # Update the existing expression in place
            expr.set('this', operand)
            return expr
        else:
            # Create new expression with transformed operand
            new_expr = expr.__class__(
                this=operand,
                value=expr.value
            )
            # Set parent and arg_key relationships
            operand.parent = new_expr
            operand.arg_key = 'this'
            return new_expr
    
    def visit_And(self, expr: Expr) -> Expr:
        """Transform And expressions by visiting their operands"""
        return self.visit_Nary(expr)
        
    def visit_Or(self, expr: Expr) -> Expr:
        """Transform Or expressions by visiting their operands"""
        return self.visit_Nary(expr)
    def visit_StrToInt(self, expr: Expr) -> Expr:
        return self.visit_Unary(expr)
    
    def visit_IntToStr(self, expr: Expr)    -> Expr:
        return self.visit_Unary(expr)
    
    def visit_Strftime(self, expr: Expr):
        operand = expr.this.transform(self)
        
        if self.inplace:
            # Update the existing expression in place
            expr.set('this', operand)
            return expr
        else:
            # Create new expression with transformed operand
            new_expr = expr.__class__(
                this=operand,
                value=expr.value,
                format = expr.args.get('format')
            )
            # Set parent and arg_key relationships
            operand.parent = new_expr
            operand.arg_key = 'this'
            # operand.format = 
            return new_expr
        
        return self.visit_Unary(expr)

    visit_Is_Null = visit_Not = visit_Unary
    def generic_visit(self, expr: Expr) -> Any:
        return expr
    
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
        'LTE': '≤',
        'LIKE': 'LIKE'
    }
for op in BINARY_OPS:
    setattr(SubstitutionVisitor, f'visit_{op}' , getattr(SubstitutionVisitor, 'visit_Binary'))


def substitute(expr: Expr, substitutions: Dict[Expr, Expr], inplace: bool = False):
    visitor = SubstitutionVisitor(substitutions, inplace= inplace)
    return expr.transform(visitor)


def extend_summation(expr: Expr, substitutions: Dict[Expr, Expr], inplace: bool = False, extend = False):
    '''
        Extend the existing expression with new variables.
        The new variables are added to the existing expression.
        Args:
            existing_expr: The existing expression.
            varis: The new variables (src, tar) to add to the expression.
        Returns:
            The extended expression.
        Example Usage:
        >>> expr: Or(Or(a0 == b0, a0 == b1), Or(a0 == b2, a0 == b3))
        >>> src: a0
        >>> tar: a1
        >>> result: Or(Or(a0 == b0, a0 == b1), Or(a0 == b2, a0 == b3), a1 == b0, a1 == b1, a1 == b2, a1 == b3)        
    '''
    
    if isinstance(expr, Or):
        operands = set()
        for op in expr.operands:
            if extend:
                operands.add(op)
            for src, tar in substitutions.items():
                if str(src) in str(op):
                    operands.add(extend_summation(op, substitutions, inplace, extend))
        if extend:
            operands.update(expr.operands)
        return Or(operands = list(operands), value = any(operands))
    elif isinstance(expr, And):
        operands = []
        for op in expr.operands:
            operands.append(extend_summation(op, substitutions, inplace))
        return And(operands = operands, value = all(operands))
    elif isinstance(expr, Not):
        return Not(this = extend_summation(expr.this, substitutions, inplace))
    else:
        return substitute(expr, substitutions, inplace)
    

def extend_distinct(expr: Union[Distinct, Variable], substitutions: Dict[Expr, Expr], inplace: bool = False, extend = False):
    assert isinstance(expr, (Distinct, Variable)), f"should only handle distinct expression, get {type(expr)}"
    operands = set()

    if isinstance(expr, Variable):
        expr = distinct([expr])
        
    # operands.add(substitute(replaced, substitutions))
    
    for operand in expr.operands:
        operands.add(operand)
        operands.add(substitute(operand, substitutions))
    
    return distinct(list(operands))

        

