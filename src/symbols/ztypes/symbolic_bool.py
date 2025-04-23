from __future__ import annotations
from ..base import SymbolicType
from ..ssa_factory import create_symbol
import logging, z3
logger = logging.getLogger(__name__)

class SymbolicBool(SymbolicType):
    def __init__(self, context, expr, value=None) -> None:
        value = False if value is None else value
        super().__init__(context, expr, value)

    def oneif(self):
        e_ =  z3.If(self.expr, 1, 0)
        v_ = 1 if self.value else 0
        return create_symbol('int', self.context, e_, v_)
    
    def _zv(self, other):
        if isinstance(other, SymbolicBool):
            return other.expr, other.value
        return z3.BoolVal(other), other

    def __add__(self, other):
        if isinstance(other, int) and other == 0:
            return self
        z, v = self._zv(other)
        e_ = self.expr + z
        v_ = self.value + v
        return create_symbol('int', self.context, e_, v_)
    

    def __radd__(self, other):
        # if isinstance(other, int) and other == 0:
        #     return self
        return self.__add__(other)

    def negate(self):
        self.value = -self.value
        self.expr = z3.Not(self.expr)
        
    def __not__(self):
        value = not self.value
        expr =z3.simplify( z3.Not(self.expr))
        return SymbolicBool(self.context, expr, value)
    
    def __bool__(self):
        r, pred = (True, self.expr) if self.value else (False, z3.Not(self.expr))
        self.context.set('paths', self)
        return r


# class SymbolicAnd(SymbolicBool):
#     def __init__(self, context, left, right, value = None) -> None:
#         super().__init__(context, expr, value)

# class SymbolicOr(SymbolicBool):
#     def __init__(self, context, expr, value=None) -> None:
#         super().__init__(context, expr, value)
