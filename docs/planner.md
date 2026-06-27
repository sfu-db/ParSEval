# Planner Module Reference

`parseval.plan.planner` builds a logical plan DAG from a SQL expression. It
restructures sqlglot's AST into a deterministic tree of typed **Step** nodes,
stamps each column with a stable **ColumnId** identity, and annotates every
step with metadata for downstream analysis and coverage tracking.

---

## Quick Start

```python
import sqlglot
from parseval.plan.planner import Plan

sql = "SELECT a, SUM(b) FROM t WHERE a > 0 GROUP BY a HAVING SUM(b) > 10 ORDER BY a LIMIT 5"
expression = sqlglot.parse_one(sql, read="sqlite")
plan = Plan(expression)

# Walk the plan
print(plan.root)                  # Limit → Project → Sort → Having → Aggregate → Filter → Scan
print(plan.ordered_steps)         # Topological tuple of all steps

# Access annotations (requires an Instance for full metadata)
# annotations = plan.annotation_for(step)
```

---

## Plan Tree Shape

Every SELECT produces a plan with this canonical shape:

```
Limit?                (if LIMIT / OFFSET)
 └── Project          (always; projections + distinct)
      └── Sort?       (if ORDER BY)
           └── Having?    (if HAVING)
                └── Aggregate?   (if GROUP BY or aggregate funcs)
                     └── Filter?  (if WHERE)
                          └── Join? / Scan / SetOperation
                               └── Scan  (one per join input)
```

**Key design choices:**

| Feature | sqlglot planner | ParSEval planner |
|---------|-----------------|------------------|
| SELECT list | Attached to top operator | Dedicated `Project` step |
| WHERE | Fused as `step.condition` on `Scan`/`Join` | Lifted into `Filter` step |
| HAVING | Stored on `Aggregate` | Lifted into `Having` step |
| LIMIT | Stored as `step.limit` | Dedicated `Limit` step |
| DISTINCT | Wrapping `Aggregate` | `Project.distinct = True` |
| Subqueries | Implicit in AST | First-class `SubPlan` step |

---

## Step Types

### `Scan`

Leaf node representing a table, CTE reference, or subquery source.

```python
class Scan(Step):
    source: Optional[exp.Expression]   # exp.Table, exp.Subquery, or None (static)
    relation_id: Optional[RelationId]  # Set during identity preparation
```

### `Join`

Combines rows from two or more scans via join predicates.

```python
class Join(Step):
    source_relation: Optional[RelationId]
    joins: Dict[RelationId, Dict[str, Any]]  # side, join_key, source_key, condition
```

### `Filter`

Applies a `WHERE` predicate.

```python
class Filter(Step):
    condition: Optional[exp.Expression]  # The WHERE predicate
    source: Optional[str]
```

### `Aggregate`

Groups rows and computes aggregate functions.

```python
class Aggregate(Step):
    aggregations: List[exp.Expression]           # e.g., SUM(x), COUNT(*)
    operands: Tuple[exp.Expression, ...]          # Non-column aggregate operands
    group: Dict[str, exp.Expression]              # {"_g0": col_a, "_g1": ...}
    source: Optional[str]
```

### `Having`

Applies a `HAVING` predicate on aggregate output.

```python
class Having(Step):
    condition: Optional[exp.Expression]
    source: Optional[str]
```

### `Sort`

Orders rows by key expressions.

```python
class Sort(Step):
    key: List[exp.Ordered]  # ORDER BY expressions
```

### `Project`

Emits the final SELECT list. Always present for SELECT statements.

```python
class Project(Step):
    projections: Sequence[exp.Expression]  # The SELECT list
    distinct: bool                         # SELECT DISTINCT flag
    source: Optional[str]
    output_column_ids: Tuple[ColumnId, ...]  # Set during identity preparation
```

### `Limit`

Caps row count with optional OFFSET.

```python
class Limit(Step):
    limit: float   # math.inf if no limit
    offset: int
    source: Optional[str]
```

### `SetOperation`

Represents `UNION`, `INTERSECT`, `EXCEPT`.

```python
class SetOperation(Step):
    op: Type[exp.Expression]  # exp.Union, exp.Intersect, exp.Except
    left: Optional[str]       # Name of left branch
    right: Optional[str]      # Name of right branch
    distinct: bool            # UNION vs UNION ALL
```

### `SubPlan`

First-class reference to a subquery or CTE. Attached as an *extra* dependency
(not a chain dependency) of the consuming step.

