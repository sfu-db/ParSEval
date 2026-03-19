"""
with speculative data generator, we could handle more data types.
"""

from __future__ import annotations
from typing import Optional, List, Dict, TYPE_CHECKING, Tuple, Union, Set, Callable, Any
from parseval.data_generator import BaseGenerator
from parseval.plan.helper import decode_aggregate
from dataclasses import dataclass, field
from sqlglot import exp
from sqlglot.planner import Aggregate, Step, Scan, Plan, Join, SetOperation
import logging
from parseval.helper import to_concrete

if TYPE_CHECKING:
    from parseval.instance import Instance

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


@dataclass
class GroupSpec:
    table: str
    group_cols: List[str]
    min_rows_per_group: int = 1


@dataclass
class SubquerySpec:
    kind: str
    outer_table: str
    outer_column: str
    inner_table: str
    inner_alias: str
    inner_specs: List[Any]
    corr_col: Optional[str] = None


@dataclass
class SetOpSpec:
    kind: str
    left_table: str
    right_table: str
    left_cols: List[str]
    right_cols: List[str]


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
        subq = None
        if isinstance(right, exp.Subquery) and isinstance(left, exp.Column):
            subq, outer_col = right, left
        elif isinstance(left, exp.Subquery) and isinstance(right, exp.Column):
            subq, outer_col = left, right
        if subq is not None:
            sel = subq.find(exp.Select)
            if sel:
                sps = []
                for p in sel.find_all(exp.Predicate):
                    sps.extend(extract_condition_specs(p))
                return sps
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


def extract_subquery_specs(condition: exp.Expression, outer_table: str):
    if condition is None:
        return []
    results = []

    def _inner_table_alias(select: exp.Select):
        from_ = select.args.get("from_")
        if from_ is None:
            return None, None
        tbl = from_.this
        alias = tbl.alias_or_name
        return alias

    def _inner_specs(select: exp.Select, inner_alias: str) -> List[Any]:
        """Extract ColumnSpecs from the inner SELECT's WHERE, excluding correlation refs."""
        inner_where = select.args.get("where")
        if not inner_where:
            return []
        # Build a local alias_map that maps inner_alias to the real table
        local_map = {**alias_map, inner_alias: alias_map.get(inner_alias, inner_alias)}
        # Extract specs, but skip columns that reference the outer table (correlated refs)
        all_specs = extract_condition_specs(inner_where.this)
        # _extract_condition_specs(inner_where.this, local_map)
        # Filter: keep only specs whose table resolves to the inner table

        return [
            s
            for s in all_specs
            if isinstance(s, ColumnSpec)
            and (s.table == inner_real or s.table == inner_alias or not s.table)
        ]

    def _corr_col(select: exp.Select, inner_alias: str) -> Optional[str]:
        """
        Find the inner correlation column from a correlated subquery.
        e.g. WHERE o.emp_id = emp.id     → inner col = 'emp_id' (inner alias 'o')
             WHERE e2.dept_id = emp.dept_id → inner col = 'dept_id' (inner alias 'e2')

        Uses the alias exclusively to identify inner-side columns, so that
        self-joins (same real table, different alias) are handled correctly.
        """
        inner_where = select.args.get("where")
        if not inner_where:
            return None
        for cond in inner_where.find_all(exp.EQ):
            left, right = cond.left, cond.right
            for inner_side, outer_side in [(left, right), (right, left)]:
                if (
                    isinstance(inner_side, exp.Column)
                    and isinstance(outer_side, exp.Column)
                    and inner_side.table == inner_alias  # strict alias match
                    and outer_side.table != inner_alias
                ):  # outer uses different alias
                    return inner_side.name
        return None


def _min_rows_per_group(aggregations: List, having: exp.Expression, min_rows=3) -> int:
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


class SpeculativeGenerator(BaseGenerator):
    def __init__(self, expr, instance, generator_config=None):
        super().__init__(expr, instance, generator_config)
        self.column_specs: List[Union[ColumnSpec, DisjunctionSpec]] = []
        self.join_specs: List[JoinSpec] = []
        self.group_specs: List[GroupSpec] = []
        self.subquery_specs: List[SubquerySpec] = []
        self.set_op_specs: List[SetOpSpec] = []
        self.cte_map: Dict[str, str] = {}
        self._build_generator()

    def _build_generator(self):
        plan = Plan(self.expr)
        all_nodes: List[Step] = []
        stack = [plan.root]
        while stack:
            n = stack.pop()
            all_nodes.append(n)
            stack.extend(n.dependencies)
            if isinstance(n, Scan) and n.condition:
                specs = extract_condition_specs(n.condition)
                self.column_specs.extend(specs)
            if isinstance(n, Join):
                for _join_alias, jinfo in n.joins.items():
                    for sk, jk in zip(
                        jinfo.get("source_key", []), jinfo.get("join_key", [])
                    ):
                        self.join_specs.append(
                            JoinSpec(
                                left_table=sk.table or n.source_name,
                                left_col=sk.name,
                                right_table=jk.table or _join_alias,
                                right_col=jk.name,
                            )
                        )
                if n.condition:
                    specs = extract_condition_specs(n.condition)
                    self.column_specs.extend(specs)
            if isinstance(n, SetOperation):
                op_name = n.op.lower()

            if isinstance(n, Aggregate):
                group_cols, aggregations, having_cond = decode_aggregate(n)
                min_rows = _min_rows_per_group(aggregations, having_cond)
                self.group_specs.append(
                    GroupSpec(
                        table=n.source,
                        group_cols=group_cols,
                        min_rows_per_group=min_rows,
                    )
                )

    def generate(self, max_tries=5, stop_event: Optional[Callable] = None) -> None:

        for _ in range(max_tries):
            self._generate()

    def _generate(self):
        table_plans: Dict[str, TablePlan] = {}

        def plan_for(table: str) -> TablePlan:
            if table not in table_plans:
                table_plans[table] = TablePlan(min_rows=3)
            return table_plans[table]

        for spec in self.column_specs:
            if isinstance(spec, DisjunctionSpec):
                import random

                first_branch = random.choice(spec.branches) if spec.branches else []
                if not first_branch or not first_branch[0].table:
                    continue
                first = first_branch[0]
                first = random.choice(first_branch)
                try:
                    real_table = self.table_alias.get(first.table, first.table)
                    pool = self._get_pool(real_table, first.column)
                    tp = plan_for(first.table)
                    tp.col_values[first.column] = pool.generate_for_spec(
                        first.op, first.value
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
                    pool = self._get_pool(real_table, spec.column)
                    tp.col_values[spec.column] = pool.generate_for_spec(
                        spec.op, spec.value
                    )
                except ValueError as e:
                    logger.warning(f"Column constraint conflict: {e}")
        for gs in self.group_specs:
            if not gs.table:
                continue
            tp = plan_for(gs.table)
            tp.min_rows = max(tp.min_rows, gs.min_rows_per_group)
            for col in gs.group_cols:
                if col not in tp.col_values:
                    try:
                        pool = self._get_pool(gs.table, col)
                        tp.col_values[col] = pool.generate()
                    except Exception:
                        tp.col_values[col] = f"group_{col}_0"

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
    def _get_pool(self, table: str, column: str):
        return self.instance.column_domains.get_or_create_pool(table, column)

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
