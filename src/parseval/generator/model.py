"""Results of concolic database generation."""

from __future__ import annotations

from dataclasses import dataclass, field

from parseval.coverage import Target
from parseval.instance import Instance
from parseval.smt import Status


@dataclass(frozen=True, slots=True)
class Attempt:
    """One solve for an uncovered outcome and how it ended."""

    target: Target
    label: str
    status: Status
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """Outcomes of the final database; ``failed`` maps labels to solve statuses."""

    reached: tuple[str, ...]
    covered: tuple[str, ...]
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def ratio(self) -> float:
        return len(self.covered) / len(self.reached) if self.reached else 0.0


@dataclass(frozen=True, slots=True)
class GenerationResult:
    instance: Instance | None
    nonempty: bool
    coverage: CoverageReport
    attempts: tuple[Attempt, ...]
    unsupported: str | None = None


__all__ = ["Attempt", "CoverageReport", "GenerationResult"]
