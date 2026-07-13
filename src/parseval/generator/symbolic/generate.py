from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from sqlglot import exp

from parseval.generator.bounds import BmcBounds
from parseval.generator.coverage import (
    CoverageObligation,
    SemanticTarget,
    SemanticCoverageRecorder,
    _coverage_ratio,
    _is_not_null_filter,
    _plan_semantic_targets,
)
from parseval.instance import Instance
from parseval.instance.schema import normalize_identifier
from parseval.plan.context import DerivedSchema
from parseval.plan.explain import (
    Aggregate,
    Filter,
    Join,
    Limit,
    Plan,
    Sort,
    Step,
    SubqueryAlias,
    TableScan,
)
from parseval.plan.rex import Environment, Variable, concrete
from parseval.solver.types import Problem, SolverVar

from .operator import (
    AggregateEncodeStep,
    EncodePipeline,
    FilterEncodeStep,
    ScanEncodeStep,
    _database_check_constraints_for_solver,
)


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
    coverage_ratio: float = 0.0


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
    """Generate bottom-up semantic witnesses for *plan*.

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
    _ensure_base_rows(
        plan,
        instance,
        skip_tables=_having_source_tables(plan) | _join_source_tables(plan),
    )
    _ensure_join_match_rows(plan, instance)
    _ensure_filter_branch_rows(plan, instance)
    having_rows = _ensure_having_branch_groups(plan, instance)
    schema = EncodePipeline(plan, instance).forward()
    targets = _plan_semantic_targets(plan, instance)
    statuses = SemanticCoverageRecorder(plan, instance).evaluate(targets)
    statuses.update(_having_filter_statuses(plan, instance))
    obligations: list[CoverageObligation] = [
        target.obligation(statuses.get(target.id, "unsupported"))
        for target in targets
    ]

    evidence = _evidence_for_obligations(obligations, schema)
    schema.obligations.extend(obligations)
    schema.evidence.update(evidence)
    schema.status = "sat"
    schema.reason = ""
    schema.create_rows = _merge_create_rows(
        _created_rows_since(instance, before_counts),
        having_rows,
    )
    schema.assignments = _assignments_for_created_rows(instance, schema.create_rows)
    schema.problem = _problem_for_schema(
        schema,
        instance,
        schema.create_rows,
        schema.assignments,
    )
    schema.coverage_ratio = _coverage_ratio(obligations)
    schema.bounds = current_bounds
    _attach_generation_state(instance, schema=schema, obligations=tuple(obligations))
    return instance


def _ensure_base_rows(
    plan: Plan,
    instance: Instance,
    *,
    skip_tables: set[str] | None = None,
) -> None:
    skip_tables = skip_tables or set()
    for step in _ordered_steps(plan.root):
        if not isinstance(step, TableScan):
            continue
        if step.table.name in skip_tables:
            continue
        if instance.get_rows(step.table):
            continue
        instance.create_rows({step.table: [{}]})


def _bounds_for_plan(plan: Plan, bounds: BmcBounds) -> BmcBounds:
    required_rows = bounds.table_rows
    for step in _ordered_steps(plan.root):
        if isinstance(step, (Limit, Sort)) and step.fetch is not None:
            offset = getattr(step, "offset", 0) or 0
            required_rows = max(required_rows, offset + step.fetch)
    adjusted, _ = bounds.raise_table_rows(required_rows)
    return adjusted


def _ensure_filter_branch_rows(plan: Plan, instance: Instance) -> None:
    for index, step in enumerate(_ordered_steps(plan.root)):
        if not isinstance(step, Filter) or step.condition is None:
            continue
        if _is_not_null_filter(step.condition):
            continue
        if _single_aggregate_dependency(step) is not None:
            continue
        operator = FilterEncodeStep(step, instance=instance)
        true_target = SemanticTarget(
            id=f"{index}.{step.type_name}.true",
            step=step,
            step_type=step.type_name,
            kind="filter",
            target="true",
            expression=step.condition,
        )
        false_target = SemanticTarget(
            id=f"{index}.{step.type_name}.false",
            step=step,
            step_type=step.type_name,
            kind="filter",
            target="false",
            expression=step.condition,
        )
        null_target = SemanticTarget(
            id=f"{index}.{step.type_name}.null",
            step=step,
            step_type=step.type_name,
            kind="filter",
            target="null",
            expression=step.condition,
        )
        try:
            operator._ensure_filter_true(true_target)
            operator._ensure_filter_false(false_target)
            operator._ensure_filter_null(null_target)
        except (KeyError, ValueError):
            continue


def _ensure_join_match_rows(plan: Plan, instance: Instance) -> None:
    for step in _ordered_steps(plan.root):
        if not isinstance(step, Join) or not step.on_keys:
            continue
        left_col, right_col = step.on_keys[0]
        if not isinstance(left_col, exp.Column) or not isinstance(right_col, exp.Column):
            continue
        if not left_col.table or not right_col.table:
            continue
        left_table = _storage_table_for_join_column(step, left_col, instance)
        right_table = _storage_table_for_join_column(step, right_col, instance)
        if left_table is None or right_table is None:
            continue
        if _has_join_match(instance, left_table, right_table, left_col, right_col):
            continue
        left_rows = instance.get_rows(left_table)
        right_rows = instance.get_rows(right_table)
        if left_rows:
            value = Instance._row_value_dict(left_rows[0]).get(
                instance.resolve_column(left_table, left_col.name)
            )
            if value is not None:
                instance.create_rows({right_table: [{right_col.name: value}]})
            continue
        if right_rows:
            value = Instance._row_value_dict(right_rows[0]).get(
                instance.resolve_column(right_table, right_col.name)
            )
            if value is not None:
                instance.create_rows({left_table: [{left_col.name: value}]})
            continue
        instance.create_rows({left_table: [{}]})
        left_rows = instance.get_rows(left_table)
        if left_rows:
            value = Instance._row_value_dict(left_rows[-1]).get(
                instance.resolve_column(left_table, left_col.name)
            )
            if value is not None:
                instance.create_rows({right_table: [{right_col.name: value}]})


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


def _storage_table_for_join_column(
    join: Join,
    column: exp.Column,
    instance: Instance,
) -> exp.Table | None:
    qualifier = column.args.get("table")
    if qualifier is None:
        return None
    for alias, table in _join_alias_tables(join):
        if _same_identifier(alias, qualifier, instance.dialect):
            return table
    try:
        return instance.resolve_table(exp.Table(this=qualifier.copy()))
    except KeyError:
        return None


def _join_alias_tables(join: Join) -> tuple[tuple[exp.Identifier, exp.Table], ...]:
    pairs: list[tuple[exp.Identifier, exp.Table]] = []

    def visit(step: Step, active_alias: exp.Identifier | None = None) -> None:
        if isinstance(step, SubqueryAlias):
            active_alias = step.alias
        if isinstance(step, TableScan) and active_alias is not None:
            pairs.append((active_alias, step.table))
            return
        for dependency in step.dependencies:
            visit(dependency, active_alias)

    for dependency in join.dependencies:
        visit(dependency)
    return tuple(pairs)


def _same_identifier(
    left: exp.Identifier,
    right: exp.Identifier,
    dialect: str,
) -> bool:
    return normalize_identifier(left, dialect) == normalize_identifier(right, dialect)


def _ensure_having_branch_groups(
    plan: Plan,
    instance: Instance,
) -> Mapping[exp.Table, Sequence[Mapping[str, object]]]:
    create_rows: dict[exp.Table, list[Mapping[str, object]]] = {}
    for filter_step, aggregate_step in _having_filters(plan.root):
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
    for index, step in enumerate(_ordered_steps(plan.root)):
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


def _having_filters(root: Step) -> tuple[tuple[Filter, Aggregate], ...]:
    filters: list[tuple[Filter, Aggregate]] = []
    for step in _ordered_steps(root):
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
    for _, aggregate_step in _having_filters(plan.root):
        table = _single_input_table(aggregate_step)
        if table is not None:
            tables.add(table.name)
    return tables


def _join_source_tables(plan: Plan) -> set[str]:
    tables: set[str] = set()
    for step in _ordered_steps(plan.root):
        if isinstance(step, Join):
            for left, right in step.on_keys:
                for expression in (left, right):
                    if isinstance(expression, exp.Column) and expression.table:
                        tables.add(expression.table)
    return tables


def _leaf_table_scans(step: Step) -> tuple[TableScan, ...]:
    if isinstance(step, TableScan):
        return (step,)
    scans: list[TableScan] = []
    for dependency in step.dependencies:
        scans.extend(_leaf_table_scans(dependency))
    return tuple(scans)


def _ordered_steps(root: Step) -> tuple[Step, ...]:
    ordered: list[Step] = []
    seen: set[int] = set()

    def visit(step: Step) -> None:
        identity = id(step)
        if identity in seen:
            return
        seen.add(identity)
        for dependency in step.dependencies:
            visit(dependency)
        ordered.append(step)

    visit(root)
    return tuple(ordered)


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
    for table, rows in create_rows.items():
        table_node = instance.resolve_table(table)
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
                _database_check_constraints_for_solver(
                    instance,
                    table_node,
                    sv_map,
                    set(sv_map),
                )
            )
    return Problem(
        constraints=constraints,
        equalities=list(schema.equalities),
        variables=variables,
    )


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
        key=f"bottom_up.{table.name}.r{row_index}.{column_node.name}",
        dtype=dtype,
        meta={
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
    targets = _plan_semantic_targets(plan, instance) if instance is not None else ()
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
