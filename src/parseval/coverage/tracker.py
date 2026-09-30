"""Mutable bookkeeping for a single coverage exploration run."""

from __future__ import annotations

from collections.abc import Iterable

from .model import (
    CoverageOutcome,
    CoverageReport,
    CoverageStatus,
    CoverageTarget,
)


class CoverageTracker:
    """Own the target inventory and the strongest evidence for each target.

    Discovery is intentionally separate from solving.  This makes it possible
    to use the same tracker with an SMT generator, a fuzzer, or user-provided
    concrete instances without teaching the coverage package about a solver.
    """

    def __init__(self) -> None:
        self._targets: dict[str, CoverageTarget] = {}
        self._outcomes: dict[str, CoverageOutcome] = {}

    @property
    def targets(self) -> tuple[CoverageTarget, ...]:
        return tuple(self._targets.values())

    def discover(self, targets: Iterable[CoverageTarget]) -> tuple[CoverageTarget, ...]:
        """Register targets and return only identities not seen before."""

        added: list[CoverageTarget] = []
        for target in targets:
            previous = self._targets.get(target.id)
            if previous is not None:
                # Witness plans are existential evidence for an outcome.  The
                # same semantic target can be rediscovered with a different
                # concrete context plan, so object equality is intentionally
                # stronger than target identity here.
                continue
            self._targets[target.id] = target
            added.append(target)
        return tuple(added)

    def record(
        self,
        target: CoverageTarget | str,
        status: CoverageStatus,
        reason: str | None = None,
    ) -> None:
        identity = target if isinstance(target, str) else target.id
        if identity not in self._targets:
            raise KeyError(f"unknown coverage target {identity!r}")
        previous = self._outcomes.get(identity)
        # Concrete replay is stronger than every symbolic result.  Never let a
        # later bounded failure erase evidence that already covers the target.
        if previous is not None and previous.status is CoverageStatus.COVERED:
            return
        self._outcomes[identity] = CoverageOutcome(identity, status, reason)

    def is_covered(self, target: CoverageTarget | str) -> bool:
        identity = target if isinstance(target, str) else target.id
        outcome = self._outcomes.get(identity)
        return outcome is not None and outcome.status is CoverageStatus.COVERED

    def report(self, unsupported_scopes: tuple[str, ...] = ()) -> CoverageReport:
        outcomes = tuple(
            self._outcomes[identity]
            for identity in self._targets
            if identity in self._outcomes
        )
        covered = frozenset(
            outcome.target for outcome in outcomes
            if outcome.status is CoverageStatus.COVERED
        )
        return CoverageReport(
            frozenset(self._targets),
            covered,
            unsupported_scopes,
            outcomes,
        )


__all__ = ["CoverageTracker"]
