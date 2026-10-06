"""Branch outcomes of U-semiring decisions reached by concolic execution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from parseval.instance.valuation import Valuation
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


class Recorder:
    """An execution observer that keeps a bounded number of witnesses per outcome.

    An outcome is covered when some binding of stored rows produces it. Its
    condition ``presence > 0 AND outcome`` is a Term over the open inputs:
    witnesses of covered outcomes are kept so solving can preserve them,
    candidates of uncovered outcomes so solving can reach them.
    """

    def __init__(self, sites: Sites, valuation: Valuation, limit: int = 4, every: Target | None = None):
        """``every`` names one outcome whose conditions are all kept."""
        self.sites = sites
        self.v = valuation
        self.limit = limit
        self.every = every
        self.coverage = Coverage()

    def child(self, site: int, index: int) -> int:
        return self.sites.child(site, index)

    def wants(self, site: int, outcome: str, covered: bool) -> bool:
        """Whether to build another candidate condition for an outcome.

        An outcome stored rows cover needs none. Otherwise conditions are
        built until ``limit`` are kept; those folding to a constant are not
        kept, so bindings that cannot reach the outcome do not crowd out
        those that can.
        """
        target = Target(site, outcome)
        if covered or target in self.coverage.covered:
            return False
        return target == self.every or len(self.coverage.candidates.get(target, ())) < self.limit

    def observe(self, site: int, term: TermId, outcome: str, covered: bool, condition: Callable[[], TermId]) -> None:
        """Record one reached outcome; build its condition only to keep a witness.

        A covered outcome whose condition folds to TRUE is stable: candidate
        rows cannot change it, so it needs neither witnesses nor solving.
        """
        coverage = self.coverage
        target = Target(site, outcome)
        self.sites.terms.setdefault(site, term)
        coverage.reached.add(target)
        if target in coverage.stable:
            return
        store = coverage.witnesses if covered else coverage.candidates
        if covered:
            coverage.covered.add(target)
        if target != self.every and len(store.get(target, ())) >= self.limit:
            return
        reached = condition()
        if reached == self.v.true:
            coverage.stable.add(target)
            coverage.witnesses.pop(target, None)
        elif not self.v.constant(reached):
            witnesses = store.setdefault(target, [])
            if reached not in witnesses:
                witnesses.append(reached)


__all__ = ["Coverage", "Recorder", "Sites", "Target"]
