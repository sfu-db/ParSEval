from __future__ import annotations
import typing as t
import datetime

if t.TYPE_CHECKING:
    from ._typing import TermLiterals

LABELED_NULL = {
    'int' : 6789,
    'real' : 0.6789,
    'string' : 'NULL',
    'bool' : 'NULL',
    'datetime' : datetime.datetime(1970, 1, 1, 0, 0, 0),
    'date' : datetime.date(1970, 1, 1),
}

class NA:
    def __init__(self, typ = 'int') -> None:
        self.typ = typ.lower()
        self.value = LABELED_NULL[typ.lower()]

    @staticmethod
    def is_null(val, typ = 'int'):
        if isinstance(val, NULL):
            return True
        for k , v in LABELED_NULL.items():
            if val == v:
                return True
        return False

    def zv(self, v):
        if isinstance(v, NA):
            return v
        for k , val in LABELED_NULL.items():
            if val == v:
                return NA(self.typ)
        return v

    def __repr__(self):
        return 'NULL'
    def __str__(self):
        return 'NULL'
    def __int__(self):
        return LABELED_NULL['int']
    def __float__(self):
        return LABELED_NULL['real']
    def __len__(self):
        return 0
    def __eq__(self, other: TermLiterals) -> bool:
        if isinstance(other, NA) and isinstance(self, NA):
            return True
        
        return False

    def __ne__(self, other: TermLiterals):
        o = self.zv(other)
        if isinstance(o, NA) ^ isinstance(self, NA):
            return True
        return not self.__eq__(other)
    
    def __hash__(self) -> int:
        return hash(str('NULL'))
   
    def __gt__(self, other: TermLiterals):
        return NA()

    def __ge__(self, other: TermLiterals):
        return NA()

    def __lt__(self, other: TermLiterals):
        return NA()

    def __le__(self, other: TermLiterals):
        return NA()
    def __mod__(self, other: TermLiterals):
        return NA()

    def __add__(self, other: TermLiterals):
        return NA()

    def __sub__(self, other: TermLiterals):
        return NA()
    def __mul__(self, other: TermLiterals):
        return NA()
    def __truediv__(self, other: TermLiterals):
        return NA()
    def __div__(self, other: TermLiterals):
        return NA()
    def __neg__(self):
        return NA()    
    def __invert__(self):
        return NA()
    def unary_op(self, fname: str, **kwargs):
        return NA()

NULL = NA