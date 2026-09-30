"""Measure term-native coverage goals on concrete instances."""

from __future__ import annotations

from parseval.instance import Instance
from parseval.terms.arena import TermArena
from parseval.terms.terms import TermId
from parseval.uexpr.evaluate import UExprEvaluator

from .explore import DEFAULT_GROUP_SIZE, explore_paths
from .model import CoverageReport, CoverageStatus, CoverageTarget
from .tracker import CoverageTracker
from .scopes import unsupported_scopes


def target_is_covered(
    arena: TermArena,
    instance: Instance,
    target: CoverageTarget,
    *,
    evaluator_class: type[UExprEvaluator] = UExprEvaluator,
) -> bool:
    obligation = target.obligation
    return evaluator_class(arena, instance).witnessed_conditions(
        obligation.plan, obligation.conditions,
        obligation.relations, obligation.contexts,
    )


def measure_coverage(
    arena: TermArena,
    root: TermId,
    instances: Instance | tuple[Instance, ...],
    targets: tuple[CoverageTarget, ...] | None = None,
    *,
    group_size: int = DEFAULT_GROUP_SIZE,
    evaluator_class: type[UExprEvaluator] = UExprEvaluator,
) -> CoverageReport:
    cases = (instances,) if isinstance(instances, Instance) else instances
    selected = targets
    if selected is None:
        discovered: dict[str, CoverageTarget] = {}
        for instance in cases:
            observed, neighbors = explore_paths(arena, root, instance, group_size=group_size, evaluator_class=evaluator_class)
            for target in (*observed, *neighbors):
                discovered[target.id] = target
        selected = tuple(discovered.values())
    tracker = CoverageTracker()
    tracker.discover(selected)
    for target in selected:
        if any(
            target_is_covered(arena, instance, target, evaluator_class=evaluator_class)
            for instance in cases
        ):
            tracker.record(target, CoverageStatus.COVERED)
    return tracker.report(unsupported_scopes(arena, root))


__all__ = ["measure_coverage", "target_is_covered"]
