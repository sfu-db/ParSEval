# Speculate Module Identity Rewrite

**Date:** 2026-06-10
**Status:** Proposed
**Scope:** `src/parseval/symbolic/speculate.py` only

## Problem

The speculate module was designed around the old solver interface (string table names, plain `exp.Expression` constraints). The new solver requires:

- `SolverConstraint.target_relations: Tuple[RelationId, ...]` (not string table names)
- Every `exp.Column` in constraints must carry `SolverVar` metadata (`solver_var(col)`) and a type annotation (`col_type(col)`)
- `join_equalities: List[Tuple[SolverVar, SolverVar]]` (not `(str, str, str, str)` tuples)

The current speculate module fails on every call to the new solver because `_validate_types` rejects unannotated columns.

## Design

### Approach: Identity-First Rewrite

Replace all string-based column resolution in speculate.py with the identity system (`ColumnId`, `RelationId`, `SolverVar`) the planner already provides.

### Section 1: SolverVar-Aware Column Creation

Add two helpers that replace `_make_typed_column`:

**`_solver_column(instance, table, col_name, row_scope=None) -> exp.Column`**
Creates a `Column` annotated with both `SolverVar` and type. Builds `ColumnId` from `(ColumnKind.PHYSICAL, col_name, relation_id)`, wraps in `SolverVar(column_id, relation_id, row_scope)`, attaches via `set_solver_var(col, var)`, and sets `col.type` from the instance schema.

**`_ensure_solver_var(col, instance) -> None`**
For columns that already exist in the plan (annotated by the planner with `PARSEVAL_COLUMN_ID`). Reads `column_identity(col)` to get the `ColumnId`, creates a `SolverVar`, and attaches it. Falls back to `_annotate_col_type` if the type is missing.

Replaces:
- `_make_typed_column` (used in ~15 places)
- `_annotate_col_type` (kept as fallback only)
- `_relation_for_column` / `_match_column` (replaced by `column_identity(col)`)

### Section 2: Propagator Identity Integration

The Propagator's column resolution switches from string-based to identity-based:

**Current flow:** `col -> _relation_for_column(col) -> str -> _match_column(rel, name) -> str -> _make_typed_column(rel, name)`

**New flow:** `col -> column_identity(col) -> ColumnId -> ColumnId.relation -> RelationId (direct) -> _solver_column(table, name)`

Key changes:
1. **`_store_expression`**: Call `_ensure_solver_var` on each column from step conditions
2. **`_resolve_columns`**: Simplified — reads `column_identity(col)` for physical table name
3. **Join handling**: Build join EQ expressions via `_solver_column`, `spec.equate(ColumnId, ColumnId)` stays
4. **Schema constraints** (`_add_schema_constraints`): NOT NULL, UNIQUE, FK all use `_solver_column`
5. **NULL branch**: IS NULL/IS NOT NULL use `_solver_column`
6. **HAVING value constraints**: Per-row EQ uses `_solver_column`
7. **Remove `_extract_temporal_age_constraints`** — solver handles YEAR/STRFTIME natively

### Section 3: Resolver → Global Solving

Unify gold mode and branch coverage into a single global solving path.

**`Resolver.resolve(spec) -> rows`:**
1. `_build_global_constraint(spec, instance)` → `(SolverConstraint, row_bindings)`
2. `solver.solve(constraint)` → `SolveResult`
3. `_rows_from_solver_assignments(result, row_bindings, instance)` → `Dict[str, List[Dict]]`
4. `_complete_gold_rows(rows, row_bindings, spec, instance)` → filled rows
5. `_satisfy_gold_scalar_subqueries(spec, plan, rows, instance, dialect)`
6. `_materialize_rows(instance, rows)`

**`_build_global_constraint` method:**
- Builds `RowBinding` objects for every `(relation, row_index)` from `min_rows`
- For each binding, creates `_solver_column` for every constraint in the `TableConstraint`, scoped via `row_scope=f"r{row_index}"`
- For `boundary_rows`: adds a separate binding per boundary row (with `row_scope=f"b{idx}"`) and adds EQ constraints for the boundary column values
- Collects `SolverVar` pairs for join equalities from `ColumnUnionFind.groups()`
- Returns `SolverConstraint(target_relations, constraints, join_equalities, variables)`

