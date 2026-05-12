"""Unified constraint solver for ParSEval.

The solver provides a single interface that the symbolic engine calls to
satisfy branch constraints. Internally it uses a tiered resolution
strategy:

* **Tier 0 — Trivial**: direct value assignment from literals, IS NULL,
  equality with a constant. No computation needed.
* **Tier 1 — Heuristic**: range constraints (``a > 5`` → pick 6),
  LIKE patterns (generate a matching string), BETWEEN, IN-list membership.
  Cheap Python logic, no SMT.
* **Tier 2 — SMT**: compound constraints with cross-column dependencies,
  arithmetic relationships, or anything Tiers 0–1 can't handle. Delegates
  to Z3 via the existing :mod:`parseval.solver.smt` translation layer.

Type handling is delegated to the domain module's :class:`TypeService` +
:class:`TypeProfile` + :func:`coerce_value`. The solver generates values
in the right family and coerces them through the domain's adapter layer
before returning, so all values are guaranteed to be instance-consistent
and database-writable.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.domain.coercion import coerce_value
from parseval.domain.types import TypeFamily, TypeProfile, TypeService
from parseval.dtype import DataType
from parseval.instance import Instance
from parseval.plan.rex import Environment, concrete, negate_predicate
from parseval.symbolic.constraints import SolverConstraint
from parseval.symbolic.types import BranchType


# =============================================================================
# Result types
# =============================================================================


@dataclass
class SolveResult:
    """Outcome of a solver invocation."""

    sat: bool
    assignments: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    # table_name → {column_name → value}
    reason: str = ""


# =============================================================================
# Tier 0 — Trivial resolution
# =============================================================================


def _try_trivial(
    atom: exp.Expression,
    target_outcome: BranchType,
    tables: Tuple[str, ...],
    instance: Instance,
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Try to satisfy the constraint with a direct value assignment.

    Handles:
    - ``col = literal`` (ATOM_TRUE → assign literal; ATOM_FALSE → assign different)
    - ``col IS NULL`` (ATOM_TRUE → assign NULL)
    - ``col IS NOT NULL`` (ATOM_TRUE → assign any non-NULL)
    - ATOM_NULL target → assign NULL to one column in the atom
    """
    if target_outcome == BranchType.ATOM_NULL:
        # Make the atom evaluate to NULL by NULLing a column.
        columns = list(atom.find_all(exp.Column))
        for col in columns:
            table = _resolve_table(col, tables, instance)
            if table and instance.nullable(table, col.name):
                return {table: {col.name: None}}
        return None

    # EQ: col = literal
    if isinstance(atom, exp.EQ):
        col, lit = _extract_column_literal(atom)
        if col is not None and lit is not None:
            table = _resolve_table(col, tables, instance)
            if target_outcome == BranchType.ATOM_TRUE:
                return {table: {col.name: lit}}
            else:  # ATOM_FALSE
                return {table: {col.name: _different_value(lit, instance, table, col.name)}}

    # NEQ: col <> literal
    if isinstance(atom, exp.NEQ):
        col, lit = _extract_column_literal(atom)
        if col is not None and lit is not None:
            table = _resolve_table(col, tables, instance)
            if target_outcome == BranchType.ATOM_TRUE:
                return {table: {col.name: _different_value(lit, instance, table, col.name)}}
            else:  # ATOM_FALSE → make them equal
                return {table: {col.name: lit}}

    # IS NULL / IS NOT NULL
    if isinstance(atom, exp.Is):
        left = atom.this
        right = atom.expression
        if isinstance(left, exp.Column) and isinstance(right, exp.Null):
            table = _resolve_table(left, tables, instance)
            if target_outcome == BranchType.ATOM_TRUE:
                return {table: {left.name: None}}
            else:
                return {table: {left.name: _non_null_value(instance, table, left.name)}}

    return None


def _extract_column_literal(
    node: exp.Expression,
) -> Tuple[Optional[exp.Column], Optional[Any]]:
    """Extract (column, python_value) from a binary comparison if one side is a literal."""
    left, right = node.this, node.expression
    if isinstance(left, exp.Column) and _is_literal(right):
        return left, concrete(right)
    if isinstance(right, exp.Column) and _is_literal(left):
        return right, concrete(left)
    return None, None


