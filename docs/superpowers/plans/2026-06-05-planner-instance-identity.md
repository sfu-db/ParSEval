# Planner Instance Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace semantic table/column string identity in the planner and instance with immutable `IdentifierName`, `RelationId`, and `ColumnId` keys while keeping sqlglot expressions as syntax.

**Architecture:** Add a small identity module first, then thread identities through catalog/domain metadata, planner annotations, instance rows/symbols, evaluator environments, and solver adapters. Do not preserve old string-keyed semantic APIs for consumers or legacy tests; update or remove tests that assert the old identity behavior.

**Execution Constraint:** Backward compatibility is out of scope. Workers must not add compatibility shims solely to keep old consumers or legacy tests working. String rendering is allowed only for display, SQL export, generated solver names, and explicit debug helpers.

**Tech Stack:** Python 3.9+, `sqlglot`, `pytest`, `unittest`

---

## File Map

- Create: `src/parseval/identity.py`
  - Own immutable identity primitives, metadata constants, and constructors from sqlglot nodes.

- Create: `tests/test_identity.py`
  - Unit tests for normalization, quoted names, relation IDs, column IDs, source links, and sqlglot AST mutation safety.

- Modify: `src/parseval/domain/spec.py`
  - Add identity fields to `ForeignKeySpec`, `ColumnSpec`, and `TableSpec`.
  - Replace semantic string matching with identity fields; keep display string properties only where current domain providers need labels.

- Modify: `src/parseval/instance/core.py`
  - Build physical table and column identities during DDL ingestion.
  - Expose `table_id()`, `column_id()`, and `catalog_column()` helpers.
  - Stamp type and identity metadata when enriching planner columns.

- Create: `tests/instance/test_catalog_identity.py`
  - Verify DDL-derived physical identities, quoted names, PK/FK identity links, and `ColumnSpec` identity fields.

- Modify: `src/parseval/plan/planner.py`
  - Add query-scope relation/column identities to `Scan`, `Join`, `SubPlan`, and `StepAnnotations`.
  - Add scoped resolution for physical scans, aliases, CTEs, subquery outputs, bare columns, and ambiguity.

- Create: `tests/plan/test_identity_resolution.py`
  - Verify self-join, quoted identifier, ambiguity, CTE output, and subquery output resolution.

- Modify: `src/parseval/plan/context.py`
  - Move `Row.columns` toward `dict[ColumnId, Symbol]`.
  - Resolve `exp.Column` lookups through `PARSEVAL_COLUMN_ID`.

- Modify: `src/parseval/plan/rex.py`
  - Change `Environment` to bind by `ColumnId`.
  - Make unresolved `exp.Column` fail closed in the migrated path.
  - Keep concrete-stamped column evaluation unchanged.

- Modify: `src/parseval/instance/symbols.py`
  - Index variables by `ColumnId` and `(RelationId, rowid)`.

- Modify: `src/parseval/plan/rex.py`
  - Add `relation_id` and `column_id` slots to `Variable`.

- Create: `tests/instance/test_row_identity.py`
  - Verify row lookup, variable back-pointers, and symbol reverse indices use identities.

- Modify: `src/parseval/solver/types.py`
  - Add helpers to read resolved `ColumnId` metadata from `exp.Column`.

- Modify: `src/parseval/solver/smt_translate.py`
  - Use identity-derived SMT variable names instead of raw `table.column` when metadata is present.

- Create: `tests/solver/test_identity_keys.py`
  - Verify aliases and self-join columns do not collapse in solver variable naming.

- Modify: `docs/superpowers/specs/2026-06-05-planner-instance-identity-design.md`
  - Update only if implementation discovers a necessary design correction.

## Task 1: Add Immutable Identity Primitives

**Files:**
- Create: `src/parseval/identity.py`
- Create: `tests/test_identity.py`

- [ ] **Step 1: Write failing identity tests**

Create `tests/test_identity.py`:

```python
from sqlglot import exp, parse_one

from parseval.identity import (
    PARSEVAL_COLUMN_ID,
    ColumnId,
    ColumnKind,
    IdentifierName,
    RelationId,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)


def test_identifier_name_normalizes_unquoted_names():
    node = exp.Identifier(this="User", quoted=False)
    ident = identifier_name(node, dialect="sqlite")
    assert ident.raw == "User"
    assert ident.normalized == "user"
    assert ident.quoted is False
    assert ident.dialect == "sqlite"


def test_identifier_name_preserves_quoted_names():
    node = exp.Identifier(this="User", quoted=True)
    ident = identifier_name(node, dialect="sqlite")
    assert ident.raw == "User"
    assert ident.normalized == "User"
    assert ident.quoted is True


def test_relation_id_distinguishes_self_join_aliases():
    table = identifier_name("users")
    rel_a = relation_id(RelationKind.TABLE, table, alias=identifier_name("a"), scope_id="s0")
    rel_b = relation_id(RelationKind.TABLE, table, alias=identifier_name("b"), scope_id="s0")
    assert rel_a != rel_b
    assert rel_a.name == rel_b.name


def test_column_id_links_query_column_to_physical_source():
    physical_table = relation_id(RelationKind.TABLE, identifier_name("users"))
    physical = column_id(ColumnKind.PHYSICAL, identifier_name("id"), physical_table)
    alias_table = relation_id(
        RelationKind.TABLE,
        identifier_name("users"),
        alias=identifier_name("u"),
        scope_id="s0",
    )
    scoped = column_id(
        ColumnKind.PHYSICAL,
        identifier_name("id"),
        alias_table,
        scope_id="s0",
        source_column_id=physical,
    )
    assert scoped != physical
    assert scoped.source_column_id == physical


def test_column_id_is_safe_when_sqlglot_ast_mutates():
    expr = parse_one("SELECT u.id FROM users AS u")
    col = next(expr.find_all(exp.Column))
    rel = relation_id(RelationKind.TABLE, identifier_name("users"), alias=identifier_name("u"), scope_id="s0")
    cid = column_id(ColumnKind.PHYSICAL, identifier_name(col.this), rel, scope_id="s0")
    lookup = {cid: "value"}
    col.set("this", exp.Identifier(this="name"))
    assert lookup[cid] == "value"


def test_parseval_column_id_constant_is_stable():
    assert PARSEVAL_COLUMN_ID == "parseval_column_id"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
pytest tests/test_identity.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'parseval.identity'
```

