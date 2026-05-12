# Symbolic Module Refined Design Plan

## Objective
Refine `src/parseval/symbolic/` into a clean, instance-driven symbolic data-generation subsystem.

This plan intentionally treats the new instance-centered workflow as the primary design and does not preserve the older tracer-first or plausible-tree-first architecture as a target. The goal is to produce code that is:

- structurally cleaner
- easier to optimize later
- easier to extend to groups / joins / subqueries
- directly aligned with how users actually run symbolic generation

## Core Design Statement
The symbolic subsystem starts from a current `Instance`.

The system repeatedly:

1. executes the planner tree over that instance
2. derives exact branch observations from runtime behavior
3. aggregates coverage from those observations
4. finds uncovered or weakly-covered branch opportunities
5. solves for additional row values or row insertions
6. mutates the same instance
7. re-executes and repeats

The main unit of progress is not a legacy symbolic path tree. It is improved branch coverage on a concrete evolving instance.

## Non-Goals
- preserving `DataGenerator` as the center of symbolic generation
- preserving `UExprToConstraint` semantics or plausible-tree state as the main symbolic model
- implementing all operator families immediately
- over-optimizing before the refined architecture is stable

## Why This Design Is Better

### Compared with the previous planner-anchored branch-goal-first design
The previous design had good internal abstractions:
- `BranchTemplate`
- `BranchInstance`
- `CoverageStore`
- `BranchGoal`

But it still risked becoming too abstract and solver-first:
- extract templates
- build coverage
- choose goal
- lower goal
- solve

That sequence is valid, but it is not the true operational center of ParSEval.

### Why the new design is better
The actual workflow is:
- start from a current instance
- run the query plan
- observe what happened
- add rows/values to cover missing behavior

That makes the system:
- execution-first
- instance-driven
- observational rather than purely hypothetical

This is better for:
- `CASE`
- `OR`
- group behavior
- contribution analysis
- partial / seeded instances

## Guiding Principles

1. The current `Instance` is the source of truth for symbolic generation state.
2. The planner tree is the source of truth for structural branch identity.
3. Coverage is derived from runtime execution, not guessed statically.
4. Branch templates and branch instances remain explicit immutable records.
5. The symbolic subsystem owns symbolic generation end-to-end.
6. Legacy generator and legacy uexpr code are not architectural dependencies.
7. Optimize for clear boundaries first, then optimize performance.

## Refined Architecture

The symbolic subsystem should be organized around these layers.

### Layer 1: Structural Plan Layer
Source:
- `ScopeNode`
- `ScopePlan`
- planner `Step`

Responsibilities:
- define structural execution order
- define stable `scope_id` and `step_id`
- expose step-local expressions
- identify which operator family a step belongs to

Non-responsibilities:
- coverage
- solving
- instance mutation

### Layer 2: Execution Layer
Main concept:
- `SymbolicScopeEncoder`

Supporting concepts:
- internal expression evaluation helpers
- per-operator execution helpers

Responsibilities:
- execute the planner tree over the current instance
- evaluate predicates / projections / groups / joins
- emit normalized execution facts
- cooperate with symbolic observation/coverage components

Important note:
`SymbolicScopeEncoder` can remain the public class, but it must stop being a giant catch-all implementation file over time.

Desired internal decomposition:
- `encoder.py`
  - public orchestration class
- internal execution helpers inside the same module or nearby helper modules
- expression evaluation helpers separated from branch/coverage logic

This plan does not require immediately splitting the encoder into many public files. The key requirement is responsibility separation, not file count.

### Layer 3: Observation and Coverage Layer
This layer should conceptually combine the current tracer and coverage responsibilities.

Main concepts:
- `ExecutionTrace`
- `BranchRecorder`
- `CoverageStore`

Responsibilities:
- capture per-run execution observations
- normalize observations into `BranchInstance`s
- aggregate across runs / attempts
- answer coverage questions

Important boundary:
- trace = one execution
- coverage = aggregated knowledge across executions

These are related but not identical. They should be colocated conceptually, but not collapsed into one muddy mutable object.

### Layer 4: Branch Model Layer
Main immutable records:
- `BranchTemplate`
- `BranchInstance`
- `ExecutionContextKey`
- `BranchGoal`
- `SymbolicBinding`

Responsibilities:
- represent branch opportunities structurally
- represent observed branch realizations
- represent coverage targets explicitly
- represent query-output binding materialization

These objects should remain immutable and lightweight.

### Layer 5: Scheduling Layer
Main concept:
- `BranchScheduler`

Responsibilities:
- inspect current coverage
- choose uncovered or under-covered branches
- apply user-configured prioritization
- return explicit `BranchGoal`s

