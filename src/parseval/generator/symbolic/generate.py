from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

import sqlglot
from sqlglot import exp

from parseval.generator.bounds import BmcBounds
from parseval.generator.order import sql_order_key
from parseval.generator.coverage import (
    CoverageTreeNode,
    CoverageObligation,
    SemanticCoverageRecorder,
    _coverage_ratio,
)
from parseval.generator.symbolic_resolution import (
    join_alias_tables,
    join_order_keys_supported,
    leaf_table_scans,
    order_expression_table,
    resolved_order_expressions,
    same_identifier,
    single_dependency_join,
    single_leaf_scan,
    storage_table_for_join_column,
)
from parseval.domain.exceptions import ConstraintViolationError, UniqueConflictError
from parseval.instance import Instance
from parseval.plan.context import DerivedSchema
from parseval.plan.explain import (
    Aggregate,
    Filter,
    Join,
    Limit,
    Plan,
    Sort,
    Step,
    TableScan,
)
from parseval.plan.rex import Environment, Variable, concrete
from parseval.solver.types import Problem, SolverVar

from .operator import (
    AggregateEncodeStep,
    EncodePipeline,
    FilterEncodeStep,
    ScanEncodeStep,
    _database_constraints_for_solver,
    pipeline_ordered_steps,
)
from . import values as v


@dataclass(frozen=True)
class GenerationState:
    status: str
    reason: str = ""
    bounds: BmcBounds = field(default_factory=BmcBounds)
    create_rows: Mapping[exp.Table, Sequence[Mapping[exp.Identifier, object]]] = field(
        default_factory=dict
    )
    problem: Problem | None = None
    assignments: Mapping[SolverVar, object] = field(default_factory=dict)
    root_schema: DerivedSchema | None = None
    obligations: tuple[CoverageObligation, ...] = ()
    coverage_tree: CoverageTreeNode | None = None
    coverage_ratio: float = 0.0


def _ensure_scalar_subquery_rows(
    plan: Plan,
    instance: Instance,
    bounds: BmcBounds,
) -> None:
    for step in pipeline_ordered_steps(plan):
        if not isinstance(step, Filter) or step.condition is None:
            continue
        for request in _scalar_subquery_comparisons(step.condition):
            _seed_scalar_subquery_filter(plan, instance, bounds, step, request)


def _seed_scalar_subquery_filter(
    plan: Plan,
    instance: Instance,
    bounds: BmcBounds,
    step: Filter,
    request: tuple[exp.Expression, exp.Expression, exp.Subquery],
) -> None:
    left, right, subquery = request
    if not isinstance(subquery.this, Step):
        return
    inner_plan = Plan(subquery.this, sql="", dialect=plan.dialect)
    _ensure_base_rows(inner_plan, instance, bounds=bounds)
    _ensure_join_match_rows(inner_plan, instance)
    _ensure_aggregate_input_rows(inner_plan, instance, bounds=bounds)
    inner_schema = EncodePipeline(inner_plan, instance).forward()
    if not inner_schema.rows:
        _ensure_topk_rows(inner_plan, instance, bounds)
        inner_schema = EncodePipeline(inner_plan, instance).forward()
    if not inner_schema.rows:
        return
    scalar_value = _first_scalar_value(inner_schema)
    if scalar_value is None:
        return
    if _comparison_with_scalar(left, right, subquery, scalar_value) is None:
        return

    scan = single_leaf_scan(step)
    if scan is None:
        return
    table = scan.table
    scalar_condition = _expression_with_scalar_subquery(
        step.condition,
        subquery,
        scalar_value,
    )
    constraints = FilterEncodeStep.decompose_conjuncts(scalar_condition)
    if v.has_row_satisfying(instance, table, constraints):
        return
    row = FilterEncodeStep._solve_row(
        instance,
        table,
        constraints,
        dialect=plan.dialect,
    )
    if row is not None:
        instance.create_rows({table: [row]})


def _materialize_row(row) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for col_ident, val in row.column_values.items():
        if isinstance(val, SolverVar):
            continue
        name = col_ident.name if hasattr(col_ident, "name") else str(col_ident)
        if isinstance(val, Variable):
            result[name] = val.concrete
        else:
            result[name] = val
    return result

