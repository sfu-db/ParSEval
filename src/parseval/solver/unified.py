"""Unified constraint solver for ParSEval.

The solver provides a single interface to satisfy constraints expressed as
sqlglot AST nodes. Internally it uses a two-tier resolution strategy:

* **Domain solver**: CSP-lite value-space narrowing with constraint
  propagation. Handles simple predicates (comparisons, LIKE, IN,
  BETWEEN) and equality propagation across JOINs.
* **SMT fallback**: Full Z3-backed constraint solving for complex
  constraints with cross-column dependencies or arithmetic
  relationships.

The solver is a pure function of its inputs — it does not depend on
``Instance`` or any database state.  The caller is responsible for
annotating ``exp.Column.type`` on every column node in the constraint
expressions so the solver can resolve datatypes for Z3 encoding and
CSP value generation.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp

from parseval.dtype import DataType
from parseval.helper import normalize_name

from .types import col_type


# =============================================================================
# Public types
# =============================================================================


@dataclass
class SolverConstraint:
    """Constraints for the solver to satisfy.

    Every ``exp.Column`` node inside *constraints* must have its ``.type``
    attribute set to a valid ``exp.DataType`` (e.g.
    ``exp.DataType.build("INT")``).  The solver reads types from these
    annotations — it does not consult any external schema.

    Attributes:
        target_tables: Tables the solver should generate values for.
        constraints: All constraint expressions (comparisons, IS NULL, etc.).
        join_equalities: Cross-table equalities ``(left_table, left_col,
            right_table, right_col)`` that the solver enforces.
        alias_map: Table alias → real name mapping for column resolution.
    """

    target_tables: Tuple[str, ...]
    constraints: List[exp.Expression] = field(default_factory=list)
    join_equalities: List[Tuple[str, str, str, str]] = field(default_factory=list)
    alias_map: Dict[str, str] = field(default_factory=dict)


@dataclass
class SolveResult:
    """Outcome of a solver invocation."""

    sat: bool
    assignments: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reason: str = ""


# =============================================================================
# Unified Solver
# =============================================================================


class Solver:
    """Unified constraint solver with tiered resolution.

    Tries cheap resolution first (domain / CSP-lite), then escalates to
    SMT (Z3) only when needed.  The solver is a pure function of its
    inputs — no ``Instance`` dependency.
    """

    def __init__(
        self,
        dialect: str = "sqlite",
        *,
        timeout_ms: int = 5000,
        seed: int = 42,
    ):
        self.dialect = dialect
        self.timeout_ms = timeout_ms
        self._rng = random.Random(seed)

    # ── Public API ──────────────────────────────────────────────

    def solve(self, constraint: SolverConstraint) -> SolveResult:
        """Satisfy *constraint* using domain + SMT solving.

        Returns :class:`SolveResult` with ``sat=True`` and assignments
        on success, or ``sat=False`` with a reason on failure.
        """
        if not constraint.constraints and not constraint.join_equalities:
            return SolveResult(sat=True, assignments={})

        # Validate type annotations
        ok, reason = self._validate_types(constraint)
        if not ok:
            return SolveResult(sat=False, reason=reason)

        # Tier 1: Domain solver
        domain_result = self._try_domain(constraint)
        if domain_result is not None:
            return SolveResult(sat=True, assignments=domain_result)

        # Tier 2: SMT solver
        smt_result = self._try_smt(constraint)
        if smt_result is not None:
            return SolveResult(sat=True, assignments=smt_result)

        return SolveResult(sat=False, reason="all tiers exhausted")

    # ── Validation ──────────────────────────────────────────────

    def _validate_types(self, constraint: SolverConstraint) -> Tuple[bool, str]:
        """Check that all Column nodes have type annotations."""
        for expr in constraint.constraints:
            for col in expr.find_all(exp.Column):
                if col_type(col) is None:
                    return False, f"Column {col.table or '?'}.{col.name} has no type annotation"
        return True, ""

    # ── Domain solver ───────────────────────────────────────────

    def _try_domain(
        self, constraint: SolverConstraint,
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Attempt to solve with the domain solver (CSP-lite)."""
        from .domain import DomainSolver

        ds = DomainSolver()
        return ds.solve(constraint)

    # ── SMT solver ──────────────────────────────────────────────

    def _try_smt(
        self, constraint: SolverConstraint,
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Solve all constraint expressions with Z3."""
        try:
            from .smt import SMTSolver, UnsupportedSMTError

            smt = SMTSolver(timeout_ms=self.timeout_ms)

            # Declare variables from all columns in constraints.
            for expr in constraint.constraints:
                for col in expr.find_all(exp.Column):
                    col_key = f"{normalize_name(col.table or '')}.{normalize_name(col.name)}"
                    dtype = col_type(col) or DataType.build("TEXT")
                    smt.declare_variable(col_key, dtype)

            # Declare variables from join equalities.
            for lt, lc, rt, rc in constraint.join_equalities:
                for table, col_name in [(lt, lc), (rt, rc)]:
                    key = f"{normalize_name(table)}.{normalize_name(col_name)}"
                    if key not in smt.context.get("variable_to_z3", {}):
                        dtype = self._find_col_type(constraint, table, col_name)
                        smt.declare_variable(key, dtype)

            # Translate and add all constraint expressions.
            for expr in constraint.constraints:
                try:
                    z3_expr = smt.translate(expr)
                    if z3_expr is not None:
                        smt.add(z3_expr)
                except (UnsupportedSMTError, Exception):
                    pass

            # Add join equalities as Z3 equalities.
            for lt, lc, rt, rc in constraint.join_equalities:
                try:
                    left_key = f"{normalize_name(lt)}.{normalize_name(lc)}"
                    right_key = f"{normalize_name(rt)}.{normalize_name(rc)}"
                    left_z3 = smt.context.get("variable_to_z3", {}).get(left_key)
                    right_z3 = smt.context.get("variable_to_z3", {}).get(right_key)
                    if left_z3 is not None and right_z3 is not None:
                        smt.add_raw(left_z3 == right_z3)
                except Exception:
                    pass

            status, solutions = smt.solve()
            if status != "sat" or not solutions:
                return None

            # Group assignments by table (using physical names).
            alias_map = constraint.alias_map or {}
            assignments: Dict[str, Dict[str, Any]] = {}
            for var_name, value in solutions.items():
                parts = var_name.split(".")
                if len(parts) == 2:
                    table, col = parts
                else:
                    table = constraint.target_tables[0] if constraint.target_tables else ""
                    col = var_name
                physical = alias_map.get(table, table)
                assignments.setdefault(physical, {})[col] = value
            return assignments if assignments else None
        except Exception:
            return None

    def _find_col_type(
        self, constraint: SolverConstraint, table: str, col_name: str
    ) -> DataType:
        """Find the DataType for a column from the constraint expressions."""
        table_norm = normalize_name(table)
        col_norm = normalize_name(col_name)
        for expr in constraint.constraints:
            for col in expr.find_all(exp.Column):
                if (
                    normalize_name(col.table or "") == table_norm
                    and normalize_name(col.name) == col_norm
                ):
                    dtype = col_type(col)
                    if dtype is not None:
                        return dtype
        return DataType.build("TEXT")

__all__ = ["Solver", "SolveResult", "SolverConstraint"]