- [ ] **Step 3: Add `src/parseval/identity.py`**

Create `src/parseval/identity.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlglot import exp


PARSEVAL_COLUMN_ID = "parseval_column_id"


@dataclass(frozen=True)
class IdentifierName:
    raw: str
    normalized: str
    quoted: bool
    dialect: str | None = None

    @property
    def display(self) -> str:
        return self.raw


class RelationKind(Enum):
    TABLE = "table"
    CTE = "cte"
    SUBQUERY = "subquery"
    VALUES = "values"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class RelationId:
    kind: RelationKind
    name: IdentifierName | None
    catalog: IdentifierName | None = None
    db: IdentifierName | None = None
    alias: IdentifierName | None = None
    scope_id: str | None = None

    @property
    def display(self) -> str:
        visible = self.alias or self.name
        return visible.display if visible is not None else self.kind.value


class ColumnKind(Enum):
    PHYSICAL = "physical"
    PROJECTED = "projected"
    DERIVED = "derived"
    AGGREGATE = "aggregate"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class ColumnId:
    kind: ColumnKind
    name: IdentifierName
    relation: RelationId | None
    scope_id: str | None = None
    ordinal: int | None = None
    source_column_id: "ColumnId | None" = None

    @property
    def display(self) -> str:
        if self.relation is None:
            return self.name.display
        return f"{self.relation.display}.{self.name.display}"


@dataclass(frozen=True)
class ColumnRef:
    ast: exp.Column
    name: IdentifierName
    qualifier: IdentifierName | None
    scope_id: str
    resolved: ColumnId | None = None


@dataclass(frozen=True)
class CatalogColumn:
    id: ColumnId
    datatype: exp.DataType
    nullable: bool
    unique: bool
    primary_key: bool


def identifier_name(value: exp.Identifier | str, dialect: str | None = None) -> IdentifierName:
    if isinstance(value, exp.Identifier):
        raw = value.name
        quoted = value.quoted
    else:
        raw = str(value)
        quoted = False
    normalized = raw if quoted else raw.lower()
    return IdentifierName(raw=raw, normalized=normalized, quoted=quoted, dialect=dialect)


def identifier_key(value: exp.Identifier | str, dialect: str | None = None) -> str:
    return identifier_name(value, dialect=dialect).normalized


def relation_id(
    kind: RelationKind,
    name: IdentifierName | None,
    *,
    catalog: IdentifierName | None = None,
    db: IdentifierName | None = None,
    alias: IdentifierName | None = None,
    scope_id: str | None = None,
) -> RelationId:
    return RelationId(kind=kind, name=name, catalog=catalog, db=db, alias=alias, scope_id=scope_id)


def column_id(
    kind: ColumnKind,
    name: IdentifierName,
    relation: RelationId | None,
    *,
    scope_id: str | None = None,
    ordinal: int | None = None,
    source_column_id: ColumnId | None = None,
) -> ColumnId:
    return ColumnId(
        kind=kind,
        name=name,
        relation=relation,
        scope_id=scope_id,
        ordinal=ordinal,
        source_column_id=source_column_id,
    )


def column_identity(node: exp.Column) -> ColumnId | None:
    value: Any = node.meta.get(PARSEVAL_COLUMN_ID)
    return value if isinstance(value, ColumnId) else None
```

- [ ] **Step 4: Run identity tests**

Run:

```bash
pytest tests/test_identity.py -q
```

Expected:

```text
6 passed
```

- [ ] **Step 5: Commit**

```bash
git add src/parseval/identity.py tests/test_identity.py
git commit -m "feat: add immutable SQL identity primitives"
```

## Task 2: Build Physical Catalog Identities from DDL

**Files:**
- Modify: `src/parseval/domain/spec.py`
- Modify: `src/parseval/instance/core.py`
- Create: `tests/instance/test_catalog_identity.py`

- [ ] **Step 1: Write failing catalog identity tests**

Create `tests/instance/test_catalog_identity.py`:

```python
from parseval.identity import ColumnId, ColumnKind, RelationId, RelationKind
from parseval.instance import Instance


def test_instance_builds_physical_table_and_column_ids():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);", name="db", dialect="sqlite")
    users_id = inst.table_id("users")
    id_col = inst.column_id("users", "id")
    assert isinstance(users_id, RelationId)
    assert users_id.kind is RelationKind.TABLE
    assert users_id.name.normalized == "users"
    assert isinstance(id_col, ColumnId)
    assert id_col.kind is ColumnKind.PHYSICAL
    assert id_col.relation == users_id
    assert id_col.name.normalized == "id"


def test_catalog_column_preserves_type_and_constraints():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT NOT NULL);", name="db", dialect="sqlite")
    id_info = inst.catalog_column("users", "id")
    name_info = inst.catalog_column("users", "name")
    assert id_info.primary_key is True
    assert id_info.nullable is False
    assert name_info.nullable is False
    assert name_info.datatype.sql().upper() == "TEXT"


def test_schema_spec_carries_identity_fields():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);", name="db", dialect="sqlite")
    table = inst.schema_spec.get_table("users")
    column = table.get_column("id")
    assert table.id == inst.table_id("users")
    assert column.id == inst.column_id("users", "id")
    assert column.table_id == table.id


def test_foreign_key_spec_carries_column_ids():
    ddl = '''
    CREATE TABLE users (id INT PRIMARY KEY);
    CREATE TABLE orders (
        id INT PRIMARY KEY,
        user_id INT REFERENCES users(id)
    );
    '''
    inst = Instance(ddl, name="db", dialect="sqlite")
    orders = inst.schema_spec.get_table("orders")
    fk = orders.foreign_keys[0]
    assert fk.source_table_id == inst.table_id("orders")
    assert fk.target_table_id == inst.table_id("users")
    assert fk.source_column_ids == (inst.column_id("orders", "user_id"),)
    assert fk.target_column_ids == (inst.column_id("users", "id"),)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
pytest tests/instance/test_catalog_identity.py -q
```

Expected:

```text
AttributeError: 'Instance' object has no attribute 'table_id'
```

- [ ] **Step 3: Add identity fields to domain specs**

