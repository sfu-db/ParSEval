# Speculate Identity Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `speculate.py` to use the identity-based solver interface (`SolverVar`, `RelationId`, `ColumnId`) instead of string-based column resolution.

**Architecture:** Replace all string-based column creation with `_solver_column` (annotates `exp.Column` with `SolverVar` + type). Unify gold and branch coverage into a single global solving path via `Resolver.resolve()`. Remove self-join special-casing, temporal special-casing, and per-table solving.

**Tech Stack:** Python, sqlglot, parseval.solver, parseval.identity

**Spec:** `docs/superpowers/specs/2026-06-10-speculate-identity-rewrite-design.md`

---

### Task 1: Add Identity-Aware Column Helpers

**Files:**
- Modify: `src/parseval/symbolic/speculate.py` (imports + helpers section)

- [ ] **Step 1: Add new imports**

At the top of `speculate.py`, add the solver types to the existing import block:

```python
from parseval.solver import Solver, SolverConstraint, SolverVar, set_solver_var, solver_var
```

Also add `col_type` from `parseval.solver.types`:

```python
from parseval.solver.types import col_type
```

- [ ] **Step 2: Add `_solver_column` helper**

Add this function after the existing `_make_typed_column` function (around line 127):

```python
def _solver_column(
    instance: Instance,
    table: str,
    col_name: str,
    row_scope: str | None = None,
) -> exp.Column:
    """Create a Column annotated with SolverVar + type from the instance schema."""
    rel = _relation_for_table(instance, table)
    col_id = column_id(ColumnKind.PHYSICAL, identifier_name(col_name), rel)
    var = SolverVar(column_id=col_id, relation_id=rel, row_scope=row_scope)
    col = exp.column(col_name, table)
    set_solver_var(col, var)
    col_type_str = _lookup_col_type(instance, rel, col_name)
    if col_type_str:
        try:
            col.type = DataType.build(col_type_str)
        except Exception:
            pass
    return col
```

- [ ] **Step 3: Add `_ensure_solver_var` helper**

Add after `_solver_column`:

```python
def _ensure_solver_var(col: exp.Column, instance: Instance) -> None:
    """Ensure a Column has SolverVar metadata. Reads identity from planner annotations."""
    if solver_var(col) is not None:
        return
    col_id = column_identity(col)
    if col_id is None or col_id.relation is None:
        return
    var = SolverVar(column_id=col_id, relation_id=col_id.relation)
    set_solver_var(col, var)
    if col_type(col) is None:
        table = col_id.relation.name.normalized if col_id.relation.name else ""
        matched = col_id.name.normalized
        if table and matched:
            _annotate_col_type(col, instance, col_id.relation, matched)
```

- [ ] **Step 4: Add `_solver_var_for_binding` helper**

Add after `_ensure_solver_var`:

```python
def _solver_var_for_binding(
    instance: Instance,
    binding: RowBinding,
    col_name: str,
) -> SolverVar:
    """Create a SolverVar for a specific row binding and column."""
    col_id = column_id(ColumnKind.PHYSICAL, identifier_name(col_name), binding.relation)
    return SolverVar(
        column_id=col_id,
        relation_id=binding.relation,
        row_scope=f"r{binding.row}",
    )
```

- [ ] **Step 5: Run existing solver identity tests to verify imports work**

Run: `pytest tests/solver/test_solver_identity.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "feat(speculate): add identity-aware column helpers"
```

---

### Task 2: Update Resolver to Build Global SolverConstraint

**Files:**
- Modify: `src/parseval/symbolic/speculate.py` (Resolver class)

- [ ] **Step 1: Add `_build_global_constraint` method to Resolver**

Add this method to the `Resolver` class, replacing the existing `_solve_row` and `_solve_boundary_row` methods:

```python
def _build_global_constraint(
    self,
    spec: BranchSpec,
) -> Tuple[SolverConstraint, Dict[str, RowBinding]]:
    """Build a single SolverConstraint for all tables in the spec."""
    row_bindings = _build_gold_row_bindings(spec)
    constraints: list[exp.Expression] = []
    variables: dict[SolverVar, DataType] = {}

    for table_key, req in spec.requirements.items():
        req_bindings = _bindings_for_requirement(table_key, req, row_bindings)
        if not req_bindings:
            continue
        for constraint in req.constraints:
            if constraint.find(exp.Subquery):
                continue
            # Skip cross-table column EQ — handled via join_equalities
            if (
                isinstance(constraint, exp.EQ)
                and isinstance(constraint.this, exp.Column)
                and isinstance(constraint.expression, exp.Column)
            ):
                continue
            for binding in req_bindings:
                rewritten = _rewrite_constraint_for_binding(
                    constraint, binding, self.instance,
                )
                if rewritten is not None:
                    constraints.append(rewritten)
                    _collect_solver_vars(rewritten, variables)

        # Boundary rows
        for b_idx, boundary in enumerate(req.boundary_rows):
            binding = RowBinding(relation=req.relation, row=1000 + b_idx)
            row_bindings[_solver_table_key(binding)] = binding
            for col_name, val in boundary.items():
                col = _solver_column(self.instance, req.table, col_name, row_scope=f"r{binding.row}")
                constraints.append(exp.EQ(this=col, expression=to_literal(val)))
                variables[solver_var(col)] = col_type(col)

    join_equalities = _build_join_equalities(spec, row_bindings, self.instance)
    for left_var, right_var in join_equalities:
        variables[left_var] = _dtype_for_solver_var(left_var, self.instance)
        variables[right_var] = _dtype_for_solver_var(right_var, self.instance)

    target_relations = tuple(
        binding.relation for binding in row_bindings.values()
    )
    return SolverConstraint(
        target_relations=target_relations,
        constraints=constraints,
        join_equalities=join_equalities,
        variables=variables,
    ), row_bindings
```

- [ ] **Step 2: Add `_rewrite_constraint_for_binding` helper function**

Add this as a module-level function (near the existing `_rewrite_expr_for_row_scope`):

```python
def _rewrite_constraint_for_binding(
    constraint: exp.Expression,
    binding: RowBinding,
    instance: Instance,
) -> exp.Expression | None:
    """Rewrite a constraint expression for a specific row binding.

    Creates new Column nodes with SolverVar metadata scoped to the binding.
    Returns None if the constraint references tables not in this binding.
    """
    rewritten = constraint.copy()
    has_columns = False
    for col in list(rewritten.find_all(exp.Column)):
        # Resolve the physical table for this column
        col_id = column_identity(col)
        if col_id is not None and col_id.relation is not None:
            physical = col_id.relation.name.normalized
            col_name = col_id.name.normalized
        else:
            physical = _table_name_for_column(col, instance)
            col_name = col.name

        if physical != binding.table:
            continue

        has_columns = True
        # Create a new annotated column for this binding
        new_col = _solver_column(instance, physical, col_name, row_scope=f"r{binding.row}")
        # Preserve the type from the original if it had one
        orig_type = getattr(col, "type", None)
        if orig_type is not None and getattr(new_col, "type", None) is None:
            new_col.type = orig_type
        col.replace(new_col)

    if not has_columns:
        return None
    return rewritten
```

- [ ] **Step 3: Add `_build_join_equalities` helper function**

