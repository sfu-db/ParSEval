"""Orchestration for concolic, coverage-directed instance generation."""

from __future__ import annotations

from collections.abc import Callable

from parseval.catalog import Catalog
from parseval.coverage import (
    CoverageExplorer,
    CoverageStatus,
    CoverageTarget,
    CoverageTracker,
    unsupported_scopes,
)
from parseval.instance import Instance
from parseval.parser.query import lower_query
from parseval.smt import SolveStatus, Solver
from parseval.terms.arena import TermArena
from parseval.uexpr.evaluate import UExprEvaluator, validate_instance
from parseval.uexpr.lowering import UExprCompiler

from .config import GenerationConfig
from .frontier import CoverageFrontier
from .model import CounterExample, GenerationResult, InvalidModelError, TargetResult


AttemptCallback = Callable[[CoverageTarget, int], None]
ProgressCallback = Callable[[str, CoverageTarget, int], None]


class Generator:
    """Compile once, explore concrete paths, and solve adjacent obligations."""

    def __init__(
        self,
        catalog: Catalog,
        *,
        config: GenerationConfig = GenerationConfig(),
        evaluator_class: type[UExprEvaluator] = UExprEvaluator,
        solver: Solver | None = None,
    ) -> None:
        self.catalog = catalog
        self.config = config
        self.evaluator_class = evaluator_class
        self.solver = solver or Solver(
            catalog,
            timeout_ms=config.timeout_ms,
            max_support=config.max_support,
            max_encoding_steps=config.max_encoding_steps,
            max_rows=config.max_rows,
            minimize=config.minimize,
        )

    def generate(
        self,
        sql: str,
        *,
        on_attempt: AttemptCallback | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> GenerationResult:
        arena, root = self._compile(sql)
        explorer = CoverageExplorer(
            arena,
            root,
            group_size=self.config.group_size,
            evaluator_class=self.evaluator_class,
        )
        tracker = CoverageTracker()
        frontier = CoverageFrontier()
        cases: list[Instance] = []
        results: list[TargetResult] = []
        attempted: set[str] = set()

        def covered_by_existing(target: CoverageTarget) -> bool:
            return any(explorer.covers(instance, target) for instance in cases)

        def discover(instance: Instance) -> None:
            snapshot = explorer.explore(instance)
            tracker.discover((*snapshot.observed, *snapshot.frontier))
            for target in snapshot.observed:
                tracker.record(target, CoverageStatus.COVERED)
                frontier.discard(target.id)
            for target in snapshot.frontier:
                if covered_by_existing(target):
                    tracker.record(target, CoverageStatus.COVERED)
                    frontier.discard(target.id)
                elif target.id not in attempted:
                    frontier.add(target, instance)

        empty = Instance.empty(self.catalog)
        initial = explorer.explore(empty)
        if initial.observed:
            cases.append(empty)
        tracker.discover((*initial.observed, *initial.frontier))
        for target in initial.observed:
            tracker.record(target, CoverageStatus.COVERED)
        for target in initial.frontier:
            frontier.add(target, empty)

        while frontier:
            if (
                self.config.max_attempts is not None
                and len(attempted) >= self.config.max_attempts
            ):
                break
            task = frontier.pop()
            target = task.target
            if target.id in attempted or tracker.is_covered(target):
                continue
            if covered_by_existing(target):
                tracker.record(target, CoverageStatus.COVERED)
                continue

            attempted.add(target.id)
            attempt = len(attempted)
            if on_attempt is not None:
                on_attempt(target, attempt)
            if on_progress is not None:
                on_progress("solve", target, attempt)
            solve = self.solver.solve(arena, target, seed=task.seed)
            results.append(TargetResult(target, solve))
            if solve.status is not SolveStatus.SAT:
                tracker.record(target, _coverage_status(solve.status), solve.reason)
                continue

            if on_progress is not None:
                on_progress("validate", target, attempt)
            instance = solve.instance
            violations = () if instance is None else validate_instance(instance)
            if (
                instance is None
                or violations
                or not explorer.covers(instance, target)
            ):
                raise InvalidModelError(
                    f"Symbolic model failed concrete replay for {target.id}: "
                    f"{violations!r}"
                )
            if instance not in cases:
                cases.append(instance)
            tracker.record(target, CoverageStatus.COVERED)
            if on_progress is not None:
                on_progress("rediscover", target, attempt)
            discover(instance)

        targets = tracker.targets
        counterexamples = tuple(
            CounterExample(
                instance,
                frozenset(
                    target.id for target in targets
                    if explorer.covers(instance, target)
                ),
            )
            for instance in cases
        )
        for case in counterexamples:
            for identity in case.covered:
                tracker.record(identity, CoverageStatus.COVERED)
        report = tracker.report(unsupported_scopes(arena, root))
        return GenerationResult(counterexamples, report, tuple(results))

    def _compile(self, sql: str):
        query = lower_query(sql, self.catalog)
        arena = TermArena(query.arena.context)
        root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
        return arena, root


def _coverage_status(status: SolveStatus) -> CoverageStatus:
    return {
        SolveStatus.BOUNDED_UNSAT: CoverageStatus.BOUNDED_UNSAT,
        SolveStatus.UNKNOWN: CoverageStatus.UNKNOWN,
        SolveStatus.UNSUPPORTED: CoverageStatus.UNSUPPORTED,
    }[status]


__all__ = ["AttemptCallback", "Generator", "ProgressCallback"]
