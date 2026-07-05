"""Unified constraint solver for ParSEval.

The solver provides a single interface to satisfy constraints expressed as
sqlglot AST nodes. Internally it uses a two-tier resolution strategy:

* **Domain solver**: CSP-lite value-space narrowing with constraint
  propagation. It returns a tri-state result: ``sat`` when it handled the
  full formula, ``unsat`` when it proved a contradiction, and ``unknown``
  when the formula is outside its supported fragment.
* **SMT fallback**: Full Z3-backed constraint solving for complex
  constraints with cross-column dependencies or arithmetic relationships.
  It runs only for domain ``unknown`` and fails closed if any input
  expression cannot be translated.

The solver is a pure function of its inputs — it does not depend on
``Instance`` or any database state.  The caller is responsible for
annotating every ``exp.Column`` node in the constraint expressions with a
datatype and a :class:`parseval.solver.types.SolverVar`. The solver uses
that identity metadata for CSP variables, join equalities, and public
assignments.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp

from parseval.dtype import DataType
from parseval.identity import RelationId

from .normalization import normalize_constraint
from .types import SolverVar, col_type, solver_var


# =============================================================================
# Public types
# =============================================================================


@dataclass
class SolverConstraint:
    """Constraints for the solver to satisfy.

    Every ``exp.Column`` node inside *constraints* must have a datatype
    annotation and ``SolverVar`` metadata. The solver reads types and
    identities from those annotations — it does not consult any external
    schema.

    Attributes:
        target_relations: Relations the solver should generate values for.
        constraints: All constraint expressions (comparisons, IS NULL, etc.).
        join_equalities: Cross-variable equalities that the solver enforces.
        variables: Optional explicit datatype map for solver variables.
        storage_relations: Physical storage relation for alias-scoped variables.
    """

    target_relations: Tuple[RelationId, ...]
    constraints: List[exp.Expression] = field(default_factory=list)
    join_equalities: List[Tuple[SolverVar, SolverVar]] = field(default_factory=list)
    variables: Dict[SolverVar, DataType] = field(default_factory=dict)
    storage_relations: Dict[SolverVar, RelationId] = field(default_factory=dict)


@dataclass
class SolveResult:
    """Outcome of a solver invocation.

    Assignments use :class:`SolverVar` keys mapping to concrete Python values.
    """

    sat: bool
    assignments: Dict[SolverVar, Any] = field(default_factory=dict)
    reason: str = ""


def normalize_temporal_predicates(constraint: SolverConstraint) -> None:
    """Backward-compatible in-place temporal normalization wrapper."""
    normalized = normalize_constraint(constraint)
    constraint.constraints = normalized.constraints
    constraint.join_equalities = normalized.join_equalities
    constraint.variables = normalized.variables
    constraint.storage_relations = normalized.storage_relations


def narrow_year_bounds(constraint: SolverConstraint) -> None:
    """Backward-compatible alias for temporal predicate normalization."""
    normalize_temporal_predicates(constraint)


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
        constraint = normalize_constraint(constraint)

        assignments: Dict[SolverVar, Any] = {}
        for component in self._components(constraint):
            domain_result = self._try_domain(component)
            if domain_result.status == "sat":
                assignments.update(domain_result.assignments or {})
                continue
            if domain_result.status == "unsat":
                return SolveResult(
                    sat=False,
                    reason=domain_result.reason or "unsat",
                )
            if domain_result.status != "unknown":
                return SolveResult(
                    sat=False,
                    reason=domain_result.reason or f"unexpected_domain_status:{domain_result.status}",
                )

            smt_result, smt_reason = self._try_smt(component)
            if smt_result is None:
                return SolveResult(sat=False, reason=smt_reason)
            assignments.update(smt_result)

        return SolveResult(sat=True, assignments=assignments)

    # ── Validation ──────────────────────────────────────────────

    def _validate_types(self, constraint: SolverConstraint) -> Tuple[bool, str]:
        """Check that all Column nodes have type and solver-var annotations."""
        for expr in constraint.constraints:
            for col in expr.find_all(exp.Column):
                if col_type(col) is None:
                    return False, f"Column {col.table or '?'}.{col.name} has no type annotation"
                if solver_var(col) is None:
                    return False, f"Column {col.table or '?'}.{col.name} has no solver variable metadata"
        return True, ""

    # ── Domain solver ───────────────────────────────────────────

    def _try_domain(
        self, constraint: SolverConstraint,
    ):
        """Attempt to solve with the domain solver (CSP-lite)."""
        from .domain import DomainSolver

        ds = DomainSolver()
        return ds.solve(constraint)

    # ── SMT solver ──────────────────────────────────────────────

    def _try_smt(
        self, constraint: SolverConstraint,
    ) -> Tuple[Optional[Dict[SolverVar, Any]], str]:
        """Solve all constraint expressions with Z3."""
        try:
            from .smt_solver import SMTSolver
            smt = SMTSolver(timeout_ms=self.timeout_ms)

            variables = self._collect_variables(constraint)
            encoded_names = {
                variable: self._smt_name(index, variable)
                for index, variable in enumerate(variables)
            }
            smt.context["solver_var_to_name"] = encoded_names
            reverse_names = {name: variable for variable, name in encoded_names.items()}

            for variable, dtype in variables.items():
                smt.declare_variable(encoded_names[variable], dtype)

            for expr in constraint.constraints:
                z3_expr = smt.translate(expr)
                if z3_expr is None:
                    return None, "unsupported_smt_expression"
                smt.add(z3_expr)

            for left_var, right_var in constraint.join_equalities:
                left_z3 = smt.context.get("variable_to_z3", {}).get(encoded_names[left_var])
                right_z3 = smt.context.get("variable_to_z3", {}).get(encoded_names[right_var])
                if left_z3 is None or right_z3 is None:
                    return None, "all tiers exhausted"
                smt.add_raw(left_z3 == right_z3)

            status, solutions = smt.solve()
            if status == "unsat":
                return None, "unsat"
            if status != "sat":
                return None, "all tiers exhausted"
            if not solutions and variables:
                return None, "all tiers exhausted"

            assignments: Dict[SolverVar, Any] = {}
            for var_name, value in solutions.items():
                variable = reverse_names.get(var_name)
                if variable is not None:
                    assignments[variable] = value
            if not assignments and variables:
                return None, "all tiers exhausted"
            return assignments, ""
        except Exception:
            return None, "all tiers exhausted"

    def _collect_variables(self, constraint: SolverConstraint) -> Dict[SolverVar, DataType]:
        variables: Dict[SolverVar, DataType] = dict(constraint.variables)
        for expr in constraint.constraints:
            for col in expr.find_all(exp.Column):
                variable = solver_var(col)
                dtype = col_type(col)
                if variable is not None and dtype is not None:
                    variables.setdefault(variable, dtype)
        for left_var, right_var in constraint.join_equalities:
            if left_var not in variables and right_var in variables:
                variables[left_var] = variables[right_var]
            elif right_var not in variables and left_var in variables:
                variables[right_var] = variables[left_var]
            else:
                variables.setdefault(left_var, DataType.build("TEXT"))
                variables.setdefault(right_var, DataType.build("TEXT"))
        return variables

    def _components(self, constraint: SolverConstraint) -> List[SolverConstraint]:
        expr_vars = [self._expression_variables(expr) for expr in constraint.constraints]
        parent: Dict[SolverVar, SolverVar] = {}

        def add(variable: SolverVar) -> None:
            parent.setdefault(variable, variable)

        def find(variable: SolverVar) -> SolverVar:
            add(variable)
            while parent[variable] != variable:
                parent[variable] = parent[parent[variable]]
                variable = parent[variable]
            return variable

        def union(left: SolverVar, right: SolverVar) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[left_root] = right_root

        for variables in expr_vars:
            for variable in variables:
                add(variable)
            if len(variables) > 1:
                first = next(iter(variables))
                for variable in variables:
                    union(first, variable)
        for left_var, right_var in constraint.join_equalities:
            union(left_var, right_var)

        grouped_exprs: Dict[object, List[exp.Expression]] = {}
        grouped_joins: Dict[object, List[Tuple[SolverVar, SolverVar]]] = {}
        grouped_vars: Dict[object, set[SolverVar]] = {}

        for index, expr in enumerate(constraint.constraints):
            variables = expr_vars[index]
            key: object = find(next(iter(variables))) if variables else ("expr", index)
            grouped_exprs.setdefault(key, []).append(expr)
            grouped_vars.setdefault(key, set()).update(variables)

        for left_var, right_var in constraint.join_equalities:
            key = find(left_var)
            grouped_joins.setdefault(key, []).append((left_var, right_var))
            grouped_vars.setdefault(key, set()).update((left_var, right_var))

        keys = set(grouped_exprs) | set(grouped_joins)
        components: List[SolverConstraint] = []
        for key in keys:
            component_vars = grouped_vars.get(key, set())
            component_types = {
                variable: dtype
                for variable, dtype in constraint.variables.items()
                if variable in component_vars
            }
            components.append(SolverConstraint(
                target_relations=constraint.target_relations,
                constraints=grouped_exprs.get(key, []),
                join_equalities=grouped_joins.get(key, []),
                variables=component_types,
                storage_relations={
                    variable: relation
                    for variable, relation in constraint.storage_relations.items()
                    if variable in component_vars
                },
            ))
        return components

    def _expression_variables(self, expr: exp.Expression) -> set[SolverVar]:
        variables: set[SolverVar] = set()
        for col in expr.find_all(exp.Column):
            variable = solver_var(col)
            if variable is not None:
                variables.add(variable)
        return variables

    def _smt_name(self, index: int, variable: SolverVar) -> str:
        return (
            f"sv_{index}_"
            f"{variable.relation_id.binding_display}_"
            f"{variable.column_id.name.normalized}"
        ).replace(".", "_").replace("#", "_")

__all__ = [
    "Solver",
    "SolveResult",
    "SolverConstraint",
    "normalize_temporal_predicates",
    "narrow_year_bounds",
]
