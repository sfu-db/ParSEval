"""CSP-lite constraint solver using value-space narrowing."""
from __future__ import annotations

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
)


def _lower_expression(
    expr: exp.Expression,
    tables: Tuple[str, ...],
    alias_map: Dict[str, str],
) -> List[ColumnPredicate]:
    """Lower a sqlglot expression into simple column predicates."""
    preds: List[ColumnPredicate] = []
    _lower_recursive(expr, tables, alias_map, preds)
    return preds


def _lower_recursive(
    expr: exp.Expression,
    tables: Tuple[str, ...],
    alias_map: Dict[str, str],
    out: List[ColumnPredicate],
) -> None:
    if isinstance(expr, exp.And):
        _lower_recursive(expr.left, tables, alias_map, out)
        _lower_recursive(expr.right, tables, alias_map, out)
        return
    if isinstance(expr, exp.Paren):
        _lower_recursive(expr.this, tables, alias_map, out)
        return
    if isinstance(expr, exp.Or):
        _lower_recursive(expr.left, tables, alias_map, out)
        return
    pred = _lower_atom(expr, tables, alias_map)
    if pred:
        out.append(pred)


def _lower_atom(
    atom: exp.Expression,
    tables: Tuple[str, ...],
    alias_map: Dict[str, str],
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
        if isinstance(atom.this, exp.Column) and isinstance(right, exp.Null):
            col = atom.this
            val = True
            op = "is_null"
    elif isinstance(atom, exp.Like):
        if isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Literal):
            col = atom.this
            val = str(atom.expression.this)
            op = "like"

    if col is not None and val is not None and op is not None:
        table = _resolve_table(col, tables, alias_map)
        return ColumnPredicate(table=table, column=col.name, op=op, value=val)
    return None


def _extract_col_literal(node: exp.Expression):
    left, right = node.this, node.expression
    if isinstance(left, exp.Column) and isinstance(right, (exp.Literal, exp.Boolean)):
        return left, _literal_value(right)
    if isinstance(right, exp.Column) and isinstance(left, (exp.Literal, exp.Boolean)):
        return right, _literal_value(left)
    return None, None


def _literal_value(node: exp.Expression):
    if isinstance(node, exp.Literal):
        if node.is_int:
            return int(node.this)
        if node.is_number:
            return float(node.this)
        return str(node.this)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    return None


def _resolve_table(col: exp.Column, tables: Tuple[str, ...], alias_map: Dict[str, str]) -> str:
    """Resolve a column's table qualifier to a key in target_tables.

    Returns the alias (from target_tables) that matches, so variables
    stay in alias-namespace.  Falls back to the first table if no match.
    """
    if col.table:
        raw = normalize_name(col.table)
        # Direct match against target_tables (covers alias case).
        for t in tables:
            if normalize_name(t) == raw:
                return t
        # alias_map resolved to physical name — find which alias maps to it.
        resolved = alias_map.get(raw, raw)
        for t in tables:
            t_norm = normalize_name(t)
            if t_norm == resolved:
                return t
            # Check if t is an alias that maps to the same physical table.
            if alias_map.get(t_norm, t_norm) == resolved:
                return t
    return tables[0] if tables else ""


class DomainSolver:
    """CSP-lite solver using value-space narrowing."""

    def solve(self, constraint) -> Optional[Dict[str, Dict[str, Any]]]:
        """Solve constraints and return assignments per table.

        Args:
            constraint: A :class:`SolverConstraint` with typed expressions.
        """
        target_tables = constraint.target_tables
        expressions = constraint.constraints
        join_equalities = constraint.join_equalities or []
        alias_map = constraint.alias_map or {}

        # 1. Extract variables from expressions
        variables = self._extract_variables(target_tables, expressions, alias_map)

        # 2. Lower expressions to predicates
        all_preds: List[ColumnPredicate] = []
        for expr in expressions:
            all_preds.extend(_lower_expression(expr, target_tables, alias_map))

        # 3. Apply predicates to variables
        self._apply_predicates(variables, all_preds)

        # 4. Build equivalences from join equalities
        constraints = self._build_equivalences(variables, join_equalities, alias_map)

        # 5. Propagate
        if not self._propagate(variables, constraints):
            return None

        # 6. Assign
        return self._assign(variables, target_tables)

    def _extract_variables(
        self,
        tables: Tuple[str, ...],
        expressions: List[exp.Expression],
        alias_map: Dict[str, str],
    ) -> Dict[str, CSPVariable]:
        variables: Dict[str, CSPVariable] = {}
        for expr in expressions:
            for col in expr.find_all(exp.Column):
                table = _resolve_table(col, tables, alias_map)
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
                space = ValueSpace()
                variables[name] = CSPVariable(
                    name=name, table=pred.table, column=pred.column, space=space,
                )
            space = variables[name].space
            op, val = pred.op, pred.value
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
            elif op == "like":
                space.like_pattern = val
            elif op == "is_null":
                space.must_null = True

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
            # Create variables for join columns not yet in scope
            for key, table, column in [(left_key, lt_key, lc), (right_key, rt_key, rc)]:
                if key not in variables:
                    variables[key] = CSPVariable(
                        name=key, table=table, column=column,
                        space=ValueSpace(),
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
                        if left.space.equals is not None and right.space.equals is None:
                            right.space.narrow_eq(left.space.equals)
                            changed = True
                        elif right.space.equals is not None and left.space.equals is None:
                            left.space.narrow_eq(right.space.equals)
                            changed = True
            for var in variables.values():
                if var.space.is_empty():
                    return False
        # Finalize: pick values for eq-constrained pairs that still lack equals
        for c in constraints:
            if c.kind == "eq":
                left = variables.get(c.left)
                right = variables.get(c.right)
                if left and right and left.space.equals is None and right.space.equals is None:
                    val = left.space.pick()
                    if val is not None:
                        left.space.narrow_eq(val)
                        right.space.narrow_eq(val)
        return True

    def _assign(
        self,
        variables: Dict[str, CSPVariable],
        target_tables: Tuple[str, ...],
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        result: Dict[str, Dict[str, Any]] = {}
        for var in variables.values():
            val = var.space.pick()
            var.assigned = val
            result.setdefault(var.table, {})[var.column] = val
        if not result:
            # No variables to assign -- return empty per-table structure
            for t in target_tables:
                result[t] = {}
        return result
