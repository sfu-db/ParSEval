# Instance Module Refactor Plan

## Scope

This plan covers the cleanup and production-hardening of [`src/parseval/instance.py`](/home/chunyu/workspaces/projects/ParSEval/src/parseval/instance.py).

The target outcome is:

1. `Instance` remains the in-memory representation of generated rows against a schema
2. instance export is explicit and deterministic
3. backend loading is delegated to clear writer / loader components
4. value coercion and SQL rendering are backend-aware instead of ad hoc
5. the module is testable without requiring large end-to-end speculative generation

## Goals

- keep a clean in-memory instance state separate from persistence concerns
- support deterministic export of generated data as readable SQL/datalog-like output
- support correct insertion into SQLite, MySQL, and Postgres through one backend-neutral pipeline
- centralize backend-specific value coercion, identifier quoting, and statement generation
- remove duplicate insertion logic between `sync_db` and `to_db`
- make the API usable by upper layers that may want either:
  - in-memory rows only
  - exported fixture/log output
  - actual database materialization

## Non-Goals For This Phase

- replacing the current query/planner pipeline
- redesigning the domain fake-data generator itself
- topological planning of table creation beyond the current schema order assumptions
- full migration of every legacy call site in one patch
- introducing async database IO

## Problems In Current Code

### 1. `Instance` has too many responsibilities

`Instance` currently handles all of the following:

- DDL parsing and catalog building
- constraint metadata storage
- row creation and FK bootstrapping
- uniqueness / null deduplication
- symbol bookkeeping for planner use
- backend connection parameter handling
- SQL statement assembly
- direct row insertion
- export of inserted SQL strings

That makes the class hard to reason about and hard to test in isolation.

### 2. Persistence logic is duplicated and inconsistent

`sync_db` and `to_db` both:

- build insert column lists
- map row values into parameter dicts
- serialize concrete values
- call `DBManager.insert(...)`

This duplication increases drift risk.

### 3. Backend behavior is only partially abstracted

Current behavior still leaks backend details into `Instance`:

- SQLite filename suffixing is embedded in the class
- SQL strings are hand-built in-place
- identifier quoting is simplistic
- value serialization is only partially typed
- there is no explicit backend writer contract

### 4. Export and load are coupled

`to_db(..., return_inserted=True)` mixes two jobs:

- materialize into a real database
- build a human-readable SQL log

Those should be separate capabilities built from the same normalized row payload.

### 5. Row cleanup mutates core state just before persistence

`_dedupe_primary_key_rows`, `_dedupe_unique_rows`, and `_dedupe_null_rows` mutate stored instance data right before writing.

Risks:

- export behavior depends on side effects
- debugging becomes harder because persisted rows are not necessarily the rows originally added
- cleanup policies are hidden instead of explicit

### 6. The API surface is not crisp

Examples:

- `create_row(...)` returns `{"rows": ..., "positions": ...}` instead of a typed result
- `_create_row(...)` does core business work but is private
- `Instance` stores DB connection attributes only when used operationally, but those are not part of a clean target config object

## Target Architecture

### Core principle

Split the current module into three layers:

1. in-memory instance state
2. instance export / normalization
3. backend materialization

### Proposed modules

- `src/parseval/instance.py`
  - keep `Instance`
  - keep or slim `Catalog` only if still needed here
- `src/parseval/instance_types.py`
  - typed result/config dataclasses
- `src/parseval/instance_export.py`
  - export rows into canonical records and readable SQL logs
- `src/parseval/instance_loader.py`
  - persist canonical records into target backends through `DBManager`
- `src/parseval/instance_dialects.py`
  - backend-specific quoting / serialization / insert statement helpers

If file count should stay smaller, `instance_export.py` and `instance_loader.py` can be merged into one `instance_io.py`, but the responsibilities should still stay separated internally.

## Proposed Domain Model

### `Instance`

Responsibilities:

- hold schema metadata and generated rows
- expose row/query helpers
- create rows in memory
- expose a normalized snapshot for downstream export/load

Should not:

- build SQL strings directly
- decide filesystem/database naming conventions
- open backend connections directly

### `InstanceSnapshot`

```python
@dataclass(frozen=True)
class InstanceSnapshot:
    schema_ddl: str
    dialect: str
    tables: tuple["TableBatch", ...]
```

### `TableBatch`

```python
@dataclass(frozen=True)
class TableBatch:
    table_name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, object], ...]
```

This becomes the canonical export/load payload.

### `MaterializationTarget`

```python
@dataclass(frozen=True)
class MaterializationTarget:
    host_or_path: str
    database: str
    dialect: str
    port: int | None = None
    username: str | None = None
    password: str | None = None
```

This removes scattered connection parameters from `Instance`.

