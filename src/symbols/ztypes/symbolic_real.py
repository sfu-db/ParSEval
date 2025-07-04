from __future__ import annotations
from ..base import *
import typing as t
import logging, random
logger = logging.getLogger(__name__)

class SymbolicReal(SymbolicType):
    def __init__(self, context, expr, value=None) -> None:
        if value is None:
            try:
                value = float(expr)
            except Exception as e:
                value = random.uniform(1, 100)
        super().__init__(context, expr, value)
    def __bool__(self):
        if self != 0:
            return True
        return False
    def __int__(self):
        return self.value
    
    def negate(self):
        value = -self.value
        expr = 0.0 - self.expr
        return SymbolicReal(self.context, expr, value)

    # def to_str(self):
    #     value = str(self.value)
    #     expr = z3.IntToStr(self.expr)
    #     return ssa_factory.create_symbol('string', self.context, expr, value)

    def _zv(self, other: SymbolOrLiteral):
        if isinstance(other, SymbolicType):
            return (other.expr, other.value)
        if isinstance(other, float):
            return (z3.RealVal(other), float(other))
        if isinstance(other, int):
            return (z3.IntVal(other), other)

    def _make_binary(self, other: t.Any, op: str) -> SymbolicType:
        """
        Create a binary operation.
        
        Args:
            other: Value to compare with
            op: Comparison operator
            
        Returns:
            New symbolic boolean value
        """
        e, v = self._zv(other)
        expr = getattr(self.expr, f"__{op}__")(e)
        value = getattr(self.value, f"__{op}__")(v)
        return create_symbol(type(value).__name__, self.context, expr, value)
    
ops =  [("add","+"),\
	("sub",    "-"  ),\
	("mul",    "*"  ),\
	("mod",    "%"  ),\
    ("truediv", "/"),
	("floordiv", "//" ),\
	("and",    "&"  ),\
	("or",     "|"  ),\
	("xor",    "^"  ),\
	("lshift", "<<" ),\
	("rshift", ">>" ) ]
for (name, op) in ops:
    def make_method(op_name: str) -> t.Callable:
        def method(self: SymbolicType, other: t.Any) -> SymbolicType:
            return self._make_comparison(other, op_name)
        return method
    setattr(SymbolicReal, f"__{name}__", make_method(name))
    setattr(SymbolicReal, f"__r{name}__", make_method(name))