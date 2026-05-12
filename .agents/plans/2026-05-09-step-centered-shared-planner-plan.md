# Step-Centered Shared Planner Refactor Plan

## Objective
Refactor `parseval.plan` into a shared planning layer that can serve both the symbolic execution generator and the speculative generator while preserving `sqlglot.planner.Plan` and `sqlglot.planner.Step` as the canonical relational plan representation inside each SQL scope.

The target outcome is not a new planner IR that replaces `sqlglot`, but a ParSEval planning architecture that:

- uses `ScopeNode` to model inter-scope dependencies
- uses `sqlglot.planner.Plan` and `Step` to model intra-scope relational operators
- attaches ParSEval-specific metadata, facts, and backend analysis to scopes and steps
- lets symbolic and speculative generation consume the same scope/step structure through different lowerers

## Why This Approach
The current design already has the right raw ingredients:

- `build_graph_from_scopes` in `src/parseval/plan/planner.py` builds the scope dependency graph
- `sqlglot.planner.Plan` is already used inside `Planner.encode()` to traverse scope-local operators
- symbolic generation in `src/parseval/data_generator.py` depends on this traversal
- speculative generation in `src/parseval/speculative/collector.py` already reuses the scope graph visitor

The architectural problem is not lack of a planner. It is that the current `Planner` class conflates four different responsibilities:

1. scope graph concerns
2. step traversal concerns
3. symbolic encoding concerns
4. context/materialization concerns

Because of that coupling:

- the symbolic path treats `Planner` as both planner and encoder
- the speculative path bypasses `Step` and constructs a second planning model
- planner logic cannot be reused cleanly without dragging symbolic side effects

Using `Step` as the shared plan unit avoids duplicating the relational plan model and keeps ParSEval aligned with `sqlglot`.

## Research Summary
- `src/parseval/plan/planner.py`
  - Contains `ScopeNode`, `Graph`, `build_graph_from_scopes`, and `Planner`
  - `Planner.encode()` constructs a `sqlglot.planner.Plan` from the current scope expression and dispatches on `Scan`, `Aggregate`, `Join`, `Sort`, `SetOperation`
  - The same class also mutates `UExprToConstraint` through symbolic side effects
- `src/parseval/plan/visitor.py`
  - Already provides a scope-level visitor abstraction over `build_graph_from_scopes`
- `src/parseval/data_generator.py`
  - Still depends on `Planner` directly for symbolic encoding inside `_encode_scope`
  - Uses scope ordering and subquery bindings during generation
- `src/parseval/speculative/collector.py`
  - Reuses scope traversal but performs its own scope-local analysis
  - Collects backend-specific specs instead of consuming `Step`
- Recent learnings in `.agents/learnings/`
  - planner-side traversal abstractions are worth centralizing in `plan/`
  - speculative code should remain import-light and not depend on symbolic/SMT stacks
  - reusable abstractions should expose true domain boundaries rather than compatibility shims

## Design Principles
1. `sqlglot` owns logical query decomposition.
2. ParSEval should annotate and interpret `Step`, not replace it.
3. Shared planning code must be importable without pulling in SMT-only dependencies.
4. Scope-level planning and scope execution/encoding must be separable.
5. Symbolic and speculative backends may diverge in solving strategy, but not in the fundamental scope/step structure they inspect.
6. Shared planner outputs should describe facts and annotations, not commit to one backend's solving model.

## Proposed Architecture

### Layer 1: Scope Graph
Keep and stabilize the existing scope graph API.

Primary responsibilities:

- build scope dependency DAG from `sqlglot` scopes
- classify dependency direction
- mark correlated dependencies
- expose deterministic dependency order

Primary types:

- `ScopeNode`
- `Graph`
- `build_graph_from_scopes`
- `walk_scope_graph`

Likely module shape:

- `src/parseval/plan/graph.py`
- `src/parseval/plan/visitor.py`

Notes:

- This layer should contain no symbolic tracer logic.
- It should depend only on `sqlglot`, helper normalization utilities, and planner-local dataclasses.

### Layer 2: Scope Plan
Introduce a planner object that wraps one `ScopeNode` and one `sqlglot.planner.Plan`.

Primary responsibilities:

- build `Plan(scope.expression)` for one scope
- normalize traversal ordering over `Step` nodes
- expose stable access to dependencies, dependents, root step, and ordered steps
- provide a place to store ParSEval annotations keyed by `Step`

