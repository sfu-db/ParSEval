"""CSP-lite constraint solver using value-space narrowing."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp

from parseval.helper import normalize_name

from .types import (
    CSPConstraint,
    CSPVariable,
    ColumnPredicate,
    TypeFamily,
    ValueSpace,
    col_type,
    type_family,
    parse_date,
    parse_time,
    parse_datetime,
    infer_type_from_string,
)

_NEGATED_OPS = {"=": "!=", "!=": "=", ">": "<=", ">=": "<", "<": ">=", "<=": ">"}
_ARITHMETIC_NODES = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod)


def _col_table(col: exp.Column, tables: Tuple[str, ...]) -> str:
    """Resolve a column's table qualifier to a target_tables key.

    Every column in solver constraints must have its .table set by the caller.
    If the column's table isn't in target_tables, falls back to the first
    target table (covers single-table solves where the qualifier may differ).
    """
    if not col.table:
        raise ValueError(f"Column {col.name} has no table qualifier — caller must set .table")
    raw = normalize_name(col.table)
    for t in tables:
        if normalize_name(t) == raw:
            return t
    return tables[0] if tables else raw

_OP_MAP = {
    exp.EQ: "=", exp.NEQ: "!=", exp.GT: ">",
    exp.GTE: ">=", exp.LT: "<", exp.LTE: "<=",
}


def _lower_negated_atom(
    inner: exp.Expression,
    tables: Tuple[str, ...],
) -> Optional[ColumnPredicate]:
    """Lower NOT(atom) by negating a directly supported predicate."""
    if isinstance(inner, exp.Is):
        if isinstance(inner.this, exp.Column):
            # NOT(IS NULL) -> IS NOT NULL
            if isinstance(inner.expression, exp.Null):
                return ColumnPredicate(table=_col_table(inner.this, tables), column=inner.this.name, op="not_null", value=True)
            # NOT(IS NOT NULL) -> IS NULL
            right = inner.expression
            if isinstance(right, exp.Not) and isinstance(right.this, exp.Null):
                return ColumnPredicate(table=_col_table(inner.this, tables), column=inner.this.name, op="is_null", value=True)
    # NOT(comparison) -> flip operator
    for cls, op in _OP_MAP.items():
        if isinstance(inner, cls):
            col, val = _extract_col_literal(inner)
            if col is not None and val is not None:
                neg_op = _NEGATED_OPS.get(op, op)
                return ColumnPredicate(table=_col_table(col, tables), column=col.name, op=neg_op, value=val)
    return None


def _lower_atom(
    atom: exp.Expression,
    tables: Tuple[str, ...],
) -> Optional[ColumnPredicate]:
    col, val, op = None, None, None
    if isinstance(atom, exp.EQ):
        col, val = _extract_col_literal(atom)
        op = "="
    elif isinstance(atom, exp.NEQ):
        col, val = _extract_col_literal(atom)
        op = "!="
    elif isinstance(atom, exp.GT):
        col, val = _extract_col_literal(atom)
        op = ">"
    elif isinstance(atom, exp.GTE):
        col, val = _extract_col_literal(atom)
        op = ">="
    elif isinstance(atom, exp.LT):
        col, val = _extract_col_literal(atom)
        op = "<"
    elif isinstance(atom, exp.LTE):
        col, val = _extract_col_literal(atom)
        op = "<="
    elif isinstance(atom, exp.Is):
        right = atom.expression
        if isinstance(atom.this, exp.Column):
            if isinstance(right, exp.Null):
                col = atom.this
                val = True
                op = "is_null"
            elif isinstance(right, exp.Not) and isinstance(right.this, exp.Null):
                col = atom.this
                val = True
                op = "not_null"
    elif isinstance(atom, exp.Like):
        if isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Literal):
            col = atom.this
            val = str(atom.expression.this)
            op = "like"
    elif isinstance(atom, exp.In):
        in_col = atom.this
        expressions = atom.args.get("expressions") or []
        if isinstance(in_col, exp.Column) and expressions:
            values = []
            for e in expressions:
                v = _literal_value(e)
                if v is not None:
                    values.append(v)
            if values:
                return ColumnPredicate(table=_col_table(in_col, tables), column=in_col.name, op="in", value=values)
    elif isinstance(atom, exp.Between):
        bw_col = atom.this
        low = atom.args.get("low")
        high = atom.args.get("high")
        if isinstance(bw_col, exp.Column) and low and high:
            low_val = _literal_value(low)
            high_val = _literal_value(high)
            if low_val is not None and high_val is not None:
                return ColumnPredicate(table=_col_table(bw_col, tables), column=bw_col.name, op="between", value=(low_val, high_val))

    if col is not None and val is not None and op is not None:
        return ColumnPredicate(table=_col_table(col, tables), column=col.name, op=op, value=val)
    return None


def _coerce_value(value: Any, col: exp.Column) -> Any:
    """Coerce a literal value based on the column's annotated type."""
    dtype = col_type(col)
    if dtype is None or value is None:
        return value
    family = type_family(dtype)
    if family == TypeFamily.INTEGER:
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                try:
                    return int(float(value))
                except ValueError:
                    return value
    elif family == TypeFamily.DECIMAL:
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except ValueError:
                return value
    elif family == TypeFamily.BOOLEAN:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() in ("1", "true", "t", "yes", "y"):
                return True
            if value.lower() in ("0", "false", "f", "no", "n"):
                return False
    elif family == TypeFamily.DATE:
        parsed = parse_date(value)
        if parsed is not None:
            return parsed
    elif family == TypeFamily.DATETIME:
        parsed = parse_datetime(value)
        if parsed is not None:
            return parsed
    elif family == TypeFamily.TIME:
        parsed = parse_time(value)
        if parsed is not None:
            return parsed
    return value


