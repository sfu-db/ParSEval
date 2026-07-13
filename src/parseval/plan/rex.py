"""Concrete evaluation of sqlglot expressions under a ParSEval Environment.

Single path from an AST node plus :class:`Environment` to a Python value.
Used by Domain CHECK, CSP constraint checking, and Instance cell vocabulary.

* **Tri-state Symbol** (``Variable`` / ``Const``): unbound / NULL / bound.
* **Environment**: Identifier-keyed row maps and/or ``SolverVar`` assignments.
* **Class-dispatched handlers** with SQL 3VL; CSP-aligned ``=`` / ``!=`` on NULL.
"""

from __future__ import annotations

import functools
import math
import re
from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple, Type, Union

from dateutil import parser as date_parser
from sqlglot import exp, generator
from sqlglot.executor.env import ENV as _SQLGLOT_ENV
from sqlglot.optimizer.simplify import simplify

from parseval.coercion import coerce_literal_value, CoercionError
from parseval.dtype import DataType, StorageLiteral, parse_date, parse_datetime, parse_time
from parseval.helper import like_to_pattern
from parseval.literals import integer_literal, literal_value, unit_name
from parseval.solver.types import SolverVar
from parseval.solver.normalization import unwrap_planning_temporal_arg

from .context import Row  # noqa: F401


# =============================================================================
# Symbol hierarchy
# =============================================================================


class Symbol(exp.Expression):
    """Base class for every ParSEval value node.

    Subclasses inherit :class:`sqlglot.exp.Expression` so they can embed
    naturally as leaves of an AST; they share the tri-state ``is_bound``
    / ``is_null`` convention and carry a ParSEval :class:`DataType`.

    ``Symbol`` itself is abstract in the usage sense — callers should
    always construct :class:`Const` (for literals) or :class:`Variable`
    (for cell identities).
    """

    arg_types = {
        "this": True,
        "type": False,
        "concrete": False,
        "is_bound": False,
        "is_null": False,
        "source": False,
    }

    @property
    def type(self) -> Optional[DataType]:  # type: ignore[override]
        return self.args.get("type")

    @type.setter
    def type(self, value: Optional[DataType]) -> None:  # type: ignore[override]
        self.set("type", value)

    @property
    def is_bound(self) -> bool:
        return bool(self.args.get("is_bound", False))

    @property
    def is_null(self) -> bool:
        return bool(self.args.get("is_null", False))

    @property
    def source(self) -> Optional[str]:
        return self.args.get("source")

    def sql(self, dialect=None, **opts):  # pragma: no cover - pretty-printer
        return f"{self.key}({self.this})"


class Const(Symbol):
    """A literal value with attached :class:`DataType` and coercion support.

    Unlike :class:`sqlglot.exp.Literal` (raw from the parser), a ``Const``
    carries a resolved ParSEval ``DataType`` and knows how to convert
    itself via :meth:`coerce_to`. Always ``is_bound=True``; may be NULL
    via :meth:`null`.
    """

    arg_types = {
        "this": True,
        "type": False,
        "is_bound": False,
        "is_null": False,
        "source": False,
    }

    def __init__(self, *args, **kwargs):
        # Legacy kwargs used by the older API.
        if "_type" in kwargs:
            kwargs["type"] = kwargs.pop("_type")
        # A Const with ``this=None`` means SQL NULL unless explicitly flagged.
        if "is_null" not in kwargs:
            kwargs["is_null"] = kwargs.get("this") is None
        kwargs.setdefault("is_bound", True)
        super().__init__(*args, **kwargs)

    @property
    def concrete(self) -> Any:
        return self.this

    @property
    def value(self) -> Any:
        # Legacy accessor.
        return self.this

    @classmethod
    def null(cls, type: Optional[DataType] = None) -> "Const":
        """Return a Const representing SQL NULL with the given type."""
        return cls(this=None, type=type, is_null=True)

    def coerce_to(
        self, target: Union[DataType, str], dialect: str = "sqlite"
    ) -> "Const":
        """Return a new Const whose value has been coerced to ``target``.

        NULLs short-circuit to :meth:`null`. Same-type coercions are
        identity. Failures (e.g. strict parsing of a non-numeric string
        into INT) return :meth:`null` under strict dialects and
        best-effort values under lenient ones.
        """
        target_dt = target if isinstance(target, DataType) else DataType.build(target)
        if self.is_null:
            return Const.null(target_dt)
        if self.type is not None and self.type == target_dt:
            return self
        coerced = _coerce_value(self.this, self.type, target_dt, dialect=dialect)
        return Const(this=coerced, type=target_dt)


class Variable(Symbol):
    """A cell identity: one column-value in one row of one table.

    Requires sqlglot ``table`` / ``column`` / ``rowid`` identity back-pointers.
    """

    arg_types = {
        "this": True,  # stable name, e.g. ``"T_0003_x"``
        "type": False,
        "concrete": False,
        "is_bound": False,
        "is_null": False,
        "table": False,
        "column": False,
        "rowid": False,
        "nullable": False,
        "unique": False,
        "domain": False,
        "source": False,
    }

    def __init__(self, *args, **kwargs):
        if "_type" in kwargs:
            kwargs["type"] = kwargs.pop("_type")
        table = kwargs.get("table")
        column = kwargs.get("column")
        if table is None or column is None:
            raise ValueError("Variable requires sqlglot table+column")
        if isinstance(table, str):
            kwargs["table"] = exp.to_table(table)
        if isinstance(column, str):
            kwargs["column"] = exp.to_identifier(column)
        if kwargs.get("rowid") is None:
            raise ValueError("Variable requires rowid")
        super().__init__(*args, **kwargs)

    @property
    def name(self) -> str:
        return self.text("this")

    @property
    def table(self) -> exp.Table | None:
        return self.args.get("table")

    @property
    def column(self) -> exp.Identifier | None:
        return self.args.get("column")

    @property
    def rowid(self) -> Any:
        return self.args["rowid"]

    @property
    def table_name(self) -> Optional[str]:
        table = self.table
        if table is not None and table.this is not None:
            return table.this.name
        return None

    @property
    def column_name(self) -> str:
        column = self.column
        if column is not None:
            return column.name
        return ""

    @property
    def concrete(self) -> Any:
        if self.args.get("is_bound"):
            if self.args.get("is_null"):
                return None
            return self.args.get("concrete")
        return self.args.get("concrete")

    def bind(self, value: Any) -> None:
        """Bind to a concrete non-NULL value."""
        self.set("is_bound", True)
        self.set("is_null", False)
        self.set("concrete", value)

    def bind_null(self) -> None:
        """Bind to SQL NULL."""
        self.set("is_bound", True)
        self.set("is_null", True)
        self.set("concrete", None)

    def unbind(self) -> None:
        """Revert to unbound / free state."""
        self.set("is_bound", False)
        self.set("is_null", False)
        self.set("concrete", None)


