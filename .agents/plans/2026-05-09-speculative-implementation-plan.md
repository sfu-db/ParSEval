# Speculative Module Implementation Plan

## Current Progress
- Completed TDD slices:
  - expanded `GenerationSpec` with capability, unresolved, provenance, order/window placeholders
  - capability model and minimal collector capability analysis
  - predicate semantics for text-vs-numeric comparison downgrades
  - function strategy registry
  - filter strategy registry and collector migration
  - join strategy registry and collector migration
  - subquery strategy registry and collector migration
  - group strategy registry and collector migration
  - window strategy registry with `ROW_NUMBER` spec collection and rank-filter binding
  - set-operation strategy registry with `UNION`/`INTERSECT`/`EXCEPT` collection
  - planner consumption of `window_specs` and `order_specs` for simple ordered/ranked witnesses
  - capability-driven generator routing with provenance traces
  - end-to-end speculative generation coverage against materialized SQLite instances
- Current targeted validation baseline:
  - `python -m unittest tests.speculative.test_architecture tests.speculative.test_end_to_end tests.test_instance_loader`

## Objective
Implement the speculative module redesign incrementally while keeping the system working at each step. `GenerationSpec` remains the permanent central abstraction and will be expanded into the declarative contract between speculative analysis and synthesis.

## Scope
This plan covers:
- capability analysis for speculative generation
- expansion of `GenerationSpec`
- predicate semantics extraction and evaluation
- strategy-based structural/query-feature handling
- synthesis alignment around `GenerationSpec`

This plan does **not** attempt in one pass to:
- solve every SQL feature exactly
- redesign `Instance` into a query-reasoning layer
- replace the planner subsystem
- remove every existing compatibility helper immediately

## Guiding Constraints
- Keep `GenerationSpec` as the main spec type.
- Keep `Instance` responsible for schema/materialization/runtime state, not query semantics.
- Prefer additive refactors with stable behavior before broad feature expansion.
- Each step must leave the repository in a testable state.

## End-State Architecture

### Central objects
- `GenerationSpec`
  - declarative query witness contract
  - includes structure, capability, unresolved gaps, provenance
- `CapabilityReport`
  - query support/confidence classification
- `PredicateSemantics`
  - type/dialect-aware predicate interpretation

### Primary flows
1. planner graph / scope traversal
2. capability visitor
3. generation-spec collector
4. synthesis from `GenerationSpec`
5. row materialization via `Instance`

## Target Module Responsibilities

### `src/parseval/speculative/specs.py`
Keep and expand:
- `GenerationSpec`
- structural spec dataclasses
- unresolved/provenance dataclasses

### `src/parseval/speculative/capability.py`
Add:
- `CapabilityLevel`
- `CapabilityIssue`
- `CapabilityReport`

### `src/parseval/speculative/collector.py`
Refactor into:
- planner-driven collector that fills `GenerationSpec`
- provenance recording
- unresolved-fragment recording

### `src/parseval/speculative/semantics.py`
Add:
- `PredicateSemantics`
- type/dialect-aware operator/function interpretation helpers

### `src/parseval/speculative/strategies/`
Add registries/modules for:
- filters
- joins
- aggregates
- subqueries
- windows
- set operations
- functions

### `src/parseval/speculative/generator.py`
Refactor into:
- orchestration only
- no deep extraction logic
- no large semantic branching

### `src/parseval/speculative/planner.py`
Refactor into:
- consumer of `GenerationSpec`
- row planning/materialization logic driven by the spec, not raw query traversal

## Dependency-Ordered Milestones

### Milestone 1: Expand `GenerationSpec`

#### Goal
Make `GenerationSpec` the explicit architecture boundary.

#### Changes
- Add to `GenerationSpec`:
  - `capability`
  - `order_specs`
  - `window_specs`
  - `unresolved`
  - `provenance`
- Add supporting dataclasses such as:
  - `OrderSpec`
  - `WindowSpec`
  - `GenerationGap`
  - `GenerationTrace`

#### Files
- `src/parseval/speculative/specs.py`
- possibly `src/parseval/speculative/__init__.py`

#### Validation
- add/extend direct unit tests for spec construction
- run speculative architecture tests

#### Exit Criteria
- `GenerationSpec` can represent both current extracted requirements and future capability/provenance metadata

### Milestone 2: Add Capability Model

#### Goal
Introduce first-class capability reporting without changing generation behavior yet.

#### Changes
- Add:
  - `CapabilityLevel`
  - `CapabilityIssue`
  - `CapabilityReport`
- Recommended levels:
  - `SUPPORTED`
  - `PARTIAL`
  - `HEURISTIC`
  - `UNSUPPORTED`