def generate(
    plan: Plan,
    instance: Instance,
    *,
    query: str | None = None,
    bounds: BmcBounds | None = None,
) -> Instance:
    """Generate semantic witnesses for *plan* through EncodePipeline.

    ``Instance`` is the committed row source. Solver output is appended to
    ``instance`` and the same object is returned.
    """
    current_bounds = _bounds_for_plan(plan, bounds or BmcBounds())
    sql = query or plan.sql
    if not sql:
        schema = _unknown_result(plan, current_bounds, "missing_query_sql")
        _attach_generation_state(
            instance,
            schema=schema,
            obligations=tuple(schema.obligations),
        )
        return instance

    before_counts = _row_counts(instance)
    schema = EncodePipeline(plan, instance, bounds=current_bounds).forward()
    tree = schema.coverage_tree
    if tree is None:
        schema = EncodePipeline(plan, instance, bounds=current_bounds).forward()
        tree = schema.coverage_tree
    evaluated_tree = (
        SemanticCoverageRecorder(plan, instance).evaluate_tree(
            tree,
            _having_filter_statuses(plan, instance),
        )
        if tree is not None
        else None
    )
    obligations = list(evaluated_tree.iter_obligations()) if evaluated_tree is not None else []

    evidence = _evidence_for_obligations(obligations, schema)
    schema.obligations.extend(obligations)
    schema.evidence.update(evidence)
    schema.status = "sat"
    schema.reason = ""
    schema.create_rows = _created_rows_since(instance, before_counts)
    schema.assignments = _assignments_for_created_rows(instance, schema.create_rows)
    schema.problem = _problem_for_schema(
        schema,
        instance,
        schema.create_rows,
        schema.assignments,
    )
    schema.coverage_ratio = _coverage_ratio(obligations)
    schema.coverage_tree = evaluated_tree
    schema.bounds = current_bounds
    _attach_generation_state(instance, schema=schema, obligations=tuple(obligations))
    return instance


def _ensure_base_rows(
    plan: Plan,
    instance: Instance,
    *,
    bounds: BmcBounds,
    skip_tables: set[str] | None = None,
) -> None:
    skip_tables = skip_tables or set()
    for step in pipeline_ordered_steps(plan):
        if not isinstance(step, TableScan):
            continue
        if step.table.name in skip_tables:
            continue
        missing = max(bounds.table_rows - len(instance.get_rows(step.table)), 0)
        if missing:
            instance.create_rows({step.table: [{} for _ in range(missing)]})


def _bounds_for_plan(plan: Plan, bounds: BmcBounds) -> BmcBounds:
    required_rows = bounds.table_rows
    for step in pipeline_ordered_steps(plan):
        if isinstance(step, (Limit, Sort)) and step.fetch is not None:
            offset = getattr(step, "offset", 0) or 0
            required_rows = max(required_rows, offset + step.fetch)
    adjusted, _ = bounds.raise_table_rows(required_rows)
    return adjusted


def _ensure_topk_rows(plan: Plan, instance: Instance, bounds: BmcBounds) -> None:
    for sort in (step for step in pipeline_ordered_steps(plan) if isinstance(step, Sort)):
        spec = _topk_spec(plan, instance, sort, bounds)
        if spec is None:
            continue
        rows_by_table = {table: rows for table, rows in spec.items() if rows}
        if not rows_by_table:
            continue
        try:
            instance.create_rows(rows_by_table)
        except (ConstraintViolationError, UniqueConflictError):
            continue


def _topk_spec(
    plan: Plan,
    instance: Instance,
    sort: Sort,
    bounds: BmcBounds,
) -> Mapping[exp.Table, list[dict[str, object]]] | None:
    if not sort.key:
        return None
    scan = single_leaf_scan(sort)
    if scan is None:
        return _join_topk_spec(plan, instance, sort, bounds)
    order_expressions = resolved_order_expressions(instance, scan.table, sort)
    if order_expressions is None:
        return None
    offset, limit = _sort_window(plan, sort)
    selected_rows = max(offset + limit, 1)
    competitor_rows = max(bounds.order_competitors, 0)
    tie_rows = 1 if competitor_rows and offset == 0 and limit > 0 else 0
    row_count = selected_rows + competitor_rows + tie_rows
    existing_count = len(instance.get_rows(scan.table))
    if _topk_rows_observed(instance, scan.table, sort, offset, limit, row_count):
        return None

    descending = _first_order_descending(sort)
    rows: list[dict[str, object]] = []
    for rank in range(row_count):
        index = existing_count + rank
        key_value = _rank_key_value(rank, descending)
        if tie_rows and rank == 1:
            key_value = _rank_key_value(0, descending)
        row: dict[str, object] = {}
        _assign_identity_values(instance, scan.table, row, index)
        for expr in order_expressions:
            _assign_order_expression_values(instance, scan.table, expr, row, key_value)
        rows.append(row)
    return {scan.table: rows}


