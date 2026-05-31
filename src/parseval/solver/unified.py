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
        if domain_result.status == "unsat":
            return SolveResult(sat=False, reason=domain_result.reason)
        if domain_result.status == "sat":
            return SolveResult(
                sat=True,
                assignments=self._remap_assignments(domain_result.assignments or {}, constraint.alias_map),
            )
        if domain_result.status != "unknown":
            return SolveResult(
                sat=False,
                reason=domain_result.reason or f"unexpected_domain_status:{domain_result.status}",
            )

        # Tier 2: SMT solver
        smt_result, smt_reason = self._try_smt(constraint)
        if smt_result is not None:
            return SolveResult(sat=True, assignments=smt_result)

        return SolveResult(sat=False, reason=smt_reason)

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
    ):
        """Attempt to solve with the domain solver (CSP-lite)."""
        from .domain import DomainSolver

        ds = DomainSolver()
        return ds.solve(constraint)

    def _remap_assignments(
        self,
        assignments: Dict[str, Dict[str, Any]],
        alias_map: Dict[str, str],
    ) -> Dict[str, Dict[str, Any]]:
        target_counts: Dict[str, int] = {}
        for table in assignments:
            physical = alias_map.get(table, table)
            target_counts[physical] = target_counts.get(physical, 0) + 1

        remapped: Dict[str, Dict[str, Any]] = {}
        for table, cols in assignments.items():
            physical = alias_map.get(table, table)
            result_key = physical if target_counts[physical] == 1 else table
            remapped[result_key] = dict(cols)
        return remapped

    # ── SMT solver ──────────────────────────────────────────────

    def _try_smt(
        self, constraint: SolverConstraint,
    ) -> Tuple[Optional[Dict[str, Dict[str, Any]]], str]:
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
            declared_keys = set(smt.context.get("variable_to_z3", {}))
            for lt, lc, rt, rc in constraint.join_equalities:
                for table, col_name in [(lt, lc), (rt, rc)]:
                    resolved_table = self._resolve_smt_table(constraint, declared_keys, table, col_name)
                    if resolved_table is None:
                        return None, "all tiers exhausted"
                    key = f"{normalize_name(resolved_table)}.{normalize_name(col_name)}"
                    if key not in smt.context.get("variable_to_z3", {}):
                        dtype = self._find_col_type(constraint, resolved_table, col_name)
                        smt.declare_variable(key, dtype)
                    declared_keys.add(key)

            # Translate and add all constraint expressions.
            for expr in constraint.constraints:
                try:
                    z3_expr = smt.translate(expr)
                except (UnsupportedSMTError, Exception):
                    return None, "unsupported_smt_expression"
                if z3_expr is None:
                    return None, "unsupported_smt_expression"
                smt.add(z3_expr)

            # Add join equalities as Z3 equalities.
            for lt, lc, rt, rc in constraint.join_equalities:
                try:
                    left_table = self._resolve_smt_table(constraint, declared_keys, lt, lc)
                    right_table = self._resolve_smt_table(constraint, declared_keys, rt, rc)
                    if left_table is None or right_table is None:
                        return None, "all tiers exhausted"
                    left_key = f"{normalize_name(left_table)}.{normalize_name(lc)}"
                    right_key = f"{normalize_name(right_table)}.{normalize_name(rc)}"
                    left_z3 = smt.context.get("variable_to_z3", {}).get(left_key)
                    right_z3 = smt.context.get("variable_to_z3", {}).get(right_key)
                    if left_z3 is None or right_z3 is None:
                        return None, "all tiers exhausted"
                    smt.add_raw(left_z3 == right_z3)
                except Exception:
                    return None, "all tiers exhausted"

            status, solutions = smt.solve()
            if status != "sat" or not solutions:
                return None, "all tiers exhausted"

            # Group assignments by solver table key first, then apply the same
            # lossless alias-to-physical remap rule as the domain path.
            alias_map = constraint.alias_map or {}
            assignments: Dict[str, Dict[str, Any]] = {}
            for var_name, value in solutions.items():
                parts = var_name.split(".")
                if len(parts) == 2:
                    table, col = parts
                else:
                    table = constraint.target_tables[0] if constraint.target_tables else ""
                    col = var_name
                assignments.setdefault(table, {})[col] = value
            if not assignments:
                return None, "all tiers exhausted"
            return self._remap_assignments(assignments, alias_map), ""
        except Exception:
            return None, "all tiers exhausted"

    def _resolve_smt_table(
        self,
        constraint: SolverConstraint,
        declared_keys: set[str],
        table: str,
        col_name: str,
    ) -> Optional[str]:
        """Resolve a join-side table name into the SMT variable namespace."""
        table_norm = normalize_name(table)
        col_norm = normalize_name(col_name)
        exact_key = f"{table_norm}.{col_norm}"
        if exact_key in declared_keys:
            return table_norm

        alias_map = constraint.alias_map or {}
        candidates: List[str] = []
        for key in declared_keys:
            if "." not in key:
                continue
            key_table, key_col = key.split(".", 1)
            if key_col != col_norm:
                continue
            if key_table == table_norm:
                return key_table
            physical = normalize_name(alias_map.get(key_table, key_table))
            if physical == table_norm:
                candidates.append(key_table)

        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return None

        target_candidates: List[str] = []
        for target in constraint.target_tables:
            target_norm = normalize_name(target)
            if target_norm == table_norm:
                return target_norm
            if normalize_name(alias_map.get(target_norm, target_norm)) == table_norm:
                target_candidates.append(target_norm)

        if len(target_candidates) == 1:
            return target_candidates[0]
        if target_candidates:
            return None

        return table_norm

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
