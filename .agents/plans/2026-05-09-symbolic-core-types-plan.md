# Symbolic Core Types Implementation Plan

## Objective
Add the Milestone 1 symbolic coverage core dataclasses under `src/parseval/symbolic/`
with tests-first coverage and no changes to legacy generator behavior.

## Research Summary
- The active symbolic coverage roadmap already defines WP1 for `BranchTemplate`,
  `ExecutionContextKey`, `BranchInstance`, and `BranchGoal`.
- Recent learnings emphasize lightweight package imports and avoiding accidental
  dependencies on solver or generator modules for import-only workflows.
- Existing public packages export selected symbols through `__init__.py` and keep
  dataclass modules small and direct.
- Tests in this repository use `unittest` and simple constructor/assertion checks
  for foundational data types.

## Proposed Architecture
Create a new lightweight `parseval.symbolic` package:
- `types.py` holds the four frozen dataclasses and supporting type aliases.
- `__init__.py` re-exports the public symbolic types.

The initial field set stays minimal but covers filter / `OR` / `CASE` branch
support:
- structural branch identity and semantic role in `BranchTemplate`
- normalized execution scope in `ExecutionContextKey`
- runtime observation state in `BranchInstance`
- scheduler intent in `BranchGoal`

## Implementation Approach
1. Add tests in `tests/symbolic/test_branch_types.py` first.
2. Make the tests assert:
   - importability from `parseval.symbolic`
   - frozen dataclass semantics
   - default immutable tuple fields
   - stable context normalization semantics captured in fields/docstrings
3. Implement the smallest types needed to satisfy those tests.
4. Keep imports limited to stdlib typing/dataclasses only.

## Validation Strategy
- First failing target: `python -m unittest tests.symbolic.test_branch_types`
- Final validation: rerun the same focused target after implementation

## Success Criteria
- [ ] `parseval.symbolic` is importable without dragging legacy symbolic machinery
- [ ] The four dataclasses exist with documented stable semantics
- [ ] Fields are sufficient for filter / `OR` / `CASE`
- [ ] Focused tests pass

## Potential Issues
- Importing from existing symbolic modules could accidentally pull heavy planner or
  SMT dependencies. Avoid that by keeping the new package self-contained.
- Over-modeling fields now would make later extraction and recording changes harder
  to evolve. Keep this slice intentionally narrow.

## Sources
- `.agents/plans/2026-05-09-planner-anchored-symbolic-coverage-plan.md`
- `.agents/plans/2026-05-09-planner-anchored-symbolic-coverage-breakdown.md`
- `.agents/learnings/2026-05-09-speculative-import-decoupling.md`
