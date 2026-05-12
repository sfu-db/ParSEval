# Symbolic Solver Boundary: Keep Coverage in Symbolic and Row Synthesis in Solver

**Date:** 2026-05-10
**Context:** Refactoring the planner-anchored symbolic module after widening BIRD
support exposed that `SymbolicDataGenerator` was accumulating too much solve
policy.

## The Problem
When symbolic orchestration owns SMT solving, heuristic fallback, and instance
mutation directly, two problems appear:

- the solver package becomes a low-level adapter instead of the concrete-data
  generation boundary
- symbolic drifts toward witness generation rather than branch-coverage control

## The Solution
Move instance-driven concrete synthesis into `src/parseval/solver` as a service
that owns:

- constraint solving
- heuristic realization of simple constraints
- live-instance mutation and row seeding

Then keep `src/parseval/symbolic` responsible for:

- extracting and lowering branch goals
- executing the query on the current instance
- recording coverage
- scheduling both positive and negative branch goals

Compatibility wrappers in symbolic are acceptable while tests still import the
old class names.

## Why This Works
The solver boundary becomes about producing concrete rows, regardless of whether
they come from SMT or heuristics. The symbolic boundary becomes about deciding
which branch outcome to pursue next on the current instance.

## How to Apply
For later symbolic extensions:

- add new concrete realization strategies in `src/parseval/solver`, not in the
  symbolic generator
- add new coverage policies, branch kinds, and scheduling logic in
  `src/parseval/symbolic`
- preserve thin compatibility layers when moving classes across package
  boundaries so tests and callers can migrate incrementally

## Tags
#symbolic #solver #architecture #instance-driven #coverage
