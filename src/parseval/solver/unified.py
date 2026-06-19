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

import calendar
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp

from parseval.dtype import DataType
from parseval.identity import RelationId

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


# =============================================================================
# Unified Solver
# =============================================================================


def _literal_int(expr: exp.Expression | None) -> Optional[int]:
    if not isinstance(expr, exp.Literal):
        return None
    try:
        return int(str(expr.this))
    except (TypeError, ValueError):
        return None


def _literal_text(expr: exp.Expression | None) -> Optional[str]:
    if not isinstance(expr, exp.Literal):
        return None
    return str(expr.this)


def _call_name(expr: exp.Expression) -> str:
    if isinstance(expr, exp.Anonymous):
        return (expr.name or "").upper()
    if isinstance(expr, exp.Substring):
        return "SUBSTR"
    return expr.key.upper() if expr.key else ""


def _call_args(expr: exp.Expression) -> List[exp.Expression]:
    if isinstance(expr, exp.Substring):
        args = [expr.this]
        if expr.args.get("start") is not None:
            args.append(expr.args["start"])
        if expr.args.get("length") is not None:
            args.append(expr.args["length"])
        return args
    return [
        child for child in expr.iter_expressions()
        if not isinstance(child, exp.DataType)
    ]


def _unwrap_temporal_column(expr: exp.Expression) -> Optional[exp.Column]:
    if isinstance(expr, (exp.TsOrDsToTimestamp, exp.TsOrDsToDate)):
        expr = expr.this
    return expr if isinstance(expr, exp.Column) else None


def _temporal_prefix_projection(expr: exp.Expression) -> Optional[Tuple[exp.Column, int]]:
    """Return ``(column, ISO-prefix-length)`` for supported temporal projections."""
    supported_formats = {
        "%Y": 4,
        "%Y-%m": 7,
        "%Y-%m-%d": 10,
    }
    if isinstance(expr, exp.TimeToStr):
        fmt = _literal_text(expr.args.get("format"))
        col = _unwrap_temporal_column(expr.this)
        if fmt in supported_formats and col is not None:
            return col, supported_formats[fmt]
        return None
    if isinstance(expr, exp.Year):
        col = _unwrap_temporal_column(expr.this)
        return (col, 4) if col is not None else None
    if isinstance(expr, exp.Extract):
        unit_node = expr.this
        unit_text = None
        if isinstance(unit_node, exp.Var):
            unit_text = unit_node.name.upper()
        elif isinstance(unit_node, exp.Identifier):
            unit_text = unit_node.name.upper()
        elif isinstance(unit_node, exp.Column):
            unit_text = unit_node.name.upper()
        if unit_text != "YEAR":
            return None
        col = _unwrap_temporal_column(expr.expression)
        return (col, 4) if col is not None else None

    name = _call_name(expr)
    if name in {"SUBSTR", "SUBSTRING"}:
        args = _call_args(expr)
        if len(args) < 3:
            return None
        col = _unwrap_temporal_column(args[0])
        start = _literal_int(args[1])
        length = _literal_int(args[2])
        if col is not None and start == 1 and length in {4, 7, 10}:
            return col, length
        return None
    if name in {"STRFTIME", "TIME_TO_STR"}:
        args = _call_args(expr)
        if len(args) < 2:
            return None
        if name == "STRFTIME":
            fmt = _literal_text(args[0])
            col = _unwrap_temporal_column(args[1])
        else:
            col = _unwrap_temporal_column(args[0])
            fmt = _literal_text(args[1])
        if fmt in supported_formats and col is not None:
            return col, supported_formats[fmt]
    return None


def _year_extractor_inner_column(expr: exp.Expression) -> Optional[exp.Column]:
    projection = _temporal_prefix_projection(expr)
    if projection is None or projection[1] != 4:
        return None
    return projection[0]


def _prefix_date_bounds(value: str, prefix_length: int):
    from datetime import date as _date

    try:
        if prefix_length == 4 and len(value) == 4:
            year = int(value)
            return _date(year, 1, 1), _date(year, 12, 31)
        if prefix_length == 7 and len(value) == 7 and value[4] == "-":
            year = int(value[:4])
            month = int(value[5:7])
            last_day = calendar.monthrange(year, month)[1]
            return _date(year, month, 1), _date(year, month, last_day)
        if prefix_length == 10 and len(value) == 10 and value[4] == "-" and value[7] == "-":
            year = int(value[:4])
            month = int(value[5:7])
            day = int(value[8:10])
            exact = _date(year, month, day)
            return exact, exact
    except ValueError:
        return None
    return None


def _is_temporal_column(col: exp.Column) -> Tuple[bool, bool]:
    dtype = col_type(col) or DataType.build("TEXT")
    is_date = dtype.is_type(DataType.Type.DATE) or dtype.is_type(DataType.Type.DATE32)
    is_datetime = dtype.is_type(
        DataType.Type.TIMESTAMP, DataType.Type.TIMESTAMP_S,
        DataType.Type.TIMESTAMP_MS, DataType.Type.TIMESTAMP_NS,
        DataType.Type.TIMESTAMPTZ, DataType.Type.TIMESTAMPLTZ,
        DataType.Type.DATETIME, DataType.Type.DATETIME64,
    )
    return is_date, is_datetime


