"""Concrete evaluation of sqlglot expressions under a ParSEval Symbol env.

The rex module provides the vocabulary ParSEval uses to reason about SQL
expressions at the value level. It sits between the AST layer (where
sqlglot expression trees describe query structure) and the solver layer
(where variables get concrete values assigned): sitting in the middle, it
offers a *single* path from an AST node plus an :class:`Environment` to a
Python primitive, and a single vocabulary of :class:`Symbol` objects that
travel through the Instance, through the encoder's expression trees, and
into / out of the solver.

Design
------

* **Tri-state Symbol**. Every ParSEval symbolic value has three states:

    +----------+------------+----------+---------------------+-------------------+
    | State    | is_bound   | is_null  | ``.concrete``       | Meaning           |
    +==========+============+==========+=====================+===================+
    | Unbound  | False      | False    | ``None``            | Free, solver to   |
    |          |            |          |                     | choose            |
    +----------+------------+----------+---------------------+-------------------+
    | NULL     | True       | True     | ``None``            | SQL NULL          |
    +----------+------------+----------+---------------------+-------------------+
    | Bound    | True       | False    | the Python value    | committed value   |
    +----------+------------+----------+---------------------+-------------------+

  The evaluator treats both "unbound" and "NULL" as ``None`` for the
  purposes of value-level arithmetic (SQL-style propagation). Solvers
  that need to distinguish the two cases read the ``is_bound`` flag on
  the :class:`Variable` directly.

* **Class-dispatched handlers**. Each sqlglot expression class has a
  registered handler that produces the Python value of that expression
  under an :class:`Environment`. Handlers are composable: ``x + 1`` calls
  the evaluator recursively for ``x``, gets its value, adds 1. Unknown
  classes fall back to sqlglot's ``executor.env`` for broad built-in
  coverage.

* **Uniform three-valued logic**. The three logical helpers
  (:func:`tvl_and`, :func:`tvl_or`, :func:`tvl_not`) implement SQL's 3VL
  truth tables, and comparison handlers propagate NULL through any
  NULL operand. This is one place — handlers that need to see NULL
  (``IS NULL``, ``COALESCE``, ``IS NOT DISTINCT FROM``) do so explicitly.

* **Type coercion on Const**. :meth:`Const.coerce_to` is the one place
  type conversions live. Handlers that compare or combine values across
  types route through coercion so the rest of the evaluator stays
  type-agnostic.

* **Environment for column resolution**. The encoder builds an
  :class:`Environment` per row (or per outer-correlation key) and hands
  it to :func:`concrete`. The environment supports scope chaining so a
  correlated subquery's inner evaluation can look up outer columns
  naturally, without the AST being mutated.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal
from functools import wraps
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Type, Union

from dateutil import parser as date_parser
from sqlglot import exp, generator
from sqlglot.executor.env import ENV as _SQLGLOT_ENV
from sqlglot.optimizer.simplify import simplify

import functools

from parseval.dtype import DataType
from parseval.helper import like_to_pattern
from parseval.identity import (
    PARSEVAL_COLUMN_ID,
    ColumnId,
    ColumnKind,
    RelationId,
    RelationKind,
    column_id,
    column_identity,
    identifier_name,
    relation_id,
)

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

    A ``Variable`` is the bridge between the ``Instance`` (which stores
    it in rows), the AST (in which it appears as a leaf of expressions),
    and the solver (which chooses its value). It carries the usual
    tri-state slots plus identity back-pointers (``relation_id`` /
    ``column_id`` / ``rowid``) and solver hints (``nullable`` / ``unique`` /
    ``domain``).
    """

    arg_types = {
        "this": True,  # stable name, e.g. ``"T_0003_x"``
        "type": False,
        "concrete": False,
        "is_bound": False,
        "is_null": False,
        # --- Instance identity back-pointers ---
        "relation_id": False,
        "column_id": False,
        "rowid": False,
        # --- solver hints ---
        "nullable": False,
        "unique": False,
        "domain": False,
        "source": False,
    }

    def __init__(self, *args, **kwargs):
        if "_type" in kwargs:
            kwargs["type"] = kwargs.pop("_type")
        if "table" in kwargs or "column" in kwargs:
            raise ValueError(
                "Variable identity must use column_id/relation_id, "
                "not table/column strings"
            )
        col_id = kwargs.get("column_id")
        if not isinstance(col_id, ColumnId):
            raise ValueError("Variable requires column_id")
        if kwargs.get("rowid") is None:
            raise ValueError("Variable requires rowid")
        kwargs.setdefault("relation_id", col_id.relation)
        super().__init__(*args, **kwargs)

    @property
    def name(self) -> str:
        return self.text("this")

    @property
    def column_id(self) -> ColumnId:
        return self.args["column_id"]

    @property
    def relation_id(self) -> RelationId | None:
        return self.args.get("relation_id") or self.column_id.relation

    @property
    def rowid(self) -> Any:
        return self.args["rowid"]

    @property
    def table_name(self) -> Optional[str]:
        relation = self.relation_id
        return (
            relation.name.normalized
            if relation is not None and relation.name
            else None
        )

    @property
    def column_name(self) -> str:
        return self.column_id.name.normalized

    @property
    def concrete(self) -> Any:
        # If the Variable has been explicitly bound we honour that; otherwise
        # we return whatever value was stored at construction time (so legacy
        # callers that pass ``Variable(this=..., concrete=v)`` still work).
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