```python
def _build_join_equalities(
    spec: BranchSpec,
    row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> List[Tuple[SolverVar, SolverVar]]:
    """Convert ColumnUnionFind equivalences to SolverVar pairs."""
    equalities: list[Tuple[SolverVar, SolverVar]] = []
    seen: set[tuple[SolverVar, SolverVar]] = set()
    for _rep, members in spec.equivalences.groups().items():
        if len(members) < 2:
            continue
        # Find bindings for each member
        member_bindings: list[tuple[ColumnId, RowBinding]] = []
        for member in members:
            table_name = member.relation.name.normalized if member.relation and member.relation.name else ""
            col_name = member.name.normalized
            binding = _find_binding_for_column(table_name, row_bindings)
            if binding is not None:
                member_bindings.append((member, binding))
        # Create equalities between consecutive members
        for i in range(len(member_bindings) - 1):
            m1, b1 = member_bindings[i]
            m2, b2 = member_bindings[i + 1]
            v1 = _solver_var_for_binding(instance, b1, m1.name.normalized)
            v2 = _solver_var_for_binding(instance, b2, m2.name.normalized)
            pair = (v1, v2)
            if pair not in seen:
                seen.add(pair)
                equalities.append(pair)
    return equalities
```

- [ ] **Step 4: Add `_find_binding_for_column` helper**

```python
def _find_binding_for_column(
    table_name: str,
    row_bindings: Dict[str, RowBinding],
) -> RowBinding | None:
    """Find the first row binding for a physical table name."""
    for binding in row_bindings.values():
        if binding.table == table_name:
            return binding
    return None
```

- [ ] **Step 5: Add `_collect_solver_vars` helper**

```python
def _collect_solver_vars(
    expr: exp.Expression,
    variables: dict[SolverVar, DataType],
) -> None:
    """Collect SolverVar + DataType pairs from all columns in an expression."""
    for col in expr.find_all(exp.Column):
        var = solver_var(col)
        dtype = col_type(col)
        if var is not None and dtype is not None:
            variables[var] = dtype
```

- [ ] **Step 6: Add `_dtype_for_solver_var` helper**

```python
def _dtype_for_solver_var(
    var: SolverVar,
    instance: Instance,
) -> DataType:
    """Look up the DataType for a SolverVar from the instance schema."""
    table = var.relation_id.name.normalized if var.relation_id.name else ""
    col_name = var.column_id.name.normalized
    col_type_str = _lookup_col_type(instance, var.relation_id, col_name)
    if col_type_str:
        try:
            return DataType.build(col_type_str)
        except Exception:
            pass
    return DataType.build("TEXT")
```

- [ ] **Step 7: Update Resolver.resolve() to use global constraint**

Replace the existing `Resolver.resolve` method:

```python
def resolve(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
    """Produce concrete rows for each table in the spec using global solving."""
    constraint, row_bindings = self._build_global_constraint(spec)
    result = self.solver.solve(constraint)
    if result.sat:
        rows = _rows_from_solver_result(result.assignments, row_bindings, self.instance)
    else:
        logger.warning(
            "Solver failed for spec=%s reason=%s; using fallback",
            spec.branch, result.reason,
        )
        rows = _fallback_rows(spec, self.instance, row_bindings)

    if not rows:
        rows = _fallback_rows(spec, self.instance, row_bindings)
    if not rows:
        return {}

    rows = _complete_gold_rows(rows, row_bindings, spec, self.instance)
    _satisfy_gold_scalar_subqueries(spec, self.plan, rows, self.instance, self.dialect)

    try:
        _materialize_rows(self.instance, rows)
        return rows
    except Exception as exc:
        logger.debug("materialization failed for spec=%s: %s", spec.branch, exc)
        return {}
```

- [ ] **Step 8: Add `_rows_from_solver_result` helper**

```python
def _rows_from_solver_result(
    assignments: dict[SolverVar, Any],
    row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> Dict[str, List[Dict[str, Any]]]:
    """Extract concrete rows from solver assignments keyed by SolverVar."""
    # Group by (table, row_index)
    cells: dict[tuple[str, int], dict[str, Any]] = {}
    for var, value in assignments.items():
        if not isinstance(var, SolverVar):
            continue
        table = var.relation_id.name.normalized if var.relation_id.name else ""
        col_name = var.column_id.name.normalized
        row_scope = var.row_scope or "r0"
        try:
            row_idx = int(row_scope.lstrip("r"))
        except ValueError:
            row_idx = 0
        # Skip boundary rows (row_idx >= 1000)
        if row_idx >= 1000:
            continue
        cells.setdefault((table, row_idx), {})[col_name] = value

    rows: Dict[str, List[Dict[str, Any]]] = {}
    for (table, _row_idx), values in sorted(cells.items()):
        rows.setdefault(table, []).append(values)
    return rows
```