# Register generator transforms so ``Symbol`` et al. pretty-print when a
# sqlglot ``Generator`` runs over an AST containing them.
for _klass in [Symbol, Const, Variable, Row]:
    generator.Generator.TRANSFORMS[_klass] = (
        lambda self, expression: expression.sql(dialect=self.dialect)
    )

# =============================================================================
# Environment
# =============================================================================


_MISSING = object()

_STRFTIME_COMPONENTS = {
    "%Y": "year",
    "%m": "month",
    "%d": "day",
    "%H": "hour",
    "%M": "minute",
    "%S": "second",
}
_FIXED_INTERVAL_SECONDS = {
    "SECOND": 1,
    "MINUTE": 60,
    "HOUR": 3600,
    "DAY": 86400,
    "WEEK": 7 * 86400,
}
_TEMPORAL_COMPONENT_CLASSES = tuple(
    (getattr(exp, class_name), component)
    for class_name, component in (
        ("Year", "year"),
        ("Month", "month"),
        ("Day", "day"),
        ("DayOfMonth", "day"),
        ("Hour", "hour"),
        ("Minute", "minute"),
        ("Second", "second"),
    )
    if hasattr(exp, class_name)
)
_CMP_OPS = {
    exp.EQ: "=",
    exp.NEQ: "!=",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
}


def _identifier_name(value: object) -> str:
    if isinstance(value, exp.Identifier):
        return value.name
    if isinstance(value, exp.Column):
        return value.name
    return str(value)


class Environment:
    """Column / SolverVar → value resolver with optional outer scope chaining.

    Accepts rows keyed by ``exp.Identifier`` (name-only) **or** ``exp.Column``
    (qualified ``table.column``).  When a row key is an ``exp.Column`` with a
    table qualifier, ``resolve()`` matches by *(table, column)* first, then
    falls back to name-only.  This lets join outputs carry ``t.x`` and ``u.x``
    without collision.
    """

    __slots__ = ("_row", "_qualified", "_assignments", "_outer")

    def __init__(
        self,
        *,
        row: Optional[Mapping[exp.Identifier | exp.Column, Any]] = None,
        assignments: Optional[Mapping[SolverVar, Any]] = None,
        outer: Optional["Environment"] = None,
    ) -> None:
        self._row: Dict[str, Any] = {}
        self._qualified: Dict[Tuple[Optional[str], str], Any] = {}
        for key, value in (row or {}).items():
            if isinstance(key, exp.Column) and key.table:
                table_name = _identifier_name(key.table)
                col_name = _identifier_name(key.this if key.this is not None else key)
                self._qualified[(table_name, col_name)] = value
            self._row[_identifier_name(key)] = value
        self._assignments: Dict[str, Any] = {}
        for key, value in (assignments or {}).items():
            if isinstance(key, SolverVar):
                self._assignments[key.var_key] = value
            else:
                self._assignments[str(key)] = value
        self._outer: Optional[Environment] = outer

    @classmethod
    def from_row(
        cls,
        row: Mapping[exp.Identifier | exp.Column, Any],
        outer: Optional["Environment"] = None,
    ) -> "Environment":
        return cls(row=row, outer=outer)

    @classmethod
    def from_assignments(
        cls,
        assignments: Mapping[SolverVar, Any],
        outer: Optional["Environment"] = None,
    ) -> "Environment":
        return cls(assignments=assignments, outer=outer)

    def resolve(self, column: exp.Column) -> Any:
        """Return the value bound to ``column``, or ``None`` if unresolved.

        If *column* has a table qualifier, looks up *(table, name)* first
        for exact qualified matching; falls back to name-only.
        """
        name = _identifier_name(column.this if column.this is not None else column)
        if column.table:
            table_name = _identifier_name(column.table)
            qualified = (table_name, name)
            if qualified in self._qualified:
                return self._qualified[qualified]
        if name in self._row:
            return self._row[name]
        if self._outer is not None:
            return self._outer.resolve(column)
        return None

    def assignment(self, var: SolverVar) -> Any:
        """Return the assigned value for ``var``, or ``_MISSING`` if absent."""
        if var.var_key in self._assignments:
            return self._assignments[var.var_key]
        if self._outer is not None:
            return self._outer.assignment(var)
        return _MISSING

    def bind(self, column: exp.Identifier | str, value: Any) -> None:
        """Bind ``column`` to ``value`` in this environment."""
        name = _identifier_name(column)
        self._row[name] = value
        if isinstance(column, exp.Column) and column.table:
            table_name = _identifier_name(column.table)
            self._qualified[(table_name, name)] = value

    def extend(
        self,
        row: Optional[Mapping[exp.Identifier | exp.Column, Any]] = None,
        assignments: Optional[Mapping[SolverVar, Any]] = None,
    ) -> "Environment":
        """Return a child environment layering new bindings on this one."""
        return Environment(row=row, assignments=assignments, outer=self)

    def contains(self, column: exp.Column) -> bool:
        """Return True if ``column`` resolves in this or any outer env."""
        name = _identifier_name(column.this if column.this is not None else column)
        if column.table:
            table_name = _identifier_name(column.table)
            if (table_name, name) in self._qualified:
                return True
        if name in self._row:
            return True
        return self._outer.contains(column) if self._outer is not None else False


