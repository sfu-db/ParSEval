from __future__ import annotations
from abc import ABC, abstractmethod
import z3, importlib, datetime, logging, re
import typing as t

if t.TYPE_CHECKING:
    from ._typing import TermOrLiteral, TermLiterals, SymbolLiterals
from .na import NULL

from parseval import datatypes as dt_typ

logger = logging.getLogger('app')
MODULE_PATH = "parseval.symbol"
MODULE_NAME = {
    'Boolean' : 'ZBool',
    'Bool' : 'ZBool',
    'Date' : 'ZDate',
    'Datetime': 'ZDatetime',
    'String': 'ZString',
    'Int': 'ZInt',
    'Real': 'ZReal'
}

def clean_name(name) -> str:
    pattern = r'[^a-zA-Z0-9_]'
    cleaned_str = re.sub(pattern, '', name)
    return cleaned_str


class Term(ABC):
    '''
        Could be symbol, concrete value or labeled null
    '''
    _stype : SymbolLiterals = None

    @property
    def key(self) -> str:
        return self.typeName()
    
    @classmethod
    def typeName(cls) -> str:                
        return cls.__name__[1:].lower()
    
    @classmethod
    @abstractmethod
    def stype(cls, ctx):
        raise NotImplementedError
    
    @classmethod
    @abstractmethod
    def mk_sval(cls, value, ctx):
        '''
            convert value to symbolic representation 
        '''
        raise NotImplementedError
    
    @classmethod
    def null_val(cls, ctx  = None):
        return NULL(cls.typeName()).value

    @classmethod
    def null_symbol(cls, ctx):
        return cls.mk_sval(NULL(cls.typeName()), ctx= ctx)
    
    
    
    @staticmethod
    def create(dtype, z_name, v = None,  ctx = None) -> Term:
        dtype = dt_typ.normalize(dtype)
        module_name = MODULE_NAME[dtype]
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
        m = getattr(module, module_name)        
        v = NULL(m.typeName()) if NULL.is_null(v) else v
        symbol = z3.Const(clean_name(z_name), m.stype(ctx))
        obj = m(symbol, v, ctx)
        return obj

    @staticmethod
    def ensure_term(val, dtype, ctx) -> Term:
        if isinstance(val, Term):
            return val
        dtyp =  dt_typ.normalize(dtype)
        module_name = MODULE_NAME[dtyp]
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')        
        m = getattr(module, module_name)
        if isinstance(val, z3.ExprRef):
            return m(symbol = val, concrete = None, ctx = ctx)
        val = NULL(m.typeName()) if NULL.is_null(val) else val
        s = m.mk_sval(val, ctx)
        return m(symbol= s, concrete=val, ctx = ctx)
    
    def to_z3ref(self):
        ''' Convert current concrete value to symbol value '''
        return self.mk_sval(self.concrete, ctx = self.ctx)
        
    def __init__(self, symbol, concrete, ctx :  t.Optional[z3.Context] = None) -> None:
        assert isinstance(symbol, z3.ExprRef), f'Should onle assign z3 symbol (i.e. z3.ExprRef) rather {type(symbol)} to {symbol}'
        self._symbol = symbol
        self._concrete = concrete
        self._ctx: z3.Context = ctx
    
    def get_null_symbol(self, vari: z3.ExprRef):
        sort_typ = str(vari.sort())
        module_name = MODULE_NAME[sort_typ]
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
        return getattr(module, module_name).null_symbol(ctx = self.ctx)

    @property
    def ctx(self):
        return self._ctx

    @ctx.setter
    def ctx(self, val):
        self._ctx = val

    @property
    def concrete(self):
        return self._concrete

    @concrete.setter
    def concrete(self, val):
        self._concrete = val

    @property
    def symbol(self):
        return self._symbol   

    @symbol.setter
    def symbol(self, val):
        self._symbol = val

    def __repr__(self) -> str:
        return str(self.symbol)
    def __str__(self) -> str:
        return f"({str(self.symbol)}, {self.concrete})"
    def __hash__(self) -> int:
        return hash(str(self._concrete))    
    def __eq__(self, other: TermOrLiteral) -> Term:
        return self.bool_op('__eq__', other)
    def __ne__(self, other: TermOrLiteral) -> Term:
        return self.bool_op('__ne__', other)

    def __gt__(self, other: TermOrLiteral) -> Term:
        return self.bool_op('__gt__', other)

    def __ge__(self, other: TermOrLiteral) -> Term:
        return self.bool_op('__ge__', other)

    def __lt__(self, other: TermOrLiteral) -> Term:
        return self.bool_op('__lt__', other)

    def __le__(self, other: TermOrLiteral) -> Term:
        return self.bool_op('__le__', other)

    def __mod__(self, other: TermOrLiteral) -> Term:
        return self.binary_op('__mod__', other)

    def __add__(self, other: TermOrLiteral) -> Term:
        return self.binary_op('__add__', other)

    def __sub__(self, other: TermOrLiteral) -> Term:
        return self.binary_op('__sub__', other)

    def __mul__(self, other: TermOrLiteral) -> Term:
        return self.binary_op('__mul__', other)

    def __truediv__(self, other: TermOrLiteral) -> Term:
        return self.binary_op('__truediv__', other)

    def __div__(self, other: TermOrLiteral) -> Term:
        return self.binary_op('__div__', other)

    def __neg__(self) -> Term:
        return self.unary_op('__neg__')

    # def __radd__(self, other: TermOrLiteral) -> Term:
    #     return self.inverse_binary_op(exp.Add, other)

    # def __rsub__(self, other: TermOrLiteral) -> Term:
    #     return self.inverse_binary_op(exp.Sub, other)

    # def __rmul__(self, other: TermOrLiteral) -> Term:
    #     return self.inverse_binary_op(exp.Mul, other)

    # def __rdiv__(self, other: TermOrLiteral) -> Term:
    #     return self.inverse_binary_op(exp.Div, other)

    # def __rtruediv__(self, other: TermOrLiteral) -> Term:
    #     return self.inverse_binary_op(exp.Div, other)

    # def __rmod__(self, other: TermOrLiteral) -> Term:
    #     return self.inverse_binary_op(exp.Mod, other)

    # def __pow__(self, power: TermOrLiteral, modulo=None):
    #     return Term(exp.Pow(this=self.expression, expression=Term(power).expression))

    # def __rpow__(self, power: TermOrLiteral):
    #     return Term(exp.Pow(this=Term(power).expression, expression=self.expression))

    def __invert__(self):
        return self.unary_op('__not__')

    # def __rand__(self, other: TermOrLiteral) -> Term:
    #     return self.inverse_binary_op(exp.And, other)

    # def __ror__(self, other: TermOrLiteral) -> Term:
    #     return self.inverse_binary_op(exp.Or, other)

    # @abstractmethod
    def logical_and(self, other) -> Term:
        ...
    
    # @abstractmethod
    def logical_or(self, other) -> Term:
        ...

    @abstractmethod
    def to_db(self)-> TermLiterals:
        raise NotImplementedError

    def is_null(self):
        varis = z3.z3util.get_vars(self.symbol)
        if self.symbol.decl().kind() == z3.Z3_OP_ITE and len(varis) > 1:
            varis.pop()
        z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) > 1 else self.symbol == self.null_symbol(self.ctx)
        v_ = isinstance(self.concrete , NULL)
        module_name = 'ZBool'
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
        m = getattr(module, module_name)
        return m(z_, v_, self.ctx)

    @abstractmethod
    def _zv(self, other) -> t.Tuple[z3.ExprRef, TermLiterals]:
        raise NotImplementedError

    # @abstractmethod
    def bool_op(self, fname: str, other: TermOrLiteral, **kwargs) -> Term:
        raise NotImplementedError(f'Function {fname} is not implemented in {self.key}')

    # @abstractmethod
    def binary_op(self, fname: str, other: TermOrLiteral, **kwargs) -> Term:
        raise NotImplementedError(f'Function {fname} is not implemented')
    
    # @abstractmethod
    def unary_op(self, fname: str, **kwargs) -> Term:
        raise NotImplementedError(f'Function {fname} is not implemented')
    
    def cast(self, dataType: str) -> Term:
        """"""
        ...

    def like(self, other) -> Term:
        ...

    def startswith(self, value: t.Union[str, Term]) -> Term:
        ...

    def endswith(self, value: t.Union[str, Term]) -> Term:
        ...

    def substr(self, start: t.Union[int, Term], length: t.Union[int, Term]) -> Term:
        ...