def _rewrite_year_extractor_predicates(constraint: SolverConstraint) -> None:
    """Replace temporal string-prefix predicates with equivalent column bounds.

    Handles year/month/day prefixes from ``SUBSTR`` and temporal formatting
    calls. For DATE / TIMESTAMP columns these prefixes are monotone, so they
    can be represented as direct epoch day/second ranges.

    Mutates ``constraint.constraints`` in place.
    """
    from datetime import datetime as _dt
    from .smt_types import date_to_epoch_day, datetime_to_epoch_second

    rewritten: List[exp.Expression] = []
    for cexpr in constraint.constraints:
        targets: List[Tuple[exp.Expression, exp.Column, Any, Any]] = []
        for node in cexpr.walk():
            projection = _temporal_prefix_projection(node)
            if projection is None:
                continue
            col, prefix_length = projection
            is_date, is_datetime = _is_temporal_column(col)
            if not (is_date or is_datetime):
                continue
            cmp_node = node
            while cmp_node is not None and not isinstance(
                cmp_node, (exp.EQ, exp.GTE, exp.LTE, exp.Between)
            ):
                cmp_node = cmp_node.parent
            if cmp_node is None:
                continue
            lo_value: Optional[str] = None
            hi_value: Optional[str] = None
            if isinstance(cmp_node, exp.EQ):
                lo_value = hi_value = _literal_text(cmp_node.expression)
            elif isinstance(cmp_node, exp.GTE):
                lo_value = _literal_text(cmp_node.expression)
            elif isinstance(cmp_node, exp.LTE):
                hi_value = _literal_text(cmp_node.expression)
            else:
                lo_value = _literal_text(cmp_node.args.get("low"))
                hi_value = _literal_text(cmp_node.args.get("high"))
            lo_bounds = _prefix_date_bounds(lo_value, prefix_length) if lo_value else None
            hi_bounds = _prefix_date_bounds(hi_value, prefix_length) if hi_value else None
            if lo_value and lo_bounds is None:
                continue
            if hi_value and hi_bounds is None:
                continue
            lo_date = lo_bounds[0] if lo_bounds is not None else None
            hi_date = hi_bounds[1] if hi_bounds is not None else None
            if lo_date is None and hi_date is None:
                continue
            targets.append((cmp_node, col, lo_date, hi_date))

        if not targets:
            rewritten.append(cexpr)
            continue

        new_expr = cexpr.copy()
        for old_node, col, lo_date, hi_date in targets:
            is_date, _is_datetime = _is_temporal_column(col)
            new_preds: List[exp.Expression] = []
            if lo_date is not None:
                if is_date:
                    payload = date_to_epoch_day(lo_date)
                else:
                    payload = datetime_to_epoch_second(
                        _dt(lo_date.year, lo_date.month, lo_date.day)
                    )
                new_preds.append(exp.GTE(
                    this=col.copy(), expression=exp.Literal.number(payload),
                ))
            if hi_date is not None:
                if is_date:
                    payload = date_to_epoch_day(hi_date)
                else:
                    payload = datetime_to_epoch_second(
                        _dt(hi_date.year, hi_date.month, hi_date.day, 23, 59, 59)
                    )
                new_preds.append(exp.LTE(
                    this=col.copy(), expression=exp.Literal.number(payload),
                ))

            new_target = _find_replica(new_expr, old_node)
            if new_target is None:
                continue
            parent = new_target.parent
            if parent is None:
                new_expr = new_preds[0] if len(new_preds) == 1 else exp.and_(*new_preds)
                break
            for k, v in list(parent.args.items()):
                if v is new_target:
                    parent.set(k, new_preds[0] if len(new_preds) == 1
                               else exp.and_(*new_preds))
                    break
        rewritten.append(new_expr)
    constraint.constraints = rewritten


def _find_replica(root: exp.Expression, target: exp.Expression) -> Optional[exp.Expression]:
    """Locate the node in ``root`` that is structurally identical to ``target``.

    Used after ``root = target.copy()`` to find the matching node when we
    no longer have identity-based references.
    """
    for candidate in root.walk():
        if type(candidate) is type(target) and candidate.sql() == target.sql():
            return candidate
    return None


def narrow_year_bounds(constraint: SolverConstraint) -> None:
    """In-place: rewrite year-extractor predicates into date bounds.

    See :func:`_rewrite_year_extractor_predicates` for the rationale.
    """
    _rewrite_year_extractor_predicates(constraint)


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

            # Narrow search space: STRFTIME('%Y', col) year comparisons
            # imply tight bounds on col (epoch day/second for the year
            # span). Without this, Z3 must invert the Hinnant year
            # decomposition and frequently times out.
            narrow_year_bounds(constraint)

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
            f"{variable.relation_id.display}_"
            f"{variable.column_id.name.normalized}"
        ).replace(".", "_").replace("#", "_")

__all__ = ["Solver", "SolveResult", "SolverConstraint"]