# =============================================================================
# Handler registry and the top-level ``concrete`` entry point
# =============================================================================


_Handler = Callable[[exp.Expression, Environment], Any]
_HANDLERS: Dict[Type[exp.Expression], _Handler] = {}


def handler(*classes: Type[exp.Expression]) -> Callable[[_Handler], _Handler]:
    """Register ``func`` as the evaluator for each class in ``classes``."""

    def decorator(func: _Handler) -> _Handler:
        for cls in classes:
            _HANDLERS[cls] = func
        return func

    return decorator


def concrete(
    expr: exp.Expression, env: Optional[Environment] = None
) -> Any:
    """Evaluate ``expr`` to a Python value under ``env``.

    This is the single entry point for concrete evaluation. ``env`` may
    be omitted for expressions that don't reference columns (e.g.
    literal arithmetic). Columns / SolverVars that don't resolve produce
    ``None``.
    """
    if env is None:
        env = Environment()
    return _eval(expr, env)


def _eval(node: Any, env: Environment) -> Any:
    if node is None:
        return None
    for cls in type(node).__mro__:
        fn = _HANDLERS.get(cls)
        if fn is not None:
            return fn(node, env)
    return _eval_via_sqlglot_env(node, env)


def _eval_via_sqlglot_env(node: exp.Expression, env: Environment) -> Any:
    """Fallback for sqlglot nodes we haven't explicitly handled."""
    op_key = getattr(node, "key", None)
    if op_key is None:
        return None
    op = _SQLGLOT_ENV.get(op_key.upper())
    if op is None:
        return None
    operand_values = [
        _eval(child, env)
        for child in node.iter_expressions()
        if not isinstance(child, exp.DataType)
    ]
    try:
        return op(*operand_values)
    except Exception:  # pragma: no cover - defensive
        return None


# =============================================================================
# NULL predicate helpers
# =============================================================================


def make_is_null(expr: exp.Expression) -> exp.Is:
    """Build the sqlglot-native ``expr IS NULL`` predicate."""
    return exp.Is(this=expr, expression=exp.Null())


def make_is_not_null(expr: exp.Expression) -> exp.Is:
    """Build the sqlglot-native ``expr IS NOT NULL`` predicate."""
    return exp.Is(this=expr, expression=exp.Not(this=exp.Null()))


def _unparen(expr: exp.Expression) -> exp.Expression:
    while isinstance(expr, exp.Paren):
        expr = expr.this
    return expr


def is_null_predicate(expr: exp.Expression) -> bool:
    """Return True for sqlglot's ``expr IS NULL`` shape."""
    expr = _unparen(expr)
    return isinstance(expr, exp.Is) and isinstance(expr.expression, exp.Null)


def is_not_null_predicate(expr: exp.Expression) -> bool:
    """Return True for sqlglot's parsed or constructed ``expr IS NOT NULL``."""
    expr = _unparen(expr)
    if isinstance(expr, exp.Is):
        right = _unparen(expr.expression)
        return isinstance(right, exp.Not) and isinstance(_unparen(right.this), exp.Null)
    if isinstance(expr, exp.Not):
        return is_null_predicate(expr.this)
    return False


def null_predicate_parts(expr: exp.Expression) -> Optional[Tuple[exp.Expression, bool]]:
    """Return ``(operand, is_null)`` for native NULL predicates, else ``None``.

    ``is_null`` is True for ``IS NULL`` and False for ``IS NOT NULL``.
    """
    expr = _unparen(expr)
    if is_null_predicate(expr):
        assert isinstance(expr, exp.Is)
        return expr.this, True
    if isinstance(expr, exp.Is) and is_not_null_predicate(expr):
        return expr.this, False
    if isinstance(expr, exp.Not) and is_null_predicate(expr.this):
        inner = _unparen(expr.this)
        assert isinstance(inner, exp.Is)
        return inner.this, False
    return None


# =============================================================================
# Three-valued logic primitives
# =============================================================================


def tvl_and(a: Any, b: Any) -> Optional[bool]:
    """SQL 3VL AND.

    Truth table (left x right → result):

        TRUE   AND TRUE   = TRUE
        TRUE   AND FALSE  = FALSE
        TRUE   AND NULL   = NULL
        FALSE  AND *      = FALSE
        NULL   AND FALSE  = FALSE
        NULL   AND TRUE   = NULL
        NULL   AND NULL   = NULL
    """
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return bool(a and b)


def tvl_or(a: Any, b: Any) -> Optional[bool]:
    """SQL 3VL OR.

        TRUE   OR *       = TRUE
        FALSE  OR FALSE   = FALSE
        FALSE  OR NULL    = NULL
        NULL   OR TRUE    = TRUE
        NULL   OR FALSE   = NULL
        NULL   OR NULL    = NULL
    """
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return bool(a or b)


def tvl_not(a: Any) -> Optional[bool]:
    """SQL 3VL NOT: ``NOT NULL == NULL``."""
    if a is None:
        return None
    return not a


# =============================================================================
# Type coercion (used by comparisons, arithmetic, Const.coerce_to)
# =============================================================================


_STRICT_DIALECTS = frozenset({"postgres", "strict"})


def _parse_temporal(value: Any) -> Optional[Union[date, datetime]]:
    if isinstance(value, (date, datetime)):
        return value
    if isinstance(value, str):
        try:
            parsed = date_parser.parse(value)
            if re.fullmatch(r"\s*\d{4}-\d{1,2}-\d{1,2}\s*", value):
                return parsed.date()
            return parsed
        except (ValueError, OverflowError, TypeError):
            return None
    return None


def _align_temporal_precision(left: Any, right: Any) -> Tuple[Any, Any]:
    if isinstance(left, datetime) and isinstance(right, date) and not isinstance(right, datetime):
        return left, datetime(right.year, right.month, right.day)
    if isinstance(right, datetime) and isinstance(left, date) and not isinstance(left, datetime):
        return datetime(left.year, left.month, left.day), right
    return left, right