```python
class SubPlan(Step):
    kind: SubPlanKind              # TABLE, SCALAR, EXISTS, IN, CTE
    inner: Step                    # Root of the inner plan
    anchor: exp.Expression         # AST node that anchors the subquery
    correlation: Tuple[exp.Column, ...]  # Outer-bound correlated columns
    output_columns: Tuple[str, ...]      # Aliases the subquery exposes
    alias: Optional[str]
    consumer: Optional[Step]       # The outer step that consumes this subquery
```

**SubPlanKind values:**

| Kind | SQL Pattern |
|------|-------------|
| `TABLE` | `FROM (SELECT ...) AS alias` |
| `SCALAR` | `(SELECT col FROM ...)` used as a value |
| `EXISTS` | `[NOT] EXISTS (SELECT ...)` |
| `IN` | `x [NOT] IN (SELECT ...)` |
| `CTE` | `WITH cte_name AS (SELECT ...)` |

---

## Dependency Traversal

Every `Step` exposes two dependency accessors:

```python
step.chain_dependencies   # Tuple[Step, ...]  — upstream operators (skips SubPlan)
step.subplan_dependencies # Tuple[SubPlan, ...] — attached subqueries/CTEs
step.dependencies         # Set[Step] — all dependencies (union of above)
step.dependents           # Set[Step] — downstream consumers
```

**Example:**

```python
for step in plan.ordered_steps:
    print(f"{type(step).__name__}:")
    for dep in step.chain_dependencies:
        print(f"  chain → {type(dep).__name__}")
    for sub in step.subplan_dependencies:
        print(f"  subplan → {sub.kind.value} ({sub.alias})")
```

---

## Query Qualification

The planner resolves `table.column` references to stable `ColumnId` identities
using a multi-layered resolution system.

### Resolution Pipeline

```
Column AST node (col.table = "T", col.name = "a")
    │
    ▼
_resolve_column_id(col, step, instance, plan=plan)
    │
    ├─ Qualified (col.table set):
    │    ├─ _resolve_relation_from_scope() → RelationId
    │    │    ├─ Direct lookup in qualifier_index (alias → RelationId)
    │    │    └─ Indirect: scope graph → physical table → qualifier_index
    │    └─ Match ColumnId from step's visible columns
    │
    └─ Unqualified:
         └─ Match by name in _visible_columns(step)
              ├─ Single match → return
              ├─ Multiple relations → raise AmbiguousColumnError
              └─ No match → raise UnresolvedColumnError (or return None)
```

### Key Data Structures

| Structure | Built By | Purpose |
|-----------|----------|---------|
| `qualifier_index` | `_build_qualifier_index()` | Maps `table_name/alias → RelationId` from physical Scans |
| `scope_sources` | `_build_scope_index()` | Maps `expression_id → {qualifier → Table/Scope}` |
| `correlations` | `_build_scope_index()` | Maps `expression_id → correlated columns` |

### Dialect-Aware Normalization

All identifier comparisons go through `identifier_name()` which uses
sqlglot's `Dialect.normalize_identifier()` for correct casing:

```python
from parseval.identity import identifier_name

# SQLite lowercases even quoted identifiers
name = identifier_name('"MyTable"', dialect="sqlite")
# name.raw = "MyTable", name.normalized = "mytable"

# PostgreSQL preserves case for quoted identifiers
name = identifier_name('"MyTable"', dialect="postgresql")
# name.raw = "MyTable", name.normalized = "MyTable"
```

### Edge Cases

- **Self-joins**: Unqualified column references across self-joined tables raise
  `ValueError("Ambiguous column: ...")`. Use explicit table qualifiers.
- **CTE/subquery scopes**: Columns in correlated subqueries resolve through
  `SubPlan.consumer` which carries the outer scope's visible columns.
- **Star expansion**: `SELECT *` is not expanded by the planner; downstream
  consumers handle it.

---

## Data Type Annotation

The planner uses a two-layer annotation system: **physical types** from the
catalog and **semantic types** inferred from query context.

### Layer 1: Physical/Catalog Types

Set by `_enrich_identity_column()` during the `_annotate()` pass.

```python
# For each Column node with a PARSEVAL_COLUMN_ID:
#   1. Walk source_column_id chain to find PHYSICAL column
#   2. Look up catalog: datatype, nullable, unique
#   3. Stamp on column via set_column_meta()

col.type  # DataType from catalog (e.g., TEXT, INT, REAL)
col.args["_parseval_meta"]  # frozenset of (key, value) pairs:
    # "table"    → normalized table name
    # "nullable" → bool
    # "unique"   → bool
    # "domain"   → DataType (may be overridden by semantic inference)
```

### Layer 2: Semantic Datatype Inference

Inferred by `_infer_semantic_datatypes()` from query predicates and expressions.

