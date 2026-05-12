# Step-Centered Shared Planner Implementation Breakdown

## Objective
Translate the architecture in `2026-05-09-step-centered-shared-planner-plan.md` into small, dependency-aware implementation tasks that can be executed incrementally with low regression risk.

This breakdown treats:

- `ScopeNode` as the inter-scope dependency unit
- `sqlglot.planner.Plan` and `Step` as the intra-scope planning unit
- ParSEval planner-core as graph + scope-plan + annotations
- symbolic and speculative paths as separate backend lowerers over shared planner structures

## Consolidated Strategy
The safest implementation sequence is:

1. split graph code out of `plan/planner.py`
2. isolate planner-core tests from symbolic tests
3. introduce `ScopePlan` as a thin, backend-neutral wrapper around `Plan(scope.expression)`
4. make step traversal deterministic and test it
5. move the symbolic encoder onto `ScopePlan` without changing symbolic behavior
6. add minimal shared `StepAnnotations`
7. migrate speculative collection to consume `ScopePlan` and annotations while preserving `GenerationSpec`
8. only then finish symbolic renaming / API cleanup / cross-backend orchestration cleanup

That sequencing is deliberate:

- graph extraction has the lowest risk and highest reuse value
- `ScopePlan` should exist before either backend starts consuming shared step metadata
- speculative migration can start using `ScopePlan` before the symbolic encoder is fully renamed, as long as planner-core stays import-light
- `GenerationSpec` and symbolic regression behavior should remain stable during the middle phases

## Work Packages

### WP1: Extract Scope Graph Into Planner-Core
Goal:
Move graph responsibilities out of the current symbolic planner without changing behavior.

Tasks:

1. Create `src/parseval/plan/graph.py`.
2. Move:
   - `ScopeNode`
   - `Graph`
   - `_scope_local_base_tables`
   - `_parent_alias_base_tables`
   - `_projection_column_keys`
   - `_non_projection_column_keys`
   - `_has_true_parent_correlation`
   - `build_graph_from_scopes`
   - `to_scope_dot`
3. Update `src/parseval/plan/visitor.py` to import graph types from `graph.py`.
4. Update `src/parseval/plan/__init__.py` to re-export graph API from `graph.py`.
5. Keep compatibility imports stable so existing symbolic and speculative code keeps working.

Key files:

- `src/parseval/plan/planner.py`
- `src/parseval/plan/graph.py`
- `src/parseval/plan/visitor.py`
- `src/parseval/plan/__init__.py`

Acceptance criteria:

- `parseval.plan.build_graph_from_scopes` still works
- `walk_scope_graph` no longer depends on `planner.py`
- no symbolic behavior changes yet

Dependencies:

- none

### WP2: Split Graph Tests Out Of Symbolic Tests
Goal:
Give graph behavior its own planner-core tests before deeper refactors.

Tasks:

1. Create `tests/plan/test_graph.py`.
2. Move graph assertions currently mixed into `tests/test_planner_symbolic.py` into the new file.
3. Add direct tests for:
   - root node selection
   - dependency direction
   - derived-subquery ordering
   - alias leakage / non-correlation classification
4. Optionally add a small `tests/plan/test_visitor.py` for `walk_scope_graph` ordering and lifecycle.

Key files:

- `tests/test_planner_symbolic.py`
- `tests/plan/test_graph.py`
- `tests/plan/test_visitor.py`

Acceptance criteria:

- graph tests run without symbolic solving
- `tests/test_planner_symbolic.py` becomes symbolic-focused

Dependencies:

- WP1

### WP3: Introduce `ScopePlan`
Goal:
Create the shared intra-scope planning wrapper around `sqlglot.planner.Plan` and `Step`.

Tasks:

1. Create `src/parseval/plan/scope_plan.py`.
2. Add `ScopePlan` with:
   - `scope_node`
   - `plan`
   - `scope_id`
   - `expression`
   - `root_step`
   - `leaves`
   - `ordered_steps`
3. Ensure `ScopePlan` requires no tracer, SMT, or `Context`.
4. Make ordering deterministic. Do not rely on raw `set(...).pop()` behavior.
5. Keep this class as a wrapper, not a new operator IR.

Key files:

- `src/parseval/plan/scope_plan.py`
- `src/parseval/plan/__init__.py`

Acceptance criteria:

- a `ScopePlan` can be built from any `ScopeNode`
- step ordering is deterministic across runs
- no symbolic or speculative imports are introduced

