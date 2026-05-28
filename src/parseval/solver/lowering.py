"""Centralized SQL resolution and predicate lowering utilities.

This module is the single source of truth for:
1. Table/column resolution (alias → real table, column name matching)
2. Predicate lowering (SQL expression → column constraints)

Every module that needs to resolve columns or extract constraints from
predicates uses these utilities — no more duplicated logic across
speculate.py, unified.py, and constraints.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.helper import normalize_name
from parseval.instance import Instance
from parseval.plan.rex import concrete


# =============================================================================
# Column/Table Resolution
# =============================================================================


def resolve_table(
    col: exp.Column,
    candidate_tables: Tuple[str, ...],
    instance: Instance,
    alias_map: Optional[Dict[str, str]] = None,
) -> str:
    """Resolve which real table a column belongs to.

    Resolution order:
    1. Column's own table qualifier, checked against alias_map and instance tables.
    2. Column-name lookup across all candidate tables (for unqualified columns).

    Args:
        col: A sqlglot Column expression.
        candidate_tables: Tuple of table names to search.
        instance: The Instance providing real table names and column info.
        alias_map: Optional mapping of alias names to real table names.

    Returns:
        The resolved real table name, or the first candidate if resolution fails.
    """
    alias_map = alias_map or {}

    if col.table:
        # Try alias resolution.
        table = alias_map.get(col.table.lower(), col.table)
        table = normalize_name(table)
        if table in instance.tables:
            return table
        # Try column-name matching across candidates.
        col_name = col.name.lower()
        for candidate in candidate_tables:
            real = alias_map.get(candidate.lower(), candidate)
            real = normalize_name(real)
            if real in instance.tables and _has_column(instance, real, col_name):
                return real

    # No table qualifier — find first table with this column.
    col_name = col.name.lower()
    for candidate in candidate_tables:
        real = alias_map.get(candidate.lower(), candidate)
        real = normalize_name(real)
        if real in instance.tables and _has_column(instance, real, col_name):
            return real

    return candidate_tables[0] if candidate_tables else ""


def match_column(instance: Instance, table: str, col_name: str) -> Optional[str]:
    """Find the canonical column name in the instance (case-insensitive, space-preserving).

    Args:
        instance: The Instance to look up columns in.
        table: The table name.
        col_name: The column name to match.

    Returns:
        The canonical column name, or None if not found.
    """
    if table not in instance.tables:
        return None
    lower = col_name.lower()
    return next((s for s in instance.tables[table] if s.lower() == lower), None)


def resolve_table_name(name: str, instance: Instance, alias_map: Optional[Dict[str, str]] = None) -> str:
    """Resolve a table name or alias to the real instance table name.

    Uses alias_map first, then falls back to normalize_name matching.
    """
    alias_map = alias_map or {}
    real = alias_map.get(name.lower(), name)
    real = normalize_name(real)
    return real if real in instance.tables else name


def _has_column(instance: Instance, table: str, col_name_lower: str) -> bool:
    return any(s.lower() == col_name_lower for s in instance.tables[table])


# =============================================================================
# Predicate Lowering
# =============================================================================


@dataclass
class ColumnPredicate:
    """A lowered constraint on a single column, extracted from SQL.

    Attributes:
        table: Resolved table name.
        column: Canonical column name from the instance.
        op: Operator — ``"="``, ``">"``, ``">="``, ``"<"``, ``"<="``, ``"!="``,
            ``"in"``, ``"like"``, ``"is_null"``, etc.
        value: The comparison value (Python scalar or list for ``in``).
    """
    table: str
    column: str  # canonical name from instance
    op: str      # "=" / ">" / ">=" / "<" / "<=" / "!=" / "in" / "like" / "is_null"
    value: Any


def lower_predicates(
    expr: exp.Expression,
    instance: Instance,
    candidate_tables: Tuple[str, ...] = (),
    alias_map: Optional[Dict[str, str]] = None,
) -> Tuple[List[ColumnPredicate], List[exp.Expression]]:
    """Lower a SQL expression into column predicates + residual expressions.

    Walks the AST recursively, extracting simple column constraints into
    :class:`ColumnPredicate` objects. Expressions that can't be lowered
    (subqueries, complex arithmetic, NOT, OR) are returned as residuals
    for SMT fallback.

    Args:
        expr: A sqlglot expression to lower.
        instance: The Instance for table/column resolution.
        candidate_tables: Tuple of table names to search for unqualified columns.
        alias_map: Optional mapping of table aliases to real names.

    Returns:
        Tuple of (predicates, residuals). Predicates are directly satisfiable
        column constraints; residuals need SMT or can't be lowered.
    """
    predicates: List[ColumnPredicate] = []
    residuals: List[exp.Expression] = []
    _lower_recursive(expr, instance, candidate_tables, alias_map or {}, predicates, residuals)
    return predicates, residuals


def _lower_recursive(
    expr: exp.Expression,
    instance: Instance,
    tables: Tuple[str, ...],
    alias_map: Dict[str, str],
    predicates: List[ColumnPredicate],
    residuals: List[exp.Expression],
) -> None:
    """Recursively decompose and lower predicates into column constraints.

    AND expressions are flattened; OR takes the left branch for satisfiability;
    NOT and subqueries are pushed to residuals.
    """
    if isinstance(expr, exp.And):
        _lower_recursive(expr.left, instance, tables, alias_map, predicates, residuals)
        _lower_recursive(expr.right, instance, tables, alias_map, predicates, residuals)
        return
    if isinstance(expr, exp.Paren):
        _lower_recursive(expr.this, instance, tables, alias_map, predicates, residuals)
        return
    if isinstance(expr, exp.Or):
        # Take first branch for satisfiability.
        _lower_recursive(expr.left, instance, tables, alias_map, predicates, residuals)
        return
    if isinstance(expr, exp.Not):
        # Skip NOT — can't easily extract positive assignments.
        residuals.append(expr)
        return
    # Skip atoms with subqueries.
    if expr.find(exp.Subquery):
        residuals.append(expr)
        return

    # Try to lower this atom.
    pred = _lower_atom(expr, instance, tables, alias_map)
    if pred:
        predicates.append(pred)
    else:
        residuals.append(expr)


def _lower_atom(
    atom: exp.Expression,
    instance: Instance,
    tables: Tuple[str, ...],
    alias_map: Dict[str, str],
) -> Optional[ColumnPredicate]:
    """Try to lower a single atom (comparison expression) into a ColumnPredicate.

    Handles EQ, GT, GTE, LT, LTE, NEQ, BETWEEN, LIKE, IS NULL, and IN operators,
    including function-wrapped variants (SUBSTR, CAST, UPPER, LENGTH, etc.).
    """

    # EQ: col = literal
    if isinstance(atom, exp.EQ):
        col, val = _extract_col_literal(atom)
        if col and val is not None:
            return _make_pred(col, "=", val, instance, tables, alias_map)
        # strftime/temporal function = literal
        col, val = _extract_temporal_func(atom)
        if col and val is not None:
            return _make_pred(col, "=", val, instance, tables, alias_map)
        # Function-wrapped: SUBSTR(col, ...) = 'val', CAST(col) = val, etc.
        col, val = _extract_func_column(atom)
        if col and val is not None:
            return _make_pred(col, "=", val, instance, tables, alias_map)

    # GT
    elif isinstance(atom, exp.GT):
        col, val = _extract_col_literal(atom)
        if col and isinstance(val, (int, float)):
            return _make_pred(col, ">", val, instance, tables, alias_map)
        col, val = _extract_temporal_func(atom)
        if col and val is not None:
            if isinstance(val, str) and len(val) >= 10 and '-' in val:
                # Date string: for GT, use a date one year later
                try:
                    year = int(val[:4])
                    val = f"{year + 1}-06-15"
                except ValueError:
                    pass
            elif isinstance(val, date):
                val = date(val.year + 1, val.month, val.day)
            return _make_pred(col, "=", val, instance, tables, alias_map)

    # GTE
    elif isinstance(atom, exp.GTE):
        col, val = _extract_col_literal(atom)
        if col and isinstance(val, (int, float)):
            return _make_pred(col, ">=", val, instance, tables, alias_map)

    # LT
    elif isinstance(atom, exp.LT):
        col, val = _extract_col_literal(atom)
        if col and isinstance(val, (int, float)):
            return _make_pred(col, "<", val, instance, tables, alias_map)
        col, val = _extract_temporal_func(atom)
        if col and val is not None:
            if isinstance(val, str) and len(val) >= 10 and '-' in val:
                try:
                    year = int(val[:4])
                    val = f"{year - 1}-06-15"
                except ValueError:
                    pass
            elif isinstance(val, date):
                val = date(val.year - 1, val.month, val.day)
            return _make_pred(col, "=", val, instance, tables, alias_map)

    # LTE
    elif isinstance(atom, exp.LTE):
        col, val = _extract_col_literal(atom)
        if col and isinstance(val, (int, float)):
            return _make_pred(col, "<=", val, instance, tables, alias_map)

    # NEQ
    elif isinstance(atom, exp.NEQ):
        col, val = _extract_col_literal(atom)
        if col and val is not None:
            return _make_pred(col, "!=", val, instance, tables, alias_map)

    # BETWEEN
    elif isinstance(atom, exp.Between):
        col = atom.this
        low = atom.args.get("low")
        high = atom.args.get("high")
        if isinstance(col, exp.Column) and isinstance(low, exp.Literal):
            low_val = concrete(low)
            # Use >= for lower bound (inclusive range)
            return _make_pred(col, ">=", low_val, instance, tables, alias_map)
        # Temporal BETWEEN
        if isinstance(col, (exp.TimeToStr, exp.Anonymous)):
            inner_col = next(col.find_all(exp.Column), None)
            if inner_col and isinstance(low, exp.Literal):
                val = concrete(low)
                if isinstance(val, str) and val.isdigit():
                    return _make_pred(inner_col, "=", f"{val}-06-15", instance, tables, alias_map)
                elif isinstance(val, str):
                    return _make_pred(inner_col, "=", val, instance, tables, alias_map)
        # Function-wrapped column: date(SUBSTR(col, ...)) BETWEEN 'date1' AND 'date2'
        inner_col = next(col.find_all(exp.Column), None) if not isinstance(col, exp.Column) else None
        if inner_col and isinstance(low, exp.Literal):
            low_val = concrete(low)
            if isinstance(low_val, str):
                return _make_pred(inner_col, "=", low_val, instance, tables, alias_map)

    # LIKE
    elif isinstance(atom, exp.Like):
        col = atom.this
        pattern = atom.expression
        if isinstance(col, exp.Column) and isinstance(pattern, exp.Literal):
            pat = str(pattern.this)
            return _make_pred(col, "like", pat, instance, tables, alias_map)

    # IS NULL
    elif isinstance(atom, exp.Is):
        left = atom.this
        right = atom.expression
        if isinstance(left, exp.Column) and isinstance(right, exp.Null):
            return _make_pred(left, "is_null", True, instance, tables, alias_map)

    # IN (literal list): col IN ('a', 'b', 'c')
    elif isinstance(atom, exp.In):
        col = atom.this
        expressions = atom.args.get("expressions") or []
        if isinstance(col, exp.Column) and expressions and not atom.args.get("query"):
            values = [concrete(e) for e in expressions if isinstance(e, (exp.Literal, exp.Boolean))]
            if values:
                # Pick the first value from the IN list.
                return _make_pred(col, "=", values[0], instance, tables, alias_map)

    return None


def _extract_func_column(node: exp.Expression) -> Tuple[Optional[exp.Column], Optional[Any]]:
    """Extract column + value from function-wrapped comparisons.

    Handles patterns like SUBSTR(col, 1, 4) = 'val', CAST(col AS INT) > 5,
    UPPER(col) = 'VALUE', LENGTH(col) > 5, REPLACE(col, 'x', 'y') = 'val', etc.

    Returns:
        Tuple of (inner Column expression, comparison value), or (None, None).
    """
    left, right = node.this, node.expression

    # Determine which side is the function and which is the literal.
    func_side, lit_side = None, None
    if isinstance(right, (exp.Literal, exp.Boolean)) and not isinstance(left, (exp.Column, exp.Literal, exp.Boolean)):
        func_side, lit_side = left, right
    elif isinstance(left, (exp.Literal, exp.Boolean)) and not isinstance(right, (exp.Column, exp.Literal, exp.Boolean)):
        func_side, lit_side = right, left
    if func_side is None or lit_side is None:
        return None, None

    val = concrete(lit_side)
    if val is None:
        return None, None

    # Find the column inside the function.
    inner_col = next(func_side.find_all(exp.Column), None)
    if inner_col is None:
        return None, None

    # SUBSTR: the column should contain the target value.
    if isinstance(func_side, exp.Substring):
        # SUBSTR(col, start, len) = 'val' → col should start with/contain 'val'
        start = concrete(func_side.args.get("start"))
        if start == 1 and isinstance(val, str):
            # col starts with val → set col = val + padding
            return inner_col, val + "xxx"
        return inner_col, str(val) if isinstance(val, str) else val

    # CAST: pass through the value directly.
    if isinstance(func_side, exp.Cast):
        return inner_col, val

    # UPPER/LOWER: pass through (case doesn't matter for generation).
    if isinstance(func_side, (exp.Upper, exp.Lower)):
        return inner_col, val

    # LENGTH: generate a string of that length.
    if isinstance(func_side, exp.Length):
        if isinstance(val, (int, float)) and isinstance(node, exp.GT):
            return inner_col, "a" * (int(val) + 1)
        if isinstance(val, (int, float)) and isinstance(node, exp.EQ):
            return inner_col, "a" * int(val)
        return None, None

    # REPLACE: approximate — use the literal value directly.
    if hasattr(exp, 'Replace') and isinstance(func_side, exp.Replace):
        return inner_col, val

    # IIF/CASE: too complex, skip.
    # INSTR: col contains substring.
    if hasattr(func_side, "key") and func_side.key.upper() == "INSTR":
        return None, None

    # Generic: if we can find a column, use the literal value.
    return inner_col, val


def _make_pred(
    col: exp.Column, op: str, value: Any,
    instance: Instance, tables: Tuple[str, ...], alias_map: Dict[str, str],
) -> Optional[ColumnPredicate]:
    """Create a ColumnPredicate with resolved table/column names."""
    table = resolve_table(col, tables, instance, alias_map)
    matched = match_column(instance, table, col.name)
    if not matched:
        return None
    return ColumnPredicate(table=table, column=matched, op=op, value=value)


def _extract_col_literal(node: exp.Expression) -> Tuple[Optional[exp.Column], Optional[Any]]:
    """Extract (column, Python value) from a binary comparison if one side is a literal.

    Returns (None, None) if neither side is a column-literal pair.
    """
    left, right = node.this, node.expression
    if isinstance(left, exp.Column) and isinstance(right, (exp.Literal, exp.Boolean)):
        return left, concrete(right)
    if isinstance(right, exp.Column) and isinstance(left, (exp.Literal, exp.Boolean)):
        return right, concrete(left)
    return None, None


def _extract_temporal_func(node: exp.Expression) -> Tuple[Optional[exp.Column], Optional[Any]]:
    """Extract column and value from temporal function patterns.

    Handles:
    - STRFTIME('%Y', col) = '2024' → col = '2024-06-15'
    - STRFTIME('%m', col) = '10' → col = '2024-10-15'
    - STRFTIME('%Y', col) = '1991' → col = '1991-06-15'
    - TIME_TO_STR(col, '%Y') = '2024' → same as above
    """
    left = node.this
    right = getattr(node, "expression", None)
    func_side, lit_side = None, None
    if isinstance(left, exp.TimeToStr):
        func_side, lit_side = left, right
    elif isinstance(left, exp.Anonymous) and _is_temporal_func_name(left):
        func_side, lit_side = left, right
    elif right and isinstance(right, exp.TimeToStr):
        func_side, lit_side = right, left
    elif right and isinstance(right, exp.Anonymous) and _is_temporal_func_name(right):
        func_side, lit_side = right, left
    if func_side and lit_side:
        inner_col = next(func_side.find_all(exp.Column), None)
        if inner_col and isinstance(lit_side, exp.Literal):
            val = concrete(lit_side)
            if not isinstance(val, str):
                return None, None
            # Determine format from TimeToStr
            fmt = None
            if isinstance(func_side, exp.TimeToStr):
                fmt_node = func_side.args.get("format")
                if isinstance(fmt_node, exp.Literal):
                    fmt = fmt_node.this
                elif fmt_node:
                    fmt = str(fmt_node).strip("'\"")
            # Generate appropriate date based on format
            if fmt == "%Y" and val.isdigit():
                return inner_col, f"{val}-06-15"
            elif fmt == "%m" and val.isdigit():
                return inner_col, f"2024-{val.zfill(2)}-15"
            elif fmt == "%d" and val.isdigit():
                return inner_col, f"2024-06-{val.zfill(2)}"
            elif fmt == "%Y-%m" and len(val) >= 6:
                return inner_col, f"{val}-15"
            elif val.isdigit():
                return inner_col, f"{val}-06-15"
    return None, None


def _is_temporal_func_name(node: exp.Anonymous) -> bool:
    """Check if an Anonymous function node represents a temporal function.

    Checks the function name against known temporal functions: STRFTIME,
    DATE, DATETIME, JULIANDAY, TIME. Falls back to parsing the SQL
    representation if the name attribute is unavailable.
    """
    name = (node.name or "").upper() if hasattr(node, "name") else ""
    if not name:
        # Try to get name from the SQL representation.
        sql = node.sql()[:20].upper()
        name = sql.split("(")[0].strip()
    return name in ("STRFTIME", "DATE", "DATETIME", "JULIANDAY", "TIME")


# =============================================================================
# Predicate negation
# =============================================================================


def negate_predicate_value(op: str, value: Any) -> Tuple[str, Any]:
    """Negate a predicate (op, value) pair for generating the negative branch.

    Converts ``=`` to ``!=`` (by changing the value), ``>`` to ``<=``,
    ``like`` to ``= "__no_match__"``, etc. Used when the symbolic engine
    needs the complementary constraint.

    Args:
        op: The operator string (e.g. "=", ">", "is_null").
        value: The comparison value.

    Returns:
        A (negated_op, negated_value) tuple.
    """
    if op == "=" and isinstance(value, (int, float)):
        return "=", value + 1
    if op == "=" and isinstance(value, str):
        # For date-like strings, change the value rather than appending _neg
        if len(value) >= 10 and value[4:5] == '-' and value[7:8] == '-':
            try:
                year = int(value[:4])
                return "=", f"{year + 1}{value[4:]}"
            except ValueError:
                pass
        return "=", value + "_neg"
    if op == "=" and isinstance(value, bool):
        return "=", not value
    if op == ">":
        return "<=", value
    if op == ">=":
        return "<", value
    if op == "<":
        return ">=", value
    if op == "<=":
        return ">", value
    if op == "is_null":
        return "not_null", True
    if op == "not_null":
        return "is_null", True
    if op == "like":
        return "=", "__no_match__"
    if op == "!=":
        return "=", value
    return "=", value


__all__ = [
    "ColumnPredicate",
    "lower_predicates",
    "match_column",
    "negate_predicate_value",
    "resolve_table",
    "resolve_table_name",
    "ColumnUnionFind",
]


# =============================================================================
# Column Equivalence (Union-Find)
# =============================================================================


class ColumnUnionFind:
    """Union-Find (Disjoint Set Union) for tracking column equivalence classes.

    Used by the solver to propagate equality constraints across JOINs,
    GROUP BY, and speculative execution. Supports path compression and
    union by rank.

    Methods:
        find(x): Find the representative of x's set.
        union(x, y): Merge the sets containing x and y.
        same(x, y): Check if x and y are in the same set.
        groups(): Return all equivalence groups as a dict.
        members(): Return all tracked elements.
    """

    def __init__(self):
        self._parent: Dict[str, str] = {}
        self._rank: Dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._rank[rx] < self._rank[ry]:
            self._parent[rx] = ry
        elif self._rank[rx] > self._rank[ry]:
            self._parent[ry] = rx
        else:
            self._parent[ry] = rx
            self._rank[rx] += 1

    def same(self, x: str, y: str) -> bool:
        return self.find(x) == self.find(y)

    def groups(self) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        for x in self._parent:
            rep = self.find(x)
            result.setdefault(rep, []).append(x)
        return result

    def members(self) -> Set[str]:
        return set(self._parent.keys())