Modify `src/parseval/domain/spec.py`.

Add imports:

```python
from parseval.identity import ColumnId, RelationId
```

Change the dataclasses so identities are first-class fields. Keep existing string fields only as display labels used by current domain providers, not as semantic lookup keys:

```python
@dataclass(frozen=True)
class ForeignKeySpec:
    source_table: str
    source_columns: Tuple[str, ...]
    target_table: str
    target_columns: Tuple[str, ...]
    source_table_id: Optional[RelationId] = None
    source_column_ids: Tuple[ColumnId, ...] = ()
    target_table_id: Optional[RelationId] = None
    target_column_ids: Tuple[ColumnId, ...] = ()
```

```python
@dataclass(frozen=True)
class ColumnSpec:
    table: str
    column: str
    datatype: DataType
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False
    foreign_key: Optional[ForeignKeySpec] = None
    default: Any = None
    native_type: Optional[str] = None
    dialect: Optional[str] = None
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    semantic_tags: Tuple[str, ...] = ()
    checks: Tuple[Any, ...] = ()
    id: Optional[ColumnId] = None
    table_id: Optional[RelationId] = None
```

```python
@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: Tuple[ColumnSpec, ...]
    primary_key: Tuple[str, ...] = ()
    unique_constraints: Tuple[Tuple[str, ...], ...] = ()
    foreign_keys: Tuple[ForeignKeySpec, ...] = ()
    id: Optional[RelationId] = None
    primary_key_ids: Tuple[ColumnId, ...] = ()
    unique_constraint_ids: Tuple[Tuple[ColumnId, ...], ...] = ()
```

- [ ] **Step 4: Add catalog identity indexes to `Catalog`**

Modify `src/parseval/instance/core.py`.

Add imports:

```python
from parseval.identity import (
    CatalogColumn,
    ColumnKind,
    IdentifierName,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
```

Initialize indexes in `Catalog.__init__` before `super().__init__`:

```python
self._relation_ids: Dict[str, Any] = {}
self._column_ids: Dict[Tuple[str, str], Any] = {}
self._catalog_columns: Dict[Any, CatalogColumn] = {}
```

Add methods on `Catalog`:

```python
def _identity_key(self, value: exp.Identifier | str) -> str:
    node = value if isinstance(value, exp.Identifier) else exp.Identifier(this=str(value))
    return identifier_name(node, dialect=self.dialect).normalized

def _remember_table_identity(self, table_name: str) -> None:
    key = self._normalize_name(table_name, self.dialect, self.normalize)
    if key not in self._relation_ids:
        self._relation_ids[key] = relation_id(
            RelationKind.TABLE,
            identifier_name(table_name, dialect=self.dialect),
        )

def _remember_column_identity(self, table_name: str, column_name: str, datatype_sql: str) -> None:
    table_key = self._normalize_name(table_name, self.dialect, self.normalize)
    column_key = self._normalize_name(column_name, self.dialect, self.normalize)
    self._remember_table_identity(table_name)
    rel_id = self._relation_ids[table_key]
    col_id = column_id(
        ColumnKind.PHYSICAL,
        identifier_name(column_name, dialect=self.dialect),
        rel_id,
    )
    self._column_ids[(table_key, column_key)] = col_id
    datatype = self._datatype_node_for(column_name, datatype_sql)
    self._catalog_columns[col_id] = CatalogColumn(
        id=col_id,
        datatype=datatype,
        nullable=self.nullable(table_key, column_key),
        unique=self.is_unique(table_key, column_key),
        primary_key=any(pk.name == column_key for pk in self.get_primary_key(table_key)),
    )

def _rebuild_identity_indexes(self) -> None:
    self._relation_ids.clear()
    self._column_ids.clear()
    self._catalog_columns.clear()
    for table_name, columns in self.tables.items():
        self._remember_table_identity(table_name)
        for column_name, datatype_sql in columns.items():
            self._remember_column_identity(table_name, column_name, datatype_sql)

def table_id(self, table: exp.Table | str):
    table_name = table.name if isinstance(table, exp.Table) else table
    key = self._normalize_name(table_name, self.dialect, self.normalize)
    return self._relation_ids[key]

def column_id(self, table: exp.Table | str, column: exp.Column | str):
    table_name = table.name if isinstance(table, exp.Table) else table
    column_name = column.name if isinstance(column, exp.Column) else column
    table_key = self._normalize_name(table_name, self.dialect, self.normalize)
    column_key = self._normalize_name(column_name, self.dialect, self.normalize)
    return self._column_ids[(table_key, column_key)]

def catalog_column(self, table: exp.Table | str, column: exp.Column | str) -> CatalogColumn:
    return self._catalog_columns[self.column_id(table, column)]
```

Call `_rebuild_identity_indexes()` at the end of `_ingest_ddls()` after all tables, primary keys, foreign keys, and constraints have been added:

```python
self._rebuild_identity_indexes()
```

- [ ] **Step 5: Thread identities into `to_schema_spec()`**

In `Catalog.to_schema_spec()`, when building FK and column specs, pass identity fields:

```python
source_column_ids = tuple(self.column_id(table_name, col) for col in source_columns)
target_table_id = self.table_id(target_table.name)
target_column_ids = tuple(self.column_id(target_table.name, col) for col in target_columns)
fk_spec = ForeignKeySpec(
    source_table=table_name,
    source_columns=source_columns,
    target_table=target_table.name,
    target_columns=target_columns,
    source_table_id=self.table_id(table_name),
    source_column_ids=source_column_ids,
    target_table_id=target_table_id,
    target_column_ids=target_column_ids,
)
```

When constructing `ColumnSpec`, add:

```python
id=self.column_id(table_name, column_name),
table_id=self.table_id(table_name),
```

When constructing `TableSpec`, add:

```python
id=self.table_id(table_name),
primary_key_ids=tuple(self.column_id(table_name, column) for column in sorted(pk_columns)),
```

- [ ] **Step 6: Run catalog identity tests**

Run:

```bash
pytest tests/instance/test_catalog_identity.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 7: Run existing domain and instance slices**

Run:

```bash
pytest tests/domain/test_plans.py tests/instance/test_instance.py tests/instance/test_symbol_index.py -q
```

Expected:

```text
passed
```

- [ ] **Step 8: Commit**

```bash
git add src/parseval/domain/spec.py src/parseval/instance/core.py tests/instance/test_catalog_identity.py
git commit -m "feat: derive catalog identities from DDL"
```

## Task 3: Resolve Planner Relations and Columns to Identities

**Files:**
- Modify: `src/parseval/plan/planner.py`
- Create: `tests/plan/test_identity_resolution.py`

- [ ] **Step 1: Write failing planner identity tests**

Create `tests/plan/test_identity_resolution.py`:

```python
import pytest
import sqlglot

from parseval.identity import PARSEVAL_COLUMN_ID, ColumnId, RelationId
from parseval.instance import Instance
from parseval.plan import Join, Plan, Project


def _plan(sql: str, ddl: str):
    instance = Instance(ddl, name="db", dialect="sqlite")
    return Plan(sqlglot.parse_one(sql, read="sqlite"), instance=instance)


def test_self_join_columns_resolve_to_distinct_query_scope_ids():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY, parent_id INT);"
    plan = _plan("SELECT a.id, b.id FROM users AS a JOIN users AS b ON a.id = b.parent_id", ddl)
    project = plan.root
    ann = plan.annotation_for(project)
    projected = ann.projected_columns
    assert len(projected) == 2
    assert projected[0] != projected[1]
    assert projected[0].source_column_id == projected[1].source_column_id
    assert projected[0].relation != projected[1].relation


def test_column_ast_is_stamped_with_resolved_identity():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
    plan = _plan("SELECT u.id FROM users AS u", ddl)
    col = next(plan.root.projections[0].find_all(sqlglot.exp.Column))
    plan.annotation_for(plan.root)
    assert isinstance(col.meta[PARSEVAL_COLUMN_ID], ColumnId)
    assert col.meta[PARSEVAL_COLUMN_ID].relation.alias.normalized == "u"


def test_bare_ambiguous_column_fails_during_annotation():
    ddl = "CREATE TABLE users (id INT); CREATE TABLE orders (id INT, user_id INT);"
    plan = _plan("SELECT id FROM users JOIN orders ON users.id = orders.user_id", ddl)
    with pytest.raises(ValueError, match="Ambiguous column"):
        plan.annotation_for(plan.root)


def test_cte_output_resolves_to_cte_column_not_physical_column():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
    plan = _plan("WITH x AS (SELECT id FROM users) SELECT id FROM x", ddl)
    ann = plan.annotation_for(plan.root)
    assert ann.projected_columns[0].relation.kind.value == "cte"
    assert ann.projected_columns[0].source_column_id.name.normalized == "id"


def test_subquery_output_resolves_to_subquery_column():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
    plan = _plan("SELECT dt.id FROM (SELECT id FROM users) AS dt", ddl)
    ann = plan.annotation_for(plan.root)
    assert ann.projected_columns[0].relation.kind.value == "subquery"
    assert ann.projected_columns[0].relation.alias.normalized == "dt"
