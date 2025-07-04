from __future__ import annotations
from typing import Any, Dict, Optional, List, Iterator, TYPE_CHECKING, Mapping
from copy import deepcopy

if TYPE_CHECKING:
    from .operators import types as op_types
    from .dtypes.dtype import DataType
    from .visitors.base import ExprVisitor

class _Expr(type):
    def __new__(cls, clsname, bases, attrs):
        klass = super().__new__(cls, clsname, bases, attrs)
        klass.key = clsname.lower().capitalize()
        klass.__doc__ = klass.__doc__ or ""
        return klass

class Expr(metaclass=_Expr):
    """Base class for all expressions"""
    key = "Expr"
    arg_types = {"context": True, "this": True, "value": True}
    __slots__ = ("args", "parent", "arg_key", "index", "_type")

    def __init__(self, **args: Any):
        self.args: Dict[str, Any] = args
        self.parent: Optional[Expr] = None
        self.arg_key: Optional[str] = None
        self.index: Optional[int] = None
        for arg_key, value in self.args.items():
            self._set_parent(arg_key, value)
        self._type: Optional[DataType] = self.args.get('_type', None)

    @property
    def dtype(self) -> Optional[DataType]:
        """Get the data type of this expression"""
        if self._type is None:
            self._type = self.resolve_type()
        return self._type

    # ... (rest of the base Expr implementation)
    
    def accept(self, visitor: 'ExprVisitor') -> Any:
        """Accept a visitor and return its result"""
        return visitor.visit(self)

    def transform(self, visitor: 'ExprVisitor') -> 'Expr':
        """Transform this expression using a visitor"""
        return visitor.visit(self)

    def __lt__(self, other: Any) -> Expr:
        from .operators import types as op_types
        return self._apply_operation('lt', other, op_types.LT)

    def __gt__(self, other: Any) -> Expr:
        from .operators import types as op_types
        return self._apply_operation('gt', other, op_types.GT)

    def __le__(self, other: Any) -> Expr:
        from .operators import types as op_types
        return self._apply_operation('le', other, op_types.LTE)

    def __ge__(self, other: Any) -> Expr:
        from .operators import types as op_types
        return self._apply_operation('ge', other, op_types.GTE)

    def __eq__(self, other: Any) -> Expr:
        if other is None:
            from .operators.logical import Is_Null
            return Is_Null(context=self.context, this=self, value=self.value is None)
        from .operators import types as op_types
        return self._apply_operation('eq', other, op_types.EQ)

    def __ne__(self, other: Any) -> Expr:
        if other is None:
            from .operators.logical import Is_NotNull
            return Is_NotNull(context=self.context, this=self, value=self.value is not None)
        from .operators import types as op_types
        return self._apply_operation('ne', other, op_types.NEQ)

    def __add__(self, other: Any) -> Expr:
        from .operators import types as op_types
        return self._apply_operation('add', other, op_types.Add)

    def __sub__(self, other: Any) -> Expr:
        from .operators import types as op_types
        return self._apply_operation('sub', other, op_types.Sub)

    def __mul__(self, other: Any) -> Expr:
        from .operators import types as op_types
        return self._apply_operation('mul', other, op_types.Mul)

    def __truediv__(self, other: Any) -> Expr:
        from .operators import types as op_types
        return self._apply_operation('truediv', other, op_types.Div)

    def __deepcopy__(self, memo: Dict) -> 'Expr':
        """
        Create a deep copy of the expression.
        
        Args:
            memo: Dictionary of id to object mappings to handle recursive structures
            
        Returns:
            A new copy of the expression with all nested expressions copied
        """
        # Check memo dictionary to handle recursive structures
        if id(self) in memo:
            return memo[id(self)]
            
        # Create new args dict with deep copied values
        new_args = {}
        for key, value in self.args.items():
            if isinstance(value, list):
                # Handle lists of expressions
                new_args[key] = [
                    deepcopy(item, memo) if hasattr(item, '__deepcopy__') else deepcopy(item, memo)
                    for item in value
                ]
            else:
                # Handle single values
                new_args[key] = (
                    deepcopy(value, memo) if hasattr(value, '__deepcopy__') else deepcopy(value, memo)
                )
        
        # Create new instance
        new_expr = self.__class__(**new_args)
        
        # Store in memo to handle recursive structures
        memo[id(self)] = new_expr
        
        # Copy type if it exists
        if self._type is not None:
            new_expr._type = deepcopy(self._type, memo)
            
        return new_expr

    def copy(self) -> 'Expr':
        """
        Returns a deep copy of the expression.
        This is a convenience wrapper around __deepcopy__.
        """
        return deepcopy(self)

class Condition(Expr):
    """Base class for logical conditions"""
    pass

class Predicate(Condition):
    """Base class for relationships"""
    pass 