"""SQL expression -> Z3 translation layer."""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple, Union

import z3
from sqlglot import exp
from sqlglot.expressions import DataType

from .smt_types import (
    SMTTypeInfo,
    SMTValue,
    UnsupportedSMTError,
    encode_literal,
    normalize_dtype,
    OptionTypeRegistry,
    is_option_expr,
    option_of,
    unwrap_option,
    register_special_function,
)

if TYPE_CHECKING:
    from .smt_solver import SMTSolver


def _coerce_numeric_sort(expr: z3.ExprRef, target_sort: z3.SortRef) -> z3.ExprRef:
    """Coerce a Z3 expression to a target numeric sort, promoting int->real if needed."""
    if expr.sort() == target_sort:
        return expr
    if (
        target_sort.kind() == z3.Z3_REAL_SORT
        and expr.sort().kind() == z3.Z3_INT_SORT
    ):
        return z3.ToReal(expr)
    return expr


def _to_z3_sort(dtype: DataType, z3ctx: Optional[z3.Context] = None) -> z3.SortRef:
    """Get the Z3 payload sort for a given SQL DataType."""
    return normalize_dtype(dtype, z3ctx).payload_sort


def _to_z3val(dtype: DataType, value, z3ctx: Optional[z3.Context] = None) -> z3.ExprRef:
    return encode_literal(dtype, value, z3ctx).expr


def declare_column(variable: exp.Column, z3ctx: Optional[z3.Context] = None) -> SMTValue:
    dtype = getattr(variable, "type", None) or DataType.build("UNKNOWN")
    typeinfo = normalize_dtype(dtype, z3ctx)
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
    var_name = f"{variable.table}.{variable.name}"
    return SMTValue(z3.Const(var_name, option_sort), typeinfo)


def _value_some(value: SMTValue) -> z3.BoolRef:
    """Return a Z3 predicate that is True when the Option value is Some(...)."""
    if value.expr is None:
        return z3.BoolVal(False)
    return option_of(value.expr).is_Some(value.expr)


def _value_null(value: SMTValue) -> z3.BoolRef:
    """Return a Z3 predicate that is True when the Option value is NULL."""
    if value.expr is None:
        return z3.BoolVal(True)
    return option_of(value.expr).is_NULL(value.expr)


def _value_payload(value: SMTValue) -> z3.ExprRef:
    """Extract the inner payload from a non-NULL Option value."""
    if value.expr is None:
        raise RuntimeError("NULL literal does not have a payload")
    return unwrap_option(value.expr)


def _coerce_pair(left: SMTValue, right: SMTValue) -> Tuple[z3.ExprRef, z3.ExprRef, str]:
    """Coerce a pair of SMTValues to a common Z3 sort for comparison/arithmetic.

    If either side is a real, both are promoted to real. Otherwise they are
    left at their natural sort. Returns (left_payload, right_payload, family).
    """
    if left.typeinfo.family == "real" or right.typeinfo.family == "real":
        target_sort = z3.RealSort()
        return (
            _coerce_numeric_sort(_value_payload(left), target_sort),
            _coerce_numeric_sort(_value_payload(right), target_sort),
            "real",
        )
    return _value_payload(left), _value_payload(right), left.typeinfo.family


def _bool_value(expr: z3.BoolRef, z3ctx: Optional[z3.Context] = None) -> SMTValue:
    """Wrap a Z3 boolean expression into an SMTValue with BOOLEAN type info."""
    typeinfo = normalize_dtype(DataType.build("BOOLEAN"), z3ctx)
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
    return SMTValue(option_sort.Some(expr), typeinfo)


def _null_value(typeinfo: SMTTypeInfo, z3ctx: Optional[z3.Context] = None) -> SMTValue:
    """Create an SMTValue representing an explicit SQL NULL of the given type."""
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
    return SMTValue(option_sort.NULL, typeinfo, is_null_literal=True)


def _zfill2(expr: z3.ExprRef, z3ctx: Optional[z3.Context] = None) -> z3.ExprRef:
    return z3.If(expr < 10, z3.Concat(z3.StringVal("0", ctx=z3ctx), z3.IntToStr(expr)), z3.IntToStr(expr))


