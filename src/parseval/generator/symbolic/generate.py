from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Mapping, Sequence

from sqlglot import exp

from parseval.generator.bounds import BmcBounds
from parseval.generator.coverage import (
    CoverageTreeNode,
    CoverageObligation,
    SemanticCoverageRecorder,
    _coverage_ratio,
)
from parseval.instance import Instance
from parseval.plan.context import DerivedSchema
from parseval.plan.explain import (
    Aggregate,
    Filter,
    explain,
    Limit,
    Plan,
    Sort,
    Step,
)
from parseval.plan.rex import Environment, concrete
from parseval.solver.types import Problem, SolverVar

from .operator import (
    EncodePipeline,
    _root_aggregate,
    _root_fetch,
    pipeline_ordered_steps,
    schema_constraints_for_solver_row,
)
from parseval.generator.speculate import speculate


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


def generate(
    ddls: str,
    query: str,
    dialect: str = "sqlite",
    *,
    bounds: BmcBounds | None = None,
    generate_negatives: bool = True,
) -> Instance:
    """Generate witness rows for *query* under *ddls*.

    Speculative seeding supplies the initial rows. When the query can be
    planned, EncodePipeline appends additional coverage-driven rows to the same
    instance. If planning fails, the speculative instance is returned.
    """
    instance = speculate(
        ddls,
        query,
        dialect=dialect,
        bounds=bounds,
        generate_negatives=generate_negatives,
    )

    try:
        plan = explain(ddls, query, dialect=dialect)
    except Exception:
        return instance

    return _generate_from_plan(plan, instance, bounds=bounds, before_counts={})


def _generate_from_plan(
    plan: Plan,
    instance: Instance,
    *,
    bounds: BmcBounds | None = None,
    before_counts: Mapping[str, int] | None = None,
) -> Instance:
    """Generate semantic witnesses for *plan* through EncodePipeline.

    ``Instance`` is the committed row source. Solver output is appended to
    ``instance`` and the same object is returned.
    """
    current_bounds = _bounds_for_plan(plan, bounds or BmcBounds())
    if before_counts is None:
        before_counts = _row_counts(instance)
    pipeline = EncodePipeline(
        plan,
        instance,
        bounds=current_bounds,
        base_row_counts=before_counts,
    )
    schema = pipeline.forward()
    tree = schema.coverage_tree
    if tree is None:
        pipeline = EncodePipeline(
            plan,
            instance,
            bounds=current_bounds,
            base_row_counts=before_counts,
        )
        schema = pipeline.forward()
        tree = schema.coverage_tree
    schema_failure_reason = getattr(pipeline, "schema_failure_reason", "")
    demand_failures = tuple(getattr(pipeline, "demand_failure_reasons", ()))
    evaluated_tree = (
        SemanticCoverageRecorder(plan, instance).evaluate_tree(
            tree,
            _having_filter_statuses(plan, instance, tree),
        )
        if tree is not None
        else None
    )
    obligations = list(evaluated_tree.iter_obligations()) if evaluated_tree is not None else []

    evidence = _evidence_for_obligations(obligations, schema)
    schema.obligations.extend(obligations)
    schema.evidence.update(evidence)
    if schema_failure_reason:
        failure_reasons = (schema_failure_reason,)
    elif schema.rows:
        failure_reasons = ()
    else:
        pending_reasons = _required_pending_reasons(obligations)
        failure_reasons = tuple(
            reason
            for reason in (*demand_failures, *pending_reasons)
            if reason
        )
    schema.status = "unknown" if failure_reasons else "sat"
    schema.reason = ";".join(dict.fromkeys(failure_reasons))
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


def _required_pending_reasons(
    obligations: Sequence[CoverageObligation],
) -> tuple[str, ...]:
    reasons: list[str] = []
    required_kinds = {"base_row", "filter", "join", "ordering", "limit", "aggregate"}
    for obligation in obligations:
        if obligation.status != "pending":
            continue
        if obligation.kind in required_kinds:
            reasons.append(
                f"pending_coverage:{obligation.kind}:{obligation.target}"
            )
    return tuple(reasons)


def _bounds_for_plan(plan: Plan, bounds: BmcBounds) -> BmcBounds:
    required_rows = bounds.table_rows
    for step in pipeline_ordered_steps(plan):
        if isinstance(step, (Limit, Sort)) and step.fetch is not None:
            offset = getattr(step, "offset", 0) or 0
            required_rows = max(required_rows, offset + step.fetch)
    root_fetch = _root_fetch(plan.root)
    root_aggregate = _root_aggregate(plan.root)
    if root_fetch is None:
        if root_aggregate is not None and not root_aggregate.group:
            required_rows = max(required_rows, 1)
        else:
            required_rows = max(required_rows, bounds.result_rows)
    adjusted, _ = bounds.raise_table_rows(required_rows)
    return adjusted


def _having_filter_statuses(
    plan: Plan,
    instance: Instance,
    tree: CoverageTreeNode,
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    aggregate_filters: dict[int, tuple[Filter, Aggregate]] = {}
    for step in pipeline_ordered_steps(plan):
        if not isinstance(step, Filter) or step.condition is None:
            continue
        aggregate_step = _single_aggregate_dependency(step)
        if aggregate_step is None:
            continue
        aggregate_filters[id(step)] = (step, aggregate_step)
    for target in tree.iter_targets():
        if target.kind != "filter":
            continue
        match = aggregate_filters.get(id(target.step))
        if match is None:
            continue
        step, aggregate_step = match
        if _aggregate_filter_has_outcome(plan, step, aggregate_step, instance, True):
            statuses[f"{target.id.rsplit('.', 1)[0]}.true"] = "covered"
        if _aggregate_filter_has_outcome(plan, step, aggregate_step, instance, False):
            statuses[f"{target.id.rsplit('.', 1)[0]}.false"] = "covered"
    return statuses


def _single_aggregate_dependency(step: Filter) -> Aggregate | None:
    if len(step.dependencies) != 1:
        return None
    dependency = next(iter(step.dependencies))
    return dependency if isinstance(dependency, Aggregate) else None


def _aggregate_filter_has_outcome(
    plan: Plan,
    filter_step: Filter,
    aggregate_step: Aggregate,
    instance: Instance,
    outcome: bool,
) -> bool:
    for row in _existing_schema_for_step(plan, instance, aggregate_step).rows:
        if concrete(filter_step.condition, Environment.from_row(row)) is outcome:
            return True
    return False


def _existing_schema_for_step(
    plan: Plan,
    instance: Instance,
    root: Step,
) -> DerivedSchema:
    pipeline = EncodePipeline(plan, instance, base_row_counts=_row_counts(instance))
    cache: dict[Step, DerivedSchema] = {}

    def process(step: Step) -> DerivedSchema:
        if step in cache:
            return cache[step]
        children = tuple(process(dependency) for dependency in step.dependencies)
        schema = pipeline._build_operator(step).forward(*children)
        cache[step] = schema
        return schema

    return process(root)


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
            exact_columns = set(sv_map)
            constraints.extend(
                schema_constraints_for_solver_row(
                    instance,
                    table_node,
                    sv_map,
                    exact_columns=exact_columns,
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
