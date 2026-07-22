from __future__ import annotations

from typing import Iterable, Mapping

from sqlglot import exp

from parseval.generator.coverage import SemanticTarget
from parseval.plan.explain import (
    Aggregate,
    Filter,
    Join,
    Projection,
    ScalarSubqueryRef,
    Sort,
    Step,
    Window,
)


def ordered_dependencies(step: Step) -> tuple[Step, ...]:
    dependencies = set(step.dependencies)
    ordered: list[Step] = []
    for candidate in (getattr(step, "left", None), getattr(step, "right", None)):
        if isinstance(candidate, Step) and candidate in dependencies and candidate not in ordered:
            ordered.append(candidate)
    ordered.extend(
        sorted(
            (dependency for dependency in dependencies if dependency not in ordered),
            key=_step_sort_key,
        )
    )
    return tuple(ordered)


def _step_sort_key(step: Step) -> tuple[str, str, str]:
    name = getattr(step, "name", None)
    if isinstance(name, exp.Expression):
        name_text = name.sql()
    else:
        name_text = str(name or "")
    return step.type_name, name_text, step.display


def _subquery_roots(
    step: Step,
    scalar_subqueries: Mapping[str, Step],
) -> tuple[Step, ...]:
    roots: list[Step] = []
    seen: set[str] = set()
    for expression in _step_expressions(step):
        for reference in expression.find_all(ScalarSubqueryRef):
            subquery_id = reference.subquery_id
            if subquery_id in seen:
                continue
            seen.add(subquery_id)
            root = scalar_subqueries.get(subquery_id)
            if root is not None:
                roots.append(root)
    return tuple(roots)


def _reachable(root: Step) -> tuple[Step, ...]:
    seen: set[Step] = set()
    ordered: list[Step] = []

    def visit(step: Step) -> None:
        if step in seen:
            return
        seen.add(step)
        ordered.append(step)
        for child in ordered_dependencies(step):
            visit(child)

    visit(root)
    return tuple(ordered)


def scalar_subquery_targets(step: Step, prefix: str) -> tuple[SemanticTarget, ...]:
    targets = [
        SemanticTarget(
            id=f"{prefix}.{step.type_name}.scalar.{outcome}",
            step=step,
            step_type=step.type_name,
            kind="scalar_subquery",
            target=outcome,
        )
        for outcome in ("singleton_null", "singleton_non_null")
    ]
    if any(isinstance(node, Sort) and bool(node.key) for node in _reachable(step)):
        targets.append(
            SemanticTarget(
                id=f"{prefix}.{step.type_name}.scalar.ordered_selection",
                step=step,
                step_type=step.type_name,
                kind="scalar_subquery",
                target="ordered_selection",
            )
        )
    return tuple(targets)


def _step_expressions(step: Step) -> Iterable[exp.Expression]:
    if isinstance(step, Filter) and step.condition is not None:
        yield step.condition
    if isinstance(step, Projection):
        yield from step.projections
    if isinstance(step, Join):
        if step.condition is not None:
            yield step.condition
        for left, right in step.on_keys:
            yield left
            yield right
    if isinstance(step, Aggregate):
        yield from step.group or ()
        yield from step.aggregations or ()
    if isinstance(step, Sort):
        yield from step.key or ()
    if isinstance(step, Window):
        yield from step.window_exprs or ()


__all__ = ["ordered_dependencies", "scalar_subquery_targets"]