Dependencies:

- WP1

### WP4: Add Planner-Core Tests For `ScopePlan`
Goal:
Lock down step traversal behavior before backends start consuming it.

Tasks:

1. Create `tests/plan/test_scope_plan.py`.
2. Add tests for:
   - single-table scan/filter/projection
   - join scope
   - aggregate/having scope
   - set operation scope
3. Assert:
   - `root_step`
   - `leaves`
   - deterministic `ordered_steps`
   - stable step type ordering
4. Keep tests independent from SMT and generator behavior.

Key files:

- `tests/plan/test_scope_plan.py`

Acceptance criteria:

- planner-core traversal is directly testable
- regressions in step ordering are caught before backend migration

Dependencies:

- WP3

### WP5: Make Current Symbolic Planner Consume `ScopePlan`
Goal:
Refactor symbolic encoding to use shared step traversal internally without renaming the symbolic planner yet.

Tasks:

1. Update current `Planner.encode()` to consume `ScopePlan` rather than reconstructing ad hoc step traversal.
2. Preserve current symbolic dispatch semantics:
   - `scan`
   - `aggregate`
   - `join`
   - `sort`
   - `set_operation`
3. Preserve current tracer behavior.
4. Do not change public symbolic call sites yet unless required by the refactor.

Key files:

- `src/parseval/plan/planner.py`

Acceptance criteria:

- existing symbolic tests stay green
- symbolic encoding now depends on planner-core traversal instead of owning it

Dependencies:

- WP3
- WP4

### WP6: Extract Symbolic Encoder Into Its Own Module
Goal:
Stop presenting the symbolic encoder as the generic planner API.

Tasks:

1. Create `src/parseval/plan/symbolic.py`.
2. Move symbolic-specific behavior out of `planner.py` into a dedicated class such as:
   - `SymbolicScopeEncoder`
3. Move symbolic-only methods:
   - `encode`
   - `scan`
   - `project`
   - `filters`
   - join handling
   - aggregate handling
   - sort handling
   - set operation handling
4. Keep a temporary compatibility alias if needed.
5. Re-export intentionally from `plan/__init__.py`.

Key files:

- `src/parseval/plan/planner.py`
- `src/parseval/plan/symbolic.py`
- `src/parseval/plan/__init__.py`

Acceptance criteria:

- backend-neutral planner-core modules remain free of tracer/SMT coupling
- symbolic encoding has a backend-specific name and home

Dependencies:

- WP5

### WP7: Update Symbolic Call Sites
Goal:
Make symbolic orchestration explicitly consume planner-core plus symbolic encoder.

Tasks:

1. Update `src/parseval/data_generator.py`:
   - `_encode_scope`
   - any scope-local planner construction
2. Update symbolic test harnesses such as `tests/test_planner_symbolic.py`.
3. Keep behavior stable around:
   - subquery binding rewrites
   - correlated dependency orchestration
   - aggregate/having behavior

Key files:

- `src/parseval/data_generator.py`
- `tests/test_planner_symbolic.py`

Acceptance criteria:

- symbolic path explicitly builds `ScopePlan`
- symbolic path explicitly invokes the symbolic encoder
- scope mutation ordering does not produce stale `ScopePlan` objects

Dependencies:

- WP6

### WP8: Add Minimal Shared `StepAnnotations`
Goal:
Introduce shared step metadata only after `ScopePlan` is stable.

Tasks:

1. Create `src/parseval/plan/annotations.py`.
2. Add `StepAnnotations` with only stable fields first:
   - `step_id`
   - `step_type`
   - `step_name`
   - `condition`
   - `referenced_columns`
   - `projected_columns`
   - `source_tables`
   - `flags`
3. Add annotation storage to `ScopePlan`.
4. Add `annotation_for(step)` with stable repeated lookup behavior.

Key files:

- `src/parseval/plan/annotations.py`
- `src/parseval/plan/scope_plan.py`

Acceptance criteria:

- every step can have an annotation object even before analysis
- annotation storage is backend-neutral

Dependencies:

- WP3
- preferably after WP7 to avoid mixing behavior and metadata refactors

### WP9: Add First-Pass Planner-Core Analysis
Goal:
Populate shared step annotations from `Step` and underlying expressions.

Tasks:

1. Create `src/parseval/plan/analysis.py`.
2. Add a first-pass analyzer that fills:
   - step type/name
   - step condition
   - referenced columns
   - projected columns
   - source tables
