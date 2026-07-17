from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, TYPE_CHECKING

from sqlglot import exp

from parseval.coercion import CoercionError, coerce_literal_value
from parseval.domain.exceptions import DomainError
from parseval.generator.schema_constraints import (
    SchemaConstraintLoweringError,
    batch_unique_constraints_for_solver_rows,
    literal_for_value as _literal_for_value,
    schema_constraints_for_solver_row,
)
from parseval.plan.context import DerivedSchema, Row
from parseval.plan.rex import Symbol
from parseval.solver.types import SolverVar, Problem
from parseval.solver.api import Solver
from parseval.plan.explain import (
    Aggregate,
    Filter,
    Join,
    Limit,
    Plan,
    Projection,
    ScalarSubqueryRef,
    Sort,
    Step,
    TableScan,
    Union,
    SubqueryAlias,
    Values,
    EmptyRelation,
    Unnest,
    Repartition,
    Distinct,
    Window,
)
from parseval.plan.rex import Environment, Variable, concrete
from parseval.solver.types import SolverVar
from parseval.generator.coverage import (
    CoverageTreeNode,
    SemanticTarget,
    _is_not_null_filter,
    _step_semantic_targets,
    sql_order_key
)
from parseval.generator.helper import same_identifier

if TYPE_CHECKING:
    from parseval.instance import Instance


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GroupDemand:
    group_index: int
    row_count: int
    group_key_values: Tuple[Tuple[exp.Expression, object], ...] = ()
    row_predicates: Tuple[exp.Expression, ...] = ()
    row_predicates_by_index: Tuple[Tuple[int, Tuple[exp.Expression, ...]], ...] = ()


@dataclass(frozen=True)
class ExpressionDemand:
    expression: exp.Expression
    kind: str
    value: object | None = None
    rank: int | None = None
    descending: bool = False
    origin: str = ""


@dataclass(frozen=True)
class SchemaDemand:
    count: int = 1
    predicates: Tuple[exp.Expression, ...] = ()
    order_keys: Tuple[exp.Expression, ...] = ()
    distinct: bool = False
    group_demands: Tuple[GroupDemand, ...] = ()
    expression_demands: Tuple[ExpressionDemand, ...] = ()


@dataclass(frozen=True)
class DemandContext:
    pipeline: "EncodePipeline"
    cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]]

    @property
    def bounds(self) -> object:
        return self.pipeline.bounds

    @property
    def dialect(self) -> str | None:
        return self.pipeline.dialect

    @property
    def instance(self) -> Instance | None:
        return self.pipeline.instance

    def lower(self, step: Step, demand: SchemaDemand) -> None:
        self.pipeline._lower_demand(step, demand, self.cache)

    def schema_for(self, step: Step) -> DerivedSchema:
        return _schema_for(self.cache, step)

    def single_dependency(self, step: Step) -> Step:
        return _single_dependency(step)

    def subquery_schemas_for(self, step: Step) -> Tuple[DerivedSchema, ...]:
        schemas: List[DerivedSchema] = []
        for subquery_root in self.pipeline._subquery_roots(step):
            schemas.append(_schema_for(self.cache, subquery_root))
        return tuple(schemas)


@dataclass
class RowAllocator:
    instance: Instance
    next_index_by_table: Dict[str, int] = field(default_factory=dict)

    def allocate(self, table: exp.Table) -> int:
        key = table.name.casefold()
        current_size = len(self.instance.get_rows(table))
        if key not in self.next_index_by_table:
            self.next_index_by_table[key] = current_size
        else:
            self.next_index_by_table[key] = max(
                self.next_index_by_table[key],
                current_size,
            )
        index = self.next_index_by_table[key]
        self.next_index_by_table[key] = index + 1
        return index

def _row_value_dict(row) -> Dict[exp.Identifier, Any]:
    """Extract concrete {col_ident: value} from a Row/Variable row."""
    d: Dict[exp.Identifier, Any] = {}
    for col_ident, val in row.column_values.items():
        if isinstance(val, Variable):
            d[col_ident] = val.concrete
        else:
            d[col_ident] = val
    return d


def _step_name(step: Step) -> str:
    return step.name.name if step.name else type(step).__name__


def _database_constraints_for_solver(
    instance: Instance,
    table: exp.Table,
    sv_map: Mapping[str, SolverVar],
    exact_columns: Set[str],
    *,
    constrain_exact_fks: bool = True,
) -> List[exp.Expression]:
    return schema_constraints_for_solver_row(
        instance,
        table,
        sv_map,
        exact_columns=exact_columns,
        constrain_exact_fks=constrain_exact_fks,
    )


def _not_null_constraints_for_columns(
    table_schema: Any,
    sv_map: Mapping[str, SolverVar],
    column_names: Set[str],
) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    for column, column_schema in table_schema.columns.items():
        if (
            column.name not in column_names
            or column.name not in sv_map
            or column_schema.nullable
        ):
            continue
        constraints.append(
            exp.Not(this=exp.Is(this=sv_map[column.name], expression=exp.Null()))
        )
    return constraints


def _row_value(row: Mapping[object, object], column: exp.Identifier) -> object:
    if hasattr(row, "column_values"):
        value = row[column]
        return value.concrete if isinstance(value, Variable) else value
    if column in row:
        value = row[column]
    else:
        value = row.get(column.name)
    return value.concrete if isinstance(value, Variable) else value


def _solver_var_for_column(
    table: exp.Table,
    column: exp.Identifier,
    row_index: int,
    dtype: Any,
) -> SolverVar:
    return SolverVar(
        key=f"gen.{table.name}.{column.name}.{row_index}",
        dtype=dtype,
        meta={"table": table.name, "column": column.name, "row_index": row_index},
    )


def _rewrite_columns_to_solver_vars(
    expression: exp.Expression,
    sv_map: Mapping[str, SolverVar],
) -> exp.Expression:
    if isinstance(expression, exp.Column) and isinstance(expression.this, exp.Identifier):
        replacement = sv_map.get(expression.this.name)
        if replacement is not None:
            return replacement
    rewritten = deepcopy(expression)
    for col in list(rewritten.find_all(exp.Column)):
        if isinstance(col.this, exp.Identifier) and col.this.name in sv_map:
            col.replace(sv_map[col.this.name])
    return rewritten


def _schema_constraints_for_solver_rows(
    instance: Instance,
    table: exp.Table,
    sv_rows: Sequence[Mapping[str, SolverVar]],
    exact_columns_by_row: Sequence[Set[str]],
) -> List[exp.Expression]:
    table_schema = instance.database_constraints(table)
    constraints: List[exp.Expression] = []
    required_non_null_by_row: List[Set[str]] = [set() for _ in sv_rows]
    for sv_map, exact_columns in zip(sv_rows, exact_columns_by_row):
        constraints.extend(
            schema_constraints_for_solver_row(
                instance,
                table,
                sv_map,
                exact_columns=exact_columns,
            )
        )

    for group in table_schema.uniqueness_groups():
        names = tuple(column.name for column in group)
        if any(not set(names) <= set(sv_map) for sv_map in sv_rows):
            continue
        for left_index, _left in enumerate(sv_rows):
            for right_index, _right in enumerate(sv_rows[left_index + 1 :], start=left_index + 1):
                required_non_null_by_row[left_index].update(names)
                required_non_null_by_row[right_index].update(names)
    constraints.extend(batch_unique_constraints_for_solver_rows(instance, table, sv_rows))
    for sv_map, column_names in zip(sv_rows, required_non_null_by_row):
        constraints.extend(
            _not_null_constraints_for_columns(
                table_schema,
                sv_map,
                column_names,
            )
        )
    return constraints


def _expression_demand_batch_constraints(
    expression_demands: Sequence[ExpressionDemand],
    sv_rows: Sequence[Mapping[str, SolverVar]],
    dialect: str | None = None,
) -> List[exp.Expression]:
    del dialect
    constraints: List[exp.Expression] = []
    distinct_by_origin: Dict[str, Dict[int, exp.Expression]] = {}
    equal_by_origin: Dict[Tuple[str, object | None], Dict[int, exp.Expression]] = {}
    order_by_origin: Dict[str, Dict[int, Tuple[exp.Expression, object | None]]] = {}
    for demand in expression_demands:
        if demand.kind not in {"distinct", "order", "equal"} or demand.rank is None:
            continue
        if demand.rank < 0 or demand.rank >= len(sv_rows):
            continue
        expression = demand.expression
        if isinstance(expression, exp.Alias):
            expression = expression.this
        if isinstance(expression, exp.Ordered):
            expression = expression.this
        if demand.kind == "order" and not isinstance(expression, exp.Column):
            continue
        expression = _rewrite_columns_to_solver_vars(expression, sv_rows[demand.rank])
        origin = demand.origin or _normalize_expression_key(demand.expression.sql())
        if demand.kind == "distinct":
            distinct_by_origin.setdefault(origin, {})[demand.rank] = expression
        elif demand.kind == "equal":
            equal_by_origin.setdefault((origin, demand.value), {})[demand.rank] = expression
        else:
            order_by_origin.setdefault(origin, {})[demand.rank] = (
                expression,
                demand.value,
            )
    for rank_to_expression in distinct_by_origin.values():
        ranked = sorted(rank_to_expression.items())
        for left_index, (left_rank, left_expression) in enumerate(ranked):
            for _right_rank, right_expression in ranked[left_index + 1 :]:
                constraints.append(exp.NEQ(this=left_expression, expression=right_expression))
    for rank_to_expression in equal_by_origin.values():
        ranked = sorted(rank_to_expression.items())
        for left_index, (_left_rank, left_expression) in enumerate(ranked):
            for _right_rank, right_expression in ranked[left_index + 1 :]:
                constraints.append(exp.EQ(this=left_expression, expression=right_expression))
    for rank_to_expression in order_by_origin.values():
        ranked = sorted(rank_to_expression.items())
        for left_index, (_left_rank, (left_expression, left_value)) in enumerate(ranked):
            for _right_rank, (right_expression, right_value) in ranked[left_index + 1 :]:
                if left_value == right_value:
                    constraints.append(exp.EQ(this=left_expression, expression=right_expression))
                elif _order_value_before(left_value, right_value):
                    constraints.append(exp.GT(this=left_expression, expression=right_expression))
                else:
                    constraints.append(exp.LT(this=left_expression, expression=right_expression))
    return constraints


def _order_value_before(left: object | None, right: object | None) -> bool:
    if left is None:
        return False
    if right is None:
        return True
    try:
        return left > right
    except TypeError:
        return str(left) > str(right)


def _solve_table_rows(
    instance: Instance,
    table: exp.Table,
    row_specs: Sequence[Mapping[object, object]],
    predicates: Sequence[Sequence[exp.Expression]],
    *,
    dialect: str | None,
    expression_demands: Sequence[ExpressionDemand] = (),
    timeout_ms: int = 2000,
) -> Optional[List[Dict[str, object]]]:
    _solve_table_rows.schema_failure_reason = ""
    table_node = instance.resolve_table(table)
    table_schema = instance.database_constraints(table_node)
    sv_rows: List[Dict[str, SolverVar]] = []
    exact_columns_by_row: List[Set[str]] = []
    constraints: List[exp.Expression] = []
    base_index = len(instance.get_rows(table_node))

    for offset, row in enumerate(row_specs):
        sv_map = {
            column.name: _solver_var_for_column(
                table_node,
                column,
                base_index + offset,
                column_schema.datatype,
            )
            for column, column_schema in table_schema.columns.items()
        }
        sv_rows.append(sv_map)
        exact_columns: Set[str] = set()
        for column in table_schema.columns:
            if column not in row and column.name not in row:
                continue
            exact_columns.add(column.name)
            value = _row_value(row, column)
            if value is None:
                constraints.append(
                    exp.Is(this=sv_map[column.name], expression=exp.Null())
                )
            else:
                constraints.append(
                    exp.EQ(
                        this=sv_map[column.name],
                        expression=_literal_for_value(value),
                    )
                )
        for predicate in predicates[offset]:
            for column in predicate.find_all(exp.Column):
                if isinstance(column.this, exp.Identifier):
                    exact_columns.add(column.this.name)
            constraints.append(_rewrite_columns_to_solver_vars(predicate, sv_map))
        exact_columns_by_row.append(exact_columns)

    try:
        constraints.extend(
            _schema_constraints_for_solver_rows(
                instance,
                table_node,
                sv_rows,
                exact_columns_by_row,
            )
        )
    except SchemaConstraintLoweringError as exc:
        _solve_table_rows.schema_failure_reason = exc.reason
        return None
    constraints.extend(
        _expression_demand_batch_constraints(
            expression_demands,
            sv_rows,
            dialect,
        )
    )
    result = Solver(dialect=dialect or instance.dialect, timeout_ms=timeout_ms).solve(
        Problem(constraints=constraints)
    )
    if not result.sat:
        return None

    solved_rows: List[Dict[str, object]] = []
    for row, sv_map in zip(row_specs, sv_rows):
        solved: Dict[str, object] = {}
        for column in table_schema.columns:
            if column in row or column.name in row:
                solved[column.name] = _row_value(row, column)
                continue
            sv = sv_map[column.name]
            if sv in result.assignments:
                solved[column.name] = result.assignments[sv]
        solved_rows.append(solved)
    return solved_rows


def _parent_row_satisfies(
    row: Mapping[object, object],
    target_column: exp.Identifier,
    value: object,
) -> bool:
    return _row_value(row, target_column) == value


def _rows_with_required_fk_parents(
    instance: Instance,
    table: exp.Table,
    rows: Sequence[Mapping[str, object]],
) -> Dict[exp.Table, List[Mapping[str, object]]]:
    table_node = instance.resolve_table(table)
    rows_by_table: Dict[exp.Table, List[Mapping[str, object]]] = {table_node: list(rows)}
    planned: Dict[exp.Table, List[Mapping[str, object]]] = {}
    table_schema = instance.database_constraints(table_node)

    for row in rows:
        for fk in table_schema.foreign_keys:
            if len(fk.source_columns) != 1 or len(fk.target_columns) != 1:
                continue
            source_column = fk.source_columns[0]
            target_column = fk.target_columns[0]
            value = row.get(source_column.name)
            if value is None:
                continue
            target_table = instance.resolve_table(fk.target_table)
            existing = instance.get_rows(target_table)
            if any(_parent_row_satisfies(parent, target_column, value) for parent in existing):
                continue
            target_planned = planned.setdefault(target_table, [])
            if any(_parent_row_satisfies(parent, target_column, value) for parent in target_planned):
                continue
            target_planned.append({target_column.name: value})

    for target_table, target_rows in planned.items():
        rows_by_table.setdefault(target_table, []).extend(target_rows)
    return rows_by_table


# ------------------------------------------------------------------
# Base
# ------------------------------------------------------------------

class EncodeStep:
    """Base class for a single-step concrete-enrichment operator.

    Each operator corresponds to one :class:`Step` in the plan DAG and
    implements :meth:`forward` to ensure the :class:`Instance` has rows
    that cover the operator's semantics (both passing and failing).
    """

    def __init__(self, step: Step, instance: Optional[Instance] = None) -> None:
        self.step = step
        self.instance = instance
        self.dialect = getattr(instance, "dialect", None)

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        raise NotImplementedError

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        dependencies = tuple(self.step.dependencies)
        if len(dependencies) == 1:
            context.lower(dependencies[0], demand)
            return
        table = getattr(output_schema, "_table", None)
        if table is not None:
            context.pipeline._materialize_table_demand(output_schema, table, demand)

    def semantic_targets(self, path: str) -> Tuple[SemanticTarget, ...]:
        return _step_semantic_targets(self.step, self.instance, path)

    @staticmethod
    def decompose_conjuncts(expr: exp.Expression) -> List[exp.Expression]:
        if isinstance(expr, exp.And):
            return (
                EncodeStep.decompose_conjuncts(expr.left)
                + EncodeStep.decompose_conjuncts(expr.right)
            )
        return [expr]

    @staticmethod
    def decompose_disjuncts(expr: exp.Expression) -> List[exp.Expression]:
        if isinstance(expr, exp.Or):
            return (
                EncodeStep.decompose_disjuncts(expr.left)
                + EncodeStep.decompose_disjuncts(expr.right)
            )
        return [expr]

    @staticmethod
    def referenced_columns(expr: exp.Expression) -> Set[exp.Identifier]:
        return {
            col.this
            for col in expr.find_all(exp.Column)
            if isinstance(col.this, exp.Identifier)
        }

    def _resolve_table(self, ds: DerivedSchema) -> exp.Table:
        if hasattr(ds, "_table"):
            return ds._table
        if ds.rows:
            for row in ds.rows:
                for part in row.rowid:
                    if isinstance(part, str) and not part.startswith("rowid_"):
                        return exp.to_table(part)
        raise ValueError("Cannot determine table resolution from DerivedSchema without _table")

# ------------------------------------------------------------------
# Scan
# ------------------------------------------------------------------