def _join_topk_spec(
    plan: Plan,
    instance: Instance,
    sort: Sort,
    bounds: BmcBounds,
) -> Mapping[exp.Table, list[dict[str, object]]] | None:
    join = single_dependency_join(sort)
    if join is None or len(join.on_keys) != 1 or join.condition is not None:
        return None
    left_key, right_key = join.on_keys[0]
    if not isinstance(left_key, exp.Column) or not isinstance(right_key, exp.Column):
        return None
    left_table = storage_table_for_join_column(join, left_key, instance)
    right_table = storage_table_for_join_column(join, right_key, instance)
    if left_table is None or right_table is None:
        return None
    alias_tables = dict(join_alias_tables(join))
    if not join_order_keys_supported(instance, alias_tables, sort.key):
        return None

    offset, limit = _sort_window(plan, sort)
    competitor_rows = max(bounds.order_competitors, 0)
    tie_rows = 1 if competitor_rows and offset == 0 and limit > 0 else 0
    row_count = max(offset + limit, 1) + competitor_rows + tie_rows
    descending = _first_order_descending(sort)
    left_existing = len(instance.get_rows(left_table))
    right_existing = len(instance.get_rows(right_table))
    left_rows: list[dict[str, object]] = []
    right_rows: list[dict[str, object]] = []
    for rank in range(row_count):
        key_value = _rank_key_value(rank, descending)
        if tie_rows and rank == 1:
            key_value = _rank_key_value(0, descending)
        join_value = _join_key_value(instance, left_table, left_key, left_existing + rank)
        left_row: dict[str, object] = {}
        right_row: dict[str, object] = {}
        _assign_identity_values(instance, left_table, left_row, left_existing + rank)
        _assign_identity_values(instance, right_table, right_row, right_existing + rank)
        left_row[instance.resolve_column(left_table, left_key).name] = join_value
        right_row[instance.resolve_column(right_table, right_key).name] = join_value
        for ordered in sort.key:
            expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            target_table = order_expression_table(instance, alias_tables, expr)
            if target_table is None:
                return None
            _assign_order_expression_values(
                instance,
                target_table,
                expr,
                left_row if target_table == left_table else right_row,
                key_value,
            )
        left_rows.append(left_row)
        right_rows.append(right_row)
    return {left_table: left_rows, right_table: right_rows}


def _topk_rows_observed(
    instance: Instance,
    table: exp.Table,
    sort: Sort,
    offset: int,
    limit: int,
    required_rows: int,
) -> bool:
    rows = [
        {
            key.name if hasattr(key, "name") else str(key): value
            for key, value in Instance._row_value_dict(row).items()
        }
        for row in instance.get_rows(table)
    ]
    if len(rows) < required_rows:
        return False
    try:
        for index, ordered in reversed(tuple(enumerate(sort.key))):
            expr = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            descending = isinstance(ordered, exp.Ordered) and bool(ordered.args.get("desc"))
            rows.sort(
                key=lambda row: sql_order_key(
                    concrete(
                        expr,
                        Environment.from_row(
                            {
                                exp.to_identifier(name): value
                                for name, value in row.items()
                            }
                        ),
                    )
                ),
                reverse=descending,
            )
    except Exception:
        return False
    return bool(rows[offset : offset + limit])


def _sort_window(plan: Plan, sort: Sort) -> tuple[int, int]:
    offset = 0
    limit = sort.fetch or 1
    for step in pipeline_ordered_steps(plan):
        if isinstance(step, Limit) and sort in step.dependencies:
            offset = step.offset or 0
            limit = step.fetch or max(limit - offset, 1)
            break
    return offset, max(limit, 1)


def _first_order_descending(sort: Sort) -> bool:
    ordered = sort.key[0]
    return isinstance(ordered, exp.Ordered) and bool(ordered.args.get("desc"))


def _rank_key_value(rank: int, descending: bool) -> int:
    return 1000 - rank if descending else -1000 + rank


def _assign_identity_values(
    instance: Instance,
    table: exp.Table,
    row: dict[str, object],
    index: int,
) -> None:
    for column in instance.column_names(table):
        try:
            col_ident = instance.resolve_column(table, column)
        except KeyError:
            continue
        if instance.is_unique(table, col_ident):
            row[col_ident.name] = 100000 + index


def _assign_order_expression_values(
    instance: Instance,
    table: exp.Table,
    expression: exp.Expression,
    row: dict[str, object],
    key_value: int,
) -> None:
    if isinstance(expression, exp.Column):
        row[instance.resolve_column(table, expression.name).name] = key_value
        return
    if isinstance(expression, exp.Div):
        numerator = expression.this
        denominator = expression.expression
        if isinstance(numerator, exp.Column):
            row[instance.resolve_column(table, numerator.name).name] = key_value * 10
        if isinstance(denominator, exp.Column):
            row[instance.resolve_column(table, denominator.name).name] = 10
        return
    columns = tuple(expression.find_all(exp.Column))
    for column_index, column in enumerate(columns):
        row[instance.resolve_column(table, column.name).name] = (
            key_value if column_index == 0 else 1
        )


