"""Capture-avoiding operations for row and relational De Bruijn namespaces."""

from __future__ import annotations

from typing import cast

from parseval.errors import IRValidationError

from . import terms as nodes
from .arena import TermArena
from .sorts import RelationSort, RowSort
from .terms import TermId


def row_binders_in_child(node_type: type[nodes.TermNode], position: int) -> int:
    """Return the number of row binders introduced for one child."""

    return int(node_type is nodes.RowLambda and position == 0)


def relation_binders_in_child(node_type: type[nodes.TermNode], position: int) -> int:
    """Return the number of relation binders introduced for one child."""

    return int(node_type is nodes.LetRel and position == 1)


def shift_vars(
    arena: TermArena,
    root: TermId,
    *,
    row_delta: int = 0,
    relation_delta: int = 0,
    row_cutoff: int = 0,
    relation_cutoff: int = 0,
) -> TermId:
    """Shift free variables in both namespaces in one memoized traversal."""

    if row_cutoff < 0 or relation_cutoff < 0:
        raise ValueError("Variable cutoffs must be nonnegative")
    arena[root]
    if row_delta == 0 and relation_delta == 0:
        return root
    memo: dict[tuple[TermId, int, int], TermId] = {}

    root_key = (root, row_cutoff, relation_cutoff)
    stack = [(root_key, False)]
    while stack:
        key, expanded = stack.pop()
        if key in memo:
            continue
        term_id, rcut, relcut = key
        node = arena[term_id]
        if isinstance(node, nodes.RowVar):
            variable = node.payload
            if variable.depth < rcut or row_delta == 0:
                result = term_id
            else:
                depth = variable.depth + row_delta
                if depth < rcut:
                    raise IRValidationError("Row-variable shift crossed its cutoff")
                result = arena.row_var(depth, cast(RowSort, node.sort))
            memo[key] = result
            continue
        if isinstance(node, nodes.RelVar):
            variable = node.payload
            if variable.depth < relcut or relation_delta == 0:
                result = term_id
            else:
                depth = variable.depth + relation_delta
                if depth < relcut:
                    raise IRValidationError(
                        "Relational-variable shift crossed its cutoff"
                    )
                result = arena.rel_var(depth, cast(RelationSort, node.sort))
            memo[key] = result
            continue
        child_keys = tuple(
            (
                child,
                rcut + row_binders_in_child(type(node), position),
                relcut + relation_binders_in_child(type(node), position),
            )
            for position, child in enumerate(node.children)
        )
        if not expanded:
            stack.append((key, True))
            stack.extend((child_key, False) for child_key in reversed(child_keys))
            continue
        memo[key] = arena.rebuild(
            term_id, tuple(memo[child_key] for child_key in child_keys)
        )

    shifted = memo[root_key]
    if arena[shifted].sort != arena[root].sort:
        raise IRValidationError("Shifting changed the term sort")
    return shifted


def substitute_row(
    arena: TermArena,
    root: TermId,
    replacement: TermId,
    *,
    depth: int = 0,
) -> TermId:
    """Capture-avoiding substitution for one row De Bruijn variable.

    The matched binder is removed, so variables beyond it are decremented.
    Replacement variables are shifted when traversal enters nested row or relation
    binders.  This is the beta-reduction primitive for ``AT(BAG_LAM, row)``.
    """

    if depth < 0:
        raise ValueError("Row substitution depth must be nonnegative")
    replacement_sort = arena[replacement].sort
    if not isinstance(replacement_sort, RowSort):
        raise IRValidationError("Row substitution requires a row replacement")
    memo: dict[tuple[TermId, int, int], TermId] = {}

    root_key = (root, 0, 0)
    stack = [(root_key, False)]
    while stack:
        key, expanded = stack.pop()
        if key in memo:
            continue
        term_id, binders, relation_binders = key
        node = arena[term_id]
        target = depth + binders
        if isinstance(node, nodes.RowVar):
            variable = node.payload
            if variable.depth == target:
                if node.sort != replacement_sort:
                    raise IRValidationError("Row substitution sort mismatch")
                result = shift_vars(
                    arena,
                    replacement,
                    row_delta=binders,
                    relation_delta=relation_binders,
                )
            elif variable.depth > target:
                result = arena.row_var(variable.depth - 1, cast(RowSort, node.sort))
            else:
                result = term_id
            memo[key] = result
            continue
        if isinstance(node, nodes.RelVar):
            memo[key] = term_id
            continue
        child_keys = tuple(
            (
                child,
                binders + row_binders_in_child(type(node), position),
                relation_binders
                + relation_binders_in_child(type(node), position),
            )
            for position, child in enumerate(node.children)
        )
        if not expanded:
            stack.append((key, True))
            stack.extend((child_key, False) for child_key in reversed(child_keys))
            continue
        memo[key] = arena.rebuild(
            term_id, tuple(memo[child_key] for child_key in child_keys)
        )

    substituted = memo[root_key]
    if arena[substituted].sort != arena[root].sort:
        raise IRValidationError("Row substitution changed the term sort")
    return substituted


def swap_row_vars(
    arena: TermArena,
    root: TermId,
    *,
    first_depth: int = 0,
) -> TermId:
    """Capture-safely exchange two adjacent free row-variable depths."""

    if first_depth < 0:
        raise ValueError("Row swap depth must be nonnegative")
    memo: dict[tuple[TermId, int], TermId] = {}

    root_key = (root, 0)
    stack = [(root_key, False)]
    while stack:
        key, expanded = stack.pop()
        if key in memo:
            continue
        term_id, binders = key
        node = arena[term_id]
        if isinstance(node, nodes.RowVar):
            variable = node.payload
            lower = first_depth + binders
            if variable.depth == lower:
                result = arena.row_var(variable.depth + 1, cast(RowSort, node.sort))
            elif variable.depth == lower + 1:
                result = arena.row_var(variable.depth - 1, cast(RowSort, node.sort))
            else:
                result = term_id
            memo[key] = result
            continue
        if isinstance(node, nodes.RelVar):
            memo[key] = term_id
            continue
        child_keys = tuple(
            (
                child,
                binders + row_binders_in_child(type(node), position),
            )
            for position, child in enumerate(node.children)
        )
        if not expanded:
            stack.append((key, True))
            stack.extend((child_key, False) for child_key in reversed(child_keys))
            continue
        memo[key] = arena.rebuild(
            term_id, tuple(memo[child_key] for child_key in child_keys)
        )

    swapped = memo[root_key]
    if arena[swapped].sort != arena[root].sort:
        raise IRValidationError("Swapping changed the term sort")
    return swapped
