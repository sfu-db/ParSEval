# Symbolic BIRD Support Plan

## Objective
Broaden `src/parseval/symbolic` so the current symbolic module can synthesize
non-empty witnesses for more `tests/symbolic/test_symbolic_bird.py` queries
without relying on speculative generation.

## Research Summary
- `SymbolicDataGenerator.generate()` currently rejects any graph with more than
  one scope and only accepts single-table, non-aggregate, non-join scopes.
- The planner already exposes useful symbolic structure for joins:
  `ScopePlan` annotations capture `Join` step conditions, referenced columns,
  and join key expressions.
- `SymbolicScopeEncoder` can execute join, aggregate, sort, and set-operation
  steps once rows exist in the instance.
- `SolveSession` can already assign values across multiple tables and mutate
  the live `Instance`, but only for columns explicitly referenced by the
  lowered goal.
- The first failing BIRD query is a two-table join where the branch predicate
  is solvable, but the symbolic lowerer omits the join key equalities and the
  generator never creates the second table row.

## Proposed Architecture
- Keep the branch-goal flow for coverage-oriented solving.
- Add a symbolic scope-witness path that lowers the scope’s structural
  constraints, especially join key equalities and step conditions, into the
  existing `SolveSession`.
- Permit broader scope shapes in the generator when they are executable by the
  current symbolic encoder.
- If a scope has no useful predicate columns, seed the minimal required base
  rows inside the symbolic mutator rather than leaving the scope empty.

## Implementation Approach
- Extend `BranchGoalLowerer` with helpers for:
  - scope-wide witness constraints
  - join key equalities from `Join` steps
  - dependency-aware expression rewriting for simple solved subqueries when
    feasible
- Extend `InstanceMutator` with a minimal symbolic-only row seeding helper for
  base scan tables when solver assignments are empty.
- Update `SymbolicDataGenerator` to:
  - support more step types
  - bootstrap a scope witness before branch scheduling
  - iterate dependency order rather than hard-failing on multi-scope graphs
- Add focused unit tests for join witnesses and non-branch join scopes.

## Validation Strategy
- First validation target: new symbolic generator unit tests for join-based
  witness synthesis.
- Regression checks:
  - `python -m unittest tests.symbolic.test_generator`
  - `python -m unittest tests.symbolic.test_solve_session`
  - `python -m unittest tests.symbolic.test_symbolic_bird`

## Success Criteria
- [ ] Symbolic generator synthesizes a non-empty witness for joined scopes.
- [ ] Symbolic generator can handle scopes without extracted branch templates.
- [ ] Existing symbolic unit tests continue to pass.
- [ ] `tests/symbolic/test_symbolic_bird.py` progresses beyond the current
      first failing join query.

## Potential Issues
- Multi-scope subquery lowering may require special handling when the root
- condition embeds a dependency scope expression.
- Some aggregate or set-operation queries may still need targeted follow-up if
  they require more than a minimal non-empty witness.
