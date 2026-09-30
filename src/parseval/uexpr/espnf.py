"""Read-only structural views over extended sum-product normal form."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.names import SchemaId
from parseval.terms.sorts import BagSort
from parseval.terms.terms import (
    RowLambdaPayload,
    TermId,
    VariablePayload,
)


@dataclass(frozen=True, slots=True)
class ProductTermView:
    term: TermId
    binders: tuple[SchemaId, ...]
    factors: tuple[TermId, ...]
    output_equality: TermId | None
    output: TermId | None
    residuals: tuple[TermId, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.residuals


@dataclass(frozen=True, slots=True)
class ESPNFView:
    schema: SchemaId
    alternatives: tuple[ProductTermView, ...]

    @property
    def complete(self) -> bool:
        return all(alternative.complete for alternative in self.alternatives)

    @property
    def residuals(self) -> tuple[TermId, ...]:
        return tuple(
            residual
            for alternative in self.alternatives
            for residual in alternative.residuals
        )


def inspect_bag_espnf(arena: TermArena, bag: TermId) -> ESPNFView:
    """Expose the available structure of a factorized E-SPNF bag."""

    node = cast(nodes.BagLambda, arena[bag])
    function = cast(nodes.RowLambda, arena[node.children[0]])
    bag_sort = cast(BagSort, node.sort)
    alternatives = tuple(
        _inspect_product(arena, alternative)
        for alternative in _flatten(arena, function.children[0], nodes.Add)
    )
    return ESPNFView(bag_sort.schema, alternatives)


def _inspect_product(arena: TermArena, term: TermId) -> ProductTermView:
    original = term
    binders: list[SchemaId] = []
    while isinstance(arena[term], nodes.Sum):
        function = cast(nodes.RowLambda, arena[arena[term].children[0]])
        payload = cast(RowLambdaPayload, function.payload)
        term = function.children[0]
        binders.append(payload.input_schema)

    factors = _flatten(arena, term, nodes.Mul)
    output_equalities: list[TermId] = []
    residuals: list[TermId] = []
    for factor in factors:
        node = arena[factor]
        if isinstance(node, nodes.Indicator):
            predicate = node.children[0]
            if constructs_output_equality(arena, predicate, len(binders)):
                output_equalities.append(predicate)
        elif type(node) not in {
            nodes.At,
            nodes.Squash,
            nodes.UNot,
            nodes.Zero,
            nodes.One,
        }:
            residuals.append(factor)
    output_equality = output_equalities[0] if len(output_equalities) == 1 else None
    output = (
        _output_constructor(arena, output_equality, len(binders))
        if output_equality is not None
        else None
    )
    return ProductTermView(
        original,
        tuple(binders),
        factors,
        output_equality,
        output,
        tuple(residuals),
    )


def _output_constructor(
    arena: TermArena,
    equality: TermId,
    output_depth: int,
) -> TermId:
    left, right = arena[equality].children
    if _is_row_variable(arena, left, output_depth):
        return right
    return left


def _constructs_output(
    arena: TermArena,
    children: tuple[TermId, ...],
    output_depth: int,
) -> bool:
    left, right = children
    return _is_row_variable(arena, left, output_depth) != _is_row_variable(
        arena,
        right,
        output_depth,
    )


def constructs_output_equality(
    arena: TermArena, predicate: TermId, output_depth: int
) -> bool:
    """Whether a row identity binds this bag's output row at a given depth."""

    node = arena[predicate]
    return isinstance(node, nodes.RowIdentityEq) and _constructs_output(
        arena, node.children, output_depth
    )


def _is_row_variable(arena: TermArena, term: TermId, depth: int) -> bool:
    node = arena[term]
    return (
        isinstance(node, nodes.RowVar)
        and isinstance(node.payload, VariablePayload)
        and node.payload.depth == depth
    )


def _flatten(
    arena: TermArena, term: TermId, node_type: type[nodes.TermNode]
) -> tuple[TermId, ...]:
    result: list[TermId] = []
    stack = [term]
    while stack:
        current = stack.pop()
        node = arena[current]
        if type(node) is node_type:
            stack.extend(reversed(node.children))
        else:
            result.append(current)
    return tuple(result)


__all__ = [
    "ESPNFView",
    "ProductTermView",
    "constructs_output_equality",
    "inspect_bag_espnf",
]
