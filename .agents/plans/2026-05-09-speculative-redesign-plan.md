# Speculative Module Redesign Plan

## Objective
Redesign the speculative generation module so it scales to broader SQL coverage through explicit architecture rather than incremental patches. The central design decision is to evolve the current `GenerationSpec` into the permanent richer spec abstraction instead of replacing or renaming it.

## Design Direction

### Core definition
Speculative generation should be treated as a **witness construction subsystem**.

Given:
- schema DDL
- SQL query
- dialect

it should produce:
- a small database instance likely to make the query return rows
- a capability report explaining confidence/support gaps
- a trace of which witness strategies were used

### Primary architectural principles
- Reuse planner output as the canonical query structure.
- Keep `Instance` as schema/materialization state, not query reasoning.
- Make capability analysis explicit and strategy-driven.
- Keep type/dialect semantics separate from row storage.
- Evolve `GenerationSpec` into `WitnessSpec` rather than replacing it in one patch.

## Why Evolve `GenerationSpec`

### Current `GenerationSpec`
`GenerationSpec` is already the speculative module’s extracted constraint container:
- `column_specs`
- `join_specs`
- `group_specs`
- `subquery_specs`
- `set_op_specs`

This is useful and already wired into the current generator/planner path.

### Why it is not enough yet
The current shape is still too close to the current implementation:
- no explicit capability/reporting
- no window/order/limit witness requirements
- no semantics/coercion annotations
- no provenance/debug metadata
- no distinction between exact vs partial vs heuristic support
- no unresolved requirements model

### Recommended move
Treat `GenerationSpec` as the permanent central declarative spec for speculative generation and expand it until it fully captures the witness-generation contract.

Practical migration rule:
- keep the existing name `GenerationSpec`
- expand structure and semantics in place
- document the new meaning clearly rather than renaming to `WitnessSpec`

## Proposed Architecture

### 1. Planner Analysis Layer
Responsibility:
- walk normalized planner/scope structure
- identify query shapes and dependencies

Implementation:
- continue using `build_graph_from_scopes`
- keep planner visitor entry points in `parseval.plan`
- speculative visitors consume planner outputs rather than doing ad hoc traversal in the generator

Outputs:
- structural query fragments
- scope-local witness requirements
- feature usage metadata for capability analysis

### 2. Capability Layer
Responsibility:
- determine how confidently speculative generation can handle a query
- route the query to the right synthesis path

Recommended levels:
- `SUPPORTED`
- `PARTIAL`
- `HEURISTIC`
- `UNSUPPORTED`

Recommended semantics:
- `SUPPORTED`: explicit witness strategy exists and is intended to work end-to-end
- `PARTIAL`: some required fragments are supported, some remain uncovered or degraded
- `HEURISTIC`: generation relies on a deliberate fallback witness recipe rather than strong structural handling
- `UNSUPPORTED`: no safe witness strategy exists

Execution policy:
- attempt generation for `SUPPORTED`, `PARTIAL`, `HEURISTIC`
- skip or coarse-fallback for `UNSUPPORTED`

Suggested shape:

```python
class CapabilityLevel(str, Enum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    HEURISTIC = "heuristic"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CapabilityIssue:
    level: CapabilityLevel
    code: str
    message: str
    expression_sql: str
    scope_id: int | None = None


@dataclass(frozen=True)
class CapabilityReport:
    level: CapabilityLevel
    issues: tuple[CapabilityIssue, ...] = ()
```

### 3. Generation Specification Layer
Responsibility:
- serve as the declarative contract between analysis and synthesis

Recommended approach:
- evolve `GenerationSpec` into the final first-class generation contract
- add missing witness features rather than introducing a second top-level spec name

Target fields:

```python
@dataclass
class GenerationSpec:
    column_specs: list[ColumnSpec]
    join_specs: list[JoinSpec]
    group_specs: list[GroupSpec]
    subquery_specs: list[SubquerySpec]
    set_op_specs: list[SetOpSpec]
    order_specs: list[OrderSpec]
    window_specs: list[WindowSpec]
    capability: CapabilityReport
    unresolved: list[WitnessGap]
    provenance: list[WitnessTrace]
```

Recommended interpretation:
- `column_specs`, `join_specs`, etc. remain the backbone
- `order_specs` and `window_specs` are new first-class additions
- `capability` explains global support level
- `unresolved` tracks uncovered fragments that caused partial/heuristic handling
- `provenance` supports debugging and tracing

New semantic definition:
- `GenerationSpec` is no longer just a bag of extracted fragments
- `GenerationSpec` is the declarative contract between speculative analysis and synthesis

### 4. Predicate Semantics Layer
Responsibility:
- answer how a predicate should be interpreted for a declared column type and dialect
- generate and validate candidate values under those semantics

This layer is needed for cases like:
- `TEXT > 30`
- date/datetime literals compared to text
- boolean-like columns
- function predicates
- implicit casts

Recommended responsibilities:
- classify operator/value compatibility
- apply type coercion decisions
- report semantics-related capability issues
- choose candidate value strategies for supported combinations

Suggested shape:

```python
class PredicateSemantics:
    def classify(self, column_spec, operator, value, dialect) -> CapabilityIssue | None:
        ...

    def candidate_strategy(self, column_spec, operator, value, dialect):
        ...

    def compare(self, left, operator, right, column_spec, dialect) -> bool:
        ...
```

### 5. Strategy Layer
Responsibility:
- provide pluggable handling for query features and value synthesis paths

Recommended registries:
- structural witness strategies
  - filters
  - joins
  - groups/having
  - subqueries
  - windows
  - set operations
- expression strategies
  - operators
  - scalar functions
  - aggregate functions
- synthesis strategies
  - scalar candidate generation
  - ordering/ranking witnesses
  - unique batch expansion
  - FK-consistent value alignment