#### Files
- `src/parseval/speculative/capability.py`
- `src/parseval/speculative/__init__.py`

#### Validation
- unit tests for report construction and ordering/composition rules

#### Exit Criteria
- capability objects exist and are reusable by collectors and strategies

### Milestone 3: Minimal Capability Visitor

#### Goal
Add planner-backed capability analysis before synthesis.

#### Changes
- create capability visitor over planner scope graph
- initial support matrix:
  - simple filter queries
  - basic joins
  - current supported aggregates/subqueries
- classify everything else conservatively

#### Files
- `src/parseval/speculative/capability_rules.py`
- `src/parseval/speculative/collector.py` or dedicated capability visitor module
- maybe `src/parseval/plan/visitor.py` consumers

#### Validation
- add tests for:
  - supported filter query
  - supported join query
  - unsupported query shape
  - partial/heuristic examples if added early

#### Exit Criteria
- a query can be classified before synthesis begins

### Milestone 4: Wire Capability Into `GenerationSpec`

#### Goal
Every collected `GenerationSpec` carries its capability result.

#### Changes
- collector stores capability report in `GenerationSpec.capability`
- unsupported or downgraded fragments are added to `GenerationSpec.unresolved`
- provenance traces record why a level was assigned

#### Files
- `src/parseval/speculative/collector.py`
- `src/parseval/speculative/specs.py`

#### Validation
- tests assert `GenerationSpec.capability`
- tests assert unresolved/provenance entries are populated

#### Exit Criteria
- capability is part of the spec, not a side result

### Milestone 5: Introduce `PredicateSemantics`

#### Goal
Separate type/dialect interpretation from row generation and storage.

#### Changes
- add `PredicateSemantics` service
- move type-aware predicate interpretation there:
  - numeric comparison
  - text comparison
  - date/datetime comparison
  - null checks
  - implicit coercion handling
- start with operators before function semantics

#### Files
- `src/parseval/speculative/semantics.py`
- `src/parseval/speculative/values.py`
- `src/parseval/speculative/collector.py`

#### Validation
- tests for:
  - numeric column `> literal`
  - text column `> literal`
  - date column `> 'YYYY-MM-DD'`
  - null predicates

#### Exit Criteria
- type/dialect predicate logic has one home

### Milestone 6: Structural Strategy Interfaces

#### Goal
Make query-feature handling registry-driven.

#### Changes
- define strategy interfaces for:
  - filters
  - joins
  - groups/having
  - subqueries
- each strategy provides:
  - `matches`
  - `capability`
  - `contribute_to_spec`

#### Files
- `src/parseval/speculative/strategies/__init__.py`
- `src/parseval/speculative/strategies/filters.py`
- `src/parseval/speculative/strategies/joins.py`
- `src/parseval/speculative/strategies/aggregates.py`
- `src/parseval/speculative/strategies/subqueries.py`

#### Validation
- unit tests for registry matching
- collector tests verifying the correct strategy contributes specs

#### Exit Criteria
- collector no longer depends on long ad hoc branching for those feature families

### Milestone 7: Order and Window Specs

#### Goal
Add first-class support for ordering and window-driven witnesses.

#### Changes
- add `OrderSpec`
- add `WindowSpec`
- begin with `ROW_NUMBER`
- model:
  - partition columns
  - order columns
  - rank filter targets

#### Files
- `src/parseval/speculative/specs.py`
- `src/parseval/speculative/collector.py`
- `src/parseval/speculative/strategies/windows.py`

#### Validation
- direct spec tests for queries with `ROW_NUMBER`
- end-to-end witness tests for rank=1 / rank<=k shapes

#### Exit Criteria
- windows/order are expressed in `GenerationSpec`, not hidden in heuristics

### Milestone 8: Function and Aggregate Strategy Consolidation

#### Goal
Continue the strategy-pattern direction already started for functions.

#### Changes
- keep function registry as the semantic center for supported functions
- connect capability analysis to registered function strategies
- extend aggregate-specific witness logic similarly where useful

#### Files
- `src/parseval/speculative/functions.py`
- `src/parseval/speculative/values.py`
- `src/parseval/speculative/capability_rules.py`

#### Validation
- supported vs unsupported function capability tests
- existing function-generation tests

#### Exit Criteria
- function support classification and generation draw from the same registry mindset

### Milestone 9: Synthesis Uses Only `GenerationSpec`

#### Goal
Decouple synthesis from raw planner/query traversal.

#### Changes
- ensure speculative planner/materializer reads only from `GenerationSpec`
- remove remaining places where synthesis implicitly inspects raw query structure unless absolutely necessary

#### Files
- `src/parseval/speculative/planner.py`
- `src/parseval/speculative/generator.py`

#### Validation
- smoke tests for filter/join/group/subquery/window queries

