"""Bounded, adaptive solving of weighted U-expression coverage obligations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from time import monotonic

import z3

from parseval.catalog import Catalog
from parseval.coverage import CoverageTarget
from parseval.instance import Instance
from parseval.terms.arena import TermArena
from parseval.terms.names import RelationId

from .budget import Budget, BudgetExceeded
from .completion import plan_completion
from .encoding import UExprEncoder, _Environment
from .instance import SymbolicInstance
from .support import grow_support, support_bounds
from .prepared import PreparedTerms
from .values import UnsupportedEncodingError, _literal, _sum


class SolveStatus(str, Enum):
    SAT = "sat"
    BOUNDED_UNSAT = "bounded_unsat"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class SolveStatistics:
    attempts: int = 0
    support: tuple[tuple[RelationId, int], ...] = ()
    encoding_steps: int = 0
    circuit_nodes: int = 0


@dataclass(frozen=True, slots=True)
class SolveResult:
    status: SolveStatus
    instance: Instance | None = None
    reason: str | None = None
    statistics: SolveStatistics = SolveStatistics()


class Solver:
    """Search small row classes first; grow value diversity only when needed.

    SAT results are decoded directly from the encoded constraints.
    Exhausting finite support is explicitly distinct from unrestricted UNSAT.
    The deadline covers encoding and refinement as well as Z3 checks.
    """

    def __init__(
        self,
        catalog: Catalog,
        *,
        timeout_ms: int | None = 5_000,
        max_support: int = 8,
        max_encoding_steps: int = 100_000,
        max_rows: int = 10_000,
        minimize: bool = True,
    ) -> None:
        if timeout_ms is not None and timeout_ms <= 0:
            raise ValueError("timeout_ms must be positive")
        if min(max_support, max_encoding_steps, max_rows) < 1:
            raise ValueError("support, encoding, and row limits must be positive")
        self.catalog = catalog
        self.timeout_ms = timeout_ms
        self.max_support = max_support
        self.max_encoding_steps = max_encoding_steps
        self.max_rows = max_rows
        self.minimize = minimize

    def solve(
        self, arena: TermArena, target: CoverageTarget, *, seed: Instance | None = None
    ) -> SolveResult:
        if seed is not None and seed.catalog.context is not self.catalog.context:
            raise ValueError("Seed and solver must share a Context")
        started = monotonic()
        budget = Budget(
            None if self.timeout_ms is None else started + self.timeout_ms / 1000,
            self.max_encoding_steps,
        )
        support, upper = support_bounds(self.catalog, arena, target, seed, self.max_support)
        attempts = 0
        encoder = None
        prepared = PreparedTerms(arena)

        def result(status, instance=None, reason=None):
            return SolveResult(status, instance, reason, SolveStatistics(
                attempts, tuple(support.items()), budget.steps,
                0 if encoder is None else len(encoder.circuit.nodes),
            ))

        def check(solver, *, optional=False):
            remaining = budget.remaining_ms()
            if optional:
                # Seed retention and shrinking must not consume the entire
                # budget after a feasible candidate has already been found.
                remaining = 100 if remaining is None else min(100, max(1, remaining // 4))
            if remaining is not None:
                solver.set(timeout=remaining)
            return solver.check()

        try:
            while True:
                budget.tick(0)
                attempts += 1
                database = SymbolicInstance(self.catalog, support)
                encoder = UExprEncoder(arena, database, budget=budget, prepared=prepared)
                goal = encoder.target(target)
                database.completion = plan_completion(encoder, target, _Environment())
                schema = encoder.schema_constraints()
                solver = z3.Solver()
                solver.add(goal, *schema, *encoder.constraints)
                # A concrete instance must fit the materialization budget. It
                # bounds physical rows, independently of symbolic class count.
                total = _sum(*(entry.multiplicity for entries in database.relations.values() for entry in entries))
                solver.add(total <= self.max_rows)
                status = check(solver)
                if status == z3.unknown:
                    return result(SolveStatus.UNKNOWN, reason=solver.reason_unknown())
                if status == z3.unsat:
                    expanded = grow_support(support, upper)
                    if expanded == support:
                        return result(SolveStatus.BOUNDED_UNSAT, reason=(
                            "No model within per-relation support and materialized-row bounds"
                        ))
                    support = expanded
                    continue
                model = solver.model()
                # Seed values are preferences, not assertions that can rule out
                # a coverage goal. Try them only after finding a feasible model.
                if seed is not None:
                    preferences = _seed_preferences(database, encoder, seed)
                    if preferences:
                        solver.push()
                        solver.add(*preferences)
                        preferred = check(solver, optional=True)
                        if preferred == z3.sat:
                            model = solver.model()
                        solver.pop()
                        if preferred == z3.sat:
                            solver.add(*preferences)
                if self.minimize:
                    best = model.eval(total, model_completion=True).as_long()
                    low = 0
                    while low < best:
                        midpoint = (low + best) // 2
                        solver.push()
                        solver.add(total <= midpoint)
                        smaller = check(solver, optional=True)
                        if smaller == z3.sat:
                            model = solver.model()
                            best = model.eval(total, model_completion=True).as_long()
                        elif smaller == z3.unsat:
                            low = midpoint + 1
                        solver.pop()
                        if smaller == z3.unknown:
                            break
                instance = database.materialize(model, max_rows=self.max_rows, budget=budget)
                budget.tick(0)
                return result(SolveStatus.SAT, instance)
        except BudgetExceeded as error:
            return result(SolveStatus.UNKNOWN, reason=str(error))
        except UnsupportedEncodingError as error:
            return result(SolveStatus.UNSUPPORTED, reason=str(error))


def _seed_preferences(database, encoder, seed):
    observed = encoder.circuit.dependencies(encoder.observed_values)
    preferences = []
    for relation, entries in database.relations.items():
        rows = []
        for row in seed.rows(relation):
            if row.values not in rows:
                rows.append(row.values)
        for entry, row in zip(entries, rows):
            for position, (value, concrete) in enumerate(zip(entry.row.values, row, strict=True)):
                if position in database.completion.get(relation, ()) or value.value.get_id() in observed:
                    continue
                preferences.append(value.is_null if concrete is None else z3.And(
                    z3.Not(value.is_null), value.value == _literal(concrete, value.sql_type)
                ))
    return preferences


__all__ = ["SolveResult", "SolveStatistics", "SolveStatus", "Solver"]