Primary type:

- `ScopePlan`

Suggested shape:

```python
@dataclass
class ScopePlan:
    scope_node: ScopeNode
    plan: Plan
    ordered_steps: tuple[Step, ...]
    step_annotations: dict[str, StepAnnotations]

    @property
    def scope_id(self) -> int: ...

    @property
    def expression(self) -> exp.Expression: ...

    def annotation_for(self, step: Step) -> "StepAnnotations": ...
```

The key point is that `ScopePlan` does not solve or encode anything. It is a reusable plan wrapper.

### Layer 3: Step Annotations
Add a neutral annotation object for ParSEval facts about a `Step`.

This should not attempt to replace `Step` semantics. It should only capture normalized metadata that both backends may need.

Primary type:

- `StepAnnotations`

Suggested contents:

```python
@dataclass
class StepAnnotations:
    step_id: str
    step_type: str
    source_tables: tuple[str, ...] = ()
    referenced_columns: tuple[exp.Column, ...] = ()
    projected_columns: tuple[str, ...] = ()
    condition: exp.Expression | None = None
    join_keys: tuple[tuple[exp.Column, exp.Column], ...] = ()
    aggregate_exprs: tuple[exp.Expression, ...] = ()
    order_exprs: tuple[exp.Expression, ...] = ()
    output_nullability: dict[str, bool] = field(default_factory=dict)
    output_uniqueness: dict[str, bool] = field(default_factory=dict)
    flags: frozenset[str] = frozenset()
    metadata: dict[str, Any] = field(default_factory=dict)
```

The annotations should be descriptive rather than prescriptive. They record what a step means or implies, not how a backend must satisfy it.

### Layer 4: Step Analysis
Add analyzer passes that populate `StepAnnotations` for a `ScopePlan`.

Primary responsibilities:

- inspect each `Step`
- extract planner-relevant facts in a normalized form
- attach those facts to `ScopePlan.step_annotations`

Candidate analyzers:

- `ColumnReferenceAnalyzer`
- `ProjectionAnalyzer`
- `JoinAnalyzer`
- `AggregateAnalyzer`
- `SortAnalyzer`
- `SubqueryOutputAnalyzer`

These can be implemented either as one multi-pass analyzer or as a small pipeline of passes. The important constraint is that they depend only on `ScopePlan`, `Step`, `Context`, and `sqlglot`, not on tracer/SMT code.

### Layer 5: Backend Lowerers
Backend-specific code should consume `ScopePlan` plus `StepAnnotations`.

#### Symbolic Lowerer
Consumes:

- `ScopePlan`
- `Context`
- `StepAnnotations`

Produces:

- tracer nodes / branch predicates
- symbolic table projections
- aggregate and join path constraints

This is effectively the role of the current `Planner`, but after refactor it should be renamed and narrowed into a symbolic encoder, for example:

- `SymbolicStepEncoder`
- `SymbolicScopeEncoder`

#### Speculative Lowerer
Consumes:

- `ScopePlan`
- schema/domain metadata
- `StepAnnotations`

Produces:

- `GenerationSpec`
- step-derived generation hints
- eventually `TablePlan` inputs

This replaces speculative extraction that currently bypasses `Step`.

## Target Module Layout
One reasonable end state:

```text
src/parseval/plan/
  __init__.py
  graph.py
  visitor.py
  context.py
  scope_plan.py
  annotations.py
  analysis.py
  symbolic.py
```

Where:

- `graph.py`
  - `ScopeNode`, `Graph`, `build_graph_from_scopes`
- `scope_plan.py`
  - `ScopePlan`, step ordering helpers
- `annotations.py`
  - `StepAnnotations`, shared planner metadata types
- `analysis.py`
  - backend-neutral annotation passes
- `symbolic.py`
  - symbolic lowering/encoding formerly embedded in `Planner`

Speculative modules would import from these planner-core modules, but planner-core would not import speculative modules.

## Concrete Refactor Plan

### Phase 1: Split Current Planner by Responsibility
Goal: separate generic planning utilities from symbolic encoding behavior without changing behavior.

Tasks:

1. Move `ScopeNode`, `Graph`, `_has_true_parent_correlation`, and `build_graph_from_scopes` into a graph-focused module.
2. Leave current `Planner` behavior intact but relocate only what is obviously backend-neutral.
3. Update imports in:
   - `src/parseval/plan/__init__.py`
   - `src/parseval/speculative/collector.py`
   - `src/parseval/data_generator.py`
   - tests that import planner graph utilities