def _coerce_temporal_pair(left: Any, right: Any) -> Tuple[Any, Any]:
    """Align date/datetime operands so they can be compared."""
    if left is None or right is None:
        return left, right
    left_temp = isinstance(left, (date, datetime))
    right_temp = isinstance(right, (date, datetime))
    if left_temp and isinstance(right, str):
        parsed = _parse_temporal(right)
        if parsed is not None:
            return left, parsed
    if right_temp and isinstance(left, str):
        parsed = _parse_temporal(left)
        if parsed is not None:
            return parsed, right
    return _align_temporal_precision(left, right)


def _coerce_numeric_pair(left: Any, right: Any) -> Tuple[Any, Any]:
    """Align numeric-ish operands. Strings get parsed into numbers if safely possible."""
    if isinstance(left, Decimal) and isinstance(right, float):
        left = float(left)
    if isinstance(right, Decimal) and isinstance(left, float):
        right = float(right)
    if isinstance(left, str) and isinstance(right, str):
        try:
            if "." in left or "." in right:
                return float(left.strip()), float(right.strip())
            return int(left.strip()), int(right.strip())
        except (ValueError, TypeError):
            return left, right

    def _coerce_str(value: Any, other: Any) -> Any:
        if not isinstance(value, str) or isinstance(other, bool):
            return value
        text = value.strip()
        try:
            if isinstance(other, int):
                if text.lstrip("-").isdigit():
                    return int(text)
                return value
            if isinstance(other, float):
                return float(text)
        except (ValueError, TypeError):
            return value
        return value

    left = _coerce_str(left, right)
    right = _coerce_str(right, left)
    return left, right


def _coerce_comparable(left: Any, right: Any) -> Tuple[Any, Any]:
    """Best-effort alignment so two values are comparable with Python ops."""
    left, right = _coerce_temporal_pair(left, right)
    left, right = _coerce_numeric_pair(left, right)
    return left, right


def _coerce_value(
    value: Any,
    from_type: Optional[DataType],
    to_type: DataType,
    dialect: str = "sqlite",
) -> Any:
    """Convert ``value`` to ``to_type``.

    Returns ``None`` when coercion is undefined and the dialect is
    strict; returns a best-effort value under lenient dialects (SQLite /
    MySQL defaults).
    """
    if value is None:
        return None

    # Numeric targets
    if to_type.is_type(*DataType.INTEGER_TYPES):
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, (float, Decimal)):
            try:
                return int(value)
            except (OverflowError, ValueError):
                return None
        if isinstance(value, str):
            try:
                return int(value.strip())
            except (ValueError, TypeError):
                try:
                    return int(float(value))
                except (ValueError, TypeError):
                    return None if dialect in _STRICT_DIALECTS else 0
        return None

    if to_type.is_type(*DataType.REAL_TYPES):
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, (int, float, Decimal)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except (ValueError, TypeError):
                return None if dialect in _STRICT_DIALECTS else 0.0
        return None

    # Text
    if to_type.is_type(*DataType.TEXT_TYPES):
        if isinstance(value, bool):
            # MySQL: TRUE → '1'; Postgres: TRUE → 'true'
            return "true" if value and dialect in ("postgres",) else (
                "false" if not value and dialect in ("postgres",) else str(int(value))
            )
        if isinstance(value, (datetime,)):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(value, date):
            return value.strftime("%Y-%m-%d")
        return str(value)

    # Boolean
    if to_type.is_type(exp.DataType.Type.BOOLEAN):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("true", "t", "1", "yes", "y"):
                return True
            if normalized in ("false", "f", "0", "no", "n"):
                return False
            return None
        return bool(value)

    # Temporal
    if to_type.is_type(*DataType.TEMPORAL_TYPES):
        if isinstance(value, datetime):
            if to_type.is_type(exp.DataType.Type.DATE):
                return value.date()
            return value
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            parsed = _parse_temporal(value)
            if parsed is None:
                return None
            if to_type.is_type(exp.DataType.Type.DATE) and isinstance(parsed, datetime):
                return parsed.date()
            return parsed
        return None

    # No explicit coercion rule — pass through unchanged.
    return value


# =============================================================================
# Handler implementations
# =============================================================================


# ----- leaves -----


@handler(exp.Literal)
def _eval_literal(node: exp.Literal, env: Environment) -> Any:
    if node.is_string:
        return str(node.this)
    text = node.this
    # sqlglot stores numeric literals as strings in ``node.this``.
    try:
        if isinstance(text, str) and "." in text:
            return float(text)
        return int(text)
    except (TypeError, ValueError):
        try:
            return float(text)
        except (TypeError, ValueError):
            return text


@handler(exp.Null)
def _eval_null(node: exp.Null, env: Environment) -> None:
    return None


@handler(exp.Boolean)
def _eval_boolean(node: exp.Boolean, env: Environment) -> bool:
    return bool(node.this)


@handler(exp.Column)
def _eval_column(node: exp.Column, env: Environment) -> Any:
    value = env.resolve(node)
    if isinstance(value, Symbol):
        return value.concrete
    return value


@handler(Const)
def _eval_const(node: Const, env: Environment) -> Any:
    return node.concrete


@handler(Variable)
def _eval_variable(node: Variable, env: Environment) -> Any:
    if node.is_bound:
        return None if node.is_null else node.args.get("concrete")
    # Unbound: prefer stored construction-time concrete, else None.
    return node.args.get("concrete")


@handler(SolverVar)
def _eval_solver_var(node: SolverVar, env: Environment) -> Any:
    resolved = env.assignment(node)
    if resolved is _MISSING:
        return None
    return resolved


# ----- arithmetic -----


@handler(exp.Add)
def _eval_add(node: exp.Add, env: Environment) -> Any:
    l, r = _eval(node.left, env), _eval(node.right, env)
    if l is None or r is None:
        return None
    try:
        return l + r
    except TypeError:
        l, r = _coerce_comparable(l, r)
        try:
            return l + r
        except TypeError:
            return None


@handler(exp.Sub)
def _eval_sub(node: exp.Sub, env: Environment) -> Any:
    l, r = _eval(node.left, env), _eval(node.right, env)
    if l is None or r is None:
        return None
    try:
        return l - r
    except TypeError:
        l, r = _coerce_numeric_pair(l, r)
        try:
            return l - r
        except TypeError:
            return None


