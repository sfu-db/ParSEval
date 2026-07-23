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
    from .smt_solver import Z3SmtSession


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


def declare_column(variable: exp.Expression, z3ctx: Optional[z3.Context] = None) -> SMTValue:
    from .types import SolverVar

    if isinstance(variable, SolverVar):
        dtype = variable.dtype
        var_name = variable.var_key
    else:
        dtype = getattr(variable, "type", None) or DataType.build("UNKNOWN")
        var_name = f"{variable.table}.{variable.name}"
    typeinfo = normalize_dtype(dtype, z3ctx)
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
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


def _zfill4(expr: z3.ExprRef, z3ctx: Optional[z3.Context] = None) -> z3.ExprRef:
    return z3.If(
        expr < 10,
        z3.Concat(z3.StringVal("000", ctx=z3ctx), z3.IntToStr(expr)),
        z3.If(
            expr < 100,
            z3.Concat(z3.StringVal("00", ctx=z3ctx), z3.IntToStr(expr)),
            z3.If(
                expr < 1000,
                z3.Concat(z3.StringVal("0", ctx=z3ctx), z3.IntToStr(expr)),
                z3.IntToStr(expr),
            ),
        ),
    )


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
    solver: Z3SmtSession, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    """Translate ``ABS(x)`` as ``If(x >= 0, x, -x)``."""
    arg = solver._as_value(args[0])
    return solver._nullable_unary(
        arg,
        lambda raw: z3.If(raw >= 0, raw, -raw),
        arg.typeinfo.dtype,
    )


