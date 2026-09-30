from __future__ import annotations

from typing import cast

from parseval.errors import expect

from . import terms as nodes
from .arena import TermArena
from .binding import relation_binders_in_child, row_binders_in_child
from .sorts import RelationSort, RowFunctionSort, RowSort, Sort
from .terms import TermId
from .walk import post_order


def verify_uexpr(arena: TermArena, root: TermId) -> Sort:
    """Require a closed U-expression, including aggregate and order extensions."""
    for term_id in post_order(arena, (root,)):
        node = arena[term_id]
        expect(
            type(node) in nodes.UEXPR_NODES,
            f"{node.key} must be lowered before using a U-expression",
        )
    return verify_closed(arena, root)


def verify_closed(
    arena: TermArena,
    root: TermId,
    *,
    rows: tuple[RowSort, ...] = (),
    relations: tuple[RelationSort, ...] = (),
) -> Sort:
    """Verify binder scope and variable sorts under an explicit lexical context."""

    memo: set[tuple[TermId, tuple[RowSort, ...], tuple[RelationSort, ...]]] = set()

    def visit(
        term_id: TermId,
        rows: tuple[RowSort, ...],
        relations: tuple[RelationSort, ...],
    ) -> None:
        key = (term_id, rows, relations)
        if key in memo:
            return
        memo.add(key)
        node = arena[term_id]
        if isinstance(node, nodes.RowVar):
            variable = node.payload
            expect(variable.depth < len(rows), "Free row variable in closed term")
            expect(
                node.sort == rows[variable.depth],
                "Row variable sort does not match its binder",
            )
            return

        if isinstance(node, nodes.RelVar):
            variable = node.payload
            expect(
                variable.depth < len(relations),
                "Free relational variable in closed term",
            )
            expect(
                node.sort == relations[variable.depth],
                "Relational variable sort does not match its binder",
            )
            return

        for position, child in enumerate(node.children):
            child_rows = rows
            child_relations = relations
            if row_binders_in_child(type(node), position):
                child_rows = (cast(RowFunctionSort, node.sort).input, *rows)
            if relation_binders_in_child(type(node), position):
                definition_sort = arena[node.children[0]].sort
                child_relations = (cast(RelationSort, definition_sort), *relations)
            visit(child, child_rows, child_relations)

    visit(root, rows, relations)
    return arena[root].sort