Non-responsibilities:
- execution
- solving
- instance mutation

### Layer 6: Lowering Layer
Main concept:
- `BranchGoalLowerer`

Responsibilities:
- convert one branch goal into solver-facing constraints
- identify referenced columns
- add operator-specific lowering logic

This layer should grow by strategy/registry patterns, not by giant conditionals.

### Layer 7: Solve Layer
Main concept:
- `SolveSession`

Responsibilities:
- own per-attempt mutable solve state
- declare variables
- declare constraints
- run SMT solve
- return concrete assignments

Non-responsibilities:
- scheduling
- branch extraction
- plan execution

### Layer 8: Instance Mutation Layer
Main concept:
- `InstanceMutator` or equivalent internal service

Responsibilities:
- apply solved assignments as row insertions or value updates
- preserve instance consistency
- handle deduplication / normalization

This may initially remain folded into `SolveSession`, but the intended direction is to separate observation from mutation explicitly.

### Layer 9: High-Level Generator Layer
Main concept:
- `SymbolicDataGenerator`

Responsibilities:
- own the instance-driven loop
- coordinate plan execution, coverage, scheduling, solving, and mutation
- expose the public symbolic-generation API

This is the real top-level owner of symbolic generation, not `DataGenerator`.

## Recommended Design Patterns

### 1. Pipeline Pattern
The entire symbolic flow should be a staged pipeline:

1. analyze plan
2. execute current instance
3. record observations
4. compute coverage
5. schedule goals
6. lower goals
7. solve
8. mutate instance
9. repeat

This is the main organizing pattern.

### 2. Strategy Pattern
Use strategies for operator-family-specific behavior.

Examples:
- extraction by branch kind
- contribution analysis by branch kind
- lowering by branch kind
- mutation validation by result kind

This prevents `encoder.py`, `lowerer.py`, and `coverage.py` from turning into giant multi-operator conditional files.

### 3. Registry Pattern
Use registries for strategy lookup.

Examples:
- extraction registry
- lowering registry
- contribution registry

This keeps extension clean for later:
- group/having
- joins
- subqueries

### 4. Orchestrator Pattern
`SymbolicDataGenerator` should be the only high-level orchestrator.

Everything else should be a focused service used by it.

### 5. Ports-and-Adapters Mindset
The symbolic subsystem should treat these as boundaries:
- planner execution
- solver backend
- instance mutation

That will help performance work later.

## Desired Module Responsibilities

### `types.py`
Keep:
- `BranchTemplate`
- `BranchInstance`
- `ExecutionContextKey`
- `BranchGoal`

No heavy logic.

### `runtime.py`
Keep runtime result helpers here:
- `SymbolicBinding`
- binding materialization helpers
- possibly execution-trace records later

### `encoder.py`
Public symbolic execution entrypoint.

Should own:
- execution orchestration

Should not own:
- coverage aggregation policy
- goal scheduling
- solve orchestration

### `extractor.py`
Owns only structural branch extraction from `ScopePlan`.

### `coverage.py`
Should become the home of:
- per-run execution trace model
- recorder logic
- coverage aggregation

Current `recorder.py` may ultimately merge into this layer conceptually.

### `scheduler.py`
Goal selection only.

### `lowerer.py`
Goal-to-constraint translation only.

### `session.py`
Per-solve mutable state only.

### `branch_solver.py`
This is likely transitional.

Current overlap:
- `branch_solver.py` and `generator.py` both orchestrate part of the symbolic loop

Refined target:
- either merge the logic into `generator.py`
- or redefine `branch_solver.py` as a narrow helper service under the generator

The generator should be the true owner.

### `generator.py`
Top-level symbolic generation API and loop owner.

This should eventually be the only place users need to touch for symbolic data generation.

## Instance-Driven Loop Specification

For one supported scope:

1. Build `ScopePlan`.
2. Execute current instance through `SymbolicScopeEncoder`.
3. Record execution trace and derive `BranchInstance`s.
4. Aggregate `CoverageStore`.
5. Determine uncovered/weakly-covered branch templates.
6. Convert selected gaps into `BranchGoal`s.
7. Lower one or more goals into constraints.
8. Solve via `SolveSession`.
9. Apply solved values to the instance.
10. Re-execute and continue until:
   - coverage saturation
   - config budget exhausted
   - unsupported scope encountered

This is the central loop the code should be designed around.

## Coverage Semantics

Coverage should be computed from:
- current instance contents
- runtime execution over the planner tree
- branch instances keyed by `ExecutionContextKey`

Coverage should support:
- template-level coverage
- context-level coverage
- contribution labels
- repeated-hit statistics