Expected result:

- scope graph utilities are explicitly planner-core
- symbolic encoding remains behaviorally unchanged

### Phase 2: Introduce `ScopePlan`
Goal: make scope-local `Plan`/`Step` traversal reusable independent of symbolic encoding.

Tasks:

1. Add `ScopePlan` that:
   - stores `ScopeNode`
   - constructs `Plan(scope.expression)`
   - computes deterministic `ordered_steps`
2. Extract the current queue traversal logic from `Planner.encode()` into reusable helpers on `ScopePlan`.
3. Add tests for step ordering and root-step discovery on representative scope shapes:
   - simple scan/filter/projection
   - join
   - aggregate/having
   - set operation

Expected result:

- both backends can iterate the same ordered `Step` sequence for one scope
- planning no longer requires a tracer object

### Phase 3: Introduce Shared `StepAnnotations`
Goal: define the shared metadata contract around `Step`.

Tasks:

1. Add `StepAnnotations` dataclass.
2. Add `ScopePlan.annotation_for(step)` and storage keyed by step identity.
3. Implement first-pass extraction for the most stable facts:
   - step type/name
   - condition expression
   - referenced columns
   - projected column names
   - direct source tables
4. Add tests that assert these annotations on known SQL shapes.

Expected result:

- ParSEval has a shared vocabulary around `Step` without replacing `Step`

### Phase 4: Extract Symbolic Encoding into a Dedicated Lowerer
Goal: stop using `Planner` as the generic planner.

Tasks:

1. Rename or replace `Planner` with a symbolic-specific class such as `SymbolicScopeEncoder`.
2. Change it to accept `ScopePlan` rather than reconstructing `Plan(scope.expression)` internally.
3. Move symbolic-only methods out of planner-core:
   - `scan`
   - `aggregate`
   - `join`
   - `sort`
   - `set_operation`
   - branch/tracer side effects
4. Keep existing symbolic tests green during the move.

Expected result:

- symbolic encoding becomes one consumer of the shared planner
- `parseval.plan` stops presenting a symbolic encoder as the main planner API

### Phase 5: Rebuild Speculative Collection on `ScopePlan`
Goal: make speculative planning step-aware and aligned with the same plan model.

Tasks:

1. Update `SpecCollectorVisitor` to build a `ScopePlan` per visited scope.
2. Replace expression-only heuristics with step-driven analysis where possible.
3. Map step annotations into current speculative outputs:
   - filter specs
   - join specs
   - group/aggregate specs
   - window/order specs
   - subquery specs
4. Keep speculative-specific strategy registries, but feed them `ScopePlan` and `StepAnnotations` rather than raw scope fragments when possible.

Expected result:

- speculative generation consumes the same scope/step model as symbolic generation
- speculative logic remains import-light and backend-specific only at lowering time

### Phase 6: Simplify Cross-Backend Scope Handling
Goal: unify scope orchestration patterns used by both generators.

Tasks:

1. Introduce a shared scope orchestration helper that iterates the scope graph and yields `ScopePlan` objects in dependency order.
2. Standardize handling for:
   - dependency ordering
   - correlated dependency flags
   - bound subquery outputs
   - root vs subquery vs derived table roles
3. Ensure orchestration code is shared while solve/materialization actions stay backend-specific.

Expected result:

- both generators follow the same dependency contract across scopes
- duplicated orchestration logic decreases

## API Sketch

### Shared Planner API

```python
graph = build_graph_from_scopes(expr)

for scope_node in graph.iter_dependency_order():
    scope_plan = ScopePlan.from_scope_node(scope_node)
    analyze_scope_plan(scope_plan, context=planner_context)
```

### Symbolic Usage

```python
scope_plan = ScopePlan.from_scope_node(scope_node)
analyze_scope_plan(scope_plan, context=context)
encoder = SymbolicScopeEncoder(
    scope_plan=scope_plan,
    ctx=context,
    tracer=tracer,
    dialect=dialect,
)
result_context = encoder.encode()
```

### Speculative Usage

```python
scope_plan = ScopePlan.from_scope_node(scope_node)
analyze_scope_plan(scope_plan, context=context)
collector = SpeculativeScopeLowerer(
    scope_plan=scope_plan,
    instance=instance,
    capability_report=report,
)
collector.contribute(generation_spec)
```

