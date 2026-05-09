"""
with speculative data generator, we could handle more data types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import reduce
import logging
import random
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING, Tuple, Union

from sqlglot import exp
from sqlglot.optimizer.eliminate_joins import join_condition

from parseval.data_generator import BaseGenerator
from parseval.helper import to_concrete
from parseval.plan import build_graph_from_scopes

if TYPE_CHECKING:
    import threading
    from parseval.plan.planner import ScopeNode

logger = logging.getLogger(__name__)


@dataclass
class ColumnSpec:
    table: Optional[str]
    column: str
    op: str
    value: Any


@dataclass
class FunctionSpec:
    name: str
    table: Optional[str]
    column: Optional[str]
    op: str
    value: Any
    args: List[Any] = field(default_factory=list)


@dataclass
class DisjunctionSpec:
    branches: List[List[Union[ColumnSpec, FunctionSpec]]]


@dataclass
class JoinSpec:
    left_table: str
    left_col: str
    right_table: str
    right_col: str
    side: Optional[str] = "inner"


@dataclass
class GroupSpec:
    tables: List[str]
    group_cols: List[str]
    min_rows_per_group: int = 3
    aggregates: List[AgregateSpec] = field(default_factory=list)


@dataclass
class AgregateSpec:
    function: str
    table: Optional[str]
    column: Optional[str]
    distinct: bool = False
    op: Optional[str] = None
    value: Any = None


@dataclass
class SubquerySpec:
    kind: str
    outer_table: str
    outer_column: Optional[str]
    inner_table: List[str]
    inner_alias: List[str]
    inner_specs: List[Any]
    inner_select_col: Optional[exp.Column] = None
    outer_op: Optional[str] = None
    corr_col: Optional[Any] = None
    aggregate_function: Optional[str] = None


@dataclass
class SetOpSpec:
    kind: str
    left_table: str
    right_table: str
    left_cols: List[str]
    right_cols: List[str]


@dataclass
class GenerationSpec:
    column_specs: List[Any] = field(default_factory=list)
    join_specs: List[JoinSpec] = field(default_factory=list)
    group_specs: List[GroupSpec] = field(default_factory=list)
    subquery_specs: List[SubquerySpec] = field(default_factory=list)
    set_op_specs: List[SetOpSpec] = field(default_factory=list)


@dataclass
class TablePlan:
    """
    All constraints for a single table merged into one place.
    col_values  — {col: concrete_value} that every generated row must have
    min_rows    — how many rows to insert
    """

    col_values: Dict[str, Any] = field(default_factory=dict)
    min_rows: int = 3


_SUPPORTED_SPEC = Union[ColumnSpec, FunctionSpec]
_SPEC_ITEM = Union[ColumnSpec, FunctionSpec, DisjunctionSpec]


def _flip_operator(op: str) -> str:
    return {
        "GT": "LT",
        "GTE": "LTE",
        "LT": "GT",
        "LTE": "GTE",
    }.get(op, op)


def _with_predicate(spec: _SUPPORTED_SPEC, op: str, value: Any) -> _SUPPORTED_SPEC:
    if isinstance(spec, ColumnSpec):
        return ColumnSpec(table=spec.table, column=spec.column, op=op, value=value)
    return FunctionSpec(
        name=spec.name,
        table=spec.table,
        column=spec.column,
        op=op,
        value=value,
        args=list(spec.args),
    )


def _has_explicit_constraint_value(spec: Union[ColumnSpec, FunctionSpec]) -> bool:
    return spec.op == "IS" and spec.value is None


def _spec_datatype(expression: exp.Expression, spec: _SUPPORTED_SPEC):
    if isinstance(spec, FunctionSpec):
        if spec.name.upper() in {"LENGTH", "ABS", "INSTR"}:
            return exp.DataType.build("INT")
        if spec.name.upper() in {"STRFTIME", "SUBSTR"}:
            return exp.DataType.build("TEXT")
    return expression.type


def _extract_operand_spec(
    expression: Optional[exp.Expression],
) -> Optional[_SUPPORTED_SPEC]:
    def _literal_arg_value(arg: Optional[exp.Expression]) -> Any:
        if arg is None:
            return None
        if isinstance(arg, exp.Neg) and isinstance(arg.this, exp.Literal):
            try:
                return -int(arg.this.this)
            except (TypeError, ValueError):
                return None
        return to_concrete(arg, datatype=arg.type)

    if isinstance(expression, exp.Column):
        return ColumnSpec(
            table=expression.table, column=expression.name, op="", value=None
        )
    if isinstance(expression, exp.Length):
        column = expression.find(exp.Column)
        if column is None:
            return None
        return FunctionSpec(
            name="LENGTH",
            table=column.table,
            column=column.name,
            op="",
            value=None,
        )
    if isinstance(expression, exp.Abs):
        column = expression.find(exp.Column)
        if column is None:
            return None
        return FunctionSpec(
            name="ABS",
            table=column.table,
            column=column.name,
            op="",
            value=None,
        )
    if isinstance(expression, exp.Substring):
        column = expression.find(exp.Column)
        if column is None:
            return None
        start = _literal_arg_value(expression.args.get("start"))
        length = _literal_arg_value(expression.args.get("length"))
        return FunctionSpec(
            name="SUBSTR",
            table=column.table,
            column=column.name,
            op="",
            value=None,
            args=[start, length],
        )
    if isinstance(expression, exp.Anonymous):
        args = list(expression.expressions)
        name = str(expression.name).upper() if expression.name is not None else ""
        if name == "INSTR" and len(args) == 2 and isinstance(args[0], exp.Column):
            needle = to_concrete(args[1], datatype=args[1].type)
            if needle is None:
                return None
            return FunctionSpec(
                name="INSTR",
                table=args[0].table,
                column=args[0].name,
                op="",
                value=None,
                args=[needle],
            )
        if name in {"SUBSTR", "SUBSTRING"} and args and isinstance(args[0], exp.Column):
            start = _literal_arg_value(args[1]) if len(args) > 1 else None
            length = _literal_arg_value(args[2]) if len(args) > 2 else None
            return FunctionSpec(
                name="SUBSTR",
                table=args[0].table,
                column=args[0].name,
                op="",
                value=None,
                args=[start, length],
            )
        if name == "STRFTIME" and len(args) == 2 and isinstance(args[1], exp.Column):
            fmt = to_concrete(args[0], datatype=args[0].type)
            if fmt is None:
                return None
            return FunctionSpec(
                name="STRFTIME",
                table=args[1].table,
                column=args[1].name,
                op="",
                value=None,
                args=[fmt],
            )
    if isinstance(expression, exp.TimeToStr):
        column = expression.find(exp.Column)
        fmt_expr = expression.args.get("format")
        fmt = (
            to_concrete(fmt_expr, datatype=fmt_expr.type)
            if fmt_expr is not None
            else None
        )
        if column is not None and fmt is not None:
            return FunctionSpec(
                name="STRFTIME",
                table=column.table,
                column=column.name,
                op="",
                value=None,
                args=[fmt],
            )
    return None


def extract_condition_specs(condition: exp.Expression) -> List[_SPEC_ITEM]:
    def _normalize_comparison(
        predicate: exp.Expression,
    ) -> Tuple[Optional[exp.Expression], Optional[exp.Expression], Optional[str]]:
        left, right = predicate.left, predicate.right
        op = type(predicate).key.upper()
        if _extract_operand_spec(left) is not None:
            return left, right, op
        if _extract_operand_spec(right) is not None:
            return right, left, _flip_operator(op)
        return None, None, None

    if condition is None:
        return []
    if isinstance(condition, exp.Paren):
        return extract_condition_specs(condition.this)
    if isinstance(condition, exp.And):
        return extract_condition_specs(condition.left) + extract_condition_specs(
            condition.right
        )
    if isinstance(condition, exp.Or):
        left_specs = extract_condition_specs(condition.left)
        right_specs = extract_condition_specs(condition.right)

        def _specs(specs: List[_SPEC_ITEM]) -> List[_SUPPORTED_SPEC]:
            result: List[_SUPPORTED_SPEC] = []
            for spec in specs:
                if isinstance(spec, (ColumnSpec, FunctionSpec)):
                    result.append(spec)
                elif isinstance(spec, DisjunctionSpec) and spec.branches:
                    result.extend(spec.branches[0])
            return result

        return [DisjunctionSpec(branches=[_specs(left_specs), _specs(right_specs)])]
    if isinstance(condition, exp.Between):
        operand_spec = _extract_operand_spec(condition.this)
        if operand_spec is not None:
            low = to_concrete(
                condition.args["low"], datatype=condition.args["low"].type
            )
            high = to_concrete(
                condition.args["high"], datatype=condition.args["high"].type
            )
            if low is not None or high is not None:
                return [_with_predicate(operand_spec, op="BETWEEN", value=(low, high))]
    if isinstance(condition, exp.Is):
        operand_spec = _extract_operand_spec(condition.this)
        if operand_spec is not None:
            is_null = isinstance(condition.expression, exp.Null)
            value = None
            if not is_null:
                value = to_concrete(condition.expression, datatype=condition.this.type)
            return [_with_predicate(operand_spec, op="IS", value=value)]
    if isinstance(condition, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        left, right, op = _normalize_comparison(condition)
        if left is not None and right is not None and op is not None:
            if isinstance(right, exp.Column):
                return []
            operand_spec = _extract_operand_spec(left)
            if operand_spec is not None:
                value = to_concrete(right, datatype=_spec_datatype(left, operand_spec))
                if value is not None:
                    return [_with_predicate(operand_spec, op=op, value=value)]

    if isinstance(condition, exp.Like):
        operand_spec = _extract_operand_spec(condition.this)
        if operand_spec is not None:
            pattern = to_concrete(
                condition.expression,
                datatype=_spec_datatype(condition.this, operand_spec),
            )
            return [_with_predicate(operand_spec, op="LIKE", value=pattern)]

    if isinstance(condition, exp.In):
        operand_spec = _extract_operand_spec(condition.this)
        if operand_spec is not None:
            if not condition.expressions:
                return []
            values = [
                to_concrete(
                    value, datatype=_spec_datatype(condition.this, operand_spec)
                )
                for value in condition.expressions
            ]
            values = [value for value in values if value is not None]
            if not values:
                return []
            return [_with_predicate(operand_spec, op="IN", value=values)]

    return []


def _min_rows_per_group(having: exp.Expression, min_rows=3) -> int:
    if having is None:
        return min_rows

    values = []
    for agg_func in having.find_all(exp.Count):
        parent = agg_func.parent
        if isinstance(parent, exp.Predicate) and isinstance(parent.right, exp.Literal):
            n = int(parent.right.this)
            if isinstance(parent, (exp.GT, exp.GTE)):
                values.append(n + 1)
            elif isinstance(parent, exp.EQ):
                values.append(n)
            else:
                values.append(n)

    return max([1, *values]) if values else min_rows


def _subquery_kind(scope) -> str:
    node = scope.expression
    while node.parent is not None:
        node = node.parent
        if type(node) in (
            exp.Subquery,
            exp.Paren,
            exp.Where,
            exp.Having,
            exp.Select,
            exp.And,
            exp.Or,
        ):
            continue
        if isinstance(node, exp.Exists):
            return "not_exists" if isinstance(node.parent, exp.Not) else "exists"
        if isinstance(node, exp.Not) and isinstance(node.this, exp.Exists):
            return "not_exists"
        if isinstance(node, exp.In):
            return "not_in" if isinstance(node.parent, exp.Not) else "in"
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
            if any(
                isinstance(c, exp.Subquery)
                for c in node.args.values()
                if isinstance(c, exp.Expression)
            ):
                return "scalar"
            continue
        break
    return "unknown"


def _select_col(scope):
    node = scope.expression
    select = node.find(exp.Select)

    if select:
        expression = select.unnest()
        if expression.expressions:
            column = expression.expressions[0].find(exp.Column)
            if column:
                return column
    return None


def _select_aggregate(scope) -> Optional[str]:
    node = scope.expression
    select = node.find(exp.Select)
    if not select:
        return None
    expression = select.unnest()
    if not expression.expressions:
        return None
    first = expression.expressions[0]
    target = first.this if isinstance(first, exp.Alias) else first
    if isinstance(target, exp.AggFunc):
        return target.key.upper()
    agg = target.find(exp.AggFunc)
    return agg.key.upper() if agg is not None else None


def _outer_col(scope) -> Optional[exp.Column]:
    node = scope.expression
    while node.parent is not None:
        node = node.parent
        if type(node) in (
            exp.Subquery,
            exp.Paren,
            exp.Where,
            exp.Having,
            exp.Select,
            exp.And,
            exp.Or,
        ):
            continue
        if isinstance(node, exp.In):
            if isinstance(node.this, exp.Column):
                return (
                    node.this,
                    "not_in" if isinstance(node.parent, exp.Not) else "in",
                )
            return None, None
        if isinstance(node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
            if any(
                isinstance(c, exp.Subquery)
                for c in node.args.values()
                if isinstance(c, exp.Expression)
            ):
                left, right = node.left, node.right
                outer = left if isinstance(right, exp.Subquery) else right
                if isinstance(outer, exp.Column):
                    return outer, node.key
            return None, None
        if isinstance(node, exp.Exists):
            outer_col = next((c for c in scope.external_columns if c.table), None)
            return outer_col, "exists"
        break
    return None, None


class SpeculativeGenerator(BaseGenerator):
    def __init__(self, expr, instance, generator_config=None):
        super().__init__(expr, instance, generator_config)
        self.column_specs: List[_SPEC_ITEM] = []
        self.join_specs: List[JoinSpec] = []
        self.group_specs: List[GroupSpec] = []
        self.subquery_specs: List[SubquerySpec] = []
        self.set_op_specs: List[SetOpSpec] = []
        self.cte_map: Dict[str, str] = {}
        self._initialize()

    def _build_projection_map(self, scope) -> Dict[Tuple[str, str], Tuple[str, str]]:
        projection_map: Dict[Tuple[str, str], Tuple[str, str]] = {}
        selected_sources = getattr(scope, "selected_sources", {}) or {}
        for source_alias, selected_source in selected_sources.items():
            if not isinstance(selected_source, tuple) or len(selected_source) != 2:
                continue
            _, source_scope = selected_source
            scope_expression = getattr(source_scope, "expression", None)
            if scope_expression is None:
                continue
            select_expr = scope_expression.unnest()
            if not isinstance(select_expr, exp.Select):
                continue
            for projection in select_expr.expressions:
                output_name = projection.alias_or_name
                inner_column = (
                    projection.this if isinstance(projection, exp.Alias) else projection
                )
                if not output_name:
                    continue
                if isinstance(inner_column, exp.Column) and inner_column.table:
                    projection_map[(source_alias, output_name)] = (
                        inner_column.table,
                        inner_column.name,
                    )
                else:
                    projection_map[(source_alias, output_name)] = (None, output_name)
        return projection_map

    def _resolve_column_ref(
        self,
        table: Optional[str],
        column: str,
        projection_map: Dict[Tuple[str, str], Tuple[str, str]],
    ) -> Tuple[Optional[str], str]:
        if not table:
            return table, column
        return projection_map.get((table, column), (table, column))

    def _resolve_specs(
        self,
        specs: List[_SPEC_ITEM],
        projection_map: Dict[Tuple[str, str], Tuple[str, str]],
    ) -> List[_SPEC_ITEM]:
        resolved: List[_SPEC_ITEM] = []
        for spec in specs:
            if isinstance(spec, ColumnSpec):
                table, column = self._resolve_column_ref(
                    spec.table, spec.column, projection_map
                )
                resolved.append(
                    ColumnSpec(
                        table=table,
                        column=column,
                        op=spec.op,
                        value=spec.value,
                    )
                )
            elif isinstance(spec, FunctionSpec):
                table, column = self._resolve_column_ref(
                    spec.table, spec.column, projection_map
                )
                resolved.append(
                    FunctionSpec(
                        name=spec.name,
                        table=table,
                        column=column,
                        op=spec.op,
                        value=spec.value,
                        args=list(spec.args),
                    )
                )
            elif isinstance(spec, DisjunctionSpec):
                branches = []
                for branch in spec.branches:
                    branch_specs = []
                    for branch_spec in branch:
                        table, column = self._resolve_column_ref(
                            branch_spec.table, branch_spec.column, projection_map
                        )
                        if isinstance(branch_spec, FunctionSpec):
                            branch_specs.append(
                                FunctionSpec(
                                    name=branch_spec.name,
                                    table=table,
                                    column=column,
                                    op=branch_spec.op,
                                    value=branch_spec.value,
                                    args=list(branch_spec.args),
                                )
                            )
                        else:
                            branch_specs.append(
                                ColumnSpec(
                                    table=table,
                                    column=column,
                                    op=branch_spec.op,
                                    value=branch_spec.value,
                                )
                            )
                    branches.append(branch_specs)
                resolved.append(DisjunctionSpec(branches=branches))
            else:
                resolved.append(spec)
        return resolved

    def _extract_join_specs_from_condition(
        self,
        condition: Optional[exp.Expression],
        projection_map: Dict[Tuple[str, str], Tuple[str, str]],
        side: Optional[str],
    ) -> List[JoinSpec]:
        if condition is None:
            return []
        if isinstance(condition, exp.Paren):
            return self._extract_join_specs_from_condition(
                condition.this, projection_map, side
            )
        if isinstance(condition, exp.And):
            return self._extract_join_specs_from_condition(
                condition.left, projection_map, side
            ) + self._extract_join_specs_from_condition(
                condition.right, projection_map, side
            )
        if not isinstance(condition, exp.EQ):
            return []
        if not isinstance(condition.left, exp.Column) or not isinstance(
            condition.right, exp.Column
        ):
            return []

        left_table, left_col = self._resolve_column_ref(
            condition.left.table, condition.left.name, projection_map
        )
        right_table, right_col = self._resolve_column_ref(
            condition.right.table, condition.right.name, projection_map
        )
        if not left_table or not right_table:
            return []
        if left_table == right_table and left_col == right_col:
            return []
        return [
            JoinSpec(
                left_table=left_table,
                left_col=left_col,
                right_table=right_table,
                right_col=right_col,
                side=side or "inner",
            )
        ]

    def _extend_join_specs_from_condition(
        self,
        condition: Optional[exp.Expression],
        projection_map: Dict[Tuple[str, str], Tuple[str, str]],
        side: Optional[str],
    ) -> None:
        extracted = self._extract_join_specs_from_condition(
            condition, projection_map, side
        )
        for join_spec in extracted:
            if join_spec not in self.join_specs:
                self.join_specs.append(join_spec)

    def _extract_having_balance_specs(
        self,
        condition: Optional[exp.Expression],
        projection_map: Dict[Tuple[str, str], Tuple[str, str]],
    ) -> List[ColumnSpec]:
        if condition is None:
            return []
        if isinstance(condition, exp.Paren):
            return self._extract_having_balance_specs(condition.this, projection_map)
        if isinstance(condition, exp.And):
            return self._extract_having_balance_specs(
                condition.left, projection_map
            ) + self._extract_having_balance_specs(condition.right, projection_map)
        if not isinstance(condition, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            return []
        if not isinstance(condition.right, exp.Literal):
            return []
        try:
            target = to_concrete(condition.right, datatype=condition.right.type)
        except Exception:
            target = None
        if target != 0:
            return []
        if not isinstance(condition.left, exp.Sub):
            return []

        left_agg = condition.left.left.find(exp.Sum) or condition.left.left.find(exp.Avg)
        right_agg = condition.left.right.find(exp.Sum) or condition.left.right.find(
            exp.Avg
        )
        left_column = left_agg.find(exp.Column) if left_agg is not None else None
        right_column = right_agg.find(exp.Column) if right_agg is not None else None
        if left_column is None or right_column is None:
            return []

        left_table, left_col = self._resolve_column_ref(
            left_column.table, left_column.name, projection_map
        )
        right_table, right_col = self._resolve_column_ref(
            right_column.table, right_column.name, projection_map
        )
        if not left_table or not right_table:
            return []

        if isinstance(condition, (exp.GT, exp.GTE)):
            larger, smaller = (left_table, left_col), (right_table, right_col)
        else:
            larger, smaller = (right_table, right_col), (left_table, left_col)

        return [
            ColumnSpec(table=larger[0], column=larger[1], op="EQ", value=1),
            ColumnSpec(table=smaller[0], column=smaller[1], op="EQ", value=0),
        ]

    def _extract_age_specs(
        self,
        condition: Optional[exp.Expression],
        projection_map: Dict[Tuple[str, str], Tuple[str, str]],
    ) -> List[ColumnSpec]:
        if condition is None:
            return []
        if isinstance(condition, exp.Paren):
            return self._extract_age_specs(condition.this, projection_map)
        if isinstance(condition, exp.And):
            return self._extract_age_specs(
                condition.left, projection_map
            ) + self._extract_age_specs(condition.right, projection_map)
        if not isinstance(condition, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            return []
        if not isinstance(condition.right, exp.Literal):
            return []
        try:
            years = float(to_concrete(condition.right, datatype=condition.right.type))
        except Exception:
            return []

        left_sql = condition.left.sql(dialect=self.instance.dialect).upper()
        if "BIRTHDAY" not in left_sql:
            return []
        supports_age_expr = (
            ("JULIANDAY('NOW')" in left_sql and "JULIANDAY" in left_sql)
            or ("CURRENT_TIMESTAMP" in left_sql and "STRFTIME('%Y'" in left_sql)
            or ("CURRENT_DATE" in left_sql and "STRFTIME('%Y'" in left_sql)
            or ("DATE('NOW')" in left_sql and "STRFTIME('%Y'" in left_sql)
        )
        if not supports_age_expr:
            return []

        birthday_col = None
        for column in condition.left.find_all(exp.Column):
            if column.name.lower() == "birthday":
                birthday_col = column
                break
        if birthday_col is None:
            return []

        table, column = self._resolve_column_ref(
            birthday_col.table, birthday_col.name, projection_map
        )
        if not table:
            return []

        today = date.today()
        if "STRFTIME('%Y'" in left_sql:
            current_year = today.year
            whole_years = int(years)
            if isinstance(condition, exp.GT):
                cutoff = date(current_year - whole_years - 1, 12, 31)
                op = "LTE"
            elif isinstance(condition, exp.GTE):
                cutoff = date(current_year - whole_years, 12, 31)
                op = "LTE"
            elif isinstance(condition, exp.LT):
                cutoff = date(current_year - whole_years, 1, 1)
                op = "GTE"
            else:
                cutoff = date(current_year - whole_years, 1, 1)
                op = "GTE"
        else:
            cutoff = today - timedelta(days=int(years * 365))
            if isinstance(condition, exp.GT):
                cutoff = cutoff - timedelta(days=1)
                op = "LTE"
            elif isinstance(condition, exp.GTE):
                op = "LTE"
            elif isinstance(condition, exp.LT):
                op = "GT"
            else:
                op = "GTE"

        return [ColumnSpec(table=table, column=column, op=op, value=cutoff)]

    def _initialize(self):
        scopes = build_graph_from_scopes(self.expr)
        for scope_id in scopes.get_dependency_order():
            scope_node = scopes.get_node(scope_id)
            scope = scope_node.scope
            if scope.is_subquery:
                self._process_subquery(scope_node)
            elif (
                scope.is_cte
                or scope.is_root
                or scope.is_derived_table
                or scope.is_union
            ):
                self._process_select(scope_node)

    def _process_subquery(self, scope_node: ScopeNode):
        scope = scope_node.scope
        kind = _subquery_kind(scope)
        o_col, op = _outer_col(scope)
        corr_c = scope.external_columns
        inner_select = _select_col(scope)
        aggregate_function = _select_aggregate(scope)

        outter_table = ""
        if scope.parent and scope.parent.tables:
            outter_table = scope.parent.tables[0].name
        inner_specs = []
        select = scope.expression.find(exp.Select)

        if select:
            where = select.args.get("where")
            if where:
                inner_specs = extract_condition_specs(where.this)
            self.subquery_specs.append(
                SubquerySpec(
                    kind=kind,
                    outer_table=o_col.table if o_col and o_col.table else outter_table,
                    outer_column=o_col.name if o_col is not None else None,
                    inner_table=[t.name for t in scope.tables],
                    inner_alias=[t.alias_or_name for t in scope.tables],
                    inner_specs=inner_specs,
                    inner_select_col=inner_select,
                    outer_op=op,
                    corr_col=corr_c,
                    aggregate_function=aggregate_function,
                )
            )

    def _process_select(self, scope_node: ScopeNode):
        scope = scope_node.scope
        projection_map = self._build_projection_map(scope)
        expr = scope.expression.unnest()

        where = expr.args.get("where")
        if where:
            self._extend_join_specs_from_condition(where.this, projection_map, None)
            self.column_specs.extend(
                self._extract_age_specs(where.this, projection_map)
            )
            specs = self._resolve_specs(
                extract_condition_specs(where.this), projection_map
            )
            self.column_specs.extend(specs)

        joins = expr.args.get("joins", [])
        for join in joins:
            source_key, join_key, condition = join_condition(join)
            for sk, jk in zip(source_key, join_key):
                if not isinstance(sk, exp.Column) or not isinstance(jk, exp.Column):
                    continue
                left_table, left_col = self._resolve_column_ref(
                    sk.table or join.source_name, sk.name, projection_map
                )
                right_table, right_col = self._resolve_column_ref(
                    jk.table or join.alias_or_name, jk.name, projection_map
                )
                self.join_specs.append(
                    JoinSpec(
                        left_table=left_table,
                        left_col=left_col,
                        right_table=right_table,
                        right_col=right_col,
                        side=join.side or "inner",
                    )
                )
            self._extend_join_specs_from_condition(condition, projection_map, join.side)
            self.column_specs.extend(
                self._resolve_specs(extract_condition_specs(condition), projection_map)
            )

        group = expr.args.get("group")
        having = expr.args.get("having")
        if group or having:
            group_cols = []
            group_tables = []
            min_rows_per_group = _min_rows_per_group(
                having.this if having else None,
                min_rows=1,
            )
            if having:
                self.column_specs.extend(
                    self._extract_having_balance_specs(having.this, projection_map)
                )
            if group:
                for g in group.expressions:
                    if isinstance(g, exp.Column):
                        group_table, group_col = self._resolve_column_ref(
                            g.table, g.name, projection_map
                        )
                        group_cols.append(group_col)
                        group_tables.append(group_table)
                agg_funcs = []
                if having:
                    for agg_func in having.find_all(exp.AggFunc):
                        parent = agg_func.parent
                        agg_op = parent.key.upper() if isinstance(parent, exp.Predicate) else None
                        agg_value = None
                        if (
                            isinstance(parent, exp.Predicate)
                            and isinstance(parent.right, exp.Literal)
                        ):
                            agg_value = to_concrete(
                                parent.right, datatype=parent.right.type
                            )
                        for agg_col in agg_func.find_all(exp.Column):
                            agg_funcs.append(
                                AgregateSpec(
                                    function=agg_func.key,
                                    table=agg_col.table,
                                    column=agg_col.name,
                                    distinct=agg_func.find(exp.Distinct) is not None,
                                    op=agg_op,
                                    value=agg_value,
                                )
                            )

                self.group_specs.append(
                    GroupSpec(
                        tables=group_tables,
                        group_cols=group_cols,
                        min_rows_per_group=min_rows_per_group,
                        aggregates=agg_funcs,
                    )
                )

        for expression in expr.expressions:
            predicates = list(expression.find_all(exp.Predicate))
            if predicates:
                constraint = reduce(lambda x, y: x.or_(y), predicates)
                specs = self._resolve_specs(
                    extract_condition_specs(constraint), projection_map
                )
                self.column_specs.extend(specs)

    def generate(
        self,
        early_stoper: Optional[Callable[[], bool]],
        stop_event: threading.Event,
        timeout: Optional[float] = None,
    ):
        max_tries = self.generator_config.max_tries
        satisfied = False
        for _ in range(max_tries):
            if stop_event.is_set():
                break
            self._generate()
            if self.generator_config.negative_threshold > 0 and not stop_event.is_set():
                self._generate_negative()
            if early_stoper(self.instance):
                satisfied = True
                break
        if satisfied:
            return
        lengths = {
            table_name: len(self.instance.get_rows(table_name))
            for table_name in self.instance.tables
        }
        if any(length < self.generator_config.min_rows for length in lengths.values()):
            self.randomdb(min_rows=self.generator_config.min_rows)
            early_stoper(self.instance)

    def _generate_negative(self, min_rows=1):
        table_plans: Dict[str, TablePlan] = {}

        def plan_for(table: str) -> TablePlan:
            if table not in table_plans:
                table_plans[table] = TablePlan(min_rows=min_rows)
            return table_plans[table]

        for join_spec in self.join_specs:
            if join_spec.side in {"inner", "left"}:
                left_tp = plan_for(join_spec.left_table)
                real_left_table = self.table_alias.get(
                    join_spec.left_table, join_spec.left_table
                )
                pool = self._get_pool(real_left_table, join_spec.left_col)
                join_val = pool.generate()
                left_tp.col_values[join_spec.left_col] = join_val
            elif join_spec.side in {"right"}:
                right_tp = plan_for(join_spec.right_table)
                real_right_table = self.table_alias.get(
                    join_spec.right_table, join_spec.right_table
                )
                pool = self._get_pool(real_right_table, join_spec.right_col)
                join_val = pool.generate()
                right_tp.col_values[join_spec.right_col] = join_val

        for spec in self.column_specs:
            if isinstance(spec, DisjunctionSpec):
                continue
            if not spec.table or not getattr(spec, "column", None):
                continue
            tp = plan_for(spec.table)
            invalid_value = self._generate_value_for_spec(spec, negate=True)
            if invalid_value is not None:
                tp.col_values[spec.column] = invalid_value

        concretes = {}
        for table, tp in table_plans.items():
            real_table = self.table_alias.get(table, table)
            for _ in range(tp.min_rows):
                for col, val in tp.col_values.items():
                    concretes.setdefault(real_table, {}).setdefault(col, []).append(val)
        if concretes:
            self._create_concretes_in_dependency_order(concretes)

    def _generate(self, min_rows=3):
        limit = self.expr.find(exp.Limit)
        offset = self.expr.find(exp.Offset)
        limit_value = int(limit.expression.this) if limit else 0
        offset_value = int(offset.expression.this) if offset else 0
        min_rows = max(min_rows, offset_value + limit_value)

        table_plans: Dict[str, TablePlan] = {}

        def plan_for(table: str) -> TablePlan:
            if table not in table_plans:
                table_plans[table] = TablePlan(min_rows=min_rows)
            return table_plans[table]

        for spec in self.column_specs:
            if isinstance(spec, DisjunctionSpec):
                first_branch = random.choice(spec.branches) if spec.branches else []
                if not first_branch or not first_branch[0].table:
                    continue
                for branch in first_branch:
                    try:
                        tp = plan_for(branch.table)
                        generated = self._generate_value_for_spec(branch)
                        if (
                            generated is not None or _has_explicit_constraint_value(branch)
                        ) and branch.column not in tp.col_values:
                            tp.col_values[branch.column] = generated
                    except Exception:
                        raise
                continue
            if not spec.table or not getattr(spec, "column", None):
                continue
            tp = plan_for(spec.table)
            existing_value = tp.col_values.get(spec.column)
            if isinstance(spec, FunctionSpec) and existing_value is not None:
                if self._validate_function_candidate(spec, existing_value):
                    continue
            elif existing_value is not None:
                continue

            try:
                generated = self._generate_value_for_spec(spec)
                if generated is not None or _has_explicit_constraint_value(spec):
                    tp.col_values[spec.column] = generated
            except ValueError as e:
                logger.warning(f"Column constraint conflict: {e}")

        for gs in self.group_specs:
            if not gs.tables:
                continue
            exact_group_rows = next(
                (
                    int(aggregate.value)
                    for aggregate in gs.aggregates
                    if aggregate.function.upper() == "COUNT"
                    and aggregate.op == "EQ"
                    and aggregate.value is not None
                ),
                gs.min_rows_per_group,
            )
            for table, col in zip(gs.tables, gs.group_cols):
                tp = plan_for(table)
                tp.min_rows = (
                    exact_group_rows
                    if exact_group_rows < tp.min_rows
                    else max(tp.min_rows, gs.min_rows_per_group)
                )
                if col not in tp.col_values:
                    try:
                        real_table = self.table_alias.get(table, table)
                        pool = self._get_pool(real_table, col, alias=f"{table}.{col}")
                        tp.col_values[col] = pool.generate()
                    except Exception:
                        tp.col_values[col] = f"group_{col}_0"

            for aggregate in gs.aggregates:
                if not aggregate.table or aggregate.table not in self.table_alias:
                    continue
                if aggregate.function.upper() == "COUNT":
                    tp = plan_for(aggregate.table)
                    tp.min_rows = (
                        exact_group_rows
                        if exact_group_rows < tp.min_rows
                        else max(tp.min_rows, gs.min_rows_per_group)
                    )
                    if aggregate.column and aggregate.column not in tp.col_values:
                        try:
                            real_table = self.table_alias.get(
                                aggregate.table, aggregate.table
                            )
                            pool = self._get_pool(
                                real_table,
                                aggregate.column,
                                alias=f"{aggregate.table}.{aggregate.column}",
                            )
                            if aggregate.distinct:
                                tp.col_values[aggregate.column] = pool.generate()
                            elif pool.unique:
                                non_null_values = pool._generate(
                                    tp.min_rows, skips={None}
                                )
                                for value in non_null_values:
                                    pool.add_generated_value(value)
                                tp.col_values[aggregate.column] = non_null_values
                            else:
                                non_null_values = pool._generate(1, skips={None})
                                tp.col_values[aggregate.column] = (
                                    non_null_values[0]
                                    if non_null_values
                                    else pool.generate()
                                )
                        except Exception:
                            tp.col_values[aggregate.column] = None
                    continue
                tp = plan_for(aggregate.table)
                if aggregate.column not in tp.col_values:
                    try:
                        real_table = self.table_alias.get(
                            aggregate.table, aggregate.table
                        )
                        pool = self._get_pool(
                            real_table,
                            aggregate.column,
                            alias=f"{aggregate.table}.{aggregate.column}",
                        )
                        target_value = self._target_value_for_aggregate(
                            aggregate, tp.min_rows
                        )
                        if target_value is not None:
                            tp.col_values[aggregate.column] = self._normalize_pool_candidate(
                                pool, target_value
                            )
                        else:
                            tp.col_values[aggregate.column] = pool.generate()
                    except Exception:
                        tp.col_values[aggregate.column] = None

        for sq in self.subquery_specs:
            if sq.kind == "scalar":
                for inner_alias in sq.inner_alias:
                    inner_tp = plan_for(inner_alias)
                    self._apply_inner_specs(sq, inner_tp)

                    if sq.outer_column and sq.inner_select_col is not None:
                        outer_tp = plan_for(sq.outer_table)
                        real_outer_table = self.table_alias.get(
                            sq.outer_table, sq.outer_table
                        )
                        inner_select_table = self._resolve_inner_select_table(sq)
                        if inner_select_table is None:
                            continue
                        real_inner_table = self.table_alias.get(
                            inner_select_table, inner_select_table
                        )
                        reference = None
                        if sq.outer_column not in outer_tp.col_values:
                            inner_tp = plan_for(inner_select_table)
                            if sq.inner_select_col.name in inner_tp.col_values:
                                reference = inner_tp.col_values[
                                    sq.inner_select_col.name
                                ]
                            pool = self._get_pool(
                                real_outer_table,
                                sq.outer_column,
                                alias=f"{sq.outer_table}.{sq.outer_column}",
                            )
                            if sq.aggregate_function in {"MIN", "MAX"}:
                                maximize = sq.aggregate_function == "MAX"
                                reference = self._extreme_value_for_pool(
                                    pool, maximize=maximize
                                )
                                outer_tp.col_values[sq.outer_column] = reference
                                inner_tp.col_values[sq.inner_select_col.name] = reference
                                continue
                            if reference is None:
                                inner_pool = self._get_pool(
                                    real_inner_table,
                                    sq.inner_select_col.name,
                                    alias=f"{inner_select_table}.{sq.inner_select_col.name}",
                                )
                                generated = inner_pool._generate(1, skips={None})
                                reference = generated[0] if generated else None
                                if reference is not None:
                                    inner_pool.add_generated_value(reference)
                                    inner_tp.col_values[sq.inner_select_col.name] = (
                                        reference
                                    )

                            if reference is not None:
                                outer_val = pool.generate_for_spec(
                                    sq.outer_op, reference
                                )
                                outer_tp.col_values[sq.outer_column] = outer_val

            if sq.kind in {"in", "not_in"}:
                inner_select_table = self._resolve_inner_select_table(sq)
                for inner_table in sq.inner_table:
                    inner_tp = plan_for(inner_table)
                    self._apply_inner_specs(sq, inner_tp)
                    if sq.outer_column:
                        outer_tp = plan_for(sq.outer_table)
                        if sq.outer_column not in outer_tp.col_values:
                            try:
                                real_table = self.table_alias.get(
                                    sq.outer_table, sq.outer_table
                                )
                                pool = self._get_pool(
                                    real_table,
                                    sq.outer_column,
                                    alias=f"{sq.outer_table}.{sq.outer_column}",
                                )
                                inner_column = (
                                    sq.inner_select_col.name
                                    if sq.inner_select_col is not None
                                    else sq.outer_column
                                )
                                inner_value_table = (
                                    inner_select_table
                                    if inner_select_table is not None
                                    else inner_table
                                )
                                inner_value_tp = plan_for(inner_value_table)
                                real_inner_value_table = self.table_alias.get(
                                    inner_value_table, inner_value_table
                                )
                                inner_pool = self._get_pool(
                                    real_inner_value_table,
                                    inner_column,
                                    alias=f"{inner_value_table}.{inner_column}",
                                )
                                inner_key_val = inner_value_tp.col_values.get(
                                    inner_column
                                )
                                if inner_key_val is None:
                                    generated_inner_values = inner_pool._generate(
                                        1, skips={None}
                                    )
                                    inner_key_val = (
                                        generated_inner_values[0]
                                        if generated_inner_values
                                        else inner_pool.generate()
                                    )
                                    inner_pool.add_generated_value(inner_key_val)
                                if sq.kind == "in":
                                    outer_val = inner_key_val
                                else:
                                    outer_val = pool.generate_for_spec(
                                        "NEQ",
                                        inner_key_val,
                                    )
                            except Exception:
                                inner_key_val = None
                                outer_val = None
                            if outer_val is not None:
                                outer_tp.col_values[sq.outer_column] = outer_val
                                if inner_key_val is not None:
                                    inner_value_tp.col_values.setdefault(
                                        inner_column, inner_key_val
                                    )

            if sq.kind == "exists":
                outer_tp = plan_for(sq.outer_table)
                for inner_table in sq.inner_table:
                    inner_tp = plan_for(inner_table)
                    inner_tp.min_rows = max(inner_tp.min_rows, outer_tp.min_rows)
                    self._apply_inner_specs(sq, inner_tp)

                    if sq.corr_col and sq.outer_column:
                        outer_val = outer_tp.col_values.get(sq.outer_column)
                        if outer_val is None:
                            try:
                                real_table = self.table_alias.get(
                                    sq.outer_table, sq.outer_table
                                )
                                pool = self._get_pool(
                                    real_table,
                                    sq.outer_column,
                                    alias=f"{sq.outer_table}.{sq.outer_column}",
                                )
                                outer_val = pool.generate()
                            except Exception:
                                outer_val = None
                        if outer_val is not None:
                            for corr_col in sq.corr_col:
                                inner_tp.col_values.setdefault(corr_col, outer_val)

        for spec in self.join_specs:
            left_tp = plan_for(spec.left_table)
            right_tp = plan_for(spec.right_table)
            if spec.left_col in left_tp.col_values:
                right_tp.col_values[spec.right_col] = left_tp.col_values[spec.left_col]
            elif spec.right_col in right_tp.col_values:
                left_tp.col_values[spec.left_col] = right_tp.col_values[spec.right_col]
            else:
                try:
                    real_left_table = self.table_alias.get(
                        spec.left_table, spec.left_table
                    )
                    pool = self._get_pool(real_left_table, spec.left_col)
                    join_val = pool.generate()
                except Exception:
                    join_val = 1
                left_tp.col_values.setdefault(spec.left_col, join_val)
                right_tp.col_values.setdefault(spec.right_col, join_val)

        for table, tp in table_plans.items():
            if tp.min_rows <= 1:
                continue
            real_table = self.table_alias.get(table, table)
            if not real_table or real_table not in self.instance.tables:
                continue
            pk_columns = [
                self.instance._normalize_name(
                    pk.name if hasattr(pk, "name") else str(pk),
                    dialect=self.instance.dialect,
                )
                for pk in self.instance.get_primary_key(real_table)
            ]
            if len(pk_columns) > 1:
                existing_pk_lists = [
                    pk_col
                    for pk_col in pk_columns
                    if isinstance(tp.col_values.get(pk_col), list)
                    and len(tp.col_values.get(pk_col)) >= tp.min_rows
                ]
                if not existing_pk_lists:
                    joined_pk_columns = {
                        spec.left_col
                        for spec in self.join_specs
                        if spec.left_table == table and spec.left_col in pk_columns
                    } | {
                        spec.right_col
                        for spec in self.join_specs
                        if spec.right_table == table and spec.right_col in pk_columns
                    }
                    candidate_columns = [
                        pk_col for pk_col in pk_columns if pk_col not in joined_pk_columns
                    ] or pk_columns
                    for pk_col in candidate_columns:
                        existing_value = tp.col_values.get(pk_col)
                        if existing_value is not None and not isinstance(
                            existing_value, list
                        ):
                            expanded_values = self._expand_constrained_values(
                                table, pk_col, existing_value, tp.min_rows
                            )
                            if expanded_values is not None:
                                tp.col_values[pk_col] = expanded_values
                                break
                            continue
                        try:
                            pool = self._get_pool(real_table, pk_col, alias=f"{table}.{pk_col}")
                            unique_values = pool._generate(tp.min_rows, skips={None})
                            if len(unique_values) < tp.min_rows:
                                continue
                            for value in unique_values:
                                pool.add_generated_value(value)
                            tp.col_values[pk_col] = unique_values
                            break
                        except Exception:
                            continue
            for col in self.instance.tables.get(real_table, {}):
                if not self.instance.is_unique(real_table, col):
                    continue
                if isinstance(tp.col_values.get(col), list):
                    continue
                if col in tp.col_values:
                    expanded_values = self._expand_constrained_values(
                        table, col, tp.col_values[col], tp.min_rows
                    )
                    if expanded_values is not None:
                        tp.col_values[col] = expanded_values
                    continue
                try:
                    pool = self._get_pool(real_table, col, alias=f"{table}.{col}")
                    unique_values = pool._generate(tp.min_rows, skips={None})
                    if len(unique_values) < tp.min_rows:
                        continue
                    for value in unique_values:
                        pool.add_generated_value(value)
                    tp.col_values[col] = unique_values
                except Exception:
                    continue

        for spec in self.join_specs:
            left_tp = plan_for(spec.left_table)
            right_tp = plan_for(spec.right_table)
            left_value = left_tp.col_values.get(spec.left_col)
            right_value = right_tp.col_values.get(spec.right_col)

            if isinstance(left_value, list):
                right_tp.col_values[spec.right_col] = list(left_value)
            elif isinstance(right_value, list):
                left_tp.col_values[spec.left_col] = list(right_value)
            elif left_value is not None:
                right_tp.col_values[spec.right_col] = left_value
            elif right_value is not None:
                left_tp.col_values[spec.left_col] = right_value

        for table, tp in table_plans.items():
            real_table = self.table_alias.get(table, table)
            if not real_table or real_table not in self.instance.tables:
                continue
            for col, value in tp.col_values.items():
                try:
                    pool = self._get_pool(real_table, col)
                    if (
                        pool.unique
                        and isinstance(value, list)
                        and len(value) >= tp.min_rows
                        and len(set(value)) == len(value)
                        and all(item is not None for item in value)
                    ):
                        continue
                    if pool.unique and tp.min_rows > 1:
                        tp.min_rows = 1
                        break
                except Exception:
                    pass

        concretes = {}

        for table, tp in table_plans.items():
            real_table = self.table_alias.get(table, table)
            for col, val in tp.col_values.items():
                if isinstance(val, list):
                    concretes.setdefault(real_table, {}).setdefault(col, []).extend(
                        val[: tp.min_rows]
                    )
                else:
                    concretes.setdefault(real_table, {}).setdefault(col, []).extend(
                        [val] * tp.min_rows
                    )
        self._create_concretes_in_dependency_order(concretes)

    def _get_pool(self, table: str, column: str, alias: Optional[str] = None):
        return self.instance.column_domains.get_or_create_pool(
            table, column, alias=alias
        )

    def _create_concretes_in_dependency_order(
        self, concretes: Dict[str, Dict[str, List[Any]]]
    ) -> None:
        ordered_tables: List[str] = []
        visited: set[str] = set()

        def visit(table: str) -> None:
            if table in visited:
                return
            visited.add(table)
            for fk in self.instance.foreign_keys.get(table, []):
                ref = fk.args.get("reference")
                ref_table = ref.find(exp.Table).name if ref is not None else None
                if ref_table in concretes:
                    visit(ref_table)
            ordered_tables.append(table)

        for table in concretes:
            visit(table)

        for table in ordered_tables:
            self.instance.create_rows(concretes={table: concretes[table]})

    def _get_row_value(self, row, column: str) -> Any:
        col_norm = self.instance._normalize_name(column, dialect=self.instance.dialect)
        col_data = row.columns.get(col_norm)
        if col_data is None:
            for key, value in row.columns.items():
                if key.lower() == col_norm.lower():
                    return value.concrete if hasattr(value, "concrete") else value
            return None
        return col_data.concrete if hasattr(col_data, "concrete") else col_data

    def _apply_inner_specs(self, sq: SubquerySpec, table_plan: TablePlan) -> None:
        for inner_spec in sq.inner_specs:
            branch_specs = (
                random.choice(inner_spec.branches)
                if isinstance(inner_spec, DisjunctionSpec) and inner_spec.branches
                else [inner_spec]
            )
            for spec in branch_specs:
                if not isinstance(spec, (ColumnSpec, FunctionSpec)):
                    continue
                resolved_table = self._resolve_inner_spec_table(sq, spec.table)
                if (
                    resolved_table not in sq.inner_table
                    and resolved_table not in sq.inner_alias
                ):
                    continue
                if not spec.column or spec.column in table_plan.col_values:
                    continue
                spec_to_apply = spec
                if resolved_table != spec.table:
                    if isinstance(spec, FunctionSpec):
                        spec_to_apply = FunctionSpec(
                            name=spec.name,
                            table=resolved_table,
                            column=spec.column,
                            op=spec.op,
                            value=spec.value,
                            args=list(spec.args),
                        )
                    else:
                        spec_to_apply = ColumnSpec(
                            table=resolved_table,
                            column=spec.column,
                            op=spec.op,
                            value=spec.value,
                        )
                try:
                    generated = self._generate_value_for_spec(spec_to_apply)
                except Exception:
                    generated = None
                if generated is not None or _has_explicit_constraint_value(spec_to_apply):
                    table_plan.col_values[spec.column] = generated

    def _resolve_inner_select_table(self, sq: SubquerySpec) -> Optional[str]:
        if sq.inner_select_col is None:
            return None

        table = sq.inner_select_col.table
        if sq.inner_select_col.name:
            candidates = []
            for inner_table in [*sq.inner_alias, *sq.inner_table]:
                real_inner_table = self.table_alias.get(inner_table, inner_table)
                if sq.inner_select_col.name in self.instance.tables.get(
                    real_inner_table, {}
                ):
                    candidates.append(inner_table)
            if len(candidates) == 1:
                return candidates[0]
        if table in sq.inner_table or table in sq.inner_alias:
            return table
        if sq.inner_alias:
            return sq.inner_alias[0]
        if sq.inner_table:
            return sq.inner_table[0]
        return table

    def _resolve_inner_spec_table(
        self, sq: SubquerySpec, table: Optional[str]
    ) -> Optional[str]:
        if table in sq.inner_table or table in sq.inner_alias:
            return table
        if len(sq.inner_alias) == 1:
            return sq.inner_alias[0]
        if len(sq.inner_table) == 1:
            return sq.inner_table[0]
        return table

    def _generate_value_for_spec(
        self, spec: Union[ColumnSpec, FunctionSpec], negate: bool = False
    ) -> Any:
        real_table = self.table_alias.get(spec.table, spec.table)
        pool = self._get_pool(
            real_table, spec.column, alias=f"{spec.table}.{spec.column}"
        )
        if isinstance(spec, ColumnSpec):
            candidate = pool.generate_for_spec(spec.op, spec.value, negate=negate)
            return self._normalize_candidate_for_spec(spec, pool, candidate)

        related_specs = self._related_function_specs(spec)
        if len(related_specs) > 1:
            combined_candidate = self._generate_combined_function_value(
                spec, negate=negate
            )
            if combined_candidate is not None and all(
                self._validate_function_candidate(
                    related_spec, combined_candidate, negate=negate
                )
                for related_spec in related_specs
            ):
                return combined_candidate

        candidate = self._generate_function_value(spec, pool, negate=negate)
        if candidate is None:
            return None
        candidate = self._normalize_candidate_for_spec(spec, pool, candidate)
        if self._validate_function_candidate(spec, candidate, negate=negate):
            return candidate
        logger.debug(
            "Rejected function candidate for %s(%s.%s)",
            spec.name,
            spec.table,
            spec.column,
        )
        related_candidate = self._generate_combined_function_value(spec, negate=negate)
        if related_candidate is not None and self._validate_function_candidate(
            spec, related_candidate, negate=negate
        ):
            return related_candidate
        return None

    def _normalize_pool_candidate(self, pool, candidate: Any) -> Any:
        datatype = getattr(pool, "datatype", None)
        if (
            isinstance(candidate, datetime)
            and datatype is not None
            and str(datatype).upper() == "DATE"
        ):
            return candidate.date()
        return candidate

    def _normalize_candidate_for_spec(
        self,
        spec: Union[ColumnSpec, FunctionSpec],
        pool,
        candidate: Any,
    ) -> Any:
        candidate = self._normalize_pool_candidate(pool, candidate)
        datatype = getattr(pool, "datatype", None)
        datatype_name = str(datatype).upper() if datatype is not None else ""

        if (
            self.instance.dialect == "sqlite"
            and spec.op == "EQ"
            and isinstance(spec.value, datetime)
            and datatype_name in {"DATETIME", "TIMESTAMP", "TIMESTAMPTZ", "TIMESTAMPLTZ"}
        ):
            return spec.value.strftime("%Y-%m-%d %H:%M:%S.0")

        return candidate

    def _related_function_specs(self, spec: FunctionSpec) -> List[FunctionSpec]:
        return [
            candidate
            for candidate in self.column_specs
            if isinstance(candidate, FunctionSpec)
            and candidate.table == spec.table
            and candidate.column == spec.column
        ]

    def _related_column_specs(
        self, table: str, column: str
    ) -> List[Union[ColumnSpec, FunctionSpec]]:
        return [
            candidate
            for candidate in self.column_specs
            if isinstance(candidate, (ColumnSpec, FunctionSpec))
            and candidate.table == table
            and candidate.column == column
        ]

    def _expand_constrained_values(
        self, table: str, column: str, base_value: Any, count: int
    ) -> Optional[List[Any]]:
        if count <= 1:
            return [base_value]

        specs = self._related_column_specs(table, column)
        values = [base_value]
        seen = {base_value}

        for step in range(1, count * 8 + 1):
            candidates = [
                self._offset_ordered_value(base_value, step),
                self._offset_ordered_value(base_value, -step),
            ]
            for candidate in candidates:
                if candidate in seen:
                    continue
                if any(
                    isinstance(spec, FunctionSpec)
                    and not self._validate_function_candidate(spec, candidate)
                    for spec in specs
                ):
                    continue
                if any(
                    isinstance(spec, ColumnSpec)
                    and not self._compare_values(candidate, spec.op, spec.value)
                    for spec in specs
                ):
                    continue
                values.append(candidate)
                seen.add(candidate)
                if len(values) >= count:
                    return values

        return None

    def _generate_combined_function_value(
        self, spec: FunctionSpec, negate: bool = False
    ) -> Optional[Any]:
        related_specs = self._related_function_specs(spec)
        if not related_specs:
            return None

        if all(candidate.name.upper() in {"LENGTH", "SUBSTR"} for candidate in related_specs):
            return self._generate_combined_string_value(related_specs, negate=negate)
        if all(candidate.name.upper() == "STRFTIME" for candidate in related_specs):
            return self._generate_combined_strftime_value(related_specs, negate=negate)

        return None

    def _generate_combined_string_value(
        self, specs: List[FunctionSpec], negate: bool = False
    ) -> Optional[str]:
        target_length: Optional[int] = None
        suffix: Optional[str] = None

        for spec in specs:
            name = spec.name.upper()
            if name == "LENGTH":
                target = self._choose_target_value(spec.op, spec.value, negate=negate)
                try:
                    target_length = int(target)
                except (TypeError, ValueError):
                    return None
            elif name == "SUBSTR":
                start = spec.args[0] if spec.args else None
                length = spec.args[1] if len(spec.args) > 1 else None
                target = self._choose_target_value(spec.op, spec.value, negate=negate)
                if target is None:
                    return None
                if isinstance(start, int) and start < 0 and length in (None, 1):
                    suffix = str(target)

        if suffix is None and target_length is None:
            return None

        if suffix is None:
            suffix = "x"

        if target_length is not None:
            if target_length < len(suffix):
                return None
            prefix_len = target_length - len(suffix)
            prefix = "x" * prefix_len
            return prefix + suffix

        return "prefix_" + suffix

    def _generate_combined_strftime_value(
        self, specs: List[FunctionSpec], negate: bool = False
    ) -> Optional[Any]:
        year = 2000
        month = 1
        day = 1

        for spec in specs:
            fmt = spec.args[0] if spec.args else None
            target = self._choose_target_value(spec.op, spec.value, negate=negate)
            if target is None or not isinstance(fmt, str):
                continue
            try:
                if fmt == "%Y":
                    year = int(target)
                elif fmt == "%m":
                    month = int(target)
                elif fmt == "%d":
                    day = int(target)
                elif fmt == "%Y-%m-%d":
                    parsed = datetime.strptime(str(target), "%Y-%m-%d").date()
                    year, month, day = parsed.year, parsed.month, parsed.day
            except (TypeError, ValueError):
                return None

        try:
            return date(year, month, day)
        except ValueError:
            return None

    def _generate_function_value(
        self, spec: FunctionSpec, pool, negate: bool = False
    ) -> Any:
        function_name = spec.name.upper()
        if function_name == "LENGTH":
            candidate = self._generate_length_value(spec, negate=negate)
        elif function_name == "ABS":
            candidate = self._generate_abs_value(spec, negate=negate)
        elif function_name == "INSTR":
            candidate = self._generate_instr_value(spec, negate=negate)
        elif function_name == "SUBSTR":
            candidate = self._generate_substr_value(spec, negate=negate)
        elif function_name == "STRFTIME":
            candidate = self._generate_strftime_value(spec, pool, negate=negate)
        else:
            candidate = None
        if candidate is None:
            return pool.generate()
        return candidate

    def _generate_length_value(
        self, spec: FunctionSpec, negate: bool = False
    ) -> Optional[str]:
        target = self._choose_target_value(spec.op, spec.value, negate=negate)
        if target is None:
            return None
        try:
            length = max(0, int(target))
        except (TypeError, ValueError):
            return None
        return "x" * length

    def _generate_abs_value(
        self, spec: FunctionSpec, negate: bool = False
    ) -> Optional[int]:
        target = self._choose_target_value(spec.op, spec.value, negate=negate)
        if target is None:
            return None
        try:
            value = int(target)
        except (TypeError, ValueError):
            return None
        if value < 0:
            return None
        if value != 0 and random.choice([True, False]):
            return -value
        return value

    def _generate_instr_value(
        self, spec: FunctionSpec, negate: bool = False
    ) -> Optional[str]:
        needle = spec.args[0] if spec.args else None
        if not isinstance(needle, str) or needle == "":
            return None
        target = self._choose_target_value(spec.op, spec.value, negate=negate)
        if target is None:
            return None
        try:
            position = int(target)
        except (TypeError, ValueError):
            return None
        if position <= 0:
            return "zzz"
        if position == 1:
            return needle + "_tail"
        return ("x" * (position - 1)) + needle + "_tail"

    def _generate_substr_value(
        self, spec: FunctionSpec, negate: bool = False
    ) -> Optional[str]:
        start = spec.args[0] if spec.args else None
        length = spec.args[1] if len(spec.args) > 1 else None
        target = self._choose_target_value(spec.op, spec.value, negate=negate)
        if target is None:
            return None

        target_text = str(target)
        if isinstance(length, int) and length >= 0:
            target_text = target_text[:length].ljust(length, "x")

        if isinstance(start, int):
            if start < 0:
                return "prefix_" + target_text
            if start <= 1:
                return target_text + "_tail"
            return ("x" * (start - 1)) + target_text + "_tail"

        return target_text

    def _generate_strftime_value(
        self, spec: FunctionSpec, pool, negate: bool = False
    ) -> Any:
        fmt = spec.args[0] if spec.args else None
        if not isinstance(fmt, str):
            return None
        target = self._choose_target_value(spec.op, spec.value, negate=negate)
        concrete = self._coerce_temporal_target(fmt, target)
        if concrete is not None:
            return concrete
        return pool.generate()

    def _offset_ordered_value(self, value: Any, amount: int) -> Any:
        offset_value = self._offset_numeric(value, amount)
        if offset_value is not value:
            return offset_value

        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                width = len(stripped)
                adjusted = int(stripped) + amount
                return f"{adjusted:0{width}d}" if width > 1 else str(adjusted)
            if amount > 0:
                return value + ("z" * amount)
            if amount < 0:
                trimmed = value[:amount]
                return trimmed if trimmed else value + "_a"

        return value

    def _coerce_temporal_target(self, fmt: str, value: Any) -> Optional[Any]:
        if not isinstance(value, str):
            return None
        try:
            if fmt == "%Y":
                return date(int(value), 1, 1)
            if fmt == "%m":
                return date(2000, int(value), 1)
            if fmt == "%d":
                return date(2000, 1, int(value))
            if fmt == "%Y-%m-%d":
                return datetime.strptime(value, "%Y-%m-%d").date()
            if fmt == "%Y-%m-%d %H:%M:%S":
                return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        return None

    def _validate_function_candidate(
        self, spec: FunctionSpec, candidate: Any, negate: bool = False
    ) -> bool:
        concrete = self._evaluate_function(spec, candidate)
        if concrete is None and spec.op != "IS":
            return False
        return self._compare_values(concrete, spec.op, spec.value, negate=negate)

    def _evaluate_function(self, spec: FunctionSpec, candidate: Any) -> Any:
        function_name = spec.name.upper()
        if function_name == "LENGTH":
            return len(candidate) if candidate is not None else None
        if function_name == "ABS":
            return abs(candidate) if candidate is not None else None
        if function_name == "INSTR":
            needle = spec.args[0] if spec.args else None
            if not isinstance(candidate, str) or not isinstance(needle, str):
                return None
            index = candidate.find(needle)
            return index + 1 if index >= 0 else 0
        if function_name == "SUBSTR":
            start = spec.args[0] if spec.args else None
            length = spec.args[1] if len(spec.args) > 1 else None
            if not isinstance(candidate, str) or not isinstance(start, int):
                return None
            begin = max(len(candidate) + start, 0) if start < 0 else max(start - 1, 0)
            if isinstance(length, int) and length >= 0:
                return candidate[begin : begin + length]
            return candidate[begin:]
        if function_name == "STRFTIME":
            fmt = spec.args[0] if spec.args else None
            if not isinstance(fmt, str):
                return None
            if isinstance(candidate, (date, datetime, time)):
                return candidate.strftime(fmt)
            return None
        return None

    def _compare_values(
        self, left: Any, op: str, right: Any, negate: bool = False
    ) -> bool:
        if op == "EQ":
            result = left == right
        elif op == "NEQ":
            result = left != right
        elif op == "GT":
            result = left is not None and right is not None and left > right
        elif op == "GTE":
            result = left is not None and right is not None and left >= right
        elif op == "LT":
            result = left is not None and right is not None and left < right
        elif op == "LTE":
            result = left is not None and right is not None and left <= right
        elif op == "IN":
            result = left in right
        elif op == "BETWEEN":
            low, high = right
            result = (low is None or left >= low) and (high is None or left <= high)
        elif op == "IS":
            result = left is right
        elif op == "LIKE":
            pattern = str(right).replace("%", "")
            result = isinstance(left, str) and pattern in left
        else:
            result = False
        return not result if negate else result

    def _choose_target_value(self, op: str, value: Any, negate: bool = False) -> Any:
        if op == "EQ":
            if not negate:
                return value
            candidate = self._offset_ordered_value(value, 1)
            return candidate if candidate != value else "_different_"
        if op == "NEQ":
            if negate:
                return value
            candidate = self._offset_ordered_value(value, 1)
            return candidate if candidate != value else "_different_"
        if op == "GT":
            return self._offset_ordered_value(value, -1 if negate else 1)
        if op == "GTE":
            return self._offset_ordered_value(value, -1) if negate else value
        if op == "LT":
            return self._offset_ordered_value(value, 1 if negate else -1)
        if op == "LTE":
            return self._offset_ordered_value(value, 1) if negate else value
        if op == "IN":
            if not isinstance(value, list) or not value:
                return None
            if negate:
                first = value[0]
                candidate = self._offset_ordered_value(first, 1)
                return candidate if candidate != first else "_not_in_list_"
            return random.choice(value)
        if op == "BETWEEN":
            low, high = value
            if negate:
                if low is not None and isinstance(low, (int, float)):
                    return low - 1
                if high is not None and isinstance(high, (int, float)):
                    return high + 1
                return None
            if (
                low is not None
                and high is not None
                and isinstance(low, (int, float))
                and isinstance(high, (int, float))
            ):
                return (
                    (low + high) // 2
                    if isinstance(low, int) and isinstance(high, int)
                    else (low + high) / 2
                )
            return low if low is not None else high
        if op == "LIKE":
            return "zzz" if negate else str(value).replace("%", "abc").replace("_", "x")
        if op == "IS":
            return "not_null" if negate and value is None else value
        return value

    def _offset_numeric(self, value: Any, amount: int) -> Any:
        if isinstance(value, int):
            return value + amount
        if isinstance(value, float):
            return value + float(amount)
        return value

    def _target_value_for_aggregate(
        self, aggregate: AgregateSpec, min_rows: int
    ) -> Any:
        if aggregate.value is None:
            return None

        function = aggregate.function.upper()
        if function == "SUM" and isinstance(aggregate.value, (int, float)):
            threshold = aggregate.value
            if aggregate.op == "GT":
                threshold = threshold + 1
            elif aggregate.op == "LT":
                threshold = threshold - 1

            if aggregate.op in {"GT", "GTE"}:
                return threshold / max(1, min_rows)
            if aggregate.op in {"LT", "LTE"}:
                return threshold / max(1, min_rows)

        if function in {"AVG", "MAX", "MIN"}:
            return self._choose_target_value(aggregate.op or "EQ", aggregate.value)

        return None

    def _extreme_value_for_pool(self, pool, maximize: bool) -> Any:
        datatype = str(getattr(pool, "datatype", "")).upper()
        if datatype in {"INT", "INTEGER", "BIGINT", "SMALLINT"}:
            return 1000 if maximize else 0
        if datatype in {"REAL", "DOUBLE", "FLOAT", "DECIMAL"}:
            return 10000.0 if maximize else 0.0
        if datatype == "DATE":
            return date(2020, 12, 31) if maximize else date(2000, 1, 1)
        if datatype in {"DATETIME", "TIMESTAMP", "TIMESTAMPTZ", "TIMESTAMPLTZ"}:
            dt = (
                datetime(2020, 12, 31, 23, 59, 59)
                if maximize
                else datetime(2000, 1, 1, 0, 0, 0)
            )
            return dt.strftime("%Y-%m-%d %H:%M:%S.0") if self.instance.dialect == "sqlite" else dt
        return pool.generate()
