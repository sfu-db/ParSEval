# DB Manager URL-Only Implementation Plan

## Objective

Refactor the active database materialization path so it accepts only
`connection_string` plus explicit `dialect`, removing the legacy
`host_or_path` / `database` connection shape from the new path.

This plan applies only to the active modules. Ignore `instance_backup.py`
and other legacy modules that still rely on the old `DBManager` API.

## Scope

In scope:

- `src/parseval/db_manager.py`
- `src/parseval/instance/types.py`
- `src/parseval/instance/loader.py`
- `src/parseval/instance/core.py`
- active tests covering the new loader / materialization path

Out of scope for this phase:

- `instance_backup.py`
- compatibility wrappers for old call sites
- broad cleanup of every historical direct `DBManager` user
- redesign of `Connect.execute(...)`

## Locked Decisions

1. The new connection API is URL-only.
2. Callers must provide both:
   - `connection_string`
   - `dialect`
3. Public dialect vocabulary remains:
   - `sqlite`
   - `mysql`
   - `postgres`
4. `DatabaseTarget.dialect` is authoritative for the destination backend.
5. `DBManager` must not mutate user URLs:
   - no auto-appending `.sqlite`
   - no forced driver rewrites
   - no reverse decomposition into host/path fields

## Research Summary

- `Instance.to_db(...)` in the active code already accepts
  `connection_string`, but `InstanceLoader` reverses that URL back into
  legacy connection fragments purely to satisfy `DBManager`.
- `DBManager` still uses a pre-URL contract and hard-coded URL builders,
  which blocks clean backend extensibility.
- Existing tests for `InstanceLoader` currently assume SQLite URL input and
  `.sqlite` suffixing behavior; those expectations need to be revisited to
  match the new non-mutating URL contract.

## Target Architecture

### Public API

```python
with DBManager().get_connection(
    connection_string="sqlite:////tmp/demo.db",
    dialect="sqlite",
    create_if_missing=True,
) as conn:
    conn.execute("SELECT 1")
```

### Target model

```python
@dataclass(frozen=True)
class DatabaseTarget:
    connection_string: str
    dialect: str
```

### Internal manager flow

1. Parse `connection_string` with SQLAlchemy `make_url`
2. Validate compatibility between parsed backend and explicit `dialect`
3. Select backend provider from registry
4. Optionally ensure database exists
5. Build or reuse engine keyed by normalized URL
6. Return `Connect`

### Backend provider responsibilities

Each provider should own:

- backend recognition / validation
- provisioning behavior (`create_if_missing`)
- engine creation defaults
- backend-specific connect args

Initial providers:

- SQLite
- MySQL
- Postgres

## Implementation Approach

### Patch 1: Update target model and active API entry points

Files:

- `src/parseval/instance/types.py`
- `src/parseval/instance/core.py`

Tasks:

- add `dialect` to `DatabaseTarget`
- update `Instance.to_db(...)` signature to require:
  - `connection_string`
  - `dialect`
- pass the explicit dialect into `DatabaseTarget`

Validation:

- targeted unit tests for `Instance.to_db(...)` call path compile and run

### Patch 2: Refactor `DBManager` to URL-only inputs

File:

- `src/parseval/db_manager.py`

Tasks:

- replace `get_connection(host_or_path, database, ...)` with
  `get_connection(connection_string, dialect, ...)`
- remove:
  - `_SQLITE_URL`
  - `_MYSQL_URL`
  - `_POSTGRES_URL`
  - `_CONNECTION_STR_MAPPING`
  - `_build_url(...)`
- add one parser/validator using `make_url`
- cache engines by normalized URL string or URL object only
- keep `Connect` semantics stable for this phase

Validation:

- focused tests for URL parsing and engine creation
- mismatch between URL backend and explicit dialect raises clearly

### Patch 3: Introduce backend provider registry inside `db_manager.py`

File:

- `src/parseval/db_manager.py`

Tasks:

- add small provider abstraction local to the file first
- implement provider-specific `ensure_database(...)`
- implement provider-specific engine creation defaults
- keep the registry small and internal until the shape stabilizes

Validation:

- SQLite provisioning test
- mocked tests for MySQL/Postgres provisioning dispatch

### Patch 4: Remove URL reverse-parsing from loader

File:

- `src/parseval/instance/loader.py`

Tasks:

- delete `_parse_connection_string(...)`
- pass `target.connection_string` and `target.dialect` directly to `DBManager`
- keep insert generation logic unchanged in this phase

Validation:

- loader tests continue to pass with direct URL flow

### Patch 5: Align tests with non-mutating URL contract

Files:

- `tests/test_instance_loader.py`
- any additional targeted DB manager tests

Tasks:

- stop relying on implicit `.sqlite` suffixing
- use explicit SQLite paths in test URLs
- add tests for:
  - SQLite URL success
  - dialect/backend mismatch failure
  - direct loader path without reverse parsing

Validation:

- run relevant test modules from project root

### Patch 6: Update active direct callers only if required

Potential files:

- active modules that still call `DBManager` directly and are exercised by
  the maintained test path

Tasks:

- update only active callers that block the new API rollout
- do not spend time preserving ignored legacy modules

Validation:

- run affected targeted tests

## Validation Strategy

Primary validation targets:

- `python -m unittest tests.test_instance_loader`
- focused tests added for `DBManager`

Secondary validation targets if needed:

- any maintained tests that exercise `Instance.to_db(...)`

Manual checks:

- verify SQLite URL path is used exactly as passed
- verify backend mismatch errors are explicit and actionable

## Success Criteria

- [ ] `DBManager` accepts URL-only input plus explicit dialect
- [ ] `InstanceLoader` no longer reverse-parses connection strings
- [ ] active materialization path no longer depends on `host_or_path`
- [ ] `DBManager` does not mutate caller-provided URLs
- [ ] backend-specific provisioning is centralized
- [ ] relevant tests pass

## Risks

- There may be active direct `DBManager` callers outside the new instance
  loader path that fail once the API changes.
- Some existing tests currently assume `.sqlite` suffixing and will need
  explicit URL updates.
- SQLAlchemy backend naming (`postgresql`) must be mapped carefully to the
  public dialect vocabulary (`postgres`) without leaking ambiguity.

## Open Questions

1. Should `dialect` remain mandatory when it can often be inferred from the
   URL, or is the explicit argument purely a consistency/validation tool?
   Current plan: keep it mandatory and validate against the URL.
2. Should provisioning support an optional separate admin URL for
   MySQL/Postgres now, or defer until a concrete caller needs it?
   Current plan: defer unless implementation pressure forces it.
