# Internal Query-Binding Aliases

## Problem

Different SQL scopes may reference the same physical table using the same
visible name. For example:

```sql
SELECT name
FROM races
WHERE name NOT IN (
  SELECT name FROM races WHERE year = 2000
)
```

The outer and inner `races` references are distinct logical bindings but share
the visible qualifier `races`. Physical lineage must map both to the same
schema table, while solver identity must keep them separate.

ParSEval already assigns different `RelationId.scope_id` values to the two
scans. However, diagnostics and solver naming display only the visible table or
alias. A planner traversal gap also skips the complete `IN` expression when it
contains a query, leaving the outer `IN.this` column without its scoped
`ColumnId`. The strict constraint compiler then correctly refuses to guess
between the two bindings.

## Decision

Keep SQL aliases and internal binding aliases separate.

- `RelationId.display` remains the SQL-visible table or alias.
- Add an internal binding label derived from the visible name and `scope_id`.
- Use the internal label for solver-variable display, SMT variable names, and
  alias-space diagnostics.
- Preserve the existing alias-scoped `RelationId` and `ColumnId` as the actual
  equality/hash identity.
- Continue using `ColumnId.source_column_id` and
  `SolverConstraint.storage_relations` for physical persistence.
- Do not rewrite the SQL AST with generated aliases.

Example:

| SQL reference | Internal binding | Physical table |
| --- | --- | --- |
| outer `races.name` | `races@s_outer` | `races.name` |
| inner `races.name` | `races@s_inner` | `races.name` |

## Planner Propagation

`_iter_scope_columns()` must treat an `IN` subquery as two parts:

- traverse `IN.this` in the current step scope;
- do not traverse `IN.query`, because the inner `SubPlan` annotates it in its
  own scope.

This stamps the outer column with the outer scan identity while retaining the
inner column identity produced by the inner plan. Reused explicit aliases must
follow the same rule; identical visible alias text does not imply identical
binding identity.

## Solver Naming

Add a `RelationId.binding_display` property:

```python
visible = relation.alias or relation.name or relation.kind.value
binding_display = f"{visible}@{relation.scope_id}" if relation.scope_id else visible
```

`SolverVar.display` and unified SMT name construction use `binding_display`.
Constraint expressions continue rendering the SQL-visible qualifier, with the
`SolverVar` metadata carrying the internal identity.

The generated label is diagnostic/internal. It does not need to be stable
across separate `Plan` instances; it must only be unique and consistent within
one plan and solve operation.

## Materialization

No materialization redesign is required. Logical rows are already keyed by the
full alias- and scope-aware `RelationId`, while storage resolution follows
physical lineage. Thus outer and inner bindings can become distinct rows in
the same physical table.

## Testing

Add a fixture-independent regression for BIRD query 887. Assert that:

- the outer `races.name` has the outer scan `ColumnId`;
- the inner `races.name` has the inner scan `ColumnId`;
- their `RelationId` values and internal binding labels differ;
- both storage mappings resolve to physical table `races`;
- `instantiate_db()` completes without `UnresolvedScopedColumnError`.

Retain the existing reused-explicit-alias regression and solver self-join
identity tests.

## Success Criteria

- Every solver relation binding is internally distinguishable across SQL
  scopes, even when visible alias text is identical.
- SQL rendering remains unchanged.
- Outer `IN.this` columns are annotated in the outer scope.
- Inner query columns retain inner-scope identities.
- BIRD query 887 generates without `unresolved_scoped_column:"races"."name"`.
