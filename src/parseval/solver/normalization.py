"""Constraint normalization before domain/SMT solving.

The public solver accepts SQL AST predicates. Some common SQL temporal
projections are monotone and can be converted to direct column bounds before
choosing between the domain solver and SMT. This module owns those rewrites so
``unified.py`` can stay focused on solver orchestration.
"""
from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import date as _date, datetime as _datetime, timedelta
from typing import Any, List, Optional, Tuple

from sqlglot import exp

from parseval.dtype import DataType

from .types import Problem, SolverVar, node_dtype


def unwrap_planning_temporal_arg(node: exp.Expression) -> exp.Expression:
    """Peel planning wrappers (CAST / ts-or-ds coercions) around a temporal arg.

    DataFusion's Utf8 ``strftime`` stub inserts ``CAST(... AS TEXT)`` around
    DATE/DATETIME columns. Those casts are planning noise for witness
    generation — peel them so solvers constrain the base temporal variable.
    """
    while isinstance(node, (exp.Cast, exp.TsOrDsToTimestamp, exp.TsOrDsToDate)):
        node = node.this
    return node


def normalize_problem(problem: Problem) -> Problem:
    """Return a Problem with each constraint expression normalized."""
    return Problem(
        constraints=[normalize_expression(expr) for expr in problem.constraints],
        equalities=list(problem.equalities),
        variables=set(problem.variables),
    )


def normalize_constraint(constraint):
    """Return a normalized copy of ``constraint``.

    The input object and its expression list are left untouched. Metadata on
    copied sqlglot nodes is preserved by sqlglot's ``copy()`` behavior.

    Legacy Constraint objects expose ``join_equalities`` / ``storage_relations``.
    Prefer :func:`normalize_problem` for the current :class:`Problem` API.
    """
    return replace(
        constraint,
        constraints=[
            normalize_expression(expression)
            for expression in constraint.constraints
        ],
        join_equalities=list(constraint.join_equalities),
        variables=dict(constraint.variables),
        storage_relations=dict(constraint.storage_relations),
    )


def normalize_expression(expression: exp.Expression) -> exp.Expression:
    """Normalize one predicate expression."""
    expression = expression.copy()
    witness = lower_expression_witness(expression)
    if witness is not None:
        return witness
    return lower_temporal_projection_bounds(expression)


def lower_temporal_projection_bounds(expression: exp.Expression) -> exp.Expression:
    """Rewrite supported monotone temporal projections to direct bounds."""
    if isinstance(expression, exp.And):
        return exp.And(
            this=lower_temporal_projection_bounds(expression.this),
            expression=lower_temporal_projection_bounds(expression.expression),
        )
    if isinstance(expression, exp.Paren):
        return exp.Paren(this=lower_temporal_projection_bounds(expression.this))

    lowered = _lower_temporal_projection_comparison(expression)
    if lowered is not None:
        return lowered
    return expression


def lower_expression_witness(expression: exp.Expression) -> Optional[exp.Expression]:
    """Lower recognized expression witnesses that are not semantic bounds."""
    return _rewrite_time_substr_arithmetic_predicate(expression)


def _lower_temporal_projection_comparison(
    expression: exp.Expression,
) -> Optional[exp.Expression]:
    if not isinstance(expression, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)):
        return None

    projection_expr = expression.this
    projection = _temporal_projection(projection_expr)
    if projection is None:
        return None
    col, prefix_length = projection

    lo_value: Optional[str] = None
    hi_value: Optional[str] = None
    if isinstance(expression, exp.EQ):
        lo_value = hi_value = _literal_text(expression.expression)
    elif isinstance(expression, exp.GT):
        next_date = _date_after_prefix(_literal_text(expression.expression), prefix_length)
        if next_date is None:
            return None
        lo_value = next_date.isoformat()[:prefix_length]
    elif isinstance(expression, exp.GTE):
        lo_value = _literal_text(expression.expression)
    elif isinstance(expression, exp.LT):
        prev_date = _date_before_prefix(_literal_text(expression.expression), prefix_length)
        if prev_date is None:
            return None
        hi_value = prev_date.isoformat()[:prefix_length]
    elif isinstance(expression, exp.LTE):
        hi_value = _literal_text(expression.expression)
    else:
        lo_value = _literal_text(expression.args.get("low"))
        hi_value = _literal_text(expression.args.get("high"))

    lo_bounds = _prefix_date_bounds(lo_value, prefix_length) if lo_value else None
    hi_bounds = _prefix_date_bounds(hi_value, prefix_length) if hi_value else None
    if lo_value and lo_bounds is None:
        return None
    if hi_value and hi_bounds is None:
        return None
    lo_date = lo_bounds[0] if lo_bounds is not None else None
    hi_date = hi_bounds[1] if hi_bounds is not None else None
    if lo_date is None and hi_date is None:
        return None

    predicates: List[exp.Expression] = []
    if lo_date is not None:
        predicates.append(exp.GTE(this=col.copy(), expression=_bound_literal(col, lo_date, is_high=False)))
    if hi_date is not None:
        predicates.append(exp.LTE(this=col.copy(), expression=_bound_literal(col, hi_date, is_high=True)))
    return predicates[0] if len(predicates) == 1 else exp.and_(*predicates)


