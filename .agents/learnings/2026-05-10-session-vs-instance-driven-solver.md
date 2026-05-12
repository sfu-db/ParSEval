# Session vs InstanceDrivenSolver: Merge the Ownership, Not the Abstraction Levels

**Date:** 2026-05-10
**Context:** After moving concrete realization policy into
`src/parseval/solver/instance.py`, the next design question is whether
`InstanceDrivenSolver` and `src/parseval/symbolic/session.py` should be fully
merged.

## The Question
Does `InstanceDrivenSolver` have the same capability as
`src/parseval/symbolic/session.py`? If so, why not merge them?

## Short Answer
Yes at the ownership level, no at the abstraction level.

- `symbolic/session.py` should not remain a separately-evolving symbolic
  implementation
- its real ownership belongs in the solver package
- but the low-level solve session and the high-level instance-driven solver
  should still remain separate classes inside solver

## What Each One Actually Represents

### ConstraintSolveSession
This is the low-level mutable solve context. It owns:

- variable declaration
- column reference bookkeeping
- DB constraint injection
- bound-table constraints
- SAT call preparation
- result assignment extraction

This is stateful, narrow, and solver-mechanical.

### InstanceDrivenSolver
This is the higher-level realization service. It owns:

- a solve session
- a mutator
- SMT-first realization policy
- heuristic fallback realization policy
- scope bootstrapping / seeding
- future mutation-cost policy

This is orchestration over realization, not raw constraint-session state.

## Why They Should Not Be Flattened Into One Class
If these two layers are collapsed directly into one class, the result becomes a
god object that simultaneously owns:

- solve-state bookkeeping
- constraint registration
- SMT invocation
- heuristic policy
- instance mutation
- row seeding
- future minimality policy

That class becomes harder to:

- test in isolation
- reuse from different symbolic flows
- extend with new mutation policies
- reason about when debugging branch coverage failures

## Correct Merge Boundary
The correct merge is:

- move the solve-session implementation out of symbolic
- keep it inside solver
- make symbolic use it only through solver-owned APIs

That is already the right directional split:

- `ConstraintSolveSession`: low-level solver primitive
- `InstanceDrivenSolver`: high-level instance realization service

## What Should Happen To symbolic/session.py

### Near Term
Keep `src/parseval/symbolic/session.py` as a compatibility wrapper only.

Requirements:
- no new business logic
- no divergence from solver implementation
- clear docstring that it is transitional

### Medium Term
Migrate call sites and tests toward:

- `parseval.solver.ConstraintSolveSession`
- `parseval.solver.InstanceDrivenSolver`

### Long Term
Delete `src/parseval/symbolic/session.py` once compatibility is no longer needed.

## Practical Rule For Future Changes

### Put code in `ConstraintSolveSession` when
- it changes variable declaration
- it changes constraint registration
- it changes how the SMT call is assembled
- it changes assignment extraction

### Put code in `InstanceDrivenSolver` when
- it changes realization strategy
- it changes heuristic fallback
- it changes row seeding behavior
- it changes mutation-cost policy
- it changes how to apply solver results to the live instance

### Do not put code in `symbolic/session.py` except
- temporary import compatibility

## Why This Matters For The Symbolic Refactor
The symbolic subsystem is moving toward a campaign engine. That campaign should
ask solver:

- "realize these branch-targeted constraints on the current instance"

It should not directly own the raw constraint-session implementation. Keeping
the two solver-side layers distinct makes that campaign API cleaner.

## Recommended End State

### Keep
- `ConstraintSolveSession` in `src/parseval/solver/instance.py` or a future
  solver session module
- `InstanceDrivenSolver` in `src/parseval/solver/instance.py`

### Remove Eventually
- `src/parseval/symbolic/session.py`

### Avoid
- one giant solver class that mixes session mechanics and realization policy

## Tags
#symbolic #solver #session #architecture #compatibility
