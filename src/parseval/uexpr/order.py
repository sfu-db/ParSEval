"""Canonical checked views over UOrder sequence-set terms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.names import SchemaId
from parseval.terms.sorts import SeqSort
from parseval.terms.terms import LiteralPayload, OrderKeySpec, OrderPayload, TermId
from parseval.terms.types import INTEGER


@dataclass(frozen=True, slots=True)
class OrderKeyTerm:
    function: TermId
    spec: OrderKeySpec


@dataclass(frozen=True, slots=True)
class OrderNormalForm:
    """One order, one natural interval, and one observable row projection."""

    source: TermId
    keys: tuple[OrderKeyTerm, ...]
    offset: int
    fetch: int | None
    mapper: TermId | None
    source_schema: SchemaId
    output_schema: SchemaId
    bindings: tuple[TermId, ...] = ()


def analyze_order_normal_form(
    arena: TermArena,
    root: TermId,
) -> OrderNormalForm | None:
    """Recognize the canonical supported UOrder observation shape."""

    if not isinstance(arena[root].sort, SeqSort):
        return None
    current = root
    bindings: list[TermId] = []
    while isinstance(arena[current], nodes.LetRel):
        definition, current = arena[current].children
        bindings.append(definition)
    mapper: TermId | None = None
    operations: list[tuple[type[nodes.TermNode], int, int | None]] = []
    while True:
        node = arena[current]
        if isinstance(node, nodes.SeqMap):
            if mapper is not None:
                return None
            current, mapper = node.children
            continue
        if isinstance(node, nodes.Take):
            count, current = node.children
            value = _natural_literal(arena, count)
            if value is None:
                return None
            operations.append((nodes.Take, value, None))
            continue
        if isinstance(node, nodes.Drop):
            count, current = node.children
            value = _natural_literal(arena, count)
            if value is None:
                return None
            operations.append((nodes.Drop, value, None))
            continue
        if isinstance(node, nodes.Slice):
            offset, count, current = node.children
            offset_value = _natural_literal(arena, offset)
            count_value = _natural_literal(arena, count)
            if offset_value is None or count_value is None:
                return None
            operations.append((nodes.Slice, offset_value, count_value))
            continue
        break
    ordered = arena[current]
    if not isinstance(ordered, nodes.OrderBy) or not isinstance(
        ordered.payload, OrderPayload
    ):
        return None
    source = ordered.children[0]
    closed_source = source
    for definition in reversed(bindings):
        closed_source = arena.intern_checked(
            nodes.LetRel,
            (definition, closed_source),
        )
    keys = tuple(
        OrderKeyTerm(function, spec)
        for function, spec in zip(
            ordered.children[1:], ordered.payload.keys, strict=True
        )
    )
    start = 0
    end: int | None = None
    for node_type, first, second in reversed(operations):
        if node_type is nodes.Drop:
            start = min(start + first, end) if end is not None else start + first
        elif node_type is nodes.Take:
            candidate = start + first
            end = candidate if end is None else min(end, candidate)
        else:
            original_end = end
            start = (
                min(start + first, original_end)
                if original_end is not None
                else start + first
            )
            candidate = start + cast(int, second)
            end = candidate if original_end is None else min(original_end, candidate)
    fetch = None if end is None else max(end - start, 0)
    source_schema = cast(SeqSort, ordered.sort).schema
    output_schema = cast(SeqSort, arena[root].sort).schema
    return OrderNormalForm(
        closed_source,
        keys,
        start,
        fetch,
        mapper,
        source_schema,
        output_schema,
        tuple(bindings),
    )


def normalize_order_term(arena: TermArena, root: TermId) -> TermId:
    """Rebuild a recognized UOrder term into its unique structural form."""

    normal = analyze_order_normal_form(arena, root)
    if normal is None:
        return root
    if normal.bindings:
        return root
    ordered = arena.intern_checked(
        nodes.OrderBy,
        (normal.source, *(key.function for key in normal.keys)),
        OrderPayload(tuple(key.spec for key in normal.keys)),
    )
    result = ordered
    if normal.offset or normal.fetch is not None:
        offset = _literal(arena, normal.offset)
        if normal.fetch is None:
            result = arena.intern_checked(nodes.Drop, (offset, result))
        else:
            result = arena.intern_checked(
                nodes.Slice,
                (offset, _literal(arena, normal.fetch), result),
            )
    if normal.mapper is not None:
        result = arena.intern_checked(nodes.SeqMap, (result, normal.mapper))
    return result


def _natural_literal(arena: TermArena, term: TermId) -> int | None:
    node = arena[term]
    if not isinstance(node, nodes.Literal) or not isinstance(
        node.payload, LiteralPayload
    ):
        return None
    value = node.payload.value
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _literal(arena: TermArena, value: int) -> TermId:
    return arena.intern_checked(
        nodes.Literal,
        (),
        LiteralPayload(value, INTEGER),
    )


__all__ = [
    "OrderKeyTerm",
    "OrderNormalForm",
    "analyze_order_normal_form",
    "normalize_order_term",
]
