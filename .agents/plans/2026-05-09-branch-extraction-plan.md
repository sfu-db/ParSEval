# Branch Extraction Implementation Plan

## Objective
Implement a planner-side TDD slice for `extract_branch_templates(scope_plan)` under
`src/parseval/symbolic/`, covering basic predicate branch discovery from scope
plans and projection `CASE` arms.

## Research Summary
- `ScopePlan` and `analyze_scope_plan` already provide deterministic step
  ordering and per-step annotations for `condition`, projections, and referenced
  columns.
- `speculative/collector.py` already treats projection predicates as reusable
  branch-like constraints by collecting predicate expressions from projection
  expressions.
- Existing planner tests build real `ScopePlan` instances from SQL via
  `build_graph_from_scopes` and `preprocess_sql`, which is the right shape for
  this slice.
- Repository learnings emphasize lightweight imports and keeping planner-side
  traversal reusable rather than embedding extraction logic inside speculative
  code.

## Proposed Architecture
Add a small extraction module that:
- imports branch template types from `parseval.symbolic.types`
- walks analyzed `ScopePlan` steps in deterministic order
- extracts branch candidate expressions from:
  - step conditions
  - projection `CASE` expressions
- normalizes logical structure so conjunctions contribute individual branch
  predicates and disjunctions contribute each alternative predicate

## Implementation Approach
- Write tests first using `unittest`.
- In tests, inject a lightweight `parseval.symbolic.types` stub if the real
  module is not present yet, so the extraction module can still be imported.
- Implement helper traversal functions in `extraction.py` to:
  - unwrap parentheses
  - split `AND` trees recursively
  - split `OR` trees recursively into individual branch predicates
  - collect `WHEN` predicates from `CASE`
- Keep result ordering stable by iterating ordered steps and preserving first
  occurrence order based on expression SQL text.

## Validation Strategy
- First validation target: new failing tests in
  `tests/symbolic/test_branch_extraction.py`.
- Main automated check: focused pytest run for the new test module.

## Success Criteria
- [ ] `extract_branch_templates(scope_plan)` is implemented in
      `src/parseval/symbolic/extraction.py`
- [ ] Simple filter predicates are extracted
- [ ] Conjunction predicates are split into separate branch templates
- [ ] OR predicates contribute each subpredicate individually
- [ ] Projection `CASE WHEN` arms contribute branch templates
- [ ] Focused symbolic branch extraction tests pass

## Potential Issues
- `parseval.symbolic.types` may not yet exist locally.
  Mitigation: import it in production code as requested, and stub it only in
  tests to keep the slice runnable.
- `sqlglot` planner may place predicates either in step annotations or directly
  on expressions depending on step type.
  Mitigation: use both analyzed annotations and projection traversal.

## Sources
- Local repository files only
