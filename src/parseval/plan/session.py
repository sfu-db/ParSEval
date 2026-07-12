"""DataFusion session façade: dialect, DDL sanitize, query rewrite, UDFs.

Public entry point is :class:`DataFusionSessionManager`. The coverage CLI in
``scripts/explain.py`` and the Step-IR planner in ``explain`` both go through it.
"""

from __future__ import annotations

import re
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,    
)

import pyarrow as pa
import sqlglot
from datafusion import SessionConfig, SessionContext
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.optimizer.normalize_identifiers import normalize_identifiers

from parseval.plan import udf as udf_api

DEFAULT_SOURCE_DIALECT = "sqlite"

# Column types DF CREATE TABLE rejects or mishandles → replacement Type
_TYPE_MAP = {
    exp.DataType.Type.DATETIME: exp.DataType.Type.TIMESTAMP,
    exp.DataType.Type.VARCHAR: exp.DataType.Type.TEXT,
    exp.DataType.Type.NVARCHAR: exp.DataType.Type.TEXT,
    exp.DataType.Type.ENUM: exp.DataType.Type.TEXT,
    exp.DataType.Type.ENUM8: exp.DataType.Type.TEXT,
    exp.DataType.Type.ENUM16: exp.DataType.Type.TEXT,
}

_DF_RESERVED = {
    str(token).lower()
    for dialect_name in ("postgres", "mysql", "sqlite")
    for token in Dialect.get_or_raise(dialect_name).tokenizer.KEYWORDS
}

_DEFAULT_SESSION_CONFIGS = {
    "datafusion.catalog.information_schema": "true",
    "datafusion.optimizer.skip_failed_rules": "false",
    "datafusion.optimizer.max_passes": "5",
    "datafusion.optimizer.filter_null_join_keys": "true",
}

_TABLE_CONSTRAINTS_DROP = (
    exp.ForeignKey,
    exp.PrimaryKey,
    exp.Check,
    exp.CheckColumnConstraint,
    exp.Index,
    exp.IndexColumnConstraint,
    exp.Constraint,
)

_COLUMN_CONSTRAINTS_DROP = (
    exp.Reference,
    exp.AutoIncrementColumnConstraint,
    exp.CheckColumnConstraint,
    exp.IndexColumnConstraint,
)

def _quote_datafusion_identifiers(tree: exp.Expression) -> exp.Expression:
    """Quote identifiers DF would treat as reserved keywords."""
    for ident in tree.find_all(exp.Identifier):
        name = ident.this
        if not name:
            continue
        if (
            ident.args.get("quoted")
            or " " in name
            or not name.replace("_", "").isalnum()
            or name.lower() in _DF_RESERVED
        ):
            ident.set("quoted", True)
    return tree


def _truthy_column(node: exp.Expression) -> exp.Expression:
    return exp.NEQ(this=node.copy(), expression=exp.Literal.number(0))


def _maybe_truthy_bool_child(child: Optional[exp.Expression]) -> Optional[exp.Expression]:
    if isinstance(child, exp.Column):
        return _truthy_column(child)
    if isinstance(child, exp.Paren) and isinstance(child.this, exp.Column):
        return exp.Paren(this=_truthy_column(child.this))
    return child


def _cast_strftime_side(child: Optional[exp.Expression]) -> Optional[exp.Expression]:
    if child is None or isinstance(child, exp.Cast):
        return child
    if _is_strftime_like(child):
        return exp.Cast(this=child.copy(), to=exp.DataType.build("DOUBLE"))
    return child


def _is_strftime_like(node: Optional[exp.Expression]) -> bool:
    if node is None:
        return False
    if isinstance(node, exp.TimeToStr):
        return True
    if isinstance(node, exp.Anonymous) and str(node.this).lower() == "strftime":
        return True
    return False


def _subquery_select(node: exp.Expression) -> Optional[exp.Select]:
    if isinstance(node, exp.Subquery):
        return _subquery_select(node.this)
    if isinstance(node, exp.Select):
        return node
    return None