@handler(exp.Mul)
def _eval_mul(node: exp.Mul, env: Environment) -> Any:
    l, r = _eval(node.left, env), _eval(node.right, env)
    if l is None or r is None:
        return None
    return l * r


@handler(exp.Div)
def _eval_div(node: exp.Div, env: Environment) -> Any:
    l, r = _eval(node.left, env), _eval(node.right, env)
    if l is None or r is None or r == 0:
        return None
    try:
        return l / r
    except TypeError:
        l, r = _coerce_numeric_pair(l, r)
        try:
            return l / r
        except TypeError:
            return None


@handler(exp.Mod)
def _eval_mod(node: exp.Mod, env: Environment) -> Any:
    l, r = _eval(node.left, env), _eval(node.right, env)
    if l is None or r is None or r == 0:
        return None
    return l % r


@handler(exp.Neg)
def _eval_neg(node: exp.Neg, env: Environment) -> Any:
    v = _eval(node.this, env)
    return None if v is None else -v


# ----- comparison -----


def fixed_interval_delta(node: exp.Expression) -> Optional[timedelta]:
    """Parse a fixed DateAdd/DateSub interval into a ``timedelta``, or ``None``."""
    interval_expr = node.expression
    unit_node = node.args.get("unit")
    if isinstance(interval_expr, exp.Interval):
        unit_node = interval_expr.args.get("unit") or unit_node
        raw = literal_value(interval_expr.this)
    else:
        raw = literal_value(interval_expr) if isinstance(interval_expr, exp.Expression) else None
    unit = unit_name(unit_node)
    if unit not in _FIXED_INTERVAL_SECONDS:
        return None
    count = integer_literal(raw)
    if count is None:
        return None
    return timedelta(seconds=count * _FIXED_INTERVAL_SECONDS[unit])


# Private aliases for in-module handlers.
_unit_name = unit_name
_integer_literal = integer_literal
_literal_leaf = literal_value
_fixed_interval_delta = fixed_interval_delta


def _ordered_comparison(left: Any, op: str, right: Any) -> Optional[bool]:
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if isinstance(left, bool) or isinstance(right, bool):
        return None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        comparable = (left, right)
    elif isinstance(left, datetime) and isinstance(right, datetime):
        comparable = (left, right)
    elif (
        isinstance(left, date)
        and not isinstance(left, datetime)
        and isinstance(right, date)
        and not isinstance(right, datetime)
    ):
        comparable = (left, right)
    elif isinstance(left, dt_time) and isinstance(right, dt_time):
        comparable = (left, right)
    elif isinstance(left, str) and isinstance(right, str):
        comparable = (left, right)
    else:
        return None
    lhs, rhs = comparable
    if op == ">":
        return lhs > rhs
    if op == ">=":
        return lhs >= rhs
    if op == "<":
        return lhs < rhs
    if op == "<=":
        return lhs <= rhs
    return None


def _date_shift_value(node: exp.Expression, env: Environment) -> Any:
    if not isinstance(node, (exp.DateAdd, exp.DateSub)):
        return _MISSING
    inner = unwrap_planning_temporal_arg(node.this)
    interval = _fixed_interval_delta(node)
    if interval is None:
        return _MISSING
    if isinstance(node, exp.DateSub):
        interval = -interval
    raw_value = _eval(inner, env)
    value = raw_value if isinstance(raw_value, datetime) else parse_datetime(raw_value)
    if value is None:
        parsed_date = parse_date(raw_value)
        if parsed_date is None:
            return None
        value = datetime(parsed_date.year, parsed_date.month, parsed_date.day)
    return value + interval


def _temporal_component_value(value: Any, component: str) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, (date, datetime, dt_time)):
        parsed = parse_datetime(value) or parse_time(value)
        if parsed is None:
            return None
        value = parsed
    if component == "year" and isinstance(value, (date, datetime)):
        return value.year
    if component == "month" and isinstance(value, (date, datetime)):
        return value.month
    if component == "day" and isinstance(value, (date, datetime)):
        return value.day
    if component == "hour" and isinstance(value, (datetime, dt_time)):
        return value.hour
    if component == "minute" and isinstance(value, (datetime, dt_time)):
        return value.minute
    if component == "second" and isinstance(value, (datetime, dt_time)):
        return value.second
    return None


def _strftime_component(node: exp.Expression) -> Optional[str]:
    if isinstance(node, exp.TimeToStr):
        fmt = node.args.get("format")
        if isinstance(fmt, exp.Expression):
            fmt_value = _eval(fmt, Environment())
        else:
            fmt_value = fmt
        return _STRFTIME_COMPONENTS.get(fmt_value) if isinstance(fmt_value, str) else None
    if isinstance(node, exp.Anonymous) and str(node.name).upper() == "STRFTIME":
        args = list(node.expressions)
        if len(args) >= 1 and isinstance(args[0], exp.Literal):
            return _STRFTIME_COMPONENTS.get(str(args[0].this))
    return None