- [ ] **Step 9: Add `_fallback_rows` helper**

```python
def _fallback_rows(
    spec: BranchSpec,
    instance: Instance,
    row_bindings: Dict[str, RowBinding],
) -> Dict[str, List[Dict[str, Any]]]:
    """Build rows using heuristic values when the solver fails."""
    rows: Dict[str, List[Dict[str, Any]]] = {}
    for _key, req in spec.requirements.items():
        physical = req.table
        if physical not in instance.tables:
            continue
        for row_index in range(max(req.min_rows, 1)):
            row: Dict[str, Any] = _extract_fixed_values(req.constraints)
            for col_name in instance.tables[physical]:
                if col_name in row:
                    continue
                try:
                    row[col_name] = instance.builder.generate_value(
                        physical, col_name, row_context=row,
                    )
                except Exception:
                    pass
            rows.setdefault(physical, []).append(row)
    return rows
```

- [ ] **Step 10: Update Resolver.__init__ to accept plan**

Update the Resolver constructor to accept the plan (needed for `_satisfy_gold_scalar_subqueries`):

```python
def __init__(
    self,
    plan: Plan,
    instance: Instance,
    dialect: str = "sqlite",
    solver=None,
):
    self.plan = plan
    self.instance = instance
    self.dialect = dialect
    self.solver = solver
```

- [ ] **Step 11: Run tests**

Run: `pytest tests/solver/test_solver_identity.py tests/solver/test_solver.py tests/solver/test_domain.py -v`
Expected: PASS

- [ ] **Step 12: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "feat(speculate): Resolver uses global SolverConstraint"
```

---

### Task 3: Update Propagator to Use Identity-Based Column Creation

**Files:**
- Modify: `src/parseval/symbolic/speculate.py` (Propagator class)

- [ ] **Step 1: Update `_store_expression` to annotate columns**

In the `_store_expression` method, add a call to `_ensure_solver_var` for each column after resolving:

```python
def _store_expression(self, expr: exp.Expression, spec: BranchSpec):
    """Decompose AND, resolve columns, store per-table."""
    conjuncts = self._split_conjuncts(expr)
    for conjunct in conjuncts:
        if conjunct.find(exp.Exists) or conjunct.find(exp.Subquery):
            spec.deferred.append(conjunct.copy())
            continue
        # Resolve column table qualifiers to physical names.
        resolved = self._resolve_columns(conjunct.copy())
        # Ensure all columns have SolverVar metadata.
        for col in resolved.find_all(exp.Column):
            _ensure_solver_var(col, self.instance)
        table = self._find_table_for_expr(resolved)
        if table:
            tc = spec.require(table)
            tc.constraints.append(resolved)
```

- [ ] **Step 2: Update `_add_schema_constraints` to use `_solver_column`**

Replace `_make_typed_column` calls in `_add_schema_constraints` with `_solver_column`:

In the NOT NULL section:
```python
# Before:
col_node = _make_typed_column(self.instance, rel, col_name)
tc.constraints.append(_make_is_not_null(col_node))

# After:
col_node = _solver_column(self.instance, table, col_name)
tc.constraints.append(_make_is_not_null(col_node))
```

In the UNIQUE section:
```python
# Before:
col_node = exp.column(col_name, table)

# After:
col_node = _solver_column(self.instance, table, col_name)
```

In the FK section:
```python
# Before:
col_node = exp.column(fk_col, table)

# After:
col_node = _solver_column(self.instance, table, fk_col)
```

- [ ] **Step 3: Update `_apply_null_overrides` to use `_solver_column`**

Replace `_make_typed_column` calls with `_solver_column`:

```python
# Before:
col_node = _make_typed_column(self.instance, tc.relation, col_name)

