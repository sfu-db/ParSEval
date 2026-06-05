# Speculate Gold Non-Empty Witness Design

Date: 2026-05-31

## Objective

Improve `parseval.symbolic.speculate` so it can generate databases that make
more BIRD gold queries return at least one row when run by
`tests/experiment/test_sqlite.py`.

The first target is positive witness generation only. CASE WHEN coverage is
part of this target because CASE output differences can be exposed by rows that
still satisfy the gold query. Disproof-oriented negative, NULL, boundary, and
unmatched-join branch coverage is out of scope until the gold query can be made
non-empty reliably.

## Current Problem

The current speculative path tries to generate many branch classes at once:
positive rows, negative filter branches, NULL branches, boundary rows, and
unmatched join rows. That is too broad for the immediate BIRD failure mode.
CASE WHEN coverage should be handled differently: it belongs in the positive
witness path when CASE expressions appear in projections, filters, grouping,
ordering, or aggregate inputs.

The implementation also decomposes constraints too early. `Resolver` solves one
table at a time with `SolverConstraint(target_tables=(table,))`, then uses
procedural repair for joins, foreign keys, duplicate rows, aggregate thresholds,
and deferred subqueries. This makes multi-table SQL fragile, especially for
queries where non-empty output depends on coordinated rows across joins,
groups, `EXISTS`, `IN`, and aggregate predicates.

## Boundary

The solver module owns expression satisfiability only.

Solver input:

- `SolverConstraint`
- typed `sqlglot.exp.Expression` constraints
- target table names
- optional join equalities or alias mappings needed to interpret expressions

Solver output:

- `SolveResult`
- concrete assignments for expression variables
- `sat=False` when the expression constraints cannot be solved

The solver must not own:

- SQL plan traversal
- deciding which tables need rows
- deciding how many rows are needed
- foreign-key parent discovery
- aggregate witness strategy
- subquery witness strategy
- query execution validation
- BIRD-specific retry policy

The speculate module owns those responsibilities. It reads the annotated plan
top-down, builds the expression constraints needed for a positive witness, asks
the solver to satisfy those expressions, materializes rows, and validates that
the gold query is non-empty.

## Proposed Architecture

Add a positive-witness pipeline inside `speculate`.

### PositiveWitnessBuilder

Walk the planner tree from root to leaves and build a `WitnessSpec` for the
gold query's positive outcome.

The spec records:

- required base tables
- minimum rows per table
- required joins and column equalities
- filter predicates that must evaluate true
- one selected satisfiable arm for `OR`
- one selected satisfiable witness for `IN` and `EXISTS`
- grouping keys that must align across rows
- aggregate lower bounds for `HAVING`
- CASE WHEN arm predicates that should be satisfied by positive output rows
- foreign-key parent table requirements
- projected columns that must be non-null only when null would remove the row

It does not generate negative, null, boundary, or unmatched-join branches.

### ConstraintAssembler

Convert a `WitnessSpec` into solver-ready constraints.

This layer is responsible for:

- resolving aliases to the correct solver namespace
- preserving typed `exp.Column` annotations from planner metadata
- creating column equality expressions for joins
- converting FK dependencies into parent row requirements
- creating per-table or multi-table `SolverConstraint` inputs depending on the
  dependency shape

The preferred first implementation should stay conservative: use one
coordinated solve for a connected join component when possible, and fall back to
table-by-table solving only for independent tables.

### WitnessMaterializer

Materialize solver assignments into the `Instance`.

This layer handles:

- table creation order based on FK dependencies
- filling required parent rows before child rows
- inserting enough rows for joins, groups, limits, and aggregate thresholds
- preserving solver-assigned values instead of overwriting them with heuristic
  defaults

### WitnessValidator

After materialization, run or evaluate the gold query and check whether it
returns at least one row.

Validation is part of speculate's responsibility because satisfying local
constraints is not enough to prove a SQL query is non-empty.

