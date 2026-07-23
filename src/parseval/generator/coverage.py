from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlglot import exp
from decimal import Decimal
from datetime import datetime, date, time
from parseval.plan.explain import (
    Aggregate,
    Distinct,
    Filter,
    Join,
    Limit,
    Plan,
    Projection,
    RawStep,
    RecursiveQuery,
    Sort,
    Step,
    TableScan,
    Union,
    Unnest,
    Window,
    normalize_join_type,
)
from parseval.generator.helper import (
    leaf_table_scans,
)


@dataclass(frozen=True)
class SemanticTarget:
    id: str
    step: Step
    step_type: str
    kind: str
    target: str
    expression: exp.Expression | None = None


@dataclass(frozen=True)
class CoverageObligation:
    """Result of compiling and validating one semantic outcome path."""

    id: str
    target_id: str
    steps: tuple[str, ...]
    status: str
    reason: str = ""


@dataclass(frozen=True)
class GenerationState:
    """Public diagnostics for a complete single-query generation run."""

    status: str
    obligations: tuple[CoverageObligation, ...]
    solver_calls: int
    coverage_ratio: float
    stage_timings: Mapping[str, float]

@dataclass(frozen=True)
class CoverageTreeNode:
    id: str
    step: Step
    step_type: str
    targets: tuple[SemanticTarget, ...]
    children: tuple["CoverageTreeNode", ...]

    def iter_targets(self) -> tuple[SemanticTarget, ...]:
        targets: list[SemanticTarget] = list(self.targets)
        for child in self.children:
            targets.extend(child.iter_targets())
        return tuple(targets)

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