def _join_key_value(
    instance: Instance,
    table: exp.Table,
    column: exp.Column,
    index: int,
) -> object:
    dtype = instance.get_column_type(table, column.name).sql(dialect=instance.dialect).upper()
    if "INT" in dtype or "REAL" in dtype or "DOUBLE" in dtype or "FLOAT" in dtype:
        return 200000 + index
    return f"topk_{index}"


def _scalar_subquery_comparisons(
    expression: exp.Expression,
) -> tuple[tuple[exp.Expression, exp.Expression, exp.Subquery], ...]:
    comparisons: list[tuple[exp.Expression, exp.Expression, exp.Subquery]] = []
    supported = (exp.EQ, exp.LT, exp.LTE, exp.GT, exp.GTE)
    for atom in FilterEncodeStep.decompose_conjuncts(expression):
        if not isinstance(atom, supported):
            continue
        left = atom.this
        right = atom.expression
        if isinstance(left, exp.Subquery):
            comparisons.append((left, right, left))
        elif isinstance(right, exp.Subquery):
            comparisons.append((left, right, right))
    return tuple(comparisons)


def _comparison_with_scalar(
    left: exp.Expression,
    right: exp.Expression,
    subquery: exp.Subquery,
    scalar_value: object,
) -> exp.Expression | None:
    scalar = _literal_for_value(scalar_value)
    if left is subquery:
        replacement_left = scalar
        replacement_right = deepcopy(right)
    elif right is subquery:
        replacement_left = deepcopy(left)
        replacement_right = scalar
    else:
        return None
    comparison_type = type(left.parent) if left.parent is right.parent else None
    if comparison_type not in (exp.EQ, exp.LT, exp.LTE, exp.GT, exp.GTE):
        return None
    return comparison_type(this=replacement_left, expression=replacement_right)


def _expression_with_scalar_subquery(
    expression: exp.Expression,
    subquery: exp.Subquery,
    scalar_value: object,
) -> exp.Expression:
    rewritten = deepcopy(expression)
    scalar = _literal_for_value(scalar_value)
    for candidate in list(rewritten.find_all(exp.Subquery)):
        if candidate is subquery or candidate.this is subquery.this:
            candidate.replace(deepcopy(scalar))
    return rewritten


def _literal_for_value(value: object) -> exp.Expression:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, (int, float)):
        return exp.Literal.number(str(value))
    return exp.Literal.string(str(value))


def _first_scalar_value(schema: DerivedSchema) -> object | None:
    if not schema.rows:
        return None
    row = schema.rows[0]
    if not row.column_values:
        return None
    value = next(iter(row.column_values.values()))
    return value.concrete if isinstance(value, Variable) else value


def _ensure_aggregate_input_rows(
    plan: Plan,
    instance: Instance,
    *,
    bounds: BmcBounds,
    cardinality_only: bool = False,
) -> None:
    having_aggregates = {
        aggregate_step for _filter_step, aggregate_step in _having_filters(plan)
    }
    for aggregate_step in (
        step for step in pipeline_ordered_steps(plan) if isinstance(step, Aggregate)
    ):
        if aggregate_step in having_aggregates and aggregate_step.group:
            continue
        if cardinality_only and not _is_simple_count_cardinality_aggregate(aggregate_step):
            continue
        table = _single_input_table(aggregate_step)
        if table is None:
            continue
        material_count = _aggregate_material_input_count(aggregate_step, table, instance)
        required_count = max(1, bounds.rows_per_group)
        if material_count >= required_count:
            continue
        for _ in range(required_count - material_count):
            row = _aggregate_input_row(aggregate_step, table, instance)
            try:
                instance.create_rows({table: [row]})
            except (ConstraintViolationError, UniqueConflictError):
                break


def _aggregate_material_input_count(
    aggregate_step: Aggregate,
    table: exp.Table,
    instance: Instance,
) -> int:
    required = _aggregate_input_column_names(aggregate_step)
    if not required:
        return len(instance.get_rows(table))
    count = 0
    for row in instance.get_rows(table):
        values = Instance._row_value_dict(row)
        if all(values.get(exp.to_identifier(column)) is not None for column in required):
            count += 1
    return count


def _is_simple_count_cardinality_aggregate(aggregate_step: Aggregate) -> bool:
    if aggregate_step.group:
        return False
    return any(
        isinstance(
            aggregate.this if isinstance(aggregate, exp.Alias) else aggregate,
            exp.Count,
        )
        for aggregate in aggregate_step.aggregations
    )


