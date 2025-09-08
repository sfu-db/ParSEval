from __future__ import annotations
import typing as t
import z3, logging

from .term import Term, NULL

logger = logging.getLogger('app')


class ZBool(Term):
    @classmethod
    def stype(cls, ctx):
        ctx = z3.main_ctx() if ctx is None else ctx
        if cls._stype is None or cls._stype.ctx is not ctx:
            stype = z3.Datatype('Boolean', ctx= ctx)
            stype.declare('true')
            stype.declare('false')
            stype.declare('NULL')
            stype.declare('unknown')
            stype = stype.create()
            cls._ctx = ctx
            cls._stype = stype
        return cls._stype
    
    @classmethod
    def mk_sval(cls, value, ctx=None):
        if value == 'NULL':
            return cls.stype(ctx).NULL
        if value in [True, 'true']:
            return cls.stype(ctx= ctx).true
        return cls.stype(ctx= ctx).false
    

    def __init__(self, symbol, concrete, ctx: z3.Context | None = None) -> None:
        super().__init__(symbol, concrete, ctx)    
    def __bool__(self):
        return self.concrete == True    
    def _zv(self, other):        
        if isinstance(other, Term):
            return other.symbol, other.concrete
        elif NULL.is_null(other):
            return self.null_symbol(self.ctx), NULL(self.typeName())
        elif other:
            return self.stype(ctx= self.ctx).true, True
        else:
            return self.stype(ctx= self.ctx).false, False
    
    def __eq__(self, other):
        z, v = self._zv(other)
        v_ = self.concrete == v
        z_ = self.symbol == z
        return ZBool(z_, v_, self.ctx)
    
    def __not__(self):
        symbol = z3.Not(self.symbol, ctx = self.ctx)
        concrete = not self.concrete
        return ZBool(symbol= symbol, concrete = concrete, ctx= self.ctx)
    def logical_and(self, other) -> Term:
        z, v = self._zv(other)
        return ZBool(z3.And(self.symbol, z), self.concrete and v, ctx = self.ctx)
    def logical_or(self, other) -> Term:
        z, v = self._zv(other)
        return ZBool(z3.Or(self.symbol, z), self.concrete or v, ctx = self.ctx)
    
    def to_db(self) -> bool:
        return 'NULL' if isinstance(self.concrete, NULL) else self.concrete
    
