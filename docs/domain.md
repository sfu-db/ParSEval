# Domain Module: Technical Deep Dive

The `domain` module is ParSEval's specialized engine for generating schema-valid synthetic data. This document provides a detailed technical overview of its internal mechanics, algorithms, and extensibility points.

---

## 1. Constraint Compilation & Normalized Plans

Before generation begins, ParSEval compiles high-level schema metadata into a **`ColumnDomainPlan`**. This plan is a normalized, execution-ready representation of all constraints affecting a single column.

### Compilation Process
The `ConstraintCompiler` performs the following operations:
1.  **Type Extraction**: Extracts implicit limits from the `DataType` (e.g., `VARCHAR(50)` sets `maximum_length=50`, `INT` sets numeric ranges based on bit-width).
2.  **Constraint Intersection**: Merges multiple `CHECK` constraints. For example, if a column has two `RangeConstraint`s ([0, 100] and [50, 150]), the compiler intersects them to [50, 100].
3.  **Pattern Merging**: Consolidates `PrefixConstraint`, `SuffixConstraint`, and `ContainsConstraint` into the plan.
4.  **Residual Predicates**: Complex constraints that cannot be normalized (e.g., custom lambdas) are stored as "residual predicates" to be validated after value generation.

### The `ColumnDomainPlan` Structure
```python
@dataclass(frozen=True)
class ColumnDomainPlan:
    nullable: bool
    unique: bool
    allowed_values: Optional[Tuple[Any, ...]]  # For ENUMs or IN clauses
    minimum, maximum: Any                    # Numeric/Temporal limits
    minimum_length, maximum_length: int     # String/Bytes limits
    pattern, prefix, suffix: str            # Pattern matching
    modulo_divisor, modulo_remainder: int   # Modulo math
    residual_predicates: Tuple[Callable]    # Fallback validation
```

---

## 2. Provider Resolution Algorithm

ParSEval uses a priority-based registry (`ProviderRegistry`) to select the best `ValueProvider` for any given column.

### Selection Priority
When `registry.resolve(column_spec)` is called, it follows this hierarchy:

1.  **Direct Column Override**: If a provider was explicitly registered via `register_column("table.column", provider)`.
2.  **Semantic Tag Match**: If the column has `semantic_tags` (e.g., `["email"]`) and a provider is registered via `register_semantic("email", provider)`.
3.  **Heuristic Type Match**: Built-in providers are scored based on their `supports(spec, profile)` method. The `ProviderRegistry` iterates through all registered providers and picks the one with the highest `(score, priority)` tuple.

---

## 3. Data Generation Lifecycle

### Step 1: Topological Ordering
To satisfy `FOREIGN KEY` constraints, tables are processed in an order that ensures parent data exists before child rows are generated. ParSEval currently requires manual ordering or independent table groups if circular dependencies exist.

### Step 2: Row Generation Algorithm (`DatabaseBuilder.complete_row`)
For each row in a table:
1.  **Preset Values**: Values provided by the user are validated and stored in `RowContext`.
2.  **Composite Foreign Keys**: Multi-column relationships are resolved by sampling an existing tuple from the parent table's `SchemaRuntime`.
3.  **Column Loop**: For each remaining column:
    - **RowContext Lookup**: If a value was already set (by a composite FK or preset), it is skipped.
    - **Null Check**: Decides whether to generate `NULL` based on the `null_rate`.
    - **Candidate Generation**: The resolved `ValueProvider` is called. It receives the `RowContext`, allowing it to generate values based on *other* columns in the same row (e.g., a "full_name" provider using "first_name").
    - **Validation**: The `ConstraintValidator` checks the candidate against the `ColumnDomainPlan`.
    - **Unique Conflict Resolution**: If `unique=True`, the generator checks the `ColumnState.used_values` for collisions. If a collision occurs, it retries generation (up to 10 times for simple types, or exhaustive search for small `allowed_values` sets).
4.  **Persistence**: The finished row is committed to `SchemaRuntime.remember_row`, updating both the table state and individual column statistics.

---

## 4. Dialect & Type Coercion

ParSEval maintains strict type safety across different SQL dialects using `TypeAdapter`s and the `coerce_value` utility.

### Type Adapters
Adapters (found in `domain/adapters/`) handle:
- **`coerce_in(value, profile)`**: Safely transforms an input (e.g., a string "2023-01-01") into a Python type (`date`).
- **`equivalent(left, left_profile, right, right_profile)`**: Determines if two values from potentially different dialects/types are semantically equal (used for FK validation).

### Type Profiling
The `TypeService` creates a `TypeProfile` for each column, which includes the base `TypeFamily` (Numeric, String, etc.) and dialect-specific traits. This profile is passed to providers to help them tune their generation logic.

---

## 5. Advanced Configuration & Extension

### The `RowContext`
Providers can access `row_context.values` to implement cross-column logic:
```python
def generate(self, spec, runtime, row_context, **kwargs):
    first_name = row_context.get("first_name", "User")
    return f"{first_name}@example.com"
```

### Implementing Custom Constraints
You can add complex logic via `CheckConstraint`:
```python
spec = ColumnSpec(
    ...,
    checks=[
        CheckConstraint(lambda x: x % 2 == 0) # Only even numbers
    ]
)
```
*Note: Constraints that the compiler cannot normalize into simple range/pattern checks will be executed as "residual predicates" after the provider generates a value.*
