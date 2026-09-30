"""Nullable SQL values and their Z3 representation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import TypeAlias

import z3

from parseval.terms.context import Context
from parseval.terms.names import SchemaId
from parseval.terms.sorts import ScalarType, TypeKind
from parseval.uexpr.evaluate import BagEntry

TRUE = z3.IntVal(1)
FALSE = z3.IntVal(0)
UNKNOWN = z3.IntVal(-1)


def _sum(*values) -> z3.ArithRef:
    """Keep empty sums in the Z3 carrier (z3.Sum() returns a Python int)."""
    return z3.Sum(z3.IntVal(0), *values)


class UnsupportedEncodingError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SymbolicValue:
    value: z3.ExprRef
    is_null: z3.BoolRef
    sql_type: ScalarType


@dataclass(frozen=True, slots=True)
class SymbolicRow:
    schema: SchemaId
    values: tuple[SymbolicValue, ...]


SymbolicEntry: TypeAlias = BagEntry[SymbolicRow, z3.ArithRef]


def _z3_sort(sql_type: ScalarType) -> z3.SortRef:
    if sql_type.kind is TypeKind.BOOLEAN:
        return z3.BoolSort()
    if sql_type.kind in (TypeKind.INTEGER, TypeKind.DATE, TypeKind.TIME, TypeKind.TIMESTAMP):
        return z3.IntSort()
    if sql_type.kind in (TypeKind.FLOAT, TypeKind.DECIMAL):
        return z3.RealSort()
    if sql_type.kind is TypeKind.STRING:
        return z3.StringSort()
    raise UnsupportedEncodingError(f"type:{sql_type.kind.value}")


def _default(sql_type: ScalarType) -> z3.ExprRef:
    sort = _z3_sort(sql_type)
    if sort.kind() == z3.Z3_BOOL_SORT:
        return z3.BoolVal(False)
    if sort.kind() == z3.Z3_INT_SORT:
        return z3.IntVal(0)
    if sort.kind() == z3.Z3_REAL_SORT:
        return z3.RealVal(0)
    return z3.StringVal("")


def _lexical_placeholder(context: Context, schema: SchemaId) -> SymbolicRow:
    """Occupy a de Bruijn slot that an output constructor cannot reference."""
    return SymbolicRow(
        schema,
        tuple(
            SymbolicValue(
                _default(field.sql_type),
                z3.BoolVal(field.nullable),
                field.sql_type,
            )
            for field in context.schema(schema).fields
        ),
    )


def _literal(value, sql_type: ScalarType) -> z3.ExprRef:
    if sql_type.kind is TypeKind.BOOLEAN:
        return z3.BoolVal(value)
    if sql_type.kind is TypeKind.INTEGER:
        return z3.IntVal(value)
    if sql_type.kind in (TypeKind.FLOAT, TypeKind.DECIMAL):
        return z3.RealVal(str(value))
    if sql_type.kind is TypeKind.STRING:
        return z3.StringVal(value)
    if sql_type.kind is TypeKind.DATE:
        return z3.IntVal(value.toordinal())
    if sql_type.kind is TypeKind.TIME:
        return z3.IntVal(_time_micros(value))
    if sql_type.kind is TypeKind.TIMESTAMP:
        return z3.IntVal(_datetime_micros(value))
    raise UnsupportedEncodingError(f"literal:{sql_type.kind.value}")


def _value_equal(left: SymbolicValue, right: SymbolicValue) -> z3.BoolRef:
    return z3.And(z3.Not(left.is_null), z3.Not(right.is_null), left.value == right.value)


def _nullable_value_equal(left: SymbolicValue, right: SymbolicValue) -> z3.BoolRef:
    return z3.Or(
        z3.And(left.is_null, right.is_null),
        _value_equal(left, right),
    )


def _row_equal(left: SymbolicRow, right: SymbolicRow) -> z3.BoolRef:
    return z3.And(
        *(
            z3.Or(
                z3.And(a.is_null, b.is_null),
                z3.And(z3.Not(a.is_null), z3.Not(b.is_null), a.value == b.value),
            )
            for a, b in zip(left.values, right.values, strict=True)
        )
    )


def _rows_equal(
    left: tuple[SymbolicRow, ...],
    right: tuple[SymbolicRow, ...],
) -> z3.BoolRef:
    return z3.And(
        *(
            _row_equal(left_row, right_row)
            for left_row, right_row in zip(left, right, strict=True)
        )
    )


def _decode(model: z3.ModelRef, value: SymbolicValue):
    if z3.is_true(model.eval(value.is_null, model_completion=True)):
        return None
    expression = model.eval(value.value, model_completion=True)
    kind = value.sql_type.kind
    if kind is TypeKind.BOOLEAN:
        return z3.is_true(expression)
    if kind is TypeKind.INTEGER:
        return expression.as_long()
    if kind in (TypeKind.FLOAT, TypeKind.DECIMAL):
        decimal = Decimal(expression.as_decimal(40).removesuffix("?"))
        return float(decimal) if kind is TypeKind.FLOAT else decimal
    if kind is TypeKind.STRING:
        return expression.as_string()
    if kind is TypeKind.DATE:
        return date.fromordinal(expression.as_long())
    if kind is TypeKind.TIME:
        return _micros_time(expression.as_long())
    if kind is TypeKind.TIMESTAMP:
        return datetime.fromtimestamp(expression.as_long() / 1_000_000, tz=timezone.utc).replace(tzinfo=None)
    raise UnsupportedEncodingError(f"decode:{kind.value}")


def _cast(value: z3.ExprRef, source: str, target: str) -> z3.ExprRef:
    if source == target:
        return value
    if source == "integer" and target in {"float", "decimal"}:
        return z3.ToReal(value)
    if source in {"float", "decimal"} and target == "integer":
        return z3.ToInt(value)
    if source == "boolean" and target == "integer":
        return z3.If(value, 1, 0)
    if source == "integer" and target == "boolean":
        return value != 0
    if source == "integer" and target == "string":
        return z3.IntToStr(value)
    if source == "string" and target == "integer":
        return z3.StrToInt(value)
    raise UnsupportedEncodingError(f"cast:{source}-to-{target}")


def _like_regex(pattern: str, *, insensitive: bool = False) -> z3.ReRef:
    pieces: list[z3.ReRef] = []
    escaped = False
    def literal(character):
        if insensitive and character.isascii() and character.isalpha():
            return z3.Union(z3.Re(character.lower()), z3.Re(character.upper()))
        return z3.Re(character)
    all_character = z3.AllChar(z3.ReSort(z3.StringSort()))
    for character in pattern:
        if escaped:
            pieces.append(literal(character))
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "%":
            pieces.append(z3.Star(all_character))
        elif character == "_":
            pieces.append(all_character)
        else:
            pieces.append(literal(character))
    if not pieces:
        return z3.Re("")
    if len(pieces) == 1:
        return pieces[0]
    return z3.Concat(*pieces)


def _value_domain(value: SymbolicValue) -> z3.BoolRef | None:
    sql_type = value.sql_type
    if sql_type.kind is TypeKind.DATE:
        return z3.And(value.value >= date.min.toordinal(), value.value <= date.max.toordinal())
    if sql_type.kind is TypeKind.TIME:
        return z3.And(value.value >= 0, value.value < 24 * 60 * 60 * 1_000_000)
    if sql_type.kind is TypeKind.TIMESTAMP:
        lower = _datetime_micros(datetime.min)
        upper = _datetime_micros(datetime.max)
        return z3.And(value.value >= lower, value.value <= upper)
    if sql_type.kind is TypeKind.DECIMAL and sql_type.precision is not None:
        scale = sql_type.scale or 0
        factor = 10**scale
        magnitude = Decimal(10) ** (sql_type.precision - scale)
        return z3.And(
            value.value > z3.RealVal(str(-magnitude)),
            value.value < z3.RealVal(str(magnitude)),
            z3.ToReal(z3.ToInt(value.value * factor)) == value.value * factor,
        )
    return None


def _time_micros(value: time) -> int:
    return ((value.hour * 60 + value.minute) * 60 + value.second) * 1_000_000 + value.microsecond


def _datetime_micros(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=value.tzinfo)
    return int((value - epoch).total_seconds() * 1_000_000)


def _micros_time(value: int) -> time:
    seconds, microsecond = divmod(value, 1_000_000)
    minute, second = divmod(seconds, 60)
    hour, minute = divmod(minute, 60)
    return time(hour % 24, minute, second, microsecond)

