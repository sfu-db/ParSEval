# speculate.py Refactor Design

## Problem

`speculate.py` (~3500 lines) re-derives column identity and type information that the planner already computes. The file has ~23 instances of a string-based fallback pattern:

```python
col_id = column_identity(col)
if col_id and col_id.relation:
    relation = col_id.relation
    matched = col_id.name.normalized
else:
    relation = self._rel(col.table or "")  # string-based fallback
    matched = col.name
```

This causes correctness issues when the planner's identity resolution and the speculator's independent resolution diverge. The `Propagator` class is also a ~1900-line god class with a 300-line `_propagate_step` method that handles every step type in a single `isinstance` chain.

## Goals

1. **Correctness**: Eliminate all string-based identity/type re-derivation. Every `exp.Column` entering a constraint must carry `PARSEVAL_COLUMN_ID` from the planner.
2. **Modularity**: Decompose the `Propagator` into per-step-type handler methods, each independently testable.
3. **Clarity**: Separate branch-type orchestration (positive/negative/null/having_fail) from per-step constraint derivation.

## Non-Goals

- Adding new query types (window functions, recursive CTEs, etc.)
- Changing the solver module
- Changing the public `speculate()` function signature
- Changing `BranchSpec`, `TableConstraint`, `ColumnUnionFind`, or `Resolver`

## Architecture

Three layers:

```
Orchestrator (Propagator.propagate)
  - Manages branch types (positive, negative, null, having_fail, case_else)
  - Dispatches to step handlers
  - Post-processes: schema constraints, type annotation

Step Handlers (Propagator._derive_<step_type>)
  - One method per plan step type
  - Receives step + BranchSpec
  - Derives table requirements using planner identity

Resolver (unchanged)
  - BranchSpec -> SolverConstraint -> concrete rows
```

## Step Handler Decomposition

The `Propagator._propagate_step` method is replaced by a dispatch table:

```python
_HANDLER_MAP = {
    Scan: "_derive_scan",
    Filter: "_derive_filter",
    Join: "_derive_join",
    Aggregate: "_derive_aggregate",
    Having: "_derive_having",
    Project: "_derive_project",
    Sort: "_derive_sort",
    Limit: "_derive_limit",
    SetOperation: "_derive_set_op",
}
```

Each handler method:

```python
def _derive_<type>(self, step: <StepType>, spec: BranchSpec) -> None:
    """Derive table requirements from this step."""
    # 1. Process this step's constraints
    # 2. Recurse into chain dependencies
    # 3. Handle subplan dependencies
```

Negation context is stored on the Propagator instance (not passed as parameters):

```python
self._negate_step: Optional[Step] = None
self._negate_conjunct: int = 0
```

The walk method becomes:

```python
def _walk_step(self, step: Step, spec: BranchSpec) -> None:
    handler_name = self._HANDLER_MAP.get(type(step))
    if handler_name:
        getattr(self, handler_name)(step, spec)
    for dep in step.chain_dependencies:
        self._walk_step(dep, spec)
    for sub in step.subplan_dependencies:
        self._derive_subplan(sub, spec, parent_condition=step.condition)
```

Only `_derive_filter` and `_derive_having` read `self._negate_step` to decide whether to negate.

### Handler Responsibilities

| Handler | Step Type | What It Does |
|---------|-----------|--------------|
| `_derive_scan` | `Scan` | Register table in `spec.requirements`. Recurse into FROM-subquery inner plan. |
| `_derive_filter` | `Filter` | Store WHERE conditions, extract column equalities, defer scalar subquery atoms. Handle negation when `step is negate_step`. |
| `_derive_join` | `Join` | Link join keys via `spec.equate()`, store join equality expressions. |
| `_derive_aggregate` | `Aggregate` | Mark group key columns, add aggregate NULL constraints. |
| `_derive_having` | `Having` | Store HAVING conditions, extract min group size, handle scalar HAVING constraints. Handle negation. |
| `_derive_project` | `Project` | Add IS NOT NULL for projected columns, handle DISTINCT duplicate detection. |
| `_derive_limit` | `Limit` | Set `min_rows` on the driving table. |
| `_derive_sort` | `Sort` | No-op (passes through to dependencies). |
| `_derive_set_op` | `SetOperation` | Passes through to dependencies. |
| `_derive_subplan` | `SubPlan` | Handle EXISTS/IN/SCALAR correlation and inner plan propagation. |

## Identity-First Resolution

### Planner Prerequisite

The planner must stamp `PARSEVAL_COLUMN_ID` on every `exp.Column` that flows into the speculator. Current gaps:

1. **Synthetic aliases** (`_g0`, `_h`, `_a_0`): The planner creates these during `Step.from_expression` but doesn't always stamp identity on the alias columns in conditions/projections.
2. **Join key columns**: `Join.joins[source_key]` and `Join.joins[join_key]` columns may lack identity.
3. **Subquery correlation columns**: Columns in `SubPlan.correlation` may lack identity.

Fix: extend `_prepare_step_identity` / `_resolve_column_id` in `planner.py` to cover these cases.

### Speculator Changes

Once the planner guarantees identity on all columns:

