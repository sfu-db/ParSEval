# Symbolic Solver Boundary Plan

## Objective
Move concrete row synthesis policy out of `src/parseval/symbolic` and into
`src/parseval/solver`, while keeping symbolic focused on instance-driven branch
coverage over a live `Instance`.

## Research Summary
- `src/parseval/symbolic/generator.py` had accumulated three responsibilities:
  scope execution, branch scheduling, and concrete-data synthesis.
- `src/parseval/symbolic/session.py` and `mutator.py` already contained solver-
  and mutation-specific logic that did not need to live in symbolic.
- `CoverageStore`, `BranchScheduler`, and branch recording already provide the
  correct symbolic abstraction for pursuing both positive and negative coverage.

## Proposed Architecture
- `src/parseval/solver/instance.py` owns:
  - SMT-backed constraint solving
  - heuristic constraint realization when SMT is insufficient
  - instance mutation and row seeding
- `src/parseval/symbolic` owns:
  - branch extraction and lowering
  - execution tracing and coverage
  - coverage-goal orchestration over the solver service

## Validation Strategy
- Solver regression: `python -m unittest tests.solver.test_instance_driven`
- Symbolic regressions:
  - `python -m unittest tests.symbolic.test_generator`
  - `python -m unittest tests.symbolic.test_solve_session`
  - `python -m unittest tests.symbolic.test_scheduler`
  - `python -m unittest tests.symbolic.test_coverage`
- Bounded BIRD probe after the refactor.

## Success Criteria
- [ ] Concrete synthesis policy is no longer embedded in `SymbolicDataGenerator`.
- [ ] Symbolic generator delegates solving/mutation to `src/parseval/solver`.
- [ ] Symbolic execution schedules both positive and negative branch goals.
- [ ] Targeted symbolic and solver tests pass.
