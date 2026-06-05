# Planner and Instance Identity Design

Date: 2026-06-05

## Objective

Replace string-based planner and instance identity with a small immutable
identity model that preserves SQL naming semantics across parsing, planning,
catalog lookup, row storage, evaluation, and solver handoff.

The current code frequently stores table and column names as strings. That is
too lossy for quoted identifiers, database/schema qualifiers, aliases,
self-joins, CTEs, subquery outputs, and correlated scopes. `sqlglot` already
parses these names into AST nodes, but those nodes are mutable syntax objects,
not stable semantic keys. ParSEval should keep sqlglot nodes as source syntax
and use its own resolved identity objects as durable keys.

Compatibility with current string-keyed APIs is not a design constraint.

## Boundary

In scope:

- Planner scan/join/subplan identity.
- Planner scope resolution for table aliases and column references.
- Catalog and schema identity derived from DDL.
- Instance row, variable, and symbol index identity.
- Evaluator and solver adapter lookup keys.

Out of scope:

- Changing sqlglot parsing behavior.
- Replacing sqlglot expressions in predicates, projections, or constraints.
- Reworking solver algorithms beyond changing assignment and lookup keys.
- Preserving old public string-keyed APIs.

## Core Rule

Use sqlglot nodes for syntax. Use ParSEval identity objects for meaning.

Examples:

- `exp.Identifier` records parsed spelling and quoting.
- `exp.Table` records parsed relation syntax.
- `exp.Column` records parsed column syntax.
- `IdentifierName`, `RelationId`, and `ColumnId` record resolved identity.

No long-lived catalog, row, environment, solver, or symbol-index dictionary
should use mutable sqlglot AST nodes as keys.

## Identity Model

Add a focused module: `src/parseval/identity.py`.

```python
@dataclass(frozen=True)
class IdentifierName:
    raw: str
    normalized: str
    quoted: bool
    dialect: str | None = None
```

`IdentifierName` is one SQL name part. It is built from `exp.Identifier` when
possible. `raw` preserves spelling. `normalized` is the comparison key under
the active dialect rules. For quoted identifiers, normalization should preserve
case unless the dialect demands otherwise.

```python
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
```

`RelationId` identifies one row source in one scope. Physical catalog tables
have no alias and no query scope. Query row sources have `scope_id` and may
have `alias`. A self-join therefore has two distinct relation IDs even when
both point at the same physical table.

```python
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
```

`ColumnId` is the durable key for a column available in a planner scope or an
instance row. Physical schema columns use `ColumnKind.PHYSICAL`. SELECT-list
outputs, aggregates, and generated helper columns use the other kinds. When a
query-scope column is derived directly from a physical column, `source_column_id`
links back to that physical catalog column.

```python
@dataclass(frozen=True)
class ColumnRef:
    ast: exp.Column
    name: IdentifierName
    qualifier: IdentifierName | None
    scope_id: str
    resolved: ColumnId | None = None
```

`ColumnRef` represents a parsed column occurrence before or during resolution.
It keeps the original AST node for traceability and rendering, but resolved
runtime behavior uses `resolved`.

## Scope Model

Planner owns semantic resolution.

```python
@dataclass
class ScopeFrame:
    scope_id: str
    parent_id: str | None
    relations: dict[IdentifierName, RelationId]
    output_columns: dict[IdentifierName, ColumnId]
```

Resolution rules:

1. Qualified columns resolve the qualifier through `relations`.
2. Bare columns search visible relation columns in the current scope.
3. Exactly one local match resolves to that `ColumnId`.
4. Multiple local matches are ambiguous and should raise or record a hard
   planning error.
5. No local match checks parent scopes for correlation.
6. No match after parent lookup is unresolved and should raise or record a hard
   planning error.

The planner may still keep human-readable `name` fields for debugging, but
step semantics should not depend on them.

## Planner Changes

`Scan` should carry the relation identity it introduces:

```python
class Scan(Step):
    relation_id: RelationId
    source_relation_id: RelationId | None
```

For physical tables, `relation_id` is the query-scope row source, and
`source_relation_id` points at the physical catalog table when an alias is used.
For CTEs and subqueries, `relation_id` identifies the scoped row source and
`source_relation_id` can be `None`.

`Join` should key joined inputs by `RelationId`, not strings:

```python
class JoinInfo:
    side: str | None
    source_key: tuple[exp.Expression, ...]
    join_key: tuple[exp.Expression, ...]
    condition: exp.Expression | None


class Join(Step):
    source_relation_id: RelationId | None
    joins: dict[RelationId, JoinInfo]
```

`SubPlan` should expose resolved output columns:

```python
class SubPlan(Step):
    relation_id: RelationId | None
    output_columns: tuple[ColumnId, ...]
    correlation: tuple[ColumnRef, ...]
```

`StepAnnotations` should switch from string lists to identities:

```python
@dataclass
class StepAnnotations:
    step_id: str
    step_type: str
    step_name: str
    condition: exp.Expression | None = None
    referenced_columns: tuple[ColumnId, ...] = ()
    projected_columns: tuple[ColumnId, ...] = ()
    source_relations: tuple[RelationId, ...] = ()
```

The annotation phase should stamp resolved `ColumnId` metadata onto each
`exp.Column` it touches, for example:

```python
column.meta[PARSEVAL_COLUMN_ID] = column_id
```

This keeps downstream expression consumers compatible with sqlglot ASTs while
giving them stable identity.

## Catalog and Domain Changes

DDL ingestion should create physical identities once and store schema state by
those identities.

