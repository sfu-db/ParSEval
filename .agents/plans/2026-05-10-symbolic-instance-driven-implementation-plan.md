# Symbolic Instance-Driven Implementation Plan

## Objective
Implement the refined symbolic instance-driven design in `src/parseval/symbolic`
using TDD, while preserving the currently supported query family:

- single-scope
- non-correlated
- no joins
- no aggregates
- filter / `OR` / projection `CASE`

## Research Summary

- The symbolic package already contains the core files targeted by the refined
  design: `generator.py`, `branch_solver.py`, `coverage.py`, `recorder.py`,
  `scheduler.py`, `lowerer.py`, `session.py`, and `encoder.py`.
- The current tests in `tests/symbolic/` already cover branch types,
  extraction, recording, coverage, scheduling, lowering, solving, and the
  package-level generator API.
- The current architecture is functionally green, but it still has the exact
  overlap described in the refined design:
  - `generator.py` owns the public API but delegates orchestration to
    `PlannerBranchSolver`
  - `recorder.py` exposes only an append-only list rather than an explicit
    per-run trace
  - `session.py` both solves and mutates
  - `encoder.py` still mixes execution and observation call sites
- Existing learnings strongly support:
  - stable `ScopePlan` step ids
  - immutable branch records
  - strategy/registry seams over hardcoded conditional growth
  - lightweight symbolic package boundaries

## Implementation Approach

The work will proceed in red-green-refactor slices:

1. Add tests for execution-trace and generator-ownership semantics.
2. Introduce `ExecutionTrace` and make coverage ingest traces directly.
3. Move the top-level execute/trace/cover/schedule/lower/solve/mutate loop into
   `generator.py`.
4. Narrow `branch_solver.py` to a compatibility helper.
5. Separate solve results from instance mutation through a mutator helper.
6. Extract encoder-local observation helpers without widening the public API.
7. Add lowering registry seams and placeholder group/having branch hooks.

## Validation Strategy

- Keep `python -m unittest discover -s tests/symbolic -p 'test_*.py'` green
  after each slice.
- Add focused tests first for each new behavior:
  - `ExecutionTrace` creation and coverage ingestion
  - generator-owned supported-scope gating
  - solve-result separation from mutation
  - instance-driven re-execution loop behavior
  - encoder helper preservation for filter and `CASE`

## Success Criteria

- [ ] `SymbolicDataGenerator` owns the instance-driven orchestration loop
- [ ] `branch_solver.py` no longer duplicates top-level orchestration
- [ ] trace and coverage are explicit but separate concepts
- [ ] solve state and instance mutation are separate concepts
- [ ] current symbolic tests pass
- [ ] new tests cover the refined boundaries directly

## Execution Order

1. Trace and coverage boundary
2. Generator ownership and supported-scope policy
3. Solve vs mutation separation
4. Instance-driven loop refactor
5. Encoder responsibility reduction
6. Lowering extension seams
