from __future__ import annotations
# from abc import ABC, abstractmethod
import typing as t
import datetime
import logging, z3
from functools import cached_property
from .ssa_factory import create_symbol

logger = logging.getLogger(__name__)

SymbolOrLiteral = t.Union[
   z3.SortRef, str, float, int, bool, t.List, t.Tuple, datetime.date, datetime.datetime
]
Literals = t.Union[str, float, int, bool, datetime.date, datetime.datetime]

NULL_VALUES = {
    'Int' : 6789,
    'Real' : 0.6789,
    'String' : 'NULL',
    'Bool' : False,
    'Datetime' : int(round(datetime.datetime(1970, 1, 1, 0, 0, 0).timestamp())),
    'Date' : datetime.date(1970, 1, 1),
}

Z3_TYPE_MAPPINGS: t.Dict[str, z3.SortRef] = {
    'bool': z3.BoolVal,
    'int' : z3.IntVal,
    'str' : z3.StringVal,
    'string' : z3.StringVal,
    'text' : z3.StringVal,
    'real': z3.RealVal,
    'float': z3.RealVal,
    'datetime': z3.IntVal
}

T = t.TypeVar('T', bound='SymbolicType')


class _Symbolic(type):
    def __new__(cls, clsname, bases, attrs):
        klass = super().__new__(cls, clsname, bases, attrs)
        # When an Expression class is created, its dtype is automatically set to be
        # the lowercase version of the class' name.
        klass.dtype = clsname.lower()[8:].capitalize()
        # This is so that docstrings are not inherited in pdoc
        klass.__doc__ = klass.__doc__ or ""
        return klass
    

class SymbolicType(metaclass = _Symbolic):
    __slots__ = ("context", "expr", "value", '_hash')
    def __init__(self, context, expr: SymbolOrLiteral, value = None) -> None:
        self.context = context
        self.expr = expr
        self.value = value
        self._hash = None
        
    def __repr__(self) -> str:
        return "%s(%s, %s)" % (self.dtype, self.expr, self.value)

    def __str__(self) -> str:
        return str(self.expr)
    
    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(self.value)
        return self._hash

    @cached_property
    def null_value(self) -> t.Any:
        """Get the null value for this type."""
        return NULL_VALUES[self.dtype]

    @cached_property
    def z3_type(self) -> t.Callable:
        return Z3_TYPE_MAPPINGS[self.dtype.lower()]

    def nullif(self, condition: t.Union[bool, SymbolicType]) -> SymbolicType:
        """
        Return null if condition is true, otherwise return self.
        
        Args:
            condition: Boolean condition
            
        Returns:
            New symbolic value
        """
        if isinstance(condition, SymbolicType):
            condition = condition.expr
            
        null_sym = self.z3_type(self.null_value)
        expr = z3.If(condition, null_sym, self.expr)        
        return create_symbol(
            self.dtype,
            self.context, 
            expr, 
            self.null_value if condition else self.value
        )
    def is_null(self):
        null_sym = self.z3_type(self.null_value)
        return create_symbol('bool', self.context, self.expr == null_sym, self.value == self.null_value)
    
    
    def logical(self, other: t.Any, op: str):
        ops = {
            'and': (z3.And, lambda x, y: x and y),
            'or': (z3.Or, lambda x, y: x or y),
        }
        z3_op, py_op = ops[op]
        e, v = self._zv(other)
        expr = z3_op(self.expr, e)
        value = py_op(self.value, v)
        
        return create_symbol('bool', self.context, expr, value)
    
    
    def logical2(self, other, op):
        connector_ops = {
            'and' : lambda x, y, v1, v2 : (z3.And(x, y), v1 and v2),
            'or': lambda x, y, v1, v2 : (z3.Or(x, y), v1 or v2),
        }
        e, v = self._zv(other)
        e_, v_ = connector_ops[op](self.expr, e, self.value, v)
        return v_

    def _make_comparison(self, other: t.Any, op: str) -> SymbolicType:
        """
        Create a comparison operation.
        
        Args:
            other: Value to compare with
            op: Comparison operator
            
        Returns:
            New symbolic boolean value
        """
        e, v = self._zv(other)
        expr = getattr(self.expr, f"__{op}__")(e)
        value = getattr(self.value, f"__{op}__")(v)
        return create_symbol('bool', self.context, expr, value)





BOOL_OPS = [
    ('eq', '=='),
    # '__req__',
    ('ne', '!='),
    # '__rne__',
    ('gt', '>'),
    ('lt', '<'),
    ('le', '<='),
    ('ge', '>='),
]

def make_method(method, op):
    code = "def %s(self, other):\n" % method
    code += "   (expr, value) = self._zv(other)\n"
    code += "   v_ = self.value %s value\n" % op
    code += "   expr_ = self.expr %s expr\n" % op
    code += "   return ssa_factory.create_symbol(type(v_).__name__, self.context, expr_, v_)\n"
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(SymbolicType, method, locals_dict[method])

for (name, op) in BOOL_OPS:
    
    method = "__%s__" % name
    make_method(method, op)
    rmethod = "__r%s__" % name
    make_method(rmethod, op)