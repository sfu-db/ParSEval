from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..symbol.base import Expr

class ExprVisitor:
    """Base visitor class for traversing and transforming expressions"""

    
    def visit(self, expr: 'Expr') -> Any:
        """Visit an expression node"""
        method = f'visit_{expr.__class__.__name__}'
        visitor = getattr(self, method, self.generic_visit)
        return visitor(expr)
    
    def generic_visit(self, expr: 'Expr') -> Any:
        """Default visit method for unhandled expression types"""
        raise NotImplementedError(
            f"No visit method for {expr.__class__.__name__}"
        )