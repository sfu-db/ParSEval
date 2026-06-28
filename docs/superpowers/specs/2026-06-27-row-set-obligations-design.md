# Row-Set Obligations for Final Result Generation

## Problem

Symbolic generation currently treats final-result coverage as a mix of independent scan obligations, branch predicates, join equalities, and aggregate-specific patches. That is not enough for queries where the final row requires a coherent set of upstream rows:

- `LIMIT 5, 1` over a join needs six joined rows that survive the whole plan, not six unrelated rows in each base table.
- Large offsets such as `LIMIT 1 OFFSET 332` require 333 surviving rows; if generation is capped at 20 rows, coverage must remain unmet or be marked deferred rather than reported complete.
- `HAVING COUNT(T2.link_to_event) > 20` needs at least 21 joined input rows in the same group.
- `HAVING COUNT(DISTINCT T4.event_id) > 1` needs at least two distinct non-null values in that group.
- Scalar aggregate subqueries must evaluate their own input scope before outer aggregate replacement.

The quick patch shape of adding special helpers such as `_multi_row_join_constraints()` is rejected. It duplicates plan semantics in `ConstraintGenerator`, handles only one path shape, and will keep missing cases where grouping, ordering, joins, and final output interact.

## Design Goal

Make planner/evaluator structures express row-set requirements directly, then let constraint generation lower those requirements uniformly.

The source of truth remains:

- `Plan` and planner annotations for operator structure and metadata.
- `BranchTree` root-result nodes and `OperatorObligation` for generation requirements.
- `PlanEvaluator` for runtime observations of final output rows and aggregate/subquery values.
- `ConstraintGenerator` only for lowering planner/evaluator-derived obligations into solver constraints.

No standalone witness model is introduced, and `speculate` is not merged into the engine.

## Core Concept

Introduce a planner-derived row-set obligation:

```python
OperatorObligation(
    kind="row_set",
    step_id=...,
    site=...,
    row_count=N,
    row_set=RowSetObligation(...),
)
```

`RowSetObligation` describes a set of logical upstream rows that must survive to a target operator. It is not a new objective model; it is structured metadata attached to existing `OperatorObligation`.

Fields:

- `anchor_step_id`: the operator whose output requires the row set.
- `required_rows`: true semantic row count, for example `offset + limit`.
- `generation_cap`: maximum rows the generator will attempt for this obligation.
- `relations`: base or alias relations participating in each logical row.
- `row_scopes`: deterministic scopes such as `out0`, `out1`, ... where each scope represents one complete logical row through the operator path.
- `join_facts`: planner-derived equality facts that must hold within each logical row scope.
- `path_predicates`: filters and join predicates that each logical row must satisfy before the anchor.
- `group_keys`: optional group-key columns or expressions that all row scopes must share.
- `counted_expression`: optional aggregate input expression for HAVING count requirements.
- `distinct_expression`: optional expression that must be pairwise distinct across row scopes.
- `ordering`: optional ordering expressions needed to make a LIMIT/OFFSET witness deterministic.

## Root Result Semantics

`root_result` coverage uses the true required final row count:

- No `LIMIT`: require one final row.
- `LIMIT n`: require at least one final row when `n > 0`, and generation may choose one unless offset requires more.
- `LIMIT offset, count` or `LIMIT count OFFSET offset`: require `offset + max(count, 1)` surviving upstream rows.
- If the true requirement exceeds the cap, scan/materialization obligations use the cap, but root-result coverage threshold stays at the true requirement. The target remains uncovered unless the evaluator observes the true number of final rows or the engine explicitly marks it deferred.

`PlanEvaluator._record_root_result()` records one observation per final output row. Coverage never treats a single final row as satisfying a multi-row root requirement.

## HAVING Cardinality Semantics

Planner HAVING metadata should describe direct aggregate predicates and alias-based predicates the same way:

- `COUNT(*) > N` -> `required_rows=N+1`
- `COUNT(col) > N` -> `required_rows=N+1`, `counted_expression=col`, non-null required
- `COUNT(DISTINCT col) > N` -> `required_rows=N+1`, `counted_expression=col`, non-null and pairwise distinct required

