"""
with speculative data generator, we could handle more data types.
"""

from __future__ import annotations
from typing import Optional, List, Dict, TYPE_CHECKING, Tuple, Union, Set, Callable, Any
from parseval.plan import build_graph_from_scopes
from parseval.data_generator import BaseGenerator
from dataclasses import dataclass, field
from sqlglot import exp
from sqlglot.optimizer.eliminate_joins import join_condition
import logging, random
from parseval.helper import to_concrete
from functools import reduce

if TYPE_CHECKING:
    from parseval.plan.planner import ScopeNode
    import threading

logger = logging.getLogger(__name__)


@dataclass
class ColumnSpec:
    table: str
    column: str
    op: str
    value: Any


@dataclass
class DisjunctionSpec:
    branches: List[List[ColumnSpec]]


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


@dataclass
class SubquerySpec:
    kind: str
    outer_table: str
    outer_column: str
    inner_table: str
    inner_alias: str

    inner_specs: List[Any]
    inner_select_col: Optional[exp.Column] = None
    outer_op: Optional[str] = None
    corr_col: Optional[exp.Column] = None


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


def extract_condition_specs(
    condition: exp.Expression,
) -> List[Union[ColumnSpec, DisjunctionSpec]]:
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

        def _col_specs(specs):
            result = []
            for s in specs:
                if isinstance(s, ColumnSpec):
                    result.append(s)
                elif isinstance(s, DisjunctionSpec) and s.branches:
                    result.extend(s.branches[0])
            return result

        return [
            DisjunctionSpec(branches=[_col_specs(left_specs), _col_specs(right_specs)])
        ]
    if isinstance(condition, exp.Between):
        col = condition.this
        if isinstance(col, exp.Column):
            lo = condition.args["low"]
            high = condition.args["high"]
            lo = to_concrete(lo, datatype=lo.type)
            hi = to_concrete(high, datatype=high.type)
            if lo is not None or hi is not None:
                return [
                    ColumnSpec(
                        table=col.table, column=col.name, op="BETWEEN", value=(lo, hi)
                    )
                ]
    if isinstance(condition, exp.Is):
        col = condition.this
        if isinstance(col, exp.Column):
            is_null = isinstance(condition.expression, exp.Null)
            value = None
            if not is_null:
                value = to_concrete(condition.expression, datatype=col.type)
            return [
                ColumnSpec(
                    table=col.table,
                    column=col.name,
                    op="IS",
                    value=value,
                )
            ]
    if isinstance(condition, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
        left, right = condition.left, condition.right
        if isinstance(right, exp.Column) and not isinstance(left, exp.Column):
            left, right = right, left
        if isinstance(left, exp.Column) and not isinstance(right, exp.Column):
            val = to_concrete(right, datatype=left.type)
            if val is not None:
                return [
                    ColumnSpec(
                        table=left.table,
                        column=left.name,
                        op=type(condition).key.upper(),
                        value=val,
                    )
                ]

    if isinstance(condition, exp.Like):
        col = condition.this
        if isinstance(col, exp.Column):

            pattern = condition.expression
            return [
                ColumnSpec(table=col.table, column=col.name, op="LIKE", value=pattern)
            ]

    if isinstance(condition, exp.In):
        col = condition.this
        if isinstance(col, exp.Column):
            values = [v for v in condition.expressions]
            return [
                ColumnSpec(
                    table=col.table,
                    column=col.name,
                    op="IN",
                    value=[v for v in values if v is not None],
                )
            ]

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

    return max(1, *values)


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
        if isinstance(node, (exp.Exists,)):
            outer_col = next((c for c in scope.external_columns if c.table), None)
            return outer_col, "exists"
        break
    return None, None

    ...


class SpeculativeGenerator(BaseGenerator):
    def __init__(self, expr, instance, generator_config=None):
        super().__init__(expr, instance, generator_config)
        self.column_specs: List[Union[ColumnSpec, DisjunctionSpec]] = []
        self.join_specs: List[JoinSpec] = []
        self.group_specs: List[GroupSpec] = []
        self.subquery_specs: List[SubquerySpec] = []
        self.set_op_specs: List[SetOpSpec] = []
        self.cte_map: Dict[str, str] = {}
        self._initialize()

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
        if scope.parent:
            if scope.parent.tables:
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
                    outer_column=o_col.name,
                    inner_table=[t.name for t in scope.tables],
                    inner_alias=[t.alias_or_name for t in scope.tables],
                    inner_specs=inner_specs,
                    inner_select_col=inner_select,
                    outer_op=op,
                    corr_col=corr_c,
                )
            )

    def _process_select(self, scope_node: ScopeNode):

        expr = scope_node.scope.expression
        expr = expr.unnest()
        limit = expr.args.get("limit")
        offset = expr.args.get("offset")
        offset = int(offset.text("expression")) if offset else 0

        if limit:
            limit = int(limit.text("expression"))

        min_rows = offset + (limit if limit is not None else 3)

        where = expr.args.get("where")
        if where:
            specs = extract_condition_specs(where.this)
            self.column_specs.extend(specs)

        joins = expr.args.get("joins", [])

        for join in joins:
            source_key, join_key, condition = join_condition(join)
            for sk, jk in zip(source_key, join_key):
                self.join_specs.append(
                    JoinSpec(
                        left_table=sk.table or join.source_name,
                        left_col=sk.name,
                        right_table=jk.table or join.alias_or_name,
                        right_col=jk.name,
                        side=join.side or "inner",
                    )
                )
            self.column_specs.extend(extract_condition_specs(condition))

        group = expr.args.get("group")
        having = expr.args.get("having")
        if group or having:
            group_cols = []
            group_tables = []
            min_rows_per_group = _min_rows_per_group(having.this if having else None)
            if group:
                for g in group.expressions:
                    if isinstance(g, exp.Column):
                        group_cols.append(g.name)
                        group_tables.append(g.table)
                self.group_specs.append(
                    GroupSpec(
                        tables=group_tables,
                        group_cols=group_cols,
                        min_rows_per_group=min_rows_per_group,
                    )
                )

        for e in expr.expressions:

            predicates = list(e.find_all(exp.Predicate))
            if predicates:
                constraint = reduce(lambda x, y: x.or_(y), predicates)
                specs = extract_condition_specs(constraint)
                self.column_specs.extend(specs)

    def generate(self, db_queue, stop_event: threading.Event, host_or_path):
        max_tries = self.generator_config.max_tries
        for _ in range(max_tries):
            if stop_event.is_set():
                break
            self._generate()
            self._generate_negative()
            if not stop_event.is_set():
                db_id = self.instance.name_seq()
                self.instance.to_db(
                    host_or_path=host_or_path,
                    database=db_id,
                )
                db_queue.put(
                    {
                        "host_or_path": host_or_path,
                        "db_id": db_id,
                    }
                )

        if not stop_event.is_set():
            self.randomdb(min_rows=max_tries)

    def _generate_negative(self):
        table_plans: Dict[str, TablePlan] = {}

        def plan_for(table: str) -> TablePlan:
            if table not in table_plans:
                table_plans[table] = TablePlan(min_rows=3)
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

        for column_spec in self.column_specs:
            if isinstance(column_spec, ColumnSpec):
                tp = plan_for(column_spec.table)
                real_table = self.table_alias.get(column_spec.table, column_spec.table)
                pool = self._get_pool(
                    real_table,
                    column_spec.column,
                    alias=f"{column_spec.table}.{column_spec.column}",
                )
                invalid_val = pool.generate_for_spec(
                    column_spec.op, column_spec.value, negate=True
                )
                tp.col_values[column_spec.column] = invalid_val

    def _generate(self):
        table_plans: Dict[str, TablePlan] = {}

        def plan_for(table: str) -> TablePlan:
            if table not in table_plans:
                table_plans[table] = TablePlan(min_rows=3)
            return table_plans[table]

        for spec in self.column_specs:
            if isinstance(spec, DisjunctionSpec):
                first_branch = random.choice(spec.branches) if spec.branches else []
                if not first_branch or not first_branch[0].table:
                    continue
                for branch in first_branch:
                    try:
                        real_table = self.table_alias.get(branch.table, branch.table)
                        pool = self._get_pool(
                            real_table,
                            branch.column,
                            alias=f"{branch.table}.{branch.column}",
                        )
                        tp = plan_for(branch.table)
                        tp.col_values[branch.column] = pool.generate_for_spec(
                            branch.op, branch.value
                        )
                    except Exception as e:
                        raise e
                continue
            if not spec.table:
                continue
            tp = plan_for(spec.table)
            real_table = self.table_alias.get(spec.table, spec.table)
            if spec.column not in tp.col_values:
                try:
                    pool = self._get_pool(
                        real_table, spec.column, alias=f"{spec.table}.{spec.column}"
                    )
                    tp.col_values[spec.column] = pool.generate_for_spec(
                        spec.op, spec.value
                    )
                except ValueError as e:
                    logger.warning(f"Column constraint conflict: {e}")
        for gs in self.group_specs:
            if not gs.tables:
                continue
            for t, col in zip(gs.tables, gs.group_cols):
                tp = plan_for(t)
                tp.min_rows = max(tp.min_rows, gs.min_rows_per_group)
                if col not in tp.col_values:
                    try:
                        real_table = self.table_alias.get(t, t)
                        pool = self._get_pool(real_table, col, alias=f"{t}.{col}")
                        tp.col_values[col] = pool.generate()
                    except Exception:
                        tp.col_values[col] = f"group_{col}_0"

        for sq in self.subquery_specs:
            if sq.kind == "scalar":
                for inner_alias in sq.inner_alias:
                    inner_tp = plan_for(inner_alias)
                    for ispec in sq.inner_specs:
                        if isinstance(ispec, ColumnSpec):
                            if (
                                ispec.table in sq.inner_table
                                or ispec.table in sq.inner_alias
                            ) and ispec.column not in inner_tp.col_values:
                                try:
                                    real_table = self.table_alias.get(
                                        ispec.table, ispec.table
                                    )

                                    pool = self._get_pool(
                                        real_table,
                                        ispec.column,
                                        alias=f"{ispec.table}.{ispec.column}",
                                    )
                                    inner_tp.col_values[ispec.column] = (
                                        pool.generate_for_spec(ispec.op, ispec.value)
                                    )
                                except Exception:
                                    ...
                        if isinstance(ispec, DisjunctionSpec):
                            for branch in ispec.branches:
                                for ispec2 in branch:
                                    if (
                                        ispec2.table in sq.inner_table
                                        or ispec2.table in sq.inner_alias
                                    ) and ispec2.column not in inner_tp.col_values:
                                        try:
                                            real_table = self.table_alias.get(
                                                ispec2.table, ispec2.table
                                            )
                                            pool = self._get_pool(
                                                real_table,
                                                ispec2.column,
                                                alias=f"{ispec2.table}.{ispec2.column}",
                                            )
                                            inner_tp.col_values[ispec2.column] = (
                                                pool.generate_for_spec(
                                                    ispec2.op, ispec2.value
                                                )
                                            )
                                        except Exception:
                                            ...

                    if sq.outer_column:
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
                                reference = inner_pool.generate()
                                inner_tp.col_values[sq.inner_select_col.name] = (
                                    reference
                                )

                            outer_val = pool.generate_for_spec(sq.outer_op, reference)
                            outer_tp.col_values[sq.outer_column] = outer_val

            if sq.kind == "in":
                for inner_table in sq.inner_table:
                    inner_tp = plan_for(inner_table)
                    for ispec in sq.inner_specs:
                        if isinstance(ispec, ColumnSpec):
                            if (
                                ispec.table in sq.inner_table
                                or ispec.table in sq.inner_alias
                            ) and ispec.column not in inner_tp.col_values:
                                try:
                                    real_table = self.table_alias.get(
                                        ispec.table, ispec.table
                                    )
                                    pool = self._get_pool(
                                        real_table,
                                        ispec.column,
                                        alias=f"{ispec.table}.{ispec.column}",
                                    )
                                    inner_tp.col_values[ispec.column] = (
                                        pool.generate_for_spec(ispec.op, ispec.value)
                                    )
                                except Exception:
                                    ...
                        if isinstance(ispec, DisjunctionSpec):
                            for branch in ispec.branches:
                                for ispec2 in branch:
                                    if (
                                        ispec2.table in sq.inner_table
                                        or ispec2.table in sq.inner_alias
                                    ) and ispec2.column not in inner_tp.col_values:
                                        try:
                                            real_table = self.table_alias.get(
                                                ispec2.table, ispec2.table
                                            )
                                            pool = self._get_pool(
                                                real_table,
                                                ispec2.column,
                                                alias=f"{ispec2.table}.{ispec2.column}",
                                            )
                                            inner_tp.col_values[ispec2.column] = (
                                                pool.generate_for_spec(
                                                    ispec2.op, ispec2.value
                                                )
                                            )
                                        except Exception:
                                            ...
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
                                    sq.outer_col, inner_key_val
                                )

            if sq.kind == "exists":
                outer_tp = plan_for(sq.outer_table)
                for inner_table in sq.inner_table:
                    inner_tp = plan_for(inner_table)
                    inner_tp.min_rows = max(inner_tp.min_rows, outer_tp.min_rows)
                    for ispec in sq.inner_specs:
                        if isinstance(ispec, ColumnSpec):
                            if (
                                ispec.table in sq.inner_table
                                or ispec.table in sq.inner_alias
                            ) and ispec.column not in inner_tp.col_values:
                                try:
                                    real_table = self.table_alias.get(
                                        ispec.table, ispec.table
                                    )
                                    pool = self._get_pool(
                                        real_table,
                                        ispec.column,
                                        alias=f"{ispec.table}.{ispec.column}",
                                    )
                                    inner_tp.col_values[ispec.column] = (
                                        pool.generate_for_spec(ispec.op, ispec.value)
                                    )
                                except Exception:
                                    ...
                        if isinstance(ispec, DisjunctionSpec):
                            for branch in ispec.branches:
                                for ispec2 in branch:
                                    if (
                                        ispec2.table in sq.inner_table
                                        or ispec2.table in sq.inner_alias
                                    ) and ispec2.column not in inner_tp.col_values:
                                        try:
                                            real_table = self.table_alias.get(
                                                ispec2.table, ispec2.table
                                            )
                                            pool = self._get_pool(
                                                real_table,
                                                ispec2.column,
                                                alias=f"{ispec2.table}.{ispec2.column}",
                                            )
                                            inner_tp.col_values[ispec2.column] = (
                                                pool.generate_for_spec(
                                                    ispec2.op, ispec2.value
                                                )
                                            )
                                        except Exception:
                                            ...

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

        for tbl, tp in table_plans.items():
            real_table = self.table_alias.get(tbl, tbl)
            for _ in range(tp.min_rows):
                for col, val in tp.col_values.items():
                    concretes.setdefault(real_table, {}).setdefault(col, []).append(val)
        self.instance.create_rows(concretes=concretes)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _get_pool(self, table: str, column: str, alias: Optional[str] = None):
        return self.instance.column_domains.get_or_create_pool(
            table, column, alias=alias
        )

    def _get_row_value(self, row, column: str) -> Any:
        col_norm = self.instance._normalize_name(column, dialect=self.instance.dialect)
        col_data = row.columns.get(col_norm)
        if col_data is None:
            for k, v in row.columns.items():
                if k.lower() == col_norm.lower():
                    return v.concrete if hasattr(v, "concrete") else v
            return None
        return col_data.concrete if hasattr(col_data, "concrete") else col_data

    def _get_row_value(self, row, column: str) -> Any:
        col_norm = self.instance._normalize_name(column, dialect=self.dialect)
        col_data = row.columns.get(col_norm)
        if col_data is None:
            for k, v in row.columns.items():
                if k.lower() == col_norm.lower():
                    return v.concrete if hasattr(v, "concrete") else v
            return None
        return col_data.concrete if hasattr(col_data, "concrete") else col_data