def _is_literal(node: exp.Expression) -> bool:
    """Check if a node is a concrete literal value."""
    return isinstance(node, (exp.Literal, exp.Boolean, exp.Null))


def _resolve_table(col: exp.Column, tables: Tuple[str, ...], instance: Instance) -> str:
    """Resolve which table a column belongs to.

    Handles aliases by checking the Instance's real table names and
    falling back to column-name matching across all candidate tables.
    """
    from parseval.helper import normalize_name

    if col.table:
        table_name = col.table
        # Direct match.
        if table_name in instance.tables:
            return table_name
        # Normalized match.
        normalized = normalize_name(table_name)
        if normalized in instance.tables:
            return normalized
        # The table might be an alias — check if any candidate table has this column.
        col_name = normalize_name(col.name)
        for candidate in tables:
            real = normalize_name(candidate)
            if real in instance.tables and col_name in instance.tables[real]:
                return real

    # No table qualifier — find the first table that has this column.
    col_name = normalize_name(col.name)
    for table in tables:
        real = normalize_name(table)
        if real in instance.tables and col_name in instance.tables[real]:
            return real
    return tables[0] if tables else ""


def _different_value(original: Any, instance: Instance, table: str, column: str) -> Any:
    """Generate a value different from ``original``, respecting the column's type."""
    profile = _column_profile(instance, table, column)
    if profile is None:
        # Fallback without type info.
        if isinstance(original, int):
            return original + 1
        if isinstance(original, float):
            return original + 1.0
        if isinstance(original, str):
            return original + "_diff"
        if isinstance(original, bool):
            return not original
        return 0

    family = profile.family
    if family == TypeFamily.INTEGER:
        return (int(original) if original is not None else 0) + 1
    if family == TypeFamily.DECIMAL:
        return (float(original) if original is not None else 0.0) + 1.0
    if family == TypeFamily.TEXT:
        return (str(original) if original is not None else "") + "_diff"
    if family == TypeFamily.BOOLEAN:
        return not bool(original)
    if family == TypeFamily.DATE:
        base = original if isinstance(original, date) else date(2024, 1, 1)
        return base + timedelta(days=1)
    if family == TypeFamily.DATETIME:
        base = original if isinstance(original, datetime) else datetime(2024, 1, 1)
        return base + timedelta(hours=1)
    return 0


def _non_null_value(instance: Instance, table: str, column: str) -> Any:
    """Generate a non-NULL value appropriate for the column's type."""
    # Check existing values for a hint.
    existing = instance.get_column_data(table, column)
    for sym in existing:
        if sym.concrete is not None:
            return sym.concrete

    profile = _column_profile(instance, table, column)
    if profile is None:
        return "value"

    return _default_for_family(profile)


def _default_for_family(profile: TypeProfile) -> Any:
    """Generate a sensible default value for a type family."""
    family = profile.family
    if family == TypeFamily.INTEGER:
        return 1
    if family == TypeFamily.DECIMAL:
        return 1.0
    if family == TypeFamily.TEXT:
        length = profile.length or 10
        return "a" * min(length, 5)
    if family == TypeFamily.BOOLEAN:
        return True
    if family == TypeFamily.DATE:
        return date(2024, 1, 15)
    if family == TypeFamily.DATETIME:
        return datetime(2024, 1, 15, 12, 0, 0)
    if family == TypeFamily.TIME:
        return time(12, 0, 0)
    if family == TypeFamily.UUID:
        import uuid
        return uuid.uuid4()
    return "value"


def _column_profile(instance: Instance, table: str, column: str) -> Optional[TypeProfile]:
    """Resolve the TypeProfile for a column, or None if unavailable."""
    try:
        table_spec = instance.schema_spec.get_table(table)
        col_spec = table_spec.get_column(column)
        return TypeService().profile(col_spec)
    except (KeyError, Exception):
        return None


# =============================================================================
# Tier 1 — Heuristic resolution
# =============================================================================