The planner should attach this metadata whether the HAVING expression is `HAVING count_alias > N` or `HAVING COUNT(col) > N`.

Constraint generation lowers HAVING cardinality through a `row_set` obligation:

- Each row scope represents one joined aggregate input row.
- All row scopes satisfy the same group-key expressions.
- Join equalities are applied inside each row scope.
- Filter and join predicates upstream of the aggregate are applied inside each row scope.
- `COUNT(col)` requires `col IS NOT NULL` in each row scope.
- `COUNT(DISTINCT col)` additionally requires pairwise `col_i <> col_j`.

This avoids special casing one counted column against one static join partner.

## Scalar Aggregate Subqueries

Aggregate expression evaluation must resolve scalar subqueries before replacing outer aggregate functions.

Required behavior:

- If an aggregate output expression contains a scalar subquery, attach the subquery to the aggregate step during planning or make it visible to the aggregate evaluator through existing subplan dependencies.
- During evaluation, resolve scalar subqueries against their inner plan first.
- Replace only aggregate functions that belong to the current aggregate input scope.
- The outer expression then evaluates against the outer aggregate row.

For the index 614 shape:

```sql
COUNT(T1.Id) / (SELECT COUNT(Id) FROM users)
```

the outer `COUNT(T1.Id)` uses outer join rows, while the scalar subquery `COUNT(Id)` uses the `users` subquery plan.

## Constraint Lowering

`ConstraintGenerator` should lower row-set obligations in one shared path:

1. Build row scopes from the obligation, not ad hoc relation counts.
2. For each logical row scope, apply relation scan existence constraints for every participating relation.
3. For each logical row scope, lower join facts using the same row scope on both sides of the joined tuple.
4. For each logical row scope, lower upstream predicates using that row scope.
5. For group obligations, add equality constraints across row scopes for group-key expressions.
6. For distinct count obligations, add pairwise inequality for the distinct expression.
7. Apply database constraints after row scopes are established so PK/FK/unique logic uses the same scope model.

Multi-row final-result and HAVING cardinality targets compile through `row_set`. The older independent `scan_exists` path is not a fallback for these targets.

## Deferred or Capped Requirements

The current cap should remain a generation budget, not a truth value.

If `required_rows > generation_cap`:

- The row-set obligation records both numbers.
- Constraint generation may generate only `generation_cap` rows.
- Root-result coverage threshold remains `required_rows`.
- The engine may mark the target deferred/capped if it cannot legally generate enough rows within budget.
- Coverage must not become `1.0` solely because the capped row set was generated.

## Tests

Focused regression coverage should include:

- `LIMIT 5, 1` over a join produces six coherent joined rows or stays uncovered/deferred when capped.
- `LIMIT 1 OFFSET 332` does not report full root-result coverage under the default cap.
- `HAVING COUNT(T2.link_to_event) > 20` over a join materializes 21 same-group joined rows and evaluates to a non-empty final result.
- `HAVING COUNT(DISTINCT T4.event_id) > 1` materializes at least two distinct non-null event IDs in one group.
- Scalar aggregate ratio query evaluates without `KeyError` and returns a non-empty final result when satisfiable.

Verification commands:

```bash
pytest tests/symbolic/test_operator_flow_paths.py -q
pytest tests/symbolic/test_constraint_generation.py -q
```

The BIRD slice should be run after focused tests:

```text
50, 57, 614, 720, 1322, 1323, 1381, 1451
```

The slice is diagnostic, not a replacement for targeted assertions.

## Implementation Boundary

Do not implement row-set semantics by layering more special-case helpers into `ConstraintGenerator`. The implementation should first add the row-set obligation representation and update tests to fail against that contract, then lower the shared representation.

Keep changes scoped to:

- planner metadata and obligation construction,
- branch-tree coverage thresholds and root observations,
- evaluator scalar-subquery aggregate scope,
- constraint lowering for row-set obligations,
- focused symbolic tests.

Do not refactor `speculate` or introduce a separate witness/objective model.
