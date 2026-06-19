# Alias-Scoped Solver Materialization Design

## Problem

ParSEval uses alias-scoped plan identities to distinguish SQL bindings, but
constraint materialization currently groups solver assignments by physical
storage relation and row scope. When two aliases refer to the same physical
table, distinct solver variables can collapse into one output row. The last
assignment for a column name then wins.

BIRD toxicology query 247 exposes the failure. Its outer `atom AS T` and inner
`atom AS T1` bindings both originate from `atom`, while `connected AS T2`
originates from `connected`. Missing identity metadata on the inner join
columns is currently resolved against the outer relation set. Both
`T1.atom_id` and `T2.atom_id` can therefore become synthetic `T.atom_id`
variables. During materialization, a synthetic null assignment overwrites the
physical non-null assignment for `atom.atom_id`, and SQLite rejects the row.

## Goals

- Use SQL aliases and query scope to distinguish solver variables.
- Preserve canonical physical table and column lineage for persistence.
- Materialize different aliases of one physical table as different rows.
- Never merge rows merely because a join equates some of their values.
- Reject ambiguous identities and conflicting assignments instead of choosing
  an assignment by iteration order.
- Reject explicit nulls for non-nullable columns before database persistence.

## Non-goals

- Change the solver's responsibility from satisfying expressions to managing
  database state.
- Introduce a second identity hierarchy alongside `RelationId` and `ColumnId`.
- Refactor unrelated planner, evaluator, or instance behavior.
- Infer that two aliases denote one row from value equality alone.

## Identity Contract

Every materializable solver variable has two independent identities:

1. **Logical binding identity** identifies the SQL relation binding, column,
   query scope, and logical row slot. It is represented by the alias-scoped
   `RelationId`, alias-scoped `ColumnId`, and `SolverVar.row_scope`.
2. **Physical lineage** identifies the destination schema table and column. It
   is represented by `ColumnId.source_column_id` and the existing
   `SolverConstraint.storage_relations` mapping.

For the motivating query, the mappings are:

| SQL reference | Logical solver identity | Physical destination |
| --- | --- | --- |
| `T.atom_id` | outer scope, alias `T`, `atom_id`, row 0 | `atom.atom_id` |
| `T1.atom_id` | subquery scope, alias `T1`, `atom_id`, row 0 | `atom.atom_id` |
| `T2.atom_id` | subquery scope, alias `T2`, `atom_id`, row 0 | `connected.atom_id` |

The first two variables share physical lineage but are not equal identities.

## Constraint Compilation

Planner-provided column identity is authoritative. Constraint compilation must
preserve it when copying expressions.

When a column lacks identity metadata, fallback resolution must operate in the
column's owning SQL scope. The resolver must use the qualifier-to-relation map
for that scope, including subquery-local aliases. It must not resolve an inner
qualified column using the first or only outer relation.

A qualified column that cannot be resolved in its owning scope makes the
constraint unsupported. Compilation must fail closed rather than manufacturing
a `ColumnKind.SYNTHETIC` identity against an unrelated physical relation.
Synthetic identities remain valid for genuinely derived values that have no
direct schema-column identity. Such values are materializable only when they
carry explicit physical lineage.

Database constraints such as `IS NOT NULL` must use the same alias-scoped
physical `ColumnId` as the corresponding query column. This prevents one
logical cell from appearing as separate physical and synthetic solver
variables.

## Solver Contract

`SolverVar` remains a pure logical variable:

```python
SolverVar(
    column_id=alias_scoped_column_id,
    relation_id=alias_scoped_relation_id,
    row_scope=logical_row_slot,
)
```

The solver does not consult the `Instance` and does not decide where values are
persisted. Join equalities relate solver values but do not merge logical row
identities.

## Materialization

Assignments must first be grouped by logical row identity:

```text
(binding_relation, row_scope)
```

Each logical group then resolves its physical destination through
`storage_relations`. This produces separate rows for separate aliases even
when their storage relation is identical.

Within a logical row, each assignment resolves its destination column using:

```python
storage_column = var.column_id.source_column_id or var.column_id
```

The materializer must reject a variable when:

- no physical storage relation can be determined;
- the column is synthetic or derived and has no physical lineage;
- variables in one logical row map to the same physical column with different
  values; or
- variables in one logical row resolve to different physical tables.

The materializer must not silently overwrite an earlier assignment.

## Instance Validation

`Instance.create_row()` remains responsible for completing omitted values.
Omission and explicit null are different inputs:

- an omitted non-nullable column may be generated by `DatabaseBuilder`;
- an explicitly supplied `None` for a non-nullable column is invalid and must
  raise before row insertion into the in-memory instance.

This validation is a defense in depth. Correct constraint identity and
materialization must prevent the invalid assignment in the first place.

## Error Handling

Identity-resolution and materialization conflicts must return an unsuccessful
generation attempt with a specific reason. They must not be converted into a
partially populated row. Suggested reason categories are:

- `unresolved_scoped_column`
- `missing_physical_lineage`
- `conflicting_materialized_assignment`
- `explicit_null_for_non_nullable_column`

The exact exception types or result representation may follow existing local
patterns, but tests must assert the distinguishing reason rather than a generic
database-write failure.

## Testing

Add focused regressions before changing implementation:

1. Reproduce BIRD toxicology query 247 and inspect the compiled constraint.
   Assert that `T`, `T1`, and `T2` retain distinct relation bindings and that
   each solver variable has the correct physical lineage.
2. Materialize the query's solution. Assert that `T` and `T1` create separate
   `atom` rows, `T2` creates a `connected` row, and no primary key is null.
3. Persist the generated instance to SQLite and execute the query without an
   integrity error.
4. Cover a plain self-join where two aliases of one physical table require two
   physical rows.
5. Cover nested scopes that reuse the same alias text; the scope identifier
   must keep the variables distinct.
6. Cover two assignments that target one physical cell with different values;
   materialization must fail instead of using last-write-wins.
7. Cover explicit `None` for a non-nullable column at the `Instance.create_row`
   boundary.

Run the focused constraint, solver-identity, instance, and BIRD regression
tests first, followed by the broader symbolic and instance test suites.

## Success Criteria

- No inner qualified column is resolved through an unrelated outer alias.
- Alias-scoped solver variables retain canonical physical lineage.
- Distinct aliases materialize as distinct physical rows by default.
- Equal join values do not unify row identity.
- Materialization has no last-write-wins collision behavior.
- Invalid non-null values fail before `InstanceLoader` reaches SQLite.
- BIRD toxicology query 247 persists without the `atom.atom_id` integrity
  error.
