# SQL parsing and compilation

The parser is organized around one shared expression compiler:

- `expression.py` binds and compiles scalar values and three-valued conditions.
  Callers select an explicit `ExpressionCapabilities` profile instead of using
  a query-specific or DDL-specific expression implementation.
- `context.py`, `scope.py`, and `syntax.py` contain the shared compilation
  context, name-binding scopes, and SQLGlot syntax helpers.
- `query/` owns relational query analysis, normalization, and compilation.
- `ddls/` imports schema declarations and compiles `CHECK` and generated-column
  expressions with the restricted schema-expression capability profile.

Query compilation supplies relational subquery services to the expression
compiler. DDL compilation does not, so row-local schema expressions cannot
accidentally depend on query planning.

## Catalog and DDL parsing

```python
from parseval.catalog import Catalog
from parseval.terms import IRBuilder, TermArena

catalog = Catalog.from_ddl(
    """
    CREATE TABLE app.users (
        id BIGINT PRIMARY KEY,
        age INT CHECK (age >= 0),
        name VARCHAR(100)
    );
""",
    dialect="postgres",
)
table = catalog.resolve_table("app.users")
column = catalog.resolve_column(table, "age")
column_type = table.column_spec(column.id).sort

arena = TermArena(catalog.context)
builder = IRBuilder(arena)
relation = builder.base(table.relation)
```

`Catalog` owns SQL names, declared type text, default expressions, and the arena
for checked schema expressions. `Context` owns semantic declarations and allocates
context-local IDs. `terms.decls.RowShape` is an anonymous row shape: equal shapes
can be shared by different base relations. Column positions come from
`RelationSpec.column_position(ColumnId)`; SQL aliases belong to query scopes.

`ColumnDecl` is the programmatic input to `Catalog.register_table`. Its sort must
already reflect effective nullability. Registered row shapes never change.
`TableDecl.columns` contains name bindings; `TableDecl.spec.columns` contains the
semantic column specifications. Function and aggregate catalog declarations
similarly reference signatures stored in the context.

DDL import first collects declarations and resolves effective nullability, then
registers all relations, keys, foreign keys, and row-local expressions. This is a
schema-description import, so forward references and cyclic foreign keys are
accepted. An unqualified FK target in a qualified table uses that table's
namespace. This is not an interpreter for a database session's search path.

Construction returns a fresh catalog only after validation. Failed construction
does not publish a catalog. There is no incremental DDL application, rollback,
term-ID reuse, or schema mutation API. Programmatic constraint registration
validates before replacing a relation's constraint tuple.

## Supported schema information

- Explicit `CREATE TABLE` declarations; dialect-normalized names, declared types,
  nullability, defaults, and collation identities.
- `CREATE TABLE IF NOT EXISTS` declarations and `ALTER TABLE ... ADD CONSTRAINT`
  for the supported key and row-local constraint forms.
- Inline/table primary keys, unique keys, foreign keys, named constraints, and
  composite keys. FK pairing order is preserved.
- Checked row-local comparisons, NULL tests, Boolean logic, BETWEEN, literal IN
  lists, and integer/float addition, subtraction, multiplication, and negation.
- Generated expressions when accepted by the SQL parser; references to other
  generated columns are rejected.
- Programmatic function/aggregate declarations and explicit UNIQUE null policies.

A `CheckDecl` stores a three-valued predicate: TRUE **or UNKNOWN** satisfies the
constraint. It is not a WHERE filter. A later proof-lowering pass must implement
this distinction, unique-key NULL policies, and foreign-key matching semantics.
Scalar arithmetic is represented by shared, immutable function symbols; this
module does not evaluate arithmetic or add arithmetic proof axioms.

Parsed unsupported constraints and unsupported scalar operations are retained as
`UnsupportedConstraintDecl` with source SQL and a reason. This includes foreign
keys whose targets or column pairings do not satisfy key requirements. Deferred
constraints, collated keys, and unsupported FK matching are not active proof
assumptions.
Consumers must check `metadata.proof_active` before using a constraint in proofs.
Unknown references, duplicate declarations, malformed SQL, unsupported SQL types,
and unsupported statements raise an error.

## SQLGlot boundary

`SQLDialect` is the syntax boundary. It selects the SQLGlot dialect, normalizes
identifiers, parses DDL, and converts SQLGlot data types into Parseval scalar
types. Query lowering consumes SQLGlot's normalized `exp.*` nodes and their
declared `arg_types`; it does not reinterpret source SQL with string matching.

SQLGlot's dialect function tables normalize surface syntax into expression
classes. They are syntax constructors, not semantic signatures. Parseval keeps
function overloads, result sorts, nullability, and volatility in `Catalog` and
`terms.Context`. The builtin-function loader therefore maps normalized
expression families to explicit Parseval declarations rather than copying a
dialect parser's function table.

Catalogs imported from DDL load a conservative builtin scalar core: `ABS`,
`LOWER`, `UPPER`, `LENGTH`, and the two- and three-argument forms of
`SUBSTRING`. Each overload records exact scalar and nullability behavior.
Unrecognized and anonymous functions remain unsupported until they have an
explicit semantic declaration.

DDL parsing retains original type spelling, from which storage limits come.
Primary-key columns are NOT NULL in every dialect: SQLite accepts NULL in a
primary key that is not an `INTEGER` rowid alias, a bug kept for
compatibility that generated data never relies on.
PostgreSQL identifiers preserve quoted case. MySQL uses case-sensitive table
names and case-insensitive column names; server-specific
`lower_case_table_names` settings are not modeled.

## Explicit limits

DDL import rejects syntax outside its supported schema-description subset,
including PostgreSQL `UNIQUE NULLS NOT DISTINCT`, generated `STORED` syntax, and
SQLite `WITHOUT ROWID`/`STRICT` in the forms covered by our tests. These inputs
raise `DDLImportError`; they are not silently dropped or reparsed as another
dialect. Unsupported `ALTER TABLE` actions, views, CTAS, and CREATE options are
rejected.

Semantic scalar types are abstractions of SQL types. Declared widths, lengths,
timezone qualifiers, and defaults are preserved as catalog metadata, but this
layer does not model overflow, all implicit coercions, SQLite dynamic storage
classes, binary value semantics, or engine-specific collation behavior. `TIME`
is a distinct scalar type; timezone qualifiers are retained in the declaration
text but not modeled semantically. `ENUM` columns use string semantics, so
engine-specific enum ordering is not modeled. Decimal arithmetic result typing
is unsupported. Full engine equivalence requires those semantics in subsequent
lowering/solver layers.

SQLGlot `MappingSchema` export preserves namespaces and declared types. Its tables
must have a consistent qualification depth; mixed depths are rejected explicitly.

Run focused tests with:

```sh
PYTHONPATH=src python -m pytest -q tests/parser tests/terms
```