def like_to_z3(var: SMTValue, pattern: Union[SMTValue, str]) -> z3.BoolRef:
    """Translate a SQL LIKE expression into Z3 string constraints.

    Supports ``%`` (any sequence) and ``_`` (any single character) wildcards.
    If ``pattern`` is an SMTValue, it must resolve to a concrete string.
    """
    some_checks = [_value_some(var)]
    raw = _value_payload(var)
    parts: List[z3.ExprRef] = []
    constraints: List[z3.BoolRef] = []

    if isinstance(pattern, SMTValue):
        if pattern.is_null_literal:
            return z3.BoolVal(False)
        some_checks.append(_value_some(pattern))
        pattern_expr = z3.simplify(_value_payload(pattern))
        if z3.is_string_value(pattern_expr):
            pattern = pattern_expr.as_string()
        else:
            raise UnsupportedSMTError("LIKE currently requires a concrete string pattern")
    elif not isinstance(pattern, str):
        raise UnsupportedSMTError("LIKE currently requires a concrete string pattern")

    for i, ch in enumerate(pattern):
        if ch == "_":
            char_expr = z3.String(f"like_char_{i}")
            constraints.append(z3.Length(char_expr) == 1)
            parts.append(char_expr)
        elif ch == "%":
            tail = z3.String(f"like_tail_{i}")
            constraints.append(z3.Length(tail) >= 0)
            parts.append(tail)
        else:
            parts.append(z3.StringVal(ch))
    expr = parts[0] if parts else z3.StringVal("")
    for part in parts[1:]:
        expr = z3.Concat(expr, part)
    constraints.append(raw == expr)
    return z3.And(*some_checks, *constraints)


# ---------------------------------------------------------------------------
# Return-type policies for special functions
# ---------------------------------------------------------------------------


def _return_same_type(expression: exp.Expression, arg_types: Sequence[SMTTypeInfo]) -> DataType:
    """Return the same type as the first argument (passthrough type policy)."""
    del expression
    return arg_types[0].dtype


def _return_int(_expression: exp.Expression, _arg_types: Sequence[SMTTypeInfo]) -> DataType:
    """Return INT type regardless of argument types."""
    return DataType.build("INT")


def _return_text(_expression: exp.Expression, _arg_types: Sequence[SMTTypeInfo]) -> DataType:
    """Return TEXT type regardless of argument types."""
    return DataType.build("TEXT")


# ---------------------------------------------------------------------------
# Special function translators
# ---------------------------------------------------------------------------


