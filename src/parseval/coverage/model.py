"""Branch sites of a U-expression and their outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field

from parseval.terms.terms import TermId


@dataclass(frozen=True, slots=True, order=True)
class Target:
    """One outcome of one U-semiring decision, such as a predicate being FALSE."""

    site: int
    outcome: str


class Sites:
    """Stable identities for term occurrences in one compiled query.

    A site is a path of child positions from the query root. Hash-consing
    shares equal subterms, so a TermId alone cannot distinguish occurrences.
    """

    ROOT = 0

    def __init__(self) -> None:
        self._children: dict[tuple[int, int], int] = {}
        self._paths: list[tuple[int, ...]] = [()]
        self.terms: dict[int, TermId] = {}

    def child(self, site: int, index: int) -> int:
        key = (site, index)
        child = self._children.get(key)
        if child is None:
            child = self._children[key] = len(self._paths)
            self._paths.append((*self._paths[site], index))
        return child

    def path(self, site: int) -> tuple[int, ...]:
        return self._paths[site]


@dataclass(slots=True)
class Coverage:
    """Outcomes reached while executing one instance.

    ``covered`` outcomes are produced by stored rows. A ``stable`` outcome is
    produced independently of candidate rows. ``witnesses`` hold, for covered
    outcomes that depend on candidate rows, predicates over open inputs that
    keep them covered; ``candidates`` hold predicates that would cover an
    outcome that is not covered yet.
    """

    reached: set[Target] = field(default_factory=set)
    covered: set[Target] = field(default_factory=set)
    stable: set[Target] = field(default_factory=set)
    witnesses: dict[Target, list[TermId]] = field(default_factory=dict)
    candidates: dict[Target, list[TermId]] = field(default_factory=dict)

    @property
    def uncovered(self) -> set[Target]:
        return self.reached - self.covered


__all__ = ["Coverage", "Sites", "Target"]