class ScanEncodeStep(EncodeStep):
    """Load concrete rows from :class:`Instance`."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        scan: TableScan = self.step
        table = scan.table
        col_keys: List[exp.Column] = [
            p for p in scan.scan_projections if isinstance(p, exp.Column)
        ]
        rows: List[Row] = []        
        for existing in self.instance.get_rows(table):
            selected = {}
            for col_key in col_keys:
                if isinstance(col_key.this, exp.Identifier):
                    val = existing[col_key.this]
                    selected[col_key] = val
            if selected:
                rows.append(Row(this=(table.name, existing.rowid), columns=selected))

        datatypes = {}
        nullables = {}
        uniqueness = {}
        for col_key in col_keys:
            datatypes[col_key] = self.instance.get_column_type(table, col_key)
            nullables[col_key] = self.instance.nullable(table, col_key)
            uniqueness[col_key] = self.instance.is_unique(table, col_key)

        ds = DerivedSchema(
            columns=tuple(col_keys),
            rows=rows,
            datatypes=datatypes,
            nullables=nullables,
            uniqueness=uniqueness,
        )
        ds._table = table
        return ds

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del child_schemas
        table = getattr(output_schema, "_table", None)
        if table is not None:
            context.pipeline._materialize_table_demand(output_schema, table, demand)


# ------------------------------------------------------------------
# Filter
# ------------------------------------------------------------------


_MISSING = object()


def _scalar_schema_value(schema: DerivedSchema) -> object:
    if not schema.rows:
        return _MISSING
    row = schema.rows[0]
    if not row.column_values:
        return _MISSING
    value = next(iter(row.column_values.values()))
    if isinstance(value, (Symbol, Variable)):
        value = value.concrete
    return value


def _scalar_schema_ready_for_predicate(
    schema: DerivedSchema,
    parent_expr: exp.Expression,
) -> bool:
    value = _scalar_schema_value(schema)
    if value is _MISSING:
        return False
    if value is not None:
        return True
    return not isinstance(parent_expr, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE))


def _literal_for_scalar_schema(schema: DerivedSchema) -> exp.Expression:
    value = _scalar_schema_value(schema)
    if value is _MISSING:
        return exp.Null()
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, (int, float)):
        return exp.Literal.number(str(value))
    return exp.Literal.string(str(value))


def _mark_scalar_schema_single(schema: DerivedSchema) -> DerivedSchema:
    schema.evidence["max_rows"] = 1
    for column in schema.columns:
        schema.uniqueness[column] = True
    return schema


def _schema_is_single(schema: DerivedSchema) -> bool:
    return schema.evidence.get("max_rows") == 1


def _scalar_ref_parent_expr(
    condition: exp.Expression,
    ref: ScalarSubqueryRef,
) -> exp.Expression:
    parent = ref.parent
    while parent is not None:
        if isinstance(parent, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
            return parent
        parent = parent.parent
    return condition


def _expression_with_scalar_subqueries(
    expression: exp.Expression,
    subquery_schemas: Sequence[DerivedSchema],
    *,
    require_ready: bool = False,
) -> exp.Expression:
    if not subquery_schemas:
        return expression
    rewritten = deepcopy(expression)
    for ref, schema in zip(list(rewritten.find_all(ScalarSubqueryRef)), subquery_schemas):
        parent_expr = _scalar_ref_parent_expr(rewritten, ref)
        if require_ready and not _scalar_schema_ready_for_predicate(schema, parent_expr):
            return expression
        ref.replace(_literal_for_scalar_schema(schema))
    return rewritten


class FilterEncodeStep(EncodeStep):
    """Evaluate a filter over the child DerivedSchema."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        fs: Filter = self.step
        if fs.condition is None:
            return child

        condition = self._condition_with_scalar_subqueries(fs.condition, children[1:])
        kept_rows: List[Row] = []
        for row in child.rows:
            env = Environment.from_row(row)
            if concrete(condition, env) is True:
                kept_rows.append(row)

        result = child.with_rows(kept_rows)
        result._table = getattr(child, '_table', None)
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema, child_schemas
        fs: Filter = self.step
        child = context.single_dependency(fs)
        if fs.condition is None:
            context.lower(child, demand)
            return
        if isinstance(child, Aggregate):
            context.pipeline._materialize_having_demand(fs, child, demand, context.cache)
            return
        if not context.pipeline._ensure_scalar_subquery_values(fs, context.cache):
            return
        subquery_schemas = context.subquery_schemas_for(fs)
        condition = self._condition_with_scalar_subqueries(
            fs.condition,
            subquery_schemas,
            require_ready=True,
        )
        if condition.find(ScalarSubqueryRef):
            return
        count = demand.count
        if any(_schema_is_single(schema) for schema in subquery_schemas) and count > 1:
            count = 1
        context.lower(
            child,
            SchemaDemand(
                count=count,
                predicates=demand.predicates + (condition,),
                order_keys=demand.order_keys,
                distinct=demand.distinct,
                group_demands=demand.group_demands,
                expression_demands=demand.expression_demands,
            ),
        )

    def _condition_with_scalar_subqueries(
        self,
        condition: exp.Expression,
        subquery_schemas: Sequence[DerivedSchema],
        *,
        require_ready: bool = False,
    ) -> exp.Expression:
        if not subquery_schemas:
            return condition
        return _expression_with_scalar_subqueries(
            condition,
            subquery_schemas,
            require_ready=require_ready,
        )

# ------------------------------------------------------------------
# Projection
# ------------------------------------------------------------------

class ProjectEncodeStep(EncodeStep):
    """Evaluate projection expressions over child DerivedSchema rows."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        proj_step: Projection = self.step

        projection_items = _projection_items_with_scalar_subqueries(
            proj_step.projections,
            children[1:],
            self.dialect,
        )

        new_rows: List[Row] = []
        for row in child.rows:
            new_columns = {}
            for output, expression in projection_items:
                new_columns[output] = _expr_value(expression, row)
            new_rows.append(Row(this=row.rowid, columns=new_columns))

        out_cols = tuple(output for output, _expression in projection_items)
        result = child.with_rows(new_rows, columns=out_cols)
        result.datatypes = {
            output: dtype
            for output, expression in projection_items
            if (dtype := _schema_column_metadata(child.datatypes, expression, self.dialect)) is not None
        }
        result.nullables = {
            output: nullable
            for output, expression in projection_items
            if (nullable := _schema_column_metadata(child.nullables, expression, self.dialect)) is not None
        }
        result.uniqueness = {
            output: unique
            for output, expression in projection_items
            if (unique := _schema_column_metadata(child.uniqueness, expression, self.dialect)) is not None
        }
        result._table = getattr(child, '_table', None)
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        child = context.single_dependency(self.step)
        context.lower(
            child,
            _rewrite_projection_demand(
                demand,
                self.step,
                output_schema,
                child_schemas[0],
                context.dialect,
            ),
        )


# ------------------------------------------------------------------
# Join
# ------------------------------------------------------------------

class JoinEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        join_step: Join = self.step
        child_map: Dict[Step, DerivedSchema] = {}
        for dep, ds in zip(join_step.dependencies, children):
            child_map[dep] = ds
        left_ds = child_map.get(join_step.left, children[0])
        right_ds = child_map.get(join_step.right, children[-1])
        result = self._build_join_output(left_ds, right_ds, join_step)
        result._table = getattr(left_ds, '_table', None)
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema, child_schemas
        context.pipeline._materialize_join_demand(self.step, demand, context.cache)

    @staticmethod
    def _build_join_output(
        left_ds: DerivedSchema,
        right_ds: DerivedSchema,
        join_step: Join,
    ) -> DerivedSchema:
        jt = join_step.join_type.upper()

        # SEMI / ANTI — emit only left rows, no right columns
        if jt in ("SEMI", "ANTI"):
            output_rows: List[Row] = []
            for lrow in left_ds.rows:
                ldict = _row_value_dict(lrow) if hasattr(lrow, 'column_values') else {}
                has_match = False
                for rrow in right_ds.rows:
                    rdict = _row_value_dict(rrow) if hasattr(rrow, 'column_values') else {}
                    merged = {**ldict, **rdict}
                    env = Environment(row=merged)
                    ok = True
                    for lexpr, rexpr in join_step.on_keys:
                        lv = concrete(lexpr, env)
                        rv = concrete(rexpr, env)
                        if lv is None or rv is None or lv != rv:
                            ok = False
                            break
                    if ok and join_step.condition is not None:
                        if concrete(join_step.condition, env) is not True:
                            ok = False
                    if ok:
                        has_match = True
                        if jt == "SEMI":
                            break
                if (jt == "SEMI" and has_match) or (jt == "ANTI" and not has_match):
                    output_rows.append(Row(
                        this=(_step_name(join_step), lrow.rowid),
                        columns={ident: ldict.get(ident, None)
                                 for ident in left_ds.columns},
                    ))

            return DerivedSchema(
                columns=left_ds.columns,
                rows=output_rows,
                datatypes=left_ds.datatypes,
                nullables=left_ds.nullables,
                uniqueness=left_ds.uniqueness,
            )

        # INNER / LEFT / RIGHT / FULL — emit combined columns
        out_cols = tuple(left_ds.columns) + tuple(right_ds.columns)

        output_rows = []

        for lrow in left_ds.rows:
            ldict = _row_value_dict(lrow) if hasattr(lrow, 'column_values') else {}
            for rrow in right_ds.rows:
                rdict = _row_value_dict(rrow) if hasattr(rrow, 'column_values') else {}
                merged = {**ldict, **rdict}
                env = Environment(row=merged)
                ok = True
                for lexpr, rexpr in join_step.on_keys:
                    lv = concrete(lexpr, env)
                    rv = concrete(rexpr, env)
                    if lv is None or rv is None or lv != rv:
                        ok = False
                        break
                if ok and join_step.condition is not None:
                    if concrete(join_step.condition, env) is not True:
                        ok = False
                if not ok:
                    continue
                out_row = Row(
                    this=(_step_name(join_step), lrow.rowid, rrow.rowid),
                    columns={ident: merged.get(ident, None)
                             for ident in out_cols},
                )
                output_rows.append(out_row)

        return DerivedSchema(
            columns=out_cols,
            rows=output_rows,
            datatypes={**left_ds.datatypes, **right_ds.datatypes},
            nullables={**left_ds.nullables, **right_ds.nullables},
            uniqueness={**left_ds.uniqueness, **right_ds.uniqueness},
        )

# ------------------------------------------------------------------
# Stub operators (passthrough)
# ------------------------------------------------------------------

class SubqueryAliasEncodeStep(EncodeStep):
    """Remap Column keys from child's table name to the alias."""

    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        alias_step: SubqueryAlias = self.step
        alias_name = alias_step.alias.name if alias_step.alias else ""

        if not alias_name:
            return child

        new_rows: List[Row] = []
        for row in child.rows:
            new_columns = {}
            for key, val in row.column_values.items():
                if isinstance(key, exp.Column) and key.table:
                    new_key = exp.Column(
                        this=exp.Identifier(this=key.this.name if key.this else ""),
                        table=exp.Identifier(this=alias_name, quoted=_quoted(key.table)),
                    )
                    new_columns[new_key] = val
                else:
                    col_name = key.name if isinstance(key, exp.Identifier) else str(key)
                    new_key = exp.Column(
                        this=exp.Identifier(this=col_name, quoted=getattr(key, 'quoted', False)),
                        table=exp.Identifier(this=alias_name),
                    )
                    new_columns[new_key] = val
            new_rows.append(Row(this=row.rowid, columns=new_columns))

        new_cols = tuple(
            exp.Column(
                this=exp.Identifier(this=c.name if isinstance(c, exp.Identifier) else (c.this.name if isinstance(c, exp.Column) and c.this else "")),
                table=exp.Identifier(this=alias_name),
            )
            for c in child.columns
        )

        result = child.with_rows(new_rows, columns=new_cols)
        column_pairs = tuple(zip(child.columns, new_cols))
        result.datatypes = _remap_column_metadata(child.datatypes, column_pairs)
        result.nullables = _remap_column_metadata(child.nullables, column_pairs)
        result.uniqueness = _remap_column_metadata(child.uniqueness, column_pairs)
        result._table = getattr(child, '_table', None)
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        child = context.single_dependency(self.step)
        context.lower(
            child,
            _rewrite_demand_alias(
                demand,
                self.step.alias.name if self.step.alias else "",
                output_schema,
                child_schemas[0],
                context.dialect,
            ),
        )


class AggregateEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        step: Aggregate = self.step
        group_exprs = tuple(step.group or ())
        aggregations = tuple(step.aggregations or ())

        grouped: Dict[Tuple[Any, ...], List[Row]] = {}
        for row in child.rows:
            key = tuple(_expr_value(expr, row) for expr in group_exprs)
            grouped.setdefault(key, []).append(row)
        if not grouped and not group_exprs:
            grouped[()] = []

        out_cols: List[Any] = []
        out_cols.extend(group_exprs)
        aggregate_keys = _aggregate_output_keys(aggregations, self.dialect)
        duplicate_keys = _duplicate_aggregate_key_names(aggregate_keys)
        if duplicate_keys:
            raise ValueError(
                "Duplicate aggregate output key(s): " + ", ".join(duplicate_keys)
            )
        out_cols.extend(aggregate_keys)
        out_rows: List[Row] = []

        for group_index, (group_key, rows) in enumerate(grouped.items()):
            values: Dict[Any, Any] = {}
            for expr, value in zip(group_exprs, group_key):
                values[expr] = value
            for aggregate, key in zip(aggregations, aggregate_keys):
                values[key] = _aggregate_value(aggregate, rows)
            rowids = tuple(row.rowid for row in rows)
            out_rows.append(
                Row(
                    this=(_step_name(step), str(group_index), *rowids),
                    columns=values,
                )
            )

        result = child.with_rows(out_rows, columns=tuple(out_cols))
        result.uniqueness.update({group_expr: True for group_expr in group_exprs})
        result.obligations.append(
            {"kind": "aggregate", "target": "groups", "count": len(out_rows)}
        )
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema
        node: Aggregate = self.step
        child = context.single_dependency(node)
        child_schema = child_schemas[0]
        expression_demands = _rewrite_aggregate_expression_demands(
            demand.expression_demands,
            node,
            context.dialect,
        )
        group_demands = demand.group_demands
        group_demands = group_demands + _aggregate_expression_group_demands(
            node,
            expression_demands,
            rows_per_group=max(
                int(getattr(context.bounds, "rows_per_group", 1) or 1),
                1,
            ),
            dialect=context.dialect,
        )
        if node.group and not group_demands:
            group_demands = _aggregate_group_demands(
                node,
                group_count=max(
                    demand.count,
                    int(getattr(context.bounds, "groups", 1) or 1),
                ),
                rows_per_group=max(
                    int(getattr(context.bounds, "rows_per_group", 1) or 1),
                    1,
                ),
                dialect=context.dialect,
            )
        if node.group and group_demands:
            group_demands = _ensure_aggregate_group_key_values(
                node,
                group_demands,
                context.dialect,
            )
            expression_demands = expression_demands + _aggregate_group_key_expression_demands(
                node,
                group_demands,
                context.dialect,
            )
        rows_per_group = max(
            int(getattr(context.bounds, "rows_per_group", 1) or 1),
            1,
        )
        group_demands, stress_expression_demands = _aggregate_argument_stress_demands(
            node,
            child_schema,
            group_demands,
            rows_per_group=rows_per_group,
            dialect=context.dialect,
        )
        expression_demands = expression_demands + stress_expression_demands
        child_group_demands = group_demands
        child_predicates = demand.predicates
        child_order_keys = tuple(node.group or ()) + tuple(
            key
            for key in demand.order_keys
            if _expression_uses_only_schema_columns(
                key.this if isinstance(key, exp.Ordered) else key,
                child_schema,
                context.dialect,
            )
        )
        context.lower(
            child,
            SchemaDemand(
                count=max(demand.count, sum(group.row_count for group in group_demands)),
                predicates=child_predicates,
                order_keys=child_order_keys,
                distinct=demand.distinct,
                group_demands=child_group_demands,
                expression_demands=tuple(
                    expression_demand
                    for expression_demand in expression_demands
                    if not _expression_contains_aggregate(expression_demand.expression)
                ),
            ),
        )


class SortEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        step: Sort = self.step

        rows = list(child.rows)
        for key in reversed(step.key or []):
            expr = key.this if isinstance(key, exp.Ordered) else key
            desc = isinstance(key, exp.Ordered) and bool(key.args.get("desc"))
            rows.sort(
                key=lambda row: sql_order_key(_expr_value(expr, row)),
                reverse=desc,
            )
        if step.fetch is not None:
            rows = rows[: step.fetch]
        result = child.with_rows(rows)
        result.obligations.append(
            {"kind": "sort", "target": "ordered", "count": len(rows)}
        )
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema
        child = context.single_dependency(self.step)
        required_count = _sort_required_child_count(
            self.step,
            demand,
            child_schemas[0],
            int(getattr(context.bounds, "order_competitors", 0) or 0),
        )
        if required_count <= 0:
            return
        context.lower(
            child,
            SchemaDemand(
                count=required_count,
                predicates=demand.predicates,
                order_keys=tuple(self.step.key or ()) + demand.order_keys,
                distinct=demand.distinct,
                group_demands=demand.group_demands,
                expression_demands=demand.expression_demands
                + _order_expression_demands(
                    self.step.key or (),
                    required_count,
                    context.dialect,
                ),
            ),
        )


class LimitEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        step: Limit = self.step
        offset = step.offset or 0
        stop = None if step.fetch is None else offset + step.fetch
        rows = list(child.rows)[offset:stop]
        result = child.with_rows(rows)
        result.obligations.append(
            {
                "kind": "limit",
                "target": "window",
                "offset": offset,
                "fetch": step.fetch,
                "count": len(rows),
            }
        )
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema, child_schemas
        offset = self.step.offset or 0
        fetch = self.step.fetch or demand.count
        context.lower(
            context.single_dependency(self.step),
            SchemaDemand(
                count=max(demand.count, offset + fetch),
                predicates=demand.predicates,
                order_keys=demand.order_keys,
                distinct=demand.distinct,
                group_demands=demand.group_demands,
                expression_demands=demand.expression_demands,
            ),
        )


class DistinctEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        seen: Set[Tuple[Any, ...]] = set()
        rows: List[Row] = []
        for row in child.rows:
            key = tuple(_cell_value(row[column]) for column in child.columns)
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
        result = child.with_rows(rows)
        result.obligations.append(
            {"kind": "distinct", "target": "duplicate_eliminated", "count": len(rows)}
        )
        return result

    def lower_demand(
        self,
        demand: SchemaDemand,
        output_schema: DerivedSchema,
        child_schemas: Sequence[DerivedSchema],
        context: DemandContext,
    ) -> None:
        del output_schema, child_schemas
        context.lower(
            context.single_dependency(self.step),
            SchemaDemand(
                count=demand.count,
                predicates=demand.predicates,
                order_keys=demand.order_keys,
                distinct=True,
                group_demands=demand.group_demands,
                expression_demands=demand.expression_demands,
            ),
        )


class UnionEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        step: Union = self.step
        columns = _union_columns(children)
        rows: List[Row] = []
        seen: Set[Tuple[Any, ...]] = set()
        for child in children:
            for row in child.rows:
                values = _row_values_by_position(row)
                if len(values) != len(columns):
                    raise ValueError(
                        f"Union row width does not match output columns: {len(values)} != {len(columns)}"
                    )
                key = tuple(_cell_value(value) for value in values)
                if not step.is_all and key in seen:
                    continue
                seen.add(key)
                rows.append(_row_with_columns(row, columns, values))
        result = (children[0] if children else DerivedSchema(columns=columns)).with_rows(
            rows,
            columns=columns,
        )
        result.obligations.append({"kind": "union", "target": "combined", "count": len(rows)})
        return result


def _union_columns(children: Tuple[DerivedSchema, ...]) -> Tuple[Any, ...]:
    for child in children:
        if child.columns:
            return child.columns
    for child in children:
        for row in child.rows:
            return row.columns
    return ()


def _row_values_by_position(row: Row) -> Tuple[Any, ...]:
    return tuple(row.values())


def _row_with_columns(row: Row, columns: Tuple[Any, ...], values: Tuple[Any, ...]) -> Row:
    if row.columns == columns:
        return row
    return Row(this=row.rowid, columns=dict(zip(columns, values)))


class ValuesEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        step: Values = self.step
        width = max((len(row) for row in step.values), default=0)
        columns = tuple(exp.to_identifier(f"column{i + 1}") for i in range(width))
        rows = [
            Row(
                this=(_step_name(step), str(index)),
                columns={
                    columns[col_index]: concrete(value, Environment())
                    for col_index, value in enumerate(values)
                },
            )
            for index, values in enumerate(step.values)
        ]
        return DerivedSchema(columns=columns, rows=rows)


class EmptyRelationEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        step: EmptyRelation = self.step
        rows = [Row(this=(_step_name(step), "0"), columns={})] if step.produce_one_row else []
        return DerivedSchema(columns=(), rows=rows)


class RepartitionEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        child.obligations.append(
            {
                "kind": "repartition",
                "target": "preserved",
                "scheme": self.step.partitioning_scheme,
            }
        )
        return child


class WindowEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        child = children[0]
        child.obligations.append(
            {
                "kind": "window",
                "target": "passthrough",
                "count": len(self.step.window_exprs),
            }
        )
        return child


class UnnestEncodeStep(EncodeStep):
    def forward(self, *children: DerivedSchema) -> DerivedSchema:
        columns = tuple(self.step.columns)
        return DerivedSchema(
            columns=columns,
            rows=[],
            obligations=[
                {
                    "kind": "unnest",
                    "target": "unsupported",
                    "status": "unknown",
                }
            ],
        )


def _quoted(value: Any) -> bool:
    return bool(getattr(value, "quoted", False))


def _row_mapping(row: Row) -> Dict[Any, Any]:
    return {key: _cell_value(value) for key, value in row.column_values.items()}


def _cell_value(value: Any) -> Any:
    return value.concrete if isinstance(value, Symbol) else value


def _expr_value(expr: exp.Expression, row: Row) -> Any:
    try:
        return concrete(expr, Environment.from_row(_row_mapping(row)))
    except Exception:
        try:
            return row[expr]
        except KeyError:
            return None


def _projection_output_item(
    projection: exp.Expression,
    dialect: str | None,
) -> Tuple[exp.Expression, exp.Expression]:
    expression = projection.this if isinstance(projection, exp.Alias) else projection
    if isinstance(projection, exp.Alias):
        output = exp.Column(
            this=exp.Identifier(this=projection.alias),
        )
    elif isinstance(expression, exp.Column):
        output = expression.copy()
    else:
        output = exp.Column(
            this=exp.Identifier(
                this=_projection_expression_name(expression, dialect),
                quoted=True,
            )
        )
    return output, expression


def _projection_items_with_scalar_subqueries(
    projections: Sequence[exp.Expression],
    subquery_schemas: Sequence[DerivedSchema],
    dialect: str | None,
) -> Tuple[Tuple[exp.Expression, exp.Expression], ...]:
    items: List[Tuple[exp.Expression, exp.Expression]] = []
    offset = 0
    for projection in projections:
        output, expression = _projection_output_item(projection, dialect)
        subquery_count = len(list(expression.find_all(ScalarSubqueryRef)))
        items.append(
            (
                output,
                _projection_expression_with_scalar_subqueries(
                    expression,
                    subquery_schemas[offset : offset + subquery_count],
                ),
            )
        )
        offset += subquery_count
    return tuple(items)


def _projection_expression_name(
    expression: exp.Expression,
    dialect: str | None,
) -> str:
    if expression.alias_or_name:
        return str(expression.alias_or_name)
    return expression.sql(dialect=dialect)


def _projection_expression_with_scalar_subqueries(
    expression: exp.Expression,
    subquery_schemas: Sequence[DerivedSchema],
) -> exp.Expression:
    if not subquery_schemas:
        return expression
    rewritten = deepcopy(expression)
    for ref, schema in zip(list(rewritten.find_all(ScalarSubqueryRef)), subquery_schemas):
        ref.replace(_literal_for_scalar_schema(schema))
    return rewritten


def _aggregate_key(
    aggregate: exp.Expression,
    dialect: str | None,
) -> exp.Column:
    return exp.Column(
        this=exp.Identifier(
            this=_aggregate_name(aggregate, dialect),
            quoted=True,
        )
    )


def _aggregate_output_keys(
    aggregations: Sequence[exp.Expression],
    dialect: str | None,
) -> Tuple[exp.Column, ...]:
    compatible_keys = tuple(
        _aggregate_output_key(aggregate, dialect) for aggregate in aggregations
    )
    counts: Dict[str, int] = {}
    for key in compatible_keys:
        name = key.name.casefold()
        counts[name] = counts.get(name, 0) + 1
    return tuple(
        _aggregate_key(aggregate, dialect)
        if counts[compatible_key.name.casefold()] > 1
        else compatible_key
        for aggregate, compatible_key in zip(aggregations, compatible_keys)
    )


def _aggregate_output_key(
    aggregate: exp.Expression,
    dialect: str | None,
) -> exp.Column:
    return exp.Column(
        this=exp.Identifier(
            this=_aggregate_output_name(aggregate, dialect),
            quoted=True,
        )
    )


def _aggregate_output_name(
    aggregate: exp.Expression,
    dialect: str | None,
) -> str:
    expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
    if isinstance(expression, exp.Count):
        source = expression.this
        if source is None or isinstance(source, exp.Star):
            return "count(Int64(1))"
        return f"count({_aggregate_arg_name(source, dialect)})"
    if isinstance(expression, exp.Avg):
        return f"avg({_aggregate_arg_name(expression.this, dialect)})"
    if isinstance(expression, exp.Sum):
        return f"sum({_aggregate_arg_name(expression.this, dialect)})"
    if isinstance(expression, exp.Min):
        return f"min({_aggregate_arg_name(expression.this, dialect)})"
    if isinstance(expression, exp.Max):
        return f"max({_aggregate_arg_name(expression.this, dialect)})"
    return expression.sql(dialect=dialect)


def _aggregate_name(
    aggregate: exp.Expression,
    dialect: str | None,
) -> str:
    expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
    if isinstance(expression, (exp.Count, exp.Avg, exp.Sum, exp.Min, exp.Max)):
        source = expression.this
        function_name = expression.key.lower()
        if source is None or isinstance(source, exp.Star):
            return f"{function_name}(*)"
        if isinstance(source, exp.Distinct):
            source_sql = ", ".join(
                item.sql(dialect=dialect) for item in source.expressions
            )
            return f"{function_name}(DISTINCT {source_sql})"
        if expression.args.get("distinct"):
            return f"{function_name}(DISTINCT {source.sql(dialect=dialect)})"
        return f"{function_name}({source.sql(dialect=dialect)})"
    return expression.sql(dialect=dialect)


def _aggregate_arg_name(
    expr: exp.Expression | None,
    dialect: str | None,
) -> str:
    if expr is None:
        return ""
    while isinstance(expr, exp.Cast):
        expr = expr.this
    if isinstance(expr, exp.Distinct):
        return ", ".join(_aggregate_arg_name(item, dialect) for item in expr.expressions)
    return expr.sql(dialect=dialect)


def _duplicate_aggregate_key_names(keys: Sequence[exp.Column]) -> Tuple[str, ...]:
    seen: Set[str] = set()
    duplicates: List[str] = []
    for key in keys:
        name = key.name.casefold()
        if name in seen and key.name not in duplicates:
            duplicates.append(key.name)
        seen.add(name)
    return tuple(duplicates)


def _aggregate_value(aggregate: exp.Expression, rows: List[Row]) -> Any:
    expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
    if isinstance(expression, exp.Count):
        source = expression.this
        if source is None or isinstance(source, exp.Star):
            return len(rows)
        values = _aggregate_inputs(source, rows)
        if isinstance(source, exp.Distinct) or expression.args.get("distinct"):
            return len({value for value in values if value is not None})
        return sum(1 for value in values if value is not None)
    if isinstance(expression, exp.Avg):
        values = [value for value in _aggregate_inputs(expression.this, rows) if value is not None]
        return None if not values else sum(values) / len(values)
    if isinstance(expression, exp.Sum):
        values = [value for value in _aggregate_inputs(expression.this, rows) if value is not None]
        return None if not values else sum(values)
    if isinstance(expression, exp.Min):
        values = [value for value in _aggregate_inputs(expression.this, rows) if value is not None]
        return None if not values else min(values)
    if isinstance(expression, exp.Max):
        values = [value for value in _aggregate_inputs(expression.this, rows) if value is not None]
        return None if not values else max(values)
    return None


def _aggregate_inputs(expr: exp.Expression | None, rows: List[Row]) -> List[Any]:
    if isinstance(expr, exp.Distinct):
        if len(expr.expressions) != 1:
            return []
        expr = expr.expressions[0]
    if expr is None:
        return []
    return [_expr_value(expr, row) for row in rows]


def _aggregate_expression_map(
    aggregate: Aggregate,
    dialect: str | None = None,
) -> Dict[str, exp.Expression]:
    mapping: Dict[str, exp.Expression] = {}
    output_keys = _aggregate_output_keys(tuple(aggregate.aggregations or ()), dialect)
    for expression, key in zip(aggregate.aggregations or (), output_keys):
        unaliased = expression.this if isinstance(expression, exp.Alias) else expression
        mapping[key.name.casefold()] = unaliased
        mapping[_expression_key(key, dialect)] = unaliased
        canonical_key = _aggregate_key(expression, dialect)
        mapping[canonical_key.name.casefold()] = unaliased
        mapping[_expression_key(canonical_key, dialect)] = unaliased
        mapping[_expression_key(unaliased, dialect)] = unaliased
        if expression.alias_or_name:
            mapping[str(expression.alias_or_name).casefold()] = unaliased
    return mapping