def _aggregate_input_row(
    aggregate_step: Aggregate,
    table: exp.Table,
    instance: Instance,
) -> dict[str, object]:
    row: dict[str, object] = {}
    _assign_identity_values(instance, table, row, len(instance.get_rows(table)))
    row.update(_base_group_row(aggregate_step, instance, table, prefix="__scalar_group"))
    for aggregate in aggregate_step.aggregations:
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        if isinstance(expression, exp.Count):
            continue
        if isinstance(expression, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
            _assign_input_columns(expression.this, [row], 10)
    return row


def _aggregate_input_column_names(aggregate_step: Aggregate) -> set[str]:
    columns: set[str] = set()
    for aggregate in aggregate_step.aggregations:
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        if isinstance(expression, exp.Count):
            continue
        if isinstance(expression, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
            for column in expression.find_all(exp.Column):
                columns.add(column.name)
    return columns


def _ensure_join_match_rows(plan: Plan, instance: Instance) -> None:
    for step in pipeline_ordered_steps(plan):
        if not isinstance(step, Join) or not step.on_keys:
            continue
        left_col, right_col = step.on_keys[0]
        if not isinstance(left_col, exp.Column) or not isinstance(right_col, exp.Column):
            continue
        if not left_col.table or not right_col.table:
            continue
        left_table = storage_table_for_join_column(step, left_col, instance)
        right_table = storage_table_for_join_column(step, right_col, instance)
        if left_table is None or right_table is None:
            continue
        if not _has_join_match(instance, left_table, right_table, left_col, right_col):
            left_rows = instance.get_rows(left_table)
            right_rows = instance.get_rows(right_table)
            if left_rows:
                value = Instance._row_value_dict(left_rows[0]).get(
                    instance.resolve_column(left_table, left_col.name)
                )
                if value is not None:
                    instance.create_rows({right_table: [{right_col.name: value}]})
            elif right_rows:
                value = Instance._row_value_dict(right_rows[0]).get(
                    instance.resolve_column(right_table, right_col.name)
                )
                if value is not None:
                    instance.create_rows({left_table: [{left_col.name: value}]})
            else:
                instance.create_rows({left_table: [{}]})
                left_rows = instance.get_rows(left_table)
                if left_rows:
                    value = Instance._row_value_dict(left_rows[-1]).get(
                        instance.resolve_column(left_table, left_col.name)
                    )
                    if value is not None:
                        instance.create_rows({right_table: [{right_col.name: value}]})
        if step.join_type.upper() == "RIGHT":
            _ensure_join_no_match_row(
                instance,
                source_table=right_table,
                source_col=right_col,
                other_table=left_table,
                other_col=left_col,
            )
        else:
            _ensure_join_no_match_row(
                instance,
                source_table=left_table,
                source_col=left_col,
                other_table=right_table,
                other_col=right_col,
            )


def _ensure_join_no_match_row(
    instance: Instance,
    *,
    source_table: exp.Table,
    source_col: exp.Column,
    other_table: exp.Table,
    other_col: exp.Column,
) -> None:
    source_ident = instance.resolve_column(source_table, source_col.name)
    other_ident = instance.resolve_column(other_table, other_col.name)
    source_values = {
        Instance._row_value_dict(row).get(source_ident)
        for row in instance.get_rows(source_table)
    }
    other_values = {
        Instance._row_value_dict(row).get(other_ident)
        for row in instance.get_rows(other_table)
    }
    if any(value is not None and value not in other_values for value in source_values):
        return
    for offset in range(10):
        value = _join_key_value(instance, source_table, source_col, len(source_values) + offset + 1)
        if value in other_values:
            continue
        row: dict[str, object] = {source_ident.name: value}
        _assign_identity_values(
            instance,
            source_table,
            row,
            len(source_values) + offset + len(other_values) + 1,
        )
        try:
            instance.create_rows({source_table: [row]})
        except (ConstraintViolationError, UniqueConflictError):
            continue
        return


def _has_join_match(
    instance: Instance,
    left_table: exp.Table,
    right_table: exp.Table,
    left_col: exp.Column,
    right_col: exp.Column,
) -> bool:
    left_ident = instance.resolve_column(left_table, left_col.name)
    right_ident = instance.resolve_column(right_table, right_col.name)
    right_values = {
        Instance._row_value_dict(row).get(right_ident)
        for row in instance.get_rows(right_table)
    }
    return any(
        Instance._row_value_dict(row).get(left_ident) in right_values
        for row in instance.get_rows(left_table)
    )


def _ensure_having_branch_groups(
    plan: Plan,
    instance: Instance,
) -> Mapping[exp.Table, Sequence[Mapping[str, object]]]:
    create_rows: dict[exp.Table, list[Mapping[str, object]]] = {}
    for filter_step, aggregate_step in _having_filters(plan):
        table = _single_input_table(aggregate_step)
        if table is None:
            continue
        if not _aggregate_filter_has_outcome(filter_step, aggregate_step, instance, True):
            rows = _passing_having_group_rows(filter_step, aggregate_step, instance)
            if rows:
                instance.create_rows({table: rows})
                create_rows.setdefault(table, []).extend(rows)
        if _aggregate_filter_has_outcome(filter_step, aggregate_step, instance, False):
            continue
        rows = _failing_having_group_rows(filter_step, aggregate_step, instance)
        if not rows:
            continue
        instance.create_rows({table: rows})
        create_rows.setdefault(table, []).extend(rows)
    return create_rows


def _having_filter_statuses(
    plan: Plan,
    instance: Instance,
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for index, step in enumerate(pipeline_ordered_steps(plan)):
        if not isinstance(step, Filter) or step.condition is None:
            continue
        aggregate_step = _single_aggregate_dependency(step)
        if aggregate_step is None:
            continue
        prefix = f"{index}.{step.type_name}"
        if _aggregate_filter_has_outcome(step, aggregate_step, instance, True):
            statuses[f"{prefix}.true"] = "covered"
        if _aggregate_filter_has_outcome(step, aggregate_step, instance, False):
            statuses[f"{prefix}.false"] = "covered"
    return statuses


def _having_filters(plan: Plan) -> tuple[tuple[Filter, Aggregate], ...]:
    filters: list[tuple[Filter, Aggregate]] = []
    for step in pipeline_ordered_steps(plan):
        if isinstance(step, Filter) and step.condition is not None:
            aggregate_step = _single_aggregate_dependency(step)
            if aggregate_step is not None:
                filters.append((step, aggregate_step))
    return tuple(filters)


def _single_aggregate_dependency(step: Filter) -> Aggregate | None:
    if len(step.dependencies) != 1:
        return None
    dependency = next(iter(step.dependencies))
    return dependency if isinstance(dependency, Aggregate) else None


def _aggregate_filter_has_outcome(
    filter_step: Filter,
    aggregate_step: Aggregate,
    instance: Instance,
    outcome: bool,
) -> bool:
    for row in _aggregate_rows(aggregate_step, instance):
        if concrete(filter_step.condition, Environment.from_row(row)) is outcome:
            return True
    return False


def _aggregate_rows(
    aggregate_step: Aggregate,
    instance: Instance,
) -> Sequence:
    child = _aggregate_child_schema(aggregate_step, instance)
    return AggregateEncodeStep(aggregate_step, instance=instance).forward(child).rows


def _aggregate_child_schema(
    aggregate_step: Aggregate,
    instance: Instance,
) -> DerivedSchema:
    scans = _leaf_table_scans(aggregate_step)
    if len(scans) != 1:
        return DerivedSchema(columns=(), rows=[])
    return ScanEncodeStep(scans[0], instance=instance).forward()


def _failing_having_group_rows(
    filter_step: Filter,
    aggregate_step: Aggregate,
    instance: Instance,
) -> list[Mapping[str, object]]:
    table = _single_input_table(aggregate_step)
    if table is None:
        return []
    row_count = 2 if _has_avg_aggregate(aggregate_step) else 1
    base = _base_group_row(aggregate_step, instance, table)
    if not base:
        return []
    rows = [dict(base) for _ in range(row_count)]
    for aggregate in aggregate_step.aggregations:
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        _apply_failing_aggregate_inputs(expression, rows)
    return rows


def _passing_having_group_rows(
    filter_step: Filter,
    aggregate_step: Aggregate,
    instance: Instance,
) -> list[Mapping[str, object]]:
    table = _single_input_table(aggregate_step)
    if table is None:
        return []
    row_count = 2 if _has_avg_aggregate(aggregate_step) else 1
    base = _base_group_row(aggregate_step, instance, table, prefix="__having_true")
    if not base:
        return []
    rows = [dict(base) for _ in range(row_count)]
    for aggregate in aggregate_step.aggregations:
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        _apply_passing_aggregate_inputs(expression, rows)
    return rows


def _base_group_row(
    aggregate_step: Aggregate,
    instance: Instance,
    table: exp.Table,
    *,
    prefix: str = "__having_false",
) -> dict[str, object]:
    row: dict[str, object] = {}
    for index, group_expr in enumerate(aggregate_step.group):
        if isinstance(group_expr, exp.Column):
            row[group_expr.name] = _fresh_group_value(
                instance,
                table,
                group_expr,
                index,
                prefix=prefix,
            )
    return row


def _fresh_group_value(
    instance: Instance,
    table: exp.Table,
    column: exp.Column,
    index: int,
    *,
    prefix: str,
) -> object:
    existing = {
        values.get(column.this)
        for values in (Instance._row_value_dict(row) for row in instance.get_rows(table))
    }
    candidate = f"{prefix}_{index}"
    suffix = 0
    while candidate in existing:
        suffix += 1
        candidate = f"{prefix}_{index}_{suffix}"
    return candidate


def _apply_passing_aggregate_inputs(
    aggregate: exp.Expression,
    rows: list[dict[str, object]],
) -> None:
    if isinstance(aggregate, (exp.Sum, exp.Avg, exp.Min, exp.Max)):
        _assign_input_columns(aggregate.this, rows, 20)


def _apply_failing_aggregate_inputs(
    aggregate: exp.Expression,
    rows: list[dict[str, object]],
) -> None:
    if isinstance(aggregate, exp.Sum):
        _assign_input_columns(aggregate.this, rows, 0)
    elif isinstance(aggregate, exp.Avg):
        _assign_input_columns(aggregate.this, rows, 0)
    elif isinstance(aggregate, (exp.Min, exp.Max)):
        _assign_input_columns(aggregate.this, rows, 0)


def _assign_input_columns(
    expression: exp.Expression | None,
    rows: list[dict[str, object]],
    value: object,
) -> None:
    if expression is None:
        return
    for column in expression.find_all(exp.Column):
        for row in rows:
            row.setdefault(column.name, value)


def _has_avg_aggregate(aggregate_step: Aggregate) -> bool:
    for aggregate in aggregate_step.aggregations:
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        if isinstance(expression, exp.Avg):
            return True
    return False


def _single_input_table(aggregate_step: Aggregate) -> exp.Table | None:
    scans = _leaf_table_scans(aggregate_step)
    return scans[0].table if len(scans) == 1 else None


def _having_source_tables(plan: Plan) -> set[str]:
    tables: set[str] = set()
    for _, aggregate_step in _having_filters(plan):
        table = _single_input_table(aggregate_step)
        if table is not None:
            tables.add(table.name)
    return tables


def _join_source_tables(plan: Plan) -> set[str]:
    tables: set[str] = set()
    for step in pipeline_ordered_steps(plan):
        if isinstance(step, Join):
            for left, right in step.on_keys:
                for expression in (left, right):
                    if isinstance(expression, exp.Column) and expression.table:
                        tables.add(expression.table)
    return tables


def _leaf_table_scans(step: Step) -> tuple[TableScan, ...]:
    return leaf_table_scans(step)


def _merge_create_rows(
    left: Mapping[exp.Table, Sequence[Mapping[exp.Identifier, object]]],
    right: Mapping[exp.Table, Sequence[Mapping[str, object]]],
) -> Mapping[exp.Table, Sequence[Mapping[object, object]]]:
    merged: dict[exp.Table, list[Mapping[object, object]]] = {
        table: list(rows) for table, rows in left.items()
    }
    for table, rows in right.items():
        merged.setdefault(table, []).extend(rows)
    return merged


def _row_counts(instance: Instance) -> dict[str, int]:
    return {
        instance.resolve_table(table_name).name: len(instance.get_rows(table_name))
        for table_name in instance.tables
    }


def _created_rows_since(
    instance: Instance,
    before_counts: Mapping[str, int],
) -> Mapping[exp.Table, Sequence[Mapping[exp.Identifier, object]]]:
    created: dict[exp.Table, list[Mapping[exp.Identifier, object]]] = {}
    for table in instance.schema.fk_safe_table_order():
        rows = instance.get_rows(table)
        start = before_counts.get(table.name, 0)
        if len(rows) <= start:
            continue
        created[table] = [
            {
                column: Instance._row_value_dict(row).get(column)
                for column in row.columns
            }
            for row in rows[start:]
        ]
    return created


def _problem_for_schema(
    schema: DerivedSchema,
    instance: Instance,
    create_rows: Mapping[exp.Table, Sequence[Mapping[object, object]]],
    assignments: Mapping[SolverVar, object],
) -> Problem:
    constraints = list(schema.constraints)
    variables = set(assignments)
    constrained_columns = _constraint_columns_by_table(
        instance,
        create_rows,
        constraints,
    )
    for table, rows in create_rows.items():
        table_node = instance.resolve_table(table)
        exact_columns = constrained_columns.get(table_node.name, set())
        for row_index, row in enumerate(rows):
            sv_map = {
                instance.resolve_column(table_node, column).name: _created_cell_solver_var(
                    instance,
                    table_node,
                    column,
                    row_index,
                )
                for column in row
            }
            constraints.extend(
                _database_constraints_for_solver(
                    instance,
                    table_node,
                    sv_map,
                    exact_columns,
                )
            )
    return Problem(
        constraints=constraints,
        equalities=list(schema.equalities),
        variables=variables,
    )


def _constraint_columns_by_table(
    instance: Instance,
    create_rows: Mapping[exp.Table, Sequence[Mapping[object, object]]],
    constraints: Sequence[exp.Expression],
) -> Dict[str, set[str]]:
    tables = tuple(instance.resolve_table(table) for table in create_rows)
    referenced: Dict[str, set[str]] = {table.name: set() for table in tables}
    for constraint in constraints:
        for column in constraint.find_all(exp.Column):
            if not isinstance(column.this, exp.Identifier):
                continue
            for table in tables:
                qualifier = column.args.get("table")
                if qualifier is not None and not same_identifier(
                    qualifier,
                    table.this,
                    instance.dialect,
                ):
                    continue
                try:
                    resolved = instance.resolve_column(table, column)
                except KeyError:
                    continue
                referenced[table.name].add(resolved.name)
                break
    return referenced


def _assignments_for_created_rows(
    instance: Instance,
    create_rows: Mapping[exp.Table, Sequence[Mapping[object, object]]],
) -> Mapping[SolverVar, object]:
    assignments: dict[SolverVar, object] = {}
    for table, rows in create_rows.items():
        table_node = instance.resolve_table(table)
        for row_index, row in enumerate(rows):
            for column, value in row.items():
                column_node = instance.resolve_column(table_node, column)
                dtype = instance.get_column_type(table_node, column_node)
                var = _created_cell_solver_var(
                    instance,
                    table_node,
                    column_node,
                    row_index,
                )
                assignments[var] = value
    return assignments


def _created_cell_solver_var(
    instance: Instance,
    table: exp.Table,
    column: object,
    row_index: int,
) -> SolverVar:
    column_node = instance.resolve_column(table, column)
    dtype = instance.get_column_type(table, column_node)
    return SolverVar(
        key=f"bottom_up.{table.name}.{table.name}.{column_node.name}.{row_index}",
        dtype=dtype,
        meta={
            "scope": table.name,
            "alias": table.name,
            "table": table.name,
            "column": column_node.name,
            "row_index": row_index,
        },
    )


def _attach_generation_state(
    instance: Instance,
    *,
    schema: DerivedSchema,
    obligations: tuple[CoverageObligation, ...],
) -> None:
    instance.generation = GenerationState(
        status=schema.status,
        reason=schema.reason,
        bounds=schema.bounds,
        create_rows=schema.create_rows,
        problem=schema.problem,
        assignments=schema.assignments,
        root_schema=schema,
        obligations=obligations,
        coverage_tree=schema.coverage_tree,
        coverage_ratio=schema.coverage_ratio,
    )


def _unknown_result(
    plan: Plan,
    bounds: BmcBounds,
    reason: str,
) -> DerivedSchema:
    obligations = _obligations_for_status(plan, None, "unsupported")
    return DerivedSchema(
        columns=(),
        rows=[],
        status="unknown",
        obligations=obligations,
        coverage_ratio=_coverage_ratio(obligations),
        reason=reason,
        bounds=bounds,
    )


def _obligations_for_status(
    plan: Plan,
    instance: Instance | None,
    status: str,
) -> tuple[CoverageObligation, ...]:
    if instance is None:
        return ()
    tree = EncodePipeline(plan, instance).forward().coverage_tree
    targets = tree.iter_targets() if tree is not None else ()
    mapped = "infeasible" if status == "unsat" else status
    return tuple(target.obligation(mapped) for target in targets)


def _evidence_for_obligations(
    obligations: Sequence[CoverageObligation],
    schema: DerivedSchema,
) -> Dict[str, Sequence[tuple[str, ...]]]:
    rowids = tuple(row.rowid for row in schema.rows)
    return {
        obligation.id: rowids
        for obligation in obligations
        if obligation.status == "covered" and rowids
    }


def materialize(instance: Instance) -> List[Dict[str, Any]]:
    """Return all rows from *instance* as flat dicts."""
    rows: List[Dict[str, Any]] = []
    for table in instance.schema.fk_safe_table_order():
        for row in instance.get_rows(table):
            materialized = _materialize_row(row)
            if materialized:
                rows.append(materialized)
    return rows