# After:
col_node = _solver_column(self.instance, tc.table, col_name)
```

- [ ] **Step 4: Update `_apply_single_null_override` to use `_solver_column`**

Same replacement pattern:

```python
# Before:
col_node = _make_typed_column(self.instance, target_rel, target_col)

# After:
col_node = _solver_column(self.instance, target_table, target_col)
```

- [ ] **Step 5: Update HAVING value constraints to use `_solver_column`**

In `_extract_agg_value_from_expr`, replace `_make_typed_column`:

```python
# Before:
col_node = _make_typed_column(self.instance, relation, matched)

# After:
col_node = _solver_column(self.instance, table, matched)
```

- [ ] **Step 6: Update `_add_null_constraint_for_col` to use `_solver_column`**

```python
# Before:
col_node = _make_typed_column(self.instance, relation, matched)

# After:
col_node = _solver_column(self.instance, table, matched)
```

- [ ] **Step 7: Update join key creation to use `_solver_column`**

In the Join handling section of `_propagate_step`, replace `exp.column()` calls with `_solver_column`:

```python
# Before:
eq_expr = exp.EQ(
    this=exp.column(sk_matched, sk_rel.name.normalized if sk_rel.name else ""),
    expression=exp.column(jk_matched, jk_rel.name.normalized if jk_rel.name else ""),
)

# After:
sk_table = sk_rel.name.normalized if sk_rel.name else ""
jk_table = jk_rel.name.normalized if jk_rel.name else ""
eq_expr = exp.EQ(
    this=_solver_column(self.instance, sk_table, sk_matched),
    expression=_solver_column(self.instance, jk_table, jk_matched),
)
```

- [ ] **Step 8: Update Project IS NOT NULL to use `_solver_column`**

In the Project handling section:

```python
# Before:
col_node = _make_typed_column(self.instance, table, matched)
tc.constraints.append(_make_is_not_null(col_node))

# After:
col_node = _solver_column(self.instance, table, matched)
tc.constraints.append(_make_is_not_null(col_node))
```

- [ ] **Step 9: Update Aggregate NULL detection to use `_solver_column`**

In the Aggregate handling section (gold mode path):

```python
# Before:
col_node = _make_typed_column(self.instance, rel, matched)
req.constraints.append(_make_is_not_null(col_node))

# After:
col_node = _solver_column(self.instance, table, matched)
req.constraints.append(_make_is_not_null(col_node))
```

- [ ] **Step 10: Run tests**

Run: `pytest tests/solver/test_solver_identity.py tests/solver/test_solver.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "feat(speculate): Propagator uses identity-based column creation"
```

---

### Task 4: Remove Dead Code and Self-Join Special-Casing

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Remove `_extract_temporal_age_constraints` method and its call**

Delete the `_extract_temporal_age_constraints` method from the Propagator class (lines ~1975-2012).

Remove its call from `_store_expression`:
```python
# Remove this line from _store_expression:
self._extract_temporal_age_constraints(expr, spec)
```

- [ ] **Step 2: Remove self-join detection from Propagator.__init__**

Remove from `Propagator.__init__`:
```python
# Remove:
self._alias_map: Dict[str, str] = {}
self._self_join_tables: Set[str] = set()
physical_counts: Dict[str, int] = {}
for step in plan.ordered_steps:
    if not isinstance(step, Scan):
        continue
    # ... the entire alias_map building loop
```

- [ ] **Step 3: Remove `_find_self_join_tables` method**

Delete the method entirely.

- [ ] **Step 4: Remove `_store_conjunct_for_self_join` method**

Delete the method entirely.

- [ ] **Step 5: Remove self-join call from `_store_expression`**

Remove from `_store_expression`:
```python
# Remove:
if self._store_conjunct_for_self_join(conjunct, spec):
    continue
