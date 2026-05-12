# Speculative Generator Refactor Plan

## Objective
Refactor the speculative database-instance generator so the current logic from `src/parseval/speculative_backup.py` is no longer concentrated in one monolithic module. The outcome should preserve the existing `parseval.speculative` API while separating SQL-spec extraction, table planning, and value synthesis into focused modules.

## Research Summary
- `src/parseval/speculative_backup.py` currently mixes datamodel definitions, SQL AST analysis, row planning, and concrete value generation in a single file and single class.
- `src/parseval/main.py`, `src/parseval/disprover.py`, and tests import `parseval.speculative`, so the public import path must remain stable.
- The repository already moved other large modules into packages such as `src/parseval/instance/` and `src/parseval/domain/`, which is the closest local pattern to follow.
- `.agents/learnings/` is currently empty, so there are no prior learnings to apply directly.
- The active worktree already deletes `src/parseval/speculative.py` and introduces `src/parseval/speculative_backup.py`, so the refactor should build on that state without reverting user changes.

## Proposed Architecture
- Create a package at `src/parseval/speculative/`.
- Keep public dataclasses and helpers exported from `parseval.speculative.__init__`.
- Split responsibilities into:
  - `specs.py`: dataclasses and shared speculative types
  - `extraction.py`: SQL predicate/spec extraction helpers and scope inspection
  - `values.py`: value-generation and validation logic for column/function specs
  - `planner.py`: table-plan construction and row materialization orchestration
  - `generator.py`: `SpeculativeGenerator` facade that coordinates extraction and planning

## Implementation Approach
- Preserve method names used by tests, especially `SpeculativeGenerator._validate_function_candidate`.
- Lift pure helpers into standalone functions/classes with minimal behavioral changes.
- Keep `SpeculativeGenerator` as the user-facing entry point, but delegate extraction and planning work to helper objects.
- Use the package form of `parseval.speculative` so all existing imports keep working.

## Validation Strategy
- Run focused speculative tests first:
  - `python -m unittest tests.test_speculate`
  - `python -m unittest tests.test_main`
  - `python -m unittest tests.test_disprover`
- If those are too environment-dependent, run at least the unit-style speculative extraction tests and report any dataset-related blockers.

## Success Criteria
- [ ] `parseval.speculative` remains import-compatible for existing callers.
- [ ] Spec dataclasses and extraction helpers move out of the monolithic file.
- [ ] Planning and value-generation logic are isolated into dedicated modules/classes.
- [ ] Relevant speculative tests pass or blockers are documented precisely.

## Potential Issues
- `tests/test_speculate.py` depends on dataset files that are currently deleted in the worktree, so full validation may be partially blocked.
- Some methods in the monolith share state implicitly through `self.column_specs`, `self.join_specs`, and `self.table_alias`; delegation must keep those interactions intact.

## Sources
- Local repository code only:
  - `src/parseval/speculative_backup.py`
  - `src/parseval/main.py`
  - `src/parseval/data_generator.py`
  - `tests/test_speculate.py`
