# Output NULL and DISTINCT-Aggregate Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add default-on, per-expression targets for projected NULL values, aggregate NULL values, and DISTINCT-aggregate input behavior.

**Architecture:** Extend `PlausibleBit` and `CoverageThresholds`, create output nodes whose `atom_id` is the output/function ordinal, observe values in `PlanEvaluator`, and compile missing outcomes into identity-preserving solver expressions. DISTINCT aggregation will explicitly retain raw inputs for coverage while using deduplicated non-NULL inputs for its SQL result.

**Tech Stack:** Python 3.10, sqlglot, ParSEval planner identities, `BranchTree`, `PlanEvaluator`, `ConstraintGenerator`, pytest.

---

## Files

- Modify `src/parseval/constants.py`: new branch outcomes.
- Modify `src/parseval/symbolic/types.py`: thresholds and target enumeration.
- Modify `src/parseval/symbolic/evaluator.py`: nodes, observations, and DISTINCT aggregate semantics.
- Modify `src/parseval/symbolic/constraints.py`: specialized target compilation.
- Create `tests/symbolic/test_output_coverage_targets.py`.
- Create `tests/symbolic/test_output_coverage_observations.py`.
- Create `tests/symbolic/test_output_coverage_generation.py`.

### Task 1: Branch vocabulary and thresholds

**Files:** `src/parseval/constants.py`, `src/parseval/symbolic/types.py`, `tests/symbolic/test_output_coverage_targets.py`

- [ ] Write failing tests for `SELECT a, b FROM t` asserting two NULL/non-NULL targets per ordinal, and for `SELECT SUM(a), COUNT(DISTINCT b) FROM t` asserting COUNT has no aggregate-NULL target.
- [ ] Run `PYTHONPATH=src .venv/bin/pytest tests/symbolic/test_output_coverage_targets.py -q`; expect missing enum/threshold failures.
- [ ] Add these enum values without renumbering existing values:

```python
PROJECT_NULL = 27
PROJECT_NON_NULL = 28
AGGREGATE_NULL = 29
AGGREGATE_NON_NULL = 30
AGG_DISTINCT_NULL_IGNORED = 31
AGG_DISTINCT_DUPLICATE_ELIMINATED = 32
AGG_DISTINCT_MULTIPLE_RETAINED = 33
```

- [ ] Add these default-on `CoverageThresholds` fields and map them in `threshold_for()`:

```python
project_null: int = 1
project_non_null: int = 1
aggregate_null: int = 1
aggregate_non_null: int = 1
aggregate_distinct_null_ignored: int = 1
aggregate_distinct_duplicate_eliminated: int = 1
aggregate_distinct_multiple_retained: int = 1
```

- [ ] Extend `_target_specs_for_node()` so `project_output` and `aggregate_output` enumerate outcomes per `atom_id`, while `aggregate_distinct_input` enumerates all three input outcomes. Unwrap one `exp.Alias`; omit `AGGREGATE_NULL` only for direct `exp.Count`.
- [ ] Add a test setting each new threshold to zero independently and proving only that outcome disappears.
- [ ] Run the target tests; expect PASS.
- [ ] Commit with `git commit -m "feat: add output coverage targets"` after staging only these files.

### Task 2: Project-output observations

**Files:** `src/parseval/symbolic/evaluator.py`, `tests/symbolic/test_output_coverage_observations.py`

- [ ] Write a failing test using rows `(a=NULL,b=1)` and `(a=2,b=NULL)` with `SELECT a, b, a FROM t`. Assert one `project_output` node with three atoms and independent NULL/non-NULL observations for atom IDs 0, 1, and 2.
- [ ] Run `PYTHONPATH=src .venv/bin/pytest tests/symbolic/test_output_coverage_observations.py -k project -q`; expect no node.
- [ ] In `build_branch_tree()`, create one node per `Project`:

```python
_add_node(step, "Project", "project_output",
          exp.Literal.string("PROJECT_OUTPUT"),
          tuple(step.projections), tables)
```

- [ ] In `_eval_project()`, evaluate projections once, then for every output ordinal record `PROJECT_NULL` or `PROJECT_NON_NULL` with the output `ColumnId`, value, and source row ID. Reuse those evaluated values for DISTINCT processing.
- [ ] Run project observation tests; expect PASS.
- [ ] Commit with `git commit -m "feat: observe projected null values"`.

### Task 3: Aggregate and DISTINCT-input observations

**Files:** `src/parseval/symbolic/evaluator.py`, `tests/symbolic/test_output_coverage_observations.py`

