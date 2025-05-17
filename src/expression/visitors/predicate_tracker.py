from typing import List, Any
from .base import ExprVisitor
from ..symbol.base import Expr, GT, GTE, LT, LTE, EQ, NEQ, Predicate, Nary

class PredicateTracker(ExprVisitor):
    """Visitor that builds a path of conditions by traversing the expression AST"""
    
    def __init__(self):
        self.predicates: List[Predicate] = []

        self.visited_nodes = set()
        
    def reset(self):
        """Reset the path to empty"""
        self.predicates = []

    def generic_visit(self, expr: Expr) -> Any:
        """Default visit method for non-condition expressions"""
        # For non-condition expressions, just visit their children
        for k, vs in expr.args.items():
            if k in ["this", "operand"] and hasattr(vs, "parent"):
                self.visit(vs)
            elif k == 'operand':
                for v in vs:
                    if hasattr(v, "parent"):
                        self.visit(v)
        return expr
    
    def push_condition(self, condition: Expr):
        """Push a new condition onto the current path"""
        self.predicates.append(condition)
       
    def visit_GT(self, expr) -> None:
        if str(expr) in self.visited_nodes:
            return
        
        self.visit(expr.left)
        self.visit(expr.right)

        self.push_condition(expr)
        self.visited_nodes.add(str(expr))

    
    def visit_Not(self, expr) -> None:
        if str(expr) in self.visited_nodes:
            return
        operand = expr.this
        if isinstance(operand, Predicate):
            self.push_condition(operand.not_())
        elif isinstance(operand, Nary):
            self.push_condition(expr)
        
        self.visited_nodes.add(str(expr))
        self.visited_nodes.add(str(operand))
        self.visit(operand)

    def visit_And(self, expr) -> None:
        """Visit AND expression - add all conditions to current path"""
        if str(expr) in self.visited_nodes:
            return
        self.visited_nodes.add(str(expr))
        try:
            for op in expr.operands:
                self.visit(op)
        except Exception as e:
            raise
    def visit_Is_Null(self, expr) -> None:
        if str(expr) in self.visited_nodes:
            return
        self.visited_nodes.add(str(expr))
        self.push_condition(expr)

    visit_Or = visit_And
    visit_GTE = visit_LTE = visit_LT = visit_EQ = visit_NEQ = visit_GT 


def get_predicates(expr: Expr) -> List[Predicate]:
    visitor = PredicateTracker()
    expr.accept(visitor)
    return visitor.predicates