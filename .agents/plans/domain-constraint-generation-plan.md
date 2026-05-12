# Domain Constraint Generation Plan

## Goal

Refactor the schema-only domain module so constraint satisfaction is handled by a compiled constraint/domain-planning layer instead of being scattered across:

- dedicated constraint-specific providers
- datatype providers with ad hoc constraint logic
- builder-level retry loops

The target execution model is:

1. parse schema constraints into `ColumnSpec`
2. compile those constraints into a normalized `ColumnDomainPlan`
3. let providers generate from the normalized plan
4. validate residual predicates after generation
5. fail early on contradictory or unsupported constraint combinations

## Non-Goals For This Phase

- composite foreign key generation
- composite unique key generation
- full SQL `CHECK` expression solving
- table-level / multi-column predicate solving
- planner/query-aware constraint generation

Those should stay separate from the first column-level cleanup.

## Problems In Current Code

- `ChoicesConstraint` is handled by a dedicated provider
- `RangeConstraint` logic lives inside numeric providers
- `CheckConstraint` relies on builder-level retry
- string/temporal constraints are under-specified
- contradictions are not compiled and rejected early
- provider logic is forced to inspect raw `spec.checks`

## Target Architecture

### New internal components

- `parseval.domain.plans`
  - `ColumnDomainPlan`
  - `ConstraintConflict`
- `parseval.domain.compiler`
  - `ConstraintCompiler`
- `parseval.domain.validator`
  - `ConstraintValidator`

### Execution flow

1. `DatabaseBuilder` asks `ConstraintCompiler` for a `ColumnDomainPlan`
2. provider generates from `ColumnDomainPlan`
3. `ConstraintValidator` validates the value
4. retry only for residual predicates that cannot be compiled structurally

### Constraint split

Structured constraints:

- nullability
- choices / enum
- range
- length
- simple pattern
- datatype-derived limits
- uniqueness hints
- default values

Residual constraints:

- callable `CheckConstraint`
- unsupported regex/pattern cases
- future complex expressions

## ColumnDomainPlan Shape

Initial shape:

```python
@dataclass
class ColumnDomainPlan:
    nullable: bool
    unique: bool
    default: Any = None

    allowed_values: tuple[Any, ...] | None = None
    excluded_values: tuple[Any, ...] = ()

    minimum: Any | None = None
    maximum: Any | None = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True

    minimum_length: int | None = None
    maximum_length: int | None = None
    pattern: str | None = None

    residual_predicates: tuple[Callable[[Any], bool], ...] = ()
```

Notes:

- keep this normalized and provider-facing
- do not store raw schema AST here
- keep it column-scoped for this milestone

## Constraint Compilation Rules

### Choices / Enum

- `ChoicesConstraint(values=...)` -> `allowed_values`
- `ENUM(...)` datatype -> `allowed_values`
- intersection of both if both exist
- empty intersection -> `ConstraintConflict`

### Range

- `RangeConstraint` -> `minimum`, `maximum`, inclusiveness
- intersect multiple ranges
- impossible interval -> `ConstraintConflict`

### Length

- datatype length and `LengthConstraint` must be intersected
- impossible intersection -> `ConstraintConflict`

### Pattern

- `PatternConstraint` should initially support only simple deterministic forms:
  - exact literal
  - prefix
  - suffix
  - fixed-width placeholder-like patterns if trivial
- unsupported forms stay as residual predicates or explicit unsupported errors

### CheckConstraint

- callable predicates go to `residual_predicates`
- do not attempt symbolic solving in this phase

### Nullability / Default

- `nullable=False` blocks null generation
- `default` should be compiled into the plan for optional use by providers/builder

## Validator Responsibilities

The validator should:

- validate generated values against the compiled plan
- validate residual predicates
- validate explicit preset values too
- stay separate from FK / uniqueness enforcement already in builder

The validator should not:

- decide how to generate values
- inspect provider internals

## Provider Refactor Goal

Providers should evolve from:

```python
generate(spec, runtime, row_context, null_rate)
```