The generator should always be able to answer:
- what is already covered by the current instance?
- what remains uncovered?
- which uncovered branch is worth solving next?

## Contribution Semantics

Contribution labeling remains important, but should be framed as part of the coverage model, not as a separate ad hoc side channel.

Initial labels:
- `active_contributing`
- `active_noncontributing`
- `potentially_contributing`

Later extension:
- `infeasible`
- possibly stronger distinctions for group/join/subquery contribution

## Supported Scope Policy

The symbolic subsystem should explicitly detect supported scope shapes.

Initial supported family:
- single-scope
- non-correlated
- no joins
- no aggregates
- filter / `OR` / projection `CASE`

Unsupported families should fail cleanly and explicitly, not through accidental fall-through.

## Public API Direction

The intended public usage should be:

```python
from parseval.symbolic import generate_symbolic_data
```

or:

```python
from parseval.symbolic import SymbolicDataGenerator
```

Users should not need `DataGenerator` for symbolic generation.

## Refactoring Priorities

### Phase 1: Structural Cleanup
1. Keep symbolic generation entirely inside `parseval.symbolic`.
2. Reduce package import cycles and broad plan imports.
3. Simplify public API shape.
4. Remove orchestration overlap between `generator.py` and `branch_solver.py`.

### Phase 2: Observation/Coverage Cleanup
1. Bring trace + recorder + coverage closer together.
2. Formalize per-run vs aggregated state.
3. Reduce accidental duplication between encoder hooks and recorder logic.

### Phase 3: Execution Cleanup
1. Reduce the size and mixed responsibilities of `encoder.py`.
2. Separate operator execution helpers from observation logic.
3. Prepare cleaner extension points for groups/joins/subqueries.

### Phase 4: Semantic Extension
1. group / having
2. joins
3. subqueries

### Phase 5: Optimization
Only after structure is stable:
- avoid redundant re-execution
- batch branch attempts
- optimize coverage indexing
- optimize solve-session reuse where safe

## Validation Strategy

The refined design should be validated through:

- unit tests for each symbolic layer
- direct symbolic API tests through `parseval.symbolic`
- end-to-end symbolic generation tests on supported query shapes
- planner integration tests
- later dataset-level evaluation once operator coverage expands

## Success Criteria
- symbolic generation is owned entirely by `parseval.symbolic`
- users can call the symbolic module directly
- the instance-driven loop is the actual orchestration model
- branch/coverage objects remain explicit and immutable
- the symbolic module is structurally cleaner and easier to optimize than the current version

## Immediate Next Implementation Tasks

1. collapse generator/branch-solver orchestration overlap
2. define a clearer execution-trace / coverage boundary
3. start shrinking `encoder.py` responsibility without over-fragmenting public surface
4. prepare group/having extension points on top of the refined design

## Bite-Size Implementation Breakdown

This breakdown turns the refined design into small, dependency-aware tasks that
can land incrementally without losing the instance-driven architecture.

### Task Group A: Generator Ownership and Public API

#### Task 1
Move the top-level symbolic loop ownership into `generator.py`.

Files:

- `src/parseval/symbolic/generator.py`
- `src/parseval/symbolic/branch_solver.py`

Validation:

- generator-level unit tests still pass
- `generate_symbolic_data(...)` remains the main entrypoint

#### Task 2
Redefine `branch_solver.py` as a narrow helper or remove it if `generator.py`
fully absorbs the orchestration.

Files:

- `src/parseval/symbolic/branch_solver.py`
- `src/parseval/symbolic/generator.py`
- `src/parseval/symbolic/__init__.py`

Validation:

- no duplicated orchestration path remains
- imports continue to work from `parseval.symbolic`

#### Task 3
Add a direct supported-scope gate owned by the generator layer.

Files:

- `src/parseval/symbolic/generator.py`
- tests for supported and unsupported scope shapes

Validation:

- single-scope / non-join / non-aggregate shapes are accepted
- unsupported shapes fail explicitly and deterministically

### Task Group B: Execution Trace and Coverage Boundary

#### Task 4
Introduce an explicit per-run `ExecutionTrace` record in the coverage layer.

Files:

- `src/parseval/symbolic/coverage.py`
- possibly `src/parseval/symbolic/runtime.py` if binding-facing helpers are needed

Validation:

- one execution can produce a standalone trace object
- trace state is separate from aggregated coverage state

#### Task 5
Refactor `BranchRecorder` to populate `ExecutionTrace` instead of exposing only
an append-only `instances` list.

Files:

- `src/parseval/symbolic/recorder.py`
- `src/parseval/symbolic/coverage.py`

