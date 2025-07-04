from __future__ import annotations
from .symbolic_int import SymbolicInt
from ..ssa_factory import  create_symbol
import typing as t
import logging, inspect, z3
from .helper import *
from datetime import datetime, timedelta
from dateutil import parser as date_parser

logger = logging.getLogger(__name__)

BASE_DT = datetime(1970, 1, 1, hour= 0, minute= 0, second= 0)

# Function to convert datetime to UNIX timestamp (seconds since epoch)
def datetime_to_timestamp(dt):
    return int(dt.timestamp())

# Function to convert UNIX timestamp back to datetime
def timestamp_to_datetime(ts):
    return datetime.fromtimestamp(ts)

class SymbolicDatetime(SymbolicInt):
    def __init__(self, context, expr, value=None):
        if value is None:
            value = datetime_to_timestamp(BASE_DT)
        super().__init__(context, expr, value)
        self.format_ = '%Y-%m-%d %H:%M:%S'
        
    def strftime(self, format_string):
        self.format_ = format_string
        return self
        
    def _zv(self, other, func):

        if isinstance(other, SymbolicDatetime):
            return other.expr, other.value
        
        datetime_parts = {
            "%Y": "%Y" in self.format_,
            "%m": "%m" in self.format_,
            "%d": "%d" in self.format_,
            "%H": "%H" in self.format_,
            "%M": "%M" in self.format_,
            "%S": "%S" in self.format_,
        }
        operand = date_parser.parse(str(other))
        if func in ['__eq__', '__ne__', '__ge__', '__le__']:
            ts = operand.timestamp()
        elif func == '__lt__':
            year = operand.year if datetime_parts.get('%Y', False) else 2000
            month = operand.month if datetime_parts.get('%m', False) else 1
            day = operand.day if datetime_parts.get('%d', False) else 1
            hour = operand.hour if datetime_parts.get('%H', False) else 0
            minute = operand.minute if datetime_parts.get('%M', False) else 0
            second = operand.second if datetime_parts.get('%S', False) else 0
            ts = datetime(year, month, day, hour, minute, second).timestamp()
        elif func == '__gt__':
            year = operand.year if datetime_parts.get('%Y', False) else 2000
            month = operand.month if datetime_parts.get('%m', False) else 12
            day = operand.day if datetime_parts.get('%d', False) else 31
            hour = operand.hour if datetime_parts.get('%H', False) else 23
            minute = operand.minute if datetime_parts.get('%M', False) else 59
            second = operand.second if datetime_parts.get('%S', False) else 59
            ts = datetime(year, month, day, hour, minute, second).timestamp()
        else:
            raise ValueError(f'Unsupport func {func} in {self.__class__.__name__}')
        return z3.IntVal(ts), ts
    

   
    


BOOL_OPS = [
    ('eq', '=='),
    # '__req__',
    ('ne', '!='),
    # '__rne__',
    ('gt', '>'),
    ('lt', '<'),
    ('le', '<='),
    ('ge', '>='),
]

def make_method(method, op):
    
    code = "def %s(self, other):\n" % method
    code += "   func_name = inspect.currentframe().f_code.co_name\n"
    code += "   (expr, value) = self._zv(other, func_name)\n"
    code += "   v_ = self.value %s value\n" % op
    code += "   expr_ = self.expr %s expr\n" % op
    code += "   return ssa_factory.create_symbol(type(v_).__name__, self.context, expr_, v_)\n"
    locals_dict = {}
    exec(code, globals(), locals_dict)
    setattr(SymbolicDatetime, method, locals_dict[method])

for (name, op) in BOOL_OPS:
    method = "__%s__" % name
    make_method(method, op)
    rmethod = "__r%s__" % name
    make_method(rmethod, op)