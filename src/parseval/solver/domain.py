"""CSP-lite constraint solver using value-space narrowing."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp

from parseval.dtype import DataType
from parseval.helper import normalize_name

from .types import (
    CSPConstraint,
    CSPVariable,
    ColumnPredicate,
    TypeFamily,
    ValueSpace,
)


def _col_type(col: exp.Column) -> Optional[DataType]:
    """Read the annotated type from a Column node, or None."""
    dtype = getattr(col, "type", None)
    if dtype is None:
        return None
    if isinstance(dtype, DataType):
        return dtype
    try:
        return DataType.build(str(dtype))
    except Exception:
        return None


def _type_family(dtype: DataType) -> TypeFamily:
    """Map a DataType to a TypeFamily."""
    if dtype.is_type(*DataType.INTEGER_TYPES):
        return TypeFamily.INTEGER
    if dtype.is_type(*DataType.REAL_TYPES):
        return TypeFamily.DECIMAL
    if dtype.is_type(DataType.Type.BOOLEAN):
        return TypeFamily.BOOLEAN
    if dtype.is_type(
        DataType.Type.DATETIME, DataType.Type.DATETIME64,
        DataType.Type.TIMESTAMP, DataType.Type.TIMESTAMPLTZ,
        DataType.Type.TIMESTAMPTZ, DataType.Type.TIMESTAMP_MS,
        DataType.Type.TIMESTAMP_NS, DataType.Type.TIMESTAMP_S,
    ):
        return TypeFamily.DATETIME
    if dtype.is_type(DataType.Type.DATE):
        return TypeFamily.DATE
    if dtype.is_type(DataType.Type.TIME, DataType.Type.TIMETZ):
        return TypeFamily.TIME
    return TypeFamily.TEXT


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
    if col.table:
        name = normalize_name(col.table)
        name = alias_map.get(name, name)
        for t in tables:
            if normalize_name(t) == name:
                return t
    return tables[0] if tables else ""


class DomainSolver:
    """CSP-lite solver using value-space narrowing."""

    def solve(
        self,
        target_tables: Tuple[str, ...],
        expressions: List[exp.Expression],
        join_equalities: List[Tuple[str, str, str, str]] = None,
        alias_map: Dict[str, str] = None,
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Solve constraints and return assignments per table."""
        join_equalities = join_equalities or []
        alias_map = alias_map or {}

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
                    dtype = _col_type(col)
                    family = _type_family(dtype) if dtype else TypeFamily.TEXT
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
            lt_real = normalize_name(lt)
            rt_real = normalize_name(rt)
            lt_real = alias_map.get(lt_real, lt_real)
            rt_real = alias_map.get(rt_real, rt_real)
            left_key = f"{lt_real}.{lc}"
            right_key = f"{rt_real}.{rc}"
            # Create variables for join columns not yet in scope
            for key, table, column in [(left_key, lt_real, lc), (right_key, rt_real, rc)]:
                if key not in variables:
                    variables[key] = CSPVariable(
                        name=key, table=table, column=column,
                        space=ValueSpace(),
                    )
            constraints.append(CSPConstraint(kind="eq", left=left_key, right=right_key))
        return constraints

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
