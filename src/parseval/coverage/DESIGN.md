# Coverage and generation design

## Goal

Generate small, valid database instances that exercise semantic outcomes of a
compiled query. Coverage is defined over typed U-expression occurrences, not
parser branches or the mere existence of output rows. A rejected filter row is
therefore observable even when the final query result is empty.

All claims are relative to finite witness analysis, the supported term
semantics, and configured SMT bounds. The system never interprets exhaustion
of a bounded search as unrestricted unsatisfiability.

## Domain model

A coverage obligation has four parts:

1. A `CoverageSite` identifies a term occurrence by its term and root-relative
   path. The path distinguishes reuse of an interned term in different lexical
   contexts.
2. A witness plan gives a finite row or unit domain in which the occurrence is
   reachable. Context plans retain correlated outer-row bindings and relation
   bindings retain `LetRel` environments.
3. Typed conditions describe the semantic outcome: three-valued predicate
   truth, scalar NULL, multiplicity interval, bag cardinality, or group input
   cardinality.
4. A stable target identity deduplicates rediscovery. Different existential
   witness plans may establish the same semantic identity.

`CoverageTarget` is solver-independent. Both the concrete evaluator and SMT
encoder consume its `WitnessedObligation`, which makes exact replay possible.

## Components

`CoverageExplorer` is configured for one arena/root pair. Given an instance it
returns a `CoverageSnapshot` containing observed targets and adjacent frontier
targets. It seeds local factor outcomes even when no productive query branch is
currently feasible.

`CoverageTracker` owns the inventory and evidence. It deliberately knows
nothing about Z3 or generation. Concrete evidence is strongest and cannot be
overwritten by a later bounded solver failure.

`CoverageFrontier` is a deterministic FIFO scheduler. Rediscovery updates a
target's nearby concrete seed without changing queue order.

`Generator` compiles once and coordinates exploration, solving, validation,
and frontier expansion. `generate` is only a convenience facade and
`GenerationConfig` owns every generation/SMT budget.

## Execution protocol

1. Compile and verify the query into a typed U-expression.
2. Explore the empty instance. Record directly observed unit outcomes and
   enqueue adjacent obligations.
3. Pop one target from the deterministic frontier and invoke the bounded SMT
   backend with the instance that discovered it as a preference seed.
4. Preserve `bounded_unsat`, `unknown`, and `unsupported` as distinct results.
5. For SAT, validate catalog constraints and replay the exact obligation with
   the concrete evaluator. A mismatch is an `InvalidModelError`, never
   coverage.
6. Explore the validated instance, merge new targets, and continue until the
   frontier or attempt budget is exhausted.
7. Recompute each retained instance's coverage over the final inventory and
   produce a report.

## Reporting contract

`CoverageReport.targets` is the discovered inventory and `covered` contains
targets with concrete evidence. Outcomes record `covered`, `bounded_unsat`,
`unknown`, or `unsupported`; inventory members without an outcome are exposed
as `not_attempted`.

`ratio` is discovered-target progress, not a completeness proof. `complete`
means finite witness analysis encountered no unsupported scope.
`fully_covered` additionally requires every discovered target to have a
concrete witness. Aggregate value classes, ordered positions, and window
outcomes remain explicit unsupported scopes until they have paired concrete
and symbolic semantics.

## Verification

- Every SAT model is independently replayed.
- Unit tests compare concrete and symbolic condition semantics for NULL,
  predicates, multiplicities, joins, correlation, CTE relation environments,
  aggregate input sizes, LIKE, and catalog constraints.
- `scripts/benchmark_postgres_coverage.py` runs paired queries from
  `data/postgres.csv`. It compares U-expression results and also materializes
  each generated instance in a fresh temporary SQLite database. PostgreSQL to
  SQLite portability failures are reported separately from generation and
  semantic mismatches.

New observation classes require all three pieces in the same change: a finite
witness analysis, a concrete observer, and an exact symbolic predicate.