- **Remove `_rel(name: str)`**: No more string-to-RelationId resolution. Use `column_identity(col).relation` directly.
- **Remove `_lookup_col_type`**: Types come from `column_meta(col)["domain"]` (set by the planner's `_enrich_identity_column`).
- **Remove `_build_alias_map`**: Alias info lives in `RelationId.alias`.
- **Remove `_ensure_solver_var`**: Columns already carry identity; read it and create `SolverVar` directly.
- **Simplify `_solver_column`**: Takes `ColumnId` + `RelationId` directly, creates `exp.Column` with `SolverVar` + type.
- **Remove all fallback patterns**: Replace `if col_id ... else string_fallback` with a fail-fast check. If `column_identity(col)` returns `None`, raise a `ValueError` with the column's SQL text. This surfaces planner identity gaps immediately instead of silently falling back to string matching.

### Speculator-Created Expressions

When the speculator creates new expressions (negated predicates, IS NULL, join equalities), it copies from existing columns that already carry identity. The identity propagates through `.copy()`.

## Branch-Type Orchestration

The `propagate()` method becomes a clean orchestrator:

```python
def propagate(self) -> List[BranchSpec]:
    specs = []

    if self.config.positive > 0:
        pos = BranchSpec(branch="positive")
        self._walk_plan(pos)
        specs.append(pos)

    if self.config.negative > 0:
        for step in self._filter_steps():
            for idx in range(len(self._split_conjuncts(step.condition))):
                neg = BranchSpec(branch=f"negative_c{idx}")
                self._negate_step = step
                self._negate_conjunct = idx
                self._walk_plan(neg)
                specs.append(neg)
    self._negate_step = None
    self._negate_conjunct = 0

    # ... left_unmatched, right_unmatched, having_fail, null, case_else

    for spec in specs:
        self._add_schema_constraints(spec)
        self._annotate_column_types(spec)

    return specs
```

Each branch-type method (`_filter_steps`, `_unmatched_join_specs`, `_having_fail_specs`, `_null_specs`, `_case_else_specs`) is a small focused method.

## What Gets Deleted

| Item | Reason |
|------|--------|
| `_build_alias_map` | Alias info comes from `RelationId` |
| `_lookup_col_type` | Types come from `column_meta` |
| `_ensure_solver_var` | Columns already carry identity |
| `_rel(name: str)` | No string-to-RelationId resolution |
| All `if col_id ... else string_fallback` patterns | Identity guaranteed by planner |
| `ColumnUnionFind.same` | Dead code (already removed) |
| `ColumnUnionFind.members` | Dead code (already removed) |
| `SpeculateConfig.from_thresholds` | Dead code (already removed) |
| `SpeculateConfig.should_generate` | Dead code (already removed) |
| `Resolver._rel` | Dead code (already removed) |
| `Resolver._alias_map` | Dead code (already removed) |

## What Stays Unchanged

- `BranchSpec`, `TableConstraint`, `ColumnUnionFind`, `RowBinding` data structures
- `SpeculateConfig` (minus dead methods)
- `Resolver` class (minus dead methods)
- `speculate()` public API
- Helper functions: `_make_is_not_null`, `_make_is_null`, `_has_is_not_null`, `_has_is_null`, `_has_equality_constraint`, `_extract_fixed_values`
- Row materialization: `_complete_gold_rows`, `_materialize_rows`, `_gold_materialization_order`
- Fallback generation: `_fallback_rows`, `_gold_domain_value`
- Scalar subquery handling: `_satisfy_gold_scalar_subqueries`, `_solve_scalar_witness_values`
- Evaluation validation: `_gold_candidate_has_output`, `_gold_has_positive_evaluator_observations`

## File Structure (Post-Refactor)

```
speculate.py
  Schema helpers         (~20 lines)  - _table_name only
  Constraint helpers     (~80 lines)  - _make_is_not_null, _has_*, _extract_fixed_values
  Data structures        (~100 lines) - RowBinding, ColumnUnionFind, TableConstraint, BranchSpec
  SpeculateConfig        (~50 lines)
  Propagator             (~800 lines) - orchestrator + handlers + schema/NULL/boundary/HAVING helpers
  Resolver               (~150 lines) - unchanged
  Row helpers            (~300 lines) - bindings, rewriting, extraction, completion, materialization
  Scalar subquery        (~200 lines) - satisfaction logic
  Evaluation validation  (~50 lines)
  Public API             (~30 lines)
  Total                  (~1800 lines, down from ~3500)
```

## Implementation Order

1. **Planner prerequisite**: Stamp identity on synthetic aliases, join keys, subquery columns
2. **Remove string fallbacks**: Replace all `if col_id ... else` with identity-only path
3. **Decompose Propagator**: Extract `_propagate_step` into `_derive_<type>` methods
4. **Clean up orchestrator**: Extract branch-type methods from `propagate()`
5. **Remove dead helpers**: `_build_alias_map`, `_lookup_col_type`, `_ensure_solver_var`, `_rel`
6. **Verify**: Run full test suite, check that all existing tests pass

## Risks

1. **Planner gaps**: If the planner can't stamp identity on some columns (edge cases in complex subqueries), the speculator will fail fast instead of falling back silently. This is the desired behavior for correctness, but may surface new bugs.
2. **Regression surface**: Removing fallbacks may break queries that currently work by accident through the string-based path. Each break indicates a planner identity gap that needs fixing.
