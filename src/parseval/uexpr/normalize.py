"""Factor-preserving U-semiring and E-SPNF normalization."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.binding import shift_vars, substitute_row, swap_row_vars
from parseval.terms.names import SchemaId
from parseval.terms.sorts import RowSort, SeqSort
from parseval.terms.terms import FieldPayload, RowLambdaPayload, TermId
from parseval.terms.walk import post_order

from .order import normalize_order_term

TermRewrite = Callable[[TermId, tuple[TermId, ...]], TermId]


def simplify_uexpr(arena: TermArena, root: TermId) -> TermId:
    """Simplify a U-expression while preserving its factorized structure.

    This pass performs beta reduction, row-projection simplification, and order
    normalization. It intentionally does not distribute products over sums.
    """

    return _fixed_point(arena, root, lambda term, children: _simplify_term(
        arena, term, children
    ))


def to_espnf(
    arena: TermArena,
    root: TermId,
) -> TermId:
    """Normalize a U-expression into factorized E-SPNF.

    This applies the size-preserving U-semiring SPNF rules.  In particular,
    summations are hoisted and squash/not factors are consolidated, while a
    product of additive choices remains factorized instead of being expanded
    by distributivity.
    """

    simplified = simplify_uexpr(arena, root)

    def rewrite(term: TermId, children: tuple[TermId, ...]) -> TermId:
        local = _simplify_term(arena, term, children)
        return _normalize_spnf_term(arena, local)

    return _fixed_point(arena, simplified, rewrite)


def _fixed_point(
    arena: TermArena,
    root: TermId,
    rewrite: TermRewrite,
) -> TermId:
    memo: dict[TermId, TermId] = {}

    def visit(term: TermId) -> TermId:
        for current in post_order(arena, (term,)):
            if current not in memo:
                memo[current] = rewrite(
                    current,
                    tuple(memo[child] for child in arena[current].children),
                )
        return memo[term]

    previous = root
    while True:
        memo.clear()
        current = visit(previous)
        if current == previous:
            return current
        previous = current


def _simplify_term(
    arena: TermArena,
    term: TermId,
    children: tuple[TermId, ...],
) -> TermId:
    result = arena.rebuild(term, children)
    node = arena[result]
    if isinstance(node, nodes.RowIdentityEq):
        identity_children = tuple(
            _identity_projection(arena, child) or child for child in node.children
        )
        result = arena.rebuild(result, identity_children)
        node = arena[result]
    if isinstance(node, nodes.SeqMap):
        source, mapper = node.children
        mapper_node = cast(nodes.RowLambda, arena[mapper])
        identity = _identity_projection(arena, mapper_node.children[0])
        if identity is not None and isinstance(arena[identity], nodes.RowVar):
            result = source
            node = arena[result]
    if isinstance(node.sort, SeqSort):
        result = normalize_order_term(arena, result)
        node = arena[result]
    if isinstance(node, nodes.At):
        bag, row = node.children
        bag_node = arena[bag]
        if isinstance(bag_node, nodes.BagLambda):
            function = cast(nodes.RowLambda, arena[bag_node.children[0]])
            result = substitute_row(arena, function.children[0], row)
    return result


def _normalize_spnf_term(arena: TermArena, term: TermId) -> TermId:
    node = arena[term]
    if isinstance(node, nodes.Mul):
        squashes = tuple(
            factor
            for factor in node.children
            if isinstance(arena[factor], nodes.Squash)
        )
        if len(squashes) > 1:
            remainder = tuple(
                factor for factor in node.children if factor not in squashes
            )
            combined = arena.intern_checked(
                nodes.Mul,
                (arena[factor].children[0] for factor in squashes),
            )
            return arena.intern_checked(
                nodes.Mul,
                (*remainder, arena.intern_checked(nodes.Squash, (combined,))),
            )
        complements = tuple(
            factor for factor in node.children if isinstance(arena[factor], nodes.UNot)
        )
        if len(complements) > 1:
            remainder = tuple(
                factor for factor in node.children if factor not in complements
            )
            combined = arena.intern_checked(
                nodes.Add,
                (arena[factor].children[0] for factor in complements),
            )
            return arena.intern_checked(
                nodes.Mul,
                (*remainder, arena.intern_checked(nodes.UNot, (combined,))),
            )
        for position, factor in enumerate(node.children):
            factor_node = arena[factor]
            remainder = (*node.children[:position], *node.children[position + 1 :])
            if isinstance(factor_node, nodes.Sum):
                schema, body = _sum_body(arena, factor)
                shifted = tuple(
                    shift_vars(arena, item, row_delta=1) for item in remainder
                )
                product = arena.intern_checked(nodes.Mul, (*shifted, body))
                return _sum(arena, schema, product)
        return term
    if not isinstance(node, nodes.Sum):
        return term

    schema, body = _sum_body(arena, term)
    body_node = arena[body]
    if isinstance(body_node, nodes.Add):
        return arena.intern_checked(
            nodes.Add,
            (_sum(arena, schema, alternative) for alternative in body_node.children),
        )
    if isinstance(body_node, nodes.Sum):
        inner_schema, inner_body = _sum_body(arena, body)
        if schema < inner_schema:
            return _sum(
                arena,
                inner_schema,
                _sum(arena, schema, swap_row_vars(arena, inner_body)),
            )
    return term


def _sum_body(arena: TermArena, term: TermId) -> tuple[SchemaId, TermId]:
    node = cast(nodes.Sum, arena[term])
    function = cast(nodes.RowLambda, arena[node.children[0]])
    payload = cast(RowLambdaPayload, function.payload)
    return payload.input_schema, function.children[0]


def _sum(arena: TermArena, schema: SchemaId, body: TermId) -> TermId:
    function = arena.intern_checked(
        nodes.RowLambda,
        (body,),
        RowLambdaPayload(schema),
    )
    return arena.intern_checked(nodes.Sum, (function,))


def _identity_projection(arena: TermArena, term: TermId) -> TermId | None:
    term_node = arena[term]
    if not isinstance(term_node, nodes.Row) or not isinstance(term_node.sort, RowSort):
        return None
    fields = term_node.children
    expected_sort = term_node.sort
    if not fields:
        return None
    row: TermId | None = None
    for position, field in enumerate(fields):
        node = arena[field]
        if (
            not isinstance(node, nodes.Field)
            or not isinstance(node.payload, FieldPayload)
            or node.payload.index != position
        ):
            return None
        (candidate,) = node.children
        if row is None:
            row = candidate
        elif row != candidate:
            return None
    if row is None or arena[row].sort != expected_sort:
        return None
    schema = arena.context.schema(expected_sort.schema)
    return row if len(schema.fields) == len(fields) else None


__all__ = ["simplify_uexpr", "to_espnf"]
