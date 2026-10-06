"""Collect U-semiring branch outcomes from one concolic execution."""

from __future__ import annotations

from collections.abc import Callable

from parseval.instance.valuation import Valuation
from parseval.terms.terms import TermId

from .model import Coverage, Sites, Target


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


__all__ = ["Recorder"]