def _bound_literal(col: SolverVar, bound: _date, *, is_high: bool) -> exp.Literal:
    is_date, is_datetime = _is_temporal_column(col)
    if is_date:
        return exp.Literal.string(bound.isoformat())
    if is_datetime:
        dt = (
            _datetime(bound.year, bound.month, bound.day, 23, 59, 59)
            if is_high
            else _datetime(bound.year, bound.month, bound.day)
        )
        return exp.Literal.string(dt.strftime("%Y-%m-%d %H:%M:%S"))
    return exp.Literal.string(bound.isoformat())


def _literal_int(expr: exp.Expression | None) -> Optional[int]:
    if not isinstance(expr, exp.Literal):
        return None
    try:
        return int(str(expr.this))
    except (TypeError, ValueError):
        return None


def _literal_text(expr: exp.Expression | None) -> Optional[str]:
    if not isinstance(expr, exp.Literal):
        return None
    return str(expr.this)


def _call_name(expr: exp.Expression) -> str:
    if isinstance(expr, exp.Anonymous):
        return (expr.name or "").upper()
    if isinstance(expr, exp.Substring):
        return "SUBSTR"
    return expr.key.upper() if expr.key else ""


def _call_args(expr: exp.Expression) -> List[exp.Expression]:
    if isinstance(expr, exp.Substring):
        args = [expr.this]
        if expr.args.get("start") is not None:
            args.append(expr.args["start"])
        if expr.args.get("length") is not None:
            args.append(expr.args["length"])
        return args
    return [
        child for child in expr.iter_expressions()
        if not isinstance(child, exp.DataType)
    ]


def _unwrap_temporal_column(expr: exp.Expression) -> Optional[SolverVar]:
    expr = unwrap_planning_temporal_arg(expr)
    return expr if isinstance(expr, SolverVar) else None


def _temporal_projection(expr: exp.Expression) -> Optional[Tuple[SolverVar, int]]:
    if isinstance(expr, exp.Date):
        inner = expr.this
        if isinstance(inner, SolverVar):
            return inner, 10
        projection = _temporal_prefix_projection(inner)
        if projection is not None and projection[1] == 10:
            return projection
        return None
    return _temporal_prefix_projection(expr)


def _temporal_prefix_projection(expr: exp.Expression) -> Optional[Tuple[SolverVar, int]]:
    supported_formats = {
        "%Y": 4,
        "%Y-%m": 7,
        "%Y-%m-%d": 10,
    }
    if isinstance(expr, exp.TimeToStr):
        fmt = _literal_text(expr.args.get("format"))
        col = _unwrap_temporal_column(expr.this)
        if fmt in supported_formats and col is not None:
            return col, supported_formats[fmt]
        return None
    if isinstance(expr, exp.Year):
        col = _unwrap_temporal_column(expr.this)
        return (col, 4) if col is not None else None
    if isinstance(expr, exp.Extract):
        unit_text = _extract_unit_name(expr.this)
        if unit_text != "YEAR":
            return None
        col = _unwrap_temporal_column(expr.expression)
        return (col, 4) if col is not None else None

    name = _call_name(expr)
    if name in {"SUBSTR", "SUBSTRING"}:
        args = _call_args(expr)
        if len(args) < 3:
            return None
        col = _unwrap_temporal_column(args[0])
        start = _literal_int(args[1])
        length = _literal_int(args[2])
        if col is not None and start == 1 and length in {4, 7, 10}:
            return col, length
        return None
    if name in {"STRFTIME", "TIME_TO_STR"}:
        args = _call_args(expr)
        if len(args) < 2:
            return None
        if name == "STRFTIME":
            fmt = _literal_text(args[0])
            col = _unwrap_temporal_column(args[1])
        else:
            col = _unwrap_temporal_column(args[0])
            fmt = _literal_text(args[1])
        if fmt in supported_formats and col is not None:
            return col, supported_formats[fmt]
    return None