| Source | Priority | Example |
|--------|----------|---------|
| `CAST(x AS DATE)` | 3 | Explicit type cast |
| `DATE(x)`, `DATETIME(x)` | 2 | Temporal functions |
| `x > '2024-01-01'` | 1 | Literal comparison hints |
| `x BETWEEN 'a' AND 'b'` | 1 | Range comparison hints |

**How it works:**

1. Walks all step expressions for CAST, temporal functions, and comparisons
2. Collects `ColumnId → [(priority, DataType)]` candidates
3. Resolves by highest priority; merges compatible types (INT+REAL→REAL)
4. Stamps `PARSEVAL_SEMANTIC_DATATYPE` on column nodes
5. Updates `_parseval_meta.domain` for TEXT columns with temporal semantics

**Important:** Only TEXT columns with temporal semantic hints get their domain
overridden. The solver must still generate strings for TEXT columns (the DB
stores text), so `col.type` stays TEXT while the semantic type informs boundary
value generation.

### Accessing Type Information

```python
# After plan construction and annotation:
for step in plan.ordered_steps:
    for col in _iter_scope_columns(some_expression):
        cid = col.meta.get(PARSEVAL_COLUMN_ID)       # ColumnId identity
        dtype = col.type                                # Physical DataType
        semantic = col.meta.get(PARSEVAL_SEMANTIC_DATATYPE)  # Semantic DataType
        meta = col.args.get("_parseval_meta")          # {table, nullable, unique, domain}
```

### Aggregate Output Types

Aggregate functions get semantic types assigned by `_aggregate_semantic_datatype()`:

| Function | Semantic Type |
|----------|---------------|
| `COUNT(*)` | INT |
| `AVG(x)` | REAL |
| `SUM(x)` | Same as input type, or REAL if unknown |
| `MIN(x)`, `MAX(x)` | Same as input type |

---

## Annotations API

Each step carries a `StepAnnotations` dataclass accessible via the plan.

### Building Annotations

```python
# Lazy — computed on first access
annotations = plan.annotations          # Dict[int, StepAnnotations]
annotation = plan.annotation_for(step)  # StepAnnotations for a specific step
```

### `StepAnnotations` Fields

```python
@dataclass
class StepAnnotations:
    step_id: str                           # "step_0", "step_1", ...
    step_type: str                         # "Scan", "Filter", "Project", ...
    step_name: str                         # Table alias or step name
    condition: Optional[exp.Expression]    # WHERE/HAVING predicate
    projected_columns: Tuple[ColumnId, ...]  # Output column identities
    referenced_columns: Tuple[ColumnId, ...] # All columns referenced in expressions
    source_relations: Tuple[RelationId, ...] # Relations this step reads from
    metadata: Dict[str, Any]               # Step-specific metadata
```

### Step-Specific Metadata

**Aggregate steps:**

```python
metadata["aggregation"] = {
    "group_keys": Tuple[ColumnId, ...],
    "group_expressions": Dict[ColumnId, exp.Expression],
    "group_sources": Dict[ColumnId, Tuple[ColumnId, ...]],
    "aggregate_outputs": Dict[ColumnId, {
        "alias": str,
        "function": str,         # "sum", "count", "avg", ...
        "argument": ColumnId,
        "semantic_datatype": DataType,
    }],
}
```

**Having steps:**

```python
metadata["having_constraints"] = Tuple[{
    "function": str,             # "sum", "count", ...
    "argument": ColumnId,
    "operator": str,             # "gt", "gte", "eq", ...
    "value": Any,                # Literal value
    "semantic_datatype": DataType,
    "required_rows": Optional[int],  # For COUNT constraints
}, ...]
```

**SubPlan steps:**

```python
metadata["subquery"] = {
    "kind": str,                 # "table", "scalar", "exists", "in", "cte"
    "polarity": str,             # "positive" or "negative"
    "cardinality": str,          # "zero", "one", "one_or_more", "many"
    "output_columns": Tuple[ColumnId, ...],
    "correlations": Tuple[{
        "inner": ColumnId,
        "outer": ColumnId,
        "operator": str,
    }, ...],
    "predicate_column": Optional[ColumnId],  # For IN subqueries
}
```

---

## Identity System

### ColumnId

Every column in the plan is stamped with a `ColumnId` that tracks its origin:

```python
@dataclass(frozen=True)
class ColumnId:
    kind: ColumnKind           # PHYSICAL, PROJECTED, DERIVED, AGGREGATE, SYNTHETIC
    name: IdentifierName       # Raw + normalized name
    relation: Optional[RelationId]
    scope_id: Optional[str]    # Unique per-step scope identifier
    ordinal: Optional[int]     # Position in output
    source_column_id: Optional[ColumnId]  # Provenance chain
```

