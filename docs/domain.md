# Domain Module Reference

The `parseval.domain` module is ParSEval's engine for generating **schema-consistent synthetic data**. It takes a declarative schema specification and produces database rows that satisfy all declared constraints: types, NOT NULL, UNIQUE, CHECK, foreign keys, and more.

---

## Architecture Overview

```
parseval/domain/
├── __init__.py          # Public API re-exports
├── spec.py              # Immutable schema specification dataclasses
├── constraints.py       # Constraint type hierarchy
├── compiler.py          # ConstraintCompiler → ColumnDomainPlan
├── builder.py           # DatabaseBuilder (orchestrates row generation)
├── state.py             # Mutable runtime state (SchemaRuntime, ColumnState)
├── types.py             # TypeProfile + TypeService (type introspection)
├── coercion.py          # Value coercion & cross-dialect comparison
├── exceptions.py        # Domain error hierarchy
├── adapters/            # TypeAdapter implementations per SQL dialect
└── providers/           # ValueProvider implementations per data type
```

The high-level flow is:

1. **Schema specification** — Define tables, columns, and constraints as immutable dataclasses (`SchemaSpec`, `TableSpec`, `ColumnSpec`).
2. **Constraint compilation** — `ConstraintCompiler` normalizes raw constraints into a `ColumnDomainPlan` per column.
3. **Row generation** — `DatabaseBuilder` iterates tables, generates values via the `ProviderRegistry`, validates against compiled plans, and tracks state.
4. **Runtime tracking** — `SchemaRuntime` records every generated row and value to enforce uniqueness and resolve foreign keys.

---

## Core Data Model (`spec.py`)

### `SchemaSpec`

The top-level container for an entire database schema.

```python
@dataclass(frozen=True)
class SchemaSpec:
    tables: Tuple[TableSpec, ...]         # All tables in the schema
    dialect: Optional[str] = None          # Default SQL dialect (e.g., "sqlite", "postgres")
    metadata: dict[str, Any] = field(default_factory=dict)  # Free-form metadata
```

- **`get_table(table_name)`** — Case-insensitive table lookup. Raises `KeyError` if not found.

### `TableSpec`

Immutable specification for a single table.

```python
@dataclass(frozen=True)
class TableSpec:
    name: str                              # Table name (automatically lowered)
    columns: Tuple[ColumnSpec, ...]        # All columns
    primary_key: Tuple[str, ...] = ()      # Column names forming the PK
    unique_constraints: Tuple[Tuple[str, ...], ...] = ()  # Multi-column UNIQUE constraints
    foreign_keys: Tuple[ForeignKeySpec, ...] = ()         # Outgoing FK references
```

- **`get_column(column_name)`** — Case-insensitive column lookup. Returns a `ColumnSpec` or raises `KeyError`.
- Table name is automatically lowered at construction via `__post_init__`.

### `ColumnSpec`

The central specification object for a single column. All downstream logic (compilation, generation, validation) starts here.

```python
@dataclass(frozen=True)
class ColumnSpec:
    table: str                             # Owning table (lowered)
    column: str                            # Column name (lowered)
    datatype: DataType                     # Declared SQL data type
    nullable: bool = True                  # Whether NULL is allowed
    unique: bool = False                   # Single-column UNIQUE constraint
    primary_key: bool = False              # Part of the primary key
    foreign_key: Optional[ForeignKeySpec] = None   # FK reference, if any
    default: Any = None                    # Default value expression
    native_type: Optional[str] = None      # DB-native type override
    dialect: Optional[str] = None          # Target dialect (overrides schema default)
    length: Optional[int] = None           # Character/precision length
    precision: Optional[int] = None        # Numeric precision
    scale: Optional[int] = None            # Decimal scale
    semantic_tags: Tuple[str, ...] = ()    # Freeform tags for categorization
    checks: Tuple[Any, ...] = ()           # CHECK constraint objects
```

- **`qualified_name`** (property) — Returns `"table.column"`, the globally unique identifier.
- `__post_init__` normalizes names, semantic tags, and checks to canonical form. It also calls `DataType.build(self.datatype)` to ensure a proper `DataType` instance.

### `ForeignKeySpec`

Declares a foreign key relationship between source and target columns.

```python
@dataclass(frozen=True)
class ForeignKeySpec:
    source_table: str                      # Table containing the FK column(s)
    source_columns: Tuple[str, ...]        # FK column(s) on the source side
    target_table: str                      # Referenced (parent) table
    target_columns: Tuple[str, ...]        # Target column(s), matched positionally
```

Supports both single-column and composite foreign keys.

---

## Constraint Types (`constraints.py`)

