"""Full CSP backend operating on sqlglot ASTs and ValueSpace."""

from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.dtype import (
    TypeFamily,
    parse_date,
    parse_datetime,
    type_family,
)
from parseval.literals import integer_literal, literal_value
from parseval.coercion import CoercionError, coerce_literal_value
from parseval.plan.rex import (
    fixed_interval_delta,
)

from .normalization import unwrap_planning_temporal_arg
from .types import (
    Problem,
    Result,
    SolverVar,
    ValueSpace,
    collect_problem_variables,
)

_ARITHMETIC = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)
_CMP = {
    exp.EQ: "=",
    exp.NEQ: "!=",
    exp.GT: ">",
    exp.GTE: ">=",
    exp.LT: "<",
    exp.LTE: "<=",
}
_NEGATED = {"=": "!=", "!=": "=", ">": "<=", ">=": "<", "<": ">=", "<=": ">"}
_MAX_SEARCH_DEPTH = 32
_MAX_OR_BRANCH_COMBINATIONS = 16
_FINITE_INT_SPAN = 64
_STRFTIME_COMPONENTS = {
    "%Y": "year",
    "%m": "month",
    "%d": "day",
    "%H": "hour",
    "%M": "minute",
    "%S": "second",
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


def _clone_spaces(spaces: Dict[SolverVar, ValueSpace]) -> Dict[SolverVar, ValueSpace]:
    return {var: copy.deepcopy(space) for var, space in spaces.items()}


def _unwrap_temporal_arg(node: exp.Expression) -> exp.Expression:
    return unwrap_planning_temporal_arg(node)


class CspBackend:
    """CSP solver: propagate + search over sqlglot constraint expressions."""

    def solve(self, problem: Problem) -> Result:
        if not problem.constraints and not problem.equalities:
            return Result(status="sat", assignments={})

        supported, reason = self._all_supported(problem.constraints)
        if not supported:
            return Result(status="unknown", reason=reason)

        spaces = self._seed_spaces(problem)
        ok, reason = self._apply_equalities(spaces, problem.equalities)
        if not ok:
            return Result(status="unsat", reason=reason or "contradictory_bounds")

        result = self._solve_conjuncts(
            problem.constraints,
            spaces,
            depth=0,
            equalities=problem.equalities,
        )
        return result

    # ── support checks ──────────────────────────────────────────────

    def _all_supported(self, exprs: List[exp.Expression]) -> Tuple[bool, str]:
        for expr in exprs:
            ok, reason = self._expr_supported(expr)
            if not ok:
                return False, reason
        return True, ""

    def _expr_supported(self, expr: exp.Expression) -> Tuple[bool, str]:
        if isinstance(expr, exp.Paren):
            return self._expr_supported(expr.this)
        if isinstance(expr, exp.And):
            left_ok, left_reason = self._expr_supported(expr.this)
            if not left_ok:
                return False, left_reason
            return self._expr_supported(expr.expression)
        if isinstance(expr, exp.Or):
            left_ok, left_reason = self._expr_supported(expr.this)
            if not left_ok:
                return False, left_reason
            return self._expr_supported(expr.expression)
        if isinstance(expr, exp.Not):
            if isinstance(expr.this, exp.Not):
                return self._expr_supported(expr.this.this)
            return self._atom_supported(expr.this, negated=True)
        return self._atom_supported(expr, negated=False)

    def _atom_supported(self, atom: exp.Expression, *, negated: bool) -> Tuple[bool, str]:
        if any(atom.find(t) is not None for t in _ARITHMETIC):
            # Allow only if the whole atom is a simple comparison with no arith
            # on either side beyond literals — reject any arithmetic node.
            return False, "unsupported_arithmetic"
        if isinstance(atom, tuple(_CMP)):
            left, right = atom.this, atom.expression
            if self._date_shift_projection(left) is not None and literal_value(right) is not None:
                return True, ""
            if self._date_shift_projection(right) is not None and literal_value(left) is not None:
                return True, ""
            if self._temporal_projection(left) is not None and literal_value(right) is not None:
                return True, ""
            if self._temporal_projection(right) is not None and literal_value(left) is not None:
                return True, ""
            if self._date_projection(left) is not None and literal_value(right) is not None:
                return True, ""
            if self._date_projection(right) is not None and literal_value(left) is not None:
                return True, ""
            if isinstance(left, SolverVar) and isinstance(right, SolverVar):
                return True, ""
            if isinstance(left, SolverVar) and literal_value(right) is not None:
                return True, ""
            if isinstance(right, SolverVar) and literal_value(left) is not None:
                return True, ""
            if isinstance(left, SolverVar) and isinstance(right, exp.Null):
                return True, ""
            if isinstance(right, SolverVar) and isinstance(left, exp.Null):
                return True, ""
            return False, "unsupported_expression"
        if isinstance(atom, exp.Is):
            if isinstance(atom.this, SolverVar) and (
                isinstance(atom.expression, exp.Null)
                or (
                    isinstance(atom.expression, exp.Not)
                    and isinstance(atom.expression.this, exp.Null)
                )
            ):
                return True, ""
            return False, "unsupported_expression"
        if isinstance(atom, exp.Like):
            if negated:
                return False, "unsupported_not_like"
            if isinstance(atom.this, SolverVar) and isinstance(atom.expression, exp.Literal):
                return True, ""
            return False, "unsupported_expression"
        if isinstance(atom, exp.In):
            if self._temporal_projection(atom.this) is not None and (
                atom.args.get("expressions") or []
            ):
                return True, ""
            if isinstance(atom.this, SolverVar) and (atom.args.get("expressions") or []):
                return True, ""
            return False, "unsupported_expression"
        if isinstance(atom, exp.Between):
            if (
                isinstance(atom.this, SolverVar)
                and atom.args.get("low") is not None
                and atom.args.get("high") is not None
            ):
                return True, ""
            if (
                self._temporal_projection(atom.this) is not None
                and atom.args.get("low") is not None
                and atom.args.get("high") is not None
            ):
                return True, ""
            return False, "unsupported_expression"
        if isinstance(atom, SolverVar):
            if type_family(atom.dtype) == TypeFamily.BOOLEAN:
                return True, ""
            return False, "unsupported_bare_predicate"
        if negated:
            return False, "unsupported_not"
        return False, "unsupported_expression"

    def _temporal_projection(
        self,
        node: exp.Expression,
    ) -> Optional[Tuple[SolverVar, str]]:
        for cls, component in _TEMPORAL_COMPONENT_CLASSES:
            if isinstance(node, cls):
                inner = _unwrap_temporal_arg(node.this)
                if isinstance(inner, SolverVar):
                    return inner, component
                return None
        if isinstance(node, exp.TimeToStr):
            fmt = node.args.get("format")
            fmt_value = literal_value(fmt) if isinstance(fmt, exp.Expression) else None
            component = _STRFTIME_COMPONENTS.get(fmt_value)
            inner = _unwrap_temporal_arg(node.this)
            if component is not None and isinstance(inner, SolverVar):
                return inner, component
        if isinstance(node, exp.Anonymous) and str(node.name).upper() == "STRFTIME":
            args = list(node.expressions)
            if len(args) >= 2:
                fmt_value = literal_value(args[0])
                component = _STRFTIME_COMPONENTS.get(fmt_value)
                inner = _unwrap_temporal_arg(args[1])
                if component is not None and isinstance(inner, SolverVar):
                    return inner, component
        return None

    def _date_projection(self, node: exp.Expression) -> Optional[SolverVar]:
        if isinstance(node, exp.Date):
            inner = _unwrap_temporal_arg(node.this)
            if isinstance(inner, SolverVar):
                return inner
        return None

    def _date_shift_projection(
        self,
        node: exp.Expression,
    ) -> Optional[Tuple[SolverVar, timedelta]]:
        if not isinstance(node, (exp.DateAdd, exp.DateSub)):
            return None
        inner = _unwrap_temporal_arg(node.this)
        if not isinstance(inner, SolverVar):
            return None
        interval = fixed_interval_delta(node)
        if interval is None:
            return None
        if isinstance(node, exp.DateSub):
            interval = -interval
        return inner, interval

    # ── seeding ─────────────────────────────────────────────────────

    def _seed_spaces(self, problem: Problem) -> Dict[SolverVar, ValueSpace]:
        spaces: Dict[SolverVar, ValueSpace] = {}
        for var in collect_problem_variables(problem):
            spaces[var] = self._space_for_var(var)
        return spaces

    def _space_for_var(self, var: SolverVar) -> ValueSpace:
        dtype = var.dtype
        space = ValueSpace(family=type_family(dtype))
        length = getattr(dtype, "length", None)
        if isinstance(length, int):
            space.max_length = length
        if getattr(dtype, "nullable", None) is False:
            space.not_null = True
        return space

    # ── solve loop ──────────────────────────────────────────────────

    def _solve_conjuncts(
        self,
        exprs: List[exp.Expression],
        spaces: Dict[SolverVar, ValueSpace],
        *,
        depth: int,
        equalities: List[Tuple[SolverVar, SolverVar]] | None = None,
    ) -> Result:
        equalities = equalities or []
        flat: List[exp.Expression] = []
        for expr in exprs:
            flat.extend(self._flatten_and(expr))
        for index, expr in enumerate(flat):
            while isinstance(expr, exp.Paren):
                expr = expr.this
            flat[index] = expr

        if self._contains_smt_preferred_disjunction(flat):
            return Result(status="unknown", reason="complex_disjunction")
        if self._or_branch_cost(flat) > _MAX_OR_BRANCH_COMBINATIONS:
            return Result(status="unknown", reason="complex_disjunction")

        # Handle OR by branching; everything else is forced.
        for index, expr in enumerate(flat):
            if isinstance(expr, exp.Or):
                for branch in (expr.this, expr.expression):
                    branched = flat[:index] + [branch] + flat[index + 1 :]
                    branch_spaces = _clone_spaces(spaces)
                    result = self._solve_conjuncts(
                        branched,
                        branch_spaces,
                        depth=depth,
                        equalities=equalities,
                    )
                    if result.status == "sat":
                        return result
                    if result.status == "unknown":
                        return result
                return Result(status="unsat", reason="or_branches_unsat")

        for expr in flat:
            ok, reason = self._propagate_expr(expr, spaces)
            if not ok:
                if reason == "unknown":
                    return Result(status="unknown", reason="unsupported_expression")
                return Result(status="unsat", reason=reason or "contradictory_bounds")

        if any(space.is_empty() for space in spaces.values()):
            return Result(status="unsat", reason="contradictory_bounds")
        if not self._has_assignable_values(spaces):
            return Result(status="unsat", reason="contradictory_bounds")

        # Search / assign
        return self._search(flat, spaces, depth=depth, equalities=equalities)

    def _has_assignable_values(self, spaces: Dict[SolverVar, ValueSpace]) -> bool:
        for space in spaces.values():
            if space.must_null:
                continue
            value = space.pick()
            explicit_null_allowed = (
                space.allowed is not None
                and None in space.allowed
                and space._candidate_valid(None)
            )
            if value is None and not explicit_null_allowed:
                return False
        return True

    def _flatten_and(self, expr: exp.Expression) -> List[exp.Expression]:
        if isinstance(expr, exp.Paren):
            return self._flatten_and(expr.this)
        if isinstance(expr, exp.And):
            return self._flatten_and(expr.this) + self._flatten_and(expr.expression)
        return [expr]

    def _or_branch_cost(self, exprs: List[exp.Expression]) -> int:
        cost = 1
        for expr in exprs:
            cost *= self._disjunction_leaf_count(expr)
            if cost > _MAX_OR_BRANCH_COMBINATIONS:
                return cost
        return cost

    def _disjunction_leaf_count(self, expr: exp.Expression) -> int:
        while isinstance(expr, exp.Paren):
            expr = expr.this
        if isinstance(expr, exp.Or):
            return self._disjunction_leaf_count(expr.this) + self._disjunction_leaf_count(
                expr.expression
            )
        return 1

    def _is_solver_var_disequality_or(self, expr: exp.Expression) -> bool:
        while isinstance(expr, exp.Paren):
            expr = expr.this
        if isinstance(expr, exp.Or):
            return self._is_solver_var_disequality_or(
                expr.this
            ) and self._is_solver_var_disequality_or(expr.expression)
        return (
            isinstance(expr, exp.NEQ)
            and isinstance(expr.this, SolverVar)
            and isinstance(expr.expression, SolverVar)
        )

    def _contains_smt_preferred_disjunction(self, exprs: List[exp.Expression]) -> bool:
        for expr in exprs:
            while isinstance(expr, exp.Paren):
                expr = expr.this
            if isinstance(expr, exp.Or) and self._is_solver_var_disequality_or(expr):
                return True
        return False

    def _search(
        self,
        exprs: List[exp.Expression],
        spaces: Dict[SolverVar, ValueSpace],
        *,
        depth: int,
        equalities: List[Tuple[SolverVar, SolverVar]] | None = None,
    ) -> Result:
        if depth > _MAX_SEARCH_DEPTH:
            return Result(status="unknown", reason="search_depth_exceeded")

        equalities = equalities or []
        self._sync_equality_groups(spaces, equalities)

        unassigned = [v for v, s in spaces.items() if s.equals is None and not s.must_null]
        if not unassigned:
            assignments = self._read_assignments(spaces)
            satisfaction = self._satisfaction_result(exprs, assignments)
            if satisfaction is True:
                return Result(status="sat", assignments=assignments)
            if satisfaction is False:
                return Result(status="unsat", reason="assignment_rejected")
            # Encoding mismatches or unknown evaluation → SMT
            return Result(status="unknown", reason="assignment_rejected")

        # Prefer finite domains
        finite = [(v, cands) for v in unassigned if (cands := self._candidates(spaces[v]))]
        if finite:
            finite.sort(key=lambda item: len(item[1]))
            var, candidates = finite[0]
            for value in candidates:
                branched = _clone_spaces(spaces)
                if value is None:
                    branched[var].must_null = True
                else:
                    branched[var].narrow_eq(value)
                self._sync_equality_groups(branched, equalities)
                if branched[var].is_empty():
                    continue
                result = self._search(
                    exprs, branched, depth=depth + 1, equalities=equalities
                )
                if result.status in ("sat", "unknown"):
                    return result
            return Result(status="unsat", reason="no_finite_assignment")

        # Heuristic pick for infinite domains — unify equality groups first
        assignments = self._read_assignments(spaces)
        self._apply_equality_to_assignments(assignments, equalities, spaces)
        if self._satisfaction_result(exprs, assignments) is True:
            return Result(status="sat", assignments=assignments)
        return Result(status="unknown", reason="unbounded_domain")

    def _sync_equality_groups(
        self,
        spaces: Dict[SolverVar, ValueSpace],
        equalities: List[Tuple[SolverVar, SolverVar]],
    ) -> None:
        if not equalities:
            return
        self._apply_equalities(spaces, equalities)

    def _apply_equality_to_assignments(
        self,
        assignments: Dict[SolverVar, Any],
        equalities: List[Tuple[SolverVar, SolverVar]],
        spaces: Dict[SolverVar, ValueSpace],
    ) -> None:
        if not equalities:
            return
        parent: Dict[SolverVar, SolverVar] = {}

        def add(v: SolverVar) -> None:
            parent.setdefault(v, v)

        def find(v: SolverVar) -> SolverVar:
            add(v)
            while parent[v] != v:
                parent[v] = parent[parent[v]]
                v = parent[v]
            return v

        def union(a: SolverVar, b: SolverVar) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for left, right in equalities:
            union(left, right)
        groups: Dict[SolverVar, List[SolverVar]] = {}
        for var in list(assignments):
            if var in parent or any(var == a or var == b for a, b in equalities):
                union(var, var)
        for var in assignments:
            # only group vars that participate in equalities
            participates = any(var == a or var == b for a, b in equalities)
            if not participates:
                continue
            groups.setdefault(find(var), []).append(var)
        for members in groups.values():
            # pick one concrete value from the group
            value = None
            for member in members:
                space = spaces.get(member)
                if space is not None and space.equals is not None:
                    value = space.equals
                    break
            if value is None:
                for member in members:
                    if assignments.get(member) is not None:
                        value = assignments[member]
                        break
            if value is None and members:
                value = spaces[members[0]].pick(hint=members[0].var_key)
            for member in members:
                assignments[member] = value
                spaces[member].narrow_eq(value)

    def _candidates(self, space: ValueSpace) -> Optional[List[Any]]:
        if space.must_null:
            return [None]
        if space.equals is not None:
            return [space.equals] if space._candidate_valid(space.equals) else []
        if space.allowed is not None:
            return sorted(
                (v for v in space.allowed if space._candidate_valid(v)),
                key=repr,
            )
        if space.family == TypeFamily.BOOLEAN:
            return [v for v in (True, False) if space._candidate_valid(v)]
        if (
            space.family == TypeFamily.INTEGER
            and space.min_val is not None
            and space.max_val is not None
        ):
            lo, hi = int(space.min_val), int(space.max_val)
            if lo > hi:
                return []
            if hi - lo + 1 <= _FINITE_INT_SPAN:
                return [v for v in range(lo, hi + 1) if space._candidate_valid(v)]
        return None

    def _read_assignments(self, spaces: Dict[SolverVar, ValueSpace]) -> Dict[SolverVar, Any]:
        assignments: Dict[SolverVar, Any] = {}
        for var, space in spaces.items():
            if space.must_null:
                assignments[var] = None
            elif space.equals is not None:
                assignments[var] = space.equals
            else:
                assignments[var] = space.pick(hint=var.var_key)
        return assignments

    # ── propagation ─────────────────────────────────────────────────

    def _apply_equalities(
        self,
        spaces: Dict[SolverVar, ValueSpace],
        equalities: List[Tuple[SolverVar, SolverVar]],
    ) -> Tuple[bool, str]:
        for left, right in equalities:
            if not self._propagate_binary(spaces, left, right, "="):
                return False, "contradictory_bounds"
        # fixpoint equality sharing
        changed = True
        iterations = 0
        while changed and iterations < 16:
            changed = False
            iterations += 1
            for left, right in equalities:
                before = (
                    spaces[left].equals,
                    spaces[right].equals,
                    spaces[left].min_val,
                    spaces[left].min_inclusive,
                    spaces[right].min_val,
                    spaces[right].min_inclusive,
                    spaces[left].max_val,
                    spaces[left].max_inclusive,
                    spaces[right].max_val,
                    spaces[right].max_inclusive,
                )
                if not self._propagate_binary(spaces, left, right, "="):
                    return False, "contradictory_bounds"
                after = (
                    spaces[left].equals,
                    spaces[right].equals,
                    spaces[left].min_val,
                    spaces[left].min_inclusive,
                    spaces[right].min_val,
                    spaces[right].min_inclusive,
                    spaces[left].max_val,
                    spaces[left].max_inclusive,
                    spaces[right].max_val,
                    spaces[right].max_inclusive,
                )
                if before != after:
                    changed = True
        return True, ""

    def _propagate_expr(
        self,
        expr: exp.Expression,
        spaces: Dict[SolverVar, ValueSpace],
    ) -> Tuple[bool, str]:
        if isinstance(expr, exp.Paren):
            return self._propagate_expr(expr.this, spaces)
        if isinstance(expr, exp.Not):
            if isinstance(expr.this, exp.Not):
                return self._propagate_expr(expr.this.this, spaces)
            return self._propagate_atom(expr.this, spaces, negated=True)
        return self._propagate_atom(expr, spaces, negated=False)

    def _propagate_atom(
        self,
        atom: exp.Expression,
        spaces: Dict[SolverVar, ValueSpace],
        *,
        negated: bool,
    ) -> Tuple[bool, str]:
        if isinstance(atom, tuple(_CMP)):
            left, right = atom.this, atom.expression
            op = _CMP[type(atom)]
            if negated:
                op = _NEGATED[op]
            left_shift = self._date_shift_projection(left)
            right_shift = self._date_shift_projection(right)
            left_projection = self._temporal_projection(left)
            right_projection = self._temporal_projection(right)
            left_date_projection = self._date_projection(left)
            right_date_projection = self._date_projection(right)
            if left_shift is not None:
                lit = literal_value(right)
                if lit is None:
                    return False, "unknown"
                ok, reason = self._propagate_date_shift(spaces, left_shift, op, lit)
                return (False, reason) if not ok else (True, "")
            if right_shift is not None:
                lit = literal_value(left)
                if lit is None:
                    return False, "unknown"
                flipped = {"=": "=", "!=": "!=", ">": "<", ">=": "<=", "<": ">", "<=": ">="}[op]
                ok, reason = self._propagate_date_shift(spaces, right_shift, flipped, lit)
                return (False, reason) if not ok else (True, "")
            if left_projection is not None:
                lit = literal_value(right)
                if lit is None:
                    return False, "unknown"
                ok = self._propagate_temporal_component(spaces, left_projection, op, lit)
                return (False, "contradictory_bounds") if not ok else (True, "")
            if right_projection is not None:
                lit = literal_value(left)
                if lit is None:
                    return False, "unknown"
                flipped = {"=": "=", "!=": "!=", ">": "<", ">=": "<=", "<": ">", "<=": ">="}[op]
                ok = self._propagate_temporal_component(spaces, right_projection, flipped, lit)
                return (False, "contradictory_bounds") if not ok else (True, "")
            if left_date_projection is not None:
                lit = literal_value(right)
                if lit is None:
                    return False, "unknown"
                ok, reason = self._propagate_date_projection(
                    spaces, left_date_projection, op, lit
                )
                return (False, reason) if not ok else (True, "")
            if right_date_projection is not None:
                lit = literal_value(left)
                if lit is None:
                    return False, "unknown"
                flipped = {"=": "=", "!=": "!=", ">": "<", ">=": "<=", "<": ">", "<=": ">="}[op]
                ok, reason = self._propagate_date_projection(
                    spaces, right_date_projection, flipped, lit
                )
                return (False, reason) if not ok else (True, "")
            if isinstance(left, SolverVar) and isinstance(right, SolverVar):
                if not self._propagate_binary(spaces, left, right, op):
                    return False, "contradictory_bounds"
                return True, ""
            variable, lit = None, None
            if isinstance(left, SolverVar):
                variable, lit = left, literal_value(right)
            elif isinstance(right, SolverVar):
                variable, lit = right, literal_value(left)
                op = {"=": "=", "!=": "!=", ">": "<", ">=": "<=", "<": ">", "<=": ">="}[op]
            if variable is None:
                return False, "unknown"
            try:
                lit = coerce_literal_value(
                    lit, variable.dtype, for_equality=(op == "=")
                )
            except CoercionError:
                return False, "unknown"
            if not self._narrow(spaces[variable], op, lit):
                return False, "contradictory_bounds"
            return True, ""

        if isinstance(atom, exp.Is):
            if not isinstance(atom.this, SolverVar):
                return False, "unknown"
            variable = atom.this
            space = spaces[variable]
            right = atom.expression
            is_null = isinstance(right, exp.Null)
            is_not_null = isinstance(right, exp.Not) and isinstance(right.this, exp.Null)
            if negated:
                is_null, is_not_null = is_not_null, is_null
            if is_null:
                space.must_null = True
            elif is_not_null:
                space.not_null = True
            else:
                return False, "unknown"
            return (False, "contradictory_bounds") if space.is_empty() else (True, "")

        if isinstance(atom, exp.Like):
            if negated:
                return False, "unknown"
            if not isinstance(atom.this, SolverVar):
                return False, "unknown"
            variable = atom.this
            spaces[variable].like_pattern = str(atom.expression.this)
            return (False, "contradictory_bounds") if spaces[variable].is_empty() else (True, "")

        if isinstance(atom, exp.In):
            projection = self._temporal_projection(atom.this)
            if projection is not None:
                values = []
                for item in atom.args.get("expressions") or []:
                    val = literal_value(item)
                    if val is not None:
                        values.append(self._component_literal(val))
                if not values or any(value is None for value in values):
                    return False, "unknown"
                var, component = projection
                component_space = spaces[var].component(component)
                if negated:
                    for value in values:
                        component_space.narrow_neq(value)
                else:
                    component_space.narrow_in(set(values))
                return (
                    (False, "contradictory_bounds")
                    if spaces[var].is_empty()
                    else (True, "")
                )
            if not isinstance(atom.this, SolverVar):
                return False, "unknown"
            variable = atom.this
            values = []
            for item in atom.args.get("expressions") or []:
                if isinstance(item, exp.Null):
                    values.append(None)
                    continue
                val = literal_value(item)
                if val is not None:
                    try:
                        values.append(coerce_literal_value(val, variable.dtype))
                    except CoercionError:
                        return False, "unknown"
            if not values:
                return False, "unknown"
            if negated:
                for val in values:
                    if val is None:
                        spaces[variable].not_null = True
                    else:
                        spaces[variable].narrow_neq(val)
            else:
                if values == [None]:
                    spaces[variable].must_null = True
                else:
                    spaces[variable].narrow_in(set(values))
            return (False, "contradictory_bounds") if spaces[variable].is_empty() else (True, "")

        if isinstance(atom, exp.Between):
            if negated:
                return False, "unknown"
            projection = self._temporal_projection(atom.this)
            if projection is not None:
                low = self._component_literal(literal_value(atom.args["low"]))
                high = self._component_literal(literal_value(atom.args["high"]))
                if low is None or high is None:
                    return False, "unknown"
                var, component = projection
                component_space = spaces[var].component(component)
                component_space.narrow_min(low)
                component_space.narrow_max(high)
                return (
                    (False, "contradictory_bounds")
                    if spaces[var].is_empty()
                    else (True, "")
                )
            if not isinstance(atom.this, SolverVar):
                return False, "unknown"
            variable = atom.this
            try:
                low = coerce_literal_value(
                    literal_value(atom.args["low"]), variable.dtype
                )
                high = coerce_literal_value(
                    literal_value(atom.args["high"]), variable.dtype
                )
            except CoercionError:
                return False, "unknown"
            spaces[variable].narrow_min(low)
            spaces[variable].narrow_max(high)
            return (False, "contradictory_bounds") if spaces[variable].is_empty() else (True, "")

        if isinstance(atom, SolverVar):
            if type_family(atom.dtype) != TypeFamily.BOOLEAN:
                return False, "unknown"
            spaces[atom].narrow_eq(False if negated else True)
            return (False, "contradictory_bounds") if spaces[atom].is_empty() else (True, "")

        return False, "unknown"

    def _component_literal(self, value: Any) -> Optional[int]:
        return integer_literal(value)

    def _propagate_temporal_component(
        self,
        spaces: Dict[SolverVar, ValueSpace],
        projection: Tuple[SolverVar, str],
        op: str,
        literal: Any,
    ) -> bool:
        value = self._component_literal(literal)
        if value is None:
            return False
        var, component = projection
        return self._narrow(spaces[var].component(component), op, value) and not spaces[
            var
        ].is_empty()

    def _propagate_date_projection(
        self,
        spaces: Dict[SolverVar, ValueSpace],
        variable: SolverVar,
        op: str,
        literal: Any,
    ) -> Tuple[bool, str]:
        parsed = parse_date(literal)
        if parsed is None:
            return False, "unknown"
        space = spaces[variable]
        family = type_family(variable.dtype)
        if op == "!=":
            return False, "unknown"
        if family == TypeFamily.DATE:
            return self._narrow(space, op, parsed), "contradictory_bounds"
        if family != TypeFamily.DATETIME:
            return False, "unknown"
        start = datetime(parsed.year, parsed.month, parsed.day)
        next_day = start + timedelta(days=1)
        if op == "=":
            space.narrow_min(start)
            space.narrow_max(next_day, inclusive=False)
        elif op == ">":
            space.narrow_min(next_day)
        elif op == ">=":
            space.narrow_min(start)
        elif op == "<":
            space.narrow_max(start, inclusive=False)
        elif op == "<=":
            space.narrow_max(next_day, inclusive=False)
        return (not space.is_empty()), "contradictory_bounds"

    def _propagate_date_shift(
        self,
        spaces: Dict[SolverVar, ValueSpace],
        projection: Tuple[SolverVar, timedelta],
        op: str,
        literal: Any,
    ) -> Tuple[bool, str]:
        if op == "!=":
            return False, "unknown"
        variable, delta = projection
        family = type_family(variable.dtype)
        if family == TypeFamily.DATE:
            parsed_date = parse_date(literal)
            if parsed_date is None:
                return False, "unknown"
            source_datetime = datetime(parsed_date.year, parsed_date.month, parsed_date.day) - delta
            source_value: Any = source_datetime.date()
        elif family == TypeFamily.DATETIME:
            target = parse_datetime(literal)
            if target is None:
                parsed_date = parse_date(literal)
                if parsed_date is None:
                    return False, "unknown"
                target = datetime(parsed_date.year, parsed_date.month, parsed_date.day)
            source_value = target - delta
        else:
            return False, "unknown"
        return self._narrow(spaces[variable], op, source_value), "contradictory_bounds"

    def _narrow(self, space: ValueSpace, op: str, val: Any) -> bool:
        if op == "=":
            space.narrow_eq(val)
        elif op == "!=":
            space.narrow_neq(val)
        elif op == ">":
            space.narrow_min(val, inclusive=False)
        elif op == ">=":
            space.narrow_min(val)
        elif op == "<":
            space.narrow_max(val, inclusive=False)
        elif op == "<=":
            space.narrow_max(val)
        return not space.is_empty()

    def _propagate_binary(
        self,
        spaces: Dict[SolverVar, ValueSpace],
        left: SolverVar,
        right: SolverVar,
        op: str,
    ) -> bool:
        ls = spaces.setdefault(left, ValueSpace(family=type_family(left.dtype)))
        rs = spaces.setdefault(right, ValueSpace(family=type_family(right.dtype)))
        if op == "=":
            if ls.equals is not None:
                rs.narrow_eq(ls.equals)
            if rs.equals is not None:
                ls.narrow_eq(rs.equals)
            if ls.min_val is not None:
                rs.narrow_min(ls.min_val, inclusive=ls.min_inclusive)
            if rs.min_val is not None:
                ls.narrow_min(rs.min_val, inclusive=rs.min_inclusive)
            if ls.max_val is not None:
                rs.narrow_max(ls.max_val, inclusive=ls.max_inclusive)
            if rs.max_val is not None:
                ls.narrow_max(rs.max_val, inclusive=rs.max_inclusive)
            shared_ne = ls.not_equals | rs.not_equals
            ls.not_equals = set(shared_ne)
            rs.not_equals = set(shared_ne)
            if ls.allowed is not None and rs.allowed is not None:
                shared = ls.allowed & rs.allowed
                ls.allowed = set(shared)
                rs.allowed = set(shared)
            elif ls.allowed is not None:
                rs.allowed = set(ls.allowed)
            elif rs.allowed is not None:
                ls.allowed = set(rs.allowed)
            if ls.must_null:
                rs.must_null = True
            if rs.must_null:
                ls.must_null = True
            if ls.not_null:
                rs.not_null = True
            if rs.not_null:
                ls.not_null = True
            return not ls.is_empty() and not rs.is_empty()
        if op == "!=":
            if ls.equals is not None:
                rs.narrow_neq(ls.equals)
            if rs.equals is not None:
                ls.narrow_neq(rs.equals)
            return not ls.is_empty() and not rs.is_empty()
        # Inequalities: tighten bounds when one side is fixed
        if op in (">", ">="):
            if rs.equals is not None:
                return self._narrow(ls, op, rs.equals)
            if ls.equals is not None:
                flip = "<" if op == ">" else "<="
                return self._narrow(rs, flip, ls.equals)
            return not ls.is_empty() and not rs.is_empty()
        if op in ("<", "<="):
            if rs.equals is not None:
                return self._narrow(ls, op, rs.equals)
            if ls.equals is not None:
                flip = ">" if op == "<" else ">="
                return self._narrow(rs, flip, ls.equals)
            return not ls.is_empty() and not rs.is_empty()
        return False

    # ── evaluation ──────────────────────────────────────────────────

    def _satisfies(
        self,
        exprs: List[exp.Expression],
        assignments: Dict[SolverVar, Any],
    ) -> bool:
        return self._satisfaction_result(exprs, assignments) is True

    def _satisfaction_result(
        self,
        exprs: List[exp.Expression],
        assignments: Dict[SolverVar, Any],
    ) -> Optional[bool]:
        from parseval.plan.rex import Environment, concrete

        env = Environment.from_assignments(assignments)
        for expr in exprs:
            value = concrete(expr, env)
            if value is False:
                return False
            if value is not True:
                return None
        return True


__all__ = ["CspBackend"]
