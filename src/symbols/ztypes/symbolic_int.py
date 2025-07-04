from __future__ import annotations
from ..base import *
from ..ssa_factory import  create_symbol

import logging, random
logger = logging.getLogger('src.parseval.symbol')

class SymbolicInt(SymbolicType):
    def __init__(self, context, expr, value=None) -> None:
        if value is None:
            try:
                value = int(expr)
            except Exception as e:
                value = random.randint(1, 100)
        super().__init__(context, expr, value)
    def __bool__(self):
        if self != 0:
            return True
        return False
    def __int__(self):
        return self.value
    
    def negate(self):
        value = -self.value
        expr = 0 - self.expr
        return SymbolicInt(self.context, expr, value)

    def to_str(self):
        value = str(self.value)
        expr = z3.IntToStr(self.expr)
        return create_symbol('string', self.context, expr, value)

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
# def make_method(method, op):
#     code = "def %s(self, other):\n" % method
#     code += "   (expr, value) = self._zv(other)\n"
#     code += "   v_ = self.value %s value\n" % op
#     code += "   expr_ = self.expr %s expr\n" % op
#     code += "   return create_symbol(type(v_).__name__, self.context, expr_, v_)\n"
#     locals_dict = {}
#     exec(code, globals(), locals_dict)
#     setattr(SymbolicInt, method, locals_dict[method])
for (name, op) in ops:
    def make_method(op_name: str) -> t.Callable:
        def method(self: SymbolicType, other: t.Any) -> SymbolicType:
            return self._make_comparison(other, op_name)
        return method
    setattr(SymbolicInt, f"__{name}__", make_method(name))
    setattr(SymbolicInt, f"__r{name}__", make_method(name))

# for (name, op) in ops:
#     method = "__%s__" % name
#     make_method(method, op)
#     rmethod = "__r%s__" % name
#     make_method(rmethod, op)
