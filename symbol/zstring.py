from __future__ import annotations
import z3, re, string, random, logging
import typing as t
import importlib


from .term import Term, NULL
from .zint import ZInt
from .zbool import ZBool
from parseval import datatypes as dt_typ

from datetime import datetime, date
from dateutil import parser as date_parser
if t.TYPE_CHECKING:
    from ._typing import TermOrLiteral

logger = logging.getLogger('app')

MODULE_PATH = "parseval.symbol"

class zstr_iterator():
    def __init__(self, zstr):
        self._zstr = zstr
        self._str_idx = 0
        self._str_max = zstr._len

    def __next__(self):
        if self._str_idx == self._str_max: 
            raise StopIteration
        c = self._zstr[self._str_idx]
        self._str_idx += 1
        return c
    def __len__(self):
        return self._len
    
class ZString(Term):
    @classmethod
    def stype(cls, ctx):
        ctx = z3.main_ctx() if ctx is None else ctx
        if cls._stype is None or cls._stype.ctx is not ctx:
            cls._ctx = ctx
            cls._stype = z3.StringSort(ctx = ctx)
        return cls._stype

    @classmethod
    def mk_sval(cls, value, ctx=None):
        value = cls.null_val(ctx) if isinstance(value, NULL) else value
        return z3.StringVal(str(value), ctx)

    def __init__(self, symbol, concrete, ctx: z3.Context | None = None) -> None:
        concrete = str(symbol) if concrete is None else concrete
        super().__init__(symbol, concrete, ctx)
        self._len = None

    def _zv(self, other) -> t.Tuple[z3.ExprRef | str | float | int | bool | t.Any | NULL]:
        if isinstance(other, Term):
            o = other.cast('String')
            return o.symbol, str(o.concrete)
        elif NULL.is_null(other):
            return self.null_symbol(self.ctx), NULL(self.typeName())
        else:
            t = str(other)
            return z3.StringVal(t, ctx = self.ctx), t
    
    @property
    def length(self):
        if self._len is None:
            self._len = ZInt(symbol= z3.Length(self.symbol), concrete = len(self.concrete), ctx = self.ctx)
        return self._len
    def __len__(self):
        raise NotImplementedError(f'{__class__.__name__} not implemented')
    
    def __getitem__(self, idx):
        if isinstance( self.concrete , NULL):
            return self
        if isinstance(idx, slice):
            start, stop, step = idx.indices(len(self.concrete))
            assert step == 1 
            assert stop >= start
            rz = z3.SubString(self.symbol, start, stop - start)
            rv = self.concrete[idx]
        elif isinstance(idx, int):
            rz = z3.SubString(self.symbol, idx, 1)
            rv = self.concrete[idx]
        else:
            assert False  # for now
        return ZString(rz, rv, self.ctx)
    
    def __iter__(self):
        return zstr_iterator(self)
    
    def __eq__(self, other: TermOrLiteral) -> Term:
        z, v = self._zv(other)
        if isinstance(self.concrete , NULL) or isinstance(v, NULL):
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) > 1 else self.symbol == self.null_symbol(self.ctx)
            v_ = NULL.is_null(self.concrete) or NULL.is_null(v)
        else:
            z_ = self.symbol == z
            v_ = self.concrete == v
        return ZBool(z_, v_, self.ctx)
    def bool_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | t.Any, **kwargs) -> Term:
        z, v = self._zv(other)
        zfun = getattr(z3.SeqRef, fname)
        if isinstance(self.concrete , NULL) or isinstance(v , NULL) :
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) > 1 else self.symbol == self.null_symbol(self.ctx)
            fun = getattr(NULL, fname)
            v_ = NULL.is_null(self.concrete) or NULL.is_null(v)
            return ZBool( z_, v_, self.ctx)
        if fname == '__eq__':
            fun = getattr(str, fname)
            z_ = zfun(self.symbol, z)
            v_ = fun(self.concrete, v)

        elif dt_typ.is_float(v) or dt_typ.is_int(v):
            zfun =  getattr(z3.ArithRef, fname)
            z = z3.StrToInt(z) if  str(z.sort()) == 'String' else z
            z_ = zfun(z3.StrToInt(self.symbol), z)
            v = int(v)
            if not dt_typ.is_int(self.concrete):
                self.concrete = random.randint(10, 100)
            fun = getattr(float, fname)
            v_ = fun(float(self.concrete), v)
        else:
            fun = getattr(str, fname)
            z_ = zfun(self.symbol, z)
            v_ = fun(str(self.concrete), str(v))
        return ZBool( z_, v_, self.ctx)

    def binary_op(self, fname: str, other: Term | str | float | int | bool | t.List | t.Tuple | t.Any, **kwargs) -> Term:
        z, v = self._zv(other)
        
        if fname == '__sub__':
            return self.cast('Int') - other
        elif fname == '__add__':
            return self.cast('Int') + other
        elif fname == '__mul__':
            return self.cast('Int') * other
        zfun = getattr(z3.SeqRef, fname)
        
        if isinstance(self.concrete , NULL) or isinstance(v , NULL) :
            varis = z3.z3util.get_vars(self.symbol) + z3.z3util.get_vars(z)
            z_ = z3.Or([vari == self.get_null_symbol(vari) for vari in varis]) if len(varis) else self.symbol == self.null_symbol(self.ctx)
            fun = getattr(NULL, fname)
            v_ = fun(self.concrete, v)
            z_ = z3.If(z_, self.null_symbol(self.ctx), zfun(self.symbol, z))
            return ZString( z_, v_, self.ctx)
        
        fun = getattr(str, fname)
        z_ = zfun(self.symbol, z)
        v_ = fun(self.concrete, v)
        
        return ZString( z_, v_, self.ctx)
    
    def to_db(self) -> str | float | int | bool | t.Any | NULL:
        if isinstance(self.concrete, NULL):
            return 'NULL'
        return f'"{self.concrete}"'
    
    def substr(self, start: int | Term, length: int | Term = None) -> Term:
        if isinstance(self.concrete, NULL):
            return self
        if start is None:
            return self        
        if isinstance(start, Term):
            start = int(start.concrete)
        if isinstance(length, Term):
            length = int(length.concrete)
        concrete = ''
        start = int(start)
        if start < 0:
            _z_start = z3.Length(self.symbol) + start
            _v_start = len(self.concrete) + start
            length = abs(start)
        elif start > 0:
            _z_start = start - 1
            _v_start = start
        else:
            _z_start = 0
            _v_start = 0
        
        if length is not None and int(length) > 0:
            length = int(length)
        else:
            length = 1
        # length = int(length) if length is not None and int(length) > 0 else 1
        rz = z3.SubString(self.symbol, _z_start, length)
        self.add_constraint(z3.Length(self.symbol)>= abs(start) + abs(length))
        concrete = self.concrete[_v_start : _v_start + length]
        return ZString(symbol=rz, concrete= concrete, ctx = self.ctx)
    
    def instr(self, substring, start = 0):
        import random
        if self.concrete.find(substring, start) != -1:
            return ZInt( z3.IndexOf(self.symbol, substring), self.concrete.find(substring, start), self.ctx)
        if substring == ':':
            from datetime import time
            self.concrete = time(random.randint(0, 23), random.randint(0, 59), random.randint(0, 59)).strftime('%H:%M:%S.%f')[:-3]
        
        return ZInt(z3.IndexOf(self.symbol, substring) , self.concrete.find(substring, start) + 1, self.ctx)
    
    def like(self, other) -> Term:

        if isinstance(self.concrete, NULL):
            return self.is_null()
        
        # regex_pattern = other
        constraints = []        
        characters = string.ascii_letters + string.digits  # You can include other characters as needed
        start = 0
        base = ''
        for o in other:
            if o == '%':
                k = random.randint(1, 4)
                random_string = ''.join(random.choices(characters, k = k))
                if len(base):
                    constraints.append(z3.SubString(self.symbol, start, len(base)) == str(base))
                    start = start + len(base)
                rz = z3.SubString(self.symbol, start, k)
                constraints.append(rz == random_string)
                start = start + k
                base = ''
            elif o == '_':
                if len(base):
                    constraints.append(z3.SubString(self.symbol, start, len(base)) == str(base))
                    start = start + len(base)
                constraints.append(z3.SubString(self.symbol, start, 1) == random.choice(characters))
                start = start + 1
                base = ''
            else:
                base += o
        if not constraints:
            constraints.append(z3.Contains(self.symbol, other))
        
        regex_pattern = other.replace('*', r'\*').replace('+', r'\+').replace('%', '.*').replace('_', '\w')
        if re.match(regex_pattern, str(self.concrete)):
            concrete = True
        else:
            concrete = False
        return ZBool(z3.And(constraints), concrete = concrete, ctx = self.ctx)
    
    def cast(self, dataType: str) -> Term:
        z_ = None
        v_ = None

        dataType = dt_typ.normalize(dataType)

        if dataType == 'String':
            return self
        elif dataType in ['Int']:
            if str(self.symbol.sort()) == 'Int':
                z_ = self.symbol
            else:
                z_ = z3.StrToInt(self.symbol)            
            if not str(self.concrete).isdigit():
                self.concrete = random.randint(1, 100)
            v_ = int(self.concrete)
            module_name = 'ZInt'
        elif dataType == 'Real':
            z_ = z3.StrToInt(self.symbol)
            z_ = z3.ToReal(z_)
            if isinstance(self.concrete, str) and ( not self.concrete.isdigit() or not self.concrete.isdecimal()):
                self.concrete = random.randint(1, 100)
            v_ =  float(self.concrete)
            module_name = 'ZReal'
        elif dataType == 'Date':# ['Date', 'Datetime']:
            module_name = 'ZDate'
            year_ = z3.SubString(self.symbol, 0, 4)
            month_ = z3.SubString(self.symbol, 5, 2)
            day_ = z3.SubString(self.symbol, 8, 2)
            self.add_constraint(z3.SubString(self.symbol, 4, 1)  == '-')
            self.add_constraint(z3.SubString(self.symbol, 7, 1)  == '-')
            self.add_constraint(z3.Length(self.symbol) >= 10)

            year = z3.StrToInt(year_)
            month = z3.StrToInt(month_)
            day = z3.StrToInt(day_)

            if not dt_typ.is_datetime(self.concrete):
                self.concrete = date.today().strftime('%Y-%m-%d')
            v_ = date_parser.parse(self.concrete, fuzzy= True) #date_parser.isoparse(self.concrete)
            module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
            z_ = getattr(module, module_name).stype(self.ctx).Date(year, month, day)

        elif dataType == 'Datetime':
            module_name = 'ZDatetime'
            year_ = z3.SubString(self.symbol, 0, 4)
            # dash_ = z3.SubString(s_, 4, 1) 
            month_ = z3.SubString(self.symbol, 5, 2)
            # dash_ = z3.SubString(s_, 7, 1)
            day_ = z3.SubString(self.symbol, 8, 2)
            # self.add_constraint(z3.SubString(self.symbol, 4, 1)  == '-')
            # self.add_constraint(z3.SubString(self.symbol, 7, 1)  == '-')
            # self.add_constraint(z3.Length(self.symbol) >= 10)

            year = z3.StrToInt(year_)
            month = z3.StrToInt(month_)
            day = z3.StrToInt(day_)
            hour_ = z3.StrToInt(z3.SubString(self.symbol, 11, 2))
            minute_ = z3.StrToInt(z3.SubString(self.symbol, 14, 2))
            second_ = z3.StrToInt(z3.SubString(self.symbol, 17, 2))
            if not dt_typ.is_datetime(self.concrete):
                self.concrete = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            v_ = date_parser.isoparse(self.concrete)
            module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
            z_ = getattr(module, module_name).stype(self.ctx).Datetime(year, month, day, hour_, minute_, second_)        
        else:
            raise NotImplementedError(f'cannot cast {self} to {dataType}')
        module = importlib.import_module( f'{MODULE_PATH}.{module_name.lower()}')
        return getattr(module, module_name)( symbol= z_, concrete= v_, ctx = self.ctx)
    
    def udate(self):
        return self.cast('Datetime')
    
    def concat(self, other):
        z, v = self._zv(other)
        if isinstance(self, NULL):
            return ZString(z, v, self.ctx)
        z_ = z3.Concat(self.symbol, z)
        v_ = self.concrete + v
        return ZString(z_, v_, self.ctx)
    
    def __hash__(self) -> int:
        return hash(str(self.concrete))
        