```

- [ ] **Step 2: Run planner identity tests to verify they fail**

Run:

```bash
pytest tests/plan/test_identity_resolution.py -q
```

Expected:

```text
AttributeError: 'StepAnnotations' object has no attribute 'projected_columns'
```

or failures showing projected columns are still strings.

- [ ] **Step 3: Add planner scope helpers**

Modify `src/parseval/plan/planner.py`.

Add imports:

```python
from parseval.identity import (
    PARSEVAL_COLUMN_ID,
    ColumnId,
    ColumnKind,
    ColumnRef,
    IdentifierName,
    RelationId,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
```

Add a small scope dataclass near `StepAnnotations`:

```python
@dataclass
class ScopeFrame:
    scope_id: str
    parent_id: str | None = None
    relations: t.Dict[IdentifierName, RelationId] = field(default_factory=dict)
    output_columns: t.Dict[IdentifierName, ColumnId] = field(default_factory=dict)
```

Add helper functions:

```python
def _scope_id_for(step: "Step") -> str:
    return f"s{id(step)}"

def _relation_lookup_key(name: str, dialect: str | None = None) -> IdentifierName:
    return identifier_name(name, dialect=dialect)

def _query_relation_for_scan(scan: "Scan", instance: t.Any, scope_id: str) -> RelationId:
    source = scan.source
    if isinstance(source, exp.Table):
        physical = instance.table_id(source)
        alias_name = source.alias_or_name
        alias_ident = identifier_name(alias_name, dialect=getattr(instance, "dialect", None)) if alias_name else None
        return relation_id(
            RelationKind.TABLE,
            physical.name,
            catalog=physical.catalog,
            db=physical.db,
            alias=alias_ident if alias_ident and alias_ident.normalized != physical.name.normalized else None,
            scope_id=scope_id,
        )
    if isinstance(source, exp.Subquery):
        return relation_id(
            RelationKind.SUBQUERY,
            None,
            alias=identifier_name(source.alias_or_name, dialect=getattr(instance, "dialect", None)),
            scope_id=scope_id,
        )
    return relation_id(RelationKind.SYNTHETIC, None, scope_id=scope_id)
```

- [ ] **Step 4: Add column resolution helpers**

Add these helpers in `planner.py`:

```python
def _visible_relations(step: "Step") -> t.Tuple[RelationId, ...]:
    relations: t.List[RelationId] = []
    for dep in step.chain_dependencies:
        rel = getattr(dep, "relation_id", None)
        if rel is not None:
            relations.append(rel)
        for nested in _visible_relations(dep):
            if nested not in relations:
                relations.append(nested)
    rel = getattr(step, "relation_id", None)
    if rel is not None and rel not in relations:
        relations.append(rel)
    return tuple(relations)

def _physical_source_for_relation(relation: RelationId, instance: t.Any) -> RelationId | None:
    if relation.kind is not RelationKind.TABLE or relation.name is None:
        return None
    try:
        return instance.table_id(relation.name.normalized)
    except Exception:
        return None

def _resolve_column_id(col: exp.Column, step: "Step", instance: t.Any, scope_id: str) -> ColumnId:
    dialect = getattr(instance, "dialect", None)
    col_name = identifier_name(col.this, dialect=dialect)
    visible = _visible_relations(step)
    if col.table:
        qualifier = identifier_name(col.table, dialect=dialect)
        matches = [
            rel for rel in visible
            if (rel.alias and rel.alias.normalized == qualifier.normalized)
            or (rel.name and rel.name.normalized == qualifier.normalized)
        ]
        if not matches:
            raise ValueError(f"Unresolved column qualifier: {col.sql()}")
        rel = matches[0]
    else:
        matches = []
        for rel in visible:
            source = _physical_source_for_relation(rel, instance)
            if source is None:
                for out_col in getattr(step, "output_columns", ()):
                    if out_col.name.normalized == col_name.normalized:
                        matches.append(rel)
                continue
            try:
                instance.column_id(source.name.normalized, col_name.normalized)
                matches.append(rel)
            except Exception:
                pass
        if len(matches) > 1:
            raise ValueError(f"Ambiguous column: {col.sql()}")
        if not matches:
            raise ValueError(f"Unresolved column: {col.sql()}")
        rel = matches[0]
    source_column = None
    source_rel = _physical_source_for_relation(rel, instance)
    if source_rel is not None:
        try:
            source_column = instance.column_id(source_rel.name.normalized, col_name.normalized)
        except Exception:
            source_column = None
    return column_id(
        ColumnKind.PHYSICAL if source_column is not None else ColumnKind.PROJECTED,
        col_name,
        rel,
        scope_id=scope_id,
        source_column_id=source_column,
    )
```

- [ ] **Step 5: Attach relation IDs during annotation**

In `Plan._annotate()`, before enriching expressions, assign relation IDs to scan steps:

```python
if self._instance is not None:
    for step in self.ordered_steps:
        if isinstance(step, Scan) and getattr(step, "relation_id", None) is None:
            step.relation_id = _query_relation_for_scan(step, self._instance, _scope_id_for(step))
```

Inside the existing `for expr in exprs` loop, replace `_enrich_one_column(...)` with:

```python
resolved_id = _resolve_column_id(col, step, self._instance, _scope_id_for(step))
col.meta[PARSEVAL_COLUMN_ID] = resolved_id
if resolved_id.source_column_id is not None:
    source_rel = resolved_id.source_column_id.relation
    meta = {
        "table": source_rel.name.normalized if source_rel and source_rel.name else "",
        "nullable": self._instance.catalog_column(source_rel.name.normalized, resolved_id.name.normalized).nullable,
        "unique": self._instance.catalog_column(source_rel.name.normalized, resolved_id.name.normalized).unique,
        "domain": DataType.build(self._instance.catalog_column(source_rel.name.normalized, resolved_id.name.normalized).datatype),
    }
    set_column_meta(col, meta)
```

Keep `_enrich_one_column()` in place for unmigrated call sites, but do not use it for columns resolved through identity metadata.

- [ ] **Step 6: Change `StepAnnotations` fields**

Replace the dataclass fields in `StepAnnotations`:

```python
referenced_columns: t.Tuple[ColumnId, ...] = ()
projected_columns: t.Tuple[ColumnId, ...] = ()
source_relations: t.Tuple[RelationId, ...] = ()
```

When building `StepAnnotations`, use:

```python
referenced_columns=_unique_column_ids(exprs),
projected_columns=_projected_column_ids(step, self._instance, _scope_id_for(step)) if self._instance is not None else (),
source_relations=_source_relations(step),
```

Add helper implementations:

```python
def _unique_column_ids(expressions: t.Iterable[exp.Expression]) -> t.Tuple[ColumnId, ...]:
    seen: t.Set[ColumnId] = set()
    columns: t.List[ColumnId] = []
    for expression in expressions:
        if expression is None:
            continue
        for column in expression.find_all(exp.Column):
            cid = column.meta.get(PARSEVAL_COLUMN_ID)
            if isinstance(cid, ColumnId) and cid not in seen:
                seen.add(cid)
                columns.append(cid)
    return tuple(columns)

def _projected_column_ids(step: "Step", instance: t.Any, scope_id: str) -> t.Tuple[ColumnId, ...]:
    projections = getattr(step, "projections", None) or []
    result: t.List[ColumnId] = []
    for ordinal, projection in enumerate(projections):
        alias_name = projection.alias_or_name
        if not alias_name:
            continue
        name = identifier_name(alias_name, dialect=getattr(instance, "dialect", None))
        source = None
        if isinstance(projection, exp.Column):
            source = projection.meta.get(PARSEVAL_COLUMN_ID)
        relation = getattr(step, "relation_id", None)
        if relation is None:
            relation = relation_id(RelationKind.SYNTHETIC, None, scope_id=scope_id)
        kind = ColumnKind.PHYSICAL if isinstance(source, ColumnId) and source.source_column_id else ColumnKind.PROJECTED
        result.append(column_id(kind, name, relation, scope_id=scope_id, ordinal=ordinal, source_column_id=source.source_column_id if isinstance(source, ColumnId) else None))
    return tuple(result)

def _source_relations(step: "Step") -> t.Tuple[RelationId, ...]:
    relations: t.List[RelationId] = []
    for rel in _visible_relations(step):
        if rel not in relations:
            relations.append(rel)
    return tuple(relations)
```

- [ ] **Step 7: Run planner identity tests**

Run:

```bash
pytest tests/plan/test_identity_resolution.py -q
```

Expected:

```text
5 passed
```

- [ ] **Step 8: Run existing planner tests**

Run:

```bash
pytest tests/plan/test_annotations.py tests/plan/test_planner_tree_shape.py tests/plan/test_subplan_shape.py -q
```

Expected:

```text
passed
```

If existing tests still assert `source_tables` or string `projected_columns`, update those assertions to `source_relations` and `ColumnId.name.normalized`.

- [ ] **Step 9: Commit**

```bash
git add src/parseval/plan/planner.py tests/plan/test_identity_resolution.py tests/plan/test_annotations.py
git commit -m "feat: resolve planner columns to identity keys"
```

## Task 4: Store Row Cells and Variables by Column Identity

**Files:**
- Modify: `src/parseval/plan/context.py`
- Modify: `src/parseval/plan/rex.py`
- Modify: `src/parseval/instance/core.py`
- Modify: `src/parseval/instance/symbols.py`
- Create: `tests/instance/test_row_identity.py`

- [ ] **Step 1: Write failing row and symbol identity tests**

Create `tests/instance/test_row_identity.py`:

```python
import sqlglot

from parseval.identity import PARSEVAL_COLUMN_ID
from parseval.instance import Instance
from parseval.plan import Plan


def test_create_row_stores_cells_by_column_id():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);", name="db", dialect="sqlite")
    result = inst.create_row("users", {"id": 1, "name": "Ada"})
    row = result.created["users"][0]
    id_col = inst.column_id("users", "id")
    assert row[id_col].concrete == 1
    assert row["id"].concrete == 1


