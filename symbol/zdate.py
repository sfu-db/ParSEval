from __future__ import annotations
import z3, re, logging
from datetime import datetime, date
import typing as t

from .zbool import ZBool
from .term import Term, NULL

from dateutil import parser as date_parser

import importlib
logger = logging.getLogger('app')
class ZDate(Term):

    @classmethod
    def stype(cls, ctx):
        ctx = z3.main_ctx() if ctx is None else ctx
        if cls._stype is None or cls._stype.ctx is not ctx:
            stype =z3.Datatype('Date', ctx= ctx)
            stype.declare('Date', ('year', z3.IntSort(ctx=ctx)), ('month', z3.IntSort(ctx=ctx)), ('day', z3.IntSort(ctx=ctx)))
            stype = stype.create()
            cls._ctx = ctx
            cls._stype = stype
        return cls._stype

    @classmethod
    def mk_sval(cls, value, ctx = None):
        value = cls.null_val(ctx) if isinstance(value, NULL) else value
        assert isinstance(value, (datetime, date)), f'cannot convert {value} to symbolic {cls.typeName()}'
        return cls.stype(ctx= ctx).Date(value.year, value.month, value.day)

    def __init__(self, symbol, concrete, ctx: z3.Context | None = None) -> None:
        concrete = date.today() if concrete is None else concrete
        concrete = date_parser.parse(concrete) if not isinstance(concrete, (date, datetime, NULL)) else concrete
        assert isinstance(concrete, (datetime, date , NULL)), f'expect datetime/NULL as concrete value, get {concrete}/{type(concrete)}'
        super().__init__(symbol, concrete, ctx)
        self.format = '%Y-%m-%d'

    def _zv(self, other) -> t.Tuple[z3.ExprRef | date | NULL]:
        if isinstance(other, Term):
            o = other.cast('Date')
            return o.symbol, o.concrete
        elif NULL.is_null(other):
            return self.null_symbol(self.ctx), NULL(self.typeName())        
        elif isinstance(other, float):
            dt_obj = date.fromtimestamp(float(other))
            return self.stype(self.ctx).Date(dt_obj.year, dt_obj.month, dt_obj.day), dt_obj
        elif isinstance(other, datetime):
            return self.stype(self.ctx).Date(other.year, other.month, other.day), other.date()        
        dt_obj = date_parser.isoparse(str(other)).date()
        return self.stype(self.ctx).Date(dt_obj.year, dt_obj.month, dt_obj.day), dt_obj
    
    def bool_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | t.Any, **kwargs) -> Term:
        z, v = self._zv(other)
        zfun = getattr(z3.ArithRef, fname)
        fun = getattr(date, fname)

        base = [zfun(self.stype(self.ctx).year(self.symbol), self.stype(self.ctx).year(z)),\
                z3.Or(self.stype(self.ctx).year(self.symbol) != self.stype(self.ctx).year(z), zfun(self.stype(self.ctx).month(self.symbol), self.stype(self.ctx).month(z))),\
                z3.Or(self.stype(self.ctx).year(self.symbol) != self.stype(self.ctx).year(z), self.stype(self.ctx).month(self.symbol) != self.stype(self.ctx).month(z), zfun(self.stype(self.ctx).day(self.symbol), self.stype(self.ctx).day(z)))]
        v_ = fun(self.concrete, v)
        return ZBool(self.context, z3.And(base), v_, self.ctx)
    
    def __eq__(self, other):
        z, v = self._zv(other)
        if NULL.is_null(self.concrete) or NULL.is_null(v):
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) > 1 else varis[0] == self.null_symbol(self.ctx)
            fun = getattr(NULL, '__eq__')
            v_ = fun(self.concrete, v)
            return ZBool(z_, v_, self.ctx)
        base = [
            self.stype(self.ctx).year(self.symbol) == self.stype(self.ctx).year(z),\
            self.stype(self.ctx).month(self.symbol) == self.stype(self.ctx).month(z), \
            self.stype(self.ctx).day(self.symbol) == self.stype(self.ctx).day(z)
        ]
        return ZBool(z3.And(base), self.concrete == v, self.ctx)
    def binary_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | date | datetime, **kwargs) -> Term:
        return super().binary_op(fname, other, **kwargs)
    
    def to_db(self) -> str | float | int | bool | date | datetime | NULL:
        if isinstance(self.concrete, NULL):
            return 'NULL'
        return f'"{self.concrete}"'