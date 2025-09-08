from __future__ import annotations
import z3, re, string, random
from datetime import datetime, date
import typing as t
import importlib, logging
from .term import Term, NULL
from .zbool import ZBool
from .zstring import ZString
from dateutil import parser as date_parser
from parseval import datatypes as dt_typ
logger = logging.getLogger('app')

MODULE_PATH = "parseval.symbol"

class ZDatetime(Term):
    @classmethod
    def stype(cls, ctx):
        ctx = z3.main_ctx() if ctx is None else ctx
        if cls._stype is None or cls._stype.ctx is not ctx:
            stype =z3.Datatype('Datetime', ctx= ctx)
            stype.declare('Datetime', ('year', z3.IntSort(ctx=ctx)), ('month', z3.IntSort(ctx=ctx)), ('day', z3.IntSort(ctx=ctx)), 
                          ('hour', z3.IntSort(ctx= ctx)), ('minute', z3.IntSort(ctx=ctx)), ('second', z3.IntSort(ctx=ctx)))
            stype = stype.create()
            cls._ctx = ctx
            cls._stype = stype
        return cls._stype

    @classmethod
    def mk_sval(cls, value, ctx=None):
        value = cls.null_val(ctx) if isinstance(value, NULL) else value
        assert isinstance(value, (datetime, date)), f'cannot convert {value} to symbolic {cls.typeName()}'
        return cls.stype(ctx= ctx).Datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)

    def __init__(self, symbol, concrete, ctx: z3.Context | None = None) -> None:
        concrete = datetime.now() if concrete is None else concrete
        concrete = date_parser.parse(concrete) if not isinstance(concrete, (date, datetime, NULL)) else concrete
        assert isinstance(concrete, (datetime, NULL)), f'expect datetime/NULL as concrete value, get {concrete}'
        super().__init__(symbol, concrete, ctx)
        self.format = '%Y-%m-%d %H:%M:%S'
    
    def _zv(self, other) -> t.Tuple[z3.ExprRef | str | float | int | bool | t.Any | NULL]:
        if isinstance(other, Term):
            o = other.cast('datetime')
            return o.symbol, o.concrete
        elif NULL.is_null(other):        
            return self.null_symbol(self.ctx), NULL(self.typeName())
        elif isinstance(other, datetime):
            return self.stype(self.ctx).Datetime(other.year, other.month, other.day, other.hour, other.minute, other.second), other
        elif isinstance(other, float):
            dt_obj = datetime.fromtimestamp(float(other))
            return self.stype(self.ctx).Datetime(dt_obj.year, dt_obj.month, dt_obj.day, dt_obj.hour, dt_obj.minute, dt_obj.second), dt_obj
        dt_obj = date_parser.parse(str(other), fuzzy=True)
        return self.stype(self.ctx).Datetime(dt_obj.year, dt_obj.month, dt_obj.day,  dt_obj.hour, dt_obj.minute, dt_obj.second), dt_obj
    
    def __eq__(self, other):
        z, v = self._zv(other)
        if isinstance(self.concrete , NULL) or isinstance(v , NULL) :
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) > 1 else self.symbol == self.null_symbol(self.ctx)
            fun = getattr(NULL, '__eq__')
            v_ = fun(self.concrete, v)
            return ZBool(z_, v_, self.ctx)
        base = [
            self.stype(self.ctx).year(self.symbol) == self.stype(self.ctx).year(z),\
            self.stype(self.ctx).month(self.symbol) == self.stype(self.ctx).month(z), \
            self.stype(self.ctx).day(self.symbol) == self.stype(self.ctx).day(z), \
            self.stype(self.ctx).hour(self.symbol) == self.stype(self.ctx).hour(z), \
            self.stype(self.ctx).minute(self.symbol) == self.stype(self.ctx).minute(z), \
            self.stype(self.ctx).second(self.symbol) == self.stype(self.ctx).second(z), \
        ]
        fun = getattr(datetime, '__eq__')
        v_ = fun(self.concrete, v)
        return ZBool(z3.And(base), v_, self.ctx)
    
    def __ne__(self, other):
        z, v = self._zv(other)
        if isinstance(self.concrete , NULL) or isinstance(v , NULL) :
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) > 1 else self.symbol == self.null_symbol(self.ctx)
            fun = getattr(NULL, '__ne__')
            v_ = fun(self.concrete, v)
            return ZBool( z_, v_, self.ctx)
        fun = getattr(datetime, '__ne__')

        base = [
            self.stype(self.ctx).year(self.symbol) != self.stype(self.ctx).year(z),\
            self.stype(self.ctx).month(self.symbol) != self.stype(self.ctx).month(z), \
            self.stype(self.ctx).day(self.symbol) != self.stype(self.ctx).day(z), \
            self.stype(self.ctx).hour(self.symbol) != self.stype(self.ctx).hour(z), \
            self.stype(self.ctx).minute(self.symbol) != self.stype(self.ctx).minute(z), \
            self.stype(self.ctx).second(self.symbol) != self.stype(self.ctx).second(z)
        ]
        return ZBool( z3.Or(base), fun(self.concrete, v), self.ctx)
    
    def bool_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | t.Any, **kwargs) -> Term:

        z, v = self._zv(other)
        zfun = getattr(z3.ArithRef, fname)
        fun = getattr(datetime, fname)
        base = [zfun(self.stype(self.ctx).year(self.symbol), self.stype(self.ctx).year(z)),\
                z3.And(self.stype(self.ctx).year(self.symbol) != self.stype(self.ctx).year(z), zfun(self.stype(self.ctx).month(self.symbol), self.stype(self.ctx).month(z))),\
                z3.And(self.stype(self.ctx).year(self.symbol) != self.stype(self.ctx).year(z), self.stype(self.ctx).month(self.symbol) != self.stype(self.ctx).month(z), zfun(self.stype(self.ctx).day(self.symbol), self.stype(self.ctx).day(z))), \
                z3.And(self.stype(self.ctx).year(self.symbol) != self.stype(self.ctx).year(z),  self.stype(self.ctx).month(self.symbol) != self.stype(self.ctx).month(z), self.stype(self.ctx).day(self.symbol) != self.stype(self.ctx).day(z), zfun(self.stype(self.ctx).hour(self.symbol), self.stype(self.ctx).hour(z))),\
                z3.And(self.stype(self.ctx).year(self.symbol) != self.stype(self.ctx).year(z),  self.stype(self.ctx).month(self.symbol) != self.stype(self.ctx).month(z), self.stype(self.ctx).day(self.symbol) != self.stype(self.ctx).day(z), \
                    self.stype(self.ctx).hour(self.symbol) != self.stype(self.ctx).hour(z), zfun(self.stype(self.ctx).minute(self.symbol) , self.stype(self.ctx).minute(z))),\
                z3.And(self.stype(self.ctx).year(self.symbol) != self.stype(self.ctx).year(z),  self.stype(self.ctx).month(self.symbol) != self.stype(self.ctx).month(z), self.stype(self.ctx).day(self.symbol) != self.stype(self.ctx).day(z), \
                    self.stype(self.ctx).hour(self.symbol) != self.stype(self.ctx).hour(z), self.stype(self.ctx).minute(self.symbol) != self.stype(self.ctx).minute(z), zfun(self.stype(self.ctx).second(self.symbol) ,self.stype(self.ctx).second(z)))]
        v_ = fun(self.concrete, v)
        return ZBool( z3.Or(base), v_, self.ctx)
    
    def binary_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | t.Any, **kwargs) -> Term:
        _z, _v = self._zv(other)
        l = self.julianday()        
        r = self.stype(self.ctx).year(_z)* 31536000  + self.stype(self.ctx).month(_z)* 2592000  + self.stype(self.ctx).day(_z)* 86400
        z_ = l.symbol - r
        v_ = self.concrete - _v
        v_ = v_.total_seconds()
        module_name = 'ZReal'
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
        return getattr(module, module_name)(  symbol= z_, concrete= v_, ctx = self.ctx)

    
    def to_db(self) -> str | float | int | bool | t.Any | NULL:
        if isinstance(self.concrete, NULL):
            return 'NULL'
        return f'"{self.concrete}"'
    def zstrftime(self, fmt):
        year = self.stype(self.ctx).year(self.symbol)
        month = self.stype(self.ctx).month(self.symbol)
        day = self.stype(self.ctx).day(self.symbol)
        hour = self.stype(self.ctx).hour(self.symbol)
        minute = self.stype(self.ctx).minute(self.symbol)
        second = self.stype(self.ctx).second(self.symbol)

        date_parts = []
        if '%Y' in fmt:
            date_parts.append(z3.IntToStr(year))
        if '%m' in fmt:
            date_parts.append(z3.IntToStr(month))
        if '%d' in fmt:
            date_parts.append(z3.IntToStr(day))

        if len(date_parts) == 3:
            date_parts.insert(1, '-')
            date_parts.insert(3, '-')
        elif len(date_parts) == 2:
            date_parts.insert(1, '-')
        # print(f'fmt: {fmt}')
        time_parts = []
        if '%H' in fmt:
            time_parts.append(z3.IntToStr(hour))
        if '%M' in fmt:
            time_parts.append(z3.IntToStr(minute))
        if '%S' in fmt:
            time_parts.append(z3.IntToStr(second))
        
        if len(time_parts) == 3:
            time_parts.insert(1, ':')
            time_parts.insert(3, ':')
        elif len(time_parts) == 2:
            time_parts.insert(1, ':')
        
        if time_parts:
            date_parts += [' ']
            date_parts += time_parts            
        
        v_ = self.concrete.strftime(fmt)
        z_ = z3.Concat(*date_parts) if len(date_parts) > 1 else date_parts.pop()
        r_ = ZString(  symbol = z_, concrete = v_, ctx = self.ctx)
        return r_
    
    def cast(self, dataType: str) -> Term:
        year = self.stype(self.ctx).year(self.symbol) 
        month = self.stype(self.ctx).month(self.symbol)
        day = self.stype(self.ctx).day(self.symbol)
        hour = self.stype(self.ctx).hour(self.symbol)
        minute = self.stype(self.ctx).minute(self.symbol)
        second = self.stype(self.ctx).second(self.symbol)
        z_ = None
        v_ = None

        dataType = dt_typ.normalize(dataType)
        if dataType == 'Datetime':
            return self
        elif dataType == 'String':
            return self.zstrftime(self.format)
        elif dataType == 'Date':
            module_name = 'ZDate'
            module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
            m = getattr(module, module_name)
            z_ = m.stype(self.ctx).Date(year, month, day)            
            v_ = date(self.concrete.year, self.concrete.month, self.concrete.day)
            return m(  symbol= z_, concrete= v_, ctx = self.ctx)
        else:
            raise NotImplementedError(f'cannot cast {self} to {dataType}')
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
        return getattr(module, module_name)(  symbol= z_, concrete= v_, ctx = self.ctx)
    
    def julianday(self):
    
        year = self.stype(self.ctx).year(self.symbol) 
        month = self.stype(self.ctx).month(self.symbol)
        day = self.stype(self.ctx).day(self.symbol)
        hour = self.stype(self.ctx).hour(self.symbol)
        minute = self.stype(self.ctx).minute(self.symbol)
        second = self.stype(self.ctx).second(self.symbol)
        v_ = self.concrete.year * 31536000 + self.concrete.month * 2592000 + self.concrete.day * 86400
        module_name = 'ZReal'
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')

        z_ = year * 31536000 + month * 2592000 + day * 86400
        return getattr(module, module_name)( symbol= z_, concrete= v_, ctx = self.ctx)

    def udate(self):
        return self
    
    