"""DataFusion planner: lower optimized logical plans into a Step DAG.

Public API::

    explain(ddl, query, dialect="sqlite", session=None) -> Plan

Pass a :class:`~parseval.plan.session.DataFusionSessionManager` to reuse a
session (e.g. after registering custom UDFs). When ``session`` is omitted, a
default manager is created for ``dialect``.

The IR follows sqlglot.planner mechanics (``Step`` / ``dependencies`` /
``dependents``) with subclasses aligned to DataFusion ``to_variant()``
types. Semantic identifiers are always sqlglot expressions — never bare
``str``.
"""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from datafusion import SessionContext
from datafusion.unparser import Dialect as UnparserDialect
from datafusion.unparser import Unparser
from sqlglot.dialects.mysql import MySQL
from sqlglot.dialects.postgres import Postgres
from sqlglot.dialects.sqlite import SQLite
from sqlglot.generator import Generator
from sqlglot import exp
import sqlglot

from parseval.plan.session import DataFusionSessionManager

_logger = logging.getLogger(__name__)


class PlanError(ValueError):
    """Raised when DataFusion produced a plan we cannot lower into Steps/exprs."""

UNPARSER_DIALECTS: Dict[str, UnparserDialect] = {
    "sqlite": UnparserDialect.sqlite(),
    "mysql": UnparserDialect.mysql(),
    "postgres": UnparserDialect.postgres(),
    "postgresql": UnparserDialect.postgres(),
}

_IDENT_ATTRS = frozenset(
    {
        "name",
        "table",
        "source",
        "alias",
        "group",
        "columns",
        "projections",
        "scan_projections",
        "key",
        "on",
        "on_keys",
        "window_exprs",
        "values",
        "aggregations",
        "condition",
        "partition_exprs",
    }
)


# ---------------------------------------------------------------------------
# DF expression → sqlglot Expression (structural)
# ---------------------------------------------------------------------------
#
# Binding gaps handled explicitly (no silent fallbacks):
# - ``RawExpr.to_variant()`` raises ``ValueError`` for ScalarFunction /
#   WindowFunction (not implemented in the Python bindings) → rex Call path.
# - ``Literal.value_string()`` does not support ``Utf8View`` → display text.
# - ``Cast`` target type comes from ``RawExpr.types().friendly_arrow_type_name()``.
# - Logical ``Limit`` has no skip/fetch accessors → parse ``display``.
# - ``DFSchema`` only exposes ``field_names()`` → parse ``display_indent_schema``.
# - ``ScalarSubquery``: inner DF ``LogicalPlan`` → plan-level scalar registry plus
#   a ``ScalarSubqueryRef`` placeholder in the outer sqlglot expression. Recovering
#   the inner plan when ``Subquery.input()`` is empty still uses unparse + replan.

_BINARY_OPS: Dict[str, type[exp.Expression]] = {
    "=": exp.EQ,
    "!=": exp.NEQ,
    "<>": exp.NEQ,
    "<": exp.LT,
    "<=": exp.LTE,
    ">": exp.GT,
    ">=": exp.GTE,
    "+": exp.Add,
    "-": exp.Sub,
    "*": exp.Mul,
    "/": exp.Div,
    "%": exp.Mod,
    "AND": exp.And,
    "OR": exp.Or,
    "LIKE": exp.Like,
    "ILIKE": exp.ILike,
}

# DF Literal.data_type() string → (extractor method, python kind)
_LITERAL_EXTRACTORS: Dict[str, Tuple[str, str]] = {
    "Boolean": ("value_bool", "bool"),
    "Int8": ("value_i8", "int"),
    "Int16": ("value_i16", "int"),
    "Int32": ("value_i32", "int"),
    "Int64": ("value_i64", "int"),
    "UInt8": ("value_u8", "int"),
    "UInt16": ("value_u16", "int"),
    "UInt32": ("value_u32", "int"),
    "UInt64": ("value_u64", "int"),
    "Float32": ("value_f32", "float"),
    "Float64": ("value_f64", "float"),
    "Utf8": ("value_string", "str"),
}

_AGG_CTORS: Dict[str, Callable[..., exp.Expression]] = {
    "sum": lambda args: exp.Sum(this=args[0]),
    "avg": lambda args: exp.Avg(this=args[0]),
    "min": lambda args: exp.Min(this=args[0]),
    "max": lambda args: exp.Max(this=args[0]),
}

_WINDOW_CTORS: Dict[str, Callable[..., exp.Expression]] = {
    "row_number": lambda _args: exp.RowNumber(),
    "sum": lambda args: exp.Sum(this=args[0]),
    "avg": lambda args: exp.Avg(this=args[0]),
    "min": lambda args: exp.Min(this=args[0]),
    "max": lambda args: exp.Max(this=args[0]),
    "count": lambda args: exp.Count(this=args[0] if args else exp.Star()),
}

_UTF8_DISPLAY_RE = re.compile(r'^(?:Utf8(?:View)?)\("(.*)"\)$', re.S)
_LIMIT_RE = re.compile(r"skip\s*=\s*(\d+).*?fetch\s*=\s*(\d+)", re.I | re.S)
_NANOS_PER_UNIT: Dict[str, int] = {
    "HOUR": 3_600_000_000_000,
    "MINUTE": 60_000_000_000,
    "SECOND": 1_000_000_000,
}

# Arrow / DF type names → sqlglot ``DataType.Type`` (not SQL string tokens).
_DF_TO_SQLGLOT_TYPE: Dict[str, exp.DataType.Type] = {
    "Int8": exp.DataType.Type.TINYINT,
    "Int16": exp.DataType.Type.SMALLINT,
    "Int32": exp.DataType.Type.INT,
    "Int64": exp.DataType.Type.BIGINT,
    "UInt8": exp.DataType.Type.UTINYINT,
    "UInt16": exp.DataType.Type.USMALLINT,
    "UInt32": exp.DataType.Type.UINT,
    "UInt64": exp.DataType.Type.UBIGINT,
    "Float32": exp.DataType.Type.FLOAT,
    "Float64": exp.DataType.Type.DOUBLE,
    "Double": exp.DataType.Type.DOUBLE,
    "Utf8": exp.DataType.Type.TEXT,
    "Utf8View": exp.DataType.Type.TEXT,
    "Boolean": exp.DataType.Type.BOOLEAN,
    "Date32": exp.DataType.Type.DATE,
    "Date64": exp.DataType.Type.DATE,
    "Timestamp": exp.DataType.Type.TIMESTAMP,
    "Time32": exp.DataType.Type.TIME,
    "Time64": exp.DataType.Type.TIME,
    "Duration": exp.DataType.Type.INTERVAL,
    "Interval": exp.DataType.Type.INTERVAL,
    "Null": exp.DataType.Type.NULL,
}

# Match trailing ``:Type`` / ``:Type;N`` so field names may contain ``:`` (e.g. Utf8(":")).
_SCHEMA_TYPE_TAIL_RE = re.compile(
    r":(?P<type>[A-Za-z_][\w]*(?:\([^)]*\))?)"
    r"(?P<nullable>;N)?(?:,\s*|$)"
)


def _df_arrow_type_name(raw: Any) -> str:
    """Arrow type name for a DF expr; tolerate bindings that omit Utf8View."""
    try:
        return str(raw.types().friendly_arrow_type_name())
    except NotImplementedError as exc:
        # datafusion-python raises ``NotImplementedError("Utf8View")`` etc.
        name = str(exc.args[0]) if exc.args else str(exc)
        if name in _DF_TO_SQLGLOT_TYPE:
            return name
        raise