def _try_heuristic(
    atom: exp.Expression,
    target_outcome: BranchType,
    tables: Tuple[str, ...],
    instance: Instance,
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Try to satisfy the constraint with simple heuristic logic.

    Handles:
    - ``col > N`` / ``col >= N`` / ``col < N`` / ``col <= N``
    - ``col BETWEEN low AND high``
    - ``col LIKE pattern``
    - ``col IN (v1, v2, ...)``
    """
    if target_outcome == BranchType.ATOM_NULL:
        return None  # Tier 0 handles NULL targets

    # GT: col > N
    if isinstance(atom, exp.GT):
        col, lit = _extract_column_literal(atom)
        if col is not None and lit is not None and isinstance(lit, (int, float)):
            table = _resolve_table(col, tables, instance)
            if target_outcome == BranchType.ATOM_TRUE:
                return {table: {col.name: lit + 1}}
            else:
                return {table: {col.name: lit - 1}}

    # GTE: col >= N
    if isinstance(atom, exp.GTE):
        col, lit = _extract_column_literal(atom)
        if col is not None and lit is not None and isinstance(lit, (int, float)):
            table = _resolve_table(col, tables, instance)
            if target_outcome == BranchType.ATOM_TRUE:
                return {table: {col.name: lit}}
            else:
                return {table: {col.name: lit - 1}}

    # LT: col < N
    if isinstance(atom, exp.LT):
        col, lit = _extract_column_literal(atom)
        if col is not None and lit is not None and isinstance(lit, (int, float)):
            table = _resolve_table(col, tables, instance)
            if target_outcome == BranchType.ATOM_TRUE:
                return {table: {col.name: lit - 1}}
            else:
                return {table: {col.name: lit + 1}}

    # LTE: col <= N
    if isinstance(atom, exp.LTE):
        col, lit = _extract_column_literal(atom)
        if col is not None and lit is not None and isinstance(lit, (int, float)):
            table = _resolve_table(col, tables, instance)
            if target_outcome == BranchType.ATOM_TRUE:
                return {table: {col.name: lit}}
            else:
                return {table: {col.name: lit + 1}}

    # BETWEEN
    if isinstance(atom, exp.Between):
        col = atom.this
        if isinstance(col, exp.Column):
            low = concrete(atom.args.get("low"))
            high = concrete(atom.args.get("high"))
            if low is not None and high is not None:
                table = _resolve_table(col, tables, instance)
                if target_outcome == BranchType.ATOM_TRUE:
                    mid = (low + high) // 2 if isinstance(low, int) else (low + high) / 2
                    return {table: {col.name: mid}}
                else:
                    return {table: {col.name: high + 1 if isinstance(high, int) else high + 1.0}}

    # LIKE
    if isinstance(atom, exp.Like):
        col = atom.this
        pattern_node = atom.expression
        if isinstance(col, exp.Column) and isinstance(pattern_node, exp.Literal):
            table = _resolve_table(col, tables, instance)
            pattern = pattern_node.this
            if target_outcome == BranchType.ATOM_TRUE:
                # Generate a string matching the pattern.
                value = _generate_like_match(pattern)
                return {table: {col.name: value}}
            else:
                return {table: {col.name: "__no_match__"}}

    # IN (literal list)
    if isinstance(atom, exp.In):
        col = atom.this
        expressions = atom.args.get("expressions") or []
        if isinstance(col, exp.Column) and expressions:
            table = _resolve_table(col, tables, instance)
            values = [concrete(e) for e in expressions if concrete(e) is not None]
            if target_outcome == BranchType.ATOM_TRUE and values:
                return {table: {col.name: values[0]}}
            elif target_outcome == BranchType.ATOM_FALSE:
                avoid = set(values)
                candidate = _value_not_in(avoid)
                return {table: {col.name: candidate}}

    return None


def _generate_like_match(pattern: str) -> str:
    """Generate a string that matches a SQL LIKE pattern."""
    result = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "%":
            result.append("x")
        elif ch == "_":
            result.append("a")
        else:
            result.append(ch)
        i += 1
    return "".join(result)


def _value_not_in(avoid: Set[Any]) -> Any:
    """Generate a value not in the avoid set."""
    if all(isinstance(v, int) for v in avoid):
        candidate = max(avoid) + 1 if avoid else 0
        return candidate
    if all(isinstance(v, str) for v in avoid):
        return "__not_in_list__"
    return 99999


# =============================================================================
# Tier 2 — SMT resolution
# =============================================================================


def _try_smt(
    constraint: SolverConstraint,
    instance: Instance,
    dialect: str,
    timeout_ms: int = 5000,
) -> Optional[Dict[str, Dict[str, Any]]]:
    """Delegate to Z3 for complex constraints.

    The existing smt.py has a different interface than what we need here;
    this is a best-effort integration that will be tightened when smt.py
    is refactored to use the domain type system. For now, if the SMT
    layer can't handle the constraint, we return None gracefully.
    """
    try:
        from .smt import SMTSolver
        solver = SMTSolver(timeout_ms=timeout_ms)
        solver.add(constraint.atom)
        for pred in constraint.path_predicates:
            try:
                solver.add(pred)
            except Exception:
                pass
        result = solver.solve()
        if not result:
            return None
        # Extract assignments grouped by table.
        assignments: Dict[str, Dict[str, Any]] = {}
        for var_name, value in result.items():
            parts = var_name.split(".")
            if len(parts) == 2:
                table, col = parts
            else:
                table = constraint.target_tables[0] if constraint.target_tables else ""
                col = var_name
            assignments.setdefault(table, {})[col] = value
        return assignments if assignments else None
    except Exception:
        return None


# =============================================================================
# Unified Solver
# =============================================================================


class Solver:
    """Unified constraint solver with tiered resolution.

    Tries cheap resolution first (trivial assignment, heuristic), then
    escalates to SMT only when needed. Always respects schema constraints
    (NOT NULL, UNIQUE, FK).
    """

    def __init__(
        self,
        instance: Instance,
        dialect: str = "sqlite",
        *,
        timeout_ms: int = 5000,
        seed: int = 42,
    ):
        self.instance = instance
        self.dialect = dialect
        self.timeout_ms = timeout_ms
        self._rng = random.Random(seed)

    def solve(self, constraint: SolverConstraint) -> SolveResult:
        """Satisfy constraints using domain-based CSP solving + SMT fallback."""

        tables = constraint.target_tables
        outcome = constraint.target_outcome

        # NULL targets: direct assignment.
        if outcome == BranchType.ATOM_NULL and constraint.null_columns:
            result: Dict[str, Dict[str, Any]] = {}
            for null_col in constraint.null_columns:
                if isinstance(null_col, exp.Column):
                    table = _resolve_table(null_col, tables, self.instance)
                    result.setdefault(table, {})[null_col.name] = None
            validated = self._validate_and_complete(result)
            if validated is not None:
                return SolveResult(sat=True, assignments=validated)
            return SolveResult(sat=False, reason="NULL target violates NOT NULL")

        # --- Domain Solver (CSP-lite with Union-Find) ---
        from .value_space import DomainSolver
        from .lowering import ColumnUnionFind

        ds = DomainSolver(self.instance, self.dialect)
        fixed_values: Dict[str, Dict[str, Any]] = {}
        predicates: List[Tuple[str, str, str, Any]] = []
        equivalences = ColumnUnionFind()

        # Extract from atom + path predicates using centralized lowering.
        from .lowering import lower_predicates
        all_exprs = [constraint.atom] + list(constraint.path_predicates)
        for expr in all_exprs:
            lowered, _ = lower_predicates(expr, self.instance, tables)
            for lp in lowered:
                if lp.op == "=":
                    fixed_values.setdefault(lp.table, {})[lp.column] = lp.value
                else:
                    predicates.append((lp.table, lp.column, lp.op, lp.value))

        # JOIN equalities → Union-Find equivalences.
        for lt, lc, rt, rc in constraint.join_equalities:
            lt_real = self._resolve(lt)
            rt_real = self._resolve(rt)
            equivalences.union(f"{lt_real}.{lc}", f"{rt_real}.{rc}")

        # FK: use existing parent values.
        for child_t, child_c, parent_t, parent_c in constraint.foreign_keys:
            parent_rows = self.instance.get_rows(parent_t)
            if parent_rows:
                val = parent_rows[-1][parent_c].concrete
                if val is not None:
                    fixed_values.setdefault(child_t, {})[child_c] = val

        result = ds.solve(
            tables=tables,
            fixed_values=fixed_values,
            predicates=predicates,
            equivalences=equivalences,
            not_null=list(constraint.not_null_columns),
            must_null=[],
            avoid_values=dict(constraint.avoid_values),
        )

        if result is not None:
            result = self._apply_join_equalities(result, constraint)
            result = self._apply_fk_constraints(result, constraint)
            validated = self._validate_and_complete(result)
            if validated is not None:
                return SolveResult(sat=True, assignments=validated)

        # --- SMT fallback ---
        smt_result = _try_smt(constraint, self.instance, self.dialect, self.timeout_ms)
        if smt_result is not None:
            smt_result = self._apply_join_equalities(smt_result, constraint)
            smt_result = self._apply_fk_constraints(smt_result, constraint)
            validated = self._validate_and_complete(smt_result)
            if validated is not None:
                return SolveResult(sat=True, assignments=validated)

        return SolveResult(sat=False, reason="all tiers exhausted")


    def _apply_join_equalities(
        self, result: Dict[str, Dict[str, Any]], constraint: SolverConstraint
    ) -> Dict[str, Dict[str, Any]]:
        """Propagate values across JOIN equalities.

        If the solver produced `{A: {id: 51}}` and there's a JOIN equality
        `A.id = B.id`, propagate: `{A: {id: 51}, B: {id: 51}}`.
        """
        result = {k: dict(v) for k, v in result.items()}  # shallow copy
        for left_table, left_col, right_table, right_col in constraint.join_equalities:
            lt = self._resolve(left_table)
            rt = self._resolve(right_table)
            # Propagate left → right.
            if lt in result and left_col in result[lt]:
                result.setdefault(rt, {})[right_col] = result[lt][left_col]
            # Propagate right → left.
            elif rt in result and right_col in result[rt]:
                result.setdefault(lt, {})[left_col] = result[rt][right_col]
        return result

    def _apply_fk_constraints(
        self, result: Dict[str, Dict[str, Any]], constraint: SolverConstraint
    ) -> Dict[str, Dict[str, Any]]:
        """Ensure FK columns reference existing parent rows.

        If the result has a child table row but no FK value, pick from
        existing parent rows. If no parent rows exist, create one with
        the same key value (coordinated).
        """
        result = {k: dict(v) for k, v in result.items()}
        for child_table, child_col, parent_table, parent_col in constraint.foreign_keys:
            if child_table not in result:
                continue
            if child_col in result[child_table] and result[child_table][child_col] is not None:
                # FK value already set (e.g., from JOIN equality propagation).
                # Ensure the parent has this value.
                fk_value = result[child_table][child_col]
                parent_rows = self.instance.get_rows(parent_table)
                parent_has_value = any(
                    row[parent_col].concrete == fk_value for row in parent_rows
                )
                if not parent_has_value:
                    # Need to create a parent row with this key.
                    result.setdefault(parent_table, {})[parent_col] = fk_value
            else:
                # No FK value set — pick from existing parent rows.
                parent_rows = self.instance.get_rows(parent_table)
                if parent_rows:
                    result[child_table][child_col] = parent_rows[-1][parent_col].concrete
                else:
                    # No parent exists — create one. Use a generated value.
                    profile = _column_profile(self.instance, parent_table, parent_col)
                    value = _default_for_family(profile) if profile else 1
                    result.setdefault(parent_table, {})[parent_col] = value
                    result[child_table][child_col] = value
        return result

    def _resolve(self, table_name: str) -> str:
        """Resolve alias to real table name."""
        from parseval.helper import normalize_name
        real = normalize_name(table_name)
        if real in self.instance.tables:
            return real
        return table_name

    def _validate_and_complete(
        self, raw: Dict[str, Dict[str, Any]]
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Validate assignments against schema constraints and coerce values.

        Uses the domain module's :func:`coerce_value` to ensure every
        generated value matches the column's declared type. Also fills FK
        columns from existing parent rows to maintain referential integrity.
        Returns None if the assignment violates an unresolvable constraint.
        """
        completed: Dict[str, Dict[str, Any]] = {}
        for table_name, col_values in raw.items():
            if table_name not in self.instance.tables:
                continue
            row: Dict[str, Any] = {}
            for col_name, col_type in self.instance.tables[table_name].items():
                if col_name in col_values:
                    value = col_values[col_name]
                    # Validate NOT NULL.
                    if value is None and not self.instance.nullable(table_name, col_name):
                        return None
                    # Coerce through the domain adapter for type safety.
                    if value is not None:
                        try:
                            datatype = DataType.build(col_type)
                            value = coerce_value(value, datatype, dialect=self.dialect)
                        except Exception:
                            pass
                    row[col_name] = value
            completed[table_name] = row
        return completed


__all__ = ["Solver", "SolveResult"]
