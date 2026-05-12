# Domain Next-Phase Plan

## Scope

This plan covers the next production-hardening phase for the schema-only domain module:

1. composite foreign key generation
2. provider resolution by exact/native type instead of mostly family-only dispatch
3. reducing residual predicate handling from blind retry toward structured compilation

This phase assumes:

- upper-layer ParSEval handles table ordering / topological sequencing
- the current column-level compiler / validator / plan architecture stays in place
- the domain module remains schema-only, not planner-aware

## Goals

- generate child rows for composite foreign keys by reusing parent key tuples as units
- resolve providers using `TypeProfile`, with exact/native-type providers outranking family providers
- reduce reliance on retry-only generation for common check-style predicates

## Non-Goals For This Phase

- topological sort inside `DatabaseBuilder`
- full SQL `CHECK` solving
- arbitrary Python lambda synthesis
- row-group or multi-table global optimization

## Current Gaps

### Composite FK generation

Current runtime only supports scalar FK reuse:

- `SchemaRuntime.referenced_values(...)` only returns values for single-column FK targets
- builder validates composite FKs by failing closed, not by generating them
- FK generation remains column-by-column instead of tuple-by-tuple

### Provider resolution

Current provider resolution is still mostly:

1. column override
2. semantic override
3. family-based builtin provider

That is too coarse for:

- `UUID`
- `ENUM`
- MySQL `TINYINT(1)`
- stricter decimal handling
- future JSON/binary providers

### Residual predicates

Current residual predicate strategy is:

- generate candidate
- validate
- bounded retry

That is acceptable as a fallback but not as the main solution for common constraints.

## Target Architecture

## 1. Composite FK generation model

Introduce the idea of an FK group / FK binding:

```python
@dataclass(frozen=True)
class ForeignKeyBinding:
    spec: ForeignKeySpec
    source_columns: tuple[str, ...]
    target_columns: tuple[str, ...]
```

Generation rule:

- parent composite key values are stored and retrieved as tuples
- child FK columns are generated together from one selected parent tuple
- validation checks tuple membership, not independent column membership

## 2. Provider resolution using `TypeProfile`

Provider matching should evolve toward:

```python
supports(spec, type_profile) -> int
generate(spec, runtime, row_context, domain_plan, type_profile, null_rate) -> Any
```

Resolution priority should become:

1. column override
2. semantic override
3. exact/native-type provider
4. type-family provider
5. fallback generic provider

## 3. Residual predicate reduction

Add more structured constraints instead of relying on opaque callables:

- parity / divisibility
- simple comparisons
- simple membership
- simple string prefix/suffix/contains
- simple length-derived rules

These should compile into `ColumnDomainPlan` where possible.

Opaque callables should remain as fallback residual predicates with bounded retry.

## Deliverables

- composite FK runtime tuple APIs
- composite FK row generation support
- composite FK validation support
- provider registry using `TypeProfile`
- first exact/native-type providers:
  - `UUIDProvider`
  - `EnumProvider` or exact-type enum specialization
  - `BooleanLikeTinyIntProvider`
  - stricter decimal specialization if needed
- structured predicate constraints or compiler support for common residual cases

## Milestone Plan

### Milestone 1: Freeze composite FK behavior with tests

Goal:
- characterize the desired behavior before refactoring runtime/builder

Tasks:

1. Add tests for successful composite FK generation when parent tuples exist.
2. Add tests for explicit preset composite FK acceptance when the tuple exists.
3. Add tests for explicit preset composite FK rejection when the tuple is missing.
4. Add tests for fail-closed behavior when parent tuples do not exist.
5. Add tests for cross-type composite FK tuple coercion if supported.

Definition of done:
- desired tuple-level FK semantics are test-locked

### Milestone 2: Add runtime tuple APIs

Goal:
- expose composite key tuples as first-class runtime objects

Tasks:

1. Add tuple retrieval helpers to `SchemaRuntime`.
2. Add APIs such as:
   - `referenced_key_tuples(fk_spec)`
   - `row_key_tuple(table_name, columns, row)`
3. Add tests for tuple extraction from persisted parent rows.

Definition of done:
- parent FK target tuples can be queried directly from runtime state

### Milestone 3: Composite FK validation

Goal:
- validate FK tuples as grouped values

Tasks:

1. Add tuple-level FK validator logic.
2. Validate composite FK preset values as a unit.
3. Keep scalar FK behavior unchanged.