def _eval_comparison(node: exp.Expression, env: Environment) -> Any:
    op = _CMP_OPS[type(node)]
    left_node, right_node = node.this, node.expression

    shift_left = _date_shift_value(left_node, env)
    if shift_left is not _MISSING:
        left = shift_left
    else:
        left = _eval(left_node, env)

    shift_right = _date_shift_value(right_node, env)
    if shift_right is not _MISSING:
        right = shift_right
    else:
        right = _eval(right_node, env)

    if left is None or right is None:
        return None

    if isinstance(left_node, exp.Date):
        right = parse_date(right)
    elif isinstance(right_node, exp.Date):
        left = parse_date(left)
    elif isinstance(left_node, (exp.DateAdd, exp.DateSub)):
        right = parse_datetime(right) or (
            datetime(parse_date(right).year, parse_date(right).month, parse_date(right).day)
            if parse_date(right) is not None
            else right
        )
    elif isinstance(right_node, (exp.DateAdd, exp.DateSub)):
        left = parse_datetime(left) or (
            datetime(parse_date(left).year, parse_date(left).month, parse_date(left).day)
            if parse_date(left) is not None
            else left
        )

    if isinstance(left_node, SolverVar) and not isinstance(right_node, SolverVar):
        if not isinstance(left, StorageLiteral) or op not in {"=", "!="}:
            try:
                right = coerce_literal_value(right, left_node.dtype)
            except CoercionError:
                return False
    elif isinstance(right_node, SolverVar) and not isinstance(left_node, SolverVar):
        if not isinstance(right, StorageLiteral) or op not in {"=", "!="}:
            try:
                left = coerce_literal_value(left, right_node.dtype)
            except CoercionError:
                return False

    if isinstance(left, StorageLiteral) or isinstance(right, StorageLiteral):
        if op == "=":
            return str(left) == str(right)
        if op == "!=":
            return str(left) != str(right)
        if isinstance(left, StorageLiteral) and isinstance(left_node, SolverVar):
            try:
                left = coerce_literal_value(str(left), left_node.dtype)
            except CoercionError:
                return False
        if isinstance(right, StorageLiteral) and isinstance(right_node, SolverVar):
            try:
                right = coerce_literal_value(str(right), right_node.dtype)
            except CoercionError:
                return False

    # Generic column/literal path: also try soft numeric coercion.
    if not isinstance(left_node, SolverVar) and not isinstance(right_node, SolverVar):
        try:
            left, right = _coerce_comparable(left, right)
        except Exception:
            pass

    result = _ordered_comparison(left, op, right)
    return False if result is None else result


_HANDLERS[exp.EQ] = _eval_comparison
_HANDLERS[exp.NEQ] = _eval_comparison
_HANDLERS[exp.GT] = _eval_comparison
_HANDLERS[exp.GTE] = _eval_comparison
_HANDLERS[exp.LT] = _eval_comparison
_HANDLERS[exp.LTE] = _eval_comparison


# ----- logical -----


@handler(exp.And)
def _eval_and(node: exp.And, env: Environment) -> Optional[bool]:
    return tvl_and(_eval(node.left, env), _eval(node.right, env))


@handler(exp.Or)
def _eval_or(node: exp.Or, env: Environment) -> Optional[bool]:
    return tvl_or(_eval(node.left, env), _eval(node.right, env))


@handler(exp.Not)
def _eval_not(node: exp.Not, env: Environment) -> Optional[bool]:
    return tvl_not(_eval(node.this, env))


# ----- NULL checks -----


@handler(exp.Is)
def _eval_is(node: exp.Is, env: Environment) -> Optional[bool]:
    """``x IS y``. Mostly appears as ``x IS NULL`` / ``x IS NOT NULL``; also
    ``x IS TRUE`` / ``x IS FALSE`` in some dialects."""
    left = _eval(node.this, env)
    right_node = node.expression
    if isinstance(right_node, exp.Null):
        return left is None
    if isinstance(right_node, exp.Not) and isinstance(
        _unparen(right_node.this),
        exp.Null,
    ):
        return left is not None
    if isinstance(right_node, exp.Boolean):
        if left is None:
            return False  # IS TRUE / IS FALSE treats NULL as not-matching
        return bool(left) is bool(right_node.this)
    right = _eval(right_node, env)
    return left is right


# ----- conditional -----


@handler(exp.Case)
def _eval_case(node: exp.Case, env: Environment) -> Any:
    case_operand = node.this
    if case_operand is not None:
        operand_value = _eval(case_operand, env)
        for branch in node.args.get("ifs", []) or []:
            candidate = _eval(branch.this, env)
            if operand_value is None or candidate is None:
                continue
            left, right = _coerce_comparable(operand_value, candidate)
            if left == right:
                return _eval(branch.args.get("true"), env)
        default = node.args.get("default")
        return _eval(default, env) if default is not None else None

    for branch in node.args.get("ifs", []) or []:
        condition = _eval(branch.this, env)
        if condition is True:
            return _eval(branch.args.get("true"), env)
    default = node.args.get("default")
    return _eval(default, env) if default is not None else None


@handler(exp.If)
def _eval_if(node: exp.Expression, env: Environment) -> Any:
    cond = _eval(node.this, env)
    if cond is None:
        return None
    if cond:
        target = node.args.get("true") or node.args.get("expression")
    else:
        target = node.args.get("false")
    return _eval(target, env) if target is not None else None


@handler(exp.Coalesce)
def _eval_coalesce(node: exp.Coalesce, env: Environment) -> Any:
    candidates: List[Any] = [node.this]
    candidates.extend(node.args.get("expressions") or [])
    for candidate in candidates:
        value = _eval(candidate, env)
        if value is not None:
            return value
    return None


@handler(exp.Nullif)
def _eval_nullif(node: exp.Nullif, env: Environment) -> Any:
    left = _eval(node.this, env)
    right = _eval(node.expression, env)
    if left is None:
        return None
    return None if left == right else left


# ----- membership -----


@handler(exp.Between)
def _eval_between(node: exp.Between, env: Environment) -> Optional[bool]:
    value = _eval(node.this, env)
    low = _eval(node.args.get("low"), env)
    high = _eval(node.args.get("high"), env)
    if value is None or low is None or high is None:
        return None
    if isinstance(node.this, SolverVar):
        lower = _ordered_comparison(low, "<=", value)
        upper = _ordered_comparison(value, "<=", high)
        if lower is None or upper is None:
            return False
        return lower and upper
    value, low = _coerce_comparable(value, low)
    value, high = _coerce_comparable(value, high)
    low, high = _coerce_comparable(low, high)
    try:
        return low <= value <= high
    except TypeError:
        return None


