from __future__ import annotations

from collections.abc import Iterable, Iterator

from .arena import TermArena
from .terms import TermId


def post_order(arena: TermArena, roots: Iterable[TermId]) -> Iterator[TermId]:
    """Deterministic DAG post-order; each node is yielded once."""

    seen: set[TermId] = set()
    for root in roots:
        arena[root]
        stack: list[tuple[TermId, bool]] = [(root, False)]
        while stack:
            term_id, expanded = stack.pop()
            if term_id in seen:
                continue
            if expanded:
                seen.add(term_id)
                yield term_id
                continue
            stack.append((term_id, True))
            for child in reversed(arena[term_id].children):
                if child not in seen:
                    stack.append((child, False))
