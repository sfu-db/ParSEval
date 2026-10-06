"""Checked structural analysis of row projections."""

from __future__ import annotations

from dataclasses import dataclass

from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.names import SchemaId
from parseval.terms.sorts import RowFunctionSort, RowSort
from parseval.terms.terms import (
    FieldPayload,
    RowLambdaPayload,
    TermId,
    VariablePayload,
)


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


__all__ = [
    "RowProjectionMap",
    "analyze_projection_map",
]