def _extract_unit_name(expr: exp.Expression | None) -> Optional[str]:
    if isinstance(expr, exp.Var):
        return expr.name.upper()
    if isinstance(expr, exp.Identifier):
        return expr.name.upper()
    if isinstance(expr, exp.Column):
        return expr.name.upper()
    if isinstance(expr, exp.Literal):
        return str(expr.this).upper()
    return None


def _prefix_date_bounds(value: str, prefix_length: int):
    try:
        if prefix_length == 4 and len(value) == 4:
            year = int(value)
            return _date(year, 1, 1), _date(year, 12, 31)
        if prefix_length == 7 and len(value) == 7 and value[4] == "-":
            year = int(value[:4])
            month = int(value[5:7])
            last_day = calendar.monthrange(year, month)[1]
            return _date(year, month, 1), _date(year, month, last_day)
        if prefix_length == 10 and len(value) == 10 and value[4] == "-" and value[7] == "-":
            year = int(value[:4])
            month = int(value[5:7])
            day = int(value[8:10])
            exact = _date(year, month, day)
            return exact, exact
    except ValueError:
        return None
    return None


def _is_temporal_column(col: SolverVar) -> Tuple[bool, bool]:
    dtype = node_dtype(col) or DataType.build("TEXT")
    is_date = dtype.is_type(DataType.Type.DATE) or dtype.is_type(DataType.Type.DATE32)
    is_datetime = dtype.is_type(
        DataType.Type.TIMESTAMP, DataType.Type.TIMESTAMP_S,
        DataType.Type.TIMESTAMP_MS, DataType.Type.TIMESTAMP_NS,
        DataType.Type.TIMESTAMPTZ, DataType.Type.TIMESTAMPLTZ,
        DataType.Type.DATETIME, DataType.Type.DATETIME64,
    )
    return is_date, is_datetime


def _date_after_prefix(value: Optional[str], prefix_length: int) -> Optional[_date]:
    bounds = _prefix_date_bounds(value, prefix_length) if value else None
    if bounds is None:
        return None
    return bounds[1] + timedelta(days=1)


def _date_before_prefix(value: Optional[str], prefix_length: int) -> Optional[_date]:
    bounds = _prefix_date_bounds(value, prefix_length) if value else None
    if bounds is None:
        return None
    return bounds[0] - timedelta(days=1)


def _rewrite_time_substr_arithmetic_predicate(
    expr: exp.Expression,
) -> Optional[exp.Expression]:
    if not isinstance(expr, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
        return None
    threshold = _literal_int(expr.expression)
    if threshold is None:
        return None
    col = _time_substr_arithmetic_column(expr.this)
    if col is None:
        return None

    if isinstance(expr, exp.LT):
        seconds = threshold - 1
    elif isinstance(expr, exp.LTE):
        seconds = threshold
    elif isinstance(expr, exp.GT):
        seconds = threshold + 1
    else:
        seconds = threshold
    if seconds < 0:
        return None
    return exp.EQ(this=col.copy(), expression=exp.Literal.string(_format_mmss_time(seconds)))


def _time_substr_arithmetic_column(expr: exp.Expression) -> Optional[SolverVar]:
    columns: List[SolverVar] = []
    starts: set[int] = set()
    for node in expr.walk():
        if _call_name(node) not in {"SUBSTR", "SUBSTRING"}:
            continue
        args = _call_args(node)
        if len(args) < 3 or not isinstance(args[0], SolverVar):
            continue
        start = _literal_int(args[1])
        length = _literal_int(args[2])
        if length != 2 or start not in {1, 4, 7}:
            continue
        columns.append(args[0])
        starts.add(start)
    if not {1, 4}.issubset(starts) or not columns:
        return None
    first = columns[0]
    if any(col != first for col in columns):
        return None
    return first


def _format_mmss_time(total_seconds: int) -> str:
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:02d}:{seconds:02d}:00"


__all__ = [
    "lower_expression_witness",
    "lower_temporal_projection_bounds",
    "normalize_constraint",
    "normalize_expression",
    "normalize_problem",
    "unwrap_planning_temporal_arg",
]
