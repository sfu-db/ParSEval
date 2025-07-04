"""Utilities for type handling and normalization."""
import re
from typing import Dict, Tuple, Type
import z3

# Type mapping configurations
BASE_SYMBOLIC_TYPE_MAPPINGS: Dict[str, Tuple[str, Type[z3.SortRef]]] = {
    'bool': ('bool', z3.Bool),
    'int': ('int', z3.Int),
    'string': ('string', z3.String),
    'real': ('real', z3.Real),
    'datetime': ('datetime', z3.Int),
    'date': ('datetime', z3.Int)
}

SYMBOLIC_TYPE_PATTERNS = {
    'int': [
        r'^int(?:\d+)?$',          # int, int32, int64, etc.
        r'^bigint$',
        r'^smallint$',
        r'^tinyint$',
        r'^integer$',
        r'^long$',
        r'^short$'
    ],
    'real': [
        r'^float(?:\d+)?$',        # float, float32, float64
        r'^double$',
        r'^decimal(?:\(\d+,\d+\))?$',  # decimal(10,2)
        r'^numeric(?:\(\d+,\d+\))?$',  # numeric(10,2)
        r'^real$'
    ],
    'string': [
        r'^varchar(?:\(\d+\))?$',   # varchar(255)
        r'^char(?:\(\d+\))?$',      # char(10)
        r'^text$',
        r'^string$',
        r'^nvarchar(?:\(\d+\))?$',
        r'^nchar(?:\(\d+\))?$',
        r'^clob$'
    ],
    'bool': [
        r'^bool(?:ean)?$',
        r'^bit$'
    ],
    'datetime': [
        r'^datetime(?:\(\d\))?$',   # datetime(6)
        r'^timestamp(?:\(\d\))?$',
        r'^date$',
        r'^time$'
    ]
}

def normalize_type(dtype: str) -> str:
    """
    Normalize various data type representations to standard types.
    
    Args:
        dtype: The input data type string
        
    Returns:
        Normalized data type string
        
    Examples:
        >>> normalize_type('VARCHAR(255)')
        'string'
        >>> normalize_type('INT64')
        'int'
    """
    dtype = dtype.lower().strip()
    
    # Direct match
    if dtype in BASE_SYMBOLIC_TYPE_MAPPINGS:
        return BASE_SYMBOLIC_TYPE_MAPPINGS[dtype][0]

    # Pattern matching
    for base_type, patterns in SYMBOLIC_TYPE_PATTERNS.items():
        if any(re.match(pattern, dtype) for pattern in patterns):
            return base_type

    # Handle array types
    if dtype.endswith('[]') or dtype.startswith('array<'):
        inner_type = re.search(r'array<(.+)>|(.+)\[\]', dtype)
        if inner_type:
            base_type = inner_type.group(1) or inner_type.group(2)
            return f"array<{normalize_type(base_type)}>"

    raise ValueError(
        f"Unsupported data type: {dtype}. "
        f"Supported base types: {list(SYMBOLIC_TYPE_PATTERNS.keys())}"
    )