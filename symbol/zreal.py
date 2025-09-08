from __future__ import annotations
import typing as t
import z3, logging, random, importlib
from .term import Term, NULL
from .zbool import ZBool

from parseval import datatypes as dt_typ

logger = logging.getLogger('app')
PRECISION = 4

MODULE_PATH = "parseval.symbol"

class ZReal(Term):
    @classmethod
    def stype(cls, ctx):
        ctx = z3.main_ctx() if ctx is None else ctx
        if cls._stype is None or cls._stype.ctx is not ctx:
            cls._ctx = ctx
            cls._stype = z3.RealSort(ctx = ctx)
        return cls._stype

    @classmethod
    def mk_sval(cls, value, ctx):
        value = cls.null_val(ctx) if isinstance(value, NULL) else float(value)
        return z3.RealVal(value, ctx = ctx)
    
    def __init__(self, symbol, concrete, ctx: z3.Context | None = None) -> None:
        concrete = random.random() if concrete is None else concrete
        assert isinstance(concrete, (int, float, NULL)), f'{symbol} expect int/float/NULL as concrete value, get {concrete}, { type(concrete)}'
        concrete = float(concrete) if not isinstance(concrete, NULL) else concrete
        super().__init__(symbol, concrete, ctx)
    def _zv(self, other):
        if isinstance(other, list):
            other = int(len(other))
        
        if isinstance(other, Term):
            other = other.cast('Real')
            return other.symbol, other.concrete
        elif NULL.is_null(other):
            return self.null_symbol(self.ctx), NULL(self.typeName())
        t = round(float(other), PRECISION)
        return z3.RealVal(t, ctx =  self.ctx), t
    def unary_op(self, fname: str, **kwargs) -> Term:
        if isinstance(self.concrete , NULL):
            return self
        fun = getattr(float, fname)
        zfun = getattr(z3.ArithRef, fname)
        z_ = zfun(self.symbol)
        v_ = fun(self.concrete)
        return ZReal(z_, v_, self.ctx)
    
    def bool_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | t.Any, **kwargs) -> Term:
        z, v = self._zv(other)
        if isinstance(self.concrete , NULL) or isinstance(v, NULL) :
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) > 1 else self.symbol == self.null_symbol(self.ctx)
            fun = getattr(NULL, fname)
            v_ = fun(self.concrete, v)            
            return ZBool(self.context, z_, v_, self.ctx)
        tolerance = z3.RealVal(1e-3, ctx= self.ctx)
        if fname == '__eq__':
            z_ = self.symbol == z
            v_ = round(self.concrete, PRECISION) == round(v, PRECISION)
        elif fname == '__ne__':
            z_ = self.symbol != z
            v_ = round(self.concrete, PRECISION) != round(v, PRECISION)
        else:
            zfun = getattr(z3.ArithRef, fname)
            z_ = zfun(self.symbol, z)
            fun = getattr(float, fname)
            c_ = float(self.concrete)
            v_ = fun(c_, v)
        return ZBool(z_, v_, self.ctx)
    
    def binary_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | t.Any, **kwargs) -> Term:
        z, v = self._zv(other)
        zfun = getattr(z3.ArithRef, fname)
        if isinstance(self.concrete , NULL) or isinstance(v, NULL):
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) > 1 else self.symbol == self.null_symbol(self.ctx)
            fun = getattr(NULL, fname)
            v_ = fun(self.concrete, v)
            z_ = z3.If(z_, self.null_symbol(self.ctx), zfun(self.symbol, z))
            return ZReal(self.context, z_, v_, self.ctx)
        
        if fname in ['__div__', '__truediv__']:
            v = 1.0 if v == 0 else v
        z_ = zfun(self.symbol, z)

        fun = getattr(float, fname)
        v_ = round(fun(float(self.concrete), float(v)), PRECISION)

        return ZReal(z_, v_, self.ctx)
    
    def to_db(self) -> str | float | int | bool | t.Any:
        if isinstance(self.concrete , NULL):
            return 'NULL'
        return self.concrete
    
    def get_abs(self):
        if isinstance(self.concrete, NULL):
            return self
        return ZReal(z3.Abs(self.symbol), abs(self.concrete), self.ctx)


    def cast(self, dataType: str) -> Term:
        dataType = dt_typ.normalize(dataType)
        z_ = None
        v_ = None

        if dataType == 'Real':
            return self
        elif dataType == 'Int':
            module_name = 'ZInt'
            z_ = z3.ToInt(self.symbol)
            v_ = int(self.concrete)
        elif dataType == 'String':            
            z_ = z3.IntToStr(z3.ToInt(self.symbol))
            v_ = str(self.concrete)
            module_name = 'ZString'
        else:
            raise NotImplementedError(f'cannot cast {self} to {dataType}')
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
        return getattr(module, module_name)(symbol= z_, concrete= v_, ctx = self.ctx)
    