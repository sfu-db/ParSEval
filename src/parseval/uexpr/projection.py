"""Checked structural analysis of row projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.binding import substitute_row
from parseval.terms.builder import IRBuilder
from parseval.terms.names import RelationId, SchemaId
from parseval.terms.sorts import BagSort, RowFunctionSort, RowSort
from parseval.terms.terms import (
    BaseRelationPayload,
    FieldPayload,
    RowLambdaPayload,
    TermId,
    VariablePayload,
)

from .espnf import inspect_bag_espnf


@dataclass(frozen=True, slots=True)
class BaseBagLineage:
    """Exact non-duplicating lineage from one registered base relation."""

    relation: RelationId
    input_positions: tuple[int, ...]
    restrictions: tuple[TermId, ...] = ()

    def compose(self, projection: RowProjectionMap) -> BaseBagLineage | None:
        if not projection.direct:
            return None
        positions = tuple(
            self.input_positions[cast(int, position)]
            for position in projection.direct_input_positions
        )
        return BaseBagLineage(self.relation, positions, self.restrictions)


@dataclass(frozen=True, slots=True)
class RowProjectionMap:
    """Exact direct-input mapping for one checked row lambda."""

    input_schema: SchemaId
    output_schema: SchemaId
    direct_input_positions: tuple[int | None, ...]

    @property
    def direct(self) -> bool:
        return all(position is not None for position in self.direct_input_positions)


def analyze_projection_map(
    arena: TermArena,
    mapper: TermId,
) -> RowProjectionMap | None:
    """Return the exact direct-field shape of a checked row mapper."""

    node = arena[mapper]
    if (
        not isinstance(node, nodes.RowLambda)
        or not isinstance(node.payload, RowLambdaPayload)
        or not isinstance(node.sort, RowFunctionSort)
        or not isinstance(node.sort.result, RowSort)
    ):
        return None
    input_schema = node.payload.input_schema
    output_schema = node.sort.result.schema
    body = node.children[0]
    body_node = arena[body]
    if isinstance(body_node, nodes.RowVar):
        payload = body_node.payload
        if payload.depth != 0 or input_schema != output_schema:
            return None
        width = len(arena.context.schema(input_schema).fields)
        return RowProjectionMap(
            input_schema,
            output_schema,
            tuple(range(width)),
        )
    if not isinstance(body_node, nodes.Row) or not isinstance(body_node.sort, RowSort):
        return None
    positions = tuple(_direct_field(arena, field) for field in body_node.children)
    return RowProjectionMap(
        input_schema,
        output_schema,
        positions,
    )


def direct_field_position(arena: TermArena, row_lambda: TermId) -> int | None:
    """Return the input position selected by a scalar row lambda."""

    node = arena[row_lambda]
    if not isinstance(node, nodes.RowLambda) or not isinstance(
        node.payload, RowLambdaPayload
    ):
        return None
    return _direct_field(arena, node.children[0])


def project_bag(
    arena: TermArena,
    source: TermId,
    mapper: TermId,
) -> TermId | None:
    """Build the exact bag image of ``source`` under a checked row mapper."""

    source_sort = arena[source].sort
    projection = analyze_projection_map(arena, mapper)
    if not isinstance(source_sort, BagSort) or projection is None:
        return None
    mapper_body = arena[mapper].children[0]
    builder = IRBuilder(arena)
    projected = builder.bag_lam(
        projection.output_schema,
        lambda output: builder.sum(
            RowSort(projection.input_schema),
            lambda input_row: builder.mul(
                builder.at(source, input_row),
                builder.indicator(
                    builder.row_identity_eq(
                        substitute_row(
                            arena,
                            mapper_body,
                            builder.resolve(input_row),
                        ),
                        output,
                    )
                ),
            ),
        ),
    )
    from .normalize import to_espnf

    return to_espnf(arena, builder.finish(projected))


def analyze_base_lineage(
    arena: TermArena,
    source: TermId,
) -> BaseBagLineage | None:
    """Recognize an exact non-duplicating base projection in canonical E-SPNF."""

    node = arena[source]
    if isinstance(node, nodes.Base):
        payload = node.payload
        if not isinstance(payload, BaseRelationPayload) or not isinstance(
            node.sort, BagSort
        ):
            return None
        width = len(arena.context.schema(node.sort.schema).fields)
        return BaseBagLineage(payload.relation, tuple(range(width)))
    if not isinstance(node, nodes.BagLambda) or not isinstance(node.sort, BagSort):
        return None
    view = inspect_bag_espnf(arena, source)
    if len(view.alternatives) != 1:
        return None
    product = view.alternatives[0]
    if len(product.binders) != 1 or product.output_equality is None:
        return None
    factors = product.factors
    base_factors = tuple(
        factor for factor in factors if isinstance(arena[factor], nodes.At)
    )
    indicator_factors = tuple(
        factor for factor in factors if isinstance(arena[factor], nodes.Indicator)
    )
    if len(base_factors) != 1 or len(indicator_factors) != len(factors) - 1:
        return None
    base_factor = base_factors[0]
    at = arena[base_factor]
    base = arena[at.children[0]]
    if (
        not isinstance(base, nodes.Base)
        or not isinstance(base.payload, BaseRelationPayload)
        or not isinstance(base.sort, BagSort)
        or product.binders[0] != base.sort.schema
    ):
        return None
    if not _row_variable(arena, at.children[1], 0):
        return None
    identity_factor = next(
        factor
        for factor in indicator_factors
        if arena[factor].children[0] == product.output_equality
    )
    equality = arena[product.output_equality]
    left, right = equality.children
    if _row_variable(arena, left, 1):
        projected = right
    elif _row_variable(arena, right, 1):
        projected = left
    else:
        return None
    positions = _row_positions(arena, projected, 0)
    if positions is None or len(positions) != len(
        arena.context.schema(view.schema).fields
    ):
        return None
    restrictions = tuple(
        factor for factor in indicator_factors if factor != identity_factor
    )
    return BaseBagLineage(base.payload.relation, positions, restrictions)


def _direct_field(arena: TermArena, term: TermId) -> int | None:
    node = arena[term]
    if not isinstance(node, nodes.Field) or not isinstance(node.payload, FieldPayload):
        return None
    row = arena[node.children[0]]
    if (
        not isinstance(row, nodes.RowVar)
        or not isinstance(row.payload, VariablePayload)
        or row.payload.depth != 0
    ):
        return None
    return node.payload.index


def _row_positions(
    arena: TermArena,
    term: TermId,
    depth: int,
) -> tuple[int, ...] | None:
    node = arena[term]
    if _row_variable(arena, term, depth):
        if not isinstance(node.sort, RowSort):
            return None
        return tuple(range(len(arena.context.schema(node.sort.schema).fields)))
    if not isinstance(node, nodes.Row):
        return None
    positions: list[int] = []
    for child in node.children:
        field = arena[child]
        if not isinstance(field, nodes.Field) or not isinstance(
            field.payload, FieldPayload
        ):
            return None
        if not _row_variable(arena, field.children[0], depth):
            return None
        positions.append(field.payload.index)
    return tuple(positions)


def _row_variable(arena: TermArena, term: TermId, depth: int) -> bool:
    node = arena[term]
    return (
        isinstance(node, nodes.RowVar)
        and isinstance(node.payload, VariablePayload)
        and node.payload.depth == depth
    )


__all__ = [
    "BaseBagLineage",
    "RowProjectionMap",
    "analyze_base_lineage",
    "analyze_projection_map",
    "direct_field_position",
    "project_bag",
]