```python
@dataclass(frozen=True)
class CatalogTable:
    id: RelationId
    columns: tuple[CatalogColumn, ...]


@dataclass(frozen=True)
class CatalogColumn:
    id: ColumnId
    datatype: exp.DataType
    nullable: bool
    unique: bool
    primary_key: bool
```

`ColumnSpec`, `TableSpec`, and `ForeignKeySpec` should carry identities:

```python
@dataclass(frozen=True)
class ForeignKeySpec:
    source_table: RelationId
    source_columns: tuple[ColumnId, ...]
    target_table: RelationId
    target_columns: tuple[ColumnId, ...]


@dataclass(frozen=True)
class ColumnSpec:
    id: ColumnId
    table: RelationId
    datatype: DataType
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False
    foreign_key: ForeignKeySpec | None = None
    default: Any = None
    native_type: str | None = None
    dialect: str | None = None


@dataclass(frozen=True)
class TableSpec:
    id: RelationId
    columns: tuple[ColumnSpec, ...]
    primary_key: tuple[ColumnId, ...]
    unique_constraints: tuple[tuple[ColumnId, ...], ...]
    foreign_keys: tuple[ForeignKeySpec, ...]
```

Domain providers may still expose display names via properties like
`ColumnSpec.qualified_name`, but matching and lookup should use `ColumnId`.

## Instance Changes

`Instance` should store physical rows by physical relation identity:

```python
class Instance(Catalog):
    data: dict[RelationId, list[Row]]
```

`Row` should store cells by `ColumnId`:

```python
class Row(exp.Expression):
    columns: dict[ColumnId, Symbol]
```

`Variable` should carry resolved IDs:

```python
Variable(
    this=stable_solver_name,
    relation_id=relation_id,
    column_id=column_id,
    rowid=rowid,
)
```

The stable solver name remains a string because solvers and logs need readable
names. It should be derived from identity objects and row index, not used as
semantic identity.

`SymbolIndex` should key reverse lookups by identities:

```python
class SymbolIndex:
    def by_column(self, column_id: ColumnId) -> list[Variable]:
        pass

    def by_row(self, relation_id: RelationId, rowid: Any) -> list[Variable]:
        pass
```

The instance API may accept sqlglot nodes or strings at boundaries, but it must
resolve them to `RelationId` and `ColumnId` before storing or looking up state.

## Evaluator and Solver Boundary

Expressions remain sqlglot ASTs. Column resolution comes from metadata:

```python
column_id = column.meta.get(PARSEVAL_COLUMN_ID)
```

`Environment` should bind by `ColumnId`:

```python
class Environment:
    bindings: dict[ColumnId, Any]
```

If a caller passes an unresolved `exp.Column`, evaluation should fail closed
with an unresolved-column error rather than silently falling back to a bare
string lookup.

Solver assignment keys should use row-scoped `ColumnId` bindings or a wrapper
around them, not `(table: str, column: str)` pairs. SQL expressions passed into
the solver can remain sqlglot expressions as long as every referenced column is
annotated with a resolved identity.

## Migration Order

1. Add identity dataclasses and constructors from `exp.Identifier`,
   `exp.Table`, and `exp.Column`.
2. Update catalog DDL ingestion to build physical `RelationId` and `ColumnId`
   while keeping display helpers for debug output.
3. Add planner `ScopeFrame` construction and column-reference resolution.
4. Change `StepAnnotations` to expose identities and stamp `exp.Column`
   metadata.
5. Change `Row`, `Variable`, and `SymbolIndex` to store identities.
6. Change `Instance` row creation and schema/domain lookup to resolve boundary
   inputs to identities before use.
7. Change `Environment`, symbolic evaluator, and solver adapters to read
   `ColumnId` metadata.
8. Remove remaining semantic uses of `normalize_name()` and string table/column
   pairs. Keep string rendering only for display, generated solver names, and
   exported SQL.

## Verification

Focused regression cases should drive the migration:

```sql
SELECT a.id, b.id
FROM users AS a
JOIN users AS b ON a.id = b.parent_id;
```

The two `id` references must resolve to distinct query-scope column IDs while
sharing the same physical source table identity.

```sql
SELECT "User"."ID" FROM "User";
```

Quoted names must preserve spelling and not collapse into unquoted normalized
names.

```sql
SELECT id
FROM users
JOIN orders ON users.id = orders.user_id;
```

The bare `id` must fail or be recorded as ambiguous when more than one visible
relation exposes `id`.

```sql
WITH x AS (SELECT id FROM users)
SELECT id FROM x;
```

The outer `id` must resolve to the CTE output column, not directly to the
physical `users.id` column.

```sql
SELECT dt.id
FROM (SELECT id FROM users) AS dt;
```

The `dt.id` reference must resolve to the subquery output column.

Additional checks:

- Foreign keys and primary keys refer to `ColumnId` objects.
- `Row.__getitem__` does not ignore qualifiers by falling back to only
  `column.name`.
- `SymbolIndex.by_column()` distinguishes self-join aliases and physical
  instance storage where appropriate.
- Solver assignments cannot collapse values for `a.id` and `b.id`.

## Design Decisions

Planner resolution fails hard for ambiguous or unresolved columns. Returning an
unknown-like result remains appropriate inside solver code, but planner identity
resolution is a prerequisite for sound downstream behavior.

Instance storage uses physical `ColumnId` values. Planner and solver witness
generation use query-scope `ColumnId` values where aliases or subquery outputs
matter. Query-scope columns that originate from catalog columns carry
`source_column_id`.

The sqlglot metadata bridge uses one constant:

```python
PARSEVAL_COLUMN_ID = "parseval_column_id"
```

No other metadata field should be used for resolved column identity.