All constraints are immutable, frozen dataclasses forming a hierarchy rooted at `SchemaConstraint`. These are attached to `ColumnSpec.checks` and compiled by the `ConstraintCompiler`.

| Constraint | Fields | Purpose |
|---|---|---|
| `NotNullConstraint` | *(none)* | Column must never be NULL |
| `UniqueConstraint` | `columns: Tuple[str, ...]` | Values must be unique across specified columns |
| `RangeConstraint` | `minimum`, `maximum`, `minimum_inclusive`, `maximum_inclusive` | Constrain value to a numeric/temporal range |
| `LengthConstraint` | `minimum`, `maximum` | Constrain string/bytes length |
| `ChoicesConstraint` | `values: Tuple[Any, ...]` | Value must be one of an explicit set (e.g., ENUM) |
| `PatternConstraint` | `pattern: str` | Value must match a regex pattern |
| `CheckConstraint` | `expression: Any` | Arbitrary callable predicate (lambda or function) |
| `ModuloConstraint` | `divisor: int`, `remainder: int` | Value must satisfy `value % divisor == remainder` |
| `PrefixConstraint` | `prefix: str` | Value must start with given prefix |
| `SuffixConstraint` | `suffix: str` | Value must end with given suffix |
| `ContainsConstraint` | `substring: str` | Value must contain the given substring |

### Example: Building a Constrained Column

```python
from parseval.domain import ColumnSpec, RangeConstraint, ChoicesConstraint, PatternConstraint
from parseval.dtype import DataType

status_col = ColumnSpec(
    table="orders",
    column="status",
    datatype=DataType.build("VARCHAR(20)"),
    checks=(
        ChoicesConstraint(values=("pending", "shipped", "cancelled")),
    ),
)

price_col = ColumnSpec(
    table="orders",
    column="price",
    datatype=DataType.build("DECIMAL(10,2)"),
    nullable=False,
    checks=(
        RangeConstraint(minimum=0.01, maximum=9999.99),
    ),
)

code_col = ColumnSpec(
    table="products",
    column="code",
    datatype=DataType.build("VARCHAR(10)"),
    checks=(
        PatternConstraint(pattern=r"[A-Z]{2}-\d{4}"),
    ),
)
```

---

## Constraint Compilation (`compiler.py`)

The `ConstraintCompiler` translates raw `ColumnSpec.checks` into a **`ColumnDomainPlan`** — a normalized, execution-ready representation that the generator and validator both consume.

### `ColumnDomainPlan`

```python
@dataclass(frozen=True)
class ColumnDomainPlan:
    nullable: bool = True                    # Whether NULL is allowed
    unique: bool = False                     # Whether values must be unique
    default: Any = None                      # Default value

    # Finite domain (from ENUM, ChoicesConstraint, etc.)
    allowed_values: Optional[Tuple[Any, ...]] = None

    # Values to exclude (e.g., already-used unique values)
    excluded_values: Tuple[Any, ...] = field(default_factory=tuple)

    # Numeric/temporal range
    minimum: Optional[Any] = None
    maximum: Optional[Any] = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True

    # String length limits
    minimum_length: Optional[int] = None
    maximum_length: Optional[int] = None

    # Pattern matching
    pattern: Optional[str] = None
    prefix: Optional[str] = None
    suffix: Optional[str] = None
    contains: Tuple[str, ...] = field(default_factory=tuple)

    # Modulo constraint
    modulo_divisor: Optional[int] = None
    modulo_remainder: int = 0

    # Opaque predicates for complex checks (CheckConstraint lambdas, etc.)
    residual_predicates: Tuple[Callable[[Any], bool], ...] = field(default_factory=tuple)
```

### How Compilation Works

1. **Datatype-derived constraints**: ENUM types are extracted into `allowed_values`. The datatype's `length` may populate `maximum_length`.

2. **Constraint intersection**: Multiple constraints of the same type are intersected. Two `RangeConstraint`s of `[0, 100]` and `[50, 200]` become `[50, 100]`. Contradictory intersections (e.g., `[0, 10]` ∩ `[20, 30]`) raise `ConstraintConflict`.

3. **Pattern/prefix/suffix merging**: When multiple pattern constraints exist but can't be intersected (regex intersection is undecidable in general), the extra constraints become `residual_predicates`.

4. **Modulo constraints**: Same-divisor modulo constraints are checked for consistency; different divisors become residual predicates.

5. **CheckConstraint lambdas**: Always become residual predicates since they are opaque.

### Usage

```python
from parseval.domain import ConstraintCompiler, ColumnDomainPlan

compiler = ConstraintCompiler()
plan = compiler.compile(column_spec)  # Returns a ColumnDomainPlan
```

