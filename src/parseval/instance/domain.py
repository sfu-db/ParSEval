"""Values of scalar sorts: carriers, placeholders, constants and providers.

``Domain`` derives values from the constants and bounds of a query. It is
shared by the CSP, which picks values satisfying narrowed spaces, and by
speculation, which samples values on both sides of the query's comparisons.
Values unrelated to the query come from a ``Provider``: a callable given the
table, the column, the values already in use and whether the value must be
new, so other generators (Faker, dictionaries, samples of real data) can
supply them.
``sequential`` is the default.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from itertools import chain, count
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from parseval.catalog import ColumnBinding, TableDecl
from parseval.symbolic.operations import like as _like
from parseval.terms.sorts import DATE, TIMESTAMP, IntervalValue, ScalarSort, ScalarType, TypeKind, parse_iso_temporal_value

_PLACEHOLDERS = {
    TypeKind.BOOLEAN: False,
    TypeKind.INTEGER: 1,
    TypeKind.FLOAT: 1.0,
    TypeKind.DECIMAL: 1.0,
    TypeKind.STRING: "a",
    TypeKind.DATE: date(2000, 1, 1),
    TypeKind.TIME: time(0, 0),
    TypeKind.TIMESTAMP: datetime(2000, 1, 1),
    TypeKind.INTERVAL: IntervalValue(),
}


def carrier(value, sort: ScalarSort):
    """FLOAT and DECIMAL cells share the Python float carrier."""
    if (
        sort.sql_type.kind in (TypeKind.FLOAT, TypeKind.DECIMAL)
        and isinstance(value, (int, Decimal))
        and not isinstance(value, bool)
    ):
        return float(value)
    return value


def placeholder(sort: ScalarSort):
    """A valid non-NULL value of a sort, used for rows that are not yet stored."""
    return _PLACEHOLDERS[sort.sql_type.kind]


Provider = Callable[[TableDecl, ColumnBinding, Collection[object], bool], object]
"""``provider(table, column, existing, unique)``: a value valid for the column's
storage type (its length, integer width or decimal precision).

``existing`` holds the values of that kind already in use. When ``unique``
(the column is a key) the value must lie outside ``existing``; otherwise
``existing`` only informs the choice and values may repeat. Cells keep
provider values unless a requirement mentions them, so they are stored as
given and must fit the column.
"""


def sequential(table: TableDecl, column: ColumnBinding, existing: Collection[object], unique: bool):
    """An unused value of 1, 2, ...; "a", "b", ...; days from 2000-01-01 that
    fits the column; one repeats once every fitting value is in use.

    The search starts after as many values as are in use, so it meets few of
    them, and wraps around within the column's capacity.
    """
    storage = column.storage_type
    size = capacity(storage)
    start = len(existing)
    if size is None:
        indexes = count(start)
    else:
        indexes = chain(range(min(start, size), size), range(min(start, size)))
    return next((value for value in map(lambda index: _nth(storage, index), indexes) if value not in existing),
                _nth(storage, 0))


def limit(storage: ScalarType) -> tuple[str, float] | None:
    """A column's storage limit: ("length", n) for strings of at most n
    characters, ("integer", b) for -b <= x < b, ("decimal", b) for -b < x < b."""
    if storage.max_length is not None:
        return "length", storage.max_length
    if storage.integer_bits is not None:
        return "integer", 2 ** (storage.integer_bits - 1)
    if storage.kind is TypeKind.DECIMAL and storage.precision is not None:
        return "decimal", 10.0 ** (storage.precision - (storage.scale or 0))
    return None


def fits(value, storage: ScalarType) -> bool:
    """Whether a value is within a column's storage limit."""
    bound = limit(storage)
    if bound is None or value is None or isinstance(value, bool):
        return True
    kind, size = bound
    if kind == "length":
        return not isinstance(value, str) or len(value) <= size
    if not isinstance(value, (int, float)):
        return True
    return -size <= value < size if kind == "integer" else -size < value < size


def capacity(storage: ScalarType) -> int | None:
    """How many distinct values ``_nth`` gives that fit the storage type; None for unbounded."""
    kind = storage.kind
    if kind is TypeKind.BOOLEAN:
        return 2
    if kind is TypeKind.TIME:
        return 24 * 60
    if kind is TypeKind.STRING and storage.max_length is not None:
        return sum(26**length for length in range(1, storage.max_length + 1))
    if kind is TypeKind.INTEGER and storage.integer_bits is not None:
        return 2 ** (storage.integer_bits - 1) - 1
    if kind is TypeKind.DECIMAL and storage.precision is not None:
        scale = storage.scale or 0
        # Whole numbers below 10^(precision - scale), or fractions when there are no integer digits.
        return 10 ** (storage.precision - scale if storage.precision > scale else scale) - 1
    return None