def _translate_abs(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    """Translate ``ABS(x)`` as ``If(x >= 0, x, -x)``."""
    arg = solver._as_value(args[0])
    return solver._nullable_unary(
        arg,
        lambda raw: z3.If(raw >= 0, raw, -raw),
        arg.typeinfo.dtype,
    )


def _translate_length(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    """Translate ``LENGTH(s)`` as the Z3 ``Length`` string function."""
    arg = solver._as_value(args[0])
    return solver._nullable_unary(arg, lambda raw: z3.Length(raw), DataType.build("INT"))


def _translate_substr(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    """Translate ``SUBSTR(s, start[, length])`` into Z3 ``SubString``."""
    source = solver._as_value(args[0])
    start = solver._as_value(args[1])
    length = solver._as_value(args[2]) if len(args) > 2 else None
    result_type = normalize_dtype(DataType.build("TEXT"), solver.z3ctx)
    option_sort = OptionTypeRegistry.get(result_type.payload_sort, solver.z3ctx)
    raw_source = _value_payload(source)
    raw_start = _value_payload(start)
    source_len = z3.Length(raw_source)
    start_payload = z3.If(
        raw_start >= 1,
        raw_start - 1,
        z3.If(raw_start < 0, source_len + raw_start, 0),
    )
    start_payload = z3.If(start_payload >= 0, start_payload, 0)
    if length is None:
        body = z3.SubString(
            raw_source, start_payload, source_len
        )
        some = z3.And(_value_some(source), _value_some(start))
    else:
        body = z3.SubString(raw_source, start_payload, _value_payload(length))
        some = z3.And(_value_some(source), _value_some(start), _value_some(length))
    return SMTValue(z3.If(some, option_sort.Some(body), option_sort.NULL), result_type)


def _translate_instr(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    """Translate ``INSTR(haystack, needle)`` as ``IndexOf + 1`` (1-based)."""
    haystack = solver._as_value(args[0])
    needle = solver._as_value(args[1])
    result_type = normalize_dtype(DataType.build("INT"), solver.z3ctx)
    option_sort = OptionTypeRegistry.get(result_type.payload_sort, solver.z3ctx)
    index = z3.IndexOf(_value_payload(haystack), _value_payload(needle), z3.IntVal(0))
    one_based = z3.If(index >= 0, index + 1, 0)
    return SMTValue(
        z3.If(
            z3.And(_value_some(haystack), _value_some(needle)),
            option_sort.Some(one_based),
            option_sort.NULL,
        ),
        result_type,
    )


def _ymd_hms_from_temporal(solver: SMTSolver, value: SMTValue):
    """Decompose a Z3 temporal payload into (year, month, day, hour, minute, second).

    Uses epoch-day arithmetic for dates and epoch-second for datetimes.
    The year is estimated from the Gregorian average year length.
    """
    raw = _value_payload(value)
    if value.typeinfo.family == "date":
        ts = raw * 86400
    elif value.typeinfo.family == "time":
        ts = raw
    else:
        ts = raw
    second = ts % 60
    minute = (ts / 60) % 60
    hour = (ts / 3600) % 24
    days_since_epoch = ts / 86400
    # Use the Gregorian average year length for a closer symbolic year estimate.
    year_offset = (days_since_epoch * 400) / 146097
    year = 1970 + year_offset
    day_of_year = days_since_epoch - ((year_offset * 146097) / 400)
    month = (day_of_year / 30) + 1
    day = (day_of_year % 30) + 1
    return year, month, day, hour, minute, second


def _translate_strftime(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    """Translate ``STRFTIME(fmt, temporal)`` into Z3 string construction.

    Supported format specifiers: ``%Y``, ``%m``, ``%d``, ``%Y-%m-%d``,
    ``%H``, ``%M``, ``%S``.
    """
    fmt = solver._as_value(args[0])
    temporal = solver._as_value(args[1])
    fmt_expr = z3.simplify(_value_payload(fmt))
    if not z3.is_string_value(fmt_expr):
        raise UnsupportedSMTError("STRFTIME requires a concrete format string")
    fmt_value = fmt_expr.as_string()
    year, month, day, hour, minute, second = _ymd_hms_from_temporal(solver, temporal)
    if fmt_value == "%Y":
        body = z3.IntToStr(year)
    elif fmt_value == "%m":
        body = _zfill2(month, solver.z3ctx)
    elif fmt_value == "%d":
        body = _zfill2(day, solver.z3ctx)
    elif fmt_value == "%Y-%m-%d":
        body = z3.Concat(
            z3.IntToStr(year),
            z3.StringVal("-"),
            _zfill2(month, solver.z3ctx),
            z3.StringVal("-"),
            _zfill2(day, solver.z3ctx),
        )
    elif fmt_value == "%H":
        body = _zfill2(hour, solver.z3ctx)
    elif fmt_value == "%M":
        body = _zfill2(minute, solver.z3ctx)
    elif fmt_value == "%S":
        body = _zfill2(second, solver.z3ctx)
    else:
        raise UnsupportedSMTError(f"Unsupported STRFTIME format: {fmt_value}")
    result_type = normalize_dtype(DataType.build("TEXT"), solver.z3ctx)
    option_sort = OptionTypeRegistry.get(result_type.payload_sort, solver.z3ctx)
    return SMTValue(
        z3.If(
            z3.And(_value_some(fmt), _value_some(temporal)),
            option_sort.Some(body),
            option_sort.NULL,
        ),
        result_type,
    )


# ---------------------------------------------------------------------------
# Register built-in special functions
# ---------------------------------------------------------------------------

register_special_function("ABS", _translate_abs, return_type=_return_same_type)
register_special_function("LENGTH", _translate_length, return_type=_return_int)
register_special_function("SUBSTR", _translate_substr, return_type=_return_text)
register_special_function("INSTR", _translate_instr, return_type=_return_int)
register_special_function("STRFTIME", _translate_strftime, return_type=_return_text)
