"""Explicit concrete semantics for the standalone concolic runtime."""

from dataclasses import dataclass
from datetime import date, datetime, time
from math import isfinite

from parseval.terms.sorts import (
    BOOLEAN,
    DATE,
    FLOAT,
    INTEGER,
    INTERVAL,
    STRING,
    TIME,
    TIMESTAMP,
    IntervalValue,
    ScalarSort,
    TypeKind,
)


@dataclass(frozen=True, slots=True)
class Semantics:
    """Scalar policy, independent of the SQL frontend and SMT abstraction.

    Integers divide toward zero and remainder follows the dividend. DECIMAL
    and FLOAT share the Python binary64 carrier. Text comparison is
    case-sensitive, without a database collation. Division by zero raises,
    or returns NULL when ``division_by_zero_is_null`` is set. With
    ``lenient_conversions``, text converts to the number in its numeric
    prefix (zero if none) and a negative substring length selects the
    characters before the start, as in SQLite; otherwise both raise.
    With ``text_temporals`` (SQLite), temporal values are ISO text: text that
    does not parse as one converts to NULL, and a temporal converts to the
    number in its text's numeric prefix (the year, or the hour of a time).
    With ``case_insensitive_text`` (MySQL's default collation), text
    equality and LIKE whose outcome depends on case raise: generated text
    then never differs from a value it meets only by case, so case-sensitive
    evaluation agrees with the backend.
    """

    division_by_zero_is_null: bool = False
    lenient_conversions: bool = False
    text_temporals: bool = False
    case_insensitive_text: bool = False

    def text_key(self, value):
        """The value as the backend tells text apart: casefolded under
        ``case_insensitive_text``."""
        return value.casefold() if self.case_insensitive_text and isinstance(value, str) else value


_CARRIERS = {
    TypeKind.BOOLEAN: bool,
    TypeKind.INTEGER: int,
    TypeKind.FLOAT: float,
    TypeKind.DECIMAL: float,
    TypeKind.STRING: str,
    TypeKind.DATE: date,
    TypeKind.TIME: time,
    TypeKind.TIMESTAMP: datetime,
    TypeKind.INTERVAL: IntervalValue,
}


def infer_sort(value):
    if value is None:
        raise TypeError("NULL needs an explicit ScalarSort")
    if isinstance(value, bool):
        kind = BOOLEAN
    elif isinstance(value, int):
        kind = INTEGER
    elif isinstance(value, float):
        kind = FLOAT
    elif isinstance(value, str):
        kind = STRING
    elif isinstance(value, datetime):
        kind = TIMESTAMP
    elif isinstance(value, date):
        kind = DATE
    elif isinstance(value, time):
        kind = TIME
    elif isinstance(value, IntervalValue):
        kind = INTERVAL
    else:
        raise TypeError(f"Unsupported concrete type: {type(value).__name__}")
    return ScalarSort(kind)


def validate(value, sort):
    """Check assignments without discarding SQL NULL."""
    if not isinstance(sort, ScalarSort):
        raise TypeError("Concolic values require a ScalarSort")
    if value is None:
        if not sort.nullable:
            raise ValueError("NULL is not a non-nullable scalar value")
        return value
    kind = sort.sql_type.kind
    carrier = _CARRIERS.get(kind)
    if carrier is None or not isinstance(value, carrier):
        raise TypeError(f"{value!r} is not a {kind.value} value")
    if kind is TypeKind.INTEGER and isinstance(value, bool):
        raise TypeError("Boolean values are not integer inputs")
    if kind is TypeKind.DATE and isinstance(value, datetime):
        raise TypeError("Timestamp values are not date inputs")
    if kind in {TypeKind.TIME, TypeKind.TIMESTAMP} and value.tzinfo is not None:
        raise TypeError("Temporal values must have no time zone")
    if kind in (TypeKind.FLOAT, TypeKind.DECIMAL) and not isfinite(value):
        raise TypeError(f"{value!r} is not a {kind.value} value")
    return value