## Testing Strategy

### Preserve Existing Behavior
The following current tests should continue to pass throughout the refactor:

- `tests/test_planner.py`
- `tests/test_planner_symbolic.py`

These protect:

- symbolic branch encoding
- aggregate/having encoding
- scope graph ordering
- set operation symbolic behavior

### Add Planner-Core Tests
Add new tests that do not depend on symbolic solving:

- graph construction tests
- `ScopePlan` step ordering tests
- step annotation extraction tests
- subquery and correlated-scope metadata tests

These should assert planner-core behavior directly without instantiating SMT or running generation.

### Add Cross-Backend Consistency Tests
Add tests that verify a representative SQL query yields:

- the same scope graph
- the same step ordering
- compatible per-step facts for both symbolic and speculative backends

Candidate query classes:

- single-table filter/projection
- inner/outer join
- group by + having
- scalar subquery
- correlated `EXISTS`
- `UNION` / `INTERSECT`
- window function with order/partition

## Risks and Mitigations

### Risk: `Step` APIs may not expose every fact the speculative path currently extracts from raw expressions
Mitigation:

- treat `Step` as the primary structure, but allow annotations to inspect underlying `scope.expression` where `Step` is not sufficient
- keep the shared contract centered on `Step`, not limited only to fields directly present on `Step`

### Risk: `Planner.encode()` currently couples traversal order with symbolic side effects
Mitigation:

- extract `ScopePlan` traversal first without changing symbolic logic
- only then redirect symbolic encoding to consume `ScopePlan`

### Risk: speculative code may accidentally import symbolic/SMT modules during refactor
Mitigation:

- keep shared planner modules free of tracer and solver imports
- move symbolic code into a dedicated module early

### Risk: correlated subqueries may still require backend-specific handling
Mitigation:

- represent correlation as planner metadata on `ScopeNode` and `ScopePlan`
- keep actual solve/materialization policies backend-specific

## Decisions To Lock Before Implementation
These should be agreed before the first refactor patch:

1. `Step` remains the canonical intra-scope plan unit.
2. `ScopeNode` remains the canonical inter-scope dependency unit.
3. Shared planner outputs are annotations/facts around scopes and steps, not a replacement operator tree.
4. Symbolic encoding moves behind a symbolic-specific class name and API.
5. Speculative collection should gradually move from expression-driven extraction to step-aware lowering.

## First Implementation Slice
To keep the first code change low-risk, the recommended first slice is:

1. extract graph utilities from `planner.py`
2. add `ScopePlan` with deterministic step ordering
3. add planner-core tests for `ScopePlan`
4. keep current symbolic `Planner` behavior unchanged except for consuming the extracted helpers

This slice creates the shared foundation without forcing immediate symbolic/speculative rewrites.

## Success Criteria
- [ ] `parseval.plan` exposes graph and scope-plan APIs that are backend-neutral
- [ ] `ScopePlan` wraps `sqlglot.planner.Plan` and `Step` without replacing them
- [ ] ParSEval-specific step facts are represented as annotations around `Step`
- [ ] symbolic encoding becomes an explicit backend consumer of shared planner structures
- [ ] speculative collection can consume `ScopePlan` and step annotations
- [ ] planner-core remains import-light and does not require SMT dependencies
- [ ] planner-core tests exist independently of symbolic solving

## Validation Strategy
- Run planner-focused tests after each structural phase
- Keep symbolic regression tests green while extracting planner-core modules
- Add planner-core tests before changing speculative collection
- After speculative migration, add cross-backend consistency tests on representative SQL shapes

## Sources
- Local code: `src/parseval/plan/planner.py`
- Local code: `src/parseval/plan/visitor.py`
- Local code: `src/parseval/data_generator.py`
- Local code: `src/parseval/speculative/collector.py`
- Local code: `src/parseval/speculative/planner.py`
- Local tests: `tests/test_planner.py`
- Local tests: `tests/test_planner_symbolic.py`
- Local learnings:
  - `.agents/learnings/2026-05-09-speculative-import-decoupling.md`
  - `.agents/learnings/2026-05-09-speculative-visitor-and-strategy-patterns.md`
  - `.agents/learnings/2026-05-09-speculative-domain-backed-pools.md`