def _translate_length(
    solver: Z3SmtSession, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    """Translate ``LENGTH(s)`` as the Z3 ``Length`` string function."""
    arg = solver._as_value(args[0])
    return solver._nullable_unary(arg, lambda raw: z3.Length(raw), DataType.build("INT"))


def _translate_substr(
    solver: Z3SmtSession, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    """Translate ``SUBSTR(s, start[, length])`` into Z3 ``SubString``."""
    source = solver._coerce_value_to_type(
        solver._as_value(args[0]),
        DataType.build("TEXT"),
    )
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
        # Enforce that the source string is long enough for the SUBSTR to
        # extract the requested number of characters. Without this, the solver
        # may generate short strings that satisfy the constraint in Z3 but
        # fail when evaluated by the database engine.
        length_val = _value_payload(length)
        min_len = z3.If(raw_start >= 1, raw_start, z3.IntVal(0, ctx=solver.z3ctx))
        solver.add(
            z3.Implies(
                z3.And(_value_some(source), _value_some(start), _value_some(length)),
                source_len >= min_len + length_val,
            ),
        )
        body = z3.SubString(raw_source, start_payload, length_val)
        some = z3.And(_value_some(source), _value_some(start), _value_some(length))
    return SMTValue(z3.If(some, option_sort.Some(body), option_sort.NULL), result_type)


def _translate_instr(
    solver: Z3SmtSession, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
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


def _ymd_hms_from_temporal(solver: Z3SmtSession, value: SMTValue):
    """Decompose a Z3 temporal payload into (year, month, day, hour, minute, second).

    Uses Hinnant's algorithm for exact (year, month, day) decomposition
    from epoch days. Time-of-day components use integer division on
    epoch seconds.
    """
    if value.typeinfo.family not in {"date", "time", "datetime", "timestamp"}:
        raise UnsupportedSMTError(
            f"Cannot decompose non-temporal value {value.typeinfo.logical_name} "
            f"(family={value.typeinfo.family})"
        )
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
    if value.typeinfo.family == "time":
        return None, None, None, hour, minute, second
    days_since_epoch = ts / 86400
    # Hinnant's algorithm: convert epoch days to (year, month, day) using
    # integer arithmetic. All divisions are Z3 integer division. The
    # returned y is the March-based year; add 1 for Jan/Feb to recover
    # the civil year.
    z = days_since_epoch + 719468
    era = z / 146097
    doe = z - era * 146097
    yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe / 4 - yoe / 100)
    mp = (5 * doy + 2) / 153
    d = doy - (153 * mp + 2) / 5 + 1
    m = mp + 3 - 12 * (mp / 10)
    y = y + z3.If(m <= 2, 1, 0)
    return y, m, d, hour, minute, second


def _format_temporal_payload(
    solver: Z3SmtSession, temporal: SMTValue, fmt_value: str
) -> z3.ExprRef:
    """Build a Z3 string for a concrete temporal format string."""
    year, month, day, hour, minute, second = _ymd_hms_from_temporal(solver, temporal)
    components = {
        "Y": year,
        "m": month,
        "d": day,
        "H": hour,
        "M": minute,
        "S": second,
    }
    renderers = {
        "Y": lambda value: _zfill4(value, solver.z3ctx),
        "m": lambda value: _zfill2(value, solver.z3ctx),
        "d": lambda value: _zfill2(value, solver.z3ctx),
        "H": lambda value: _zfill2(value, solver.z3ctx),
        "M": lambda value: _zfill2(value, solver.z3ctx),
        "S": lambda value: _zfill2(value, solver.z3ctx),
    }
    parts: List[z3.ExprRef] = []
    i = 0
    while i < len(fmt_value):
        ch = fmt_value[i]
        if ch == "%":
            if i + 1 >= len(fmt_value):
                raise UnsupportedSMTError(f"Unsupported STRFTIME format: {fmt_value}")
            token = fmt_value[i + 1]
            if token == "%":
                parts.append(z3.StringVal("%", ctx=solver.z3ctx))
            elif token in components:
                component = components[token]
                if component is None:
                    raise UnsupportedSMTError(
                        f"Unsupported STRFTIME token %{token} for {temporal.typeinfo.logical_name}"
                    )
                parts.append(renderers[token](component))
            else:
                raise UnsupportedSMTError(f"Unsupported STRFTIME token: %{token}")
            i += 2
            continue
        literal_start = i
        while i < len(fmt_value) and fmt_value[i] != "%":
            i += 1
        parts.append(z3.StringVal(fmt_value[literal_start:i], ctx=solver.z3ctx))
    if not parts:
        return z3.StringVal("", ctx=solver.z3ctx)
    body = parts[0]
    for part in parts[1:]:
        body = z3.Concat(body, part)
    return body


def _default_temporal_format(family: str) -> str:
    if family == "date":
        return "%Y-%m-%d"
    if family == "time":
        return "%H:%M:%S"
    return "%Y-%m-%d %H:%M:%S"


def format_temporal_value(
    solver: Z3SmtSession, temporal: SMTValue, fmt_value: Optional[str] = None
) -> SMTValue:
    """Format a temporal SMT value as SQL-facing TEXT."""
    if temporal.typeinfo.family not in {"date", "time", "datetime", "timestamp"}:
        raise UnsupportedSMTError(
            f"Cannot format non-temporal value {temporal.typeinfo.logical_name}"
        )
    fmt = fmt_value if fmt_value is not None else _default_temporal_format(temporal.typeinfo.family)
    body = _format_temporal_payload(solver, temporal, fmt)
    result_type = normalize_dtype(DataType.build("TEXT"), solver.z3ctx)
    option_sort = OptionTypeRegistry.get(result_type.payload_sort, solver.z3ctx)
    return SMTValue(
        z3.If(_value_some(temporal), option_sort.Some(body), option_sort.NULL),
        result_type,
    )


def _translate_strftime(
    solver: Z3SmtSession, expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    """Translate ``STRFTIME(fmt, temporal)`` into Z3 string construction.

    Supports concrete format strings composed from ``%Y``, ``%m``, ``%d``,
    ``%H``, ``%M``, ``%S``, ``%%``, and literal characters.
    """
    from .normalization import unwrap_planning_temporal_arg

    fmt = solver._as_value(args[0])
    temporal_node: Optional[exp.Expression] = None
    if isinstance(expression, exp.TimeToStr):
        temporal_node = expression.this
    elif isinstance(expression, exp.Anonymous) and len(list(expression.expressions)) >= 2:
        temporal_node = list(expression.expressions)[1]
    if temporal_node is not None:
        unwrapped = unwrap_planning_temporal_arg(temporal_node)
        if unwrapped is not temporal_node:
            temporal = solver._as_value(solver._to_z3_expr(unwrapped))
        else:
            temporal = solver._as_value(args[1])
    else:
        temporal = solver._as_value(args[1])
    if temporal.typeinfo.family not in {"date", "time", "datetime", "timestamp"}:
        raise UnsupportedSMTError(
            f"STRFTIME requires a temporal argument, got {temporal.typeinfo.logical_name}"
        )
    fmt_expr = z3.simplify(_value_payload(fmt))
    if not z3.is_string_value(fmt_expr):
        raise UnsupportedSMTError("STRFTIME requires a concrete format string")
    body = _format_temporal_payload(solver, temporal, fmt_expr.as_string())
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
# Temporal extractors: YEAR / MONTH / DAY (and the portable EXTRACT form)
# ---------------------------------------------------------------------------


_EXTRACT_UNITS = {
    "YEAR": "year",
    "MONTH": "month",
    "DAY": "day",
    "DAYOFMONTH": "day",
    "DAY_OF_MONTH": "day",
    "HOUR": "hour",
    "MINUTE": "minute",
    "SECOND": "second",
}


def _wrap_optional(
    value: z3.ArithRef, guard: z3.BoolRef,
    typeinfo: SMTTypeInfo, z3ctx,
) -> "SMTValue":
    """Wrap a Z3 expression in an Option type, gated on a None-propagation guard.

    The output SMTValue is ``Some(value)`` when ``guard`` holds, else ``NULL``.
    Used by the temporal extractors (result type is INT) and by the
    temporal manipulators (result type matches the source column).
    """
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
    return SMTValue(
        z3.If(guard, option_sort.Some(value), option_sort.NULL),
        typeinfo,
    )


def _int_typeinfo(z3ctx) -> SMTTypeInfo:
    """Standard INT typeinfo for temporal extractor results."""
    return normalize_dtype(DataType.build("INT"), z3ctx)


def _translate_temporal_extractor(
    solver: Z3SmtSession, expression: exp.Expression, args, *,
    component: str,
) -> SMTValue:
    """Shared translator for ``YEAR/MONTH/DAY/EXTRACT(unit FROM col)``.

    The Hinnant decomposition in :func:`_ymd_hms_from_temporal` already
    produces ``(year, month, day, hour, minute, second)`` as Z3 int
    expressions; we just pick the right component.
    """
    if len(args) != 1:
        raise UnsupportedSMTError(
            f"{type(expression).__name__} expects exactly one argument"
        )
    temporal = solver._as_value(args[0])
    year, month, day, hour, minute, second = _ymd_hms_from_temporal(solver, temporal)
    components = {
        "year": year, "month": month, "day": day,
        "hour": hour, "minute": minute, "second": second,
    }
    return _wrap_optional(
        components[component], _value_some(temporal),
        _int_typeinfo(solver.z3ctx), solver.z3ctx,
    )


def _temporal_extractor(component: str):
    """Build a translator that picks a single component from the Hinnant decomp."""
    def _fn(solver, expression, args):
        return _translate_temporal_extractor(
            solver, expression, args, component=component,
        )
    _fn.__name__ = f"_translate_temporal_extractor_{component}"
    return _fn


def _translate_extract(solver, expression, args):
    """Translate ``EXTRACT(unit FROM col)`` (PostgreSQL / MySQL 8+)."""
    if len(args) != 1:
        raise UnsupportedSMTError("EXTRACT expects exactly one argument")
    unit_node = expression.this
    unit_text = None
    if isinstance(unit_node, exp.Var):
        unit_text = unit_node.name.upper()
    elif isinstance(unit_node, exp.Identifier):
        unit_text = unit_node.name.upper()
    elif isinstance(unit_node, exp.Column):
        unit_text = unit_node.name.upper()
    elif isinstance(unit_node, exp.Literal):
        unit_text = str(unit_node.this).upper()
    if unit_text is None or unit_text not in _EXTRACT_UNITS:
        raise UnsupportedSMTError(f"Unsupported EXTRACT unit: {unit_text!r}")
    return _translate_temporal_extractor(
        solver, expression, args, component=_EXTRACT_UNITS[unit_text],
    )


# ---------------------------------------------------------------------------
# Temporal manipulators: DATE_ADD / DATE_SUB / DATEDIFF / TIMESTAMPDIFF
# ---------------------------------------------------------------------------


_INTERVAL_UNITS = {
    "DAY": 86400, "HOUR": 3600, "MINUTE": 60, "SECOND": 1,
    "WEEK": 7 * 86400,
}


def _interval_to_seconds(value: "SMTValue") -> int:
    """Resolve a fixed-length interval unit to its seconds count.

    Reads the interval magnitude from the *Z3* payload (an int constant
    produced by the literal translator), not from the original sqlglot
    expression.
    """
    if not isinstance(value, SMTValue):
        raise UnsupportedSMTError("Interval length must be a literal integer")
    raw = _value_payload(value)
    simplified = z3.simplify(raw)
    if not z3.is_int_value(simplified):
        raise UnsupportedSMTError("Interval length must be an integer constant")
    return simplified.as_long()


def _shift_temporal(temporal: SMTValue, seconds: int, z3ctx):
    """Add ``seconds`` to a temporal payload (None-propagating)."""
    raw = _value_payload(temporal)
    if temporal.typeinfo.family == "date":
        new_raw = raw + seconds // 86400
    elif temporal.typeinfo.family == "time":
        new_raw = raw + seconds
    else:
        new_raw = raw + seconds
    return _wrap_optional(new_raw, _value_some(temporal), temporal.typeinfo, z3ctx)


def _translate_date_shift(solver, expression, args, sign: int):
    """Translate ``DATE_ADD``/``DATE_SUB(col, INTERVAL n unit)`` (MySQL).

    ``sign = +1`` for DATE_ADD, ``-1`` for DATE_SUB.  Otherwise identical.
    """
    if len(args) != 2:
        raise UnsupportedSMTError(
            f"{type(expression).__name__} expects (date, interval)"
        )
    col_val = solver._as_value(args[0])
    interval_val = solver._as_value(args[1])
    unit = _unit_name(expression).upper()
    if unit not in _INTERVAL_UNITS:
        raise UnsupportedSMTError(f"Unsupported unit: {unit!r}")
    n = _interval_to_seconds(interval_val)
    return _shift_temporal(col_val, sign * n * _INTERVAL_UNITS[unit], solver.z3ctx)


def _translate_date_add(solver, expression, args):
    return _translate_date_shift(solver, expression, args, sign=+1)


def _translate_date_sub(solver, expression, args):
    return _translate_date_shift(solver, expression, args, sign=-1)


def _translate_date_diff(solver, expression, args, *, default_unit: Optional[str] = None):
    """Translate ``DATEDIFF``/``TIMESTAMPDIFF(unit, d1, d2)`` (MySQL).

    ``DATEDIFF(d1, d2)`` is shorthand for ``TIMESTAMPDIFF(DAY, d1, d2)``;
    the AST lacks a ``unit`` arg, so we fall back to ``default_unit``.
    """
    if len(args) != 2:
        raise UnsupportedSMTError(
            f"{type(expression).__name__} expects (d1, d2)"
        )
    d1 = solver._as_value(args[0])
    d2 = solver._as_value(args[1])
    unit = _unit_name(expression).upper() or (default_unit or "").upper()
    if unit not in _INTERVAL_UNITS:
        raise UnsupportedSMTError(f"Unsupported unit: {unit!r}")
    seconds_per_unit = _INTERVAL_UNITS[unit]
    p1 = _value_payload(d1)
    p2 = _value_payload(d2)
    if d1.typeinfo.family == "date" and d2.typeinfo.family == "date":
        raw_diff = (p1 - p2) * 86400
    else:
        try:
            raw_diff = p1 - p2
        except (TypeError, z3.Z3Exception) as exc:
            raise UnsupportedSMTError(
                f"unsupported_date_diff: {d1.typeinfo.family}, {d2.typeinfo.family}"
            ) from exc
    diff = raw_diff / seconds_per_unit
    guard = z3.And(_value_some(d1), _value_some(d2))
    return _wrap_optional(
        diff, guard, _int_typeinfo(solver.z3ctx), solver.z3ctx,
    )


def _translate_datediff(solver, expression, args):
    return _translate_date_diff(solver, expression, args, default_unit="DAY")


def _translate_timestampdiff(solver, expression, args):
    return _translate_date_diff(solver, expression, args)


def _unit_name(expression: exp.Expression) -> str:
    """Pull the unit name out of a DateAdd/DateSub/TimestampDiff node."""
    unit = expression.args.get("unit")
    if unit is None:
        return ""
    if isinstance(unit, exp.Var):
        return unit.name
    if isinstance(unit, exp.Identifier):
        return unit.name
    if isinstance(unit, exp.Literal):
        return str(unit.this)
    return str(unit).upper()


# ---------------------------------------------------------------------------
# Register built-in special functions
# ---------------------------------------------------------------------------

register_special_function("ABS", _translate_abs, return_type=_return_same_type)
register_special_function("LENGTH", _translate_length, return_type=_return_int)
register_special_function("CHAR_LENGTH", _translate_length, return_type=_return_int)
register_special_function("SUBSTR", _translate_substr, return_type=_return_text)
register_special_function("INSTR", _translate_instr, return_type=_return_int)
register_special_function("STRFTIME", _translate_strftime, return_type=_return_text)
register_special_function("YEAR", _temporal_extractor("year"), return_type=_return_int)
register_special_function("MONTH", _temporal_extractor("month"), return_type=_return_int)
register_special_function("DAY", _temporal_extractor("day"), return_type=_return_int)
register_special_function("DAYOFMONTH", _temporal_extractor("day"), return_type=_return_int)
register_special_function("HOUR", _temporal_extractor("hour"), return_type=_return_int)
register_special_function("MINUTE", _temporal_extractor("minute"), return_type=_return_int)
register_special_function("SECOND", _temporal_extractor("second"), return_type=_return_int)
register_special_function("EXTRACT", _translate_extract, return_type=_return_int)
register_special_function("DATEADD", _translate_date_add, return_type=_return_same_type)
register_special_function("DATESUB", _translate_date_sub, return_type=_return_same_type)
register_special_function("DATEDIFF", _translate_datediff, return_type=_return_int)
register_special_function("TIMESTAMPDIFF", _translate_timestampdiff, return_type=_return_int)