to:

```python
generate(spec, runtime, row_context, domain_plan, null_rate)
```

Provider expectations:

- no raw constraint parsing when possible
- no direct handling of `ChoicesConstraint`
- no direct handling of `RangeConstraint`
- mostly consume normalized plan fields

## Milestone Plan

### Milestone 1: Freeze behavior with tests

Goal:
- characterize current supported behavior before refactoring internals

Tasks:

1. Add tests for `ChoicesConstraint` uniqueness exhaustion.
2. Add tests for contradictory `RangeConstraint`.
3. Add tests for `LengthConstraint` generation.
4. Add tests for `CheckConstraint` retry exhaustion / failure mode.
5. Add tests for datatype `ENUM` and explicit choices interaction.
6. Add tests for explicit preset value validation against compiled constraints.

Definition of done:
- all tests describe intended column-level behavior clearly

### Milestone 2: Introduce `ColumnDomainPlan`

Goal:
- create a normalized plan object without changing generation behavior yet

Tasks:

1. Create `src/parseval/domain/plans.py`.
2. Add `ColumnDomainPlan`.
3. Add `ConstraintConflict`.
4. Add unit tests for plan object defaults and representation.

Definition of done:
- plan object exists and is test-covered

### Milestone 3: Introduce `ConstraintCompiler`

Goal:
- compile schema constraints into a normalized plan

Tasks:

1. Create `src/parseval/domain/compiler.py`.
2. Implement compilation of:
   - nullability
   - default
   - datatype length
   - `ChoicesConstraint`
   - enum datatype values
3. Add tests for:
   - choices only
   - enum only
   - enum + choices intersection
   - empty choices intersection -> `ConstraintConflict`
4. Implement `RangeConstraint` compilation.
5. Add tests for:
   - inclusive/exclusive bounds
   - intersected ranges
   - contradictory range -> `ConstraintConflict`
6. Implement `LengthConstraint` compilation.
7. Add tests for:
   - datatype length + explicit length
   - contradictory lengths -> `ConstraintConflict`
8. Implement initial `PatternConstraint` compilation strategy.
9. Add tests for:
   - simple supported pattern
   - unsupported pattern fallback or failure
10. Add callable `CheckConstraint` to residual predicates.

Definition of done:
- compiler can build a plan for currently supported constraint types

### Milestone 4: Introduce `ConstraintValidator`

Goal:
- centralize value validation against compiled plans

Tasks:

1. Create `src/parseval/domain/validator.py`.
2. Implement plan validation for:
   - allowed values
   - range
   - length
   - pattern
   - residual predicates
3. Add tests for explicit values and generated values.
4. Replace builder `_check_satisfied(...)` with validator calls.

Definition of done:
- builder no longer owns generic check-predicate logic directly

### Milestone 5: Wire compiler into builder

Goal:
- make builder compile once and pass plans into providers

Tasks:

1. Add plan compilation in `DatabaseBuilder.complete_row`.
2. Cache compiled plans by column where safe.
3. Validate explicit preset values using compiled plans.
4. Validate generated values using compiled plans.
5. Add regression tests for existing row-completion and FK behavior.

Definition of done:
- builder uses compiler + validator for all generated columns

### Milestone 6: Refactor providers to consume plans

Goal:
- remove raw check parsing from providers

Tasks:

1. Update provider interface to accept `domain_plan`.
2. Refactor string provider to use:
   - allowed values
   - length bounds
   - pattern hints
3. Refactor integer provider to use:
   - allowed values
   - range bounds
4. Refactor decimal provider to use:
   - allowed values
   - range bounds
   - scale/precision from type profile
5. Refactor temporal providers to use:
   - allowed values
   - range bounds
6. Remove direct `RangeConstraint` handling from numeric providers.
7. Remove direct `ChoicesConstraint` handling from providers.

Definition of done:
- datatype providers no longer inspect raw `spec.checks` for common structured constraints

### Milestone 7: Remove `ChoiceProvider`

Goal:
- delete the special-case constraint provider once plans are in place

