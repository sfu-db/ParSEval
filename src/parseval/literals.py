"""Extract Python values from sqlglot AST literal leaves."""

from __future__ import annotations

import re
from typing import Any, Optional

from sqlglot import exp

from parseval.dtype import TypeFamily, type_family


def unit_name(node: Any) -> Optional[str]:
    if node is None:
        return None
    value = getattr(node, "this", node)
    return str(value).upper()


def integer_literal(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    return None


def literal_value(node: exp.Expression) -> Any:
    """Extract a Python value from a literal-ish AST leaf (no Environment)."""
    if isinstance(node, exp.Literal):
        dtype = getattr(node, "type", None)
        if dtype is not None and type_family(dtype) in {
            TypeFamily.TEXT,
            TypeFamily.DATE,
            TypeFamily.DATETIME,
            TypeFamily.TIME,
        }:
            return str(node.this)
        if node.is_int:
            try:
                return int(node.this)
            except (TypeError, ValueError):
                return str(node.this)
        if node.is_number:
            try:
                return float(node.this)
            except (TypeError, ValueError):
                return str(node.this)
        return str(node.this)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Neg):
        inner = literal_value(node.this)
        if isinstance(inner, (int, float)):
            return -inner
        return None
    if isinstance(node, exp.Cast):
        return literal_value(node.this)
    return None


__all__ = ["integer_literal", "literal_value", "unit_name"]
