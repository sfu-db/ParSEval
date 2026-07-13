"""Dialect-aware SMT coercion policy (Z3 substrate)."""

from __future__ import annotations

from typing import Any, Optional, Protocol, Tuple

import z3
from sqlglot import exp
from sqlglot.expressions import DataType

from parseval.coercion import CoercionError, coerce_literal_value
from parseval.dtype import StorageLiteral
from parseval.literals import literal_value
from parseval.solver.smt_types import (
    SMTTypeInfo,
    SMTValue,
    UnsupportedSMTError,
    encode_literal,
    normalize_dtype,
)
from parseval.solver.smt_translate import (
    _null_value,
    _value_payload,
    format_temporal_value,
)
from parseval.solver.types import SolverVar

_NUMERIC_FAMILIES = frozenset({"int", "real"})
_TEMPORAL_FAMILIES = frozenset({"date", "time", "datetime", "timestamp"})


class _SmtCoercionSession(Protocol):
    z3ctx: Any

    def _wrap_payload(self, payload: z3.ExprRef, dtype: DataType) -> SMTValue: ...

    def _wrap_nullable_payload(
        self, source: SMTValue, payload: z3.ExprRef, dtype: DataType
    ) -> SMTValue: ...

    def _encode_temporal_literal(
        self, s: str, target: SMTTypeInfo
    ) -> Optional[SMTValue]: ...


def coerce_comparison_pair(
    left: SMTValue,
    right: SMTValue,
    left_expr: exp.Expression,
    right_expr: exp.Expression,
    dialect: str | None,
    ctx: _SmtCoercionSession,
) -> Tuple[SMTValue, SMTValue]:
    """Coerce a comparison pair to matching SMT families when dialect allows."""
    left_family = left.typeinfo.family
    right_family = right.typeinfo.family

    if left_family == right_family:
        return left, right

    if left_family in _NUMERIC_FAMILIES and right_family in _NUMERIC_FAMILIES:
        return left, right

    temporal = _coerce_temporal_pair(left, right, left_expr, right_expr, ctx)
    if temporal is not None:
        return temporal

    if isinstance(left_expr, SolverVar) and _is_literal(right_expr):
        return left, _encode_literal_for_target(right_expr, left, dialect, ctx)
    if isinstance(right_expr, SolverVar) and _is_literal(left_expr):
        return _encode_literal_for_target(left_expr, right, dialect, ctx), right

    raise UnsupportedSMTError(
        f"unsupported_mixed_sort_comparison:{left_family}:{right_family}"
    )


def coerce_smt_value(
    value: SMTValue,
    target_dtype: DataType,
    session: _SmtCoercionSession,
) -> SMTValue:
    """Coerce an SMTValue to a target SQL dtype inside Z3."""
    target_type = normalize_dtype(target_dtype, session.z3ctx)
    if value.typeinfo.family == target_type.family:
        return SMTValue(value.expr, target_type, value.is_null_literal)
    if value.is_null_literal:
        return _null_value(target_type, session.z3ctx)

    raw = _value_payload(value)
    src = value.typeinfo.family
    dst = target_type.family

    if dst == "real" and src == "int":
        return session._wrap_nullable_payload(value, z3.ToReal(raw), target_type.dtype)
    if dst == "int" and src == "real":
        return session._wrap_nullable_payload(value, z3.ToInt(raw), target_type.dtype)

    if src in _TEMPORAL_FAMILIES and dst in _TEMPORAL_FAMILIES:
        if src == "date" and dst in {"datetime", "timestamp"}:
            return session._wrap_nullable_payload(
                value, raw * 86400, target_type.dtype
            )
        if src in {"datetime", "timestamp"} and dst == "date":
            return session._wrap_nullable_payload(
                value, raw / 86400, target_type.dtype
            )
        if src == "time" and dst in {"datetime", "timestamp"}:
            return session._wrap_nullable_payload(value, raw, target_type.dtype)
        if src in {"datetime", "timestamp"} and dst == "time":
            return session._wrap_nullable_payload(
                value, raw % 86400, target_type.dtype
            )

    if dst == "text":
        if src in _TEMPORAL_FAMILIES:
            return format_temporal_value(session, value)  # type: ignore[arg-type]
        if src == "int":
            return session._wrap_nullable_payload(
                value, z3.IntToStr(raw), target_type.dtype
            )
        if src == "bool":
            return session._wrap_nullable_payload(
                value,
                z3.If(
                    raw,
                    z3.StringVal("TRUE", ctx=session.z3ctx),
                    z3.StringVal("FALSE", ctx=session.z3ctx),
                ),
                target_type.dtype,
            )
        if src == "real":
            raise UnsupportedSMTError(
                f"Unsupported conversion from {value.typeinfo.logical_name} to {target_type.logical_name}"
            )

    if dst == "int" and src == "text":
        return session._wrap_nullable_payload(
            value, z3.StrToInt(raw), target_type.dtype
        )

    raise UnsupportedSMTError(
        f"Unsupported conversion from {value.typeinfo.logical_name} to {target_type.logical_name}"
    )


def _is_literal(node: exp.Expression) -> bool:
    return isinstance(node, exp.Literal) or node.key == "const"


def _coerce_temporal_pair(
    left: SMTValue,
    right: SMTValue,
    left_expr: exp.Expression,
    right_expr: exp.Expression,
    ctx: _SmtCoercionSession,
) -> Optional[Tuple[SMTValue, SMTValue]]:
    if (
        left.typeinfo.family in _TEMPORAL_FAMILIES
        and isinstance(right_expr, exp.Literal)
        and right_expr.is_string
    ):
        coerced = ctx._encode_temporal_literal(str(right_expr.this), left.typeinfo)
        if coerced is not None:
            return left, coerced
    if (
        right.typeinfo.family in _TEMPORAL_FAMILIES
        and isinstance(left_expr, exp.Literal)
        and left_expr.is_string
    ):
        coerced = ctx._encode_temporal_literal(str(left_expr.this), right.typeinfo)
        if coerced is not None:
            return coerced, right
    return None


def _encode_literal_for_target(
    literal_expr: exp.Expression,
    target: SMTValue,
    dialect: str | None,
    ctx: _SmtCoercionSession,
) -> SMTValue:
    try:
        raw = literal_value(literal_expr)
        coerced = coerce_literal_value(
            raw, target.typeinfo.dtype, dialect, for_equality=False
        )
    except CoercionError as exc:
        raise UnsupportedSMTError(str(exc)) from exc
    if isinstance(coerced, StorageLiteral):
        raise UnsupportedSMTError("unsupported_storage_literal_in_smt")
    return encode_literal(target.typeinfo.dtype, coerced, ctx.z3ctx)


__all__ = [
    "coerce_comparison_pair",
    "coerce_smt_value",
]