Validation:

- runtime recording still produces deterministic `BranchInstance`s
- stable `step_id` recording remains intact

#### Task 6
Teach `CoverageStore` to ingest traces and answer the core generator questions:
covered, uncovered, and weakly covered branches.

Files:

- `src/parseval/symbolic/coverage.py`
- `src/parseval/symbolic/types.py`

Validation:

- tests cover template-level coverage
- tests cover context-level coverage
- tests cover repeated-hit counts

### Task Group C: Scheduler and Goal Semantics

#### Task 7
Tighten `BranchGoal` semantics so goals represent coverage gaps from the current
instance, not abstract template wishes.

Files:

- `src/parseval/symbolic/types.py`
- `src/parseval/symbolic/scheduler.py`

Validation:

- goal fields are immutable and minimal
- goal tests assert current-instance-driven meaning

#### Task 8
Expand `BranchScheduler` to choose uncovered first, then weakly covered
branches, under explicit policy.

Files:

- `src/parseval/symbolic/scheduler.py`
- `src/parseval/symbolic/coverage.py`

Validation:

- scheduler ordering is deterministic
- include-covered / weak-coverage behavior is tested

### Task Group D: Solve and Mutation Separation

#### Task 9
Keep `SolveSession` focused on per-attempt solver state and return a clearer
assignment result object.

Files:

- `src/parseval/symbolic/session.py`
- `src/parseval/symbolic/lowerer.py`

Validation:

- solve-layer tests assert returned assignments separately from mutation

#### Task 10
Add an `InstanceMutator` service or equivalent narrow helper for applying solved
assignments to the live instance.

Files:

- new `src/parseval/symbolic/mutator.py` or equivalent helper module
- `src/parseval/symbolic/generator.py`
- `src/parseval/symbolic/session.py`

Validation:

- mutation tests cover row insertion and value update paths
- solve success does not imply silent mutation failure

#### Task 11
Refactor the generator loop to run: execute -> trace -> cover -> schedule ->
lower -> solve -> mutate -> re-execute.

Files:

- `src/parseval/symbolic/generator.py`
- supporting symbolic modules as needed

Validation:

- direct symbolic API test proves at least one re-execution cycle
- solved result reflects the mutated instance, not only the first solve

### Task Group E: Encoder Responsibility Reduction

#### Task 12
Extract expression-evaluation helpers from `encoder.py` without changing public
encoder behavior.

Files:

- `src/parseval/symbolic/encoder.py`
- new nearby helper module if needed

Validation:

- existing encoder behavior remains unchanged for current supported shapes
- no public API expansion is required

#### Task 13
Extract runtime branch-recording call sites behind small encoder-local helper
methods so observation logic is not interleaved everywhere.

Files:

- `src/parseval/symbolic/encoder.py`
- `src/parseval/symbolic/recorder.py`

Validation:

- filter and `CASE` recording still emit the same branch instances
- encoder methods become smaller and easier to test

#### Task 14
Introduce operator-family execution helpers for the currently supported subset:
filter, `OR`, and projection `CASE`.

Files:

- `src/parseval/symbolic/encoder.py`
- helper modules if needed

Validation:

- supported-family tests still pass
- unsupported operators remain explicit rather than accidental

### Task Group F: Extension Points for Next Operator Families

#### Task 15
Add strategy/registry seams for branch lowering by branch kind before group and
having support lands.

Files:

- `src/parseval/symbolic/lowerer.py`
- new lowering registry/helper modules if needed

Validation:

- current branch kinds are registered through the new path
- no large new conditional chain is introduced

#### Task 16
Add placeholder branch-kind and contribution hooks for group/having without
claiming full semantic support yet.

Files:

- `src/parseval/symbolic/types.py`
- `src/parseval/symbolic/extractor.py`
- `src/parseval/symbolic/coverage.py`

Validation:

- code can represent upcoming group/having targets cleanly
- supported-scope policy still rejects unsupported aggregate execution

### Recommended Execution Order

1. Task 1
2. Task 2
3. Task 3
4. Task 4
5. Task 5
6. Task 6
7. Task 7
8. Task 8
9. Task 9
10. Task 10
11. Task 11
12. Task 12
13. Task 13
14. Task 14
15. Task 15
16. Task 16

### Suggested PR Sequence

#### PR 1
- Task 1
- Task 2
- Task 3

#### PR 2
- Task 4
- Task 5
- Task 6

#### PR 3
- Task 7
- Task 8

#### PR 4
- Task 9
- Task 10
- Task 11

#### PR 5
- Task 12
- Task 13
- Task 14

#### PR 6
- Task 15
- Task 16