def _sqlglot_type(df_type: str) -> exp.DataType:
    """Map a DataFusion/Arrow type name to a sqlglot ``DataType``."""
    base = df_type.split("(", 1)[0].strip()
    kind = _DF_TO_SQLGLOT_TYPE.get(base)
    if kind is None:
        raise PlanError(f"unsupported DataFusion type {df_type!r}")
    return exp.DataType(this=kind)


def _is_text_cast_target(to: Optional[exp.Expression]) -> bool:
    if not isinstance(to, exp.DataType):
        return False
    return to.this in {
        exp.DataType.Type.TEXT,
        exp.DataType.Type.VARCHAR,
        exp.DataType.Type.CHAR,
        exp.DataType.Type.NCHAR,
        exp.DataType.Type.NVARCHAR,
    }


def _drop_planning_text_cast(node: exp.Expression) -> exp.Expression:
    """Peel DF Utf8 coercions (``CAST(... AS TEXT)``) around strftime args."""
    while isinstance(node, exp.Cast) and _is_text_cast_target(node.args.get("to")):
        node = node.this
    return node


def _annotate_type(
    expr: exp.Expression,
    df_type: str,
    *,
    nullable: Optional[bool] = None,
) -> exp.Expression:
    """Attach DF-provided type (and optional nullability) onto ``expr``."""
    expr.type = _sqlglot_type(df_type)
    if isinstance(expr, exp.Cast) and isinstance(expr.this, exp.Literal):
        to_type = expr.args.get("to")
        if isinstance(to_type, exp.DataType):
            expr.this.type = to_type
    if nullable is not None:
        expr.meta["nullable"] = nullable
    return expr


def _parse_schema_body(body: str) -> List[Tuple[str, str, bool]]:
    """Parse ``name:Type`` / ``name:Type;N`` fields from a schema body.

    Field names can embed ``:`` (e.g. ``Utf8(":")`` in a projected expr), so we
    locate type tails rather than splitting on the first colon.
    """
    body = body.strip()
    if not body:
        return []
    fields: List[Tuple[str, str, bool]] = []
    pos = 0
    for match in _SCHEMA_TYPE_TAIL_RE.finditer(body):
        name = body[pos : match.start()].strip()
        if not name:
            raise PlanError(f"cannot parse schema field in {body!r} at {pos}")
        fields.append(
            (
                name,
                match.group("type"),
                match.group("nullable") is not None,
            )
        )
        pos = match.end()
    if pos < len(body) and body[pos:].strip():
        raise PlanError(f"cannot parse schema field in {body!r} at {pos}")
    if not fields:
        raise PlanError(f"cannot parse schema field in {body!r} at 0")
    return fields


def _parse_plan_schema(plan: Any) -> List[Tuple[str, str, bool]]:
    """Parse ``[(name, df_type, nullable), ...]`` from ``display_indent_schema``.

    Schema is the trailing ``[...]`` on the first line. Window plans nest
    brackets inside field names, so we match the outermost trailing pair.
    """
    text = plan.display_indent_schema().splitlines()[0]
    end = text.rfind("]")
    if end < 0:
        raise PlanError(f"logical plan schema missing from {text!r}")
    depth = 0
    start = -1
    for i in range(end, -1, -1):
        ch = text[i]
        if ch == "]":
            depth += 1
        elif ch == "[":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start < 0:
        raise PlanError(f"logical plan schema brackets unbalanced in {text!r}")
    return _parse_schema_body(text[start + 1 : end])


def _annotate_exprs(
    exprs: Sequence[exp.Expression],
    fields: Sequence[Tuple[str, str, bool]],
    *,
    what: str,
) -> None:
    """Zip DF schema fields onto expressions; lengths must match."""
    if len(exprs) != len(fields):
        raise PlanError(
            f"{what}: expression count {len(exprs)} != schema field count {len(fields)} "
            f"({[f[0] for f in fields]})"
        )
    for expr, (name, df_type, nullable) in zip(exprs, fields):
        expr.meta["datafusion_name"] = name
        _annotate_type(expr, df_type, nullable=nullable)


def _parse_interval_struct(text: str) -> tuple[int, int, int] | None:
    try:
        brace_start = text.index("{")
        brace_end = text.rindex("}")
    except ValueError:
        return None
    body = text[brace_start + 1 : brace_end]
    pairs: dict[str, str] = {}
    for part in body.split(","):
        part = part.strip()
        if ":" in part:
            key, val = part.split(":", 1)
            pairs[key.strip()] = val.strip()
    if not {"months", "days", "nanoseconds"} <= pairs.keys():
        return None
    return int(pairs["months"]), int(pairs["days"]), int(pairs["nanoseconds"])


