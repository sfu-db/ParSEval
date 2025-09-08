from __future__ import annotations
import typing as t
import z3, importlib, random

from .term import Term, NULL
from .zbool import ZBool
from .zreal import ZReal
from .zint import ZInt
from parseval import datatypes as dt_typ
PRECISION = 4
INT_NULL = 6789

MODULE_PATH = "parseval.symbol"

class ZRelation(ZInt):
    @classmethod
    def stype(cls, ctx):
        ctx = z3.main_ctx() if ctx is None else ctx
        if cls._stype is None or cls._stype.ctx is not ctx:
            cls._ctx = ctx
            cls._stype = z3.IntSort(ctx = ctx)
        return cls._stype
    
    @classmethod
    def mk_sval(cls, value, ctx=None):
        value = cls.null_val(ctx) if isinstance(value, NULL) else int(value)
        return z3.IntVal(value, ctx= ctx)

    def __init__(self, symbol, concrete, ctx: z3.Context | None = None) -> None:
        concrete = random.randint(-10, 10) if concrete is None else concrete
        concrete = int(concrete) if not isinstance(concrete, NULL) else concrete
        assert isinstance(concrete, (int, NULL, float)), f'expect int/float/NULL as concrete value, get {concrete}/{type(concrete)}'
        super().__init__(symbol, concrete, ctx)
    
    def _zv(self, other):
        if isinstance(other, list):
            other = len(other)
        if isinstance(other, Term):
            o = other.cast('Int')
            return o.symbol, o.concrete
        if isinstance(other, z3.ArithRef):
            return other, 1
        if NULL.is_null(other):
            return self.null_symbol(self.ctx), NULL(self.typeName())
        elif isinstance(other, float):
            return z3.RealVal(float(other), ctx = self.ctx), round(float(other), PRECISION)
        try:
            t = int(other)
            return z3.IntVal(t, ctx =  self.ctx), t
        except Exception as e:
            raise RuntimeError(f'cannot convert {str(other)} to {self.key}. {e}')

    def __int__(self):
        return 0 if isinstance(self.concrete , NULL) else self.concrete
    
    def unary_op(self, fname: str, **kwargs) -> Term:
        if isinstance(self.concrete , NULL):
            return self
        fun = getattr(int, fname)
        zfun = getattr(z3.ArithRef, fname)
        z_ = zfun(self.symbol)
        v_ = fun(self.concrete)
        r_ = ZInt(z_, v_, self.ctx)
        return r_
    
    def bool_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | t.Any, **kwargs) -> Term:
        z, v = self._zv(other)
        if isinstance(self.concrete , NULL) or isinstance(v , NULL) :
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) > 1 else self.symbol == self.null_symbol(self.ctx)
            fun = getattr(NULL, fname)
            v_ = fun(self.concrete, v)
            return ZBool( z_, v_, self.ctx)
        zfun = getattr(z3.ArithRef, fname)
        fun = getattr(int, fname)
        c_ = int(self.concrete)
        z_ = zfun(self.symbol, z)
        v_ = fun(c_, v)
        return ZBool( z_, v_, self.ctx)
    
    def binary_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | t.Any, **kwargs) -> Term:        
        z, v = self._zv(other)
        if isinstance(self.concrete , NULL) or isinstance(v , NULL) :
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) else self.symbol == self.null_symbol(self.ctx)
            fun = getattr(NULL, fname)
            v_ = fun(self.concrete, v)
            return ZInt( z_, v_, self.ctx)
        if fname in ['__div__', '__truediv__']:
            v = 1 if v == 0 else v

        zfun = getattr(z3.ArithRef, fname)
        fun = getattr(int, fname)
        c_ = self.concrete
        if isinstance(v, float):
            fun = getattr(float, fname)
            c_ = float(self.concrete)

        z_ = zfun(self.symbol, z)
        v_ = fun(c_, v)
        
        if isinstance(v_, float):            
            return ZReal( z_, round( v_, PRECISION), self.ctx)
        else:
            return ZInt( z_, v_, self.ctx)
    def to_db(self) -> str | float | int | bool | t.Any:
        return 'NULL' if isinstance(self.concrete , NULL) else self.concrete
    
    def get_abs(self):
        if isinstance(self.concrete, NULL):
            return self
        return ZInt( z3.Abs(self.symbol), abs(self.concrete), self.ctx)
    def cast(self, dataType: str) -> Term:        
        z_ = None
        v_ = None
        dataType = dt_typ.normalize(dataType)
        if dataType == 'Int':
            return self
        elif dataType == 'String':
            z_ = z3.IntToStr(self.symbol)
            v_ = str(self.concrete)
            module_name = 'ZString'
        elif dataType == 'Real':
            z_ = z3.ToReal(self.symbol) if str(self.symbol.sort()) == 'Int' else self.symbol            
            v_ =  float(self.concrete)
            module_name = 'ZReal'
        else:
            raise NotImplementedError(f'cannot cast {self} to {dataType}')
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
        return getattr(module, module_name)( symbol= z_, concrete= v_, ctx = self.ctx)