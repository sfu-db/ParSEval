"""Finite-witness exploration of U-expression outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from parseval.instance import Instance
from parseval.terms.arena import TermArena
from parseval.terms.terms import TermId
from parseval.uexpr.espnf import ProductTermView, inspect_bag_espnf
from parseval.uexpr.evaluate import UExprEvaluator
from parseval.uexpr.observation import (
    BagCardinalityCondition, GroupCardinalityCondition, Condition, NullCondition, PredicateCondition,
    WeightCondition,
)
from parseval.uexpr.witness import (
    UnitWitnessPlan, WitnessPlan, support_plans,
)

from .decisions import FactorOutcome, factor_choices
from .model import CoverageSite, CoverageTarget, WitnessedObligation
from .scopes import CoverageScope, closed_scopes, closed_unit_sites, fingerprint


DEFAULT_GROUP_SIZE = 3


@dataclass(frozen=True, slots=True)
class CoverageSnapshot:
    """Observed targets and the adjacent frontier for one concrete instance."""

    observed: tuple[CoverageTarget, ...]
    frontier: tuple[CoverageTarget, ...]


class CoverageExplorer:
    """Configured concrete explorer for one compiled U-expression."""

    def __init__(
        self,
        arena: TermArena,
        root: TermId,
        *,
        group_size: int = DEFAULT_GROUP_SIZE,
        evaluator_class: type[UExprEvaluator] = UExprEvaluator,
    ) -> None:
        if group_size < 1:
            raise ValueError("group_size must be positive")
        self.arena = arena
        self.root = root
        self.group_size = group_size
        self.evaluator_class = evaluator_class

    def explore(self, instance: Instance) -> CoverageSnapshot:
        observed, frontier = explore_paths(
            self.arena,
            self.root,
            instance,
            group_size=self.group_size,
            evaluator_class=self.evaluator_class,
        )
        return CoverageSnapshot(observed, frontier)

    def covers(self, instance: Instance, target: CoverageTarget) -> bool:
        from .evaluate import target_is_covered

        return target_is_covered(
            self.arena,
            instance,
            target,
            evaluator_class=self.evaluator_class,
        )


def observed_paths(
    arena: TermArena,
    root: TermId,
    instance: Instance,
    *,
    group_size: int = DEFAULT_GROUP_SIZE,
    evaluator_class: type[UExprEvaluator] = UExprEvaluator,
) -> tuple[CoverageTarget, ...]:
    return explore_paths(arena, root, instance, group_size=group_size, evaluator_class=evaluator_class)[0]


def next_paths(
    arena: TermArena,
    root: TermId,
    instance: Instance,
    *,
    group_size: int = DEFAULT_GROUP_SIZE,
    evaluator_class: type[UExprEvaluator] = UExprEvaluator,
) -> tuple[CoverageTarget, ...]:
    return explore_paths(arena, root, instance, group_size=group_size, evaluator_class=evaluator_class)[1]


def explore_paths(
    arena: TermArena,
    root: TermId,
    instance: Instance,
    *,
    group_size: int = DEFAULT_GROUP_SIZE,
    evaluator_class: type[UExprEvaluator] = UExprEvaluator,
) -> tuple[tuple[CoverageTarget, ...], tuple[CoverageTarget, ...]]:
    """Observe complete vectors and derive one-decision and local neighbors."""

    if group_size < 1:
        raise ValueError("group_size must be positive")
    evaluator = evaluator_class(arena, instance)
    observed: dict[str, CoverageTarget] = {}
    frontier: dict[str, CoverageTarget] = {}

    for scope in closed_scopes(arena, root):
        view = inspect_bag_espnf(arena, scope.bag)
        for alternative, branch in enumerate(view.alternatives):
            choices = tuple(factor_choices(arena, branch, factor) for factor in branch.factors)
            tests = tuple(tuple(outcome.condition for outcome in group) for group in choices)
            for plan in support_plans(arena, scope.bag, branch):
                vectors = evaluator.observed_choices(
                    plan, tests, scope.relations, scope.contexts
                )
                if vectors:
                    for vector in vectors:
                        selected = tuple(choices[index][choice] for index, choice in enumerate(vector))
                        for path_plan in _path_plans(arena, scope.bag, branch, selected):
                            target = _target(
                                arena, scope, alternative, path_plan,
                                tuple(item.condition for item in selected),
                                f"alternative.{alternative}.path.{'.'.join(map(str, vector))}",
                            )
                            if evaluator.witnessed_conditions(
                                target.obligation.plan, target.obligation.conditions,
                                target.obligation.relations, target.obligation.contexts,
                            ):
                                observed[target.id] = target
                        for position, alternatives in enumerate(choices):
                            for index, choice in enumerate(alternatives):
                                if index == vector[position]:
                                    continue
                                neighbor = (*vector[:position], index, *vector[position + 1:])
                                selected = tuple(choices[i][chosen] for i, chosen in enumerate(neighbor))
                                for neighbor_plan in _path_plans(arena, scope.bag, branch, selected):
                                    candidate = _target(
                                        arena, scope, alternative, neighbor_plan,
                                        tuple(item.condition for item in selected),
                                        f"alternative.{alternative}.path.{'.'.join(map(str, neighbor))}",
                                    )
                                    if not evaluator.witnessed_conditions(
                                        candidate.obligation.plan,
                                        candidate.obligation.conditions,
                                        candidate.obligation.relations,
                                        candidate.obligation.contexts,
                                    ):
                                        frontier[candidate.id] = candidate
                    continue

                # A productive path need not be feasible. Seed each local
                # outcome from the same independent support domain.
                productive_conditions = tuple(WeightCondition(factor) for factor in branch.factors)
                for productive_plan in support_plans(arena, scope.bag, branch, branch.factors):
                    productive = _target(
                        arena, scope, alternative, productive_plan, productive_conditions,
                        f"alternative.{alternative}.seed",
                    )
                    if not evaluator.witnessed_conditions(
                        productive_plan, productive.obligation.conditions,
                        scope.relations, scope.contexts,
                    ):
                        frontier[productive.id] = productive
                for position, alternatives in enumerate(choices):
                    for index, outcome in enumerate(alternatives):
                        factors = list(branch.factors)
                        factors[position] = outcome.support
                        for local_plan in support_plans(
                            arena, scope.bag, branch, tuple(factors)
                        ):
                            candidate = _target(
                                arena, scope, alternative, local_plan,
                                (outcome.condition,),
                                f"alternative.{alternative}.factor.{position}.outcome.{index}",
                            )
                            if not evaluator.witnessed_conditions(
                                local_plan, candidate.obligation.conditions,
                                scope.relations, scope.contexts,
                            ):
                                frontier[candidate.id] = candidate

    for site in closed_unit_sites(arena, root):
        for label, condition in (
            ("empty", BagCardinalityCondition(site.source, 0, 0)),
            ("nonempty", BagCardinalityCondition(site.source, 1)),
            ("group.single", GroupCardinalityCondition(site.term, 1, 1)),
            ("group.pair", GroupCardinalityCondition(site.term, 2, 2)),
            (f"group.above.{group_size}", GroupCardinalityCondition(site.term, group_size + 1)),
        ):
            path = ".".join(map(str, site.path))
            target = CoverageTarget(
                f"site.{path}.input.{fingerprint(arena, site.source)}.{label}",
                CoverageSite(site.term, site.path),
                WitnessedObligation(UnitWitnessPlan(), (condition,), site.relations),
                f"input.{label}",
            )
            if evaluator.witnessed_conditions(
                target.obligation.plan,
                target.obligation.conditions,
                target.obligation.relations,
                target.obligation.contexts,
            ):
                observed[target.id] = target
            else:
                frontier[target.id] = target

    for identity in observed:
        frontier.pop(identity, None)
    return tuple(observed.values()), tuple(frontier.values())


def _path_plans(
    arena: TermArena,
    bag: TermId,
    branch: ProductTermView,
    selected: tuple[FactorOutcome, ...],
) -> tuple[WitnessPlan, ...]:
    return support_plans(
        arena, bag, branch, tuple(item.support for item in selected)
    )


def _target(
    arena: TermArena,
    scope: CoverageScope,
    alternative: int,
    plan: WitnessPlan,
    conditions: tuple[Condition, ...],
    label: str,
) -> CoverageTarget:
    path = ".".join(map(str, scope.path))
    signature = ".".join(_condition_key(arena, condition) for condition in conditions)
    context_signature = ".".join(
        fingerprint(arena, context.product.term) for context in scope.contexts
    )
    identity = (
        f"site.{path}.alternative.{alternative}."
        f"context.{context_signature}."
        f"support.{fingerprint(arena, plan.product.term)}.conditions.{signature}"
    )
    return CoverageTarget(
        identity,
        CoverageSite(scope.source, scope.path),
        WitnessedObligation(
            plan, conditions,
            scope.relations, scope.contexts,
        ),
        label,
    )


def _condition_key(arena: TermArena, condition: Condition) -> str:
    term = fingerprint(arena, condition.term)
    if isinstance(condition, (WeightCondition, BagCardinalityCondition, GroupCardinalityCondition)):
        return f"{type(condition).__name__}.{term}.{condition.minimum}.{condition.maximum}"
    if isinstance(condition, PredicateCondition):
        return f"predicate.{term}.{condition.truth.name}"
    if isinstance(condition, NullCondition):
        return f"null.{term}.{condition.is_null}"
    raise TypeError(type(condition).__name__)


__all__ = [
    "CoverageExplorer",
    "CoverageSnapshot",
    "explore_paths",
    "next_paths",
    "observed_paths",
]