def _step_semantic_targets(
    step: Step,
    instance: Instance | None,
    path: str,
) -> tuple[SemanticTarget, ...]:
    step_type = step.type_name
    prefix = f"{path}.{step_type}"
    targets: list[SemanticTarget] = []
    if isinstance(step, (RawStep, RecursiveQuery, Unnest)):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.unsupported",
                step=step,
                step_type=step_type,
                kind="unsupported_plan_node",
                target=step_type,
            )
        )
    if isinstance(step, TableScan):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.row",
                step=step,
                step_type=step_type,
                kind="base_row",
                target="exists",
            )
        )
    if (
        isinstance(step, Filter)
        and step.condition is not None
        and not _is_not_null_filter(step.condition)
    ):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.true",
                step=step,
                step_type=step_type,
                kind="filter",
                target="true",
                expression=step.condition,
            )
        )
        targets.append(
            SemanticTarget(
                id=f"{prefix}.false",
                step=step,
                step_type=step_type,
                kind="filter",
                target="false",
                expression=step.condition,
            )
        )
        if instance is None or _condition_has_nullable_input(instance, step):
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.null",
                    step=step,
                    step_type=step_type,
                    kind="filter",
                    target="null",
                    expression=step.condition,
                )
            )
    if isinstance(step, Join):
        jt = normalize_join_type(step.join_type)
        if step.subquery_kind is not None and jt in {"SEMI", "ANTI"}:
            for outcome in ("matching", "non_matching", "multi_row"):
                targets.append(
                    SemanticTarget(
                        id=f"{prefix}.{step.subquery_kind}.{outcome}",
                        step=step,
                        step_type=step_type,
                        kind="subquery",
                        target=outcome,
                    )
                )
            if step.subquery_kind in {"in", "not_in"}:
                targets.append(
                    SemanticTarget(
                        id=f"{prefix}.{step.subquery_kind}.null_operand",
                        step=step,
                        step_type=step_type,
                        kind="subquery",
                        target="null_operand",
                    )
                )
            if step.subquery_kind == "not_in":
                targets.append(
                    SemanticTarget(
                        id=f"{prefix}.not_in.null_poison",
                        step=step,
                        step_type=step_type,
                        kind="subquery",
                        target="null_poison",
                    )
                )
        elif jt == "SEMI":
            targets.extend(
                (
                    SemanticTarget(
                        id=f"{prefix}.semi_match",
                        step=step,
                        step_type=step_type,
                        kind="semi_join",
                        target="semi_match",
                    ),
                    SemanticTarget(
                        id=f"{prefix}.semi_no_match",
                        step=step,
                        step_type=step_type,
                        kind="semi_join",
                        target="semi_no_match",
                    ),
                )
            )
        elif jt == "ANTI":
            targets.extend(
                (
                    SemanticTarget(
                        id=f"{prefix}.anti_no_match",
                        step=step,
                        step_type=step_type,
                        kind="anti_join",
                        target="anti_no_match",
                    ),
                    SemanticTarget(
                        id=f"{prefix}.anti_match_excluded",
                        step=step,
                        step_type=step_type,
                        kind="anti_join",
                        target="anti_match_excluded",
                    ),
                )
            )
        else:
            targets.extend(
                (
                    SemanticTarget(
                        id=f"{prefix}.match",
                        step=step,
                        step_type=step_type,
                        kind="join",
                        target="match",
                    ),
                    SemanticTarget(
                        id=f"{prefix}.no_match",
                        step=step,
                        step_type=step_type,
                        kind="join",
                        target="no_match",
                    ),
                )
            )
        if jt in {"LEFT", "FULL"}:
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.preserved_left",
                    step=step,
                    step_type=step_type,
                    kind="join",
                    target="preserved_left",
                )
            )
        if jt in {"RIGHT", "FULL"}:
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.preserved_right",
                    step=step,
                    step_type=step_type,
                    kind="join",
                    target="preserved_right",
                )
            )
    if isinstance(step, Aggregate):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.group",
                step=step,
                step_type=step_type,
                kind="group_existence",
                target="group",
            )
        )
        targets.extend(_aggregate_targets(prefix, step))
        if step.group and not step.aggregations:
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.duplicate_eliminated",
                    step=step,
                    step_type=step_type,
                    kind="distinct",
                    target="duplicate_eliminated",
                )
            )
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.distinct_preserved",
                    step=step,
                    step_type=step_type,
                    kind="distinct",
                    target="distinct_preserved",
                )
            )
    if isinstance(step, Distinct):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.duplicate_eliminated",
                step=step,
                step_type=step_type,
                kind="distinct",
                target="duplicate_eliminated",
            )
        )
        targets.append(
            SemanticTarget(
                id=f"{prefix}.distinct_preserved",
                step=step,
                step_type=step_type,
                kind="distinct",
                target="distinct_preserved",
            )
        )
    if isinstance(step, Union):
        targets.extend(
            (
                SemanticTarget(
                    id=f"{prefix}.left_only",
                    step=step,
                    step_type=step_type,
                    kind="union",
                    target="left_only",
                ),
                SemanticTarget(
                    id=f"{prefix}.right_only",
                    step=step,
                    step_type=step_type,
                    kind="union",
                    target="right_only",
                ),
                SemanticTarget(
                    id=f"{prefix}.overlap",
                    step=step,
                    step_type=step_type,
                    kind="union",
                    target="overlap",
                ),
                SemanticTarget(
                    id=f"{prefix}.duplicate_{'preserved' if step.is_all else 'eliminated'}",
                    step=step,
                    step_type=step_type,
                    kind="union",
                    target="duplicate_preserved" if step.is_all else "duplicate_eliminated",
                ),
            )
        )
    if isinstance(step, Window):
        for window_index, window in enumerate(step.window_exprs):
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.window{window_index}.row",
                    step=step,
                    step_type=step_type,
                    kind="window",
                    target="row",
                    expression=window,
                )
            )
            if window.args.get("partition_by"):
                targets.append(
                    SemanticTarget(
                        id=f"{prefix}.window{window_index}.partition_peer",
                        step=step,
                        step_type=step_type,
                        kind="window",
                        target="partition_peer",
                        expression=window,
                    )
                )
            order = window.args.get("order")
            if isinstance(order, exp.Order) and order.expressions:
                targets.append(
                    SemanticTarget(
                        id=f"{prefix}.window{window_index}.order_tie",
                        step=step,
                        step_type=step_type,
                        kind="window",
                        target="order_tie",
                        expression=window,
                    )
                )
    if isinstance(step, Sort):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.selected",
                step=step,
                step_type=step_type,
                kind="ordering",
                target="selected",
            )
        )
        targets.append(
            SemanticTarget(
                id=f"{prefix}.competitor",
                step=step,
                step_type=step_type,
                kind="ordering",
                target="excluded_competitor",
            )
        )
        targets.append(
            SemanticTarget(
                id=f"{prefix}.rank_tie",
                step=step,
                step_type=step_type,
                kind="ordering",
                target="rank_tie",
            )
        )
    if isinstance(step, Limit):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.selected",
                step=step,
                step_type=step_type,
                kind="limit_window",
                target="selected",
            )
        )
        if step.offset:
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.offset_skipped",
                    step=step,
                    step_type=step_type,
                    kind="limit_window",
                    target="offset_skipped",
                )
            )
    if isinstance(step, Projection):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.projection",
                step=step,
                step_type=step_type,
                kind="projection_visible",
                target="visible",
            )
        )
        for case_index, case in enumerate(_case_expressions(step.projections)):
            targets.extend(_case_targets(prefix, step, case_index, case))
    return tuple(targets)


