from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def sql_order_key(value: Any) -> tuple[int, int, Any]:
    """Return a deterministic SQL-like key for generated ORDER BY values."""
    if value is None:
        return (0, 0, None)
    if isinstance(value, bool):
        return (1, 1, int(value))
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return (1, 1, float(value))
    if isinstance(value, (datetime, date, time)):
        return (1, 2, value.isoformat())
    if isinstance(value, bytes):
        return (1, 4, value)
    if isinstance(value, str):
        return (1, 3, value)
    return (1, 5, str(value))