def _resolve_aggregate_expression(
    expression: exp.Expression,
    aggregates: Mapping[str, exp.Expression],
    dialect: str | None = None,
) -> exp.Expression | None:
    while isinstance(expression, exp.Cast):
        expression = expression.this
    if isinstance(expression, exp.Column):
        return (
            aggregates.get(expression.name.casefold())
            or aggregates.get(_expression_key(expression, dialect))
        )
    if isinstance(expression, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
        return expression
    return None


def _rewrite_aggregate_expression_demands(
    expression_demands: Sequence[ExpressionDemand],
    aggregate: Aggregate,
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    aggregates = _aggregate_expression_map(aggregate, dialect)

    def replace(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column):
            replacement = _resolve_aggregate_expression(node, aggregates, dialect)
            if replacement is not None:
                return deepcopy(replacement)
        return node

    return tuple(
        ExpressionDemand(
            expression=deepcopy(expression_demand.expression).transform(replace),
            kind=expression_demand.kind,
            value=expression_demand.value,
            rank=expression_demand.rank,
            descending=expression_demand.descending,
            origin=expression_demand.origin,
        )
        for expression_demand in expression_demands
    )


def _group_key_values(
    aggregate: Aggregate,
    group_index: int,
    dialect: str | None = None,
) -> Tuple[Tuple[exp.Expression, object], ...]:
    del dialect
    values: List[Tuple[exp.Expression, object]] = []
    for expr_index, group_expr in enumerate(aggregate.group or ()):
        value = _group_key_value_for_index(group_expr, group_index + expr_index)
        if value is not None:
            values.append((deepcopy(group_expr), value))
    return tuple(values)


def _group_key_value_for_index(expression: exp.Expression, index: int) -> object:
    if isinstance(expression, exp.Alias):
        expression = expression.this
    if isinstance(expression, exp.Case):
        values = _distinct_target_values(expression, index + 1)
        if values:
            return values[index % len(values)]
    if isinstance(expression, exp.Column):
        return index + 1
    return None


def _group_key_predicates_for_table(
    instance: Instance,
    table: exp.Table,
    group: GroupDemand,
) -> Tuple[exp.Expression, ...]:
    predicates: List[exp.Expression] = []
    for key, value in group.group_key_values:
        if (
            isinstance(key, exp.Column)
            and key.name.casefold()
            in {name.casefold() for name in instance.column_names(table)}
        ):
            try:
                value = _coerce_value_for_column(instance, table, key.name, value)
            except CoercionError:
                pass
        predicates.extend(
            _expression_demand_predicates(
                ExpressionDemand(
                    expression=key,
                    kind="group",
                    value=value,
                    rank=group.group_index,
                    origin="group_key",
                )
            )
        )
    return tuple(predicates)


def _columns_including_self(expression: exp.Expression) -> Tuple[exp.Column, ...]:
    if isinstance(expression, exp.Column):
        return (expression,)
    return tuple(expression.find_all(exp.Column))


def _predicate_equality_value(
    predicates: Sequence[exp.Expression],
    expression: exp.Expression,
    dialect: str | None = None,
) -> object:
    expression_key = _expression_key(expression, dialect)
    for predicate in predicates:
        for atom in _conjuncts(predicate):
            if not isinstance(atom, exp.EQ):
                continue
            if (
                isinstance(atom.expression, exp.Literal)
                and _expression_key(atom.this, dialect) == expression_key
            ):
                return _literal_value(atom.expression)
            if (
                isinstance(atom.this, exp.Literal)
                and _expression_key(atom.expression, dialect) == expression_key
            ):
                return _literal_value(atom.this)
    return _MISSING


def _aggregate_arg_expression(expression: exp.Expression) -> exp.Expression | None:
    arg = expression.this if isinstance(expression, (exp.Sum, exp.Avg, exp.Min, exp.Max, exp.Count)) else None
    while isinstance(arg, exp.Cast):
        arg = arg.this
    if isinstance(arg, exp.Distinct) and len(arg.expressions) == 1:
        arg = arg.expressions[0]
    if isinstance(arg, exp.Star):
        return None
    return arg


def _aggregate_argument_columns(
    aggregate: Aggregate,
    child_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[Tuple[exp.Expression, exp.Column], ...]:
    columns: List[Tuple[exp.Expression, exp.Column]] = []
    seen: Set[str] = set()
    for item in aggregate.aggregations or ():
        expression = item.this if isinstance(item, exp.Alias) else item
        if not isinstance(expression, (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)):
            continue
        arg = _aggregate_arg_expression(expression)
        while isinstance(arg, exp.Cast):
            arg = arg.this
        if not isinstance(arg, exp.Column):
            continue
        visible = _visible_schema_column(child_schema, arg, dialect)
        if visible is None:
            continue
        key = _expression_key(visible, dialect)
        if key in seen:
            continue
        seen.add(key)
        columns.append((expression, visible))
    if not aggregate.aggregations:
        for expression in aggregate.group or ():
            arg = expression.this if isinstance(expression, exp.Alias) else expression
            while isinstance(arg, exp.Cast):
                arg = arg.this
            if not isinstance(arg, exp.Column):
                continue
            visible = _visible_schema_column(child_schema, arg, dialect)
            if visible is None:
                continue
            key = _expression_key(visible, dialect)
            if key in seen:
                continue
            seen.add(key)
            columns.append((expression, visible))
    return tuple(columns)


def _aggregate_argument_stress_demands(
    aggregate: Aggregate,
    child_schema: DerivedSchema,
    group_demands: Sequence[GroupDemand],
    *,
    rows_per_group: int,
    dialect: str | None = None,
) -> Tuple[Tuple[GroupDemand, ...], Tuple[ExpressionDemand, ...]]:
    arguments = _aggregate_argument_columns(aggregate, child_schema, dialect)
    if not arguments:
        return tuple(group_demands), ()
    demands = list(group_demands) or (
        [
            GroupDemand(
                group_index=0,
                row_count=max(rows_per_group, 1),
                group_key_values=_group_key_values(aggregate, 0, dialect),
            )
        ]
    )
    expression_demands: List[ExpressionDemand] = []
    stressed: List[GroupDemand] = []
    base_rank = 0
    for group in demands:
        row_predicates_by_index = {
            index: tuple(predicates)
            for index, predicates in group.row_predicates_by_index
        }
        row_count = group.row_count
        for aggregate_expression, argument in arguments:
            nullable = child_schema.nullable(argument)
            unique = child_schema.is_unique(argument)
            mandatory = group.row_predicates
            is_group_key_argument = any(
                _expression_key(key, dialect) == _expression_key(argument, dialect)
                or any(
                    _expression_key(column, dialect) == _expression_key(argument, dialect)
                    for column in _columns_including_self(key)
                )
                for key, _value in group.group_key_values
            )
            allow_null = (
                nullable
                and not is_group_key_argument
                and not _predicate_contradicts_null_stress(
                    mandatory,
                    argument,
                    dialect,
                )
            )
            allow_duplicate = (
                not unique
                and _aggregate_allows_duplicate_stress(
                    aggregate_expression,
                    group,
                )
            )
            if not allow_null and not allow_duplicate:
                continue
            required_null_rows = int(allow_null)
            required_duplicate_rows = 2 if allow_duplicate else 0
            required_stress_rows = required_null_rows + required_duplicate_rows
            row_count = max(row_count, required_stress_rows)
            null_index = None
            if allow_null:
                null_index = next(
                    (
                        index
                        for index in range(row_count)
                        if not _predicate_contradicts_null_stress(
                            row_predicates_by_index.get(index, ()),
                            argument,
                            dialect,
                        )
                    ),
                    None,
                )
                if null_index is not None:
                    row_predicates_by_index[null_index] = row_predicates_by_index.get(null_index, ()) + (
                        exp.Is(this=deepcopy(argument), expression=exp.Null()),
                    )
            if allow_duplicate:
                duplicate_indexes = _duplicate_stress_indexes(
                    row_predicates_by_index,
                    row_count,
                    argument,
                    null_index,
                    dialect,
                )
                if duplicate_indexes is None:
                    continue
                left_index, right_index = duplicate_indexes
                row_predicates_by_index[left_index] = row_predicates_by_index.get(left_index, ()) + (
                    _not_null_predicate(argument),
                )
                row_predicates_by_index[right_index] = row_predicates_by_index.get(right_index, ()) + (
                    _not_null_predicate(argument),
                )
                origin = f"aggregate_argument_duplicate:{argument.sql(dialect=dialect)}:{group.group_index}"
                for row_index in (left_index, right_index):
                    expression_demands.append(
                        ExpressionDemand(
                            expression=deepcopy(argument),
                            kind="equal",
                            value=origin,
                            rank=base_rank + row_index,
                            origin=origin,
                        )
                    )
        stressed.append(
            GroupDemand(
                group_index=group.group_index,
                row_count=row_count,
                group_key_values=group.group_key_values,
                row_predicates=group.row_predicates,
                row_predicates_by_index=tuple(
                    sorted(row_predicates_by_index.items(), key=lambda item: item[0])
                ),
            )
        )
        base_rank += row_count
    return tuple(stressed), tuple(expression_demands)


def _aggregate_allows_duplicate_stress(
    aggregate_expression: exp.Expression,
    group: GroupDemand,
) -> bool:
    if isinstance(aggregate_expression, (exp.Min, exp.Max)):
        return True
    if isinstance(aggregate_expression, exp.Count):
        if isinstance(aggregate_expression.this, exp.Distinct) or aggregate_expression.args.get("distinct"):
            return True
        return not group.row_predicates
    if isinstance(aggregate_expression, (exp.Sum, exp.Avg)):
        return not group.row_predicates
    return True


def _predicate_contradicts_null_stress(
    predicates: Sequence[exp.Expression],
    argument: exp.Expression,
    dialect: str | None = None,
) -> bool:
    argument_key = _expression_key(argument, dialect)
    for predicate in predicates:
        for atom in _conjuncts(predicate):
            if _is_not_null_atom_for_argument(atom, argument_key, dialect):
                return True
            if isinstance(atom, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
                if _expression_key(atom.this, dialect) == argument_key and isinstance(atom.expression, exp.Literal):
                    return True
                if _expression_key(atom.expression, dialect) == argument_key and isinstance(atom.this, exp.Literal):
                    return True
    return False


def _is_not_null_atom_for_argument(
    atom: exp.Expression,
    argument_key: str,
    dialect: str | None = None,
) -> bool:
    if _is_not_null_filter(atom):
        target = atom.this
        if isinstance(atom, exp.Not) and isinstance(atom.this, exp.Is):
            target = atom.this.this
        return _expression_key(target, dialect) == argument_key
    if (
        isinstance(atom, exp.Not)
        and isinstance(atom.this, exp.Is)
        and isinstance(atom.this.expression, exp.Null)
    ):
        return _expression_key(atom.this.this, dialect) == argument_key
    return False


def _duplicate_stress_indexes(
    row_predicates_by_index: Mapping[int, Tuple[exp.Expression, ...]],
    row_count: int,
    argument: exp.Expression,
    null_index: int | None,
    dialect: str | None = None,
) -> Tuple[int, int] | None:
    unconstrained: List[int] = []
    forced_by_value: Dict[object, List[int]] = {}
    for index in range(row_count):
        if index == null_index:
            continue
        predicates = row_predicates_by_index.get(index, ())
        forces_null, forced_value = _predicate_argument_row_state(
            predicates,
            argument,
            dialect,
        )
        if forces_null:
            continue
        if forced_value is _MISSING:
            unconstrained.append(index)
        else:
            forced_by_value.setdefault(forced_value, []).append(index)
    if len(unconstrained) >= 2:
        return unconstrained[0], unconstrained[1]
    if unconstrained:
        for indexes in forced_by_value.values():
            if indexes:
                return unconstrained[0], indexes[0]
    for indexes in forced_by_value.values():
        if len(indexes) >= 2:
            return indexes[0], indexes[1]
    return None


def _predicate_argument_row_state(
    predicates: Sequence[exp.Expression],
    argument: exp.Expression,
    dialect: str | None = None,
) -> Tuple[bool, object]:
    argument_key = _expression_key(argument, dialect)
    forced_value: object = _MISSING
    for predicate in predicates:
        for atom in _conjuncts(predicate):
            if (
                isinstance(atom, exp.Is)
                and _expression_key(atom.this, dialect) == argument_key
                and isinstance(atom.expression, exp.Null)
            ):
                return True, forced_value
            if not isinstance(atom, exp.EQ):
                continue
            if (
                _expression_key(atom.this, dialect) == argument_key
                and isinstance(atom.expression, exp.Literal)
            ):
                forced_value = _literal_value(atom.expression)
            if (
                _expression_key(atom.expression, dialect) == argument_key
                and isinstance(atom.this, exp.Literal)
            ):
                forced_value = _literal_value(atom.this)
    return False, forced_value


def _row_predicates_for_group_row(
    group: GroupDemand,
    row_index: int,
) -> Tuple[exp.Expression, ...]:
    predicates = list(group.row_predicates)
    for local_index, local_predicates in group.row_predicates_by_index:
        if local_index == row_index:
            predicates.extend(local_predicates)
    return tuple(predicates)


def _not_null_predicate(expression: exp.Expression) -> exp.Expression:
    return exp.Not(this=exp.Is(this=deepcopy(expression), expression=exp.Null()))


def _comparison_target(
    condition: exp.Expression,
) -> Tuple[exp.Expression, exp.Expression, str] | None:
    if isinstance(condition, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
        operator = type(condition).__name__.lower()
        if isinstance(condition.expression, exp.Literal):
            return condition.this, condition.expression, operator
        if isinstance(condition.this, exp.Literal):
            reversed_operator = {
                "gt": "lt",
                "gte": "lte",
                "lt": "gt",
                "lte": "gte",
                "eq": "eq",
            }[operator]
            return condition.expression, condition.this, reversed_operator
    return None


def _value_for_operator(threshold: float, operator: str, *, pass_group: bool) -> object:
    if pass_group:
        if operator in {"gt", "gte", "eq"}:
            return int(threshold + 1) if float(threshold).is_integer() else threshold + 1
        return int(threshold - 1) if float(threshold).is_integer() else threshold - 1
    if operator in {"gt", "gte"}:
        return int(threshold - 1) if float(threshold).is_integer() else threshold - 1
    if operator in {"lt", "lte"}:
        return int(threshold + 1) if float(threshold).is_integer() else threshold + 1
    return int(threshold + 1) if float(threshold).is_integer() else threshold + 1


def _row_count_for_count(
    threshold: float,
    operator: str,
    default_count: int,
    *,
    pass_group: bool,
) -> int:
    if operator in {"gt", "gte"}:
        passing = int(threshold) + (1 if operator == "gt" else 0)
        failing = max(int(threshold) - (0 if operator == "gt" else 1), 0)
    elif operator in {"lt", "lte"}:
        passing = max(int(threshold) - (1 if operator == "lt" else 0), 0)
        failing = int(threshold) + (0 if operator == "lt" else 1)
    else:
        passing = int(threshold)
        failing = int(threshold) + 1
    return max(default_count, passing if pass_group else failing, 1)


def _group_demand_for_having(
    condition: exp.Expression,
    aggregate: Aggregate,
    group_index: int,
    default_row_count: int,
    *,
    pass_group: bool,
    dialect: str | None = None,
) -> GroupDemand | None:
    conjuncts = _conjuncts(condition)
    if len(conjuncts) > 1:
        if not pass_group:
            return _group_demand_for_having(
                conjuncts[0],
                aggregate,
                group_index,
                default_row_count,
                pass_group=False,
                dialect=dialect,
            )
        demands = [
            _group_demand_for_having(
                conjunct,
                aggregate,
                group_index,
                default_row_count,
                pass_group=True,
                dialect=dialect,
            )
            for conjunct in conjuncts
        ]
        if any(demand is None for demand in demands):
            return None
        return GroupDemand(
            group_index=group_index,
            row_count=max(demand.row_count for demand in demands if demand is not None),
            group_key_values=_group_key_values(aggregate, group_index, dialect),
            row_predicates=tuple(
                predicate
                for demand in demands
                if demand is not None
                for predicate in demand.row_predicates
            ),
            row_predicates_by_index=tuple(
                item
                for demand in demands
                if demand is not None
                for item in demand.row_predicates_by_index
            ),
        )
    target = _comparison_target(condition)
    if target is None:
        return None
    expression, literal, operator = target
    threshold = _numeric_value(literal)
    if threshold is None:
        return None

    aggregates = _aggregate_expression_map(aggregate, dialect)
    row_predicates: List[exp.Expression] = []
    row_predicates_by_index: List[Tuple[int, Tuple[exp.Expression, ...]]] = []
    row_count = max(default_row_count, 1)

    if isinstance(expression, exp.Div):
        numerator = _resolve_aggregate_expression(expression.this, aggregates, dialect)
        denominator = _resolve_aggregate_expression(expression.expression, aggregates, dialect)
        if not isinstance(numerator, exp.Sum) or not isinstance(denominator, exp.Count):
            return None
        numerator_arg = _aggregate_arg_expression(numerator)
        denominator_arg = _aggregate_arg_expression(denominator)
        if numerator_arg is None:
            return None
        value = _value_for_operator(threshold, operator, pass_group=pass_group)
        row_predicates.append(
            exp.GT(this=deepcopy(numerator_arg), expression=_literal_for_value(value))
            if pass_group and operator in {"gt", "gte"}
            else exp.LTE(this=deepcopy(numerator_arg), expression=_literal_for_value(value))
        )
        if denominator_arg is not None:
            row_predicates_by_index.extend(
                _required_not_null_row_predicates(
                    denominator_arg,
                    required_count=1,
                    row_count=row_count,
                    existing=row_predicates_by_index,
                    dialect=dialect,
                )
            )
        return GroupDemand(
            group_index=group_index,
            row_count=row_count,
            group_key_values=_group_key_values(aggregate, group_index, dialect),
            row_predicates=tuple(row_predicates),
            row_predicates_by_index=tuple(row_predicates_by_index),
        )

    aggregate_expression = _resolve_aggregate_expression(expression, aggregates, dialect)
    if aggregate_expression is None:
        return None

    if isinstance(aggregate_expression, exp.Count):
        row_count = _row_count_for_count(
            threshold,
            operator,
            default_row_count,
            pass_group=pass_group,
        )
        arg = _aggregate_arg_expression(aggregate_expression)
        if arg is not None:
            required_non_null = _non_null_count_for_count(
                threshold,
                operator,
                row_count,
                pass_group=pass_group,
            )
            row_predicates_by_index.extend(
                _required_not_null_row_predicates(
                    arg,
                    required_count=required_non_null,
                    row_count=row_count,
                    existing=row_predicates_by_index,
                    dialect=dialect,
                )
            )
    elif isinstance(aggregate_expression, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
        arg = _aggregate_arg_expression(aggregate_expression)
        if arg is None:
            return None
        if isinstance(aggregate_expression, exp.Avg):
            row_count = max(row_count, 2)
        elif isinstance(aggregate_expression, exp.Sum):
            row_count = 1
        value = _value_for_operator(threshold, operator, pass_group=pass_group)
        predicate_type = exp.GT if pass_group and operator in {"gt", "gte"} else exp.LTE
        if operator in {"lt", "lte"}:
            predicate_type = exp.LT if pass_group else exp.GTE
        row_predicates.append(
            predicate_type(this=deepcopy(arg), expression=_literal_for_value(value))
        )
    else:
        return None

    return GroupDemand(
        group_index=group_index,
        row_count=row_count,
        group_key_values=_group_key_values(aggregate, group_index, dialect),
        row_predicates=tuple(row_predicates),
        row_predicates_by_index=tuple(row_predicates_by_index),
    )


def _required_not_null_row_predicates(
    argument: exp.Expression,
    *,
    required_count: int,
    row_count: int,
    existing: Sequence[Tuple[int, Tuple[exp.Expression, ...]]],
    dialect: str | None = None,
) -> Tuple[Tuple[int, Tuple[exp.Expression, ...]], ...]:
    if required_count <= 0:
        return ()
    existing_by_index: Dict[int, Tuple[exp.Expression, ...]] = {
        index: predicates for index, predicates in existing
    }
    selected: List[Tuple[int, Tuple[exp.Expression, ...]]] = []
    for index in range(row_count):
        predicates = existing_by_index.get(index, ())
        forces_null, _forced_value = _predicate_argument_row_state(
            predicates,
            argument,
            dialect,
        )
        if forces_null:
            continue
        selected.append((index, (_not_null_predicate(argument),)))
        if len(selected) == required_count:
            return tuple(selected)
    return tuple(selected)


def _non_null_count_for_count(
    threshold: float,
    operator: str,
    row_count: int,
    *,
    pass_group: bool,
) -> int:
    if pass_group:
        if operator == "gt":
            return min(max(int(threshold) + 1, 0), row_count)
        if operator == "gte":
            return min(max(int(threshold), 0), row_count)
        if operator == "eq":
            return min(max(int(threshold), 0), row_count)
        if operator == "lt":
            return min(max(int(threshold) - 1, 0), row_count)
        if operator == "lte":
            return min(max(int(threshold), 0), row_count)
    if operator in {"gt", "gte"}:
        return 0
    if operator in {"lt", "lte", "eq"}:
        return min(row_count, max(int(threshold) + 1, 1))
    return 0


def _aggregate_expression_group_demands(
    aggregate: Aggregate,
    expression_demands: Sequence[ExpressionDemand],
    *,
    rows_per_group: int,
    dialect: str | None = None,
) -> Tuple[GroupDemand, ...]:
    demands: List[GroupDemand] = []
    for expression_demand in expression_demands:
        if not _expression_contains_aggregate(expression_demand.expression):
            continue
        predicates = _expression_demand_predicates(expression_demand)
        for predicate in predicates:
            demand = _aggregate_predicate_group_demand(
                predicate,
                aggregate,
                group_index=expression_demand.rank or 0,
                default_row_count=rows_per_group,
                dialect=dialect,
            )
            if demand is not None:
                demands.append(demand)
    return tuple(demands)


def _aggregate_group_key_expression_demands(
    aggregate: Aggregate,
    group_demands: Sequence[GroupDemand],
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    demands: List[ExpressionDemand] = []
    for group in group_demands:
        for expression in aggregate.group or ():
            if any(
                _expression_key(key, dialect) == _expression_key(expression, dialect)
                for key, _value in group.group_key_values
            ):
                continue
            demands.append(
                ExpressionDemand(
                    expression=deepcopy(expression),
                    kind="distinct",
                    rank=group.group_index,
                    origin=expression.sql(dialect=dialect),
                )
            )
    return tuple(demands)


def _ensure_aggregate_group_key_values(
    aggregate: Aggregate,
    group_demands: Sequence[GroupDemand],
    dialect: str | None = None,
) -> Tuple[GroupDemand, ...]:
    normalized: List[GroupDemand] = []
    for group in group_demands:
        existing = list(group.group_key_values)
        for key, value in _group_key_values(aggregate, group.group_index, dialect):
            if any(
                _expression_key(existing_key, dialect) == _expression_key(key, dialect)
                for existing_key, _existing_value in existing
            ):
                continue
            existing.append((key, value))
        normalized.append(
            GroupDemand(
                group_index=group.group_index,
                row_count=group.row_count,
                group_key_values=tuple(existing),
                row_predicates=group.row_predicates,
                row_predicates_by_index=group.row_predicates_by_index,
            )
        )
    return tuple(normalized)


def _expression_contains_aggregate(expression: exp.Expression) -> bool:
    return any(
        expression.find(kind) is not None
        for kind in (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
    )


def _aggregate_predicate_group_demand(
    predicate: exp.Expression,
    aggregate: Aggregate,
    *,
    group_index: int,
    default_row_count: int,
    dialect: str | None = None,
) -> GroupDemand | None:
    literal_demand = _group_demand_for_having(
        predicate,
        aggregate,
        group_index,
        default_row_count,
        pass_group=True,
        dialect=dialect,
    )
    if literal_demand is not None:
        return literal_demand
    if not isinstance(predicate, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
        return None
    aggregates = _aggregate_expression_map(aggregate, dialect)
    left = _resolve_aggregate_expression(predicate.this, aggregates, dialect)
    right = _resolve_aggregate_expression(predicate.expression, aggregates, dialect)
    if type(left) is not type(right) or not isinstance(left, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
        return None
    left_arg = _aggregate_arg_expression(left)
    right_arg = _aggregate_arg_expression(right)
    if left_arg is None or right_arg is None:
        return None
    row_predicate = _aggregate_argument_margin_predicate(
        type(predicate),
        left_arg,
        right_arg,
    )
    row_count = max(default_row_count, 2 if isinstance(left, exp.Avg) else 1)
    return GroupDemand(
        group_index=group_index,
        row_count=row_count,
        group_key_values=_group_key_values(aggregate, group_index, dialect),
        row_predicates=(row_predicate,),
    )


def _aggregate_argument_margin_predicate(
    predicate_type: type,
    left_arg: exp.Expression,
    right_arg: exp.Expression,
) -> exp.Expression:
    margin = exp.Literal.number("1000")
    if predicate_type in (exp.GT, exp.GTE):
        return exp.GT(
            this=deepcopy(left_arg),
            expression=exp.Add(this=deepcopy(right_arg), expression=margin),
        )
    if predicate_type in (exp.LT, exp.LTE):
        return exp.LT(
            this=exp.Add(this=deepcopy(left_arg), expression=margin),
            expression=deepcopy(right_arg),
        )
    return exp.EQ(this=deepcopy(left_arg), expression=deepcopy(right_arg))


def _having_group_demands(
    condition: exp.Expression,
    aggregate: Aggregate,
    *,
    group_count: int,
    default_row_count: int,
    pass_group: bool,
    start_index: int = 0,
    dialect: str | None = None,
) -> Tuple[GroupDemand, ...]:
    demands: List[GroupDemand] = []
    for offset in range(max(group_count, 1)):
        group_index = start_index + offset
        demand = _group_demand_for_having(
            condition,
            aggregate,
            group_index,
            default_row_count,
            pass_group=pass_group,
            dialect=dialect,
        )
        if demand is not None:
            demands.append(demand)
    return tuple(demands)


def _aggregate_group_demands(
    aggregate: Aggregate,
    *,
    group_count: int,
    rows_per_group: int,
    dialect: str | None = None,
) -> Tuple[GroupDemand, ...]:
    return tuple(
        GroupDemand(
            group_index=index,
            row_count=min(index + 1, max(rows_per_group, 1)),
            group_key_values=_group_key_values(aggregate, index, dialect),
        )
        for index in range(max(group_count, 1))
    )


def _schema_satisfies_group_demands(
    schema: DerivedSchema,
    demand: SchemaDemand,
) -> bool:
    group_sizes = sorted(max(len(row.rowid) - 2, 0) for row in schema.rows)
    used: set[int] = set()
    for required in sorted(group.row_count for group in demand.group_demands):
        match = next(
            (
                index
                for index, size in enumerate(group_sizes)
                if index not in used and size >= required
            ),
            None,
        )
        if match is None:
            return False
        used.add(match)
    return True


def _schema_satisfies_aggregate_argument_stress(
    root: Step,
    cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    dialect: str | None = None,
) -> bool:
    aggregates = [
        step
        for step in _reachable_steps(root)
        if isinstance(step, Aggregate) and step in cache and step.dependencies
    ]
    for aggregate in aggregates:
        child = next(iter(aggregate.dependencies))
        if child not in cache:
            continue
        child_schema = _schema_for(cache, child)
        arguments = _aggregate_argument_columns(aggregate, child_schema, dialect)
        if not arguments:
            continue
        groups = _rows_by_group(aggregate, child_schema)
        for _aggregate_expression, argument in arguments:
            if child_schema.nullable(argument) and not any(
                any(_expr_value(argument, row) is None for row in rows)
                for rows in groups.values()
            ):
                return False
            if not child_schema.is_unique(argument) and not any(
                _has_duplicate_non_null_argument(argument, rows)
                for rows in groups.values()
            ):
                return False
    return True


def _rows_by_group(
    aggregate: Aggregate,
    child_schema: DerivedSchema,
) -> Dict[Tuple[Any, ...], List[Row]]:
    group_exprs = tuple(aggregate.group or ())
    grouped: Dict[Tuple[Any, ...], List[Row]] = {}
    for row in child_schema.rows:
        key = tuple(_expr_value(expr, row) for expr in group_exprs)
        grouped.setdefault(key, []).append(row)
    if not grouped and not group_exprs:
        grouped[()] = []
    return grouped


def _has_duplicate_non_null_argument(
    argument: exp.Expression,
    rows: Sequence[Row],
) -> bool:
    values = [_expr_value(argument, row) for row in rows]
    non_null_values = [value for value in values if value is not None]
    return len(set(non_null_values)) < len(non_null_values)


def _root_result_count(root: Step, bounds: object) -> int:
    fetch = _root_fetch(root)
    if fetch is not None:
        return max(int(fetch or 1), 1)
    aggregate = _root_aggregate(root)
    if aggregate is not None:
        if _is_distinct_aggregate(aggregate):
            return max(int(getattr(bounds, "result_rows", 1) or 1), 1)
        if aggregate.group:
            return max(int(getattr(bounds, "groups", 1) or 1), 1)
        return 1
    return max(int(getattr(bounds, "result_rows", 1) or 1), 1)


def _root_fetch(root: Step) -> int | None:
    node = root
    while True:
        if isinstance(node, (Limit, Sort)) and node.fetch is not None:
            return node.fetch
        if not isinstance(node, Projection) or len(node.dependencies) != 1:
            return None
        node = next(iter(node.dependencies))


def _root_aggregate(root: Step) -> Aggregate | None:
    node = root
    while True:
        if isinstance(node, Aggregate):
            return node
        if isinstance(node, (Limit, Sort)):
            return None
        if not isinstance(node, Projection) or len(node.dependencies) != 1:
            return None
        node = next(iter(node.dependencies))


def _is_distinct_aggregate(aggregate: Aggregate) -> bool:
    return bool(aggregate.group) and not aggregate.aggregations


def _single_dependency(step: Step) -> Step:
    try:
        return next(iter(step.dependencies))
    except StopIteration as exc:
        raise ValueError(f"{step.type_name} has no dependency") from exc


def _join_inputs(join: Join) -> Tuple[Step, Step] | None:
    if join.left is not None and join.right is not None:
        return join.left, join.right
    dependencies = tuple(join.dependencies)
    if len(dependencies) == 2:
        return dependencies[0], dependencies[1]
    return None


def _schema_for(
    cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    step: Step,
) -> DerivedSchema:
    return cache[step][0]


def _schema_alias(schema: DerivedSchema) -> str:
    for column in schema.columns:
        if isinstance(column, exp.Column) and column.table:
            return column.table.casefold()
    table = getattr(schema, "_table", None)
    return table.name.casefold() if table is not None else ""


def _schema_table_order(instance: Instance, schema: DerivedSchema) -> int:
    table = getattr(schema, "_table", None)
    if table is None:
        return 0
    for index, ordered in enumerate(instance.schema.fk_safe_table_order()):
        if ordered.name.casefold() == table.name.casefold():
            return index
    return 0


def _identifier(value: object) -> exp.Identifier:
    if isinstance(value, exp.Identifier):
        return value
    if isinstance(value, (exp.Table, exp.Column)):
        return value.this
    return exp.to_identifier(str(value))


def _identifier_equal(left: object, right: object, dialect: str | None) -> bool:
    if dialect:
        return same_identifier(_identifier(left), _identifier(right), dialect)
    return _identifier(left).name.casefold() == _identifier(right).name.casefold()


def _filter_conditions_for_step(
    step: Step,
    schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[exp.Expression, ...]:
    conditions: List[exp.Expression] = []
    seen: Set[int] = set()

    def visit(node: Step) -> None:
        if id(node) in seen:
            return
        seen.add(id(node))
        if isinstance(node, Filter) and node.condition is not None:
            if not node.condition.find(ScalarSubqueryRef):
                conditions.append(_expression_in_schema_scope(node.condition, schema, dialect))
        for dependency in node.dependencies:
            visit(dependency)

    visit(step)
    return tuple(conditions)


def _filter_outcomes(schema: DerivedSchema, condition: exp.Expression) -> Set[object]:
    outcomes: Set[object] = set()
    for row in schema.rows:
        try:
            outcomes.add(concrete(condition, Environment.from_row(row)))
        except Exception:
            continue
    return outcomes


def _false_condition(condition: exp.Expression) -> exp.Expression | None:
    conjuncts = _conjuncts(condition)
    if not conjuncts:
        return None
    atoms = [deepcopy(atom) for atom in conjuncts]
    invert_index = next(
        (index for index, atom in enumerate(atoms) if not _is_not_null_filter(atom)),
        None,
    )
    if invert_index is None:
        return None
    atoms[invert_index] = _invert_atom(atoms[invert_index])
    return _and_all(atoms)


def _invert_atom(atom: exp.Expression) -> exp.Expression:
    if isinstance(atom, exp.GT):
        return exp.LTE(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    if isinstance(atom, exp.GTE):
        return exp.LT(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    if isinstance(atom, exp.LT):
        return exp.GTE(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    if isinstance(atom, exp.LTE):
        return exp.GT(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    if isinstance(atom, exp.EQ):
        return exp.NEQ(this=deepcopy(atom.this), expression=deepcopy(atom.expression))
    return exp.Not(this=deepcopy(atom))


def _null_condition_for_schema(
    condition: exp.Expression,
    schema: DerivedSchema,
    dialect: str | None = None,
) -> exp.Expression | None:
    conjuncts = _conjuncts(condition)
    for atom_index, atom in enumerate(conjuncts):
        if _is_not_null_filter(atom):
            continue
        for column in atom.find_all(exp.Column):
            if not _schema_has_column(schema, column, dialect):
                continue
            visible = _visible_schema_column(schema, column, dialect)
            if visible is None:
                continue
            if not schema.nullable(visible):
                continue
            atoms = [
                deepcopy(other)
                for index, other in enumerate(conjuncts)
                if index != atom_index
            ]
            atoms.append(exp.Is(this=deepcopy(column), expression=exp.Null()))
            return _and_all(atoms)
    return None


def _and_all(atoms: Sequence[exp.Expression]) -> exp.Expression:
    if not atoms:
        return exp.Boolean(this=True)
    expression = atoms[0]
    for atom in atoms[1:]:
        expression = exp.And(this=expression, expression=atom)
    return expression


def _expression_in_schema_scope(
    expression: exp.Expression,
    schema: DerivedSchema,
    dialect: str | None = None,
) -> exp.Expression:
    rewritten = deepcopy(expression)
    for column in rewritten.find_all(exp.Column):
        visible = _visible_schema_column(schema, column, dialect)
        if visible is not None and visible.table:
            column.set("table", exp.Identifier(this=visible.table))
    return rewritten


def _rewrite_demand_alias(
    demand: SchemaDemand,
    alias: str,
    output_schema: DerivedSchema,
    child_schema: DerivedSchema,
    dialect: str | None = None,
) -> SchemaDemand:
    if not alias:
        return demand
    column_map = _schema_column_name_map(output_schema, child_schema, dialect)
    return SchemaDemand(
        count=demand.count,
        predicates=tuple(
            _rewrite_columns_through_schema(predicate, alias, column_map, dialect)
            for predicate in demand.predicates
        ),
        order_keys=tuple(
            _rewrite_columns_through_schema(key, alias, column_map, dialect)
            for key in demand.order_keys
        ),
        distinct=demand.distinct,
        group_demands=tuple(
            GroupDemand(
                group_index=group.group_index,
                row_count=group.row_count,
                group_key_values=tuple(
                    (
                        _rewrite_columns_through_schema(key, alias, column_map, dialect),
                        value,
                    )
                    for key, value in group.group_key_values
                ),
                row_predicates=tuple(
                    _rewrite_columns_through_schema(predicate, alias, column_map, dialect)
                    for predicate in group.row_predicates
                ),
                row_predicates_by_index=tuple(
                    (
                        index,
                        tuple(
                            _rewrite_columns_through_schema(predicate, alias, column_map, dialect)
                            for predicate in predicates
                        ),
                    )
                    for index, predicates in group.row_predicates_by_index
                ),
            )
            for group in demand.group_demands
        ),
        expression_demands=tuple(
            ExpressionDemand(
                expression=_rewrite_columns_through_schema(
                    expression_demand.expression,
                    alias,
                    column_map,
                    dialect,
                ),
                kind=expression_demand.kind,
                value=expression_demand.value,
                rank=expression_demand.rank,
                descending=expression_demand.descending,
                origin=expression_demand.origin,
            )
            for expression_demand in demand.expression_demands
        ),
    )


def _schema_column_name_map(
    output_schema: DerivedSchema,
    child_schema: DerivedSchema,
    dialect: str | None = None,
) -> Dict[str, exp.Column]:
    mapping: Dict[str, exp.Column] = {}
    for output in output_schema.columns:
        if not isinstance(output, exp.Column):
            continue
        child = next(
            (
                column
                for column in child_schema.columns
                if isinstance(column, exp.Column)
                and _identifier_equal(column.this, output.this, dialect)
            ),
            None,
        )
        if child is not None:
            mapping[output.name.casefold()] = child
    return mapping


def _rewrite_columns_through_schema(
    expression: exp.Expression,
    alias: str,
    column_map: Mapping[str, exp.Column],
    dialect: str | None = None,
) -> exp.Expression:
    rewritten = deepcopy(expression)
    for column in rewritten.find_all(exp.Column):
        if column.table and _identifier_equal(column.args["table"], alias, dialect):
            child = column_map.get(column.name.casefold())
            if child is not None:
                column.set("table", exp.Identifier(this=child.table) if child.table else None)
                column.set("this", child.this.copy())
    return rewritten


def _rewrite_projection_demand(
    demand: SchemaDemand,
    projection: Projection,
    output_schema: DerivedSchema,
    child_schema: DerivedSchema,
    dialect: str | None = None,
) -> SchemaDemand:
    del child_schema
    expression_by_output = _projection_expression_map(projection, output_schema, dialect)
    expression_demands = tuple(
        _rewrite_projection_expression_demand(
            expression_demand,
            expression_by_output,
            dialect,
        )
        for expression_demand in demand.expression_demands
    )
    if demand.distinct:
        expression_demands += _projection_distinct_expression_demands(
            projection,
            output_schema,
            demand.count,
            dialect,
        )
    if not demand.distinct and not demand.group_demands:
        expression_demands += _projection_case_expression_demands(
            projection,
            output_schema,
            dialect,
        )
    return SchemaDemand(
        count=demand.count,
        predicates=tuple(
            _rewrite_projection_expression(predicate, expression_by_output, dialect)
            for predicate in demand.predicates
        ),
        order_keys=tuple(
            _rewrite_projection_expression(key, expression_by_output, dialect)
            for key in demand.order_keys
        ),
        distinct=False if demand.distinct else demand.distinct,
        group_demands=tuple(
            GroupDemand(
                group_index=group.group_index,
                row_count=group.row_count,
                group_key_values=tuple(
                    _rewrite_projection_group_key(key, value, group.group_index, expression_by_output, dialect)
                    for key, value in group.group_key_values
                ),
                row_predicates=tuple(
                    _rewrite_projection_expression(predicate, expression_by_output, dialect)
                    for predicate in group.row_predicates
                ),
                row_predicates_by_index=tuple(
                    (
                        index,
                        tuple(
                            _rewrite_projection_expression(predicate, expression_by_output, dialect)
                            for predicate in predicates
                        ),
                    )
                    for index, predicates in group.row_predicates_by_index
                ),
            )
            for group in demand.group_demands
        ),
        expression_demands=expression_demands,
    )


def _rewrite_projection_group_key(
    key: exp.Expression,
    value: object,
    group_index: int,
    expression_by_output: Mapping[str, exp.Expression],
    dialect: str | None = None,
) -> Tuple[exp.Expression, object]:
    rewritten = _rewrite_projection_expression(key, expression_by_output, dialect)
    if isinstance(rewritten, exp.Case):
        value = _group_key_value_for_index(rewritten, group_index)
    return rewritten, value


def _rewrite_projection_expression_demand(
    expression_demand: ExpressionDemand,
    expression_by_output: Mapping[str, exp.Expression],
    dialect: str | None = None,
) -> ExpressionDemand:
    expression = _rewrite_projection_expression(
        expression_demand.expression,
        expression_by_output,
        dialect,
    )
    value = expression_demand.value
    kind = expression_demand.kind
    if (
        kind == "distinct"
        and expression_demand.rank is not None
        and isinstance(expression, exp.Case)
    ):
        values = _distinct_target_values(expression, expression_demand.rank + 1)
        if values:
            value = values[expression_demand.rank % len(values)]
            kind = "predicate"
    return ExpressionDemand(
        expression=expression,
        kind=kind,
        value=value,
        rank=expression_demand.rank,
        descending=expression_demand.descending,
        origin=expression_demand.origin,
    )


def _projection_distinct_expression_demands(
    projection: Projection,
    output_schema: DerivedSchema,
    count: int,
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    demands: List[ExpressionDemand] = []
    for projected, output in zip(projection.projections, output_schema.columns):
        expression = projected.this if isinstance(projected, exp.Alias) else projected
        for rank in range(count):
            demands.append(
                ExpressionDemand(
                    expression=deepcopy(expression),
                    kind="distinct",
                    rank=rank,
                    origin=output.sql(dialect=dialect)
                    if isinstance(output, exp.Expression)
                    else str(output),
                )
            )
    return tuple(demands)


def _projection_case_expression_demands(
    projection: Projection,
    output_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    demands: List[ExpressionDemand] = []
    for projected, output in zip(projection.projections, output_schema.columns):
        expression = projected.this if isinstance(projected, exp.Alias) else projected
        case = expression if isinstance(expression, exp.Case) else expression.find(exp.Case)
        if not isinstance(case, exp.Case):
            continue
        for branch in case.args.get("ifs") or ():
            result = branch.args.get("true") if isinstance(branch, exp.If) else None
            if isinstance(result, exp.Literal):
                demands.append(
                    ExpressionDemand(
                        expression=deepcopy(case),
                        kind="predicate",
                        value=_literal_value(result),
                        origin=output.sql(dialect=dialect)
                        if isinstance(output, exp.Expression)
                        else str(output),
                    )
                )
                break
    return tuple(demands)


def _distinct_target_values(expression: exp.Expression, count: int) -> Tuple[object, ...]:
    if isinstance(expression, exp.Case):
        values: List[object] = []
        for branch in expression.args.get("ifs") or ():
            result = branch.args.get("true") if isinstance(branch, exp.If) else None
            if isinstance(result, exp.Literal):
                values.append(_literal_value(result))
        default = expression.args.get("default")
        if isinstance(default, exp.Literal):
            values.append(_literal_value(default))
        deduped = tuple(dict.fromkeys(values))
        if len(deduped) >= min(count, len(values)):
            return deduped
    return ()


def _order_expression_demands(
    order_keys: Sequence[exp.Expression],
    count: int,
    dialect: str | None = None,
) -> Tuple[ExpressionDemand, ...]:
    demands: List[ExpressionDemand] = []
    for order_index, ordered in enumerate(order_keys):
        expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
        desc = isinstance(ordered, exp.Ordered) and bool(ordered.args.get("desc"))
        order_expr = expr
        predicate_expr: exp.Expression | None = None
        if isinstance(expr, exp.Case):
            for branch in expr.args.get("ifs") or ():
                if not isinstance(branch, exp.If):
                    continue
                branch_result = branch.args.get("true")
                if branch.this is not None and branch_result is not None:
                    predicate_expr = deepcopy(branch.this)
                    order_expr = deepcopy(branch_result)
                    break
        for rank in range(count):
            demands.append(
                ExpressionDemand(
                    expression=deepcopy(order_expr),
                    kind="order",
                    value=_order_rank_value(rank, count, desc),
                    rank=rank,
                    descending=desc,
                    origin=ordered.sql(dialect=dialect)
                    if isinstance(ordered, exp.Expression)
                    else str(ordered),
                )
            )
            if predicate_expr is not None:
                demands.append(
                    ExpressionDemand(
                        expression=deepcopy(predicate_expr),
                        kind="predicate_expression",
                        rank=rank,
                        origin=ordered.sql(dialect=dialect)
                        if isinstance(ordered, exp.Expression)
                        else str(ordered),
                    )
                )
    return tuple(demands)


def _order_rank_value(rank: int, count: int, descending: bool) -> object:
    if count > 1 and rank == count - 1:
        return count if descending else 1
    return count - rank if descending else rank + 1


def _expression_demand_predicates(
    demand: ExpressionDemand,
) -> Tuple[exp.Expression, ...]:
    expression = demand.expression
    value = demand.value
    if demand.kind == "equal":
        return ()
    if demand.kind == "predicate_expression":
        return (deepcopy(expression),)
    if isinstance(expression, exp.Alias):
        expression = expression.this
    if isinstance(expression, exp.Ordered):
        expression = expression.this
    if demand.kind == "order" and isinstance(expression, exp.Column):
        return ()
    while isinstance(expression, exp.Cast):
        expression = expression.this
    if isinstance(expression, exp.Case) and value is not None:
        predicates = _case_value_predicates(expression, value)
        if predicates:
            return predicates
    if value is None:
        return ()
    return (exp.EQ(this=deepcopy(expression), expression=_literal_for_value(value)),)


def _case_value_predicates(
    expression: exp.Case,
    value: object,
) -> Tuple[exp.Expression, ...]:
    prior_conditions: List[exp.Expression] = []
    for branch in expression.args.get("ifs") or ():
        if not isinstance(branch, exp.If):
            continue
        condition = branch.this
        result = branch.args.get("true")
        if _literal_matches(result, value):
            return tuple([deepcopy(condition)] + [_false_condition(cond) for cond in prior_conditions if _false_condition(cond) is not None])
        if result is not None and value is not None and not isinstance(result, exp.Literal):
            return tuple(
                [deepcopy(condition)]
                + [
                    false_condition
                    for prior in prior_conditions
                    if (false_condition := _false_condition(prior)) is not None
                ]
                + [exp.EQ(this=deepcopy(result), expression=_literal_for_value(value))]
            )
        if condition is not None:
            prior_conditions.append(condition)
    default = expression.args.get("default")
    if _literal_matches(default, value):
        return tuple(
            false_condition
            for condition in prior_conditions
            if (false_condition := _false_condition(condition)) is not None
        )
    if default is not None and value is not None and not isinstance(default, exp.Literal):
        return tuple(
            [
                false_condition
                for condition in prior_conditions
                if (false_condition := _false_condition(condition)) is not None
            ]
            + [exp.EQ(this=deepcopy(default), expression=_literal_for_value(value))]
        )
    return ()


def _literal_matches(expression: exp.Expression | None, value: object) -> bool:
    return isinstance(expression, exp.Literal) and _literal_value(expression) == value


def _projection_expression_map(
    projection: Projection,
    output_schema: DerivedSchema,
    dialect: str | None = None,
) -> Dict[str, exp.Expression]:
    mapping: Dict[str, exp.Expression] = {}
    for projected, output in zip(projection.projections, output_schema.columns):
        if not isinstance(output, exp.Column):
            continue
        expression = projected.this if isinstance(projected, exp.Alias) else projected
        mapping[output.name.casefold()] = expression
        mapping[_expression_key(output, dialect)] = expression
        mapping[_expression_key(expression, dialect)] = expression
        mapping[_expression_key_without_casts(expression, dialect)] = expression
        mapping[_physical_expression_key(output, dialect)] = expression
        mapping[_physical_expression_key(expression, dialect)] = expression
    for projected in projection.projections:
        expression = projected.this if isinstance(projected, exp.Alias) else projected
        mapping[_expression_key(expression, dialect)] = expression
        mapping[_expression_key_without_casts(expression, dialect)] = expression
        mapping[_physical_expression_key(expression, dialect)] = expression
        if expression.alias_or_name:
            mapping[str(expression.alias_or_name).casefold()] = expression
    return mapping


def _rewrite_projection_expression(
    expression: exp.Expression,
    expression_by_output: Mapping[str, exp.Expression],
    dialect: str | None = None,
) -> exp.Expression:
    def replace(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column):
            return node
        replacement = expression_by_output.get(node.name.casefold())
        if replacement is None:
            replacement = expression_by_output.get(_expression_key(node, dialect))
        if replacement is None:
            replacement = expression_by_output.get(_expression_key_without_casts(node, dialect))
        if replacement is None:
            replacement = expression_by_output.get(_physical_expression_key(node, dialect))
        if replacement is not None:
            return deepcopy(replacement)
        return node

    if isinstance(expression, exp.Column):
        return replace(expression)
    if isinstance(expression, exp.Ordered):
        rewritten = deepcopy(expression)
        rewritten.set(
            "this",
            _rewrite_projection_expression(expression.this, expression_by_output, dialect),
        )
        return rewritten
    return deepcopy(expression).transform(replace)


def _expression_key(expression: exp.Expression, dialect: str | None = None) -> str:
    if isinstance(expression, exp.Expression):
        canonical = deepcopy(expression).transform(_normalize_identifier)
        return canonical.sql(dialect=dialect, normalize=True)
    return str(expression)


def _expression_key_without_casts(
    expression: exp.Expression,
    dialect: str | None = None,
) -> str:
    rewritten = deepcopy(expression).transform(
        lambda node: node.this.copy() if isinstance(node, exp.Cast) else node
    )
    return _expression_key(rewritten, dialect)


def _physical_expression_key(
    expression: exp.Expression,
    dialect: str | None = None,
) -> str:
    return _expression_key_without_casts(expression, dialect)


def _safe_expression_sql(expression: exp.Expression, dialect: str | None = None) -> str:
    return expression.sql(dialect=dialect)


def _normalize_identifier(node: exp.Expression) -> exp.Expression:
    if isinstance(node, exp.Identifier):
        node.set("this", str(node.this).casefold())
    return node


def _normalize_expression_key(text: str) -> str:
    return (
        text.casefold()
        .replace('"', "")
        .replace("`", "")
        .replace(" ", "")
    )


def _strip_generated_casts(text: str) -> str:
    key = _normalize_expression_key(text)
    while True:
        start = key.find("cast(")
        if start < 0:
            return key
        inner_start = start + len("cast(")
        depth = 0
        as_index: int | None = None
        index = inner_start
        while index < len(key):
            char = key[index]
            if char == "(":
                depth += 1
                index += 1
                continue
            if char == ")":
                if depth == 0:
                    break
                depth -= 1
                index += 1
                continue
            if depth == 0 and key.startswith("as", index):
                as_index = index
                break
            index += 1
        if as_index is None:
            return key
        end = key.find(")", as_index)
        if end < 0:
            return key
        key = key[:start] + key[inner_start:as_index] + key[end + 1 :]


def _split_predicate_by_schema(
    predicate: exp.Expression,
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[Tuple[exp.Expression, ...], Tuple[exp.Expression, ...]]:
    left: List[exp.Expression] = []
    right: List[exp.Expression] = []
    for atom in _conjuncts(predicate):
        columns = tuple(atom.find_all(exp.Column))
        if not columns:
            continue
        if all(_schema_has_column(left_schema, column, dialect) for column in columns):
            left.append(_expression_in_schema_scope(atom, left_schema, dialect))
        elif all(_schema_has_column(right_schema, column, dialect) for column in columns):
            right.append(_expression_in_schema_scope(atom, right_schema, dialect))
    return tuple(left), tuple(right)


def _split_group_row_predicates_by_schema(
    group: GroupDemand,
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[
    Tuple[Tuple[int, Tuple[exp.Expression, ...]], ...],
    Tuple[Tuple[int, Tuple[exp.Expression, ...]], ...],
]:
    left_rows: List[Tuple[int, Tuple[exp.Expression, ...]]] = []
    right_rows: List[Tuple[int, Tuple[exp.Expression, ...]]] = []
    for row_index, predicates in group.row_predicates_by_index:
        left_predicates: List[exp.Expression] = []
        right_predicates: List[exp.Expression] = []
        for predicate in predicates:
            left_part, right_part = _split_predicate_by_schema(
                predicate,
                left_schema,
                right_schema,
                dialect,
            )
            left_predicates.extend(left_part)
            right_predicates.extend(right_part)
        if left_predicates:
            left_rows.append((row_index, tuple(left_predicates)))
        if right_predicates:
            right_rows.append((row_index, tuple(right_predicates)))
    return tuple(left_rows), tuple(right_rows)


def _aggregate_case_predicates(step: Aggregate) -> Tuple[exp.Expression, ...]:
    predicates: List[exp.Expression] = []
    for aggregate in step.aggregations:
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        for case in expression.find_all(exp.Case):
            for branch in case.args.get("ifs") or ():
                condition = branch.this if isinstance(branch, exp.If) else None
                if condition is not None:
                    predicates.append(_unwrap_aggregate_common_expr_aliases(condition))
    return tuple(predicates)


def _unwrap_aggregate_common_expr_aliases(expression: exp.Expression) -> exp.Expression:
    rewritten = deepcopy(expression)
    for alias in list(rewritten.find_all(exp.Alias)):
        alias_name = alias.alias
        if not alias_name or "." not in alias_name:
            continue
        table_name, column_name = alias_name.split(".", 1)
        alias.replace(
            exp.Column(
                this=exp.Identifier(this=column_name),
                table=exp.Identifier(this=table_name),
            )
        )
    return rewritten


def _sort_required_child_count(
    sort: Sort,
    demand: SchemaDemand,
    child_schema: DerivedSchema,
    competitor_count: int,
) -> int:
    fetch = sort.fetch or demand.count
    required_window = max(demand.count, fetch)
    if not sort.key:
        return max(required_window - len(child_schema.rows), 0)

    rows = _sorted_rows_for_step(sort, child_schema.rows)
    selected = rows[:fetch]
    selected_count = len(selected)
    has_competitor = len(rows) > fetch
    has_tie = _sort_has_rank_tie(sort, rows, selected)

    missing = max(required_window - selected_count, 0)
    if competitor_count and not has_competitor:
        missing += competitor_count
    if not has_tie:
        missing += 1
    return missing


def _sorted_rows_for_step(sort: Sort, rows: Sequence[Row]) -> List[Row]:
    ordered = list(rows)
    for key in reversed(sort.key or []):
        expr = key.this if isinstance(key, exp.Ordered) else key
        desc = isinstance(key, exp.Ordered) and bool(key.args.get("desc"))
        ordered.sort(
            key=lambda row: sql_order_key(_expr_value(expr, row)),
            reverse=desc,
        )
    return ordered


def _sort_has_rank_tie(
    sort: Sort,
    rows: Sequence[Row],
    selected: Sequence[Row],
) -> bool:
    if not selected:
        return False
    selected_ids = {id(row) for row in selected}
    selected_keys = {
        _sort_key_tuple(sort, row)
        for row in selected
    }
    return any(
        id(row) not in selected_ids and _sort_key_tuple(sort, row) in selected_keys
        for row in rows
    )


def _sort_key_tuple(sort: Sort, row: Row) -> Tuple[Any, ...]:
    return tuple(
        _expr_value(key.this if isinstance(key, exp.Ordered) else key, row)
        for key in sort.key
    )


def _split_order_keys_by_schema(
    order_keys: Tuple[exp.Expression, ...],
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    dialect: str | None = None,
) -> Tuple[Tuple[exp.Expression, ...], Tuple[exp.Expression, ...]]:
    left: List[exp.Expression] = []
    right: List[exp.Expression] = []
    for key in order_keys:
        expr = key.this if isinstance(key, exp.Ordered) else key
        columns = tuple(expr.find_all(exp.Column))
        if columns and all(_schema_has_column(left_schema, column, dialect) for column in columns):
            left.append(_expression_in_schema_scope(key, left_schema, dialect))
        elif columns and all(_schema_has_column(right_schema, column, dialect) for column in columns):
            right.append(_expression_in_schema_scope(key, right_schema, dialect))
    return tuple(left), tuple(right)


def _expression_uses_only_schema_columns(
    expression: exp.Expression,
    schema: DerivedSchema,
    dialect: str | None = None,
) -> bool:
    columns = tuple(expression.find_all(exp.Column))
    return bool(columns) and all(
        _schema_has_column(schema, column, dialect)
        for column in columns
    )


def _schema_has_column(
    schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> bool:
    requested_table = column.args.get("table")
    for candidate in schema.columns:
        if not isinstance(candidate, exp.Column):
            continue
        if not _identifier_equal(candidate.this, column.this, dialect):
            continue
        if (
            requested_table is not None
            and candidate.args.get("table") is not None
            and not _identifier_equal(candidate.args["table"], requested_table, dialect)
        ):
            continue
        return True
    return False


def _schema_side_for_column(
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> str | None:
    if _schema_has_column(left_schema, column, dialect):
        return "left"
    if _schema_has_column(right_schema, column, dialect):
        return "right"
    return None


def _solve_join_key_value(
    left_schema: DerivedSchema,
    right_schema: DerivedSchema,
    left: exp.Column,
    right: exp.Column,
    left_predicates: Sequence[exp.Expression],
    right_predicates: Sequence[exp.Expression],
    avoid_values: Sequence[object],
    dialect: str | None = None,
) -> object | None:
    left_dtype = _schema_column_dtype(left_schema, left, dialect)
    right_dtype = _schema_column_dtype(right_schema, right, dialect)
    dtype = left_dtype or right_dtype
    nonce = len(avoid_values)
    left_var = SolverVar(
        key=f"join.left.{left.sql(dialect=dialect)}.{nonce}",
        dtype=dtype,
        meta={"column": left.name},
    )
    right_var = SolverVar(
        key=f"join.right.{right.sql(dialect=dialect)}.{nonce}",
        dtype=dtype,
        meta={"column": right.name},
    )
    constraints: List[exp.Expression] = [
        exp.EQ(this=left_var, expression=right_var),
    ]
    constraints.extend(
        exp.NEQ(this=left_var, expression=_literal_for_value(value))
        for value in avoid_values
        if value is not None
    )
    constraints.extend(
        _rewrite_join_key_predicates(
            left_predicates,
            left_schema,
            left,
            left_var,
            dialect,
        )
    )
    constraints.extend(
        _rewrite_join_key_predicates(
            right_predicates,
            right_schema,
            right,
            right_var,
            dialect,
        )
    )
    result = Solver(dialect=dialect or "sqlite", timeout_ms=2000).solve(
        Problem(constraints=constraints)
    )
    if not result.sat:
        return None
    if left_var in result.assignments:
        return result.assignments[left_var]
    if right_var in result.assignments:
        return result.assignments[right_var]
    return None


def _predicates_reference_join_key(
    predicates: Sequence[exp.Expression],
    schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> bool:
    return any(
        isinstance(candidate, exp.Column)
        and candidate.name.casefold() == column.name.casefold()
        and _schema_has_column(schema, candidate, dialect)
        for predicate in predicates
        for atom in _conjuncts(predicate)
        if not _is_not_null_filter(atom)
        for candidate in atom.find_all(exp.Column)
    )


def _rewrite_join_key_predicates(
    predicates: Sequence[exp.Expression],
    schema: DerivedSchema,
    column: exp.Column,
    variable: SolverVar,
    dialect: str | None = None,
) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    for predicate in predicates:
        if not any(
            isinstance(candidate, exp.Column)
            and candidate.name.casefold() == column.name.casefold()
            and _schema_has_column(schema, candidate, dialect)
            for candidate in predicate.find_all(exp.Column)
        ):
            continue
        rewritten = deepcopy(predicate)
        unsupported = False
        for candidate in list(rewritten.find_all(exp.Column)):
            if (
                candidate.name.casefold() == column.name.casefold()
                and _schema_has_column(schema, candidate, dialect)
            ):
                candidate.replace(variable)
            else:
                unsupported = True
        if not unsupported:
            constraints.append(rewritten)
    return constraints


def _schema_column_dtype(
    schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> Any:
    return _schema_column_metadata(schema.datatypes, column, dialect)


def _schema_column_metadata(
    metadata: Mapping[Any, Any],
    column: exp.Expression,
    dialect: str | None = None,
) -> Any:
    while isinstance(column, exp.Cast):
        column = column.this
    if not isinstance(column, exp.Column):
        return None
    for candidate, value in metadata.items():
        if not isinstance(candidate, exp.Column):
            continue
        if not _identifier_equal(candidate.this, column.this, dialect):
            continue
        requested_table = column.args.get("table")
        candidate_table = candidate.args.get("table")
        if (
            requested_table is not None
            and candidate_table is not None
            and not _identifier_equal(candidate_table, requested_table, dialect)
        ):
            continue
        return value
    return None


def _remap_column_metadata(
    metadata: Mapping[Any, Any],
    column_pairs: Sequence[Tuple[Any, Any]],
    dialect: str | None = None,
) -> Dict[Any, Any]:
    remapped: Dict[Any, Any] = {}
    for source, output in column_pairs:
        value = _schema_column_metadata(metadata, source, dialect)
        if value is not None:
            remapped[output] = value
    return remapped


def _schema_column_values(
    schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> Set[object]:
    values: Set[object] = set()
    visible = _visible_schema_column(schema, column, dialect)
    if visible is None:
        return values
    for row in schema.rows:
        try:
            values.add(concrete(row[visible]))
        except KeyError:
            continue
    return values


def _join_has_no_match(
    join: Join,
    cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    dialect: str | None = None,
) -> bool:
    inputs = _join_inputs(join)
    if inputs is None or not join.on_keys:
        return False
    left_schema = _schema_for(cache, inputs[0])
    right_schema = _schema_for(cache, inputs[1])
    left_key, right_key = join.on_keys[0]
    if not isinstance(left_key, exp.Column) or not isinstance(right_key, exp.Column):
        return False
    left_values = _schema_column_values(left_schema, left_key, dialect)
    right_values = _schema_column_values(right_schema, right_key, dialect)
    return any(value is not None and value not in right_values for value in left_values)


def _visible_schema_column(
    schema: DerivedSchema,
    column: exp.Column,
    dialect: str | None = None,
) -> exp.Column | None:
    for candidate in schema.columns:
        if not isinstance(candidate, exp.Column):
            continue
        if _identifier_equal(candidate.this, column.this, dialect):
            return candidate
    return None


def _non_matching_value(
    schema: DerivedSchema,
    column: exp.Column,
    forbidden: Set[object],
    seed: int,
    dialect: str | None = None,
) -> object | None:
    return _solve_schema_column_value(
        schema,
        column,
        avoid_values=tuple(forbidden),
        dialect=dialect,
        nonce=seed,
    )


def _solve_schema_column_value(
    schema: DerivedSchema,
    column: exp.Column,
    *,
    avoid_values: Sequence[object] = (),
    predicates: Sequence[exp.Expression] = (),
    dialect: str | None = None,
    nonce: int = 0,
) -> object | None:
    var = SolverVar(
        key=f"generated.{column.sql(dialect=dialect)}.{nonce}",
        dtype=_schema_column_dtype(schema, column, dialect),
        meta={"column": column.name},
    )
    constraints: List[exp.Expression] = [
        exp.NEQ(this=var, expression=_literal_for_value(value))
        for value in avoid_values
        if value is not None
    ]
    constraints.extend(
        _rewrite_join_key_predicates(
            predicates,
            schema,
            column,
            var,
            dialect,
        )
    )
    result = Solver(dialect=dialect or "sqlite", timeout_ms=2000).solve(
        Problem(constraints=constraints, variables={var})
    )
    if not result.sat:
        return None
    return result.assignments.get(var)


def _dtype_family(dtype: Any) -> str:
    text = dtype.sql().upper() if hasattr(dtype, "sql") else str(dtype).upper()
    if "INT" in text:
        return "integer"
    if any(token in text for token in ("REAL", "DOUBLE", "FLOAT", "NUM")):
        return "real"
    if "CHAR" in text or "TEXT" in text or "CLOB" in text:
        return "text"
    return "other"


def _column_eq_literal(column: exp.Column, value: object) -> exp.Expression:
    return exp.EQ(this=deepcopy(column), expression=_literal_for_value(value))


def _rank_expression_predicates(
    demands: Sequence[ExpressionDemand],
    rank: int,
) -> Tuple[exp.Expression, ...]:
    predicates: List[exp.Expression] = []
    for demand in demands:
        if demand.rank is not None and demand.rank != rank:
            continue
        predicates.extend(_expression_demand_predicates(demand))
    return tuple(predicates)


def _predicate_uses_only_base_columns(
    instance: Instance,
    table: exp.Table,
    predicate: exp.Expression,
) -> bool:
    base_columns = {name.casefold() for name in instance.column_names(table)}
    for column in predicate.find_all(exp.Column):
        if not isinstance(column.this, exp.Identifier):
            return False
        if column.name.casefold() not in base_columns:
            return False
    return True


def _apply_predicate_assignments(
    instance: Instance,
    alias_rows: Dict[str, Dict[str, object]],
    aliases: Mapping[str, exp.Table],
    predicate: exp.Expression,
    seed: int,
) -> None:
    for atom in _conjuncts(predicate):
        if isinstance(atom, exp.EQ):
            left = atom.this
            right = atom.expression
            if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
                _assign_column(instance, alias_rows, aliases, left, _literal_value(right))
            elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
                _assign_column(instance, alias_rows, aliases, right, _literal_value(left))
            elif isinstance(left, exp.Column) and isinstance(right, exp.Column):
                value = _solve_predicate_join_value(instance, aliases, left, right, seed)
                if value is not None:
                    _assign_column(instance, alias_rows, aliases, left, value)
                    _assign_column(instance, alias_rows, aliases, right, value)
        elif isinstance(atom, exp.Between):
            low = _numeric_value(atom.args.get("low"))
            high = _numeric_value(atom.args.get("high"))
            if isinstance(atom.this, exp.Column) and low is not None and high is not None:
                _assign_column(instance, alias_rows, aliases, atom.this, int((low + high) / 2))
        elif isinstance(atom, (exp.GT, exp.GTE, exp.LT, exp.LTE)):
            greater = isinstance(atom, (exp.GT, exp.GTE))
            if isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Literal):
                base = _numeric_value(atom.expression)
                if base is not None:
                    _assign_column(instance, alias_rows, aliases, atom.this, base + 1 if greater else base - 1)
        elif isinstance(atom, exp.Like):
            if isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Literal):
                pattern = str(atom.expression.this)
                value = pattern[:-1] + "_witness" if pattern.endswith("%") else pattern
                _assign_column(instance, alias_rows, aliases, atom.this, value)
        elif isinstance(atom, exp.Is):
            if isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Null):
                _assign_column(instance, alias_rows, aliases, atom.this, None)


def _conjuncts(expression: exp.Expression) -> Tuple[exp.Expression, ...]:
    if isinstance(expression, exp.And):
        return _conjuncts(expression.this) + _conjuncts(expression.expression)
    return (expression,)


def _assign_column(
    instance: Instance,
    alias_rows: Dict[str, Dict[str, object]],
    aliases: Mapping[str, exp.Table],
    column: exp.Column,
    value: object,
) -> None:
    alias = (column.table or "").casefold()
    if not alias and len(aliases) == 1:
        alias = next(iter(aliases))
    table = aliases.get(alias)
    if table is None or alias not in alias_rows:
        return
    try:
        alias_rows[alias][column.name] = _coerce_value_for_column(
            instance,
            table,
            column.name,
            value,
        )
    except CoercionError:
        return


def _first_assignable_columns(instance: Instance, table: exp.Table) -> Tuple[str, ...]:
    return tuple(
        column
        for column in instance.column_names(table)
        if not instance.is_unique(table, instance.resolve_column(table, column))
    )


def _solve_predicate_join_value(
    instance: Instance,
    aliases: Mapping[str, exp.Table],
    left: exp.Column,
    right: exp.Column,
    seed: int,
) -> object | None:
    left_table = _table_for_column(instance, aliases, left)
    right_table = _table_for_column(instance, aliases, right)
    left_var = SolverVar(
        key=f"predicate.left.{left.sql(dialect=instance.dialect)}.{seed}",
        dtype=(
            instance.get_column_type(left_table, left.name)
            if left_table is not None
            else None
        ),
        meta={"column": left.name},
    )
    right_var = SolverVar(
        key=f"predicate.right.{right.sql(dialect=instance.dialect)}.{seed}",
        dtype=(
            instance.get_column_type(right_table, right.name)
            if right_table is not None
            else None
        ),
        meta={"column": right.name},
    )
    result = Solver(dialect=instance.dialect, timeout_ms=2000).solve(
        Problem(
            constraints=[exp.EQ(this=left_var, expression=right_var)],
            variables={left_var, right_var},
        )
    )
    if not result.sat:
        return None
    return result.assignments.get(left_var, result.assignments.get(right_var))


def _solve_distinct_column_values(
    instance: Instance,
    table: exp.Table,
    column: str,
    count: int,
    *,
    nonce: int,
) -> Tuple[object, ...]:
    if count <= 0:
        return ()
    column_ident = instance.resolve_column(table, column)
    variables = tuple(
        SolverVar(
            key=f"distinct.{table.sql(dialect=instance.dialect)}.{column_ident.name}.{nonce}.{index}",
            dtype=instance.get_column_type(table, column_ident),
            meta={"table": table.name, "column": column_ident.name},
        )
        for index in range(count)
    )
    existing_values = {
        _row_value_dict(row).get(column_ident)
        for row in instance.get_rows(table)
    }
    constraints: List[exp.Expression] = []
    for index, var in enumerate(variables):
        constraints.extend(
            exp.NEQ(this=var, expression=_literal_for_value(value))
            for value in existing_values
            if value is not None
        )
        for other in variables[:index]:
            constraints.append(exp.NEQ(this=var, expression=other))
    result = Solver(dialect=instance.dialect, timeout_ms=2000).solve(
        Problem(constraints=constraints, variables=set(variables))
    )
    if not result.sat:
        return ()
    values = tuple(result.assignments.get(var) for var in variables)
    if any(value is None for value in values):
        return ()
    return values


def _table_for_column(
    instance: Instance,
    aliases: Mapping[str, exp.Table],
    column: exp.Column,
) -> exp.Table | None:
    alias = (column.table or "").casefold()
    if alias:
        return aliases.get(alias)
    matches = []
    for table in aliases.values():
        if column.name.casefold() in {name.casefold() for name in instance.column_names(table)}:
            matches.append(table)
    return matches[0] if len(matches) == 1 else None


def _coerce_value_for_column(
    instance: Instance,
    table: exp.Table,
    column_name: str,
    value: object,
) -> object:
    if value is None:
        return None
    dtype = instance.get_column_type(table, column_name)
    return coerce_literal_value(value, dtype, instance.dialect, for_equality=True)


def _literal_value(literal: exp.Literal) -> object:
    if literal.is_string:
        return literal.this
    text = str(literal.this)
    if "." not in text:
        return int(text)
    return float(text)


def _numeric_value(expression: exp.Expression | None) -> float | None:
    if isinstance(expression, exp.Literal) and not expression.is_string:
        try:
            return float(expression.this)
        except (TypeError, ValueError):
            return None
    return None


# ------------------------------------------------------------------
# Pipeline orchestrator
# ------------------------------------------------------------------

class EncodePipeline:
    """Orchestrates the concrete-enrichment execution of a query plan.

    Walks the plan DAG bottom-up (leaves → root), dispatching each
    :class:`Step` to its registered operator class.  Each operator
    ensures the :class:`Instance` has rows that cover the operator's
    semantics (both passing and failing).
    """

    _DEFAULT_REGISTRY: Dict[type, type] = {
        TableScan: ScanEncodeStep,
        Filter: FilterEncodeStep,
        Projection: ProjectEncodeStep,
        Join: JoinEncodeStep,
        Aggregate: AggregateEncodeStep,
        Sort: SortEncodeStep,
        Limit: LimitEncodeStep,
        Union: UnionEncodeStep,
        SubqueryAlias: SubqueryAliasEncodeStep,
        Values: ValuesEncodeStep,
        EmptyRelation: EmptyRelationEncodeStep,
        Unnest: UnnestEncodeStep,
        Repartition: RepartitionEncodeStep,
        Distinct: DistinctEncodeStep,
        Window: WindowEncodeStep,
    }

    def __init__(
        self,
        plan: Plan,
        instance: Optional[Instance] = None,
        bounds: Any = None,
    ) -> None:
        self.plan = plan
        self.instance = instance
        self.bounds = bounds
        self.dialect = plan.dialect
        self._allocator = RowAllocator(instance) if instance is not None else None
        self._operator_registry: Dict[type, type] = dict(self._DEFAULT_REGISTRY)
        self.schema_failure_reason = ""

    def register_operator(self, step_type: type, operator_class: type) -> None:
        self._operator_registry[step_type] = operator_class

    def _build_operator(self, step: Step) -> EncodeStep:
        for step_type, op_cls in self._operator_registry.items():
            if isinstance(step, step_type):
                return op_cls(step, instance=self.instance)
        raise ValueError(f"No operator registered for step type {type(step).__name__}")

    def _subquery_roots(self, step: Step) -> Tuple[Step, ...]:
        exprs: List[exp.Expression] = []
        if isinstance(step, Filter) and step.condition is not None:
            exprs.append(step.condition)
        if isinstance(step, Projection):
            exprs.extend(step.projections)
        if isinstance(step, Join):
            if step.condition is not None:
                exprs.append(step.condition)
            for left, right in step.on_keys:
                exprs.append(left)
                exprs.append(right)

        roots: list[Step] = []
        seen: set[str] = set()
        for expr in exprs:
            for ref in list(expr.find_all(ScalarSubqueryRef)):
                subquery_id = ref.subquery_id
                if subquery_id in seen:
                    continue
                inner_root = self.plan.scalar_subqueries.get(subquery_id)
                if inner_root is None:
                    raise RuntimeError(f"unsupported_scalar_subquery_ref:{subquery_id}")
                seen.add(subquery_id)
                roots.append(inner_root)
        return tuple(roots)

    def _subquery_roots_for_expression(self, expression: exp.Expression) -> Tuple[Step, ...]:
        roots: list[Step] = []
        for ref in list(expression.find_all(ScalarSubqueryRef)):
            inner_root = self.plan.scalar_subqueries.get(ref.subquery_id)
            if inner_root is None:
                raise RuntimeError(f"unsupported_scalar_subquery_ref:{ref.subquery_id}")
            roots.append(inner_root)
        return tuple(roots)

    def _ensure_scalar_expression_values(
        self,
        expression: exp.Expression,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        changed = False
        roots = self._subquery_roots_for_expression(expression)
        refs = list(expression.find_all(ScalarSubqueryRef))
        for ref, subquery_root in zip(refs, roots):
            schema = _schema_for(cache, subquery_root)
            parent_expr = _scalar_ref_parent_expr(expression, ref)
            if _scalar_schema_ready_for_predicate(schema, parent_expr):
                continue
            before = self._row_counts()
            self._lower_demand(subquery_root, self._root_demand(subquery_root), cache)
            if self._row_counts() != before:
                changed = True
        return not changed

    def _scalar_schemas_for_expression(
        self,
        expression: exp.Expression,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> Tuple[DerivedSchema, ...]:
        return tuple(
            _schema_for(cache, subquery_root)
            for subquery_root in self._subquery_roots_for_expression(expression)
        )

    def _filter_conditions_for_step(
        self,
        step: Step,
        schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> Tuple[exp.Expression, ...]:
        conditions: List[exp.Expression] = []
        seen: Set[int] = set()

        def visit(node: Step) -> None:
            if id(node) in seen:
                return
            seen.add(id(node))
            if isinstance(node, Filter) and node.condition is not None:
                condition = node.condition
                if condition.find(ScalarSubqueryRef):
                    if not self._ensure_scalar_expression_values(condition, cache):
                        return
                    condition = _expression_with_scalar_subqueries(
                        condition,
                        self._scalar_schemas_for_expression(condition, cache),
                        require_ready=True,
                    )
                    if condition.find(ScalarSubqueryRef):
                        return
                conditions.append(
                    _expression_in_schema_scope(condition, schema, self.dialect)
                )
            for dependency in node.dependencies:
                visit(dependency)

        visit(step)
        return tuple(conditions)

    def _ensure_scalar_subquery_values(
        self,
        step: Step,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if not isinstance(step, Filter) or step.condition is None:
            return True
        changed = False
        roots = self._subquery_roots(step)
        refs = list(step.condition.find_all(ScalarSubqueryRef))
        for ref, subquery_root in zip(refs, roots):
            schema = _schema_for(cache, subquery_root)
            parent_expr = _scalar_ref_parent_expr(step.condition, ref)
            if _scalar_schema_ready_for_predicate(schema, parent_expr):
                continue
            before = self._row_counts()
            self._lower_demand(subquery_root, self._root_demand(subquery_root), cache)
            if self._row_counts() != before:
                changed = True
        return not changed

    def forward(self) -> DerivedSchema:
        schema: DerivedSchema | None = None
        for _iteration in range(max(int(getattr(self.bounds, "max_iterations", 0) or 0) + 3, 3)):
            cache: Dict[Step, tuple[DerivedSchema, CoverageTreeNode]] = {}

            def process(node: Step, path: str) -> tuple[DerivedSchema, CoverageTreeNode]:
                if node in cache:
                    return cache[node]

                child_results = [
                    process(dependency, f"{path}.dep{index}")
                    for index, dependency in enumerate(node.dependencies)
                ]
                child_schemas = [schema for schema, _tree in child_results]
                child_trees = [tree for _schema, tree in child_results]

                subquery_results: list[tuple[DerivedSchema, CoverageTreeNode]] = []
                for index, subquery_root in enumerate(self._subquery_roots(node)):
                    subquery_schema, subquery_tree = process(
                        subquery_root,
                        f"{path}.subq{index}",
                    )
                    if self.instance is not None and not subquery_schema.rows:
                        before = self._row_counts()
                        self._lower_demand(
                            subquery_root,
                            self._root_demand(subquery_root),
                            cache,
                        )
                        if self._row_counts() != before:
                            for cached_step in _reachable_steps(subquery_root):
                                cache.pop(cached_step, None)
                            subquery_schema, subquery_tree = process(
                                subquery_root,
                                f"{path}.subq{index}",
                            )
                    subquery_schema = _mark_scalar_schema_single(subquery_schema)
                    subquery_results.append((subquery_schema, subquery_tree))

                op = self._build_operator(node)
                step_schema = op.forward(
                    *child_schemas,
                    *(schema for schema, _tree in subquery_results),
                )
                tree = CoverageTreeNode(
                    id=path,
                    step=node,
                    step_type=node.type_name,
                    targets=op.semantic_targets(path),
                    children=tuple(child_trees + [tree for _schema, tree in subquery_results]),
                )
                step_schema.coverage_tree = tree
                cache[node] = (step_schema, tree)
                return step_schema, tree

            schema = self._forward_from_root(self.plan.root, "root", process=process)
            if self.instance is None:
                return schema

            before = self._row_counts()
            root_demand = self._root_demand(self.plan.root)
            if (
                len(schema.rows) < root_demand.count
                or not _schema_satisfies_group_demands(schema, root_demand)
                or (
                    self.bounds is not None
                    and not _schema_satisfies_aggregate_argument_stress(
                        self.plan.root,
                        cache,
                        self.dialect,
                    )
                )
            ):
                self._lower_demand(
                    self.plan.root,
                    root_demand,
                    cache,
                )
                if self._row_counts() != before:
                    continue

            before = self._row_counts()
            self._materialize_coverage_demands(cache)
            if self._row_counts() != before:
                continue
            return schema

        return schema if schema is not None else self._forward_from_root(self.plan.root, "root")

    def _row_counts(self) -> Dict[exp.Table, int]:
        if self.instance is None:
            return {}
        return {
            table: len(self.instance.get_rows(table))
            for table in self.instance.schema.fk_safe_table_order()
        }

    def _root_demand(self, root: Step) -> SchemaDemand:
        count = _root_result_count(root, self.bounds)
        aggregate = _root_aggregate(root)
        group_demands: Tuple[GroupDemand, ...] = ()
        if aggregate is not None:
            rows_per_group = max(int(getattr(self.bounds, "rows_per_group", 1) or 1), 1)
            if aggregate.group:
                group_demands = _aggregate_group_demands(
                    aggregate,
                    group_count=max(int(getattr(self.bounds, "groups", 1) or 1), count),
                    rows_per_group=rows_per_group,
                    dialect=self.dialect,
                )
            else:
                group_demands = (
                    GroupDemand(
                        group_index=0,
                        row_count=rows_per_group,
                    ),
                )
        return SchemaDemand(count=count, group_demands=group_demands)

    def _next_seed(self) -> int:
        if not hasattr(self, "_demand_seed"):
            self._demand_seed = 0
        seed = self._demand_seed
        self._demand_seed += 1
        return seed

    def _lower_demand(
        self,
        node: Step,
        demand: SchemaDemand,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> None:
        if self.instance is None or self._allocator is None:
            return
        op = self._build_operator(node)
        child_schemas = tuple(_schema_for(cache, child) for child in node.dependencies)
        op.lower_demand(
            demand,
            _schema_for(cache, node),
            child_schemas,
            DemandContext(self, cache),
        )

    def _try_create_rows(
        self,
        rows_by_table: Mapping[exp.Table, Sequence[Mapping[str, object]]],
        *,
        reason: str,
    ) -> bool:
        if self.instance is None:
            return False
        token = self.instance.checkpoint()
        try:
            self.instance.create_rows(rows_by_table)
            return True
        except (DomainError, KeyError) as exc:
            self.instance.rollback(token)
            self.schema_failure_reason = reason
            logger.info("%s:%s", reason, exc)
            return False

    def _materialize_having_demand(
        self,
        step: Filter,
        aggregate: Aggregate,
        demand: SchemaDemand,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if step.condition is None:
            return False
        materialized_having = getattr(self, "_materialized_having_demands", set())
        identity = id(step)
        if identity in materialized_having:
            return False
        aggregate_schema = _schema_for(cache, aggregate)
        outcomes = _filter_outcomes(aggregate_schema, step.condition)
        group_count = max(int(getattr(self.bounds, "groups", 1) or 1), demand.count, 1)
        default_row_count = max(int(getattr(self.bounds, "rows_per_group", 1) or 1), 1)
        group_demands: List[GroupDemand] = list(demand.group_demands)
        if True not in outcomes:
            group_demands.extend(
                _having_group_demands(
                    step.condition,
                    aggregate,
                    group_count=group_count,
                    default_row_count=default_row_count,
                    pass_group=True,
                    dialect=self.dialect,
                )
            )
        if False not in outcomes:
            group_demands.extend(
                _having_group_demands(
                    step.condition,
                    aggregate,
                    group_count=1,
                    default_row_count=default_row_count,
                    pass_group=False,
                    start_index=group_count,
                    dialect=self.dialect,
                )
            )
        if not group_demands:
            return False
        self._lower_demand(
            aggregate,
            SchemaDemand(
                count=max(demand.count, sum(group.row_count for group in group_demands)),
                predicates=demand.predicates,
                order_keys=demand.order_keys,
                distinct=demand.distinct,
                group_demands=tuple(group_demands),
            ),
            cache,
        )
        materialized_having.add(identity)
        self._materialized_having_demands = materialized_having
        return True

    def _materialize_join_demand(
        self,
        join: Join,
        demand: SchemaDemand,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> None:
        inputs = _join_inputs(join)
        if inputs is None:
            for dependency in tuple(join.dependencies):
                self._lower_demand(dependency, demand, cache)
            return
        left_dep, right_dep = inputs
        left_schema = _schema_for(cache, left_dep)
        right_schema = _schema_for(cache, right_dep)
        if demand.group_demands:
            self._materialize_join_group_demands(
                join,
                demand,
                left_dep,
                left_schema,
                right_dep,
                right_schema,
                cache,
            )
            return
        used_join_values: Dict[Tuple[str, str], List[object]] = {}
        for rank in range(demand.count):
            left_predicates: List[exp.Expression] = []
            right_predicates: List[exp.Expression] = []
            left_context: List[exp.Expression] = []
            right_context: List[exp.Expression] = []
            if join.condition is not None:
                left_part, right_part = _split_predicate_by_schema(
                    join.condition,
                    left_schema,
                    right_schema,
                    self.dialect,
                )
                left_context.extend(left_part)
                right_context.extend(right_part)
            demand_predicates: List[exp.Expression] = []
            for predicate in demand.predicates:
                if predicate.find(ScalarSubqueryRef):
                    if not self._ensure_scalar_expression_values(predicate, cache):
                        return
                    predicate = _expression_with_scalar_subqueries(
                        predicate,
                        self._scalar_schemas_for_expression(predicate, cache),
                        require_ready=True,
                    )
                    if predicate.find(ScalarSubqueryRef):
                        continue
                demand_predicates.append(predicate)
            for predicate in demand_predicates:
                left_part, right_part = _split_predicate_by_schema(
                    predicate,
                    left_schema,
                    right_schema,
                    self.dialect,
                )
                left_context.extend(left_part)
                right_context.extend(right_part)
            left_context.extend(
                self._filter_conditions_for_step(left_dep, left_schema, cache)
            )
            right_context.extend(
                self._filter_conditions_for_step(right_dep, right_schema, cache)
            )
            for left, right in join.on_keys:
                if isinstance(left, exp.Column) and isinstance(right, exp.Column):
                    left_target = _schema_side_for_column(
                        left_schema,
                        right_schema,
                        left,
                        self.dialect,
                    )
                    right_target = _schema_side_for_column(
                        left_schema,
                        right_schema,
                        right,
                        self.dialect,
                    )
                    left_side_schema = left_schema if left_target == "left" else right_schema
                    right_side_schema = left_schema if right_target == "left" else right_schema
                    left_side_predicates = left_context if left_target == "left" else right_context
                    right_side_predicates = left_context if right_target == "left" else right_context
                    join_key = (
                        left.sql(dialect=self.dialect),
                        right.sql(dialect=self.dialect),
                    )
                    avoid_values: Sequence[object] = ()
                    if not _predicates_reference_join_key(
                        left_side_predicates,
                        left_side_schema,
                        left,
                        self.dialect,
                    ) and not _predicates_reference_join_key(
                        right_side_predicates,
                        right_side_schema,
                        right,
                        self.dialect,
                    ):
                        avoid_values = tuple(
                            list(used_join_values.get(join_key, ()))
                            + list(_schema_column_values(left_side_schema, left, self.dialect))
                            + list(_schema_column_values(right_side_schema, right, self.dialect))
                        )
                    value = _solve_join_key_value(
                        left_side_schema,
                        right_side_schema,
                        left,
                        right,
                        left_side_predicates,
                        right_side_predicates,
                        avoid_values,
                        self.dialect,
                    )
                    if value is None:
                        return
                    used_join_values.setdefault(join_key, []).append(value)
                    if left_target == "left":
                        left_predicates.append(_column_eq_literal(left, value))
                    elif left_target == "right":
                        right_predicates.append(_column_eq_literal(left, value))
                    if right_target == "left":
                        left_predicates.append(_column_eq_literal(right, value))
                    elif right_target == "right":
                        right_predicates.append(_column_eq_literal(right, value))
            left_predicates.extend(left_context)
            right_predicates.extend(right_context)
            left_order, right_order = _split_order_keys_by_schema(
                demand.order_keys,
                left_schema,
                right_schema,
                self.dialect,
            )
            child_demands = [
                (
                    left_dep,
                    left_schema,
                    SchemaDemand(
                        count=1,
                        predicates=tuple(left_predicates),
                        order_keys=left_order,
                        distinct=demand.distinct,
                    ),
                ),
                (
                    right_dep,
                    right_schema,
                    SchemaDemand(
                        count=1,
                        predicates=tuple(right_predicates),
                        order_keys=right_order,
                        distinct=demand.distinct,
                    ),
                ),
            ]
            for dependency, _schema, child_demand in sorted(
                child_demands,
                key=lambda item: _schema_table_order(self.instance, item[1]),
            ):
                self._lower_demand(dependency, child_demand, cache)

    def _materialize_join_group_demands(
        self,
        join: Join,
        demand: SchemaDemand,
        left_dep: Step,
        left_schema: DerivedSchema,
        right_dep: Step,
        right_schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> None:
        used_join_values: Dict[Tuple[str, str], List[object]] = {}
        for group in demand.group_demands:
            left_predicates: List[exp.Expression] = []
            right_predicates: List[exp.Expression] = []
            if join.condition is not None:
                left_part, right_part = _split_predicate_by_schema(
                    join.condition,
                    left_schema,
                    right_schema,
                    self.dialect,
                )
                left_predicates.extend(left_part)
                right_predicates.extend(right_part)
            for predicate in demand.predicates + group.row_predicates:
                left_part, right_part = _split_predicate_by_schema(
                    predicate,
                    left_schema,
                    right_schema,
                    self.dialect,
                )
                left_predicates.extend(left_part)
                right_predicates.extend(right_part)

            left_predicates.extend(
                self._filter_conditions_for_step(left_dep, left_schema, cache)
            )
            right_predicates.extend(
                self._filter_conditions_for_step(right_dep, right_schema, cache)
            )
            left_row_predicates_by_index, right_row_predicates_by_index = (
                _split_group_row_predicates_by_schema(
                    group,
                    left_schema,
                    right_schema,
                    self.dialect,
                )
            )

            left_group_keys: List[Tuple[exp.Expression, object]] = []
            right_group_keys: List[Tuple[exp.Expression, object]] = []
            for key, value in group.group_key_values:
                columns = _columns_including_self(key)
                if columns and all(
                    _schema_has_column(left_schema, column, self.dialect)
                    for column in columns
                ):
                    scoped_key = _expression_in_schema_scope(
                        key,
                        left_schema,
                        self.dialect,
                    )
                    scoped_value = _predicate_equality_value(
                        left_predicates,
                        scoped_key,
                        self.dialect,
                    )
                    if scoped_value is _MISSING:
                        scoped_value = value
                    scoped_predicate = exp.EQ(
                        this=deepcopy(scoped_key),
                        expression=_literal_for_value(scoped_value),
                    )
                    left_group_keys.append((scoped_key, scoped_value))
                    left_predicates.append(
                        _expression_in_schema_scope(
                            scoped_predicate,
                            left_schema,
                            self.dialect,
                        )
                    )
                elif columns and all(
                    _schema_has_column(right_schema, column, self.dialect)
                    for column in columns
                ):
                    scoped_key = _expression_in_schema_scope(
                        key,
                        right_schema,
                        self.dialect,
                    )
                    scoped_value = _predicate_equality_value(
                        right_predicates,
                        scoped_key,
                        self.dialect,
                    )
                    if scoped_value is _MISSING:
                        scoped_value = value
                    scoped_predicate = exp.EQ(
                        this=deepcopy(scoped_key),
                        expression=_literal_for_value(scoped_value),
                    )
                    right_group_keys.append((scoped_key, scoped_value))
                    right_predicates.append(
                        _expression_in_schema_scope(
                            scoped_predicate,
                            right_schema,
                            self.dialect,
                        )
                    )
            for left, right in join.on_keys:
                if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
                    continue
                left_target = _schema_side_for_column(
                    left_schema,
                    right_schema,
                    left,
                    self.dialect,
                )
                right_target = _schema_side_for_column(
                    left_schema,
                    right_schema,
                    right,
                    self.dialect,
                )
                left_side_schema = left_schema if left_target == "left" else right_schema
                right_side_schema = left_schema if right_target == "left" else right_schema
                join_key = (
                    left.sql(dialect=self.dialect),
                    right.sql(dialect=self.dialect),
                )
                avoid_values = tuple(
                    list(used_join_values.get(join_key, ()))
                    + list(_schema_column_values(left_side_schema, left, self.dialect))
                    + list(_schema_column_values(right_side_schema, right, self.dialect))
                )
                value = _solve_join_key_value(
                    left_side_schema,
                    right_side_schema,
                    left,
                    right,
                    left_predicates if left_target == "left" else right_predicates,
                    left_predicates if right_target == "left" else right_predicates,
                    avoid_values,
                    self.dialect,
                )
                if value is None:
                    continue
                used_join_values.setdefault(join_key, []).append(value)
                if left_target == "left":
                    left_predicates.append(_column_eq_literal(left, value))
                elif left_target == "right":
                    right_predicates.append(_column_eq_literal(left, value))
                if right_target == "left":
                    left_predicates.append(_column_eq_literal(right, value))
                elif right_target == "right":
                    right_predicates.append(_column_eq_literal(right, value))
            left_order, right_order = _split_order_keys_by_schema(
                demand.order_keys,
                left_schema,
                right_schema,
                self.dialect,
            )
            left_count = 1 if left_group_keys and not right_group_keys else group.row_count
            right_count = 1 if right_group_keys and not left_group_keys else group.row_count
            child_demands = [
                (
                    left_dep,
                    left_schema,
                    SchemaDemand(
                        count=max(left_count, 1),
                        predicates=tuple(left_predicates),
                        order_keys=left_order,
                        distinct=demand.distinct,
                        group_demands=(
                            GroupDemand(
                                group_index=group.group_index,
                                row_count=max(left_count, 1),
                                group_key_values=tuple(left_group_keys),
                                row_predicates=(),
                                row_predicates_by_index=left_row_predicates_by_index,
                            ),
                        ),
                    ),
                ),
                (
                    right_dep,
                    right_schema,
                    SchemaDemand(
                        count=max(right_count, 1),
                        predicates=tuple(right_predicates),
                        order_keys=right_order,
                        distinct=demand.distinct,
                        group_demands=(
                            GroupDemand(
                                group_index=group.group_index,
                                row_count=max(right_count, 1),
                                group_key_values=tuple(right_group_keys),
                                row_predicates=(),
                                row_predicates_by_index=right_row_predicates_by_index,
                            ),
                        ),
                    ),
                ),
            ]
            for dependency, _schema, child_demand in sorted(
                child_demands,
                key=lambda item: _schema_table_order(self.instance, item[1]),
            ):
                self._lower_demand(dependency, child_demand, cache)

    def _materialize_table_demand(
        self,
        schema: DerivedSchema,
        table: exp.Table,
        demand: SchemaDemand,
    ) -> None:
        row_specs: List[Dict[str, object]] = []
        row_predicates: List[Tuple[exp.Expression, ...]] = []
        alias = _schema_alias(schema)
        aliases = {alias: table}
        if demand.group_demands:
            for group in demand.group_demands:
                for row_index in range(max(group.row_count, 1)):
                    alias_rows = {alias: {}}
                    row_seed = self._seed_alias_rows(alias_rows, aliases)
                    group_key_predicates = _group_key_predicates_for_table(
                        self.instance,
                        table,
                        group,
                    )
                    predicates = (
                        demand.predicates
                        + _row_predicates_for_group_row(group, row_index)
                        + group_key_predicates
                        + _rank_expression_predicates(
                            demand.expression_demands,
                            group.group_index,
                        )
                    )
                    predicates = tuple(
                        predicate
                        for predicate in predicates
                        if _predicate_uses_only_base_columns(
                            self.instance,
                            table,
                            predicate,
                        )
                    )
                    for predicate in predicates:
                        _apply_predicate_assignments(
                            self.instance,
                            alias_rows,
                            aliases,
                            predicate,
                            row_seed,
                        )
                    row_specs.append(alias_rows[alias])
                    row_predicates.append(tuple(predicates))
            if row_specs:
                solved_rows = _solve_table_rows(
                    self.instance,
                    table,
                    row_specs,
                    row_predicates,
                    dialect=self.dialect,
                    expression_demands=demand.expression_demands,
                )
                if solved_rows:
                    table_name = self.instance.resolve_table(table).name
                    self._try_create_rows(
                        _rows_with_required_fk_parents(self.instance, table, solved_rows),
                        reason=f"schema_constraint_materialization_failed:{table_name}",
                    )
                elif getattr(_solve_table_rows, "schema_failure_reason", ""):
                    self.schema_failure_reason = _solve_table_rows.schema_failure_reason
            return
        distinct_column: str | None = None
        distinct_values: Tuple[object, ...] = ()
        if demand.distinct:
            distinct_columns = _first_assignable_columns(self.instance, table)
            if distinct_columns:
                distinct_column = distinct_columns[0]
                distinct_values = _solve_distinct_column_values(
                    self.instance,
                    table,
                    distinct_column,
                    demand.count,
                    nonce=self._next_seed(),
                )
        for rank in range(demand.count):
            alias_rows = {alias: {}}
            row_seed = self._seed_alias_rows(alias_rows, aliases)
            predicates = demand.predicates + _rank_expression_predicates(
                demand.expression_demands,
                rank,
            )
            predicates = tuple(
                predicate
                for predicate in predicates
                if _predicate_uses_only_base_columns(
                    self.instance,
                    table,
                    predicate,
                )
            )
            for predicate in predicates:
                _apply_predicate_assignments(
                    self.instance,
                    alias_rows,
                    aliases,
                    predicate,
                    row_seed,
                )
            if (
                demand.distinct
                and distinct_column is not None
                and rank < len(distinct_values)
            ):
                alias_rows[alias].setdefault(distinct_column, distinct_values[rank])
            row_specs.append(alias_rows[alias])
            row_predicates.append(tuple(predicates))
        if row_specs:
            solved_rows = _solve_table_rows(
                self.instance,
                table,
                row_specs,
                row_predicates,
                dialect=self.dialect,
                expression_demands=demand.expression_demands,
            )
            if solved_rows:
                table_name = self.instance.resolve_table(table).name
                self._try_create_rows(
                    _rows_with_required_fk_parents(self.instance, table, solved_rows),
                    reason=f"schema_constraint_materialization_failed:{table_name}",
                )
            elif getattr(_solve_table_rows, "schema_failure_reason", ""):
                self.schema_failure_reason = _solve_table_rows.schema_failure_reason

    def _materialize_coverage_demands(
        self,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        changed = False
        for step, (schema, _tree) in tuple(cache.items()):
            if isinstance(step, Filter) and step.condition is not None:
                changed = self._materialize_filter_coverage_demand(step, cache) or changed
            elif isinstance(step, Projection):
                changed = self._materialize_projection_case_coverage_demand(step, schema, cache) or changed
            elif isinstance(step, Aggregate):
                changed = self._materialize_aggregate_coverage_demand(step, cache) or changed
            elif isinstance(step, Join):
                changed = self._materialize_join_coverage_demand(step, schema, cache) or changed
        return changed

    def _materialize_projection_case_coverage_demand(
        self,
        step: Projection,
        schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        case_demands = _projection_case_expression_demands(
            step,
            schema,
            self.dialect,
        )
        if not case_demands:
            return False
        existing_values = {
            concrete(value)
            for row in schema.rows
            for value in row.column_values.values()
        }
        missing_demands = tuple(
            demand
            for demand in case_demands
            if demand.value not in existing_values
        )
        if not missing_demands:
            return False
        self._lower_demand(
            _single_dependency(step),
            SchemaDemand(count=1, expression_demands=missing_demands),
            cache,
        )
        return True

    def _materialize_filter_coverage_demand(
        self,
        step: Filter,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if step.condition.find(ScalarSubqueryRef):
            return False
        child = _single_dependency(step)
        if isinstance(child, Aggregate):
            return self._materialize_having_demand(
                step,
                child,
                SchemaDemand(count=1),
                cache,
            )
        child_schema = _schema_for(cache, child)
        outcomes = _filter_outcomes(child_schema, step.condition)
        demands: List[SchemaDemand] = []
        if True not in outcomes:
            demands.append(SchemaDemand(count=1, predicates=(step.condition,)))
        if False not in outcomes:
            false_condition = _false_condition(step.condition)
            if false_condition is not None:
                demands.append(SchemaDemand(count=1, predicates=(false_condition,)))
        if None not in outcomes:
            null_condition = _null_condition_for_schema(
                step.condition,
                child_schema,
                self.dialect,
            )
            if null_condition is not None:
                demands.append(SchemaDemand(count=1, predicates=(null_condition,)))
        for demand in demands:
            self._lower_demand(child, demand, cache)
        return bool(demands)

    def _materialize_aggregate_coverage_demand(
        self,
        step: Aggregate,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        child = _single_dependency(step)
        child_schema = _schema_for(cache, child)
        if (
            not step.group
            and not child_schema.rows
            and any(
                isinstance(
                    expression.this if isinstance(expression, exp.Alias) else expression,
                    (exp.Sum, exp.Avg, exp.Min, exp.Max),
                )
                for expression in step.aggregations
            )
        ):
            self._lower_demand(
                child,
                SchemaDemand(
                    count=max(int(getattr(self.bounds, "rows_per_group", 1) or 1), 1),
                ),
                cache,
            )
            return True
        predicates = _aggregate_case_predicates(step)
        if not predicates:
            return False
        changed = False
        for predicate in predicates:
            scoped = _expression_in_schema_scope(predicate, child_schema, self.dialect)
            if True in _filter_outcomes(child_schema, scoped):
                continue
            self._lower_demand(
                child,
                SchemaDemand(count=1, predicates=(scoped,)),
                cache,
            )
            changed = True
        return changed

    def _materialize_join_coverage_demand(
        self,
        step: Join,
        schema: DerivedSchema,
        cache: Mapping[Step, tuple[DerivedSchema, CoverageTreeNode]],
    ) -> bool:
        if schema.rows and _join_has_no_match(step, cache, self.dialect):
            return False
        inputs = _join_inputs(step)
        if inputs is None or not step.on_keys:
            return False
        left_dep, right_dep = inputs
        left_schema = _schema_for(cache, left_dep)
        right_schema = _schema_for(cache, right_dep)
        left_key, right_key = step.on_keys[0]
        if not isinstance(left_key, exp.Column) or not isinstance(right_key, exp.Column):
            return False
        if _schema_side_for_column(
            left_schema,
            right_schema,
            left_key,
            self.dialect,
        ) == "right":
            target_dep, target_schema, target_key = right_dep, right_schema, left_key
            other_schema, other_key = left_schema, right_key
        else:
            target_dep, target_schema, target_key = left_dep, left_schema, left_key
            other_schema, other_key = right_schema, right_key
        forbidden = _schema_column_values(other_schema, other_key, self.dialect)
        value = _non_matching_value(
            target_schema,
            target_key,
            forbidden,
            self._next_seed(),
            self.dialect,
        )
        if value is None:
            return False
        self._lower_demand(
            target_dep,
            SchemaDemand(count=1, predicates=(_column_eq_literal(target_key, value),)),
            cache,
        )
        return True

    def _seed_alias_rows(
        self,
        alias_rows: Dict[str, Dict[str, object]],
        aliases: Mapping[str, exp.Table],
    ) -> int:
        seed = 0
        for alias, table in aliases.items():
            index = self._allocator.allocate(table)
            seed = max(seed, index)
        return seed

    def _forward_from_root(
        self,
        root: Step,
        path: str,
        process: Optional[Any] = None,
    ) -> DerivedSchema:
        if process is None:
            original_root = self.plan.root
            self.plan.root = root
            try:
                return self.forward()
            finally:
                self.plan.root = original_root
        schema, _tree = process(root, path)
        return schema


def pipeline_ordered_steps(plan: Plan) -> tuple[Step, ...]:
    ordered: list[Step] = []
    seen: Set[Step] = set()

    def visit(node: Step) -> None:
        if node in seen:
            return
        seen.add(node)
        for dependency in node.dependencies:
            visit(dependency)
        ordered.append(node)

    visit(plan.root)
    return tuple(ordered)


def _reachable_steps(root: Step) -> tuple[Step, ...]:
    ordered: list[Step] = []
    seen: Set[Step] = set()

    def visit(node: Step) -> None:
        if node in seen:
            return
        seen.add(node)
        ordered.append(node)
        for dependency in node.dependencies:
            visit(dependency)

    visit(root)
    return tuple(ordered)