Definition of done:
- FK validation is tuple-based for composite keys

### Milestone 4: Composite FK generation in builder

Goal:
- generate all columns of a composite FK group together

Tasks:

1. Detect FK groups on a table before per-column generation.
2. Choose one parent tuple for each composite FK binding.
3. Fill all participating child columns from that tuple before non-FK columns.
4. Skip per-column provider generation for those already-bound FK columns.
5. Add regression tests for mixed FK and non-FK tables.

Definition of done:
- composite FK child rows can be generated successfully when parent tuples exist

### Milestone 5: TypeProfile-aware provider resolution

Goal:
- make provider selection depend on `TypeProfile`

Tasks:

1. Change provider base signature to accept `type_profile`.
2. Change registry resolution to call `supports(spec, type_profile)`.
3. Cache/resuse `TypeProfile` where appropriate.
4. Add tests that exact/native-type providers outrank family providers.

Definition of done:
- registry resolves providers with type-profile context

### Milestone 6: Exact/native-type providers

Goal:
- support important exact/native types explicitly

Tasks:

1. Add `UUIDProvider`.
2. Add exact enum specialization or dedicated `EnumProvider`.
3. Add MySQL `TINYINT(1)` boolean-like provider behavior.
4. Add stricter decimal provider behavior if current family provider is too loose.
5. Add tests for each provider and precedence over generic family providers.

Definition of done:
- exact/native-type generation works for high-value special cases

### Milestone 7: Reduce residual-predicate reliance

Goal:
- compile more common predicate patterns into structured plans

Tasks:

1. Introduce new structured constraints where useful, for example:
   - `ModuloConstraint`
   - `PrefixConstraint`
   - `SuffixConstraint`
   - `ContainsConstraint`
2. Add compiler support for them.
3. Update providers to consume the compiled fields.
4. Keep opaque `CheckConstraint(callable)` as fallback only.
5. Add tests comparing structured generation vs retry-only generation.

Definition of done:
- retry remains only for opaque predicates, not common structured ones

## Bite-Size Implementation Tasks

### Task 1

Add composite FK tests:

- successful generation with existing parent tuple
- missing tuple rejection
- preset composite FK validation

Files:

- `tests/test_domain_module.py`
- `tests/domain/test_domain.py`

### Task 2

Add runtime tuple helpers.

Files:

- `src/parseval/domain/state.py`
- new tests in `tests/domain/test_runtime.py`

### Task 3

Add tuple-level FK validation path in builder.

Files:

- `src/parseval/domain/builder.py`
- tests

### Task 4

Generate composite FK columns together in builder.

Files:

- `src/parseval/domain/builder.py`
- tests

### Task 5

Refactor provider base interface to include `type_profile`.

Files:

- `src/parseval/domain/providers/base.py`
- builtin providers
- tests

### Task 6

Refactor provider registry to resolve with `TypeProfile`.

Files:

- `src/parseval/domain/providers/registry.py`
- tests

### Task 7

Add `UUIDProvider`.

Files:

- `src/parseval/domain/providers/uuid.py`
- registry
- tests

### Task 8

Add enum-specific provider path.

Files:

- `src/parseval/domain/providers/enum.py` or string provider specialization
- registry
- tests

### Task 9

Add MySQL `TINYINT(1)` exact/native-type provider behavior.

Files:

- `src/parseval/domain/providers/boolean_like.py` or boolean provider specialization
- registry
- tests

### Task 10

Tighten decimal generation against exact type profile.

Files:

- `src/parseval/domain/providers/numeric.py`
- tests

### Task 11

Introduce first structured residual replacements.

Files:

- `src/parseval/domain/constraints.py`
- `src/parseval/domain/compiler.py`
- tests

### Task 12

Reduce retry use to opaque predicates only.

Files:

- `src/parseval/domain/builder.py`
- validator/compiler tests

## Recommended Execution Order

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

## Success Criteria

This phase is successful when:

- composite FK child rows are generated from real parent tuples
- explicit composite FK preset values are validated as grouped tuples
- provider resolution is driven by `TypeProfile`
- exact/native-type providers outrank family providers
- `UUID`, enum, and boolean-like tinyint generation are explicit and tested
- common residual-style constraints are compiled structurally
- retry remains only for opaque predicates

## Follow-Up After This Phase

- richer dialect-specific native type support
- JSON / binary specialized providers
- multi-column unique constraint generation
- row-level check constraint compilation
