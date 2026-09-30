"""Semantic outcomes and independent row support for U-expression factors."""

from __future__ import annotations

from dataclasses import dataclass

from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.terms import TermId
from parseval.uexpr.espnf import ProductTermView
from parseval.uexpr.observation import (
    Condition, PredicateCondition, TruthOutcome, WeightCondition,
)


@dataclass(frozen=True, slots=True)
class FactorOutcome:
    """A local test and the factor that supplies its finite row domain."""

    condition: Condition
    support: TermId
    label: str


def factor_choices(
    arena: TermArena, product: ProductTermView, factor: TermId
) -> tuple[FactorOutcome, ...]:
    node = arena[factor]
    one = arena.intern_checked(nodes.One)
    if isinstance(node, nodes.Indicator):
        predicate = node.children[0]
        if predicate == product.output_equality or isinstance(
            arena[predicate], nodes.RowIdentityEq
        ):
            return (FactorOutcome(WeightCondition(factor, 1), factor, "equal"),)
        predicate_node = arena[predicate]
        if isinstance(predicate_node, (nodes.True3, nodes.False3, nodes.Unknown3)):
            truth = {
                nodes.True3: TruthOutcome.TRUE,
                nodes.False3: TruthOutcome.FALSE,
                nodes.Unknown3: TruthOutcome.UNKNOWN,
            }[type(predicate_node)]
            return (FactorOutcome(PredicateCondition(predicate, truth), one, truth.name.lower()),)
        truths = (TruthOutcome.TRUE, TruthOutcome.FALSE)
        if _may_be_unknown(arena, predicate):
            truths = (*truths, TruthOutcome.UNKNOWN)
        return tuple(
            FactorOutcome(PredicateCondition(predicate, truth), one, truth.name.lower())
            for truth in truths
        )
    if isinstance(node, nodes.At):
        return (
            FactorOutcome(WeightCondition(factor, 0, 0), one, "absent"),
            FactorOutcome(WeightCondition(factor, 1, 1), factor, "single"),
            FactorOutcome(WeightCondition(factor, 2), factor, "multiple"),
        )
    if isinstance(node, nodes.Add):
        children = tuple(
            child for child in node.children if not isinstance(arena[child], nodes.Zero)
        )
        return (
            *(FactorOutcome(WeightCondition(child, 1), child, f"contribution.{index}")
              for index, child in enumerate(children)),
            FactorOutcome(WeightCondition(factor, 0, 0), one, "zero"),
        )
    if isinstance(node, nodes.Zero):
        return (FactorOutcome(WeightCondition(factor, 0, 0), one, "zero"),)
    if isinstance(node, nodes.One):
        return (FactorOutcome(WeightCondition(factor, 1, 1), one, "one"),)
    return (
        FactorOutcome(WeightCondition(factor, 0, 0), one, "zero"),
        FactorOutcome(WeightCondition(factor, 1), factor, "positive"),
    )


def _may_be_unknown(arena: TermArena, term: TermId) -> bool:
    node = arena[term]
    if isinstance(node, nodes.Unknown3):
        return True
    if isinstance(
        node,
        (
            nodes.True3,
            nodes.False3,
            nodes.IsNull,
            nodes.IsNotNull,
            nodes.IsNotDistinct,
            nodes.RowIdentityEq,
        ),
    ):
        return False
    if isinstance(node, nodes.ToPredicate):
        return bool(getattr(arena[node.children[0]].sort, "nullable", False))
    if isinstance(node, (nodes.Eq3, nodes.Lt3, nodes.Like3, nodes.ILike3)):
        return any(
            bool(getattr(arena[child].sort, "nullable", False))
            for child in node.children
        )
    if isinstance(node, (nodes.And3, nodes.Or3, nodes.Not3)):
        return any(_may_be_unknown(arena, child) for child in node.children)
    return True


__all__ = ["FactorOutcome", "factor_choices"]