Tasks:

1. Remove `ChoiceProvider` registration from provider registry.
2. Delete or shrink `src/parseval/domain/providers/constraints.py`.
3. Add regression tests proving choices/enum still work through normal providers.

Definition of done:
- choices are handled structurally, not by a special provider

### Milestone 8: Tighten failure behavior

Goal:
- make unsupported or impossible constraints fail clearly

Tasks:

1. Add explicit `ConstraintConflictError` or reuse `ConstraintViolationError`.
2. Fail fast on contradictory compiled plans.
3. Add bounded retry policy only for residual predicates.
4. Add tests for retry exhaustion with custom callable checks.

Definition of done:
- failures are deterministic and explainable

## Bite-Size Implementation Tasks

These are small enough to complete in isolated PRs or single coding sessions.

### Task 1

Add tests for:

- choices uniqueness exhaustion
- contradictory ranges
- length-constrained strings

Files:

- `tests/domain/test_constraints.py`

### Task 2

Create `ColumnDomainPlan` and `ConstraintConflict`.

Files:

- `src/parseval/domain/plans.py`
- new tests in `tests/domain/test_plans.py`

### Task 3

Compile `ChoicesConstraint` and enum values into `allowed_values`.

Files:

- `src/parseval/domain/compiler.py`
- `tests/domain/test_compiler.py`

### Task 4

Compile `RangeConstraint` intersections and detect contradictions.

Files:

- `src/parseval/domain/compiler.py`
- `tests/domain/test_compiler.py`

### Task 5

Compile datatype length + `LengthConstraint`.

Files:

- `src/parseval/domain/compiler.py`
- `tests/domain/test_compiler.py`

### Task 6

Move callable `CheckConstraint` into residual predicates.

Files:

- `src/parseval/domain/compiler.py`
- `tests/domain/test_compiler.py`

### Task 7

Create `ConstraintValidator` and validate:

- allowed values
- range
- length
- residual predicates

Files:

- `src/parseval/domain/validator.py`
- `tests/domain/test_validator.py`

### Task 8

Replace builder `_check_satisfied(...)` with validator use.

Files:

- `src/parseval/domain/builder.py`
- `tests/domain/test_constraints.py`
- `tests/test_domain_module.py`

### Task 9

Change provider interface to accept `domain_plan`.

Files:

- `src/parseval/domain/providers/base.py`
- all builtin providers
- `tests/test_domain_module.py`

### Task 10

Refactor integer/real providers to consume compiled range/choices.

Files:

- `src/parseval/domain/providers/numeric.py`
- `tests/domain/test_constraints.py`

### Task 11

Refactor string provider to consume compiled choices/length/pattern.

Files:

- `src/parseval/domain/providers/string.py`
- `tests/domain/test_constraints.py`

### Task 12

Refactor temporal providers to consume compiled range/choices.

Files:

- `src/parseval/domain/providers/temporal.py`
- `tests/domain/test_constraints.py`

### Task 13

Remove `ChoiceProvider` and prove no regression.

Files:

- `src/parseval/domain/providers/registry.py`
- `src/parseval/domain/providers/constraints.py`
- related tests

### Task 14

Introduce explicit failure for unsupported pattern forms.

Files:

- `src/parseval/domain/compiler.py`
- `src/parseval/domain/validator.py`
- tests

## Recommended Execution Order

Recommended sequence:

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

## Success Criteria

The refactor is successful when:

- no builtin provider needs to parse raw `ChoicesConstraint`
- no builtin provider needs to parse raw `RangeConstraint`
- builder does not rely on generic retry for structured constraints
- contradictions fail before generation
- explicit values and generated values go through the same validator
- enum/choices/range/length/pattern are handled by compiled plans
- residual callable checks use bounded retry only as fallback

## Follow-Up Milestones

After this plan is complete:

1. row-level and table-level constraints
2. composite unique constraints
3. composite foreign keys
4. richer sqlglot `CHECK` compilation
5. dialect-native constraint extraction from DDL