**`_rows_from_solver_assignments` method:**
- Iterates `SolveResult.assignments` (keyed by `SolverVar`)
- Maps `SolverVar.relation_id.name.normalized` → table name
- Maps `SolverVar.row_scope` → row index (`"r0"` → 0)
- Maps `SolverVar.column_id.name.normalized` → column name

**Removed:**
- `Resolver._solve_row` (per-table solving)
- `Resolver._solve_boundary_row` (merged into global)
- `Resolver._equivalences_to_join_equalities` (merged into constraint builder)
- `Resolver._creation_order` (no longer needed)
- `Resolver._discover_fk_parents` (discovered during constraint building)

### Section 4: Row Scope and SolverVar Mapping

**Row scope convention:** `"r0"`, `"r1"`, `"r2"`, ... (matching row index in `min_rows`)

**`RowBinding`** stays as internal mapping from `(relation, row_index)` to solver variables.

**`_solver_var_for_binding(instance, binding, col_name) -> SolverVar`:**
Creates `SolverVar(column_id=column_id(PHYSICAL, name, binding.relation), relation_id=binding.relation, row_scope=f"r{binding.row}")`

**Self-join handling:** Removed entirely. The identity system handles it naturally:
- Each alias gets its own `RelationId(name=physical, alias=alias_name)`
- Each alias's columns get distinct `ColumnId` objects
- Distinct `SolverVar` objects → solver treats them as separate variables
- No synthetic `"t__t1"` keys, no `_store_conjunct_for_self_join`, no `_alias_map`

### Section 5: Cleanup

**Removed from speculate.py:**
- `_extract_temporal_age_constraints` (solver handles YEAR/STRFTIME)
- `_find_self_join_tables` / `_store_conjunct_for_self_join` (identity handles self-joins)
- `_solve_row` / `_solve_boundary_row` (per-table solving, replaced by global)
- `_equivalences_to_join_equalities` (merged into constraint builder)
- `_creation_order` / `_discover_fk_parents` (no longer needed)
- `_solve_and_materialize_gold` / `_solve_and_materialize_branch_coverage` (merged into `Resolver.resolve`)
- `_try_heuristic_fallback` / `_heuristic_gold_rows` (solver handles fallback)
- `_gold_domain_value` builder-based fallback
- String-based `_relation_for_column` / `_match_column` (replaced by identity)
- `_alias_map` / `_self_join_tables` tracking in Propagator

**Kept and updated:**
- `Propagator` — identity-aware column creation
- `Resolver` — global solving orchestration
- `_complete_gold_rows` — fills missing columns via `instance.builder.generate_value` directly (not through removed `_gold_domain_value`)
- `_satisfy_gold_scalar_subqueries` — deferred scalar subquery handling
- `_materialize_rows` / `_gold_materialization_order` — FK-ordered materialization
- `BranchSpec`, `TableConstraint`, `ColumnUnionFind`, `SpeculateConfig`, `RowBinding`

## API Contract

### Inputs (unchanged)
```python
speculate(plan: Plan, instance: Instance, dialect: str, config: SpeculateConfig)
```

### Outputs (unchanged)
```python
List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]
# [(branch_name, {table_name: [{col: val, ...}, ...]}), ...]
```

### Solver Contract
```python
SolverConstraint(
    target_relations=(RelationId, ...),
    constraints=[exp.Expression],  # every Column has SolverVar + type
    join_equalities=[(SolverVar, SolverVar)],
    variables={SolverVar: DataType},
)
```

## Verification

1. All existing tests in `tests/solver/test_solver.py`, `tests/solver/test_domain.py`, `tests/solver/test_smt.py` pass unchanged
2. All existing tests in `tests/plan/test_rex.py` pass unchanged
3. Speculate-specific tests pass (if any exist)
4. Manual verification: `speculate()` returns non-empty rows for a simple SELECT with WHERE, JOIN, GROUP BY, HAVING
