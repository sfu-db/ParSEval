"""CSP-lite constraint solver using value-space narrowing.

Replaces the ad-hoc Tier 0/1 heuristics with a principled approach:
1. BUILD: Extract variables + constraints from predicates.
2. PROPAGATE: Narrow value spaces via constraint intersection (AC-3 lite).
3. ASSIGN: Topological sort by dependency, pick values from narrowed spaces.

Integrates with the domain module's TypeAdapter for type-correct value
generation within the narrowed space.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from parseval.domain.types import TypeFamily, TypeService
from parseval.dtype import DataType
from parseval.instance import Instance


# =============================================================================
# ValueSpace: the constrained set of valid values for one column
# =============================================================================


@dataclass
class ValueSpace:
    """The narrowed space of valid values for a variable."""

    family: TypeFamily = TypeFamily.TEXT
    min_val: Optional[Any] = None
    max_val: Optional[Any] = None
    equals: Optional[Any] = None
    not_equals: Set[Any] = field(default_factory=set)
    allowed: Optional[Set[Any]] = None  # IN (...)
    must_null: bool = False
    not_null: bool = False
    like_pattern: Optional[str] = None
    max_length: Optional[int] = None
    # Derived: value = source_var OP operand
    derived_from: Optional[Tuple[str, str, Any]] = None

    def is_empty(self) -> bool:
        """True if no valid value exists in this space."""
        if self.must_null and self.not_null:
            return True
        if self.must_null:
            return False
        if self.equals is not None:
            if self.equals in self.not_equals:
                return True
            if self.min_val is not None and self.equals < self.min_val:
                return True
            if self.max_val is not None and self.equals > self.max_val:
                return True
            if self.allowed is not None and self.equals not in self.allowed:
                return True
            return False
        if self.min_val is not None and self.max_val is not None:
            if self.min_val > self.max_val:
                return True
        if self.allowed is not None:
            valid = self.allowed - self.not_equals
            if self.min_val is not None:
                valid = {v for v in valid if v >= self.min_val}
            if self.max_val is not None:
                valid = {v for v in valid if v <= self.max_val}
            if not valid:
                return True
        return False

    def pick(self) -> Any:
        """Choose a concrete value from this space."""
        if self.must_null:
            return None
        if self.equals is not None:
            return self.equals
        if self.allowed is not None:
            valid = self.allowed - self.not_equals
            if self.min_val is not None:
                valid = {v for v in valid if v >= self.min_val}
            if self.max_val is not None:
                valid = {v for v in valid if v <= self.max_val}
            if valid:
                return min(valid)  # deterministic
            return None

        # Range-based selection (prefer middle for robustness).
        if self.family in (TypeFamily.INTEGER, TypeFamily.DECIMAL):
            return self._pick_numeric()
        elif self.family == TypeFamily.TEXT:
            return self._pick_text()
        elif self.family in (TypeFamily.DATE, TypeFamily.DATETIME):
            return self._pick_temporal()
        elif self.family == TypeFamily.BOOLEAN:
            if True not in self.not_equals:
                return True
            return False
        # Fallback
        return self._pick_numeric() if self.min_val is not None or self.max_val is not None else "value"

    def _pick_numeric(self) -> Any:
        lo = self.min_val if self.min_val is not None else 1
        hi = self.max_val if self.max_val is not None else lo + 100
        # Pick middle of range, avoiding not_equals.
        candidate = (lo + hi) // 2 if isinstance(lo, int) else (lo + hi) / 2
        while candidate in self.not_equals:
            candidate = candidate + 1
            if self.max_val is not None and candidate > self.max_val:
                candidate = lo
                while candidate in self.not_equals:
                    candidate += 1
                break
        return candidate

    def _pick_text(self) -> str:
        if self.like_pattern:
            return self.like_pattern.replace("%", "x").replace("_", "a")
        length = min(self.max_length or 10, 10)
        base = "value"[:length]
        i = 1
        while base in self.not_equals:
            base = f"val_{i}"[:length]
            i += 1
        return base

    def _pick_temporal(self) -> Any:
        if self.min_val and isinstance(self.min_val, (date, datetime)):
            return self.min_val
        if self.max_val and isinstance(self.max_val, (date, datetime)):
            return self.max_val - timedelta(days=1)
        return date(2024, 6, 15)

    def narrow_min(self, val: Any) -> None:
        if self.min_val is None or val > self.min_val:
            self.min_val = val

    def narrow_max(self, val: Any) -> None:
        if self.max_val is None or val < self.max_val:
            self.max_val = val

    def narrow_eq(self, val: Any) -> None:
        self.equals = val

    def narrow_neq(self, val: Any) -> None:
        self.not_equals.add(val)

    def narrow_in(self, values: Set[Any]) -> None:
        if self.allowed is None:
            self.allowed = values
        else:
            self.allowed &= values


# =============================================================================
# CSP Variables and Constraints
# =============================================================================


@dataclass
class CSPVariable:
    """A column that needs a value."""
    name: str  # "table.column"
    table: str
    column: str
    space: ValueSpace
    assigned: Optional[Any] = None
    depends_on: Optional[str] = None  # name of variable this derives from


@dataclass
class CSPConstraint:
    """A relationship between variables."""
    kind: str  # "eq" / "derived"
    left: str  # variable name
    right: str  # variable name
    operator: str = "="  # for derived: +, -, *, /
    operand: Any = None  # for derived: the constant


# =============================================================================
# DomainSolver: build → propagate → assign
# =============================================================================


class DomainSolver:
    """CSP-lite solver using value-space narrowing."""

    def __init__(self, instance: Instance, dialect: str = "sqlite"):
        self.instance = instance
        self.dialect = dialect
        self._type_service = TypeService()

    def solve(
        self,
        tables: Tuple[str, ...],
        fixed_values: Dict[str, Dict[str, Any]],
        predicates: List[Tuple[str, str, str, Any]],  # (table, col, op, value)
        shared_keys: Dict[str, List[Tuple[str, str]]],  # key_id → [(table, col)]
        not_null: List[Tuple[str, str]],
        must_null: List[Tuple[str, str]],
        avoid_values: Dict[str, Set[Any]],  # "table.col" → existing values
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Solve the constraint set and return assignments per table.

        Returns None if UNSAT.
        """
        # Phase 1: BUILD
        variables, constraints = self._build(
            tables, fixed_values, predicates, shared_keys,
            not_null, must_null, avoid_values,
        )

        # Phase 2: PROPAGATE
        if not self._propagate(variables, constraints):
            return None  # UNSAT

        # Phase 3: ASSIGN
        return self._assign(variables, constraints)

    def _build(
        self, tables, fixed_values, predicates, shared_keys,
        not_null, must_null, avoid_values,
    ) -> Tuple[Dict[str, CSPVariable], List[CSPConstraint]]:
        """Build CSP variables and constraints."""
        variables: Dict[str, CSPVariable] = {}
        constraints: List[CSPConstraint] = []

        # Create variables for all columns in target tables.
        for table in tables:
            if table not in self.instance.tables:
                continue
            for col, col_type in self.instance.tables[table].items():
                name = f"{table}.{col}"
                family = self._type_family(col_type)
                space = ValueSpace(family=family)
                variables[name] = CSPVariable(name=name, table=table, column=col, space=space)

        # Apply fixed values.
        for table, cols in fixed_values.items():
            for col, val in cols.items():
                name = f"{table}.{col}"
                if name in variables:
                    variables[name].space.narrow_eq(val)

        # Apply predicates.
        for table, col, op, val in predicates:
            name = f"{table}.{col}"
            if name not in variables:
                continue
            space = variables[name].space
            if op == "=":
                space.narrow_eq(val)
            elif op == ">" and isinstance(val, (int, float)):
                space.narrow_min(val + 1 if isinstance(val, int) else val + 0.01)
            elif op == ">=" and isinstance(val, (int, float)):
                space.narrow_min(val)
            elif op == "<" and isinstance(val, (int, float)):
                space.narrow_max(val - 1 if isinstance(val, int) else val - 0.01)
            elif op == "<=" and isinstance(val, (int, float)):
                space.narrow_max(val)
            elif op == "!=":
                space.narrow_neq(val)
            elif op == "in":
                space.narrow_in(set(val) if isinstance(val, (list, tuple, set)) else {val})
            elif op == "like":
                space.like_pattern = val
            elif op == "is_null":
                space.must_null = True
            elif op == ">" and isinstance(val, str):
                # String comparison: just use a value that's "greater"
                space.narrow_eq(val + "z")
            elif op == "<" and isinstance(val, str):
                space.narrow_eq(val[:-1] if len(val) > 1 else "a")

        # Apply shared keys (equality constraints between variables).
        for key_id, cols in shared_keys.items():
            if len(cols) >= 2:
                first = f"{cols[0][0]}.{cols[0][1]}"
                for table, col in cols[1:]:
                    other = f"{table}.{col}"
                    constraints.append(CSPConstraint(kind="eq", left=first, right=other))

        # Apply NOT NULL.
        for table, col in not_null:
            name = f"{table}.{col}"
            if name in variables:
                variables[name].space.not_null = True

        # Apply must NULL.
        for table, col in must_null:
            name = f"{table}.{col}"
            if name in variables:
                variables[name].space.must_null = True

        # Apply UNIQUE avoidance.
        for key, existing in avoid_values.items():
            if key in variables:
                for val in existing:
                    variables[key].space.narrow_neq(val)

        return variables, constraints

    def _propagate(self, variables: Dict[str, CSPVariable], constraints: List[CSPConstraint]) -> bool:
        """Narrow domains via constraint propagation. Returns False if UNSAT."""
        changed = True
        iterations = 0
        while changed and iterations < 10:
            changed = False
            iterations += 1
            for constraint in constraints:
                if constraint.kind == "eq":
                    left = variables.get(constraint.left)
                    right = variables.get(constraint.right)
                    if left and right:
                        # Unify: if one has equals, propagate to the other.
                        if left.space.equals is not None and right.space.equals is None:
                            right.space.narrow_eq(left.space.equals)
                            changed = True
                        elif right.space.equals is not None and left.space.equals is None:
                            left.space.narrow_eq(right.space.equals)
                            changed = True
                        # Propagate bounds.
                        if left.space.min_val and (right.space.min_val is None or left.space.min_val > right.space.min_val):
                            right.space.narrow_min(left.space.min_val)
                            changed = True
                        if left.space.max_val and (right.space.max_val is None or left.space.max_val < right.space.max_val):
                            right.space.narrow_max(left.space.max_val)
                            changed = True

            # Check for empty domains.
            for var in variables.values():
                if var.space.is_empty():
                    return False

        return True

    def _assign(self, variables: Dict[str, CSPVariable], constraints: List[CSPConstraint]) -> Optional[Dict[str, Dict[str, Any]]]:
        """Assign values from narrowed spaces."""
        # Resolve equality groups: all vars in an eq constraint get the same value.
        eq_groups: Dict[str, str] = {}  # var_name → group leader
        for c in constraints:
            if c.kind == "eq":
                leader = eq_groups.get(c.left, c.left)
                eq_groups[c.right] = leader
                eq_groups[c.left] = leader

        # Pick values for leaders first, then propagate to group members.
        assigned: Dict[str, Any] = {}
        for var in variables.values():
            leader = eq_groups.get(var.name, var.name)
            if leader in assigned:
                var.assigned = assigned[leader]
            else:
                val = var.space.pick()
                var.assigned = val
                assigned[var.name] = val
                assigned[leader] = val

        # Build result grouped by table.
        result: Dict[str, Dict[str, Any]] = {}
        for var in variables.values():
            if var.assigned is not None or var.space.must_null:
                result.setdefault(var.table, {})[var.column] = var.assigned

        return result if result else None

    def _type_family(self, col_type_str: str) -> TypeFamily:
        """Map a column type string to TypeFamily."""
        upper = str(col_type_str).upper()
        if any(t in upper for t in ("INT", "INTEGER", "BIGINT", "SMALLINT")):
            return TypeFamily.INTEGER
        if any(t in upper for t in ("REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC")):
            return TypeFamily.DECIMAL
        if any(t in upper for t in ("BOOL",)):
            return TypeFamily.BOOLEAN
        if "DATETIME" in upper or "TIMESTAMP" in upper:
            return TypeFamily.DATETIME
        if "DATE" in upper:
            return TypeFamily.DATE
        if "TIME" in upper:
            return TypeFamily.TIME
        return TypeFamily.TEXT


__all__ = ["DomainSolver", "ValueSpace", "CSPVariable", "CSPConstraint"]