The validation loop should retry within a small budget. On each failed attempt,
it should refine only the positive witness:

- add missing join partner rows
- increase group cardinality for `HAVING COUNT`, `SUM`, or `AVG`
- select a different satisfiable `OR` arm
- populate the inner side of `IN` or `EXISTS`
- select a different satisfiable CASE WHEN arm while preserving non-empty output
- relax non-essential projection `IS NOT NULL` constraints
- avoid NULL generation unless the query specifically requires `IS NULL`

## Public API Shape

Keep the existing `speculate` API but add an objective mode:

```python
speculate(
    plan,
    instance,
    alias_map,
    dialect="sqlite",
    objective="gold_non_empty",
)
```

For `objective="gold_non_empty"`, return only positive witness rows:

```python
[
    ("positive", rows_per_table),
    ("positive_case_0_when_0", rows_per_table),
]
```

The base `"positive"` witness is required. Additional `"positive_case_*"`
witnesses are allowed when the query contains CASE expressions and the witness
still makes the gold query return at least one row.

Existing branch-diverse behavior can remain behind a separate objective such as
`objective="branch_coverage"` or the current default until the experiment is
migrated deliberately.

`tests/experiment/test_sqlite.py` should use `gold_non_empty` for the first
BIRD improvement pass.

## Positive Witness Semantics

A positive witness means:

1. Every scan table needed by the plan has at least one materialized row.
2. Inner join keys have matching values across participating tables.
3. Required filter predicates evaluate true for at least one row combination.
4. `IN` and `EXISTS` subqueries have at least one matching inner row when the
   outer predicate requires one.
5. `GROUP BY` and `HAVING` produce at least one group that survives `HAVING`.
6. CASE WHEN expressions have positive witnesses for satisfiable arms without
   turning the gold result empty.
7. `LIMIT` and `OFFSET` have enough upstream rows to expose at least one output
   row after offset.
8. The executed gold SQL returns at least one row on the generated database.

## Out Of Scope

The first implementation should not try to improve:

- negative branch generation
- NULL branch coverage
- comparison boundary rows
- unmatched outer-join rows
- prediction-vs-gold disproof quality
- solver support for full SQL semantics

Those can be reintroduced after positive witnesses are validated reliably.

## Testing Strategy

Add focused tests before implementation:

- simple single-table filter returns non-empty
- inner join generates matching rows on both sides
- FK child query creates parent rows first
- `WHERE a OR b` selects one satisfiable arm
- `WHERE col IN (SELECT ...)` creates matching outer and inner rows
- `WHERE EXISTS (SELECT ...)` creates a correlated inner row
- `GROUP BY ... HAVING COUNT(*) > N` creates `N + 1` rows in one group
- projected `CASE WHEN` generates positive witnesses for satisfiable CASE arms
- `LIMIT ... OFFSET ...` creates enough upstream rows
- a regression-style BIRD fixture where the current speculate path leaves the
  gold query empty

The key assertion for each test is not just that rows were generated. The test
must execute or evaluate the query and assert that the gold result is non-empty.

## Success Criteria

The design is successful when:

- `gold_non_empty` mode produces only positive witness rows.
- CASE WHEN witnesses are treated as positive rows and each validates non-empty.
- The solver remains a pure expression solver behind `SolverConstraint`.
- Existing solver tests continue to pass.
- New speculate tests validate actual non-empty gold query results.
- Running a sampled subset of BIRD pairs through `tests/experiment/test_sqlite.py`
  shows fewer cases where the gold query remains empty after speculation.

## Implementation Notes

Start by adding the positive-only path alongside the current branch-diverse path
instead of rewriting all of `speculate.py` at once. This keeps the change
surgical and allows the experiment script to opt in.

Prefer reusing planner annotations and existing `exp.Expression` constraints.
Avoid adding BIRD-specific SQL string matching. When a query form is unsupported,
record the reason and let validation fail closed for that attempt.