def _nth(storage: ScalarType, index: int):
    kind = storage.kind
    if kind is TypeKind.BOOLEAN:
        return index % 2 == 1
    if kind is TypeKind.INTEGER:
        return index + 1
    if kind is TypeKind.DECIMAL and storage.precision is not None and storage.precision <= (storage.scale or 0):
        # No integer digits: fractions of the smallest unit.
        return (index + 1) / 10**storage.scale
    if kind in (TypeKind.FLOAT, TypeKind.DECIMAL):
        return float(index + 1)
    if kind is TypeKind.STRING:
        text = ""
        index += 1
        while index:
            index, digit = divmod(index - 1, 26)
            text = chr(ord("a") + digit) + text
        return text
    if kind is TypeKind.DATE:
        return date(2000, 1, 1) + timedelta(days=index)
    if kind is TypeKind.TIMESTAMP:
        return datetime(2000, 1, 1) + timedelta(days=index)
    if kind is TypeKind.TIME:
        return time(index // 60 % 24, index % 60)
    return IntervalValue(days=index)


def like(value, pattern: str) -> bool:
    """SQL LIKE as executed; non-strings never match."""
    return isinstance(value, str) and _like(value, pattern)


@dataclass(frozen=True, slots=True)
class Domain:
    """Values of one scalar kind."""

    kind: TypeKind

    @classmethod
    def of(cls, sort: ScalarSort) -> Domain:
        return cls(sort.sql_type.kind)

    def around(self, constant):
        """The constant converted to this kind, and its neighbours."""
        kind = self.kind
        if isinstance(constant, bool):
            if kind is TypeKind.BOOLEAN:
                yield from (constant, not constant)
            return
        number = constant if isinstance(constant, (int, float)) else _number(constant)
        if kind is TypeKind.INTEGER and number is not None:
            yield from (int(number), int(number) + 1, int(number) - 1)
        elif kind in (TypeKind.FLOAT, TypeKind.DECIMAL) and number is not None:
            yield from (float(number), float(number) + 1, float(number) - 1, float(number) + 0.5)
        elif kind is TypeKind.STRING:
            if isinstance(constant, str):
                filled = constant.replace("%", "").replace("_", "a")
                yield from (constant, filled, filled + "a", "a" + filled, constant + "a")
            elif number is not None:
                yield str(constant)
        elif kind in (TypeKind.DATE, TypeKind.TIMESTAMP):
            # Neighbours lie one unit of the constant's own precision away:
            # '1991' is a year, so 1990 and 1992 lie around it.
            moment, unit = _moment(constant, kind)
            if moment is not None:
                yield moment
                for direction in (1, -1):
                    neighbour = _shift(moment, unit, direction)
                    if neighbour is not None:
                        yield neighbour

    def near(self, value, direction: int, strict: bool):
        """Values at or just beyond a bound, in the given direction."""
        kind = self.kind
        if not strict:
            yield value
        if kind is TypeKind.INTEGER and isinstance(value, int):
            yield value + direction
        elif kind in (TypeKind.FLOAT, TypeKind.DECIMAL) and isinstance(value, (int, float)):
            yield float(value) + direction * 0.5
            yield float(value) + direction
        elif kind is TypeKind.STRING and isinstance(value, str):
            if direction > 0:
                yield value + "a"
            elif value:
                yield value[:-1] + chr(max(ord(value[-1]) - 1, 32))
        elif kind is TypeKind.DATE and isinstance(value, date):
            yield value + direction * timedelta(days=1)
        elif kind is TypeKind.TIMESTAMP and isinstance(value, datetime):
            yield value + direction * timedelta(seconds=1)

    @staticmethod
    def matching(pattern: str):
        """Strings matching a LIKE pattern."""
        for filler in ("", "a", "aa"):
            yield pattern.replace("%", filler).replace("_", "a")


@dataclass(slots=True)
class Space:
    """The values one input may still take."""

    kind: TypeKind
    nullable: bool
    null: bool | None = None
    equals: object = None
    excluded: set = field(default_factory=set)
    lower: object = None
    lower_strict: bool = False
    upper: object = None
    upper_strict: bool = False
    patterns: tuple = ()
    rejected: tuple = ()
    tests: tuple = ()

    def narrow(self, op: str, value) -> bool:
        """Apply one atom; False when no value remains."""
        if op == "test":
            # Checked against candidate values when they are picked.
            self.tests += (value,)
            return True
        if op == "null":
            if self.null is False or not self.nullable:
                return False
            self.null = True
            return True
        if self.null is True:
            return False
        self.null = False
        if op == "=":
            if self.equals is not None and self.equals != value:
                return False
            self.equals = value
        elif op == "!=":
            self.excluded.add(value)
        elif op in (">", ">="):
            if self.lower is None or value > self.lower or (value == self.lower and op == ">"):
                self.lower, self.lower_strict = value, op == ">"
        elif op in ("<", "<="):
            if self.upper is None or value < self.upper or (value == self.upper and op == "<"):
                self.upper, self.upper_strict = value, op == "<"
        elif op == "like":
            self.patterns += (value,)
        elif op == "notlike":
            self.rejected += (value,)
        if self.equals is not None:
            return self.admits(self.equals)
        if self.lower is not None and self.upper is not None:
            return self.lower < self.upper or (self.lower == self.upper and not (self.lower_strict or self.upper_strict))
        return True

    def admits(self, value) -> bool:
        if value is None:
            return self.null is not False and self.nullable
        if self.null is True or value in self.excluded:
            return False
        if self.equals is not None and value != self.equals:
            return False
        if self.lower is not None and (value < self.lower or (self.lower_strict and value == self.lower)):
            return False
        if self.upper is not None and (value > self.upper or (self.upper_strict and value == self.upper)):
            return False
        return all(like(value, pattern) for pattern in self.patterns) and not any(
            like(value, pattern) for pattern in self.rejected
        )

    def candidates(self, preferred, min_string_length: int):
        """Values worth trying, from the most to the least preferred."""
        if self.equals is not None:
            yield self.equals
            return
        yield preferred
        for bound, strict, direction in ((self.lower, self.lower_strict, 1), (self.upper, self.upper_strict, -1)):
            if bound is not None:
                yield from Domain(self.kind).near(bound, direction, strict)
        for pattern in self.patterns:
            yield from Domain.matching(pattern)
        if self.kind is TypeKind.STRING:
            base = self.lower if isinstance(self.lower, str) else "a" * max(min_string_length, 1)
            for suffix in ("", "a", "b", "z", "0", "1"):
                yield base + suffix
        elif self.kind in (TypeKind.INTEGER, TypeKind.FLOAT, TypeKind.DECIMAL):
            for number in (0, 1, -1, 2, 10, 100):
                yield number if self.kind is TypeKind.INTEGER else float(number)
        for excluded in list(self.excluded)[:4]:
            yield from Domain(self.kind).near(excluded, 1, True)


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _moment(constant, kind: TypeKind):
    """The date or timestamp a constant denotes and its precision: year, month, day or second."""
    if isinstance(constant, datetime):
        return (constant, "second") if kind is TypeKind.TIMESTAMP else (constant.date(), "day")
    if isinstance(constant, date):
        return (constant, "day") if kind is TypeKind.DATE else (datetime.combine(constant, datetime.min.time()), "day")
    if not isinstance(constant, str):
        return None, None
    text = constant.strip()
    if len(text) == 4 and text.isdigit():
        text, unit = text + "-01-01", "year"
    elif len(text) == 7 and text[:4].isdigit() and text[4] == "-":
        text, unit = text + "-01", "month"
    else:
        unit = "day" if len(text) <= 10 else "second"
    try:
        return parse_iso_temporal_value(text, DATE if kind is TypeKind.DATE else TIMESTAMP), unit
    except (TypeError, ValueError):
        return None, None


def _shift(moment, unit: str, direction: int):
    """``moment`` moved by one ``unit``, or None outside the calendar."""
    try:
        if unit == "year":
            return moment.replace(year=moment.year + direction)
        if unit == "month":
            months = moment.year * 12 + moment.month - 1 + direction
            return moment.replace(year=months // 12, month=months % 12 + 1)
        return moment + direction * (timedelta(days=1) if unit == "day" else timedelta(seconds=1))
    except (ValueError, OverflowError):
        return None


__all__ = ["Domain", "Provider", "Space", "capacity", "carrier", "fits", "like", "limit", "placeholder", "sequential"]