def _tuple_in_to_exists(inn: exp.In) -> Optional[exp.Expression]:
    """Build EXISTS rewrite for multi-column ``IN (SELECT …)``, or ``None``."""
    left = inn.this
    if not isinstance(left, exp.Tuple):
        return None
    left_exprs = list(left.expressions)
    if len(left_exprs) < 2:
        return None

    subquery = inn.args.get("query")
    select = _subquery_select(subquery) if subquery is not None else None
    if select is None:
        return None
    right_exprs = list(select.expressions or [])
    if len(right_exprs) != len(left_exprs):
        return None

    inner = select.copy()
    col_aliases: List[str] = []
    new_projections: List[exp.Expression] = []
    for i, item in enumerate(right_exprs):
        alias = f"_c{i}"
        col_aliases.append(alias)
        if isinstance(item, exp.Alias):
            new_projections.append(exp.alias_(item.this.copy(), alias, quoted=False))
        else:
            new_projections.append(exp.alias_(item.copy(), alias, quoted=False))
    inner.set("expressions", new_projections)

    derived = exp.Subquery(
        this=inner,
        alias=exp.TableAlias(this=exp.to_identifier("_sq")),
    )
    eqs = [
        exp.EQ(
            this=lhs.copy(),
            expression=exp.column(alias, table="_sq"),
        )
        for lhs, alias in zip(left_exprs, col_aliases)
    ]
    correlation = eqs[0]
    for eq in eqs[1:]:
        correlation = exp.and_(correlation, eq)

    exists: exp.Expression = exp.Exists(
        this=exp.Select()
        .select(exp.Literal.number(1))
        .from_(derived)
        .where(correlation)
    )
    if inn.args.get("not"):
        exists = exp.Not(this=exists)
    return exists


def _ensure_group_covers_select(select: exp.Select) -> exp.Select:
    """MySQL loose GROUP BY: add non-aggregated SELECT exprs to GROUP BY."""
    group = select.args.get("group")
    if group is None:
        return select
    group_exprs = (
        list(group.expressions or []) if isinstance(group, exp.Group) else [group]
    )
    group_sql = {e.sql() for e in group_exprs}
    missing: List[exp.Expression] = []
    for proj in select.expressions or []:
        expr = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(expr, exp.Star):
            continue
        if (
            isinstance(expr, (exp.AggFunc, exp.Window))
            or expr.find(exp.AggFunc) is not None
            or expr.find(exp.Window) is not None
        ):
            continue
        if expr.sql() in group_sql:
            continue
        if isinstance(expr, (exp.Literal, exp.Boolean, exp.Null)):
            continue
        missing.append(expr.copy())
        group_sql.add(expr.sql())
    if not missing:
        return select
    new = select.copy()
    group = new.args.get("group")
    if isinstance(group, exp.Group):
        group.set("expressions", list(group.expressions or []) + missing)
    else:
        new.set("group", exp.Group(expressions=group_exprs + missing))
    return new


# --- Node transformers for ``Expression.transform`` (return replacement or node) ---


def _rewrite_agg_boolean(node: exp.Expression) -> exp.Expression:
    """``SUM/AVG(predicate)`` → numeric 0/1 ``CASE`` argument."""
    if not isinstance(node, (exp.Sum, exp.Avg)):
        return node
    arg = node.this
    if arg is None or not isinstance(
        arg, (exp.Predicate, exp.Connector, exp.Not, exp.Boolean)
    ):
        return node
    new = node.copy()
    new.set(
        "this",
        exp.Case(
            ifs=[exp.If(this=arg.copy(), true=exp.Literal.number(1))],
            default=exp.Literal.number(0),
        ),
    )
    return new


def _rewrite_if_to_case(node: exp.Expression) -> exp.Expression:
    """Standalone ``IF``/``IIF`` → ``CASE`` so sqlite emit is not ``IIF(...)``."""
    if not isinstance(node, exp.If) or isinstance(node.parent, exp.Case):
        return node
    true = node.args.get("true")
    false = node.args.get("false")
    return exp.Case(
        ifs=[
            exp.If(
                this=node.this.copy() if node.this is not None else None,
                true=true.copy() if true is not None else None,
            )
        ],
        default=false.copy() if false is not None else None,
    )