**ColumnKind values:**

| Kind | Meaning |
|------|---------|
| `PHYSICAL` | Column from a real table |
| `PROJECTED` | Column in a SELECT list (may alias a physical column) |
| `DERIVED` | Computed from an expression (e.g., GROUP BY expr) |
| `AGGREGATE` | Output of an aggregate function |
| `SYNTHETIC` | Planner-generated name (e.g., `_g0`, `_a_1`) |

### RelationId

Tables and subqueries are identified by `RelationId`:

```python
@dataclass(frozen=True)
class RelationId:
    kind: RelationKind         # TABLE, CTE, SUBQUERY, VALUES, SYNTHETIC
    name: Optional[IdentifierName]
    catalog: Optional[IdentifierName]
    db: Optional[IdentifierName]
    alias: Optional[IdentifierName]
    scope_id: Optional[str]
```

### Provenance Chain

`source_column_id` traces a column back to its physical origin:

```
Project.output_column_ids[0]
  └── ColumnId(PROJECTED, "a", relation=t)
        └── source_column_id: ColumnId(PHYSICAL, "a", relation=t)
```

This chain enables the solver to look up catalog metadata (nullable, unique,
domain) for any column by walking to its physical source.

---

## Topological Ordering

`plan.ordered_steps` returns a deterministic topological walk of the outer DAG:

```python
plan.ordered_steps  # Tuple[Step, ...]
```

Ordering uses Kahn's algorithm with a stable tie-break on `(type_name, name, id)`
for reproducibility across runs. `SubPlan` nodes appear as leaves because their
`dependencies` set is empty by construction.

---

## Internal Helpers

These functions are not part of the public API but are documented for
contributors:

| Function | Purpose |
|----------|---------|
| `_build_qualifier_index()` | Maps table names/aliases → RelationId |
| `_build_scope_index()` | Builds correlation and scope source maps |
| `_resolve_column_id()` | Resolves Column AST → ColumnId |
| `_resolve_relation_from_scope()` | Resolves table qualifier → RelationId |
| `_enrich_identity_column()` | Stamps catalog metadata on columns |
| `_infer_semantic_datatypes()` | Infers types from predicates |
| `_prepare_step_identity()` | Sets output_column_ids on a step |
| `_build_project_output_columns()` | Computes Project's output identities |
| `_build_aggregate_output_columns()` | Computes Aggregate's output identities |
| `_topological_order()` | Kahn's algorithm for deterministic ordering |

---

## Limitations

1. **Star expansion**: `SELECT *` is not expanded. Downstream consumers must
   resolve star references against the schema.

2. **Ambiguous columns**: Self-joins without explicit table qualifiers raise
   `ValueError`. Use `t1.a` instead of `a`.

3. **Correlated subqueries**: Correlation columns are precomputed at plan build
   time from `traverse_scope`. If sqlglot's scope analysis misses a correlation,
   the planner won't detect it.

4. **Expression normalization**: The planner normalizes `STRFTIME` to
   `TimeToStr`. Other dialect-specific functions may need similar treatment.

5. **Instance requirement**: Full identity resolution and type annotation
   require an `Instance` object. Without it, `ColumnId` and `DataType`
   metadata are not populated.

---

## Example: Walking a Complete Plan

```python
import sqlglot
from parseval.plan.planner import Plan, Scan, Filter, Aggregate, Having, Project, Limit

sql = """
    SELECT DISTINCT t.a, SUM(t.b) AS total
    FROM t AS t
    WHERE t.a > 0
    GROUP BY t.a
    HAVING SUM(t.b) > 10
    ORDER BY t.a
    LIMIT 5
"""

plan = Plan(sqlglot.parse_one(sql, read="sqlite"))

# Top-down walk from root
step = plan.root
while step:
    print(f"{type(step).__name__}: {step.name}")
    if hasattr(step, 'condition') and step.condition:
        print(f"  condition: {step.condition.sql()}")
    if hasattr(step, 'projections'):
        for p in step.projections:
            print(f"  projection: {p.sql()}")
    if hasattr(step, 'limit') and step.limit != float('inf'):
        print(f"  limit: {step.limit}, offset: {step.offset}")

    # Follow chain dependencies (skip SubPlan)
    deps = step.chain_dependencies
    step = deps[0] if deps else None
```

Output:

```
Limit: t
  limit: 5, offset: 0
Project: t
  projection: t.a
  projection: SUM(t.b) AS total
  distinct: True
Sort: t
Having: t
  condition: SUM(t.b) > 10
Aggregate: t
Filter: t
  condition: t.a > 0
Scan: t
```