```

- [ ] **Step 6: Remove per-table solving methods from Resolver**

Delete from the Resolver class:
- `_solve_row`
- `_solve_boundary_row`
- `_equivalences_to_join_equalities`
- `_creation_order`
- `_discover_fk_parents`
- `_annotate_col_type` (instance method — the module-level one stays)
- `_drop_subquery_constraints`
- `_minimal_non_null_row`

- [ ] **Step 7: Remove `_solve_and_materialize_gold` and `_solve_and_materialize_branch_coverage`**

Delete both functions entirely.

- [ ] **Step 8: Remove `_try_heuristic_fallback` and `_heuristic_gold_rows`**

Delete both functions entirely.

- [ ] **Step 9: Remove `_gold_domain_value` function**

Delete entirely — `_complete_gold_rows` uses `instance.builder.generate_value` directly.

- [ ] **Step 10: Remove `_solve_scalar_witness_values` helper chain**

Delete:
- `_solve_scalar_witness_values`
- `_solve_scalar_witness_with_domain_values`
- `_domain_scalar_witness_assignments`
- `_domain_scalar_column_values`

These are replaced by the solver handling scalar subquery constraints directly.

- [ ] **Step 11: Remove `_gold_fk_columns` function**

Delete — FK handling is now in the constraint builder.

- [ ] **Step 12: Run tests**

Run: `pytest tests/solver/test_solver_identity.py tests/solver/test_solver.py tests/solver/test_domain.py -v`
Expected: PASS

- [ ] **Step 13: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): remove dead code and self-join special-casing"
```

---

### Task 5: Update `speculate()` Top-Level Function

**Files:**
- Modify: `src/parseval/symbolic/speculate.py` (speculate function)

- [ ] **Step 1: Simplify `speculate()` function**

Replace the existing `speculate()` function:

```python
def speculate(
    plan: Plan,
    instance: Instance,
    dialect: str = "sqlite",
    config: Optional[SpeculateConfig] = None,
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    """One-call API: propagate + resolve -> list of (branch_name, rows_per_table).

    Returns one entry per branch (positive + negatives). The engine
    materializes each one.
    """
    if config is None:
        config = SpeculateConfig.gold_non_empty()

    propagator = Propagator(plan, instance, dialect, config=config)
    solver = Solver(dialect=dialect)
    resolver = Resolver(plan, instance, dialect, solver=solver)

    branch_specs = propagator.propagate()
    logger.info("Generated %d branch specs", len(branch_specs))

    results = []
    for spec in branch_specs:
        if not spec.requirements:
            continue
        try:
            rows = resolver.resolve(spec)
        except Exception as exc:
            logger.debug("spec %s failed: %s", spec.branch, exc)
            rows = {}
        if rows:
            results.append((spec.branch, rows))
    return results
```

- [ ] **Step 2: Update `__all__` exports**

Ensure `__all__` includes the new types if needed. The current `__all__` is fine — no new public types.

- [ ] **Step 3: Run tests**

Run: `pytest tests/solver/ -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): simplify speculate() top-level function"
```

---

### Task 6: Integration Verification

**Files:**
- Test: `tests/solver/test_solver_identity.py`
- Test: `tests/solver/test_solver.py`
- Test: `tests/solver/test_domain.py`
- Test: `tests/solver/test_smt.py`

- [ ] **Step 1: Run full solver test suite**

Run: `pytest tests/solver/ -v`
Expected: All PASS

- [ ] **Step 2: Run identity tests**

Run: `pytest tests/solver/test_solver_identity.py -v`
Expected: All 3 tests PASS

- [ ] **Step 3: Run plan tests**

Run: `pytest tests/plan/ -v`
Expected: All PASS

- [ ] **Step 4: Run symbolic tests**

Run: `pytest tests/symbolic/ -v`
Expected: All PASS

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v --timeout=60`
Expected: All PASS (or only pre-existing failures)

- [ ] **Step 6: Final commit if any fixes needed**

```bash
git add -A
git commit -m "fix(speculate): integration test fixes"
```
