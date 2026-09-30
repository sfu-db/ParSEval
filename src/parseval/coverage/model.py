"""Value objects shared by coverage discovery, replay, and generation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from parseval.terms.terms import TermId
from .observation import Condition
from .witness import UnitWitnessPlan, WitnessPlan


@dataclass(frozen=True, slots=True)
class CoverageSite:
    """One occurrence in the compiled term, including its use context."""

    term: TermId
    path: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WitnessedObligation:
    """Existential finite support and local U-expression conditions."""

    plan: WitnessPlan | UnitWitnessPlan
    conditions: tuple[Condition, ...]
    relations: tuple[TermId, ...] = ()
    contexts: tuple[WitnessPlan, ...] = ()


@dataclass(frozen=True, slots=True)
class CoverageTarget:
    """A semantic outcome reachable through a finite witness."""

    id: str
    site: CoverageSite
    obligation: WitnessedObligation
    label: str = "productive"


class CoverageStatus(str, Enum):
    """What generation established for one discovered target.

    ``BOUNDED_UNSAT`` deliberately does not mean infeasible.  It only records
    that the configured finite SMT search was exhausted.
    """

    COVERED = "covered"
    BOUNDED_UNSAT = "bounded_unsat"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class CoverageOutcome:
    """Terminal information for a target that was observed or attempted."""

    target: str
    status: CoverageStatus
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Coverage inventory plus evidence collected for its targets.

    ``complete`` remains a capability statement: all encountered scopes have
    a complete finite witness analysis.  Use ``fully_covered`` to ask whether
    every discovered target has a concrete witness.
    """

    targets: frozenset[str]
    covered: frozenset[str]
    unsupported_scopes: tuple[str, ...] = ()
    outcomes: tuple[CoverageOutcome, ...] = ()

    def __post_init__(self) -> None:
        if not self.covered <= self.targets:
            raise ValueError("covered targets must belong to the target inventory")
        identities = tuple(outcome.target for outcome in self.outcomes)
        if len(identities) != len(set(identities)):
            raise ValueError("coverage outcomes must have unique target identities")
        if not set(identities) <= self.targets:
            raise ValueError("coverage outcomes must belong to the target inventory")

    @property
    def uncovered(self) -> frozenset[str]:
        return self.targets - self.covered

    @property
    def ratio(self) -> float:
        return (
            len(self.covered) / len(self.targets)
            if self.targets
            else 1.0
        )

    @property
    def complete(self) -> bool:
        return not self.unsupported_scopes

    @property
    def fully_covered(self) -> bool:
        return self.covered == self.targets and self.complete

    @property
    def bounded_unsat(self) -> frozenset[str]:
        return self._with_status(CoverageStatus.BOUNDED_UNSAT)

    @property
    def unknown(self) -> frozenset[str]:
        return self._with_status(CoverageStatus.UNKNOWN)

    @property
    def unsupported(self) -> frozenset[str]:
        return self._with_status(CoverageStatus.UNSUPPORTED)

    @property
    def not_attempted(self) -> frozenset[str]:
        decided = frozenset(outcome.target for outcome in self.outcomes)
        return self.targets - decided

    @property
    def unresolved(self) -> frozenset[str]:
        """Targets without concrete coverage evidence."""

        return self.targets - self.covered

    def outcome(self, target: str) -> CoverageOutcome | None:
        return next(
            (outcome for outcome in self.outcomes if outcome.target == target),
            None,
        )

    def _with_status(self, status: CoverageStatus) -> frozenset[str]:
        return frozenset(
            outcome.target for outcome in self.outcomes
            if outcome.status is status
        )


__all__ = [
    "CoverageOutcome",
    "CoverageReport",
    "CoverageSite",
    "CoverageStatus",
    "CoverageTarget",
    "WitnessedObligation",
]