def _rewrite_strftime_arithmetic(node: exp.Expression) -> exp.Expression:
    """``strftime('%Y', …) - strftime(...)`` → cast sides to DOUBLE."""
    if not isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Div)):
        return node
    left = _cast_strftime_side(node.this)
    right = _cast_strftime_side(node.args.get("expression"))
    if left is node.this and right is node.args.get("expression"):
        return node
    new = node.copy()
    if left is not None:
        new.set("this", left)
    if right is not None:
        new.set("expression", right)
    return new


def _rewrite_tuple_in(node: exp.Expression) -> exp.Expression:
    """``(a, b) IN (SELECT …)`` → correlated ``EXISTS`` (DF rejects multi-column IN)."""
    if not isinstance(node, exp.In):
        return node
    return _tuple_in_to_exists(node) or node


def _rewrite_bare_predicate_columns(node: exp.Expression) -> exp.Expression:
    """Bare columns in boolean contexts → ``col <> 0``."""
    if isinstance(node, (exp.And, exp.Or, exp.Xor)):
        left = _maybe_truthy_bool_child(node.this)
        right = _maybe_truthy_bool_child(node.args.get("expression"))
        if left is node.this and right is node.args.get("expression"):
            return node
        new = node.copy()
        if left is not None:
            new.set("this", left)
        if right is not None:
            new.set("expression", right)
        return new
    if isinstance(node, (exp.Not, exp.Where, exp.Having)):
        child = _maybe_truthy_bool_child(node.this)
        if child is node.this or child is None:
            return node
        new = node.copy()
        new.set("this", child)
        return new
    return node


def _rewrite_group_by_select_list(node: exp.Expression) -> exp.Expression:
    """MySQL loose GROUP BY: add non-aggregated SELECT exprs to GROUP BY."""
    if not isinstance(node, exp.Select):
        return node
    return _ensure_group_covers_select(node)


def _rewrite_like_operands(node: exp.Expression) -> exp.Expression:
    """Cast LIKE sides to TEXT so mixed-type LIKE plans under DataFusion."""
    if not isinstance(node, (exp.Like, exp.ILike)):
        return node
    changed = False
    new = node.copy()
    for key in ("this", "expression"):
        child = new.args.get(key)
        if child is None or isinstance(child, exp.Cast):
            continue
        new.set(key, exp.Cast(this=child.copy(), to=exp.DataType.build("TEXT")))
        changed = True
    return new if changed else node


_REWRITE_PASSES = (
    _rewrite_agg_boolean,
    _rewrite_if_to_case,
    _rewrite_strftime_arithmetic,
    _rewrite_tuple_in,
    _rewrite_bare_predicate_columns,
    _rewrite_group_by_select_list,
    _rewrite_like_operands,
)


def _to_datafusion_sql(sql: str, *, source_dialect: str) -> str:
    tree = sqlglot.parse_one(sql, read=source_dialect)
    tree = normalize_identifiers(tree, dialect=source_dialect)
    tree = _quote_datafusion_identifiers(tree)
    for pass_fn in _REWRITE_PASSES:
        tree = tree.transform(pass_fn)
    return tree.sql(dialect=DataFusionSessionManager.parser_dialect(source_dialect))


def _strip_ddl_node(node: exp.Expression) -> Optional[exp.Expression]:
    """Drop DF-unsupported constraints; remap column types. Used with ``transform``."""
    if isinstance(node, _TABLE_CONSTRAINTS_DROP):
        return None
    if isinstance(node, exp.ColumnConstraint) and isinstance(
        node.kind, _COLUMN_CONSTRAINTS_DROP
    ):
        return None
    if isinstance(node, exp.ColumnDef) and isinstance(node.kind, exp.DataType):
        mapped = _TYPE_MAP.get(node.kind.this)
        if mapped is not None:
            node.set("kind", exp.DataType(this=mapped))
    return node