class ITE(Symbol):
    """Symbolic if-then-else (``CASE WHEN cond THEN a ELSE b END``).

    Kept as an explicit node because downstream branch analysis wants to
    treat each arm of a conditional as a first-class decision site.
    """

    arg_types = {
        "this": True,
        "true_branch": True,
        "false_branch": True,
    }

    @property
    def condition(self) -> Symbol:
        return self.this

    @property
    def true_branch(self) -> Symbol:
        return self.args.get("true_branch")

    @property
    def false_branch(self) -> Symbol:
        return self.args.get("false_branch")


# Register generator transforms so ``Symbol`` et al. pretty-print when a
# sqlglot ``Generator`` runs over an AST containing them.
for _klass in [Symbol, Const, Variable, ITE, Row]:
    generator.Generator.TRANSFORMS[_klass] = (
        lambda self, expression: expression.sql(dialect=self.dialect)
    )

# =============================================================================
# Environment
# =============================================================================


class Environment:
    """Column → value resolver with scope chaining for correlated subqueries.

    Construct with a dict of bindings keyed by :class:`ColumnId`. Resolution
    uses ``PARSEVAL_COLUMN_ID`` metadata on :class:`exp.Column` nodes for
    direct :class:`ColumnId` lookup. No name-based fallback — columns must
    carry identity metadata.

    Environments are immutable in intent: to extend with new bindings for
    a nested scope, call :meth:`extend`, which returns a child
    environment rather than mutating the parent.
    """

    __slots__ = ("_bindings", "_outer")

    def __init__(
        self,
        bindings: Optional[Dict[ColumnId, Any]] = None,
        outer: Optional["Environment"] = None,
    ) -> None:
        self._bindings: Dict[ColumnId, Any] = dict(bindings) if bindings else {}
        self._outer: Optional[Environment] = outer

    def _resolve_by_id(self, col_id: ColumnId) -> Any:
        """Direct ColumnId lookup with source_column_id chain fallback."""
        if col_id in self._bindings:
            return self._bindings[col_id]
        source = col_id.source_column_id
        while source is not None:
            if source in self._bindings:
                return self._bindings[source]
            source = source.source_column_id
        return _MISSING

    def resolve(self, column: Union[exp.Column, ColumnId]) -> Any:
        """Return the value bound to ``column``, or ``None`` if unresolved."""
        if isinstance(column, ColumnId):
            result = self._resolve_by_id(column)
            if result is not _MISSING:
                return result
        elif isinstance(column, exp.Column):
            col_id = column_identity(column)
            if col_id is not None:
                result = self._resolve_by_id(col_id)
                if result is not _MISSING:
                    return result

        if self._outer is not None:
            return self._outer.resolve(column)
        return None

    def bind(self, column: ColumnId, value: Any) -> None:
        """Bind ``column`` to ``value`` in this environment."""
        self._bindings[column] = value

    def extend(self, bindings: Dict[ColumnId, Any]) -> "Environment":
        """Return a child environment layering ``bindings`` on top of this one."""
        return Environment(bindings=bindings, outer=self)

    def contains(self, column: Union[exp.Column, ColumnId]) -> bool:
        """Return True if ``column`` resolves in this or any outer env."""
        if isinstance(column, ColumnId):
            if self._resolve_by_id(column) is not _MISSING:
                return True
        elif isinstance(column, exp.Column):
            col_id = column_identity(column)
            if col_id is not None and self._resolve_by_id(col_id) is not _MISSING:
                return True
        return self._outer.contains(column) if self._outer is not None else False


