# Speculative Visitor + Registry Refactor Plan

## Objective
Refactor speculative spec extraction and function handling so:

1. speculative spec collection reuses planner scope traversal through a visitor pattern rather than ad hoc generator-local methods
2. speculative function evaluation/generation uses a registry/strategy model instead of large `if/elif` chains

## Research Summary
- `src/parseval/speculative/extraction.py` currently hardcodes `extract_condition_specs`, age-specific logic, and subquery processing around raw `sqlglot` scope internals.
- `src/parseval/plan/planner.py` already provides `Graph`, `ScopeNode`, and `build_graph_from_scopes`, but no reusable visitor abstraction yet.
- `src/parseval/speculative/values.py` still has large open-coded function dispatch for evaluate/generate flows.
- Existing tests import `parseval.speculative` directly; the public API must stay stable.
- The recent learning on speculative import decoupling means new abstractions should stay lightweight and avoid pulling solver-heavy modules.

## Proposed Architecture
- Add a lightweight scope visitor abstraction in `parseval.plan.planner` or a sibling planner module built around `build_graph_from_scopes`.
- Introduce `SpecCollectorVisitor` in `parseval.speculative` that walks planner scopes and emits `GenerationSpec`.
- Keep low-level predicate parsing as standalone helpers, but remove generator-local orchestration methods like `_process_subquery`.
- Add a function strategy registry in speculative value generation:
  - evaluator strategies define `evaluate(...)`
  - candidate strategies define `generate(...)`
  - optional combined-strategy hooks handle cross-function composition cases

## Implementation Approach
- Add planner visitor interfaces without altering existing planner behavior.
- Move speculative collection state into a dedicated collector object and make `SpeculativeGenerator` consume its output.
- Preserve `extract_condition_specs(...)` as a public helper for compatibility, but stop using generator-owned extraction methods.
- Replace function-name branching with a `FunctionEvaluatorRegistry` / `FunctionStrategyRegistry` keyed by uppercased function names.

## Validation Strategy
- Run lightweight targeted tests and smoke checks:
  - import + compilation checks for planner/speculative modules
  - focused speculative unit smoke scripts covering:
    - function-spec extraction
    - registry-based evaluation for `LENGTH`, `ABS`, `INSTR`, `SUBSTR`, `STRFTIME`
    - visitor-based scope collection for subqueries and grouped queries
- If available in the current worktree, run `python -m unittest tests.speculative.test_speculative`

## Success Criteria
- [ ] Speculative scope collection is implemented as a planner-backed visitor.
- [ ] Generator-local hardcoded extraction orchestration is removed or reduced to thin delegation.
- [ ] Function handling no longer relies on core `if/elif` chains for supported functions.
- [ ] Public `parseval.speculative` imports remain stable.
- [ ] Targeted validation passes or blockers are documented.

## Potential Issues
- Planner scope metadata is not a fully normalized semantic plan, so some SQL-shape-specific logic may still need expression helpers.
- Tests in this repository have optional dependency and fixture issues; unit-style smoke validation may be the practical minimum.

## Sources
- Local repository code only:
  - `src/parseval/plan/planner.py`
  - `src/parseval/speculative/extraction.py`
  - `src/parseval/speculative/values.py`
  - `src/parseval/speculative/generator.py`
  - `tests/speculative/test_speculative.py`