Each strategy should declare:
- `matches(...)`
- `capability(...)`
- `contribute_to_spec(...)` or `synthesize(...)`

Example:

```python
class WindowWitnessStrategy(Protocol):
    def matches(self, scope_fragment) -> bool: ...
    def capability(self, scope_fragment, instance) -> CapabilityReport: ...
    def contribute_to_spec(self, scope_fragment, witness_spec) -> None: ...
```

### 6. Instance Materialization Layer
Responsibility:
- create concrete rows through `Instance`
- enforce PK/FK/uniqueness/nullability rules
- persist or export the result

Keep in `Instance`:
- `schema_spec`
- rows and generated values
- builder/runtime state
- materialization/export/load

Do not move into `Instance`:
- query capability analysis
- query predicate semantics
- planner feature strategies

## Feature Family Roadmap

The redesign should be organized by feature families rather than by syntax node only.

### Family 1: Filters
- comparisons
- `IS NULL`
- `BETWEEN`
- `IN`
- `LIKE`
- boolean combinations

### Family 2: Joins
- inner joins
- left/right joins
- equi-joins first
- non-equi joins later

### Family 3: Aggregation
- `GROUP BY`
- `COUNT`, `SUM`, `MIN`, `MAX`, `AVG`
- `HAVING`

### Family 4: Subqueries
- `IN`
- `EXISTS`
- scalar subqueries
- correlated subqueries

### Family 5: Ordering and Limits
- `ORDER BY`
- `LIMIT`
- `OFFSET`
- top-k/extreme witnesses

### Family 6: Window Functions
- `ROW_NUMBER`
- `RANK`
- `DENSE_RANK`
- partition/order-driven witness generation

### Family 7: Set Operations
- `UNION`
- `UNION ALL`
- `INTERSECT`
- `EXCEPT`

### Family 8: Expression Functions
- string
- numeric
- temporal
- casts/coercions

## Recommended Module Layout

Suggested destination shape:

```text
src/parseval/speculative/
  __init__.py
  generator.py
  capability.py
  capability_rules.py
  witness.py            # optional name; can also remain specs.py if preferred
  semantics.py
  collector.py
  synthesizer.py
  materializer.py
  pool.py               # possibly temporary during migration
  strategies/
    __init__.py
    filters.py
    joins.py
    aggregates.py
    subqueries.py
    windows.py
    setops.py
    functions.py
```

Notes:
- `collector.py` stays as the visitor-driven extraction layer
- `witness.py` becomes the home of the expanded `GenerationSpec`, or the existing spec module can keep that responsibility
- `pool.py` may become obsolete if `Instance`-centric helpers replace the legacy pool-shaped interface

## Recommended Migration Plan

### Phase 1: Formalize the target abstractions
- define `CapabilityLevel`, `CapabilityIssue`, `CapabilityReport`
- define expanded `GenerationSpec` target shape
- document that `GenerationSpec` is the permanent central abstraction

### Phase 2: Capability pass
- add a planner-backed capability visitor
- use strategy registry lookup rather than generator-local checks
- report support reasons before synthesis begins

### Phase 3: Expand `GenerationSpec`
- add `order_specs`
- add `window_specs`
- add `capability`
- add unresolved/provenance metadata

### Phase 4: Predicate semantics service
- centralize type/dialect predicate interpretation
- remove ad hoc coercion/comparison cases from generator-local code

### Phase 5: Structural strategies
- move filter/join/group/subquery handling into explicit strategy modules
- collector becomes registry-driven rather than a long method set

### Phase 6: Window and ordering support
- add first-class witness strategies for `ROW_NUMBER` and order/limit patterns
- classify unsupported shapes explicitly

### Phase 7: Materialization simplification
- reduce pool-shaped compatibility helpers
- prefer `Instance` + `schema_spec` + builder/runtime direct integration where possible

### Phase 8: Stabilize naming and docs
- keep `GenerationSpec` as the public and internal architecture boundary
- update docs/comments/tests so the team consistently treats it as the central declarative spec

## Testing Plan

### Capability tests
Add direct tests for classification:
- simple filter -> `SUPPORTED`
- supported window pattern -> `SUPPORTED` or `HEURISTIC`
- partially handled subquery pattern -> `PARTIAL`
- no strategy available -> `UNSUPPORTED`

### Witness spec tests
Assert spec extraction, not just final rows:
- filter fragments
- join fragments
- group/having fragments
- subquery fragments
- window/order fragments

### Predicate semantics tests
Cases like:
- numeric column vs numeric literal
- text column vs numeric literal
- date vs string literal
- function-based predicates

### End-to-end witness tests
Continue using:
- focused speculative unit tests
- selected Bird-style end-to-end smoke queries

## Open Questions

### 1. Should we rename `GenerationSpec`?
Recommendation:
- no
- keep the existing name and evolve its semantics in place

### 2. Should pool-like helpers survive long term?
Recommendation:
- probably not as a conceptual model
- short term they may remain as implementation helpers
- long term prefer `Instance`-centric access plus semantics strategies

### 3. What is the definition of speculative support?
Recommendation:
- support means: a deliberate witness-generation strategy exists
- not full exact SQL semantic modeling

## Success Criteria
- [ ] `GenerationSpec` is documented as the permanent central declarative spec
- [ ] capability analysis is a first-class pass
- [ ] predicate semantics becomes an explicit module
- [ ] strategy registries handle structural and expression-level support
- [ ] window/order/set-op support has a clear architectural home
- [ ] `Instance` remains storage/materialization rather than semantic reasoning

## Sources
- Local repository code only:
  - `src/parseval/speculative/*`
  - `src/parseval/plan/*`
  - `src/parseval/instance/*`
  - `src/parseval/domain/*`
