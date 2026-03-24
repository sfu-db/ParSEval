"""
with speculative data generator, we could handle more data types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
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


def _spec_datatype(expression: exp.Expression, spec: _SUPPORTED_SPEC):
    if isinstance(spec, FunctionSpec):
        if spec.name.upper() in {"LENGTH", "ABS", "INSTR"}:
            return exp.DataType.build("INT")
        if spec.name.upper() == "STRFTIME":
            return exp.DataType.build("TEXT")
    return expression.type


def _extract_operand_spec(
    expression: Optional[exp.Expression],
) -> Optional[_SUPPORTED_SPEC]:
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
            else:
                values.append(n)

    return max([1, min_rows, *values])


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
            return "in"
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
                return node.this, "in"
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
                )
            )

    def _process_select(self, scope_node: ScopeNode):
        scope = scope_node.scope
        projection_map = self._build_projection_map(scope)
        expr = scope.expression.unnest()

        where = expr.args.get("where")
        if where:
            specs = self._resolve_specs(
                extract_condition_specs(where.this), projection_map
            )
            self.column_specs.extend(specs)

        joins = expr.args.get("joins", [])
        for join in joins:
            source_key, join_key, condition = join_condition(join)
            for sk, jk in zip(source_key, join_key):
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
            self.column_specs.extend(
                self._resolve_specs(extract_condition_specs(condition), projection_map)
            )

        group = expr.args.get("group")
        having = expr.args.get("having")
        if group or having:
            group_cols = []
            group_tables = []
            min_rows_per_group = _min_rows_per_group(having.this if having else None)
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
                        for agg_col in agg_func.find_all(exp.Column):
                            agg_funcs.append(
                                AgregateSpec(
                                    function=agg_func.key,
                                    table=agg_col.table,
                                    column=agg_col.name,
                                    distinct=agg_func.find(exp.Distinct) is not None,
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
        for _ in range(max_tries):
            if stop_event.is_set():
                break
            self._generate()
            if self.generator_config.negative_threshold > 0 and not stop_event.is_set():
                self._generate_negative()
            early_stoper(self.instance)
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
            self.instance.create_rows(concretes=concretes)

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
                        if generated is not None and branch.column not in tp.col_values:
                            tp.col_values[branch.column] = generated
                    except Exception:
                        raise
                continue
            if not spec.table or not getattr(spec, "column", None):
                continue
            tp = plan_for(spec.table)
            if spec.column not in tp.col_values:
                try:
                    generated = self._generate_value_for_spec(spec)
                    if generated is not None:
                        tp.col_values[spec.column] = generated
                except ValueError as e:
                    logger.warning(f"Column constraint conflict: {e}")

        for gs in self.group_specs:
            if not gs.tables:
                continue
            for table, col in zip(gs.tables, gs.group_cols):
                tp = plan_for(table)
                tp.min_rows = max(tp.min_rows, gs.min_rows_per_group)
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
                        tp.col_values[aggregate.column] = pool.generate_for_spec(
                            "NEQ", None
                        )
                    except Exception:
                        tp.col_values[aggregate.column] = (
                            f"{aggregate.function.lower()}_{aggregate.column}_0"
                        )

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
                        real_inner_table = self.table_alias.get(
                            sq.inner_select_col.table, sq.inner_select_col.table
                        )
                        reference = None
                        if sq.outer_column not in outer_tp.col_values:
                            inner_tp = plan_for(sq.inner_select_col.table)
                            if sq.inner_select_col.name in inner_tp.col_values:
                                reference = inner_tp.col_values[
                                    sq.inner_select_col.name
                                ]
                            pool = self._get_pool(
                                real_outer_table,
                                sq.outer_column,
                                alias=f"{sq.outer_table}.{sq.outer_column}",
                            )
                            if reference is None:
                                inner_pool = self._get_pool(
                                    real_inner_table,
                                    sq.inner_select_col.name,
                                    alias=f"{sq.inner_select_col.table}.{sq.inner_select_col.name}",
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

            if sq.kind == "in":
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
                                inner_key_val = pool.generate_for_spec(
                                    "EQ",
                                    inner_tp.col_values.get(
                                        sq.outer_column, pool.generate()
                                    ),
                                )
                            except Exception:
                                inner_key_val = None
                            if inner_key_val is not None:
                                outer_tp.col_values[sq.outer_column] = inner_key_val
                                inner_tp.col_values.setdefault(
                                    sq.outer_column, inner_key_val
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
                right_tp.col_values.setdefault(
                    spec.right_col, left_tp.col_values[spec.left_col]
                )
            elif spec.right_col in right_tp.col_values:
                left_tp.col_values.setdefault(
                    spec.left_col, right_tp.col_values[spec.right_col]
                )
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
            real_table = self.table_alias.get(table, table)
            for col in tp.col_values:
                try:
                    pool = self._get_pool(real_table, col)
                    if pool.unique and tp.min_rows > 1:
                        tp.min_rows = 1
                        break
                except Exception:
                    pass

        concretes = {}
        ffffflag = False
        for table, tp in table_plans.items():
            real_table = self.table_alias.get(table, table)
            for _ in range(tp.min_rows):
                for col, val in tp.col_values.items():
                    concretes.setdefault(real_table, {}).setdefault(col, []).append(val)
            if tp.min_rows == 6:
                print(concretes)
                ffffflag = True
        r = self.instance.create_rows(concretes=concretes)

        if ffffflag:
            for table, rows in r.items():
                print(f"Table: {table}: {len(rows)} rows")

    def _get_pool(self, table: str, column: str, alias: Optional[str] = None):
        return self.instance.column_domains.get_or_create_pool(
            table, column, alias=alias
        )

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
                if (
                    spec.table not in sq.inner_table
                    and spec.table not in sq.inner_alias
                ):
                    continue
                if not spec.column or spec.column in table_plan.col_values:
                    continue
                try:
                    generated = self._generate_value_for_spec(spec)
                except Exception:
                    generated = None
                if generated is not None:
                    table_plan.col_values[spec.column] = generated

    def _generate_value_for_spec(
        self, spec: Union[ColumnSpec, FunctionSpec], negate: bool = False
    ) -> Any:
        real_table = self.table_alias.get(spec.table, spec.table)
        pool = self._get_pool(
            real_table, spec.column, alias=f"{spec.table}.{spec.column}"
        )
        if isinstance(spec, ColumnSpec):
            return pool.generate_for_spec(spec.op, spec.value, negate=negate)

        candidate = self._generate_function_value(spec, pool, negate=negate)
        if candidate is None:
            return None
        if self._validate_function_candidate(spec, candidate, negate=negate):
            return candidate
        logger.debug(
            "Rejected function candidate for %s(%s.%s)",
            spec.name,
            spec.table,
            spec.column,
        )
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
            return (
                self._offset_numeric(value, 1)
                if isinstance(value, (int, float))
                else "_different_"
            )
        if op == "NEQ":
            if negate:
                return value
            return (
                self._offset_numeric(value, 1)
                if isinstance(value, (int, float))
                else "_different_"
            )
        if op == "GT":
            return self._offset_numeric(value, -1 if negate else 1)
        if op == "GTE":
            return self._offset_numeric(value, -1) if negate else value
        if op == "LT":
            return self._offset_numeric(value, 1 if negate else -1)
        if op == "LTE":
            return self._offset_numeric(value, 1) if negate else value
        if op == "IN":
            if not isinstance(value, list) or not value:
                return None
            if negate:
                first = value[0]
                return (
                    self._offset_numeric(first, 1)
                    if isinstance(first, (int, float))
                    else "_not_in_list_"
                )
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
