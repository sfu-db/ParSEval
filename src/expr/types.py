from __future__ import annotations
from enum import auto
from typing import Union
from .base import Expr
from .helper import AutoName

class DataType(Expr):
    """Expression type system implementation"""
    # ... (existing DataType implementation)

# Move this to a separate types_common.py to avoid circular imports
DATA_TYPE = Union[str, DataType, DataType.Type] 