def _extract_col_literal(node: exp.Expression):
    left, right = node.this, node.expression
    if isinstance(left, exp.Column):
        val = _literal_value(right)
        if val is not None or isinstance(right, exp.Null):
            return left, _coerce_value(val, left)
    if isinstance(right, exp.Column):
        val = _literal_value(left)
        if val is not None or isinstance(left, exp.Null):
            return right, _coerce_value(val, right)
    return None, None


def _extract_col_col(node: exp.Expression):
    """Extract (left_col, right_col) from a column-column comparison."""
    left, right = node.this, node.expression
    if isinstance(left, exp.Column) and isinstance(right, exp.Column):
        return left, right
    return None, None


def _literal_value(node: exp.Expression):
    """Extract a Python value from a literal-like expression node.

    Handles Literal, Boolean, Null, Neg (negative numbers), and Cast.
    Returns None for expressions that can't be reduced to a value.
    """
    if isinstance(node, exp.Literal):
        if node.is_int:
            return int(node.this)
        if node.is_number:
            return float(node.this)
        return str(node.this)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    if isinstance(node, exp.Null):
        return None
    if isinstance(node, exp.Neg):
        inner = _literal_value(node.this)
        if isinstance(inner, (int, float)):
            return -inner
        return None
    if isinstance(node, exp.Cast):
        return _literal_value(node.this)
    return None



def _unsupported_reason(expr: exp.Expression) -> str:
    if any(expr.find(node_type) is not None for node_type in _ARITHMETIC_NODES):
        return "unsupported_arithmetic"
    if isinstance(expr, exp.Not):
        return "unsupported_not"
    if isinstance(expr, exp.Or):
        return "unsupported_or"
    return "unsupported_expression"


def _predicate_family(pred: ColumnPredicate) -> TypeFamily:
    if pred.op in {"=", "!="} and isinstance(pred.value, bool):
        return TypeFamily.BOOLEAN
    if pred.op == "in" and isinstance(pred.value, list) and pred.value:
        if all(isinstance(value, bool) for value in pred.value):
            return TypeFamily.BOOLEAN
    return TypeFamily.TEXT


@dataclass
class DomainResult:
    """Result from the domain solver.

    Assignments use ``"table.column"`` keys mapping to concrete Python values.
    """
    status: str
    assignments: Optional[Dict[str, Any]] = None
    reason: str = ""


@dataclass
class LoweringOutcome:
    status: str
    predicates: List[ColumnPredicate] = field(default_factory=list)
    equalities: List[Tuple[str, str]] = field(default_factory=list)
    families: Dict[str, TypeFamily] = field(default_factory=dict)
    reason: str = ""


