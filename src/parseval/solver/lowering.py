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

    Checks (in order):
    1. Column's own table qualifier → alias_map → instance tables
    2. Column name lookup across all candidate tables
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
    """Find the canonical column name in the instance (case-insensitive, space-preserving)."""
    if table not in instance.tables:
        return None
    lower = col_name.lower()
    return next((s for s in instance.tables[table] if s.lower() == lower), None)


def resolve_table_name(name: str, instance: Instance, alias_map: Optional[Dict[str, str]] = None) -> str:
    """Resolve a table name/alias to the real instance table name."""
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
    """A lowered constraint on a single column."""
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
    """Lower a SQL expression into column predicates + residuals.

    Returns:
        (predicates, residuals) where predicates are directly satisfiable
        column constraints and residuals are expressions that need SMT or
        can't be lowered (subqueries, complex arithmetic, etc.)
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
    """Recursively decompose and lower predicates."""
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
    """Try to lower a single atom into a ColumnPredicate."""

    # EQ: col = literal
    if isinstance(atom, exp.EQ):
        col, val = _extract_col_literal(atom)
        if col and val is not None:
            return _make_pred(col, "=", val, instance, tables, alias_map)
        # strftime/temporal function = literal
        col, val = _extract_temporal_func(atom)
        if col and val is not None:
            return _make_pred(col, "=", val, instance, tables, alias_map)

    # GT
    elif isinstance(atom, exp.GT):
        col, val = _extract_col_literal(atom)
        if col and isinstance(val, (int, float)):
            return _make_pred(col, ">", val, instance, tables, alias_map)
        col, val = _extract_temporal_func(atom)
        if col and val is not None:
            if isinstance(val, date):
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
            if isinstance(val, date):
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
        if isinstance(col, exp.Column) and isinstance(low, exp.Literal):
            return _make_pred(col, ">=", concrete(low), instance, tables, alias_map)
        # Temporal BETWEEN
        if isinstance(col, (exp.TimeToStr, exp.Anonymous)):
            inner_col = next(col.find_all(exp.Column), None)
            if inner_col and isinstance(low, exp.Literal):
                val = concrete(low)
                if isinstance(val, str) and val.isdigit():
                    return _make_pred(inner_col, "=", date(int(val), 6, 15), instance, tables, alias_map)

    # LIKE
    elif isinstance(atom, exp.Like):
        col = atom.this
        pattern = atom.expression
        if isinstance(col, exp.Column) and isinstance(pattern, exp.Literal):
            pat = str(pattern.this).replace("%", "x").replace("_", "a")
            return _make_pred(col, "=", pat, instance, tables, alias_map)

    # IS NULL
    elif isinstance(atom, exp.Is):
        left = atom.this
        right = atom.expression
        if isinstance(left, exp.Column) and isinstance(right, exp.Null):
            return _make_pred(left, "is_null", True, instance, tables, alias_map)

    return None


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
    """Extract (column, python_value) from a binary comparison."""
    left, right = node.this, node.expression
    if isinstance(left, exp.Column) and isinstance(right, (exp.Literal, exp.Boolean)):
        return left, concrete(right)
    if isinstance(right, exp.Column) and isinstance(left, (exp.Literal, exp.Boolean)):
        return right, concrete(left)
    return None, None


def _extract_temporal_func(node: exp.Expression) -> Tuple[Optional[exp.Column], Optional[Any]]:
    """Handle strftime('%Y', col) patterns."""
    left = node.this
    right = getattr(node, "expression", None)
    func_side, lit_side = None, None
    if isinstance(left, (exp.TimeToStr, exp.Anonymous)):
        func_side, lit_side = left, right
    elif right and isinstance(right, (exp.TimeToStr, exp.Anonymous)):
        func_side, lit_side = right, left
    if func_side and lit_side:
        inner_col = next(func_side.find_all(exp.Column), None)
        if inner_col and isinstance(lit_side, exp.Literal):
            val = concrete(lit_side)
            if isinstance(val, str) and val.isdigit():
                return inner_col, date(int(val), 6, 15)
    return None, None


# =============================================================================
# Predicate negation
# =============================================================================


def negate_predicate_value(op: str, value: Any) -> Tuple[str, Any]:
    """Negate a predicate for negative branch generation."""
    if op == "=" and isinstance(value, (int, float)):
        return "=", value + 1
    if op == "=" and isinstance(value, str):
        return "=", value + "_neg"
    if op == ">":
        return "<=", value
    if op == ">=":
        return "<", value
    if op == "<":
        return ">=", value
    if op == "<=":
        return ">", value
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
    """Union-Find for column equivalence classes.

    Tracks which columns must share the same value. Used by both the
    speculative component (JOIN coordination, GROUP BY) and the solver
    (constraint propagation).
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
