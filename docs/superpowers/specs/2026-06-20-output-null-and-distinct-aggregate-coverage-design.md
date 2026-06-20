# Output NULL and DISTINCT-Aggregate Coverage

## Goal

Extend the symbolic branch tree so coverage includes value behavior at final
projection and aggregate outputs, not only predicate outcomes. Coverage is
tracked per output expression. DISTINCT aggregate functions additionally track
the input-set behaviors that affect SQL aggregation.

The feature is enabled by default and remains configurable through the existing
`CoverageThresholds` object.

## Coverage Model

### Project outputs

Each `Project` step owns one `project_output` branch node. Its `atoms` are the
final projection expressions in output order, so `atom_id` is the output
ordinal and identical SQL expressions in different output positions remain
separate targets.

Each projection supports:

- `PROJECT_NULL`: the evaluated output value is NULL;
- `PROJECT_NON_NULL`: the evaluated output value is not NULL.

Observations use the projected output row identity. DISTINCT processing does
not change the value classification; project-output observations are recorded
from the evaluated projection values before duplicate rows are removed.

### Aggregate outputs

Each `Aggregate` step owns one `aggregate_output` branch node. Its `atoms` are
the final aggregate output expressions in output order, and `atom_id` is the
aggregate output ordinal.

Each nullable aggregate output supports:

- `AGGREGATE_NULL`: the final aggregate expression evaluates to NULL;
- `AGGREGATE_NON_NULL`: the final aggregate expression evaluates non-NULL.

Direct `COUNT(...)` and `COUNT(DISTINCT ...)` outputs do not receive an
`AGGREGATE_NULL` target because SQL returns a non-NULL integer for them.
Other expressions receive both targets unless the planner can prove the NULL
outcome impossible. Unsupported or impossible targets continue through the
existing dynamic infeasibility mechanism.

Observations use the aggregate output row identity, which is also the group
identity for grouped aggregation and the synthetic global aggregate identity
for ungrouped aggregation.

### DISTINCT aggregate inputs

Every DISTINCT aggregate function creates an `aggregate_distinct_input` node.
The node belongs to the containing aggregate step and identifies the function
by its position within the final aggregate output expression. This prevents two
DISTINCT functions with identical argument SQL from collapsing.

Each DISTINCT aggregate input supports:

- `AGG_DISTINCT_NULL_IGNORED`: at least one contributing input is NULL;
- `AGG_DISTINCT_DUPLICATE_ELIMINATED`: at least two contributing non-NULL
  inputs are equal;
- `AGG_DISTINCT_MULTIPLE_RETAINED`: at least two contributing non-NULL inputs
  are different.

These outcomes describe the input multiset, independent of the final aggregate
result. They apply to `COUNT`, `SUM`, `AVG`, `MIN`, and `MAX` when the function
uses DISTINCT.

## Branch Identity and Observations

`BranchNode.site` remains a semantic site label. Per-expression identity uses
`atom_id`; DISTINCT aggregate functions additionally require a stable function
ordinal in the node identity so repeated expressions do not merge.

All expressions stored on nodes are live planner expressions carrying
`ColumnId` metadata. Constraint generation must preserve those identities when
copying expressions and must not resolve output columns by textual table or
column names.

Project observations are recorded for every output row. Aggregate and
DISTINCT-input observations are recorded once per aggregate group. Empty
global aggregation still produces one aggregate-output observation.

## Configuration

Add these fields to `CoverageThresholds`, all defaulting to `1`:

```python
project_null: int = 1
project_non_null: int = 1
aggregate_null: int = 1
aggregate_non_null: int = 1
aggregate_distinct_null_ignored: int = 1
aggregate_distinct_duplicate_eliminated: int = 1
aggregate_distinct_multiple_retained: int = 1
```

`threshold_for()` and branch-tree target enumeration map each new branch type
to its field. A value of `0` disables that outcome globally, matching existing
threshold behavior.

## Constraint Generation

### Project targets

For direct columns and derived projection expressions, copy the planner-owned
expression and constrain it with `IS NULL` or `IS NOT NULL`. The target retains
the upstream path predicates, join facts, storage mappings, and row scope.

### Aggregate-result targets

For `SUM`, `AVG`, `MIN`, and `MAX`:

- NULL requires a contributing row whose aggregate argument is NULL, with all
  generated contributors for that target constrained NULL;
- non-NULL requires at least one contributing row whose argument is non-NULL.

For direct `COUNT`, only the non-NULL result target exists. Derived aggregate
expressions are constrained through their constituent aggregate arguments;
unsupported expression shapes fail closed and are marked dynamically
infeasible rather than receiving guessed values.

### DISTINCT-input targets

- NULL-ignored uses one contributing row with a NULL argument.
- Duplicate-eliminated uses two row scopes with equal, non-NULL arguments.
- Multiple-retained uses two row scopes with unequal, non-NULL arguments.

Group keys and upstream predicates are shared across the generated contributor
rows so they participate in the same aggregate group. Physical persistence
continues through `SolverConstraint.storage_relations`.

## Testing

Add focused tests for:

1. Two projected columns producing independent NULL/non-NULL targets.
2. Identical projection SQL in different ordinals remaining separate.
3. A nullable direct column observing both project outcomes.
4. A derived projection expression observing NULL.
5. `SUM`, `AVG`, `MIN`, and `MAX` observing aggregate NULL and non-NULL.
6. Direct `COUNT` exposing only aggregate non-NULL.
7. Grouped aggregate observations remaining separate per group.
8. `COUNT(DISTINCT x)` observing NULL ignored, duplicate eliminated, and
   multiple values retained.
9. A non-COUNT DISTINCT aggregate receiving the same input behavior targets
   plus its aggregate-result NULL/non-NULL targets.
10. Threshold fields independently disabling each new outcome.
11. End-to-end generation materializing the required one- or two-row witness
    without losing scoped `ColumnId` or physical storage lineage.

## Success Criteria

- Branch-tree coverage exposes project NULL/non-NULL per final output ordinal.
- Aggregate NULL/non-NULL is tracked per final aggregate output ordinal.
- Direct COUNT outputs never request an impossible NULL target.
- DISTINCT aggregates track NULL, duplicate, and multiple-distinct input
  behavior per function.
- All seven new thresholds default to one and remain independently
  configurable.
- Constraint generation uses planner identity exclusively and preserves group,
  row-scope, and physical-lineage semantics.