def test_exp_column_lookup_uses_resolved_column_id():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    row = inst.create_row("users", {"id": 7}).created["users"][0]
    col = sqlglot.parse_one("SELECT id FROM users").expressions[0]
    col.meta[PARSEVAL_COLUMN_ID] = inst.column_id("users", "id")
    assert row[col].concrete == 7


def test_variable_carries_relation_and_column_ids():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    row = inst.create_row("users", {"id": 1}).created["users"][0]
    var = row[inst.column_id("users", "id")]
    assert var.args["relation_id"] == inst.table_id("users")
    assert var.args["column_id"] == inst.column_id("users", "id")


def test_symbol_index_lookup_by_identity():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    inst.create_row("users", {"id": 1})
    cells = inst.symbols.by_column(inst.column_id("users", "id"))
    assert len(cells) == 1
    assert cells[0].concrete == 1
```

- [ ] **Step 2: Run row identity tests to verify they fail**

Run:

```bash
pytest tests/instance/test_row_identity.py -q
```

Expected:

```text
FAIL
```

The first failure should show row lookup or variable metadata still uses strings.

- [ ] **Step 3: Add identity slots to `Variable`**

In `src/parseval/plan/rex.py`, extend `Variable.arg_types`:

```python
"relation_id": False,
"column_id": False,
```

- [ ] **Step 4: Update `Row.__getitem__` to prefer `ColumnId` metadata**

Modify `src/parseval/plan/context.py`.

Add imports:

```python
from parseval.identity import PARSEVAL_COLUMN_ID, ColumnId
```

At the start of `Row.__getitem__`, add:

```python
columns = self.args.get("columns", {})
if isinstance(key, ColumnId):
    if key in columns:
        return columns[key]
    if key.source_column_id is not None and key.source_column_id in columns:
        return columns[key.source_column_id]
if isinstance(key, exp.Column):
    resolved = key.meta.get(PARSEVAL_COLUMN_ID)
    if isinstance(resolved, ColumnId):
        if resolved in columns:
            return columns[resolved]
        if resolved.source_column_id is not None and resolved.source_column_id in columns:
            return columns[resolved.source_column_id]
```

Remove fallback from `exp.Column` to `column.name`; `exp.Column` lookup must use resolved identity metadata. Keep explicit `row["id"]` string lookup only as a debug/display convenience.

- [ ] **Step 5: Update `Instance.place_row()` and `_create_row()`**

In `src/parseval/instance/core.py`, when constructing `row_cells`, use physical column IDs:

```python
col_id = self.column_id(table_name, column)
z_value = Variable(
    this=z_name,
    _type=datatype,
    concrete=concrete,
    table=table_name,
    column=column,
    relation_id=self.table_id(table_name),
    column_id=col_id,
    rowid=rowid,
)
row_cells[col_id] = z_value
```

Apply the same change in both `place_row()` and `_create_row()`.

- [ ] **Step 6: Update `SymbolIndex` reverse keys**

Modify `src/parseval/instance/symbols.py`.

Import identity types:

```python
from parseval.identity import ColumnId, RelationId
```

Change reverse index annotations:

```python
self._by_column: Dict[Any, List[Variable]] = defaultdict(list)
self._by_row: Dict[Tuple[Any, Any], List[Variable]] = defaultdict(list)
```

In `register()`, prefer identity fields:

```python
relation_id = variable.args.get("relation_id") or variable.args.get("table")
column_id = variable.args.get("column_id") or variable.args.get("column")
rowid = variable.args.get("rowid")
if relation_id and column_id:
    self._by_column[column_id].append(variable)
if relation_id and rowid is not None:
    self._by_row[(relation_id, rowid)].append(variable)
```

Update `_remove_from_reverse_indices()` with the same key derivation.

Change lookup methods:

```python
def by_column(self, column_id_or_table, column: str | None = None) -> List[Variable]:
    key = column_id_or_table if column is None else (column_id_or_table, column)
    return list(self._by_column.get(key, ()))

def by_row(self, relation_id_or_table, rowid: Any) -> List[Variable]:
    return list(self._by_row.get((relation_id_or_table, rowid), ()))
```

Do not add tuple fallback for old callers. Update old tests that call `by_column("table", "column")` to use `by_column(instance.column_id(...))`.

- [ ] **Step 7: Run row identity and symbol tests**

Run:

```bash
pytest tests/instance/test_row_identity.py tests/instance/test_symbol_index.py -q
```

Expected:

```text
passed
```

- [ ] **Step 8: Run instance tests**

Run:

```bash
pytest tests/instance/test_instance.py tests/test_instance.py tests/test_instance_snapshot.py -q
```

Expected:

```text
passed
```

- [ ] **Step 9: Commit**

```bash
git add src/parseval/plan/context.py src/parseval/plan/rex.py src/parseval/instance/core.py src/parseval/instance/symbols.py tests/instance/test_row_identity.py tests/instance/test_symbol_index.py
git commit -m "feat: store instance rows by column identity"
```

## Task 5: Bind Evaluator Environments by Column Identity

**Files:**
- Modify: `src/parseval/plan/rex.py`
- Create: `tests/plan/test_environment_identity.py`

- [ ] **Step 1: Write failing environment tests**

Create `tests/plan/test_environment_identity.py`:

```python
import pytest
from sqlglot import exp

from parseval.identity import PARSEVAL_COLUMN_ID, ColumnKind, identifier_name, column_id
from parseval.plan.rex import Environment, concrete


def test_environment_resolves_column_by_column_id():
    cid = column_id(ColumnKind.PROJECTED, identifier_name("answer"), None, scope_id="s0")
    col = exp.column("answer")
    col.meta[PARSEVAL_COLUMN_ID] = cid
    env = Environment({cid: 42})
    assert concrete(col, env) == 42