---

## Runtime State (`state.py`)

During generation, `SchemaRuntime` tracks all generated data to enforce constraints that require global knowledge (uniqueness, foreign keys).

### `SchemaRuntime`

The root runtime object, initialized once per `DatabaseBuilder`.

```python
@dataclass
class SchemaRuntime:
    schema: SchemaSpec                      # The schema being generated
    seed: int = 142                          # Random seed
    rng: random.Random                       # RNG instance (initialized in __post_init__)
    tables: Dict[str, TableState]            # table_name → TableState
    columns: Dict[str, ColumnState]          # "table.column" → ColumnState
```

- **`table_state(table_name)`** — Case-insensitive lookup.
- **`column_state(table_name, column_name)`** — Case-insensitive lookup for a column's state.
- **`referenced_values(column)`** — Returns used values from a single-column FK target. Returns `None` for composite FKs.
- **`referenced_key_tuples(foreign_key)`** — Returns all tuples from the target table (for composite FK resolution).
- **`remember_row(table_name, row)`** — Persists a generated row, updating both `TableState` and all relevant `ColumnState` entries.

### `TableState`

Tracks rows generated for a single table.

```python
@dataclass
class TableState:
    spec: TableSpec
    rows: List[Dict[str, Any]] = field(default_factory=list)
```

### `ColumnState`

Tracks per-column statistics for uniqueness enforcement.

```python
@dataclass
class ColumnState:
    spec: ColumnSpec
    generated_values: List[Any] = field(default_factory=list)   # All generated values (including None)
    used_values: Set[Any] = field(default_factory=set)          # Non-None values (for uniqueness)
    null_count: int = 0                                          # Number of NULLs generated
```

### `RowContext`

A mutable workspace for building a single row. Tracks which columns have been explicitly provided vs. auto-generated.

```python
@dataclass
class RowContext:
    table: TableSpec
    values: Dict[str, Any] = field(default_factory=dict)         # column_name (lower) → value
    provided_columns: Set[str] = field(default_factory=set)      # Explicitly set columns
    generated_columns: Set[str] = field(default_factory=set)     # Auto-generated columns

    def set_provided(column, value)    # Mark column as explicitly provided
    def set_generated(column, value)   # Mark column as auto-generated
    def get(column, default=None)      # Case-insensitive value lookup
```

---

## Data Generation (`builder.py`)

### `DatabaseBuilder`

The main entry point for data generation. Orchestrates the full pipeline: compile constraints → generate candidates → validate → persist.

```python
class DatabaseBuilder:
    def __init__(self, schema, registry=None, seed=142)
    def build(self, policy=None) -> Dict[str, list[Dict[str, Any]]]
    def generate_row(self, table_name, null_rate=0.0) -> Dict[str, Any]
    def complete_row(self, table_name, preset_values=None, persist=True, null_rate=0.0) -> Dict[str, Any]
    def generate_value(self, table_name, column_name, row_context=None, null_rate=0.0) -> Any
```

#### `BuildPolicy`

Controls how many rows to generate and the NULL rate.

```python
@dataclass(frozen=True)
class BuildPolicy:
    row_counts: Mapping[str, int] = field(default_factory=dict)   # Per-table overrides
    default_row_count: int = 1                                     # Fallback for unlisted tables
    null_rate: float = 0.0                                         # Probability of NULL (0.0–1.0)
```

#### Generation Flow

For each row, `complete_row` follows this sequence:

1. **Apply presets**: Each explicitly provided value is coerced to the column's type, validated against its `ColumnDomainPlan`, and checked for uniqueness/FK violations. It is stored in the `RowContext`.

2. **Resolve composite FKs**: Multi-column foreign keys are resolved by sampling a matching tuple from the parent table's existing rows (via `SchemaRuntime.referenced_key_tuples`).

3. **Generate remaining columns**: For each column not yet set:
   - The compiled `ColumnDomainPlan` is fetched (cached per column).
   - With probability `null_rate`, NULL is assigned (if the column is nullable).
   - Otherwise, `_generate_candidate` is called:
     - If the column has a finite domain (`allowed_values`) and is unique, the first unused value from the allowed pool is used.
     - Otherwise, the `ProviderRegistry` is consulted to generate a candidate via the type-specific `ValueProvider`.
     - The candidate is validated against the domain plan. If it fails, up to 10 retries are attempted (only when residual predicates exist).
   - The accepted value is validated for uniqueness and FK compliance, then stored in the `RowContext`.