def _sanitize_create_table(
    tree: exp.Expression, *, source_dialect: str
) -> Optional[str]:
    """Strip DF-unsupported constraints/types; emit for the session parser."""
    if not isinstance(tree, exp.Create) or tree.find(exp.Schema) is None:
        return None

    tree = tree.transform(_strip_ddl_node)
    tree = normalize_identifiers(tree, dialect=source_dialect)
    tree = _quote_datafusion_identifiers(tree)
    emit = DataFusionSessionManager.parser_dialect(source_dialect)
    sql = tree.sql(dialect=emit)
    if emit == "mysql":
        sql = re.sub(r"\bDATETIME\b", "TIMESTAMP", sql)
    return sql


def _iter_schema_ddl(entry: Any, *, source_dialect: str) -> Iterable[str]:
    if isinstance(entry, str):
        statements: Sequence[Any] = sqlglot.parse(entry, read=source_dialect)
    elif isinstance(entry, Sequence) and not isinstance(entry, (str, bytes)):
        statements = entry
    else:
        raise TypeError(f"unsupported_schema_entry:{type(entry)!r}")

    for stmt in statements:
        if stmt is None:
            continue
        if isinstance(stmt, exp.Expression):
            rewritten = _sanitize_create_table(stmt, source_dialect=source_dialect)
            if rewritten is not None:
                yield rewritten
            continue
        text = str(stmt).strip()
        if not text:
            continue
        tree = sqlglot.parse_one(text, read=source_dialect)
        rewritten = _sanitize_create_table(tree, source_dialect=source_dialect)
        if rewritten is None:
            raise ValueError(f"not_a_create_table:{text[:80]!r}")
        yield rewritten


class DataFusionSessionManager:
    """Centralized DataFusion session: DDL, query rewrite, and UDF registration."""

    def __init__(
        self,
        dialect: str = DEFAULT_SOURCE_DIALECT,
        config_options: Optional[Dict[str, str]] = None,
    ) -> None:
        self.dialect = dialect
        configs = {
            **_DEFAULT_SESSION_CONFIGS,
            "datafusion.sql_parser.dialect": DataFusionSessionManager.parser_dialect(
                dialect
            ),
            **(config_options or {}),
        }
        config = SessionConfig()
        for key, value in configs.items():
            config.set(key, str(value))
        self._ctx = SessionContext(config)
        udf_api.register_predefined_udfs(self._ctx)

    @property
    def context(self) -> SessionContext:
        return self._ctx

    @staticmethod
    def parser_dialect(source_dialect: str) -> str:
        """DataFusion ``sql_parser.dialect`` / sqlglot emit dialect for ``source_dialect``."""
        key = source_dialect.strip().lower()
        return "postgres" if key == "postgresql" else key

    emit_dialect = parser_dialect

    def execute_ddl(self, ddl: str | Sequence[str]) -> None:
        """Sanitize and execute CREATE TABLE DDL against this session."""
        for stmt in _iter_schema_ddl(ddl, source_dialect=self.dialect):
            self._ctx.sql(stmt)

    def prepare_query(self, sql: str) -> str:
        """Rewrite ``sql`` into a form DataFusion can parse under this dialect."""
        return _to_datafusion_sql(sql, source_dialect=self.dialect)

    def register_scalar_udf(
        self,
        name: str,
        func: Callable,
        input_types: Sequence[pa.DataType],
        return_type: pa.DataType,
        volatility: str = "immutable",
    ) -> None:
        """Register a user scalar UDF (overrides a predefined stub of the same name)."""
        udf_api.register_scalar_udf(
            self._ctx,
            name,
            func,
            input_types,
            return_type,
            volatility=volatility,
        )

    def bootstrap(self, ddl: str, query: str) -> str:
        """Load DDL, rewrite query, and validate planning.

        Returns the DataFusion-ready SQL. Planning stubs come from
        :data:`parseval.plan.udf.PREDEFINED_PLANNING_UDFS` (registered at init).
        """
        if ddl and str(ddl).strip():
            self.execute_ddl(ddl)
        df_sql = self.prepare_query(query)
        self._ctx.sql(df_sql)
        return df_sql