@handler(exp.In)
def _eval_in(node: exp.In, env: Environment) -> Optional[bool]:
    value = _eval(node.this, env)
    expressions = node.args.get("expressions") or []
    if isinstance(node.this, SolverVar):
        dtype = node.this.dtype
        values = []
        for candidate_node in expressions:
            if isinstance(candidate_node, exp.Null):
                values.append(None)
                continue
            candidate = _eval(candidate_node, env)
            if candidate is not None:
                try:
                    candidate = coerce_literal_value(candidate, dtype)
                except CoercionError:
                    continue
            values.append(candidate)
        return value in values
    if value is None:
        return None
    saw_null = False
    for candidate_node in expressions:
        candidate = _eval(candidate_node, env)
        if candidate is None:
            saw_null = True
            continue
        if value == candidate:
            return True
    return None if saw_null else False


# ----- string -----


@handler(exp.Like)
def _eval_like(node: exp.Like, env: Environment) -> Optional[bool]:
    return _like(_eval(node.this, env), _eval(node.expression, env), case_insensitive=False)


@handler(exp.ILike)
def _eval_ilike(node: exp.ILike, env: Environment) -> Optional[bool]:
    return _like(_eval(node.this, env), _eval(node.expression, env), case_insensitive=True)


@functools.lru_cache(maxsize=256)
def _cached_like_pattern(pattern: str, case_insensitive: bool):
    """Cache compiled LIKE patterns — they're fixed per AST node."""
    compiled = like_to_pattern(pattern)
    if case_insensitive:
        return re.compile(compiled.pattern, re.IGNORECASE)
    return compiled


def _like(value: Any, pattern: Any, *, case_insensitive: bool) -> Optional[bool]:
    if value is None or pattern is None:
        return None
    try:
        compiled = _cached_like_pattern(str(pattern), case_insensitive)
        return bool(compiled.match(str(value)))
    except re.error:  # pragma: no cover - defensive
        return False


@handler(exp.Concat)
def _eval_concat(node: exp.Concat, env: Environment) -> Any:
    parts = [_eval(piece, env) for piece in (node.args.get("expressions") or [])]
    if any(part is None for part in parts):
        return None
    return "".join(str(part) for part in parts)


@handler(exp.Substring)
def _eval_substring(node: exp.Substring, env: Environment) -> Any:
    value = _eval(node.this, env)
    start = _eval(node.args.get("start"), env)
    length = _eval(node.args.get("length"), env)
    if value is None or start is None:
        return None
    text = str(value)
    start_idx = max(int(start) - 1, 0)  # SQL is 1-indexed
    if length is None:
        return text[start_idx:]
    return text[start_idx : start_idx + int(length)]


@handler(exp.Length)
def _eval_length(node: exp.Length, env: Environment) -> Any:
    value = _eval(node.this, env)
    return None if value is None else len(str(value))


@handler(exp.Upper)
def _eval_upper(node: exp.Upper, env: Environment) -> Any:
    value = _eval(node.this, env)
    return None if value is None else str(value).upper()


@handler(exp.Lower)
def _eval_lower(node: exp.Lower, env: Environment) -> Any:
    value = _eval(node.this, env)
    return None if value is None else str(value).lower()


@handler(exp.Trim)
def _eval_trim(node: exp.Trim, env: Environment) -> Any:
    value = _eval(node.this, env)
    return None if value is None else str(value).strip()


# ----- numeric functions -----


@handler(exp.Abs)
def _eval_abs(node: exp.Abs, env: Environment) -> Any:
    value = _eval(node.this, env)
    return None if value is None else abs(value)


@handler(exp.Round)
def _eval_round(node: exp.Round, env: Environment) -> Any:
    value = _eval(node.this, env)
    digits_node = node.args.get("decimals")
    digits = _eval(digits_node, env) if digits_node is not None else 0
    if value is None or digits is None:
        return None
    return round(value, int(digits))


@handler(exp.Ceil)
def _eval_ceil(node: exp.Ceil, env: Environment) -> Any:
    value = _eval(node.this, env)
    return None if value is None else math.ceil(value)


@handler(exp.Floor)
def _eval_floor(node: exp.Floor, env: Environment) -> Any:
    value = _eval(node.this, env)
    return None if value is None else math.floor(value)


# ----- cast -----


@handler(exp.Cast, exp.TryCast)
def _eval_cast(node: exp.Cast, env: Environment) -> Any:
    value = _eval(node.this, env)
    target_node = node.args.get("to")
    if value is None or target_node is None:
        return value
    try:
        target_dt = DataType.build(target_node.sql() if hasattr(target_node, "sql") else str(target_node))
    except Exception:  # pragma: no cover - defensive
        return value
    strict = isinstance(node, exp.Cast)
    dialect = "postgres" if strict else "sqlite"
    return _coerce_value(value, None, target_dt, dialect=dialect)


@handler(exp.TsOrDsToTimestamp)
def _eval_ts_or_ds_to_timestamp(node: exp.TsOrDsToTimestamp, env: Environment) -> Any:
    parsed = _parse_temporal(_eval(node.this, env))
    if isinstance(parsed, date) and not isinstance(parsed, datetime):
        return datetime(parsed.year, parsed.month, parsed.day)
    return parsed


@handler(exp.Date)
def _eval_date(node: exp.Date, env: Environment) -> Any:
    return parse_date(_eval(unwrap_planning_temporal_arg(node.this), env))


@handler(exp.DateAdd, exp.DateSub)
def _eval_date_shift(node: exp.Expression, env: Environment) -> Any:
    value = _date_shift_value(node, env)
    return None if value is _MISSING else value


def _eval_temporal_component(node: exp.Expression, env: Environment, component: str) -> Any:
    inner = unwrap_planning_temporal_arg(node.this)
    return _temporal_component_value(_eval(inner, env), component)


for _cls, _component in _TEMPORAL_COMPONENT_CLASSES:
    _HANDLERS[_cls] = (
        lambda node, env, component=_component: _eval_temporal_component(
            node, env, component
        )
    )


# ----- ordered (pass-through used inside ORDER BY) -----


@handler(exp.Ordered)
def _eval_ordered(node: exp.Ordered, env: Environment) -> Any:
    return _eval(node.this, env)


# ----- dialect functions (Anonymous dispatch) -----