class DomainSolver:
    """CSP-lite solver using value-space narrowing."""

    def solve(self, constraint) -> DomainResult:
        """Solve constraints and return assignments per table.

        Partitions expressions into connected components by variable.
        Each component is solved independently — a single unsupported
        expression no longer poisons the entire batch.

        Args:
            constraint: A :class:`SolverConstraint` with typed expressions.
        """
        target_tables = constraint.target_tables
        expressions = constraint.constraints
        join_equalities = constraint.join_equalities or []
        alias_map = constraint.alias_map or {}

        if not expressions and not join_equalities:
            return DomainResult(status="sat", assignments={})

        # Partition expressions into connected components by variable.
        partitions = self._partition_by_variables(expressions, target_tables)

        all_variables: Dict[str, CSPVariable] = {}
        all_constraints: List[CSPConstraint] = []
        unknown_reasons: List[str] = []

        for partition_exprs in partitions:
            # Analyze this partition independently.
            analysis = LoweringOutcome(status="sat")
            for expr in partition_exprs:
                analysis = self._merge_and(
                    analysis,
                    self._analyze_expression(expr, target_tables),
                )
                if analysis.status == "unsat":
                    return DomainResult(status="unsat", reason=analysis.reason or "contradictory_bounds")
            if analysis.status == "unknown":
                unknown_reasons.append(analysis.reason or "unsupported_expression")
                continue

            # Extract variables and apply predicates for this partition.
            part_variables = self._extract_variables(target_tables, partition_exprs)
            self._apply_predicates(part_variables, analysis.predicates)

            # Add col-col equalities from this partition.
            for left_key, right_key in analysis.equalities:
                if left_key in part_variables and right_key in part_variables:
                    all_constraints.append(CSPConstraint(kind="eq", left=left_key, right=right_key))

            all_variables.update(part_variables)

        # If any partition was unknown, let the caller escalate to SMT.
        if unknown_reasons:
            return DomainResult(status="unknown", reason=unknown_reasons[0])

        # Build equivalences from join equalities.
        join_constraints = self._build_equivalences(all_variables, join_equalities, alias_map)
        all_constraints.extend(join_constraints)

        # Propagate across all variables and constraints.
        if not self._propagate(all_variables, all_constraints):
            return DomainResult(status="unsat", reason="contradictory_bounds")

        return DomainResult(
            status="sat",
            assignments=self._assign(all_variables, target_tables),
        )

    def _partition_by_variables(
        self,
        expressions: List[exp.Expression],
        tables: Tuple[str, ...],
    ) -> List[List[exp.Expression]]:
        """Partition expressions into connected components by variable.

        Two expressions are in the same component if they share a variable
        (directly or transitively via column-column equalities).  Each
        component can be solved independently by the domain solver.
        """
        # Assign each expression an index.
        n = len(expressions)
        if n <= 1:
            return [expressions] if expressions else []

        # Union-Find over expression indices.
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # Map variable key → first expression index that uses it.
        var_to_expr: Dict[str, int] = {}
        for idx, expr in enumerate(expressions):
            for col in expr.find_all(exp.Column):
                key = f"{_col_table(col, tables)}.{col.name}"
                first = var_to_expr.get(key)
                if first is not None:
                    union(first, idx)
                else:
                    var_to_expr[key] = idx

        # Column-column equalities connect two variables (and their expressions).
        for idx, expr in enumerate(expressions):
            if isinstance(expr, exp.EQ):
                left_col, right_col = _extract_col_col(expr)
                if left_col and right_col:
                    left_key = f"{_col_table(left_col, tables)}.{left_col.name}"
                    right_key = f"{_col_table(right_col, tables)}.{right_col.name}"
                    left_idx = var_to_expr.get(left_key)
                    right_idx = var_to_expr.get(right_key)
                    if left_idx is not None and right_idx is not None:
                        union(left_idx, right_idx)

        # Group expressions by component.
        groups: Dict[int, List[int]] = {}
        for idx in range(n):
            root = find(idx)
            groups.setdefault(root, []).append(idx)

        return [[expressions[i] for i in group] for group in groups.values()]

    def _analyze_expression(
        self,
        expr: exp.Expression,
        tables: Tuple[str, ...],
    ) -> LoweringOutcome:
        if isinstance(expr, exp.And):
            return self._merge_and(
                self._analyze_expression(expr.left, tables),
                self._analyze_expression(expr.right, tables),
            )
        if isinstance(expr, exp.Paren):
            return self._analyze_expression(expr.this, tables)
        if isinstance(expr, exp.Or):
            return self._merge_or(
                self._analyze_expression(expr.left, tables),
                self._analyze_expression(expr.right, tables),
            )
        if isinstance(expr, exp.Not):
            if isinstance(expr.this, exp.Not):
                return self._analyze_expression(expr.this.this, tables)
            pred = _lower_negated_atom(expr.this, tables)
            if pred is None:
                return LoweringOutcome(status="unknown", reason="unsupported_not")
            return self._classify_supported(LoweringOutcome(
                status="sat",
                predicates=[pred],
                families=self._expression_families(expr, tables),
            ))
        if isinstance(expr, exp.EQ):
            left_col, right_col = _extract_col_col(expr)
            if left_col and right_col:
                return self._classify_supported(LoweringOutcome(
                    status="sat",
                    equalities=[(f"{_col_table(left_col, tables)}.{left_col.name}", f"{_col_table(right_col, tables)}.{right_col.name}")],
                    families=self._expression_families(expr, tables),
                ))
        if isinstance(expr, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            left_col, right_col = _extract_col_col(expr)
            if left_col and right_col:
                return LoweringOutcome(
                    status="sat",
                    predicates=[],
                    equalities=[],
                    families=self._expression_families(expr, tables),
                )

        pred = _lower_atom(expr, tables)
        if pred is None:
            return LoweringOutcome(status="unknown", reason=_unsupported_reason(expr))
        return self._classify_supported(LoweringOutcome(
            status="sat",
            predicates=[pred],
            families=self._expression_families(expr, tables),
        ))

    def _merge_and(self, left: LoweringOutcome, right: LoweringOutcome) -> LoweringOutcome:
        if left.status == "unsat":
            return left
        if right.status == "unsat":
            return right
        if left.status == "unknown":
            return left
        if right.status == "unknown":
            return right
        return self._classify_supported(LoweringOutcome(
            status="sat",
            predicates=[*left.predicates, *right.predicates],
            equalities=[*left.equalities, *right.equalities],
            families={**left.families, **right.families},
        ))

    def _merge_or(self, left: LoweringOutcome, right: LoweringOutcome) -> LoweringOutcome:
        if left.status == "sat" and right.status == "unsat":
            return left
        if right.status == "sat" and left.status == "unsat":
            return right
        if left.status == "unsat" and right.status == "unsat":
            return LoweringOutcome(status="unsat", reason=left.reason or right.reason)
        if left.status == "sat" and right.status == "sat":
            return left  # pick one branch — we only need one satisfying assignment
        if left.status == "sat" and right.status == "unknown":
            return left
        if right.status == "sat" and left.status == "unknown":
            return right
        if left.status == "unknown":
            return LoweringOutcome(status="unknown", reason=left.reason or right.reason or "unsupported_or")
        if right.status == "unknown":
            return LoweringOutcome(status="unknown", reason=right.reason or left.reason or "unsupported_or")
        return LoweringOutcome(status="unknown", reason="unsupported_or")

    def _classify_supported(self, outcome: LoweringOutcome) -> LoweringOutcome:
        if outcome.status != "sat":
            return outcome
        if not outcome.predicates and not outcome.equalities:
            return outcome

        variables: Dict[str, CSPVariable] = {
            name: CSPVariable(
                name=name,
                table=name.split(".", 1)[0],
                column=name.split(".", 1)[1],
                space=ValueSpace(family=family),
            )
            for name, family in outcome.families.items()
        }
        self._apply_predicates(variables, outcome.predicates)

        constraints: List[CSPConstraint] = []
        for left_key, right_key in outcome.equalities:
            for key in (left_key, right_key):
                if key not in variables:
                    table, column = key.split(".", 1)
                    variables[key] = CSPVariable(
                        name=key,
                        table=table,
                        column=column,
                        space=ValueSpace(family=outcome.families.get(key, TypeFamily.TEXT)),
                    )
            constraints.append(CSPConstraint(kind="eq", left=left_key, right=right_key))

        if not self._propagate(variables, constraints):
            return LoweringOutcome(status="unsat", reason="contradictory_bounds")
        return outcome

    def _expression_families(
        self,
        expr: exp.Expression,
        tables: Tuple[str, ...],
    ) -> Dict[str, TypeFamily]:
        families: Dict[str, TypeFamily] = {}
        for col in expr.find_all(exp.Column):
            table = _col_table(col, tables)
            name = f"{table}.{col.name}"
            dtype = col_type(col)
            families[name] = type_family(dtype) if dtype else TypeFamily.TEXT
        return families

    def _extract_variables(
        self,
        tables: Tuple[str, ...],
        expressions: List[exp.Expression],
    ) -> Dict[str, CSPVariable]:
        variables: Dict[str, CSPVariable] = {}
        for expr in expressions:
            for col in expr.find_all(exp.Column):
                table = _col_table(col, tables)
                name = f"{table}.{col.name}"
                if name not in variables:
                    dtype = col_type(col)
                    family = type_family(dtype) if dtype else TypeFamily.TEXT
                    space = ValueSpace(family=family)
                    variables[name] = CSPVariable(
                        name=name, table=table, column=col.name, space=space,
                    )
        return variables

    def _apply_predicates(
        self,
        variables: Dict[str, CSPVariable],
        predicates: List[ColumnPredicate],
    ) -> None:
        for pred in predicates:
            name = f"{pred.table}.{pred.column}"
            if name not in variables:
                space = ValueSpace(family=_predicate_family(pred))
                variables[name] = CSPVariable(
                    name=name, table=pred.table, column=pred.column, space=space,
                )
            space = variables[name].space
            op, val = pred.op, pred.value
            # Infer real type for string values on TEXT columns.
            # A TEXT column storing '596' should compare numerically.
            if isinstance(val, str) and space.family == TypeFamily.TEXT:
                inferred = infer_type_from_string(val)
                if not isinstance(inferred, str):
                    val = inferred
                    if isinstance(val, int):
                        space.family = TypeFamily.INTEGER
                    elif isinstance(val, float):
                        space.family = TypeFamily.DECIMAL
                    elif isinstance(val, datetime):
                        space.family = TypeFamily.DATETIME
                    elif isinstance(val, date):
                        space.family = TypeFamily.DATE
                    elif isinstance(val, dt_time):
                        space.family = TypeFamily.TIME
            if op == "=":
                if space.equals is not None and space.equals != val:
                    space.narrow_neq(val)
                space.narrow_eq(val)
            elif op == ">":
                if isinstance(val, int):
                    space.narrow_min(val + 1)
                elif isinstance(val, float):
                    space.narrow_min(val + 0.01)
                elif isinstance(val, date) and not isinstance(val, datetime):
                    space.narrow_min(val + timedelta(days=1))
                elif isinstance(val, datetime):
                    space.narrow_min(val + timedelta(seconds=1))
                elif isinstance(val, dt_time):
                    space.narrow_min(val)
                else:
                    space.narrow_min(val)
            elif op == ">=":
                space.narrow_min(val)
            elif op == "<":
                if isinstance(val, int):
                    space.narrow_max(val - 1)
                elif isinstance(val, float):
                    space.narrow_max(val - 0.01)
                elif isinstance(val, date) and not isinstance(val, datetime):
                    space.narrow_max(val - timedelta(days=1))
                elif isinstance(val, datetime):
                    space.narrow_max(val - timedelta(seconds=1))
                elif isinstance(val, dt_time):
                    space.narrow_max(val)
                else:
                    space.narrow_max(val)
            elif op == "<=":
                space.narrow_max(val)
            elif op == "!=":
                space.narrow_neq(val)
            elif op == "like":
                space.like_pattern = val
            elif op == "is_null":
                space.must_null = True
            elif op == "not_null":
                space.not_null = True
            elif op == "in" and isinstance(val, list):
                space.narrow_in(set(val))
            elif op == "between" and isinstance(val, tuple):
                space.narrow_min(val[0])
                space.narrow_max(val[1])

    def _build_equivalences(
        self,
        variables: Dict[str, CSPVariable],
        join_equalities: List[Tuple[str, str, str, str]],
        alias_map: Dict[str, str],
    ) -> List[CSPConstraint]:
        constraints: List[CSPConstraint] = []
        for lt, lc, rt, rc in join_equalities:
            # Resolve to alias namespace (same as variables from expressions).
            lt_key = self._resolve_alias(lt, variables, alias_map)
            rt_key = self._resolve_alias(rt, variables, alias_map)
            left_key = f"{lt_key}.{lc}"
            right_key = f"{rt_key}.{rc}"
            # Create variables for join columns not yet in scope.
            # Inherit type family from the existing side so pick() uses
            # the correct strategy (numeric vs text vs temporal).
            existing_family = None
            for key in (left_key, right_key):
                if key in variables:
                    existing_family = variables[key].space.family
                    break
            for key, table, column in [(left_key, lt_key, lc), (right_key, rt_key, rc)]:
                if key not in variables:
                    space = ValueSpace(family=existing_family) if existing_family else ValueSpace()
                    variables[key] = CSPVariable(
                        name=key, table=table, column=column,
                        space=space,
                    )
            constraints.append(CSPConstraint(kind="eq", left=left_key, right=right_key))
        return constraints

    def _resolve_alias(
        self, name: str, variables: Dict[str, CSPVariable], alias_map: Dict[str, str],
    ) -> str:
        """Resolve a table name to the alias used in variable keys."""
        raw = normalize_name(name)
        # Check if any existing variable uses this as a table prefix.
        for var_key in variables:
            if var_key.startswith(f"{raw}."):
                return raw
        # Check alias_map: name might be a physical name, find the alias.
        for alias, physical in alias_map.items():
            if normalize_name(physical) == raw:
                # Check if this alias is used in variables.
                for var_key in variables:
                    if var_key.startswith(f"{alias}."):
                        return alias
        return raw

    def _propagate(
        self,
        variables: Dict[str, CSPVariable],
        constraints: List[CSPConstraint],
    ) -> bool:
        changed = True
        iterations = 0
        while changed and iterations < 10:
            changed = False
            iterations += 1
            for c in constraints:
                if c.kind == "eq":
                    left = variables.get(c.left)
                    right = variables.get(c.right)
                    if left and right:
                        # Propagate equals
                        if left.space.equals is not None and right.space.equals is None:
                            right.space.narrow_eq(left.space.equals)
                            changed = True
                        elif right.space.equals is not None and left.space.equals is None:
                            left.space.narrow_eq(right.space.equals)
                            changed = True
                        # Propagate bounds (bidirectional)
                        if left.space.min_val is not None:
                            if right.space.min_val is None or left.space.min_val > right.space.min_val:
                                right.space.narrow_min(left.space.min_val)
                                changed = True
                        if right.space.min_val is not None:
                            if left.space.min_val is None or right.space.min_val > left.space.min_val:
                                left.space.narrow_min(right.space.min_val)
                                changed = True
                        if left.space.max_val is not None:
                            if right.space.max_val is None or left.space.max_val < right.space.max_val:
                                right.space.narrow_max(left.space.max_val)
                                changed = True
                        if right.space.max_val is not None:
                            if left.space.max_val is None or right.space.max_val < left.space.max_val:
                                left.space.narrow_max(right.space.max_val)
                                changed = True
                        # Propagate finite-domain restrictions across equality.
                        shared_not_equals = left.space.not_equals | right.space.not_equals
                        if shared_not_equals != left.space.not_equals:
                            left.space.not_equals = set(shared_not_equals)
                            changed = True
                        if shared_not_equals != right.space.not_equals:
                            right.space.not_equals = set(shared_not_equals)
                            changed = True
                        if left.space.allowed is not None and right.space.allowed is not None:
                            shared_allowed = left.space.allowed & right.space.allowed
                            if shared_allowed != left.space.allowed:
                                left.space.allowed = set(shared_allowed)
                                changed = True
                            if shared_allowed != right.space.allowed:
                                right.space.allowed = set(shared_allowed)
                                changed = True
                        elif left.space.allowed is not None and right.space.allowed is None:
                            right.space.allowed = set(left.space.allowed)
                            changed = True
                        elif right.space.allowed is not None and left.space.allowed is None:
                            left.space.allowed = set(right.space.allowed)
                            changed = True
            for var in variables.values():
                if var.space.is_empty():
                    return False
        # Finalize: pick values for eq-constrained pairs that still lack equals.
        # Pick from the more constrained side; fall back to the other if pick fails.
        for c in constraints:
            if c.kind == "eq":
                left = variables.get(c.left)
                right = variables.get(c.right)
                if left and right and left.space.equals is None and right.space.equals is None:
                    left_has_bounds = left.space.min_val is not None or left.space.max_val is not None
                    right_has_bounds = right.space.min_val is not None or right.space.max_val is not None
                    if right_has_bounds and not left_has_bounds:
                        val = right.space.pick() or left.space.pick()
                    elif left_has_bounds and not right_has_bounds:
                        val = left.space.pick() or right.space.pick()
                    else:
                        val = left.space.pick() or right.space.pick()
                    if val is not None:
                        left.space.narrow_eq(val)
                        right.space.narrow_eq(val)
        return True

    def _assign(
        self,
        variables: Dict[str, CSPVariable],
        target_tables: Tuple[str, ...],
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for var in variables.values():
            val = var.space.pick()
            var.assigned = val
            result[f"{var.table}.{var.column}"] = val
        return result
