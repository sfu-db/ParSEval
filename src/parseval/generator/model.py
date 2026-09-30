"""Results produced by the coverage-directed generation engine."""

from __future__ import annotations

from dataclasses import dataclass

from parseval.coverage import CoverageReport, CoverageTarget
from parseval.instance import Instance
from parseval.smt import SolveResult


class InvalidModelError(RuntimeError):
    """The independent evaluator rejected a purported satisfying model."""


@dataclass(frozen=True, slots=True)
class CounterExample:
    """A concrete database and all known targets it covers."""

    instance: Instance
    covered: frozenset[str]


@dataclass(frozen=True, slots=True)
class TargetResult:
    """The bounded solver result for one attempted target."""

    target: CoverageTarget
    solve: SolveResult


@dataclass(frozen=True, slots=True)
class GenerationResult:
    """Concrete witnesses, coverage evidence, and solver diagnostics."""

    counterexamples: tuple[CounterExample, ...]
    coverage: CoverageReport
    results: tuple[TargetResult, ...]

    @property
    def instances(self) -> tuple[Instance, ...]:
        return tuple(case.instance for case in self.counterexamples)


__all__ = [
    "CounterExample",
    "GenerationResult",
    "InvalidModelError",
    "TargetResult",
]