@handler(exp.TimeToStr)
def _eval_time_to_str(node: exp.TimeToStr, env: Environment) -> Any:
    component = _strftime_component(node)
    if component is not None:
        inner = unwrap_planning_temporal_arg(node.this)
        value = _temporal_component_value(_eval(inner, env), component)
        if value is None:
            return None
        return f"{value:04d}" if component == "year" else f"{value:02d}"
    value = _eval(node.this, env)
    fmt = node.args.get("format")
    if value is None and isinstance(node.this, (exp.Cast, exp.TsOrDsToTimestamp)):
        inner = node.this.this
        if isinstance(inner, exp.Literal) and str(inner.this).lower() == "now":
            value = datetime.utcnow()
    if value is None or fmt is None:
        return None
    fmt_str = fmt if isinstance(fmt, str) else _eval(fmt, env)
    if fmt_str is None:
        return None
    d = _parse_temporal(value) if isinstance(value, str) else value
    if d is None:
        d = _parse_temporal(str(value))
    if d is None:
        return None
    if isinstance(d, date) and not isinstance(d, datetime):
        d = datetime(d.year, d.month, d.day)
    try:
        return d.strftime(fmt_str)
    except (ValueError, AttributeError):
        return None


@handler(exp.Anonymous)
def _eval_anonymous(node: exp.Anonymous, env: Environment) -> Any:
    if str(node.name).upper() == "STRFTIME":
        component = _strftime_component(node)
        args = list(node.expressions)
        if component is not None and len(args) >= 2:
            inner = unwrap_planning_temporal_arg(args[1])
            value = _temporal_component_value(_eval(inner, env), component)
            if value is None:
                return None
            return f"{value:04d}" if component == "year" else f"{value:02d}"
    name = node.name.upper()
    args = [_eval(arg, env) for arg in node.expressions]
    fn = _ANONYMOUS_HANDLERS.get(name)
    if fn:
        try:
            return fn(*args)
        except (TypeError, ValueError, IndexError):
            return None
    return None


def _julianday(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return float(val)
    d = _parse_temporal(val) if isinstance(val, str) else val
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        # Julian day number approximation (days since epoch for diff purposes)
        from datetime import date as _date
        return float((d - _date(1, 1, 1)).days + 1721425.5)
    return None


def _instr(haystack: Any, needle: Any) -> Any:
    if haystack is None or needle is None:
        return None
    pos = str(haystack).find(str(needle))
    return pos + 1 if pos >= 0 else 0


def _replace(s: Any, old: Any, new: Any) -> Any:
    if s is None or old is None or new is None:
        return None
    return str(s).replace(str(old), str(new))


def _typeof(val: Any) -> str:
    if val is None:
        return "null"
    if isinstance(val, int):
        return "integer"
    if isinstance(val, float):
        return "real"
    if isinstance(val, str):
        return "text"
    return "text"


def _total(val: Any) -> float:
    if val is None:
        return 0.0
    return float(val)


def _sign(val: Any) -> Any:
    if val is None:
        return None
    if val > 0:
        return 1
    if val < 0:
        return -1
    return 0


def _unicode(val: Any) -> Any:
    if val is None:
        return None
    s = str(val)
    return ord(s[0]) if s else None


def _substr(text: Any, start: Any, length: Any = None) -> Any:
    if text is None or start is None:
        return None
    s = str(text)
    idx = max(int(start) - 1, 0)  # SQL is 1-indexed
    if length is None:
        return s[idx:]
    return s[idx : idx + int(length)]


_ANONYMOUS_HANDLERS = {
    "SUBSTR": _substr,
    "SUBSTRING": _substr,
    "JULIANDAY": _julianday,
    "INSTR": _instr,
    "REPLACE": _replace,
    "TYPEOF": _typeof,
    "TOTAL": _total,
    "SIGN": _sign,
    "UNICODE": _unicode,
    "CHAR": lambda *args: chr(int(args[0])) if args and args[0] is not None else None,
    "HEX": lambda val: hex(int(val))[2:].upper() if val is not None else None,
    "LTRIM": lambda s, *a: str(s).lstrip(a[0] if a else None) if s is not None else None,
    "RTRIM": lambda s, *a: str(s).rstrip(a[0] if a else None) if s is not None else None,
    "PRINTF": lambda fmt, *a: str(fmt) % tuple(a) if fmt is not None and all(x is not None for x in a) else None,
    "UPPER": lambda s: str(s).upper() if s is not None else None,
    "LOWER": lambda s: str(s).lower() if s is not None else None,
    "TRIM": lambda s, *a: str(s).strip(a[0] if a else None) if s is not None else None,
    "ABS": lambda x: abs(x) if x is not None else None,
    "ROUND": lambda x, *a: round(x, int(a[0])) if a and x is not None else round(x) if x is not None else None,
    "COALESCE": lambda *args: next((a for a in args if a is not None), None),
    "IFNULL": lambda a, b: a if a is not None else b,
    "NULLIF": lambda a, b: None if a == b else a,
    "LENGTH": lambda s: len(str(s)) if s is not None else None,
    "CHAR_LENGTH": lambda s: len(str(s)) if s is not None else None,
}


# ----- paren -----


@handler(exp.Paren)
def _eval_paren(node: exp.Paren, env: Environment) -> Any:
    return _eval(node.this, env)


# =============================================================================
# negate_predicate
# =============================================================================


def negate_predicate(expr: exp.Expression) -> exp.Expression:
    """Return the logical negation of ``expr``.

    ``IS NULL`` and ``IS NOT NULL`` are flipped directly using sqlglot's
    native ``exp.Is`` forms. Other predicates are wrapped in ``NOT`` and
    handed to sqlglot's ``simplify`` so double negations collapse naturally.
    """
    null_parts = null_predicate_parts(expr)
    if null_parts is not None:
        operand, is_null = null_parts
        if is_null:
            return make_is_not_null(operand.copy())
        return make_is_null(operand.copy())
    return simplify(expr.not_())


__all__ = [
    "Symbol",
    "Const",
    "Variable",
    "Environment",
    "concrete",
    "Row",
    "DataType",
    "negate_predicate",
    "tvl_and",
    "tvl_or",
    "tvl_not",
    "fixed_interval_delta",
]