### `WriteResult`

```python
@dataclass(frozen=True)
class WriteResult:
    inserted_tables: tuple[str, ...]
    inserted_rows: int
    statements: tuple[str, ...] = ()
```

This replaces `return_inserted=True` as the only way to obtain SQL output.

## Export / Load Pipeline

### Step 1. Snapshot

`Instance.snapshot(...)` should:

- normalize row order deterministically
- optionally apply cleanup policy
- emit backend-neutral row dictionaries

### Step 2. Backend adaptation

A backend adapter should transform snapshot rows into:

- properly coerced driver values for inserts
- properly quoted SQL literals for export
- properly quoted identifiers for table/column names

### Step 3. Materialization

An `InstanceLoader` should:

- prepare database target
- create tables from DDL
- insert rows table by table
- return a `WriteResult`

### Step 4. Export

An `InstanceExporter` should:

- render readable SQL fixture output from the same snapshot
- optionally emit a more explicit datalog-style text format if needed later

## Backend Abstraction Design

Introduce a small dialect adapter interface:

```python
class InstanceDialectAdapter(Protocol):
    def normalize_database_name(self, database: str) -> str: ...
    def quote_identifier(self, name: str) -> str: ...
    def serialize_driver_value(self, value: Any) -> Any: ...
    def render_literal(self, value: Any) -> str: ...
    def build_insert_statement(self, table: str, columns: Sequence[str]) -> str: ...
```

Notes:

- `serialize_driver_value(...)` is for real inserts
- `render_literal(...)` is for exported SQL text
- these must not be conflated

## Cleanup Policy Design

Current implicit mutation should become explicit:

```python
@dataclass(frozen=True)
class SnapshotPolicy:
    dedupe_primary_keys: bool = True
    dedupe_unique_columns: bool = True
    drop_invalid_null_rows: bool = True
```

Then:

- `Instance.snapshot(policy=...)` applies cleanup into a derived snapshot
- in-memory rows remain unchanged unless explicitly compacted

This is a cleaner production model and easier to test.

## Row Creation API Cleanup

Current `create_row(...)` return value should be replaced over time with a typed result:

```python
@dataclass(frozen=True)
class RowCreationResult:
    created: dict[str, tuple[Row, ...]]
    positions: dict[str, int]
```

This preserves existing semantics while removing ad hoc dict conventions.

## Relationship To The Domain Module

The instance refactor should not re-implement fake data generation rules.

Instead:

- `Instance` owns row/state management
- the domain module owns type-aware value generation
- instance export/load owns persistence and fixture rendering

This keeps the previous domain cleanup direction intact.

## TDD Strategy

Refactor this module in thin vertical slices. Do not start by moving large amounts of code without behavior locks.

### Test layers

1. snapshot/unit tests
2. exporter tests
3. loader tests with SQLite
4. backend adapter tests for quoting/serialization
5. compatibility tests for existing public `Instance.to_db(...)`

## Milestone Plan

### Milestone 1: Freeze current externally required behavior

Goal:
- define the behavior that existing callers actually depend on

Tasks:

1. Add focused tests for `create_row`, `create_rows`, and `to_db`.
2. Add tests for dedupe behavior before persistence.
3. Add tests for `return_inserted=True` current output shape.
4. Add tests for datetime/date/time serialization.
5. Add tests for SQLite filename normalization.

Definition of done:
- current required write behavior is characterized by tests

### Milestone 2: Introduce snapshot types

Goal:
- create a stable intermediate representation without changing behavior

Tasks:

1. Add `InstanceSnapshot`, `TableBatch`, `SnapshotPolicy`, and `WriteResult`.
2. Add `Instance.snapshot(...)`.
3. Move dedupe/null filtering logic under snapshot generation first, leaving legacy methods as wrappers if necessary.
4. Add tests for deterministic snapshot output.

Definition of done:
- instance state can be exported into a typed, backend-neutral snapshot

### Milestone 3: Extract dialect adapter layer

Goal:
- remove SQL string/value-formatting logic from `Instance`

Tasks:

1. Add adapter interface and default implementations for:
   - SQLite
   - MySQL
   - Postgres
2. Move `_serialize_concrete` logic into adapters.
3. Add tests for:
   - identifier quoting
   - literal rendering
   - database name normalization
   - date/time/datetime handling

Definition of done:
- backend-specific formatting logic is centralized and test-covered

### Milestone 4: Extract loader

Goal:
- replace direct `to_db` insertion logic with a dedicated writer

Tasks:

1. Add `InstanceLoader.load(snapshot, target, truncate_first=True)`.
2. Reuse one insert-building path for both batch insert and single-row sync.
3. Make `Instance.to_db(...)` a compatibility wrapper around loader + snapshot.
4. Add SQLite integration tests for:
   - create tables
   - insert rows
   - verify persisted contents