def test_unresolved_column_fails_closed():
    col = exp.column("answer")
    env = Environment({})
    with pytest.raises(KeyError, match="Unresolved column"):
        concrete(col, env)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/plan/test_environment_identity.py -q
```

Expected:

```text
FAIL
```

The unresolved test should currently return `None`.

- [ ] **Step 3: Update `Environment` to prefer `ColumnId` keys**

Modify `src/parseval/plan/rex.py`.

Add imports:

```python
from parseval.identity import PARSEVAL_COLUMN_ID, ColumnId
```

Update `_column_key()`:

```python
@staticmethod
def _column_key(column: Union[exp.Column, str, ColumnId]) -> Union[str, ColumnId]:
    if isinstance(column, ColumnId):
        return column
    if isinstance(column, exp.Column):
        resolved = column.meta.get(PARSEVAL_COLUMN_ID)
        if isinstance(resolved, ColumnId):
            return resolved
        if column.table:
            return f"{normalize_name(column.table)}.{normalize_name(column.name)}"
        return normalize_name(column.name)
    return normalize_name(str(column))
```

Update `resolve()`:

```python
def resolve(self, column: Union[exp.Column, str, ColumnId]) -> Any:
    key = self._column_key(column)
    if key in self._bindings:
        return self._bindings[key]
    if isinstance(column, exp.Column) and isinstance(key, ColumnId):
        source = key.source_column_id
        if source is not None and source in self._bindings:
            return self._bindings[source]
    if self._outer is not None:
        return self._outer.resolve(column)
    if isinstance(column, exp.Column):
        raise KeyError(f"Unresolved column: {column.sql()}")
    return None
```

Update `bind()` and `contains()` to use `_column_key()`.

- [ ] **Step 4: Keep stamped concrete values working**

Leave `_eval_column()` concrete-stamped branch unchanged:

```python
if "concrete" in node.args:
    stamped = node.args["concrete"]
    if isinstance(stamped, Symbol):
        return stamped.concrete
    return stamped
```

This is not semantic identity; it is an existing row-by-row evaluation shortcut.

- [ ] **Step 5: Run evaluator identity tests**

Run:

```bash
pytest tests/plan/test_environment_identity.py -q
```

Expected:

```text
2 passed
```

- [ ] **Step 6: Run symbolic evaluator tests**

Run:

```bash
pytest tests/symbolic/test_symbolic_engine.py tests/symbolic/test_subplan_eval.py tests/symbolic/test_distinct_eval.py -q
```

Expected:

```text
passed
```

If a symbolic test intentionally evaluates unresolved ad-hoc columns without planner metadata, update that test setup to bind `ColumnId` metadata explicitly instead of depending on bare strings.

- [ ] **Step 7: Commit**

```bash
git add src/parseval/plan/rex.py tests/plan/test_environment_identity.py tests/symbolic
git commit -m "feat: bind expression environments by column identity"
```

## Task 6: Use Column Identity in Solver Variable Naming

**Files:**
- Modify: `src/parseval/solver/types.py`
- Modify: `src/parseval/solver/smt_translate.py`
- Create: `tests/solver/test_identity_keys.py`

- [ ] **Step 1: Write failing solver identity tests**

Create `tests/solver/test_identity_keys.py`:

```python
from sqlglot import exp

from parseval.dtype import DataType
from parseval.identity import PARSEVAL_COLUMN_ID, ColumnKind, RelationKind, column_id, identifier_name, relation_id
from parseval.solver.smt_translate import declare_column


def _scoped_col(alias: str, name: str):
    rel = relation_id(
        RelationKind.TABLE,
        identifier_name("users"),
        alias=identifier_name(alias),
        scope_id="s0",
    )
    cid = column_id(ColumnKind.PHYSICAL, identifier_name(name), rel, scope_id="s0")
    col = exp.column(name, table=alias)
    col.type = DataType.build("INT")
    col.meta[PARSEVAL_COLUMN_ID] = cid
    return col


def test_smt_column_declaration_uses_column_identity_display():
    left = declare_column(_scoped_col("a", "id")).expr
    right = declare_column(_scoped_col("b", "id")).expr
    assert str(left) != str(right)
    assert "a.id" in str(left)
    assert "b.id" in str(right)
```

- [ ] **Step 2: Run the test to verify it fails or exposes current naming**

Run:

```bash
pytest tests/solver/test_identity_keys.py -q
```

Expected:

```text
FAIL
```

The failure should show raw `variable.table.variable.name` naming or no identity helper.

- [ ] **Step 3: Add a solver column-key helper**

In `src/parseval/solver/types.py`, add:

```python
from parseval.identity import PARSEVAL_COLUMN_ID, ColumnId


def column_key(column: exp.Column) -> str:
    cid = column.meta.get(PARSEVAL_COLUMN_ID)
    if isinstance(cid, ColumnId):
        return cid.display
    if column.table:
        return f"{column.table}.{column.name}"
    return column.name
```

- [ ] **Step 4: Use the helper in SMT declaration**

In `src/parseval/solver/smt_translate.py`, import and use `column_key`:

```python
from .types import column_key
```

Replace:

```python
var_name = f"{variable.table}.{variable.name}"
```

with:

```python
var_name = column_key(variable)
```

- [ ] **Step 5: Run solver identity key tests**

Run:

```bash
pytest tests/solver/test_identity_keys.py -q
```

Expected:

```text
1 passed
```

- [ ] **Step 6: Run focused solver tests**

Run:

```bash
pytest tests/solver/test_solver.py tests/solver/test_smt.py tests/solver/test_domain.py -q
```

Expected:

```text
passed
```

- [ ] **Step 7: Commit**

```bash
git add src/parseval/solver/types.py src/parseval/solver/smt_translate.py tests/solver/test_identity_keys.py
git commit -m "feat: derive solver variable names from column identity"
```

## Task 7: Remove Semantic String Fallback from Migrated Paths

**Files:**
- Modify: `src/parseval/plan/context.py`
- Modify: `src/parseval/plan/rex.py`
- Modify: `src/parseval/instance/symbols.py`
- Modify: affected tests from earlier tasks

- [ ] **Step 1: Write regression tests for forbidden fallback**

Append to `tests/instance/test_row_identity.py`:

```python
import pytest


