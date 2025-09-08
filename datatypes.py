from dateutil import parser as date_parser
import typing as t
from sqlglot import expressions as exp

INT_TYPES = {
    *exp.DataType.INTEGER_TYPES
}
REAL_TYPES = {
    *exp.DataType.REAL_TYPES
}
STRING_TYPES = {
    *exp.DataType.TEXT_TYPES
}
BOOLEAN_TYPES = {
    exp.DataType.build('Bool'),
    exp.DataType.Type.BOOLEAN
}
DATE_TYPES = {
    exp.DataType.Type.DATE32, 
    exp.DataType.Type.DATE
}
DATETIME_TYPES = {
    exp.DataType.Type.DATETIME,
    exp.DataType.Type.DATETIME64,
    exp.DataType.Type.TIME,
    exp.DataType.Type.TIMESTAMP,
    exp.DataType.Type.TIMESTAMPLTZ,
    exp.DataType.Type.TIMESTAMPTZ,
    exp.DataType.Type.TIMESTAMP_MS,
    exp.DataType.Type.TIMESTAMP_NS,
    exp.DataType.Type.TIMESTAMP_S,
    exp.DataType.Type.TIMETZ,
}

def get_datatype(val):
    if getattr(val, 'key', None):
        return val.key    
    if is_int(val):
        return 'Int'
    if is_float(val):
        return 'Real'
    if is_datetime(str(val)):
        return 'Date'
    if is_bool(val):
        return 'Bool'
    return 'String'

def normalize(dtype: t.Union[exp.DataType, str]):
    if isinstance(dtype, str):
        dtype = exp.DataType.build(dtype= dtype)
    if dtype.is_type(*INT_TYPES):
        return 'Int'
    elif dtype.is_type(*REAL_TYPES):
        return 'Real'
    elif dtype.is_type(*STRING_TYPES):
        return 'String'
    elif dtype.is_type(*DATE_TYPES):
        return 'Date'
    elif dtype.is_type(*DATETIME_TYPES):
        return 'Datetime'
    elif dtype.is_type(*BOOLEAN_TYPES):
        return 'Bool'
    
    raise TypeError(f' cannot support data type {dtype}')
   
def is_int(text: str) -> bool:
    if isinstance(text, bool):
        return False
    return is_type(text, int)

def is_float(text: str) -> bool:
    if isinstance(text, bool):
        return False
    return is_type(text, float)

def is_datetime(text: str) -> bool:
    try:
        datetime_object = date_parser.parse(text)
        return True
    except Exception as e:
        return False

def is_bool(text: str) -> bool:
    if str(text).lower() in ['true', 'false']:
        return True
    return False

def is_type(text: str, target_type: t.Type) -> bool:
    try:
        target_type(text)
        return True
    except TypeError:
        return False
    except ValueError:
        return False