def _literal_from_variant(variant: Any, raw: Any) -> exp.Expression:
    """Build a sqlglot literal from a DF ``Literal`` via dtype dispatch."""
    dtype = variant.data_type()
    base = dtype.split("(", 1)[0].strip()
    if base == "Null":
        return _annotate_type(exp.null(), "Null", nullable=True)

    if base == "Utf8View":
        # value_string() does not support Utf8View; use display / into_type.
        into = str(variant.into_type())
        if into.upper() == "NULL":
            return _annotate_type(exp.null(), "Utf8View", nullable=True)
        text = str(raw.canonical_name())
        match = _UTF8_DISPLAY_RE.match(text)
        if match is None:
            raise PlanError(f"cannot parse Utf8View literal from {text!r}")
        return _annotate_type(
            exp.Literal.string(match.group(1)), "Utf8View", nullable=False
        )

    if base == "Date32":
        into = str(variant.into_type())
        if len(into) == 10 and into[4] == "-" and into[7] == "-":
            node: exp.Expression = exp.Literal.string(into)
        else:
            node = exp.Literal.string(
                (date(1970, 1, 1) + timedelta(days=int(variant.value_date32()))).isoformat()
            )
        return _annotate_type(node, base, nullable=False)

    if base == "Date64":
        into = str(variant.into_type())
        if len(into) == 10 and into[4] == "-" and into[7] == "-":
            node = exp.Literal.string(into)
        else:
            node = exp.Literal.string(
                (date(1970, 1, 1) + timedelta(days=int(variant.value_date64()))).isoformat()
            )
        return _annotate_type(node, base, nullable=False)

    if base == "Timestamp":
        pair = variant.value_timestamp()
        ns = int(pair[0]) if isinstance(pair, tuple) else int(pair)
        dt = datetime.fromtimestamp(ns / 1_000_000_000, tz=timezone.utc).replace(
            tzinfo=None
        )
        return _annotate_type(
            exp.Literal.string(dt.isoformat(sep=" ", timespec="microseconds")),
            "Timestamp",
            nullable=False,
        )

    if base == "Time64":
        seconds, _ = divmod(int(variant.value_time64()), 1_000_000_000)
        hours, rem = divmod(seconds, 3600)
        minutes, secs = divmod(rem, 60)
        return _annotate_type(
            exp.Literal.string(f"{hours:02d}:{minutes:02d}:{secs:02d}"),
            base,
            nullable=False,
        )

    if base == "Interval":
        text = str(variant.into_type())
        if text.upper() == "NULL":
            raise PlanError(
                f"unsupported DataFusion literal dtype {dtype!r} ({raw.canonical_name()})"
            )
        components = _parse_interval_struct(text)
        if components is None:
            return _annotate_type(exp.Literal.string(text), "Interval", nullable=False)
        months, days, nanoseconds = components
        if months and not days and not nanoseconds:
            return _annotate_type(
                exp.Interval(this=exp.Literal.number(months), unit=exp.Var(this="MONTH")),
                "Interval",
                nullable=False,
            )
        if days and not months and not nanoseconds:
            return _annotate_type(
                exp.Interval(this=exp.Literal.number(days), unit=exp.Var(this="DAY")),
                "Interval",
                nullable=False,
            )
        for unit, ns_per_unit in _NANOS_PER_UNIT.items():
            if nanoseconds and nanoseconds % ns_per_unit == 0:
                value = nanoseconds // ns_per_unit + days * 86400 + months * 2592000
                return _annotate_type(
                    exp.Interval(this=exp.Literal.number(value), unit=exp.Var(this=unit)),
                    "Interval",
                    nullable=False,
                )
        return _annotate_type(
            exp.Interval(
                this=exp.Literal.number(nanoseconds // 1_000_000_000 + days * 86400 + months * 2592000),
                unit=exp.Var(this="SECOND"),
            ),
            "Interval",
            nullable=False,
        )

    extractor = _LITERAL_EXTRACTORS.get(dtype) or _LITERAL_EXTRACTORS.get(base)
    if extractor is not None:
        method, kind = extractor
        value = getattr(variant, method)()
        if value is None:
            return _annotate_type(exp.null(), base, nullable=True)
        if kind == "bool":
            node = exp.Boolean(this=bool(value))
        elif kind == "str":
            node = exp.Literal.string(str(value))
        elif kind == "float":
            node = exp.Literal.number(value)
        else:
            node = exp.Literal.number(int(value))
        return _annotate_type(node, base, nullable=False)

    into = str(variant.into_type())
    if into.upper() == "NULL":
        return _annotate_type(exp.null(), "Null", nullable=True)
    raise PlanError(
        f"unsupported DataFusion literal dtype {dtype!r} ({raw.canonical_name()})"
    )


def _lower_binary(op: str, left: exp.Expression, right: exp.Expression) -> exp.Expression:
    key = op.strip().upper() if op.strip().upper() in {"AND", "OR", "LIKE", "ILIKE"} else op.strip()
    cls = _BINARY_OPS.get(key) or _BINARY_OPS.get(op.strip())
    if cls is None:
        return exp.Anonymous(this=op, expressions=[left, right])
    return cls(this=left, expression=right)


def _lower_aggregate_function(
    variant: Any,
    *,
    state: Optional["_NodeLowerState"] = None) -> exp.Expression:
    func = str(variant.aggregate_type()).lower()
    args = [to_expression(a, state=state) for a in (variant.args() or [])]
    if func == "count":
        # DF encodes COUNT(*) as count(Int64(1)).
        if (
            len(args) == 1
            and isinstance(args[0], exp.Literal)
            and not args[0].is_string
            and str(args[0].this) == "1"
        ):
            node: exp.Expression = exp.Count(this=exp.Star())
        elif not args:
            node = exp.Count(this=exp.Star())
        else:
            node = exp.Count(this=args[0])
    elif func in _AGG_CTORS and args:
        node = _AGG_CTORS[func](args)
    else:
        node = exp.Anonymous(this=func, expressions=args)
    if variant.is_distinct():
        node.set("distinct", True)
    return node


def _lower_from_variant(
    variant: Any,
    raw: Any,
    *,
    state: Optional["_NodeLowerState"] = None) -> exp.Expression:
    """Lower a successful ``to_variant()`` payload by concrete variant type."""
    kind = type(variant).__name__

    if kind == "Column":
        relation = variant.relation()
        return exp.column(variant.name(), table=relation or None)

    if kind == "Literal":
        return _literal_from_variant(variant, raw)

    if kind == "Alias":
        return exp.alias_(
            to_expression(variant.expr(), state=state),
            variant.alias(),
            quoted=False,
        )

    if kind == "BinaryExpr":
        return _lower_binary(
            str(variant.op()),
            to_expression(variant.left(), state=state),
            to_expression(variant.right(), state=state),
        )

    if kind == "Not":
        return exp.Not(this=to_expression(variant.expr(), state=state))

    if kind == "Negative":
        return exp.Neg(this=to_expression(variant.expr(), state=state))

    if kind == "IsNull":
        return exp.Is(
            this=to_expression(variant.expr(), state=state), expression=exp.null()
        )

    if kind == "IsNotNull":
        return exp.Not(
            this=exp.Is(
                this=to_expression(variant.expr(), state=state),
                expression=exp.null(),
            )
        )

    if kind == "Like":
        node: exp.Expression = exp.Like(
            this=to_expression(variant.expr(), state=state),
            expression=to_expression(variant.pattern(), state=state),
        )
        return exp.Not(this=node) if variant.negated() else node

    if kind == "ILike":
        node = exp.ILike(
            this=to_expression(variant.expr(), state=state),
            expression=to_expression(variant.pattern(), state=state),
        )
        return exp.Not(this=node) if variant.negated() else node

    if kind == "Cast":
        df_type = _df_arrow_type_name(raw)
        node = exp.Cast(
            this=to_expression(variant.expr(), state=state),
            to=_sqlglot_type(df_type),
        )
        return _annotate_type(node, df_type)

    if kind == "Case":
        whens = [
            exp.If(
                this=to_expression(when, state=state),
                true=to_expression(then, state=state),
            )
            for when, then in (variant.when_then_expr() or [])
        ]
        else_expr = variant.else_expr()
        return exp.Case(
            ifs=whens,
            default=to_expression(else_expr, state=state)
            if else_expr is not None
            else None,
        )

    if kind == "AggregateFunction":
        return _lower_aggregate_function(variant, state=state)

    if kind == "InList":
        node = exp.In(
            this=to_expression(variant.expr(), state=state),
            expressions=[
                to_expression(item, state=state) for item in (variant.list() or [])
            ],
        )
        return exp.Not(this=node) if variant.negated() else node

    if kind == "ScalarSubquery":
        if state is None:
            raise PlanError(
                f"unsupported DataFusion expr variant {kind!r}: {raw.canonical_name()}"
            )
        return state.register_scalar_subquery(variant.subquery())

    if kind == "InSubquery":
        if state is None:
            raise PlanError(
                f"unsupported DataFusion expr variant {kind!r}: {raw.canonical_name()}"
            )
        return UnsupportedExpression(
            this=kind,
            expressions=[
                to_expression(variant.expr(), state=state),
                state.register_scalar_subquery(variant.subquery()),
            ],
        )

    raise PlanError(
        f"unsupported DataFusion expr variant {kind!r}: {raw.canonical_name()}"
    )


def _lower_from_rex(
    raw: Any,
    *,
    state: Optional["_NodeLowerState"] = None) -> exp.Expression:
    """Lower call-shaped exprs when ``to_variant`` is unimplemented (e.g. ScalarFunction)."""
    rex_type = raw.rex_type()
    if not (str(rex_type).endswith("Call") or getattr(rex_type, "name", "") == "Call"):
        raise PlanError(
            f"DataFusion expr {raw.variant_name()!r} has no Python variant "
            f"and is not a rex Call: {raw.canonical_name()}"
        )
    op = raw.rex_call_operator()
    if not op:
        raise PlanError(f"rex Call missing operator: {raw.canonical_name()}")
    args = [to_expression(o, state=state) for o in (raw.rex_call_operands() or [])]
    op_l = str(op).strip().lower()

    if op_l in {"and", "or"} and len(args) == 2:
        return _lower_binary(op_l.upper(), args[0], args[1])
    if op in _BINARY_OPS and len(args) == 2:
        return _lower_binary(op, args[0], args[1])
    if op_l == "not" and len(args) == 1:
        return exp.Not(this=args[0])
    if op_l in {"is null", "isnull"} and len(args) == 1:
        return exp.Is(this=args[0], expression=exp.null())
    if op_l in {"is not null", "isnotnull"} and len(args) == 1:
        return exp.Not(this=exp.Is(this=args[0], expression=exp.null()))
    if op_l == "like" and len(args) >= 2:
        return exp.Like(this=args[0], expression=args[1])
    if op_l == "ilike" and len(args) >= 2:
        return exp.ILike(this=args[0], expression=args[1])
    if op_l == "cast" and args:
        df_type = _df_arrow_type_name(raw)
        node = exp.Cast(this=args[0], to=_sqlglot_type(df_type))
        return _annotate_type(node, df_type)
    # SQLite/Bird planning stub: canonicalize to sqlglot TimeToStr.
    # Drop DF's CAST(... AS TEXT) around the timestring — Utf8 UDF typing noise.
    if op_l == "strftime" and len(args) == 2:
        node = exp.TimeToStr(this=_drop_planning_text_cast(args[1]), format=args[0])
        return _annotate_type(node, "Utf8")
    return exp.Anonymous(this=str(op), expressions=args)


def _lower_window_expr(
    window_node: Any,
    raw: Any,
    *,
    state: Optional["_NodeLowerState"] = None) -> exp.Expression:
    """Lower a window function via ``WindowExpr`` getters (variant not implemented)."""
    func = str(window_node.window_func_name(raw)).lower()
    args = [to_expression(a, state=state) for a in (window_node.get_args(raw) or [])]
    partitions = [
        to_expression(p, state=state)
        for p in (window_node.get_partition_exprs(raw) or [])
    ]
    order_exprs = [
        exp.Ordered(
            this=to_expression(sort_expr.expr(), state=state),
            desc=not bool(sort_expr.ascending()),
        )
        for sort_expr in (window_node.get_sort_exprs(raw) or [])
    ]
    ctor = _WINDOW_CTORS.get(func)
    this = ctor(args) if ctor is not None else exp.Anonymous(this=func, expressions=args)
    return exp.Window(
        this=this,
        partition_by=partitions or None,
        order=exp.Order(expressions=order_exprs) if order_exprs else None,
        over="OVER",
    )


def to_expression(
    df_expr: Any,
    *,
    window_node: Any = None,
    state: Optional["_NodeLowerState"] = None) -> exp.Expression:
    """Lower a DataFusion expr to sqlglot via variant / rex structure."""
    if isinstance(df_expr, exp.Expression):
        return df_expr
    if df_expr is None:
        raise PlanError("cannot lower None DataFusion expression")

    kind = df_expr.variant_name()
    if kind == "WindowFunction":
        if window_node is None:
            raise PlanError("WindowFunction requires parent WindowExpr for lowering")
        return _lower_window_expr(window_node, df_expr, state=state)

    # ScalarFunction / WindowFunction raise ValueError — not implemented in DF Python.
    try:
        variant = df_expr.to_variant()
    except ValueError:
        return _lower_from_rex(df_expr, state=state)
    return _lower_from_variant(variant, df_expr, state=state)


def _scalar_inner_logical(sq: Any, state: "_NodeLowerState", plan: Any) -> Any:
    """Inner logical plan for a scalar subquery (bindings or unparse fallback)."""
    inputs = sq.input()
    if inputs:
        return inputs[0]
    fragment = state.pop_scalar_sql(plan)
    return state.ctx.sql(fragment).optimized_logical_plan()


def repr_expr(node: Any, *, indent: int = 0) -> str:
    """Detailed tree representation of a sqlglot expression."""
    pad = "  " * indent
    inner = "  " * (indent + 1)    
    if isinstance(node, Step):
        return repr_step(node, indent=indent)

    if not isinstance(node, exp.Expression):
        return repr(node)

    if not list(node.iter_expressions()):
        return f"{type(node).__name__}({node.sql()!r})"

    cls = type(node).__name__
    parts = []

    for key, val in node.args.items():
        # Skip empty arguments to keep the tree clean
        if val is None or (isinstance(val, list) and not val):
            continue

        # Handle lists of expressions (e.g., an IN clause with multiple values)
        if isinstance(val, list):
            list_str = "[\n"
            for item in val:
                list_str += f"{inner}  {repr_expr(item, indent=indent + 2)},\n"
            list_str += f"{inner}]"
            parts.append(f"{inner}{key}={list_str},")
            
        # Handle standard single expressions or primitives (Alias names, Cast types)
        else:
            parts.append(f"{inner}{key}={repr_expr(val, indent=indent + 1)},")

    # If it somehow has no arguments, just return the class name
    if not parts:
        return f"{cls}()"

    return f"{cls}(\n" + "\n".join(parts) + f"\n{pad})"

def _all_reachable_steps(root: Step) -> List[Step]:
    seen: Set[int] = set()
    out: List[Step] = []
    stack: List[Step] = [root]
    while stack:
        step = stack.pop()
        sid = id(step)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(step)
        stack.extend(step.dependencies)
    return out



def _walk_sql_expr(node: exp.Expression, visit: Callable[[exp.Subquery], None]) -> None:
    """DFS over sqlglot expr tree; ``visit`` on each scalar ``Subquery``."""
    if isinstance(node, exp.Subquery):
        parent = node.parent
        if not isinstance(parent, (exp.From, exp.Join, exp.CTE)):
            visit(node)
            return
    for child in node.iter_expressions():
        _walk_sql_expr(child, visit)


def _find_outer_select(ast: exp.Expression) -> Optional[exp.Select]:
    if isinstance(ast, exp.Select):
        return ast
    if isinstance(ast, exp.Subquery):
        return _find_outer_select(ast.this)
    if isinstance(ast, exp.Query) and ast.this is not None:
        return _find_outer_select(ast.this)
    if isinstance(ast, exp.SetOperation):
        left = _find_outer_select(ast.left) if hasattr(ast, 'left') else None
        right = _find_outer_select(ast.right) if hasattr(ast, 'right') else None
        return left or right
    for child in ast.iter_expressions():
        found = _find_outer_select(child)
        if found is not None:
            return found
    return None


def _sql_expr_roots_for_plan(ast: exp.Expression, plan: Any) -> List[exp.Expression]:
    """sqlglot expr subtrees that mirror ``_plan_df_exprs`` for this plan node."""
    select = _find_outer_select(ast)
    if select is None:
        return []

    variant = plan.to_variant()
    kind = type(variant).__name__
    roots: List[exp.Expression] = []

    if kind == "Filter":
        where = select.args.get("where")
        if where is not None:
            roots.append(where)
        return roots
    if kind == "Projection":
        roots.extend(select.expressions or [])
        return roots
    if kind == "Aggregate":
        group = select.args.get("group")
        if group is not None:
            if isinstance(group, exp.Group):
                roots.extend(group.expressions or [])
            else:
                roots.append(group)
        roots.extend(select.expressions or [])
        return roots
    if kind == "Join":
        for join in select.args.get("joins") or []:
            on = join.args.get("on")
            if on is None:
                continue
            if isinstance(on, exp.And):
                roots.extend(on.flatten())
            else:
                roots.append(on)
        where = select.args.get("where")
        if where is not None:
            roots.append(where)
        return roots
    if kind == "Window":
        roots.extend(select.expressions or [])
        return roots
    if kind == "Sort":
        order = select.args.get("order")
        if order is not None:
            if isinstance(order, exp.Order):
                roots.extend(order.expressions or [])
            else:
                roots.append(order)
        return roots
    if kind == "Values":
        return list(select.expressions or [])
    if kind == "Repartition":
        return list(select.expressions or [])
    return roots


def _extract_scalar_subquery_sql(
    unparsed_sql: str,
    dialect: str,
    plan: Any,
) -> List[str]:
    ast = sqlglot.parse_one(unparsed_sql, read=dialect)
    emit = DataFusionSessionManager.emit_dialect(dialect)
    fragments: List[str] = []
    for root in _sql_expr_roots_for_plan(ast, plan):
        _walk_sql_expr(
            root,
            lambda node: fragments.append(node.this.sql(dialect=emit)),
        )
    return fragments


def _plannable_sql_fragments(ctx: SessionContext, fragments: Iterable[str]) -> List[str]:
    plannable = []
    for fragment in fragments:
        try:
            ctx.sql(fragment).optimized_logical_plan()
            plannable.append(fragment)
        except Exception:
            continue
    return plannable


@dataclass
class _NodeLowerState:
    """Shared root-plan lowering state for scalar-subquery dependencies."""

    ctx: SessionContext
    dialect: str
    plan: Any
    sql: str = ""
    scalar_subqueries: Dict[str, "Step"] = field(default_factory=dict)
    _scalar_counter: int = 0
    _scalar_sql: Dict[int, Deque[str]] = field(default_factory=dict, repr=False)

    def register_scalar_subquery(self, sq: Any) -> "ScalarSubqueryRef":
        subquery_id = f"sq{self._scalar_counter}"
        self._scalar_counter += 1
        inner_lp = _scalar_inner_logical(sq, self, self.plan)
        self.scalar_subqueries[subquery_id] = _from_logical(
            inner_lp,
            ctx=self.ctx,
            dialect=self.dialect,
            sql=self.sql,
            state=self,
        )
        return ScalarSubqueryRef(this=exp.to_identifier(subquery_id))

    def _extract_subquery_fragments(self, plan: Any, sql: str) -> List[str]:
        ast = sqlglot.parse_one(sql, read=self.dialect)
        emit = DataFusionSessionManager.emit_dialect(self.dialect)
        fragments: List[str] = []
        roots = _sql_expr_roots_for_plan(ast, plan)
        if roots:
            for root in roots:
                _walk_sql_expr(
                    root,
                    lambda node: fragments.append(node.this.sql(dialect=emit)),
                )
        if not fragments:
            for node in ast.walk(bfs=True):
                if isinstance(node, exp.Subquery) and node.this is not None:
                    fragments.append(node.this.sql(dialect=emit))
        return _plannable_sql_fragments(self.ctx, fragments)

    def pop_scalar_sql(self, plan: Any) -> str:
        key = id(plan)
        if key not in self._scalar_sql:
            fragments: List[str] = []
            try:
                source_sql = Unparser(UNPARSER_DIALECTS[self.dialect]).plan_to_sql(
                    plan
                )
                fragments = self._extract_subquery_fragments(plan, source_sql)
            except Exception as exc:
                _logger.warning(
                    "Path A (Unparser roundtrip) failed for plan %s: %s",
                    repr(plan)[:120],
                    exc,
                )
            if not fragments and self.sql:
                fragments = self._extract_subquery_fragments(plan, self.sql)
            self._scalar_sql[key] = deque(fragments)
        queue = self._scalar_sql[key]
        if not queue:
            if self.sql:
                fragments = self._extract_subquery_fragments(plan, self.sql)
                self._scalar_sql[key] = deque(fragments)
                if fragments:
                    return self._scalar_sql[key].popleft()
            raise PlanError("ScalarSubquery SQL fragment queue is empty")
        return queue.popleft()


# ---------------------------------------------------------------------------
# Step IR
# ---------------------------------------------------------------------------


class Step:
    """Base operator node in the DataFusion-aligned plan DAG."""

    def __init__(self) -> None:
        self.name: Optional[exp.Identifier] = None
        self.dependencies: Set[Step] = set()
        self.dependents: Set[Step] = set()
        self.display: str = ""

    def add_dependency(self, dependency: "Step") -> None:
        self.dependencies.add(dependency)
        dependency.dependents.add(self)

    def __repr__(self) -> str:
        return repr_step(self)

    @property
    def type_name(self) -> str:
        return self.__class__.__name__


class ScalarSubqueryRef(exp.Expression):
    """SQLGlot leaf that points to a plan-level scalar-subquery dependency."""

    arg_types = {"this": True}

    @property
    def subquery_id(self) -> str:
        value = self.this
        if isinstance(value, exp.Identifier):
            return value.name
        return str(value)

    def sql(self, *args: Any, **kwargs: Any) -> str:
        return self.subquery_id


class UnsupportedExpression(exp.Expression):
    """Plan expression retained solely to report an unsupported path."""

    arg_types = {"this": True, "expressions": False}

    @property
    def variant(self) -> str:
        return str(self.this)

    def sql(self, *args: Any, **kwargs: Any) -> str:
        return f"UNSUPPORTED_{self.variant.upper()}"


def _generate_scalar_subquery_ref(
    generator: Generator,
    expression: ScalarSubqueryRef,
) -> str:
    del generator
    return expression.subquery_id


def _generate_unsupported_expression(
    generator: Generator,
    expression: UnsupportedExpression,
) -> str:
    del generator
    return f"UNSUPPORTED_{expression.variant.upper()}"


for _generator_class in (Generator, SQLite.Generator, MySQL.Generator, Postgres.Generator):
    _generator_class.TRANSFORMS[ScalarSubqueryRef] = _generate_scalar_subquery_ref
    _generator_class.TRANSFORMS[UnsupportedExpression] = _generate_unsupported_expression


class TableScan(Step):
    def __init__(self) -> None:
        super().__init__()
        self.table: exp.Table = exp.table_("")
        self.source: exp.Expression = exp.table_("")
        self.scan_projections: List[exp.Column] = []


class Filter(Step):
    def __init__(self) -> None:
        super().__init__()
        self.condition: Optional[exp.Expression] = None


class Projection(Step):
    def __init__(self) -> None:
        super().__init__()
        self.projections: Sequence[exp.Expression] = []


class Aggregate(Step):
    def __init__(self) -> None:
        super().__init__()
        self.aggregations: List[exp.Expression] = []
        self.group: List[exp.Expression] = []


class Join(Step):
    def __init__(self) -> None:
        super().__init__()
        # 1. Explicit Topological Pointers (Order matters!)
        self.left: Optional["Step"] = None
        self.right: Optional["Step"] = None

        # 2. Join Type (INNER, LEFT, RIGHT, FULL, SEMI, ANTI)
        self.join_type: str = "INNER"
        # 3. Equi-Join Keys (The ON clause for equality)
        # Stored as a list of tuples: [(left_expr, right_expr), ...]
        # DataFusion uses these specifically for Hash Joins.
        self.on_keys: List[Tuple[exp.Expression, exp.Expression]] = []
        # 4. Non-Equi Join Conditions (e.g., t1.date > t2.date)
        self.condition: Optional[exp.Expression] = None
        self.subquery_kind: Optional[str] = None

    def set_left(self, step: "Step") -> None:
        """Sets the left input and registers the DAG dependency."""
        self.left = step
        self.add_dependency(step)

    def set_right(self, step: "Step") -> None:
        """Sets the right input and registers the DAG dependency."""
        self.right = step
        self.add_dependency(step)


def normalize_join_type(value: str) -> str:
    normalized = "".join(character for character in value.upper() if character.isalnum())
    if normalized.endswith("SEMI"):
        return "SEMI"
    if normalized.endswith("ANTI"):
        return "ANTI"
    if normalized in {"LEFT", "LEFTOUTER"}:
        return "LEFT"
    if normalized in {"RIGHT", "RIGHTOUTER"}:
        return "RIGHT"
    if normalized in {"FULL", "FULLOUTER"}:
        return "FULL"
    if normalized == "INNER":
        return "INNER"
    return normalized


class Sort(Step):
    def __init__(self) -> None:
        super().__init__()
        self.key: List[exp.Expression] = []
        # DF may fuse LIMIT into Sort as fetch=N.
        self.fetch: Optional[int] = None


class Limit(Step):
    def __init__(self) -> None:
        super().__init__()
        self.fetch: Optional[int] = None
        self.offset: Optional[int] = None


class Union(Step):
    def __init__(self) -> None:
        super().__init__()
        self.is_all: bool = True


class Distinct(Step):
    def __init__(self) -> None:
        super().__init__()
        self.is_all: bool = False
        self.on: List[exp.Expression] = []


class Window(Step):
    def __init__(self) -> None:
        super().__init__()
        self.window_exprs: List[exp.Expression] = []


class SubqueryAlias(Step):
    def __init__(self) -> None:
        super().__init__()
        self.alias: exp.Identifier = exp.to_identifier("")


class Values(Step):
    def __init__(self) -> None:
        super().__init__()
        self.values: List[List[exp.Expression]] = []


class EmptyRelation(Step):
    def __init__(self) -> None:
        super().__init__()
        self.produce_one_row: bool = False


class Unnest(Step):
    def __init__(self) -> None:
        super().__init__()
        self.columns: List[exp.Column] = []


class Repartition(Step):
    def __init__(self) -> None:
        super().__init__()
        # e.g., "HASH" or "ROUND_ROBIN"
        self.partitioning_scheme: str = "HASH"
        # The columns being hashed (empty if round-robin)
        self.partition_exprs: List[exp.Expression] = []
        # Number of partitions (useful for cost estimation)
        self.partition_count: Optional[int] = None


class RecursiveQuery(Step):
    def __init__(self) -> None:
        super().__init__()
        self.name: exp.Identifier = exp.to_identifier("")
        # Explicit topological pointers
        self.static_term: Optional["Step"] = None
        self.recursive_term: Optional["Step"] = None

        self.is_distinct: bool = False

    def set_static(self, step: "Step") -> None:
        self.static_term = step
        self.add_dependency(step)

    def set_recursive(self, step: "Step") -> None:
        self.recursive_term = step
        self.add_dependency(step)


class RawStep(Step):
    """DF variants we only keep as display text (Extension, Explain, DDL, …)."""

    def __init__(self, kind: str = "Raw") -> None:
        super().__init__()
        self._kind = kind
        self.raw: str = ""

    @property
    def type_name(self) -> str:
        return self._kind


def repr_step(step: Step, *, indent: int = 0) -> str:
    """Detailed tree representation of a Step operator node."""
    pad = "  " * indent
    inner = "  " * (indent + 1)
    lines = [f"{pad}{step.type_name}("]

    if step.name is not None:
        lines.append(f"{inner}name={step.name.name!r},")

    if isinstance(step, TableScan):
        lines.append(f"{inner}table={repr_expr(step.table)},")
        if step.scan_projections:
            cols = ", ".join(repr_expr(c) for c in step.scan_projections)
            lines.append(f"{inner}scan_projections=[{cols}],")
    elif isinstance(step, Filter):
        if step.condition is not None:
            lines.append(
                f"{inner}condition={repr_expr(step.condition, indent=indent + 1)},"
            )
    elif isinstance(step, Projection):
        if step.projections:
            lines.append(f"{inner}projections=[")
            for projection in step.projections:
                lines.append(f"{inner}  {repr_expr(projection, indent=indent + 2)},")
            lines.append(f"{inner}],")
    elif isinstance(step, Aggregate):
        if step.aggregations:
            lines.append(f"{inner}aggregations=[")
            for aggregation in step.aggregations:
                lines.append(f"{inner}  {repr_expr(aggregation, indent=indent + 2)},")
            lines.append(f"{inner}],")
        if step.group:
            lines.append(f"{inner}group=[")
            for expression in step.group:
                lines.append(f"{inner}  {repr_expr(expression, indent=indent + 2)},")
            lines.append(f"{inner}],")
    elif isinstance(step, Join):
        lines.append(f"{inner}join_type={step.join_type!r},")
        if step.on_keys:
            lines.append(f"{inner}on_keys=[")
            for left_key, right_key in step.on_keys:
                lines.append(
                    f"{inner}  ({repr_expr(left_key)}, {repr_expr(right_key)}),"
                )
            lines.append(f"{inner}],")
        if step.condition is not None:
            lines.append(
                f"{inner}condition={repr_expr(step.condition, indent=indent + 1)},"
            )
    elif isinstance(step, Sort):
        if step.key:
            lines.append(f"{inner}key=[")
            for key in step.key:
                lines.append(f"{inner}  {repr_expr(key, indent=indent + 2)},")
            lines.append(f"{inner}],")
        if step.fetch is not None:
            lines.append(f"{inner}fetch={step.fetch},")
    elif isinstance(step, Limit):
        if step.fetch is not None:
            lines.append(f"{inner}fetch={step.fetch},")
        if step.offset is not None:
            lines.append(f"{inner}offset={step.offset},")
    elif isinstance(step, Union):
        lines.append(f"{inner}is_all={step.is_all},")
    elif isinstance(step, Distinct):
        lines.append(f"{inner}is_all={step.is_all},")
        if step.on:
            lines.append(f"{inner}on=[")
            for expression in step.on:
                lines.append(f"{inner}  {repr_expr(expression, indent=indent + 2)},")
            lines.append(f"{inner}],")
    elif isinstance(step, Window):
        if step.window_exprs:
            lines.append(f"{inner}window_exprs=[")
            for expression in step.window_exprs:
                lines.append(f"{inner}  {repr_expr(expression, indent=indent + 2)},")
            lines.append(f"{inner}],")
    elif isinstance(step, SubqueryAlias):
        lines.append(f"{inner}alias={step.alias.name!r},")
    elif isinstance(step, Values):
        if step.values:
            lines.append(f"{inner}values=[")
            for row in step.values:
                cells = ", ".join(repr_expr(cell) for cell in row)
                lines.append(f"{inner}  [{cells}],")
            lines.append(f"{inner}],")
    elif isinstance(step, EmptyRelation):
        lines.append(f"{inner}produce_one_row={step.produce_one_row},")
    elif isinstance(step, Unnest):
        if step.columns:
            cols = ", ".join(repr_expr(column) for column in step.columns)
            lines.append(f"{inner}columns=[{cols}],")
    elif isinstance(step, Repartition):
        lines.append(f"{inner}partitioning_scheme={step.partitioning_scheme!r},")
        if step.partition_exprs:
            lines.append(f"{inner}partition_exprs=[")
            for expression in step.partition_exprs:
                lines.append(f"{inner}  {repr_expr(expression, indent=indent + 2)},")
            lines.append(f"{inner}],")
        if step.partition_count is not None:
            lines.append(f"{inner}partition_count={step.partition_count},")
    elif isinstance(step, RecursiveQuery):
        lines.append(f"{inner}is_distinct={step.is_distinct},")
        if step.static_term is not None:
            lines.append(f"{inner}static_term=")
            lines.append(repr_step(step.static_term, indent=indent + 1) + ",")
        if step.recursive_term is not None:
            lines.append(f"{inner}recursive_term=")
            lines.append(repr_step(step.recursive_term, indent=indent + 1) + ",")
    elif isinstance(step, RawStep) and step.raw:
        lines.append(f"{inner}raw={step.raw!r},")

    if step.dependencies:
        lines.append(f"{inner}dependencies=[")
        for dependency in step.dependencies:
            lines.append(repr_step(dependency, indent=indent + 2) + ",")
        lines.append(f"{inner}],")

    if step.display:
        lines.append(f"{inner}display={step.display!r},")

    lines.append(f"{pad})")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Variant → Step builders
# ---------------------------------------------------------------------------

Builder = Callable[
    [Any, List[Step], str, List[Tuple[str, str, bool]], _NodeLowerState], Step
]


def _wire(step: Step, children: List[Step], display: str) -> Step:
    step.display = display or ""
    for child in children:
        step.add_dependency(child)
    return step


def _build_table_scan(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del state
    step = TableScan()
    fqn = variant.fqn()
    catalog = schema = None
    table_name = variant.table_name()
    if isinstance(fqn, tuple) and len(fqn) >= 3:
        catalog, schema, table_name = fqn[0], fqn[1], fqn[2] or table_name
    db = schema or catalog
    step.table = exp.table_(table_name, db=db)
    step.source = step.table.copy()
    step.name = exp.to_identifier(table_name)
    step.scan_projections = [
        exp.column(
            name if not isinstance(name, tuple) else name[-1],
            table=table_name,
        )
        for name in (variant.projection() or [])
    ]
    _annotate_exprs(step.scan_projections, fields, what="TableScan")
    return _wire(step, children, display)


def _build_filter(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del fields
    step = Filter()
    step.condition = _annotate_type(
        to_expression(variant.predicate(), state=state),
        "Boolean",
        nullable=False,
    )
    return _wire(step, children, display)


def _build_projection(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    step = Projection()
    step.projections = [
        to_expression(p, state=state) for p in (variant.projections() or [])
    ]
    _annotate_exprs(step.projections, fields, what="Projection")
    return _wire(step, children, display)


def _build_aggregate(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    step = Aggregate()
    step.group = [
        to_expression(g, state=state) for g in (variant.group_by_exprs() or [])
    ]
    step.aggregations = [
        to_expression(a, state=state) for a in (variant.aggregate_exprs() or [])
    ]
    outputs = list(step.group) + list(step.aggregations)
    _annotate_exprs(outputs, fields, what="Aggregate")
    return _wire(step, children, display)


def _build_join(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del fields  # Join output schema is the concatenated input schemas.
    if len(children) < 2:
        raise PlanError(
            f"Join expects 2 children, got {len(children)}: {display!r}"
        )
    step = Join()
    if hasattr(variant, "join_type"):
        step.join_type = str(variant.join_type()).split(".")[-1]
    else:
        step.join_type = "CROSS"

    step.set_left(children[0])
    step.set_right(children[1])

    on_pairs: List[Tuple[exp.Expression, exp.Expression]] = []
    if hasattr(variant, "on"):
        on_pairs = [
            (to_expression(left, state=state), to_expression(right, state=state))
            for left, right in (variant.on() or [])
        ]
    step.on_keys = on_pairs

    filt = variant.filter() if hasattr(variant, "filter") else None
    if filt is not None:
        step.condition = _annotate_type(
            to_expression(filt, state=state),
            "Boolean",
            nullable=False,
        )
    return _wire(step, [], display)


def _build_sort(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del fields
    step = Sort()
    step.key = [
        exp.Ordered(
            this=to_expression(sort_expr.expr(), state=state),
            desc=not bool(sort_expr.ascending()),
        )
        for sort_expr in (variant.sort_exprs() or [])
    ]
    fetch = variant.get_fetch_val()
    if fetch is not None:
        step.fetch = int(fetch)
    return _wire(step, children, display)


def _build_limit(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del variant, fields, state  # DF Limit exposes only input/schema in Python bindings.
    step = Limit()
    match = _LIMIT_RE.search(display or "")
    if not match:
        raise PlanError(
            f"Limit node missing skip/fetch in display {display!r}; "
            "DataFusion Python bindings do not expose Limit accessors"
        )
    step.offset = int(match.group(1))
    step.fetch = int(match.group(2))
    return _wire(step, children, display)


def _build_union(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del variant, fields, state
    step = Union()
    # DataFusion emits Aggregate for DISTINCT UNION; Union node is UNION ALL.
    step.is_all = True
    return _wire(step, children, display)


def _build_distinct(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del variant, fields, state
    step = Distinct()
    step.is_all = False
    return _wire(step, children, display)


def _build_window(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    step = Window()
    step.window_exprs = [
        to_expression(w, window_node=variant, state=state)
        for w in (variant.get_window_expr() or [])
    ]
    # Schema is input columns followed by window exprs.
    n = len(step.window_exprs)
    if n > len(fields):
        raise PlanError(
            f"Window: {n} window exprs but only {len(fields)} schema fields"
        )
    _annotate_exprs(step.window_exprs, fields[-n:], what="Window")
    return _wire(step, children, display)


def _build_subquery_alias(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del fields, state
    step = SubqueryAlias()
    if hasattr(variant, "alias"):
        alias = variant.alias()
        if alias:
            step.alias = exp.to_identifier(alias)
            step.name = step.alias
    return _wire(step, children, display)


def _build_values(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    step = Values()
    step.values = [
        [to_expression(cell, state=state) for cell in row]
        for row in (variant.values() or [])
    ]
    for row in step.values:
        _annotate_exprs(row, fields, what="Values")
    return _wire(step, children, display)


def _build_empty_relation(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del fields, state
    step = EmptyRelation()
    step.produce_one_row = bool(variant.produce_one_row())
    return _wire(step, children, display)


def _build_unnest(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del state
    step = Unnest()
    step.columns = [exp.to_column(c) for c in (variant.columns() or [])]
    _annotate_exprs(step.columns, fields, what="Unnest")
    return _wire(step, children, display)


def _partitioning_scheme_name(partitioning: Any) -> str:
    name = type(partitioning).__name__
    upper = name.upper()
    if "ROUND" in upper:
        return "ROUND_ROBIN"
    if "HASH" in upper:
        return "HASH"
    return upper or "HASH"


def _build_repartition(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del fields
    step = Repartition()
    partitioning = variant.partitioning()
    step.partitioning_scheme = _partitioning_scheme_name(partitioning)
    exprs: List[Any] = []
    if hasattr(partitioning, "expr"):
        raw = partitioning.expr()
        if raw is not None:
            exprs = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    elif hasattr(partitioning, "exprs"):
        exprs = list(partitioning.exprs() or [])
    elif hasattr(partitioning, "columns"):
        exprs = list(partitioning.columns() or [])
    step.partition_exprs = [to_expression(e, state=state) for e in exprs]
    if hasattr(partitioning, "partition_count"):
        step.partition_count = int(partitioning.partition_count())
    elif hasattr(variant, "partition_count"):
        step.partition_count = int(variant.partition_count())
    return _wire(step, children, display)


def _build_raw(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del fields, state
    step = RawStep(type(variant).__name__)
    step.raw = display or ""
    return _wire(step, children, display)


def _build_recursive_query(
    variant: Any,
    children: List[Step],
    display: str,
    fields: List[Tuple[str, str, bool]],
    state: _NodeLowerState,
) -> Step:
    del fields, state
    step = RecursiveQuery()
    if hasattr(variant, "name"):
        name = variant.name()
        if name:
            step.name = exp.to_identifier(name)
    if hasattr(variant, "is_distinct"):
        step.is_distinct = bool(variant.is_distinct())
    if len(children) >= 2:
        step.set_static(children[0])
        step.set_recursive(children[1])
    elif children:
        step.set_static(children[0])
    return _wire(step, [], display)


_VARIANT_BUILDERS: Dict[str, Builder] = {
    "TableScan": _build_table_scan,
    "Filter": _build_filter,
    "Projection": _build_projection,
    "Aggregate": _build_aggregate,
    "Join": _build_join,
    "CrossJoin": _build_join,
    "Sort": _build_sort,
    "Limit": _build_limit,
    "Union": _build_union,
    "Distinct": _build_distinct,
    "Window": _build_window,
    "WindowExpr": _build_window,
    "SubqueryAlias": _build_subquery_alias,
    "Subquery": _build_subquery_alias,
    "Values": _build_values,
    "EmptyRelation": _build_empty_relation,
    "Unnest": _build_unnest,
    "Repartition": _build_repartition,
    "RecursiveQuery": _build_recursive_query,
    "Extension": _build_raw,
    "Explain": _build_raw,
    "Analyze": _build_raw,
    "CreateMemoryTable": _build_raw,
    "CreateView": _build_raw,
    "DropTable": _build_raw,
    "CopyTo": _build_raw,
    "DmlStatement": _build_raw,
    "Prepare": _build_raw,
}


def _from_logical(
    plan: Any,
    *,
    ctx: SessionContext,
    dialect: str,
    sql: str = "",
    state: Optional[_NodeLowerState] = None,
) -> Step:
    if state is None:
        state = _NodeLowerState(ctx=ctx, dialect=dialect, plan=plan, sql=sql)
    variant = plan.to_variant()
    op = type(variant).__name__
    display = plan.display()
    fields = _parse_plan_schema(plan)
    children = [
        _from_logical(child, ctx=ctx, dialect=dialect, sql=sql, state=state)
        for child in (plan.inputs() or [])
    ]
    builder = _VARIANT_BUILDERS.get(op)
    if builder is None:
        raise PlanError(
            f"unsupported DataFusion logical plan variant {op!r}: {display}"
        )
    previous_plan = state.plan
    state.plan = plan
    try:
        return builder(variant, children, display, fields, state)
    finally:
        state.plan = previous_plan


# ---------------------------------------------------------------------------
# Plan + public API
# ---------------------------------------------------------------------------


class Plan:
    """Root container for a DataFusion-lowered Step DAG."""

    def __init__(
        self,
        root: Step,
        *,
        sql: str,
        dialect: str,
        logical_display: str = "",
        scalar_subqueries: Optional[Mapping[str, Step]] = None,
    ) -> None:
        self.root = root
        self.sql = sql
        self.dialect = dialect
        self.logical_display = logical_display
        self.scalar_subqueries: Dict[str, Step] = dict(scalar_subqueries or {})
        self._dag: Dict[Step, Set[Step]] = {}

    @property
    def dag(self) -> Dict[Step, Set[Step]]:
        if not self._dag:
            dag: Dict[Step, Set[Step]] = {}
            nodes = {self.root}
            while nodes:
                node = nodes.pop()
                if node in dag:
                    continue
                dag[node] = set(node.dependencies)
                nodes.update(node.dependencies)
            self._dag = dag
        return self._dag

    @property
    def leaves(self) -> Iterable[Step]:
        return (node for node, deps in self.dag.items() if not deps)

    def __repr__(self) -> str:
        return f"Plan(\n{repr_step(self.root, indent=1)}\n)"


def explain(
    ddl: str,
    query: str,
    dialect: str = "sqlite",
    session: Optional[DataFusionSessionManager] = None,
) -> Plan:
    """Lower ``query`` (under ``ddl``) to a DataFusion Step DAG.

    If ``session`` is provided, use it (so callers can
    :meth:`~DataFusionSessionManager.register_scalar_udf` first). Otherwise
    construct a default :class:`DataFusionSessionManager` for ``dialect``.
    """
    if session is None:
        session = DataFusionSessionManager(dialect)
    else:
        dialect = session.dialect
    df_sql = session.bootstrap(ddl, query)
    ctx = session.context
    df = ctx.sql(df_sql)
    logical = df.optimized_logical_plan()
    state = _NodeLowerState(ctx=ctx, dialect=dialect, plan=logical, sql=df_sql)
    root = _from_logical(logical, ctx=ctx, dialect=dialect, sql=df_sql, state=state)
    plan = Plan(
        root,
        sql=df_sql,
        dialect=dialect,
        logical_display=str(logical),
        scalar_subqueries=state.scalar_subqueries,
    )
    _annotate_subquery_joins(plan, query)
    return plan


def _annotate_subquery_joins(plan: Plan, query: str) -> None:
    tree = sqlglot.parse_one(query, read=plan.dialect)
    kinds: list[str] = []
    for node in tree.walk():
        if isinstance(node, exp.Exists):
            kinds.append("not_exists" if isinstance(node.parent, exp.Not) else "exists")
        elif isinstance(node, exp.In) and node.args.get("query") is not None:
            kinds.append("not_in" if isinstance(node.parent, exp.Not) else "in")

    joins = [
        step
        for step in _all_reachable_steps(plan.root)
        if isinstance(step, Join) and normalize_join_type(step.join_type) in {"SEMI", "ANTI"}
    ]
    compatible = {
        "SEMI": {"exists", "in"},
        "ANTI": {"not_exists", "not_in"},
    }
    assigned: set[int] = set()
    for join in joins:
        right = join.right
        if not isinstance(right, SubqueryAlias):
            continue
        match = re.fullmatch(r"__correlated_sq_(\d+)", right.alias.name)
        if match is None:
            continue
        index = int(match.group(1)) - 1
        if index < 0 or index >= len(kinds):
            continue
        kind = kinds[index]
        if kind in compatible[normalize_join_type(join.join_type)]:
            join.subquery_kind = kind
            assigned.add(index)

    remaining = [kind for index, kind in enumerate(kinds) if index not in assigned]
    for join in sorted(joins, key=lambda item: item.display):
        if join.subquery_kind is not None:
            continue
        allowed = compatible[normalize_join_type(join.join_type)]
        index = next((i for i, kind in enumerate(remaining) if kind in allowed), None)
        if index is not None:
            join.subquery_kind = remaining.pop(index)


def assert_no_bare_string_identifiers(plan: Plan) -> None:
    """Raise ``AssertionError`` if semantic Step fields hold bare ``str`` ids."""
    roots = [plan.root, *plan.scalar_subqueries.values()]
    seen: Set[int] = set()
    steps: List[Step] = []
    for root in roots:
        for step in _all_reachable_steps(root):
            if id(step) in seen:
                continue
            seen.add(id(step))
            steps.append(step)
    for step in steps:
        for attr in _IDENT_ATTRS:
            if not hasattr(step, attr):
                continue
            value = getattr(step, attr)
            _assert_ident_value(step, attr, value)


def _assert_ident_value(step: Step, attr: str, value: Any) -> None:
    if value is None:
        return
    if attr in {"limit", "fetch", "offset", "distinct", "produce_one_row"}:
        return
    if isinstance(value, str):
        raise AssertionError(
            f"{type(step).__name__}.{attr} is bare str {value!r}; expected Expression"
        )
    if isinstance(value, Step):
        return
    if isinstance(value, exp.Expression):
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                raise AssertionError(
                    f"{type(step).__name__}.{attr} has bare str key {key!r}"
                )
            if not isinstance(key, (exp.Expression, Step)):
                raise AssertionError(
                    f"{type(step).__name__}.{attr} key must be Expression, got {type(key)}"
                )
            _assert_ident_value(step, attr, item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            if isinstance(item, str):
                raise AssertionError(
                    f"{type(step).__name__}.{attr} list contains bare str {item!r}"
                )
            if isinstance(item, (list, tuple)):
                for cell in item:
                    if isinstance(cell, str):
                        raise AssertionError(
                            f"{type(step).__name__}.{attr} nested list has bare str"
                        )
                    if cell is not None and not isinstance(
                        cell, (exp.Expression, Step, int, float, bool)
                    ):
                        raise AssertionError(
                            f"{type(step).__name__}.{attr} nested cell must be Expression"
                        )
            elif item is not None and not isinstance(
                item, (exp.Expression, Step, int, float, bool)
            ):
                raise AssertionError(
                    f"{type(step).__name__}.{attr} has non-Expression {type(item)}"
                )
        return