Definition of done:
- persistence is handled outside `Instance`, with behavior preserved

### Milestone 5: Extract exporter

Goal:
- separate SQL fixture rendering from actual DB writes

Tasks:

1. Add `InstanceExporter.render_sql(snapshot)`.
2. Make `return_inserted=True` delegate to exporter output during compatibility period.
3. Add tests for readable deterministic SQL output.
4. Optionally add `render_datalog(snapshot)` if the project wants a non-SQL fixture format.

Definition of done:
- export and materialization are separate features using the same snapshot

### Milestone 6: Clean public API and remove dead paths

Goal:
- simplify the `Instance` surface after extraction

Tasks:

1. Replace ad hoc return dicts with typed result objects where safe.
2. Remove duplicate SQL assembly code from `Instance`.
3. Remove backend connection attributes from `Instance` if no longer necessary.
4. Delete legacy helpers that are only kept alive by extracted services.
5. Add regression tests for `main.py`, `disprover.py`, and generator call paths that still use `Instance.to_db(...)`.

Definition of done:
- `Instance` is mostly an in-memory state object, not a transport god-class

## Bite-Size Implementation Tasks

The implementation target is now the package directory `src/parseval/instance/`.

### Package Slice 0

Bootstrap the package and make it the owner of the instance public surface.

Files:

- `src/parseval/instance/__init__.py`
- `src/parseval/instance/core.py`

### Package Slice 1

Add typed package-local models for snapshot, write results, and database targets.

Files:

- `src/parseval/instance/types.py`
- `tests/test_instance_snapshot.py`

### Package Slice 2

Implement `Instance.snapshot(...)` and move cleanup to snapshot generation.

Files:

- `src/parseval/instance/core.py`
- `tests/test_instance_snapshot.py`

### Package Slice 3

Extract dialect rendering and value serialization into package-local adapters.

Files:

- `src/parseval/instance/dialects.py`
- `tests/test_instance_loader.py`

### Package Slice 4

Extract loader/exporter and make `Instance.to_db(...)` delegate through them.

Files:

- `src/parseval/instance/loader.py`
- `src/parseval/instance/exporter.py`
- `src/parseval/instance/core.py`
- `tests/test_instance_loader.py`

### Task 1

Lock current persistence behavior with tests.

Files:

- `tests/test_instance_io.py`
- `tests/test_instance.py`

### Task 2

Add `InstanceSnapshot`, `TableBatch`, `SnapshotPolicy`, `WriteResult`.

Files:

- `src/parseval/instance_types.py`
- `tests/test_instance_snapshot.py`

### Task 3

Implement `Instance.snapshot(...)` using current row data and cleanup rules.

Files:

- `src/parseval/instance.py`
- `tests/test_instance_snapshot.py`

### Task 4

Add dialect adapters for quoting and value serialization.

Files:

- `src/parseval/instance_dialects.py`
- `tests/test_instance_dialects.py`

### Task 5

Extract `InstanceLoader` and make `to_db(...)` delegate to it.

Files:

- `src/parseval/instance_loader.py`
- `src/parseval/instance.py`
- `tests/test_instance_loader.py`

### Task 6

Extract `InstanceExporter.render_sql(...)`.

Files:

- `src/parseval/instance_export.py`
- `tests/test_instance_export.py`

### Task 7

Replace `sync_db(...)` internals to reuse loader code path or remove it if not needed.

Files:

- `src/parseval/instance.py`
- `src/parseval/instance_loader.py`
- `tests/test_instance_loader.py`

### Task 8

Add compatibility coverage for upstream callers.

Files:

- `tests/test_generator.py`
- `tests/test_speculate.py`
- `tests/test_main.py`

## Recommended Implementation Order

1. Freeze current behavior with tests
2. Introduce snapshot dataclasses
3. Move cleanup into snapshot generation
4. Extract dialect adapters
5. Extract loader
6. Extract exporter
7. Shrink `Instance`
8. Remove dead code

## Design Notes / Tradeoffs

### Why snapshot-first?

Because it gives one stable payload for both:

- human-readable export
- actual backend insertion

Without that, the code will keep duplicating transformation steps.

### Why keep `Instance.to_db(...)` for now?

Because many existing call sites already depend on it. A compatibility wrapper reduces migration risk while still allowing internal cleanup.

### Why not merge this directly into `DBManager`?

Because `DBManager` should remain a connection/SQL execution utility.
It should not become responsible for:

- instance cleanup policy
- row serialization policy
- fixture rendering
- schema-bound batch export semantics

Those belong closer to instance materialization.
