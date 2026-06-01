"""Shared types for the solver module: ValueSpace, CSP structures, ColumnPredicate."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time
from enum import Enum
from typing import Any, Optional, Set, Dict, List
from sqlglot import exp

from parseval.dtype import DataType


class TypeFamily(Enum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"


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


def type_family(dtype: DataType) -> TypeFamily:
    """Map a DataType to a TypeFamily."""
    if dtype.is_type(*DataType.INTEGER_TYPES):
        return TypeFamily.INTEGER
    if dtype.is_type(*DataType.REAL_TYPES):
        return TypeFamily.DECIMAL
    if dtype.is_type(DataType.Type.BOOLEAN):
        return TypeFamily.BOOLEAN
    if dtype.is_type(
        DataType.Type.DATETIME, DataType.Type.DATETIME64,
        DataType.Type.TIMESTAMP, DataType.Type.TIMESTAMPLTZ,
        DataType.Type.TIMESTAMPTZ, DataType.Type.TIMESTAMP_MS,
        DataType.Type.TIMESTAMP_NS, DataType.Type.TIMESTAMP_S,
    ):
        return TypeFamily.DATETIME
    if dtype.is_type(DataType.Type.DATE):
        return TypeFamily.DATE
    if dtype.is_type(DataType.Type.TIME, DataType.Type.TIMETZ):
        return TypeFamily.TIME
    return TypeFamily.TEXT


# =============================================================================
# Temporal Parsing & Encoding
# =============================================================================


def parse_date(value: Any) -> Optional[date]:
    """Parse a value into a ``date``, or None if unparseable."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            if "T" in value or " " in value:
                return datetime.fromisoformat(value.replace(" ", "T")).date()
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def parse_time(value: Any) -> Optional[dt_time]:
    """Parse a value into a ``time``, or None if unparseable."""
    if isinstance(value, datetime):
        return value.time().replace(microsecond=0)
    if isinstance(value, dt_time):
        return value.replace(microsecond=0)
    if isinstance(value, str):
        try:
            if "T" in value or " " in value:
                return datetime.fromisoformat(value.replace(" ", "T")).time().replace(
                    microsecond=0
                )
            return dt_time.fromisoformat(value[:8])
        except ValueError:
            return None
    return None


def parse_datetime(value: Any) -> Optional[datetime]:
    """Parse a value into a ``datetime``, or None if unparseable."""
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        for candidate in (value.replace(" ", "T"), value):
            try:
                return datetime.fromisoformat(candidate).replace(microsecond=0)
            except ValueError:
                continue
    return None


def date_to_epoch_day(value: Any) -> int:
    """Convert a date/datetime/string to days since Unix epoch."""
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError(f"Cannot parse as date: {value!r}")
    return (parsed - date(1970, 1, 1)).days


def time_to_seconds(value: Any) -> int:
    """Convert a time/datetime/string to seconds since midnight."""
    parsed = parse_time(value)
    if parsed is None:
        raise ValueError(f"Cannot parse as time: {value!r}")
    return parsed.hour * 3600 + parsed.minute * 60 + parsed.second


def datetime_to_epoch_second(value: Any) -> int:
    """Convert a datetime/date/string to Unix epoch seconds."""
    parsed = parse_datetime(value)
    if parsed is None:
        raise ValueError(f"Cannot parse as datetime: {value!r}")
    return int((parsed - datetime(1970, 1, 1)).total_seconds())


def epoch_day_to_date(days: int) -> date:
    """Convert days since Unix epoch to a ``date``."""
    return date(1970, 1, 1) + __import__("datetime").timedelta(days=days)


def seconds_to_time(seconds: int) -> dt_time:
    """Convert seconds since midnight to a ``time``."""
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return dt_time(h, m, s)


def epoch_second_to_datetime(value: int) -> datetime:
    """Convert Unix epoch seconds to a timezone-naive ``datetime``."""
    return datetime.fromtimestamp(value, tz=__import__("datetime").timezone.utc).replace(tzinfo=None)


def infer_type_from_value(value: Any) -> DataType:
    """Infer a SQL DataType from a Python value's runtime type."""
    if value is None:
        return DataType.build("NULL")
    if isinstance(value, bool):
        return DataType.build("BOOLEAN")
    if isinstance(value, int):
        return DataType.build("INT")
    if isinstance(value, float):
        return DataType.build("FLOAT")
    if isinstance(value, str):
        return DataType.build("TEXT", length=len(value))
    if isinstance(value, dt_time):
        return DataType.build("TIME")
    if isinstance(value, datetime):
        return DataType.build("DATETIME")
    if isinstance(value, date):
        return DataType.build("DATE")
    return DataType.build("TEXT")


def infer_type_from_string(value: str) -> Any:
    """Try to parse a string as a typed Python value (int, float, date, etc.).

    Returns the parsed value, or the original string if no type matches.
    """
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    # Try date before datetime — date-only strings like '2024-01-01' should
    # parse as date, not datetime.  Skip if the string has time components.
    has_time = ":" in value or (" " in value and len(value) > 10)
    if not has_time:
        parsed = parse_date(value)
        if parsed is not None and not isinstance(parsed, datetime):
            return parsed
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed
    parsed = parse_time(value)
    if parsed is not None:
        return parsed
    return value


__all__ = [
    "TypeFamily",
    "ValueSpace",
    "CSPVariable",
    "CSPConstraint",
    "ColumnPredicate",
    "col_type",
    "type_family",
]
