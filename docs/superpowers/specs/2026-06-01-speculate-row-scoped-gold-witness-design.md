# Speculate Row-Scoped Gold Witness Design

Date: 2026-06-01

## Objective

Refine `parseval.symbolic.speculate` so `objective="gold_non_empty"` can build
solver constraints for one positive SQL witness, including queries that need
multiple coordinated rows. The generated rows must be materialized through
`Instance`, then validated by checking that the original query returns a
non-empty result.

This is a focused follow-up to the earlier gold/non-empty design. The key
addition is explicit row-scoped solver variables.

## Boundary

The solver remains a pure constraint solver. It exposes only:

- `Solver`
- `SolverConstraint`
- `SolveResult`

Speculate may pass `sqlglot` expressions to the solver, but it must not import
solver internals. The solver should return flat assignments:

```python
{"orders__o__r0.total": 125}
```

Speculate owns all interpretation of that variable name: physical table,
query alias, row index, and materialization into `Instance`.

## Current Problem

The current `Resolver` solves one table row at a time. It adds constraints
against rows already generated for other tables or earlier row indexes. This is
fragile because some SQL witnesses require several rows to be considered
together before solving:

- joins across aliases
- self-joins against the same physical table
- grouped rows that must share group keys
- `HAVING COUNT(*) > n`
- duplicate or distinct witnesses
- correlated `EXISTS` and `IN` subqueries

Solving each row separately forces procedural repair logic into `_solve_row`.
The solver should instead see the interacting rows as distinct variables in one
constraint set whenever those rows affect the same witness.

## Design

Use a small functional seam in `speculate.py`, not a new assembler class.

### Row-Scoped Variable Names

Each logical witness row receives a solver table key:

```text
<physical_table>__<alias_or_table>__r<row_index>
```

Examples:

```text
orders__o__r0.total
orders__o__r1.total
people__manager__r0.id
people__employee__r0.manager_id
```

These keys are solver namespaces only. They are not database table names.

### Transient Row Bindings

During one solve, speculate keeps a local mapping:

```python
{
    "orders__o__r0": RowBinding(table="orders", alias="o", row=0),
}
```

This mapping is short-lived. It exists only to build constraints and decode
flat solver assignments. After rows are created, `Instance` is the source of
truth for generated values.

### Core Functions

Add focused helpers in `speculate.py`:

```python
def build_gold_solver_constraint(spec, instance, alias_map) -> tuple[SolverConstraint, dict]:
    ...

def rewrite_expr_for_row_scope(expr, row_bindings, default_row=0) -> exp.Expression:
    ...

def rows_from_solver_assignments(assignments, row_bindings, instance) -> dict[str, list[dict]]:
    ...

def solve_and_materialize_gold(spec, instance, solver, alias_map, validate) -> dict[str, list[dict]]:
    ...
```

The exact helper names can change during implementation. The important boundary
is that constraint construction and assignment decoding are explicit functions,
while persistent state stays in `Instance`.

### Constraint Construction

`build_gold_solver_constraint` should:

1. Create row bindings from each `TableConstraint.min_rows`.
2. Rewrite each `exp.Column` table qualifier to the appropriate solver table
   key.
3. Preserve column type annotations on rewritten columns.
4. Build `target_tables` from the solver row keys.
5. Build `join_equalities` from row-scoped solver keys.
6. Add row-to-row equality or inequality constraints for grouped, duplicate,
   distinct, and aggregate witnesses when needed.

For a normal filter:

```sql
SELECT * FROM orders o WHERE o.total > 100
```

the solver sees:

```text
orders__o__r0.total > 100
```

For an inner join:

```sql
customers c JOIN orders o ON c.id = o.customer_id
```

the solver sees a join equality:

```python
("customers__c__r0", "id", "orders__o__r0", "customer_id")
```

For `HAVING COUNT(*) > 2`, speculate creates three row bindings for the grouped
table and adds equality constraints on group keys so those rows form one group.

### Materialization Through Instance

After `Solver.solve()` returns flat assignments, speculate decodes them into
physical rows:

```python
{
    "orders": [
        {"total": 125},
        {"total": 140},
    ]
}
```

Rows are then persisted through `Instance.create_row()`, not by mutating solver
results directly. This reuses existing behavior for:

- schema completion
- foreign key parent creation
- unique conflict handling
- symbol registration
- rollback checkpoints

The validation flow should be:

```python
checkpoint = instance.checkpoint()
try:
    create decoded rows through instance.create_row(...)
    if original_query_is_non_empty():
        keep rows
    else:
        instance.rollback(checkpoint)
except Exception:
    instance.rollback(checkpoint)
```

## Incremental Scope

First implementation slice:

- single-table filters
- conjunctions
- inner joins
- self-joins with aliases
- flat solver assignment decoding
- materialization through `Instance`
- query non-empty validation

Later slices:

- `GROUP BY`
- `HAVING COUNT`
- `SUM`, `AVG`, `MIN`, `MAX`
- `IN`
- `EXISTS`
- scalar subqueries
- positive CASE arm witnesses

Branch coverage, negative branches, NULL branches, boundary rows, and unmatched
outer-join witnesses stay out of scope for this gold/non-empty refinement.

## Testing Strategy

Each test should assert the full loop:

```text
schema + SQL -> build gold witness -> materialize through Instance -> original SQL returns non-empty
```

Initial tests:

- simple `WHERE` predicate
- two-table inner join
- self-join requiring distinct aliases
- multi-row `LIMIT/OFFSET` positive witness
- regression that confirms solver flat assignments decode into physical rows

Tests should not assert only that a `SolverConstraint` is satisfiable. A
satisfiable local constraint is not sufficient unless the original query is
non-empty after materialization.