#### Exit Criteria
- `GenerationSpec` is truly the bridge between analysis and synthesis

### Milestone 10: Execution Policy and Fallbacks

#### Goal
Use capability to route execution behavior.

#### Changes
- `SUPPORTED`: normal synthesis
- `PARTIAL`: synthesize covered parts, use bounded completion for gaps
- `HEURISTIC`: run heuristic witness recipe with bounded retries
- `UNSUPPORTED`: skip or explicit coarse fallback

#### Files
- `src/parseval/speculative/generator.py`
- `src/parseval/main.py` if routing needs to be exposed

#### Validation
- tests for query routing behavior
- tests for explicit unsupported reporting

#### Exit Criteria
- capability is actionable, not just descriptive

### Milestone 11: Provenance and Debugging

#### Goal
Make the system explainable.

#### Changes
- record:
  - which strategies matched
  - where capability was downgraded
  - which fragments were unresolved
  - which witness assumptions were introduced

#### Files
- `src/parseval/speculative/specs.py`
- `src/parseval/speculative/collector.py`
- `src/parseval/speculative/generator.py`

#### Validation
- provenance-focused tests or snapshot assertions

#### Exit Criteria
- debugging support is built into the spec

## Bite-Size Task Breakdown

### Task Group A: Foundation
1. Extend `GenerationSpec` fields.
2. Add `CapabilityLevel`, `CapabilityIssue`, `CapabilityReport`.
3. Add direct tests for new dataclasses.

### Task Group B: First Capability Pass
4. Add minimal capability visitor for simple filters and joins.
5. Attach capability to collected `GenerationSpec`.
6. Add unresolved/provenance recording stubs.

### Task Group C: Type Semantics
7. Create `PredicateSemantics`.
8. Move comparison/coercion logic into it.
9. Add tests for mixed-type predicates and date/text comparisons.

### Task Group D: Strategy Migration
10. Create filter strategy interface and registry.
11. Migrate current filter collection logic.
12. Create join strategy interface and registry.
13. Migrate current join logic.
14. Create aggregate/subquery strategy registries.

### Task Group E: New Feature Families
15. Add `OrderSpec`.
16. Add `WindowSpec`.
17. Implement `ROW_NUMBER` capability + collection.
18. Add witness-generation logic for simple ranking queries.

### Task Group F: Synthesis Alignment
19. Refactor speculative planner to consume only `GenerationSpec`.
20. Remove remaining raw-query dependencies from synthesis where possible.
21. Tighten fallback routing by capability level.

### Task Group G: Observability
22. Add full provenance tracking.
23. Add trace-oriented tests.
24. Document supported/partial/heuristic/unsupported meanings in code/docs.

## Suggested PR Sequence

### PR 1
- expand `GenerationSpec`
- add capability dataclasses
- add tests

### PR 2
- minimal capability visitor
- attach capability to `GenerationSpec`
- add simple supported/unsupported tests

### PR 3
- introduce `PredicateSemantics`
- migrate current operator/type comparison logic
- add semantics tests

### PR 4
- introduce filter/join registries
- migrate collector logic for those features

### PR 5
- add unresolved/provenance tracking
- add trace tests

### PR 6
- add order/window specs
- implement `ROW_NUMBER` path

### PR 7
- route execution by capability
- add partial/heuristic handling

## Validation Matrix

### Unit-level
- spec dataclasses
- capability composition
- semantics decisions
- strategy matching

### Mid-level
- collector emits expected `GenerationSpec`
- synthesis consumes `GenerationSpec` correctly

### End-to-end
- selected speculative query fixtures
- selected Bird-style smoke queries
- instance materialization tests

## Risks and Mitigations

### Risk 1: Too much refactor at once
Mitigation:
- use the PR sequence above
- do not combine spec expansion, semantics migration, and window support in one patch

### Risk 2: Capability semantics become subjective
Mitigation:
- define levels in code and tests early
- classify conservatively

### Risk 3: `GenerationSpec` becomes a dumping ground
Mitigation:
- keep structural fields, capability, unresolved, provenance clearly separated

### Risk 4: Synthesis still leaks raw query knowledge
Mitigation:
- use milestone 9 as a hard architectural checkpoint

## Immediate Next Step
Implement **PR 1**:
- expand `GenerationSpec`
- add capability dataclasses
- add tests for the new spec structure

## Success Criteria
- [ ] `GenerationSpec` is the explicit central contract
- [ ] capability analysis exists as a first-class pass
- [ ] type/dialect predicate semantics are centralized
- [ ] structural feature handling is strategy-driven
- [ ] order/window support has a clean architectural home
- [ ] synthesis is driven by `GenerationSpec`
- [ ] capability routing is explicit and testable
