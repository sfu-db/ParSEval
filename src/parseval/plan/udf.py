"""DataFusion planning stubs and user scalar UDF registration.

:data:`PREDEFINED_PLANNING_UDFS` covers Bird (SQLite) / LeetCode (MySQL)
dialect gaps. :class:`~parseval.plan.session.DataFusionSessionManager`
registers them at construction; callers may add real scalar UDFs via
:func:`register_scalar_udf`.
"""

from __future__ import annotations

from typing import Callable, Sequence, Tuple

import pyarrow as pa
from datafusion import SessionContext, udf

# (name, arity, return_type). One arity per name — DataFusion keeps a single
# signature per UDF. Window builtins (rank/…) omitted so we do not shadow them.
# ``julianday`` returns float so ``JULIANDAY(a) - JULIANDAY(b)`` type-checks.
PREDEFINED_PLANNING_UDFS: Tuple[Tuple[str, int, pa.DataType], ...] = (
    # SQLite
    ("strftime", 2, pa.string()),
    ("julianday", 1, pa.float64()),
    ("total", 1, pa.float64()),
    ("iif", 3, pa.string()),
    ("datetime", 1, pa.string()),
    ("date", 1, pa.string()),
    ("time", 1, pa.string()),
    # MySQL date/time
    ("adddate", 2, pa.string()),
    ("subdate", 2, pa.string()),
    ("datediff", 2, pa.float64()),
    ("timestampdiff", 3, pa.float64()),
    ("str_to_date", 2, pa.string()),
    ("unix_timestamp", 1, pa.float64()),
    ("convert_tz", 3, pa.string()),
    ("year", 1, pa.float64()),
    ("month", 1, pa.float64()),
    ("day", 1, pa.float64()),
    ("hour", 1, pa.float64()),
    ("minute", 1, pa.float64()),
    ("second", 1, pa.float64()),
    ("week", 1, pa.float64()),
    ("quarter", 1, pa.float64()),
    ("dayofweek", 1, pa.float64()),
    ("dayofmonth", 1, pa.float64()),
    ("dayofyear", 1, pa.float64()),
    ("weekday", 1, pa.float64()),
    ("last_day", 1, pa.string()),
    ("from_days", 1, pa.string()),
    # MySQL string / numeric
    ("locate", 2, pa.float64()),  # INSTR emits as LOCATE under mysql
    ("format", 2, pa.string()),
    ("truncate", 2, pa.float64()),
    ("field", 4, pa.float64()),
)


def _planning_stub(arity: int, return_type: pa.DataType, *, name: str = ""):
    """Return a no-op Arrow kernel used only so DataFusion can plan."""
    if arity <= 0:

        def _nullary() -> pa.Array:
            return pa.array([None], type=return_type)

        return _nullary

    # strftime is often CASTed to REAL for year diffs; returning the last
    # argument (a timestamp) makes CAST fail during simplify_expressions.
    if name == "strftime" and pa.types.is_string(return_type):

        def _strftime_impl(*arrays: pa.Array) -> pa.Array:
            n = len(arrays[0]) if arrays else 1
            return pa.array(["0"] * n, type=pa.string())

        return _strftime_impl

    def _impl(*arrays: pa.Array) -> pa.Array:
        n = len(arrays[0]) if arrays else 1
        # Prefer a typed null batch so return_type matches the UDF signature
        # even when argument arrays are Utf8 (e.g. julianday → float64).
        if not pa.types.is_string(return_type) and not pa.types.is_large_string(
            return_type
        ):
            return pa.array([None] * n, type=return_type)
        return arrays[-1]

    return _impl


def register_predefined_udfs(ctx: SessionContext) -> None:
    """Register :data:`PREDEFINED_PLANNING_UDFS` as planning stubs."""
    for name, arity, return_type in PREDEFINED_PLANNING_UDFS:
        arity = max(arity, 0)
        impl = _planning_stub(arity, return_type, name=name)
        # Args stay Utf8: Bird/LeetCode often pass text dates/numbers.
        arg_types = [pa.string()] * arity
        try:
            ctx.register_udf(
                udf(impl, arg_types, return_type, "immutable", name=name)
            )
        except Exception:  # noqa: BLE001 — skip names DF already provides
            continue


def register_scalar_udf(
    ctx: SessionContext,
    name: str,
    func: Callable,
    input_types: Sequence[pa.DataType],
    return_type: pa.DataType,
    *,
    volatility: str = "immutable",
) -> None:
    """Compile and register a user scalar UDF on ``ctx``."""
    ctx.register_udf(
        udf(func, list(input_types), return_type, volatility, name=name)
    )