def _aggregate_targets(
    prefix: str,
    step: Aggregate,
) -> tuple[SemanticTarget, ...]:
    targets: list[SemanticTarget] = []
    for aggregate_index, aggregate in enumerate(step.aggregations):
        expression = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
        if isinstance(expression, exp.Avg):
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.agg{aggregate_index}.multi_row",
                    step=step,
                    step_type=step.type_name,
                    kind="multi_row_aggregate_witness",
                    target="multi_row",
                    expression=expression,
                )
            )
        if isinstance(expression, exp.Count):
            source = expression.this
            is_distinct = isinstance(source, exp.Distinct) or bool(
                expression.args.get("distinct")
            )
            if (
                source is not None
                and not isinstance(source, (exp.Star, exp.Literal))
                and not is_distinct
            ):
                targets.append(
                    SemanticTarget(
                        id=f"{prefix}.agg{aggregate_index}.null_sensitive",
                        step=step,
                        step_type=step.type_name,
                        kind="null_sensitive_aggregate_witness",
                        target="count_column_null",
                        expression=expression,
                    )
                )
            if is_distinct:
                targets.append(
                    SemanticTarget(
                        id=f"{prefix}.agg{aggregate_index}.distinct",
                        step=step,
                        step_type=step.type_name,
                        kind="distinct_aggregate_witness",
                        target="duplicate_eliminated",
                        expression=expression,
                    )
                )
        if isinstance(expression, exp.Sum) and isinstance(expression.this, exp.Case):
            targets.append(
                SemanticTarget(
                    id=f"{prefix}.agg{aggregate_index}.case",
                    step=step,
                    step_type=step.type_name,
                    kind="conditional_aggregate_case",
                    target="case_path",
                    expression=expression.this,
                )
            )
            targets.extend(
                _case_targets(prefix, step, aggregate_index, expression.this)
            )
    return tuple(targets)


def _case_targets(
    prefix: str,
    step: Step,
    case_index: int,
    case: exp.Case,
) -> tuple[SemanticTarget, ...]:
    targets: list[SemanticTarget] = []
    for when_index, branch in enumerate(case.args.get("ifs", ()) or ()):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.case{case_index}.when{when_index}.true",
                step=step,
                step_type=step.type_name,
                kind="case",
                target=f"when_{when_index}_true",
                expression=branch.this,
            )
        )
    targets.append(
        SemanticTarget(
            id=f"{prefix}.case{case_index}.default",
            step=step,
            step_type=step.type_name,
            kind="case",
            target="default",
            expression=case,
        )
    )
    return tuple(targets)


def _case_expressions(
    expressions: Sequence[exp.Expression],
) -> tuple[exp.Case, ...]:
    cases: list[exp.Case] = []
    for expression in expressions:
        cases.extend(expression.find_all(exp.Case))
    return tuple(cases)


def _sort_window(plan: Plan, sort: Sort) -> tuple[int, int]:
    offset = 0
    limit = sort.fetch or 1
    for step in _ordered_steps(plan.root):
        if isinstance(step, Limit) and sort in step.dependencies:
            offset = step.offset or 0
            limit = step.fetch or max(limit - offset, 1)
            break
    return offset, max(limit, 1)




def _condition_has_nullable_input(instance: Instance, step: Filter) -> bool:
    scans = leaf_table_scans(step)
    for column in step.condition.find_all(exp.Column) if step.condition is not None else ():
        for scan in scans:
            try:
                stored = instance.resolve_column(scan.table, column)
            except KeyError:
                continue
            if instance.nullable(scan.table, stored):
                return True
    return False


def _is_not_null_filter(expression: exp.Expression) -> bool:
    return (
        isinstance(expression, exp.Not)
        and isinstance(expression.this, exp.Is)
        and isinstance(expression.this.expression, exp.Null)
    )
    
    
def sql_order_key(value: Any) -> tuple[int, int, Any]:
    """Return a deterministic SQL-like key for generated ORDER BY values."""
    if value is None:
        return (0, 0, None)
    if isinstance(value, bool):
        return (1, 1, int(value))
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return (1, 1, float(value))
    if isinstance(value, (datetime, date, time)):
        return (1, 2, value.isoformat())
    if isinstance(value, bytes):
        return (1, 4, value)
    if isinstance(value, str):
        return (1, 3, value)
    return (1, 5, str(value))