_MISSING = object()


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
    literal arithmetic). Columns that don't resolve in ``env`` produce
    ``None`` — which the SQL 3VL propagation then spreads upward.
    """
    if env is None:
        env = Environment()
    return _eval(expr, env)


def _eval(node: Any, env: Environment) -> Any:
    if node is None:
        return None
    # Walk MRO so subclasses inherit their parent's handler if none is
    # registered for the specific type.
    for cls in type(node).__mro__:
        fn = _HANDLERS.get(cls)
        if fn is not None:
            return fn(node, env)
    return _eval_via_sqlglot_env(node, env)


def _eval_via_sqlglot_env(node: exp.Expression, env: Environment) -> Any:
    """Fallback for sqlglot nodes we haven't explicitly handled.

    Routes through :data:`sqlglot.executor.env.ENV` with operand values
    obtained by recursive evaluation. Returns ``None`` on lookup miss or
    evaluation error — safer than raising, given the breadth of SQL.
    """
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


def _null_aware(func: Callable[..., Any]) -> Callable[..., Any]:
    """Binary-op decorator: NULL in → NULL out."""

    @wraps(func)
    def wrapper(a: Any, b: Any) -> Any:
        if a is None or b is None:
            return None
        return func(a, b)

    return wrapper


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
    # Legacy / encoder convention: columns may be stamped with a concrete
    # value via ``column.set("concrete", ...)`` during row-by-row
    # evaluation. Honor that first so existing callers keep working, then
    # fall back to the Environment for Clean-API callers.
    if "concrete" in node.args:
        stamped = node.args["concrete"]
        if isinstance(stamped, Symbol):
            return stamped.concrete
        return stamped
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
    # For unbound variables, try the environment by ColumnId.
    resolved = env.resolve(node.column_id)
    if isinstance(resolved, Symbol):
        return resolved.concrete
    if resolved is not None:
        return resolved
    return node.args.get("concrete")


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


def _compare(op: Callable[[Any, Any], bool]) -> _Handler:
    """Build a NULL-propagating comparison handler from a Python binary op."""

    def fn(node: exp.Expression, env: Environment) -> Any:
        l, r = _eval(node.left, env), _eval(node.right, env)
        if l is None or r is None:
            return None
        l, r = _coerce_comparable(l, r)
        try:
            return op(l, r)
        except TypeError:
            return None

    return fn


_HANDLERS[exp.EQ] = _compare(lambda a, b: a == b)
_HANDLERS[exp.NEQ] = _compare(lambda a, b: a != b)
_HANDLERS[exp.GT] = _compare(lambda a, b: a > b)
_HANDLERS[exp.GTE] = _compare(lambda a, b: a >= b)
_HANDLERS[exp.LT] = _compare(lambda a, b: a < b)
_HANDLERS[exp.LTE] = _compare(lambda a, b: a <= b)


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
    if value is None:
        return None
    expressions = node.args.get("expressions") or []
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


# ----- ordered (pass-through used inside ORDER BY) -----


@handler(exp.Ordered)
def _eval_ordered(node: exp.Ordered, env: Environment) -> Any:
    return _eval(node.this, env)


# ----- dialect functions (Anonymous dispatch) -----


@handler(exp.Anonymous)
def _eval_anonymous(node: exp.Anonymous, env: Environment) -> Any:
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
}


# ----- TimeToStr (STRFTIME) -----


@handler(exp.TimeToStr)
def _eval_time_to_str(node: exp.TimeToStr, env: Environment) -> Any:
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


# ----- paren -----


@handler(exp.Paren)
def _eval_paren(node: exp.Paren, env: Environment) -> Any:
    return _eval(node.this, env)


# ----- ITE -----


@handler(ITE)
def _eval_ite(node: ITE, env: Environment) -> Any:
    cond = _eval(node.condition, env)
    if cond is None:
        return None
    return _eval(node.true_branch if cond else node.false_branch, env)


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


# =============================================================================
# Column metadata (schema hints stamped on exp.Column nodes)
# =============================================================================


def column_meta(col: exp.Column) -> Optional[dict]:
    """Read schema hints stamped on a Column by :meth:`Plan._annotate`.

    Returns a dict with keys ``table``, ``nullable``, ``unique``, ``domain``
    (a :class:`DataType`), or ``None`` if the column was not enriched.
    """
    raw = col.args.get("_parseval_meta")
    if raw is None:
        return None
    # Stored as a frozenset of (key, value) pairs for hashability.
    return dict(raw)


def set_column_meta(col: exp.Column, meta: dict) -> None:
    """Stamp schema hints onto a Column node.

    Internally stored as a frozenset of ``(key, value)`` pairs so the
    Column remains hashable (required by sqlglot's ``simplify`` and other
    passes that hash expression nodes).
    """
    col.set("_parseval_meta", frozenset(meta.items()))


# =============================================================================
# Compatibility shims (to be removed once consumers migrate)
# =============================================================================


def _concrete_shim(self: exp.Expression) -> Any:
    """``expr.concrete`` — legacy property wrapper around :func:`concrete`.

    Provided for consumers that still read ``expression.concrete`` as a
    property. Does one evaluation under an empty :class:`Environment`
    each call; callers that need repeated evaluation under a real
    environment should migrate to the function form.
    """
    return _eval(self, Environment())


def _datatype_shim(self: exp.Expression) -> Optional[DataType]:
    """``expr.datatype`` — legacy property resolving the expression's type."""
    if self.type is not None:
        return self.type
    raw = self.args.get("_type")
    if raw is None:
        return None
    return DataType.build(raw)


def _column_ref_shim(self: exp.Column) -> int:
    """``column.ref`` — legacy positional reference slot."""
    return self.args.get("ref", 0)


exp.Expression.concrete = property(_concrete_shim)  # type: ignore[attr-defined]
exp.Expression.datatype = property(_datatype_shim)  # type: ignore[attr-defined]
exp.Column.ref = property(_column_ref_shim)  # type: ignore[attr-defined]


__all__ = [
    # Symbol vocabulary
    "Symbol",
    "Const",
    "Variable",
    "ITE",
    # Environment + evaluator
    "Environment",
    "concrete",
    "handler",
    # 3VL
    "tvl_and",
    "tvl_or",
    "tvl_not",
    # Re-exports
    "Row",
    "DataType",
    # Utilities
    "make_is_null",
    "make_is_not_null",
    "is_null_predicate",
    "is_not_null_predicate",
    "null_predicate_parts",
    "negate_predicate",
    "column_meta",
    "set_column_meta",
]
