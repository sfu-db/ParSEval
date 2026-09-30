"""SQL scalar domains and IR value sorts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
import re
from typing import TypeAlias, TypeGuard

from parseval.errors import ScalarTypeError, expect

from .names import (
    AggregateSpecId,
    SchemaId,
)


class TypeKind(str, Enum):
    BOOLEAN = "boolean"
    INTEGER = "integer"
    FLOAT = "float"
    DECIMAL = "decimal"
    STRING = "string"
    DATE = "date"
    TIME = "time"
    TIMESTAMP = "timestamp"
    INTERVAL = "interval"
    OPAQUE = "opaque"


@dataclass(frozen=True, slots=True)
class ScalarType:
    kind: TypeKind
    precision: int | None = None
    scale: int | None = None

    def __post_init__(self) -> None:
        expect(
            isinstance(self.kind, TypeKind),
            "ScalarType.kind must be a TypeKind",
            error=ScalarTypeError,
        )
        expect(
            self.precision is None or self.precision > 0,
            "SQL type precision must be positive",
            error=ScalarTypeError,
        )
        expect(
            self.scale is None or self.scale >= 0,
            "SQL type scale must be nonnegative",
            error=ScalarTypeError,
        )
        expect(
            self.scale is None or self.precision is not None,
            "SQL type scale requires precision",
            error=ScalarTypeError,
        )
        expect(
            self.precision is None
            or self.scale is None
            or self.scale <= self.precision,
            "SQL type scale cannot exceed precision",
            error=ScalarTypeError,
        )


BOOLEAN = ScalarType(TypeKind.BOOLEAN)
INTEGER = ScalarType(TypeKind.INTEGER)
FLOAT = ScalarType(TypeKind.FLOAT)
# This project models DECIMAL as a bounded real-valued domain. Bare SQL
# NUMERIC/DECIMAL uses this conventional finite shape.
DECIMAL = ScalarType(TypeKind.DECIMAL, 38, 18)
STRING = ScalarType(TypeKind.STRING)

DATE = ScalarType(TypeKind.DATE)
TIME = ScalarType(TypeKind.TIME)
TIMESTAMP = ScalarType(TypeKind.TIMESTAMP)
INTERVAL = ScalarType(TypeKind.INTERVAL)


@dataclass(frozen=True, slots=True)
class IntervalValue:
    """Canonical SQL interval with calendar months kept distinct."""

    months: int = 0
    days: int = 0
    microseconds: int = 0

    @classmethod
    def from_unit(cls, value: Decimal, unit: str) -> IntervalValue:
        normalized = unit.casefold().removesuffix("s")
        if normalized in {"year", "month"}:
            months = value * (12 if normalized == "year" else 1)
            if months != months.to_integral_value():
                raise ValueError("Month-based intervals require an integral value")
            return cls(months=int(months))

        micros_per_unit = {
            "week": 7 * 24 * 60 * 60 * 1_000_000,
            "day": 24 * 60 * 60 * 1_000_000,
            "hour": 60 * 60 * 1_000_000,
            "minute": 60 * 1_000_000,
            "second": 1_000_000,
            "millisecond": 1_000,
            "microsecond": 1,
        }
        try:
            total_micros = value * micros_per_unit[normalized]
        except KeyError as error:
            raise ValueError(f"Unsupported interval unit: {unit}") from error
        if total_micros != total_micros.to_integral_value():
            raise ValueError("Interval has sub-microsecond precision")
        days, microseconds = divmod(
            int(total_micros), 24 * 60 * 60 * 1_000_000
        )
        return cls(days=days, microseconds=microseconds)

    def __add__(self, other: IntervalValue) -> IntervalValue:
        if not isinstance(other, IntervalValue):
            return NotImplemented
        return IntervalValue(
            self.months + other.months,
            self.days + other.days,
            self.microseconds + other.microseconds,
        )


def parse_interval_value(value: str) -> IntervalValue:
    """Parse unit-bearing SQL interval text into its canonical value."""

    parts = re.findall(
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*([A-Za-z]+)",
        value,
    )
    compact = "".join(f"{number}{unit}" for number, unit in parts).casefold()
    if not parts or compact != re.sub(r"\s+", "", value).casefold():
        raise ValueError("Malformed interval literal")
    result = IntervalValue()
    for number, unit in parts:
        result += IntervalValue.from_unit(Decimal(number), unit)
    return result


def parse_iso_temporal_value(
    text: str, sql_type: ScalarType
) -> date | datetime | time:
    match = re.match(
        r"^(\d{4})-(\d{1,2})-(\d{1,2})(.*)$",
        text,
    )
    if match is not None:
        year, month, day, suffix = match.groups()
        text = f"{year}-{int(month):02d}-{int(day):02d}{suffix}"
    if sql_type.kind is TypeKind.DATE:
        return date.fromisoformat(text)
    if sql_type.kind is TypeKind.TIME:
        return time.fromisoformat(text)
    if sql_type.kind is TypeKind.TIMESTAMP:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    raise ScalarTypeError(f"Expected a temporal SQL type, got {sql_type!r}")


class Sort:
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class ScalarSort(Sort):
    sql_type: ScalarType
    nullable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.sql_type, ScalarType):
            raise TypeError("ScalarSort.sql_type must be a ScalarType")
        if not isinstance(self.nullable, bool):
            raise TypeError("ScalarSort.nullable must be a bool")


@dataclass(frozen=True, slots=True)
class PredicateSort(Sort):
    """SQL three-valued predicate domain."""


@dataclass(frozen=True, slots=True)
class RowSort(Sort):
    schema: SchemaId


@dataclass(frozen=True, slots=True)
class MultiplicitySort(Sort):
    """Natural-number bag multiplicities."""


@dataclass(frozen=True, slots=True)
class BagSort(Sort):
    schema: SchemaId


@dataclass(frozen=True, slots=True)
class SeqSort(Sort):
    schema: SchemaId


@dataclass(frozen=True, slots=True)
class AggStateSort(Sort):
    aggregate: AggregateSpecId


@dataclass(frozen=True, slots=True)
class RowFunctionSort(Sort):
    input: RowSort
    result: Sort


PREDICATE = PredicateSort()
MULTIPLICITY = MultiplicitySort()
RelationSort: TypeAlias = BagSort | SeqSort


def is_relation_sort(sort: object) -> TypeGuard[RelationSort]:
    return isinstance(sort, (BagSort, SeqSort))


def relation_schema(sort: Sort) -> SchemaId:
    if isinstance(sort, (BagSort, SeqSort)):
        return sort.schema
    raise TypeError(f"Expected relation sort, got {sort!r}")