3. Allow raw-expression fallback where `Step` alone is insufficient.
4. Keep this layer import-light and backend-neutral.

Key files:

- `src/parseval/plan/analysis.py`
- `src/parseval/plan/annotations.py`
- `src/parseval/plan/scope_plan.py`

Acceptance criteria:

- annotations can be filled without tracer or generator code
- planner-core tests can assert extracted facts directly

Dependencies:

- WP8

### WP10: Add Planner-Core Annotation Tests
Goal:
Protect the new shared metadata contract before backend migration.

Tasks:

1. Create `tests/plan/test_annotations.py`.
2. Add tests for:
   - default annotation creation
   - repeated `annotation_for(step)` stability
   - first-pass extracted facts on representative queries
3. Add `tests/plan/test_analysis.py` only if analysis grows beyond trivial extraction.

Key files:

- `tests/plan/test_annotations.py`
- `tests/plan/test_analysis.py`

Acceptance criteria:

- planner-core facts are testable directly
- no symbolic or speculative backend is required to validate them

Dependencies:

- WP9

### WP11: Introduce A Speculative Collector Context Based On `ScopePlan`
Goal:
Move speculative extraction onto the shared planner-core while preserving the existing `GenerationSpec` contract.

Tasks:

1. Update `src/parseval/speculative/collector.py` so each visited scope builds a `ScopePlan`.
2. Add a collector-local context object if needed to hold:
   - `ScopePlan`
   - projection map
   - alias normalization
   - capability reporting state
3. Keep `GenerationSpec` unchanged for now.
4. Keep `SpeculativeGenerator` and `TablePlanBuilder` consuming flat specs.

Key files:

- `src/parseval/speculative/collector.py`
- `src/parseval/speculative/generator.py`
- `src/parseval/speculative/specs.py`

Acceptance criteria:

- speculative collector depends on planner-core, not symbolic planner internals
- generator boundary remains stable

Dependencies:

- WP3
- WP8
- WP9

### WP12: Migrate Speculative Filter And Join Collection First
Goal:
Use the safest step-aware speculative lowerers before touching harder constructs.

Tasks:

1. Refactor filter collection to use `ScopePlan` / `StepAnnotations` where possible.
2. Refactor join collection to use step-aware join metadata where possible.
3. Keep raw-expression fallback for:
   - non-key predicates
   - special semantics heuristics
   - date/age value handling

Key files:

- `src/parseval/speculative/collector.py`
- `src/parseval/speculative/strategies/filters.py`
- `src/parseval/speculative/strategies/joins.py`
- `src/parseval/speculative/extraction.py`

Acceptance criteria:

- `GenerationSpec` remains unchanged
- filter/join extraction becomes step-aware without regressions

Dependencies:

- WP11

### WP13: Migrate Speculative Group / Order / Window Collection
Goal:
Extend speculative lowering to the next most structured operator classes.

Tasks:

1. Migrate group and aggregate extraction.
2. Migrate order extraction.
3. Migrate window extraction.
4. Preserve fallback logic for:
   - synthetic HAVING aliases
   - outer-scope window rank binding
   - placeholder projection columns

Key files:

- `src/parseval/speculative/strategies/groups.py`
- `src/parseval/speculative/strategies/windows.py`
- `src/parseval/speculative/collector.py`

Acceptance criteria:

- step-aware logic handles the common cases
- fallback logic remains for awkward `sqlglot` shapes

Dependencies:

- WP12

### WP14: Migrate Speculative Subquery And Set-Operation Collection
Goal:
Move the most correlation-sensitive speculative logic last.

Tasks:

1. Migrate subquery collection to consume `ScopePlan` where possible.
2. Migrate set-operation collection.
3. Explicitly allow raw `exp` fallback for:
   - kind detection
   - outer-column extraction
   - correlation details
4. Do not fully trust correlation metadata until graph behavior is tightened and tested further.

Key files:

- `src/parseval/speculative/strategies/subqueries.py`
- `src/parseval/speculative/strategies/set_operations.py`
- `src/parseval/speculative/extraction.py`
- `src/parseval/speculative/collector.py`

Acceptance criteria:

- speculative collector is largely step-aware
- remaining raw-expression fallbacks are explicit and justified

Dependencies:

- WP11
- WP13

### WP15: Add Cross-Backend Planner Consistency Tests
Goal:
Validate that symbolic and speculative now consume the same scope/step structure.

Tasks:

1. Add planner-core consistency tests for representative query families:
   - single-table filter/projection
   - join
   - group by + having
   - scalar subquery
   - correlated `EXISTS`
   - `UNION` / `INTERSECT`
   - window function
2. Assert:
   - same graph shape
   - same step ordering
   - same baseline step annotations
3. Keep backend-specific result assertions separate from planner-core assertions.

Key files:

- `tests/plan/test_scope_plan.py`
- `tests/plan/test_annotations.py`
- possibly new `tests/plan/test_cross_backend_consistency.py`

Acceptance criteria:

- planner-core semantics are shared in practice, not only by design

Dependencies:

- WP10
- enough of WP7 and WP14 to exercise both backends

### WP16: Final API Cleanup
Goal:
Finish the transition from “planner as symbolic encoder” to “planner-core plus backend lowerers.”

Tasks:

1. Remove or narrow compatibility exports that make `Planner` appear generic.
2. Clarify public planner API in `src/parseval/plan/__init__.py`.
3. Add a shared scope-plan orchestration helper only if duplication remains meaningful after backend migrations.
4. Update docs if public developer-facing APIs changed.

Key files:

- `src/parseval/plan/__init__.py`
- `src/parseval/plan/visitor.py`
- `src/parseval/data_generator.py`
- docs if needed

Acceptance criteria:

- planner-core is clearly distinct from backend-specific encoders
- import boundaries are intentional and lightweight

Dependencies:

- WP7
- WP14
- WP15

## Recommended PR Sequence

### PR 1
- WP1
- WP2

Reason:
Lowest-risk extraction, immediate test separation.

### PR 2
- WP3
- WP4

Reason:
Introduce `ScopePlan` and lock down deterministic step traversal before backend changes.

### PR 3
- WP5

Reason:
Move symbolic traversal onto `ScopePlan` without changing public architecture yet.

### PR 4
- WP6
- WP7

Reason:
Finish symbolic split and make symbolic usage explicit.

### PR 5
- WP8
- WP9
- WP10

Reason:
Add minimal shared annotations after shared traversal is stable.

### PR 6
- WP11
- WP12

Reason:
Start speculative migration at the collector boundary with the safest operator classes first.

### PR 7
- WP13
- WP14

Reason:
Finish speculative step-aware migration on the harder operator families.

### PR 8
- WP15
- WP16

Reason:
Close the loop with cross-backend consistency protection and API cleanup.

## Risks And Guardrails

### Risk: stale `ScopePlan` objects in symbolic orchestration
Cause:
`DataGenerator` mutates scope expressions during subquery binding.

Guardrail:
Only build `ScopePlan` after scope-local expression rewrites that affect the current scope.

### Risk: nondeterministic traversal order
Cause:
Current code uses sets in both graph ordering and symbolic plan traversal.

Guardrail:
Make step ordering deterministic in `ScopePlan`, and assert it in planner-core tests before backend migration.

### Risk: aggregate/having alias normalization
Cause:
`sqlglot` introduces synthetic aliases such as `_h` and `_g0`.

Guardrail:
Preserve existing symbolic and speculative normalization logic until annotation and lowering tests cover these cases directly.

### Risk: correlation metadata is not yet fully trustworthy
Cause:
Some correlated subquery shapes may not be marked correctly.

Guardrail:
Do not let early speculative subquery migration rely solely on `is_correlated_dependency`; allow explicit raw-expression fallback.

### Risk: speculative import boundary regression
Cause:
shared planner-core accidentally imports symbolic/SMT code

Guardrail:
Keep `graph.py`, `scope_plan.py`, `annotations.py`, and `analysis.py` free of tracer and solver imports.

## Immediate Next Slice
The shortest safe next implementation slice is:

1. WP1: extract graph code into `plan/graph.py`
2. WP2: move graph tests into `tests/plan/test_graph.py`

Do not introduce `ScopePlan` in the same patch unless the graph extraction remains trivially small after implementation begins.

## Success Criteria
- [ ] graph logic is planner-core and independent from symbolic encoding
- [ ] graph and scope-plan behaviors are protected by planner-core tests
- [ ] `ScopePlan` becomes the shared intra-scope structure for both backends
- [ ] symbolic encoding becomes a named backend consumer of planner-core
- [ ] speculative collection becomes a step-aware consumer of planner-core
- [ ] `GenerationSpec` remains stable during speculative migration
- [ ] planner-core stays lightweight and backend-neutral