def test_row_rejects_unresolved_exp_column_when_identity_cells_exist():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    row = inst.create_row("users", {"id": 1}).created["users"][0]
    unresolved = sqlglot.exp.column("id")
    with pytest.raises(KeyError):
        row[unresolved]
```

Append to `tests/plan/test_environment_identity.py`:

```python
def test_environment_does_not_resolve_bare_string_for_expression_column():
    col = exp.column("answer")
    env = Environment({"answer": 42})
    with pytest.raises(KeyError, match="Unresolved column"):
        concrete(col, env)
```

- [ ] **Step 2: Run fallback tests to verify they fail**

Run:

```bash
pytest tests/instance/test_row_identity.py::test_row_rejects_unresolved_exp_column_when_identity_cells_exist tests/plan/test_environment_identity.py::test_environment_does_not_resolve_bare_string_for_expression_column -q
```

Expected:

```text
FAIL
```

- [ ] **Step 3: Tighten `Row.__getitem__` for `exp.Column`**

In `src/parseval/plan/context.py`, after checking `exp.Column` metadata, raise instead of falling through to `column.name`:

```python
if isinstance(key, exp.Column):
    resolved = key.meta.get(PARSEVAL_COLUMN_ID)
    if isinstance(resolved, ColumnId):
        if resolved in columns:
            return columns[resolved]
        if resolved.source_column_id is not None and resolved.source_column_id in columns:
            return columns[resolved.source_column_id]
    raise KeyError(key)
```

Keep string lookup for explicit `row["id"]` display/debug callers:

```python
if isinstance(key, str):
    normalized = normalize_name(key)
    for column_name, value in columns.items():
        if normalize_name(self._key_name(column_name)) == normalized:
            return value
```

- [ ] **Step 4: Tighten `Environment.resolve()` for `exp.Column`**

In `src/parseval/plan/rex.py`, ensure unresolved `exp.Column` never falls back to a bare string key:

```python
if isinstance(column, exp.Column):
    resolved = column.meta.get(PARSEVAL_COLUMN_ID)
    if not isinstance(resolved, ColumnId):
        if self._outer is not None:
            return self._outer.resolve(column)
        raise KeyError(f"Unresolved column: {column.sql()}")
```

Then use `resolved` for lookups.

- [ ] **Step 5: Tighten `SymbolIndex.by_column()`**

In `src/parseval/instance/symbols.py`, make `by_column()` accept only `ColumnId`:

```python
def by_column(self, column_id: ColumnId) -> List[Variable]:
    return list(self._by_column.get(column_id, ()))
```

Variable registration should already prefer `column_id`.

- [ ] **Step 6: Run fallback regressions**

Run:

```bash
pytest tests/instance/test_row_identity.py tests/plan/test_environment_identity.py -q
```

Expected:

```text
passed
```

- [ ] **Step 7: Run broad focused suites**

Run:

```bash
pytest tests/plan tests/instance tests/solver tests/symbolic -q
```

Expected:

```text
passed
```

- [ ] **Step 8: Commit**

```bash
git add src/parseval/plan/context.py src/parseval/plan/rex.py src/parseval/instance/symbols.py tests/instance/test_row_identity.py tests/plan/test_environment_identity.py
git commit -m "refactor: remove semantic string fallback from identity paths"
```

## Task 8: Documentation and Final Verification

**Files:**
- Modify: `docs/superpowers/specs/2026-06-05-planner-instance-identity-design.md`
- Modify: `docs/instance.md` if docs are tracked in the execution worktree
- Modify: `docs/solver.md` if docs are tracked in the execution worktree

- [ ] **Step 1: Update the identity design spec with implementation notes**

In `docs/superpowers/specs/2026-06-05-planner-instance-identity-design.md`, add a short section near the end:

```markdown
## Implementation Notes

- `src/parseval/identity.py` owns immutable identity objects.
- Planner annotations stamp `PARSEVAL_COLUMN_ID` onto `exp.Column.meta`.
- Instance rows store cells by physical `ColumnId`.
- Query-scope `ColumnId` values link to physical columns through `source_column_id`.
- Evaluator and solver adapters read identity metadata from sqlglot expressions.
```

- [ ] **Step 2: Run the full test suite**

Run:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

Expected:

```text
OK
```

If INFO logs print during the run, ignore them unless the final result is not `OK`.

- [ ] **Step 3: Run pytest focused identity suite**

Run:

```bash
pytest tests/test_identity.py tests/plan/test_identity_resolution.py tests/instance/test_catalog_identity.py tests/instance/test_row_identity.py tests/plan/test_environment_identity.py tests/solver/test_identity_keys.py -q
```

Expected:

```text
passed
```

- [ ] **Step 4: Check remaining string semantic identity usage**

Run:

```bash
rg -n "normalize_name\\(|\\.table\\)|\\.name\\)|by_column\\(\"|by_row\\(\" src/parseval/plan src/parseval/instance src/parseval/solver
```

Expected:

```text
```

The command may still show display, parsing, or compatibility code. Inspect each hit. Any hit that uses a string as semantic table/column identity in migrated planner/instance/evaluator/solver paths must be converted to `RelationId` or `ColumnId` before finishing.

- [ ] **Step 5: Commit docs and final cleanup**

```bash
git add docs/superpowers/specs/2026-06-05-planner-instance-identity-design.md docs/instance.md docs/solver.md
git commit -m "docs: describe planner instance identity implementation"
```

If `docs/instance.md` or `docs/solver.md` are ignored or absent in the execution worktree, commit only the spec update:

```bash
git add -f docs/superpowers/specs/2026-06-05-planner-instance-identity-design.md
git commit -m "docs: describe planner instance identity implementation"
```

## Self-Review Checklist

- The plan covers identity primitives, planner resolution, catalog/domain, instance storage, evaluator lookup, solver naming, final fallback removal, and docs.
- Each task writes failing tests before implementation.
- Each task has exact file paths and verification commands.
- The plan keeps sqlglot expressions as syntax and uses `ColumnId` / `RelationId` for durable identity.
- The migration avoids changing solver algorithms except where variable names and adapter keys read identity metadata.
