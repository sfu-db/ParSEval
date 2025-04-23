from typing import List, Set, Dict, Optional
from ..base import Expr
from ..visitors.base import ExprVisitor
from ..operators.logical import And, Or, Not, EQ, NEQ, GT, GTE, LT, LTE
from ..operators.literal import Variable, Literal
import logging

class PathCondition:
    """Represents a path condition with its constraints"""
    
    def __init__(self):
        self.constraints: List[Expr] = []
        self.variables: Set[str] = set()
        
    def add_constraint(self, constraint: Expr):
        """Add a constraint to this path condition"""
        self.constraints.append(constraint)
        # Collect variables used in the constraint
        self._collect_variables(constraint)
        
    def _collect_variables(self, expr: Expr):
        """Recursively collect variables from an expression"""
        if isinstance(expr, Variable):
            self.variables.add(expr.this)
        for child in expr.iter_expressions():
            self._collect_variables(child)
            
    def is_satisfiable(self) -> bool:
        """Check if this path condition is satisfiable"""
        # Here you could integrate with Z3 or other solvers
        # For now, we'll just return True
        return True
        
    def __str__(self) -> str:
        return " AND ".join(str(c) for c in self.constraints)

class PathTracker(ExprVisitor):
    """Visitor that tracks path conditions through the expression tree"""
    
    def __init__(self):
        self.current_path = PathCondition()
        self.all_paths: List[PathCondition] = []
        self.branch_stack: List[PathCondition] = []
        self.visited_nodes = set()
        self.max_depth = 1000  # Prevent infinite recursion
        
    def push_condition(self, condition: Expr):
        """Push a new condition onto the current path"""
        if len(self.current_path.constraints) >= self.max_depth:
            raise ValueError("Maximum path depth exceeded - possible infinite recursion")
        self.current_path.add_constraint(condition)
        
    def push_branch(self):
        """Save current path for branching"""
        if len(self.branch_stack) >= self.max_depth:
            raise ValueError("Maximum branch depth exceeded - possible infinite recursion")
        self.branch_stack.append(self.current_path)
        self.current_path = PathCondition()
        for constraint in self.branch_stack[-1].constraints:
            self.current_path.add_constraint(constraint)
            
    def pop_branch(self):
        """Restore path from before branching"""
        if not self.branch_stack:
            raise ValueError("Attempted to pop branch with empty stack")
        if self.current_path.constraints:
            self.all_paths.append(self.current_path)
        self.current_path = self.branch_stack.pop()
        
    def visit_And(self, expr: And) -> None:
        """Visit AND expression - add all conditions to current path"""
        if expr in self.visited_nodes:
            return
        self.visited_nodes.add(expr)
        
        try:
            for op in expr.operands:
                op.accept(self)
        except Exception as e:
            logging.error(f"Error processing AND expression: {e}")
            raise
            
    def visit_Or(self, expr: Or) -> None:
        """Visit OR expression - branch for each condition"""
        if expr in self.visited_nodes:
            return
        self.visited_nodes.add(expr)
        
        try:
            for i, op in enumerate(expr.operands):
                if i > 0:
                    self.push_branch()
                op.accept(self)
                if i < len(expr.operands) - 1:
                    self.pop_branch()
        except Exception as e:
            logging.error(f"Error processing OR expression: {e}")
            raise
                
    def visit_Not(self, expr: Not) -> None:
        """Visit NOT expression - negate the condition"""
        if expr in self.visited_nodes:
            return
        self.visited_nodes.add(expr)
        
        try:
            # For simple conditions, we can negate them directly
            inner = expr.this
            if isinstance(inner, (EQ, NEQ, GT, GTE, LT, LTE)):
                negated = self._negate_condition(inner)
                self.push_condition(negated)
            else:
                # For complex conditions, just store the NOT
                self.push_condition(expr)
        except Exception as e:
            logging.error(f"Error processing NOT expression: {e}")
            raise
            
    def _negate_condition(self, expr: Expr) -> Expr:
        """Negate a simple comparison condition"""
        if isinstance(expr, EQ):
            return NEQ(context=expr.context, this=expr.left, operand=expr.right)
        if isinstance(expr, NEQ):
            return EQ(context=expr.context, this=expr.left, operand=expr.right)
        if isinstance(expr, GT):
            return LTE(context=expr.context, this=expr.left, operand=expr.right)
        if isinstance(expr, GTE):
            return LT(context=expr.context, this=expr.left, operand=expr.right)
        if isinstance(expr, LT):
            return GTE(context=expr.context, this=expr.left, operand=expr.right)
        if isinstance(expr, LTE):
            return GT(context=expr.context, this=expr.left, operand=expr.right)
        return Not(context=expr.context, this=expr)
            
    def visit_EQ(self, expr: EQ) -> None:
        self.push_condition(expr)
        
    def visit_NEQ(self, expr: NEQ) -> None:
        self.push_condition(expr)
        
    def visit_GT(self, expr: GT) -> None:
        self.push_condition(expr)
        
    def visit_GTE(self, expr: GTE) -> None:
        self.push_condition(expr)
        
    def visit_LT(self, expr: LT) -> None:
        self.push_condition(expr)
        
    def visit_LTE(self, expr: LTE) -> None:
        self.push_condition(expr)
        
    def generic_visit(self, expr: Expr) -> None:
        """Default behavior - do nothing"""
        pass 

    def get_all_paths(self) -> List[PathCondition]:
        """Get all valid paths through the expression"""
        # Ensure we capture the final path if it has constraints
        if self.current_path.constraints:
            self.all_paths.append(self.current_path)
        return self.all_paths 