"""Shared types for the solver module: ValueSpace, CSP structures, ColumnPredicate."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional, Set
from sqlglot import exp

from parseval.dtype import (
    DataType,
    TypeFamily,
    date_to_epoch_day,
    datetime_to_epoch_second,
    epoch_day_to_date,
    epoch_second_to_datetime,
    infer_type_from_string,
    infer_type_from_value,
    parse_date,
    parse_datetime,
    parse_time,
    seconds_to_time,
    time_to_seconds,
    type_family,
)


@dataclass
class ValueSpace:
    """The narrowed space of valid values for a variable."""
    family: TypeFamily = TypeFamily.TEXT
    min_val: Optional[Any] = None
    max_val: Optional[Any] = None
    equals: Optional[Any] = None
    not_equals: Set[Any] = field(default_factory=set)
    allowed: Optional[Set[Any]] = None
    must_null: bool = False
    not_null: bool = False
    like_pattern: Optional[str] = None
    max_length: Optional[int] = None

    def is_empty(self) -> bool:
        if self.must_null and self.not_null:
            return True
        if self.must_null:
            return False
        if self.equals is not None:
            if self.equals in self.not_equals:
                return True
            if self.min_val is not None and self.equals < self.min_val:
                return True
            if self.max_val is not None and self.equals > self.max_val:
                return True
            if self.allowed is not None and self.equals not in self.allowed:
                return True
            return False
        if self.min_val is not None and self.max_val is not None:
            if self.min_val > self.max_val:
                return True
        if self.allowed is not None:
            valid = self.allowed - self.not_equals
            if not valid:
                return True
        if self.family == TypeFamily.BOOLEAN:
            candidates = {True, False}
            if self.allowed is not None:
                candidates &= self.allowed
            if not candidates - self.not_equals:
                return True
        return False

    def pick(self) -> Any:
        if self.must_null:
            return None
        if self.equals is not None:
            if self.equals in self.not_equals:
                return None
            return self.equals
        if self.allowed is not None:
            valid = self.allowed - self.not_equals
            return min(valid) if valid else None
        if self.family in (TypeFamily.INTEGER, TypeFamily.DECIMAL):
            return self._pick_numeric()
        elif self.family == TypeFamily.TEXT:
            return self._pick_text()
        elif self.family in (TypeFamily.DATE, TypeFamily.DATETIME, TypeFamily.TIME):
            return self._pick_temporal()
        elif self.family == TypeFamily.BOOLEAN:
            candidates = {True, False}
            if self.allowed is not None:
                candidates &= self.allowed
            for value in (True, False):
                if value in candidates and value not in self.not_equals:
                    return value
            return None
        # Fallback: return a safe default that won't cause type coercion errors.
        return None

    def _pick_numeric(self) -> Any:
        lo = self.min_val if self.min_val is not None else 1
        hi = self.max_val if self.max_val is not None else lo + 100
        if lo > hi:
            return None
        is_integer = self.family == TypeFamily.INTEGER
        if is_integer:
            lo = int(lo)
            hi = int(hi)
            mid = (lo + hi) // 2
            for offset in range(hi - lo + 1):
                for try_val in (mid + offset, mid - offset):
                    if lo <= try_val <= hi and try_val not in self.not_equals:
                        return try_val
        else:
            mid = (lo + hi) / 2
            for try_val in (mid, lo, hi):
                if try_val not in self.not_equals:
                    return try_val
        return None

    def _pick_text(self) -> Optional[str]:
        if self.like_pattern:
            return self.like_pattern.replace("%", "x").replace("_", "a")
        length = min(self.max_length or 10, 10)
        # Handle numeric bounds for TEXT columns (e.g., `Academic Year` BETWEEN 2014 AND 2015).
        # Return a string representation of a number within the range.
        has_numeric_min = self.min_val is not None and isinstance(self.min_val, (int, float))
        has_numeric_max = self.max_val is not None and isinstance(self.max_val, (int, float))
        if has_numeric_min or has_numeric_max:
            lo = int(self.min_val) if has_numeric_min else int(self.max_val) - 100
            hi = int(self.max_val) if has_numeric_max else int(self.min_val) + 100
            # Pick the midpoint, avoiding not_equals.
            mid = (lo + hi) // 2
            for offset in range(hi - lo + 1):
                for try_val in (mid + offset, mid - offset):
                    if lo <= try_val <= hi and str(try_val) not in self.not_equals:
                        return str(try_val)
            return str(lo)
        base = "value"[:length]
        # Respect min_val: append a character to ensure we exceed it.
        if self.min_val is not None and isinstance(self.min_val, str):
            base = self.min_val + "a"
        # Respect max_val: truncate to stay within bound.
        if self.max_val is not None and isinstance(self.max_val, str):
            if base > self.max_val:
                base = self.max_val
        base = base[:length]
        if not base:
            base = "v"
        i = 1
        while base in self.not_equals:
            base = f"val_{i}"[:length]
            if not base:
                base = "v"
            i += 1
        return base

    def _pick_temporal(self) -> Any:
        # Handle LIKE patterns for DATE/DATETIME columns (e.g., '1996-01%').
        if self.like_pattern:
            # Extract the prefix before wildcards and try to parse as date.
            prefix = self.like_pattern.replace('%', '').replace('_', '')
            if len(prefix) >= 4:
                # Try different date formats based on prefix length.
                try:
                    if len(prefix) <= 4:
                        return date(int(prefix), 1, 1)
                    elif len(prefix) <= 7:
                        return date.fromisoformat(prefix + '-01')
                    else:
                        return date.fromisoformat(prefix[:10])
                except (ValueError, IndexError):
                    pass
        if self.min_val is not None:
            if isinstance(self.min_val, datetime):
                return self.min_val
            if isinstance(self.min_val, date):
                return self.min_val
            if isinstance(self.min_val, str):
                try:
                    return date.fromisoformat(self.min_val[:10])
                except (ValueError, IndexError):
                    pass
        if self.max_val is not None:
            if isinstance(self.max_val, date):
                return self.max_val
        return date(2024, 6, 15)

    def narrow_min(self, val: Any) -> None:
        if self.min_val is None or val > self.min_val:
            self.min_val = val

    def narrow_max(self, val: Any) -> None:
        if self.max_val is None or val < self.max_val:
            self.max_val = val

    def narrow_eq(self, val: Any) -> None:
        self.equals = val

    def narrow_neq(self, val: Any) -> None:
        self.not_equals.add(val)

    def narrow_in(self, values: Set[Any]) -> None:
        if self.allowed is None:
            self.allowed = values
        else:
            self.allowed &= values


@dataclass
class CSPVariable:
    """A column variable in the CSP solver."""
    name: str
    table: str
    column: str
    space: ValueSpace
    assigned: Optional[Any] = None


@dataclass
class CSPConstraint:
    """A relationship between two CSP variables."""
    kind: str
    left: str
    right: str


@dataclass
class ColumnPredicate:
    """A lowered constraint on a single column."""
    table: str
    column: str
    op: str
    value: Any


def col_type(col: exp.Column) -> Optional[DataType]:
    """Read the annotated type from a Column node, or None."""
    dtype = getattr(col, "type", None)
    if dtype is None:
        return None
    if isinstance(dtype, DataType):
        return dtype
    try:
        return DataType.build(str(dtype))
    except Exception:
        return None


__all__ = [
    "TypeFamily",
    "ValueSpace",
    "CSPVariable",
    "CSPConstraint",
    "ColumnPredicate",
    "col_type",
    "type_family",
    "parse_date",
    "parse_time",
    "parse_datetime",
    "date_to_epoch_day",
    "time_to_seconds",
    "datetime_to_epoch_second",
    "epoch_day_to_date",
    "seconds_to_time",
    "epoch_second_to_datetime",
    "infer_type_from_value",
    "infer_type_from_string",
]