- [ ] Write failing tests with raw inputs `[NULL, 2, 2, 3]` for `SUM(x), COUNT(x), COUNT(DISTINCT x), SUM(DISTINCT x)`. Assert results `(7, 3, 2, 5)` and all applicable output/input observations.
- [ ] Add an all-NULL grouped test asserting SUM/AVG/MIN/MAX observe `AGGREGATE_NULL`, while COUNT observes only `AGGREGATE_NON_NULL`.
- [ ] Run `PYTHONPATH=src .venv/bin/pytest tests/symbolic/test_output_coverage_observations.py -k aggregate -q`; expect failures.
- [ ] Add a helper that unwraps `exp.Distinct`, evaluates its first expression, and returns `(raw_values, is_distinct)`. Compute aggregate results from:

```python
non_null = [value for value in raw_values if value is not None]
result_values = list(dict.fromkeys(non_null)) if is_distinct else non_null
```

- [ ] Add one `aggregate_output` node whose atoms are `step.aggregations`.
- [ ] Walk aggregate functions in output order and add one `aggregate_distinct_input` node whose atoms are the DISTINCT argument expressions; repeated functions remain repeated atoms.
- [ ] Per group/global row, record aggregate result NULL/non-NULL by output ordinal. For each raw DISTINCT input list, record NULL-ignored when any value is NULL, duplicate-eliminated when non-NULL cardinality exceeds set cardinality, and multiple-retained when at least two distinct non-NULL values exist.
- [ ] Run all observation tests; expect PASS.
- [ ] Commit with `git commit -m "feat: observe aggregate output behavior"`.

### Task 4: Specialized constraint generation

**Files:** `src/parseval/symbolic/constraints.py`, `tests/symbolic/test_output_coverage_generation.py`

- [ ] Write failing tests asserting project targets compile to `IS NULL`/`IS NOT NULL`; SUM targets constrain its source argument; DISTINCT duplicate/multiple targets produce `r0` and `r1` variables with EQ/NEQ and non-NULL constraints.
- [ ] Assert every generated variable retains planner `RelationId` and has a physical `storage_relations` entry.
- [ ] Run `PYTHONPATH=src .venv/bin/pytest tests/symbolic/test_output_coverage_generation.py -q`; expect missing specialized compilation.
- [ ] Dispatch `project_output`, `aggregate_output`, and `aggregate_distinct_input` in `ConstraintGenerator.compile()`.
- [ ] Add `_scoped_expression(expression, tables, row_scope)` that copies an expression, requires every query column to carry `ColumnId`, assigns `SolverVar(row_scope=...)`, and preserves datatype metadata. It must not use textual relation fallback.
- [ ] Compile project targets as:

```python
exp.Is(this=scoped_expression,
       expression=exp.Null() if want_null else exp.Not(this=exp.Null()))
```

- [ ] For aggregate targets, locate the aggregate argument, unwrap `exp.Distinct`, and constrain one contributor (`r0`) NULL/non-NULL. Reject direct COUNT NULL and unsupported derived shapes with `ConstraintConflict` so dynamic infeasibility handles them.
- [ ] For DISTINCT inputs generate: NULL on `r0`; EQ plus non-NULL on `r0/r1`; or NEQ plus non-NULL on `r0/r1`. For grouped aggregates, constrain every group expression equal across both scopes.
- [ ] Feed specialized constraints through existing database constraints, variable collection, join facts, and storage mapping rather than returning a partial `SolverConstraint`.
- [ ] Run generation tests; expect PASS.
- [ ] Commit with `git commit -m "feat: generate output coverage witnesses"`.

### Task 5: End-to-end verification

**Files:** `tests/symbolic/test_output_coverage_generation.py`

- [ ] Add SQLite engine tests for `SELECT nullable_text, id FROM items` and grouped `SUM(DISTINCT amount), COUNT(DISTINCT amount)`. Assert enabled targets become covered, two-row outcomes materialize two rows, and primary keys remain non-NULL.
- [ ] Run focused tests:

```bash
PYTHONPATH=src .venv/bin/pytest \
  tests/symbolic/test_output_coverage_targets.py \
  tests/symbolic/test_output_coverage_observations.py \
  tests/symbolic/test_output_coverage_generation.py -q
```

- [ ] Run adjacent regressions:

```bash
PYTHONPATH=src .venv/bin/pytest \
  tests/symbolic/test_distinct_eval.py \
  tests/symbolic/test_operator_flow_paths.py \
  tests/symbolic/test_symbolic_engine.py \
  tests/symbolic/test_strict_row_shape.py \
  tests/solver -q
```

- [ ] Run `git diff --check` and `rg -n "PROJECT_NULL|AGGREGATE_NULL|AGG_DISTINCT_" src tests`; expect every outcome in constants, thresholds, targets, evaluator, constraints, and tests.
- [ ] Commit integration tests with `git commit -m "test: cover output branch generation end to end"`.
