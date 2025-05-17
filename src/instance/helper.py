import random, re
from sqlglot import exp
from typing import List, Any
from datetime import datetime

def random_value_from_list(values, skips = [], default = None):
    candidates = [v for v in values if v not in skips ]
    return random.choice(candidates) if candidates else default

def generate_unique_value(table_name, column_name, dtype, existing_values, max_attempts = 5):
    dtype = exp.DataType.build(dtype)
    for idx in range(max_attempts):
        if dtype.is_type(*exp.DataType.NUMERIC_TYPES):
            v = random.randint(1, 65535)
        elif dtype.is_type(*exp.DataType.TEXT_TYPES):
            v = f'{table_name}_{column_name}_{dtype}_{len(existing_values) + 1}'
        elif dtype.is_type(*exp.DataType.TEMPORAL_TYPES):
            v = None
        else:
            raise ValueError(f'could not generate unique values for datatype: {dtype}')
        if v not in existing_values:
            return v
    return None

def convert(value: Any, copy: bool = False) -> exp.Expression:
    """A wrapper of exp.convert. Convert a value(symbol or concrete) into an expression object.
    Raises an error if a conversion is not possible.
    Args:
        value: A python object.
        copy: Whether to copy `value` (only applies to Expressions and collections).

    Returns:
        The equivalent expression object.
    """
    v = value
    if hasattr(value, 'dtype'):
        v = value.value
        if value.dtype == 'Str':
            v = v.replace(':', '\:')
        elif value.dtype == 'Datetime':
            v = datetime.fromtimestamp(v)
    return exp.convert(value= v, copy= copy)

def clean_name(name) -> str:
    pattern = r'[^a-zA-Z0-9_]'
    cleaned_str = re.sub(pattern, '', name)
    return cleaned_str