4. **Persist**: The completed row is committed to `SchemaRuntime.remember_row`, updating all column states and the table state.

#### Foreign Key Resolution

- **Single-column FKs**: The child column's value is validated against all existing values in the target column (`SchemaRuntime.referenced_values`).
- **Composite FKs**: Full tuples are sampled from the parent table (`SchemaRuntime.referenced_key_tuples`). Partial bindings are supported — if some source columns are already set, only matching target tuples are considered.

### `TypeService` and Type Profiling (`types.py`)

`TypeService` provides type introspection by resolving each column's `DataType` into a `TypeProfile` (caching the result). The profile contains the type family, exact type string, dimensional attributes, and dialect — used by providers and the SMT solver to make type-aware decisions.

```python
@dataclass(frozen=True)
class TypeProfile:
    datatype: DataType
    dialect: Optional[str]
    family: TypeFamily          # INTEGER, DECIMAL, TEXT, BOOLEAN, DATE, DATETIME, TIME, etc.
    exact_type: str
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    unsigned: Optional[bool] = None
    timezone: Optional[bool] = None
    metadata: dict[str, Any] = field(default_factory=dict)
```

### Value Coercion (`coercion.py`)

Cross-dialect type coercion utilities used by the builder and solver.

- **`coerce_value(value, datatype, dialect)`** — Converts a concrete value to the target datatype (e.g., string `"2023-01-01"` → `date(2023, 1, 1)`).
- **`can_coerce_value(value, datatype, dialect)`** — Returns `True` if coercion succeeds without error.
- **`coerce_reference_value(value, target_datatype, dialect)`** — Coerces a FK parent value into the child column's type for cross-dialect FK resolution.
- **`values_equivalent(left, left_datatype, right, right_datatype, ...)`** — Compares values across different datatypes/dialects by projecting both through their respective type adapters.

### Error Hierarchy (`exceptions.py`)

```
DomainError                          (base for all domain errors)
├── TypeCoercionError                (value cannot be coerced to required type)
├── ConstraintViolationError         (value violates a constraint)
│   ├── UniqueConflictError          (duplicate value on unique column)
│   └── ForeignKeyResolutionError    (FK reference not found)
└── ConstraintConflict              (contradictory constraints at compile time)
```

---

## Usage Example

```python
from parseval.domain import (
    SchemaSpec, TableSpec, ColumnSpec, ForeignKeySpec,
    BuildPolicy, DatabaseBuilder,
    RangeConstraint, NotNullConstraint,
)
from parseval.dtype import DataType

# Define schema
schema = SchemaSpec(
    tables=[
        TableSpec(
            name="users",
            columns=[
                ColumnSpec(
                    table="users", column="id",
                    datatype=DataType.build("INT"),
                    nullable=False, primary_key=True,
                    unique=True,
                ),
                ColumnSpec(
                    table="users", column="age",
                    datatype=DataType.build("INT"),
                    nullable=False,
                    checks=[RangeConstraint(minimum=18, maximum=99)],
                ),
            ],
        ),
        TableSpec(
            name="orders",
            columns=[
                ColumnSpec(
                    table="orders", column="id",
                    datatype=DataType.build("INT"),
                    nullable=False, primary_key=True,
                ),
                ColumnSpec(
                    table="orders", column="user_id",
                    datatype=DataType.build("INT"),
                    nullable=False,
                    foreign_key=ForeignKeySpec(
                        source_table="orders", source_columns=("user_id",),
                        target_table="users", target_columns=("id",),
                    ),
                ),
            ],
            foreign_keys=[
                ForeignKeySpec(
                    source_table="orders", source_columns=("user_id",),
                    target_table="users", target_columns=("id",),
                )
            ],
        ),
    ]
)

# Generate data
builder = DatabaseBuilder(schema, seed=42)
policy = BuildPolicy(
    row_counts={"users": 5, "orders": 10},
    null_rate=0.0,
)
result = builder.build(policy)

# result = {
#     "users": [
#         {"id": 1, "age": 34},
#         {"id": 2, "age": 27},
#         ...
#     ],
#     "orders": [
#         {"id": 1, "user_id": 1},   # FK references existing user
#         ...
#     ]
# }
```

---

## Provider Extensibility

The `ProviderRegistry` (in `domain/providers/`) supports custom value generation:

- **`register_column(table_column, provider)`** — Override generation for a specific column.
- **`register_semantic(tag, provider)`** — Override generation for columns with a matching semantic tag.
- **`register_type_adapter(datatype_class, adapter)`** — Register a new type adapter for coercion.

Providers receive the column spec, runtime state, row context, and the compiled domain plan, enabling sophisticated cross-column value generation.