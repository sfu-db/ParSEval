# Row-Scoped Gold Witnesses Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a gold/non-empty speculate path that solves coordinated multi-row witnesses with row-scoped solver variables, materializes through `Instance`, and validates the original SQL returns rows.

**Architecture:** Keep solver semantics isolated: `Solver.solve()` returns flat variable assignments, while `speculate.py` owns alias, row, and physical-table interpretation. Add small helper functions in `speculate.py` instead of a new assembler class; use `Instance.create_row()` and checkpoint/rollback as the persistent generated-value store.

**Tech Stack:** Python dataclasses, `sqlglot.exp`, `pytest`, existing `parseval.solver` public API, existing `Instance` row creation and validation helpers.

---

## Files

- Modify: `src/parseval/symbolic/speculate.py`
  - Import solver through `parseval.solver`.
  - Add row-scoped binding helpers.
  - Add gold/non-empty solve-and-materialize helper functions.
  - Wire `objective="gold_non_empty"` through the new helper for the first slice.
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`
  - Add row-scoped helper tests and end-to-end gold/non-empty tests for filters, joins, and self-joins.

---

## Precondition

`Solver.solve()` already returns flat assignments shaped as
`{"solver_table_key.column": value}`. This plan must not modify
`src/parseval/solver` or solver tests.

---

### Task 1: Add Row-Scoped Binding Helpers In Speculate

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`

- [ ] **Step 1: Write row-key unit tests**

Add these tests to `tests/symbolic/test_speculate_gold_non_empty.py`:

```python
def test_row_scoped_solver_key_includes_table_alias_and_row():
    from parseval.symbolic.speculate import RowBinding, _solver_table_key

    binding = RowBinding(table="orders", alias="o", row=2)

    assert _solver_table_key(binding) == "orders__o__r2"


def test_rows_from_flat_solver_assignments_decodes_physical_rows():
    from parseval.symbolic.speculate import RowBinding, _rows_from_solver_assignments

    schema = "CREATE TABLE orders (id INT PRIMARY KEY, total INT);"
    instance = Instance(ddls=schema, name="decode_rows", dialect="sqlite")
    bindings = {
        "orders__o__r0": RowBinding(table="orders", alias="o", row=0),
        "orders__o__r1": RowBinding(table="orders", alias="o", row=1),
    }
    assignments = {
        "orders__o__r0.id": 1,
        "orders__o__r0.total": 125,
        "orders__o__r1.id": 2,
        "orders__o__r1.total": 140,
    }

    rows = _rows_from_solver_assignments(assignments, bindings, instance)

    assert rows == {
        "orders": [
            {"id": 1, "total": 125},
            {"id": 2, "total": 140},
        ]
    }
```

- [ ] **Step 2: Run the failing row-key tests**

Run:

```bash
pytest tests/symbolic/test_speculate_gold_non_empty.py::test_row_scoped_solver_key_includes_table_alias_and_row tests/symbolic/test_speculate_gold_non_empty.py::test_rows_from_flat_solver_assignments_decodes_physical_rows -q
```

Expected: imports fail because the helpers do not exist.

- [ ] **Step 3: Add helper dataclass and decoder functions**

In `src/parseval/symbolic/speculate.py`, add this near the existing data structures:

```python
@dataclass(frozen=True)
class RowBinding:
    """Transient mapping from a solver table key to one physical witness row."""
    table: str
    alias: Optional[str]
    row: int


def _solver_table_key(binding: RowBinding) -> str:
    alias = normalize_name(binding.alias or binding.table)
    table = normalize_name(binding.table)
    return f"{table}__{alias}__r{binding.row}"


def _split_solver_variable(name: str) -> Tuple[str, str]:
    if "." not in name:
        return "", normalize_name(name)
    table_key, column = name.rsplit(".", 1)
    return normalize_name(table_key), normalize_name(column)


def _rows_from_solver_assignments(
    assignments: Dict[str, Any],
    row_bindings: Dict[str, RowBinding],
    instance: Instance,
) -> Dict[str, List[Dict[str, Any]]]:
    rows_by_slot: Dict[Tuple[str, int], Dict[str, Any]] = {}
    for variable_name, value in assignments.items():
        table_key, column = _split_solver_variable(variable_name)
        binding = row_bindings.get(table_key)
        if binding is None:
            continue
        schema = instance.tables.get(binding.table)
        if schema is None or column not in schema:
            continue
        rows_by_slot.setdefault((binding.table, binding.row), {})[column] = value

    rows: Dict[str, List[Dict[str, Any]]] = {}
    for (table, _row_index), values in sorted(rows_by_slot.items()):
        rows.setdefault(table, []).append(values)
    return rows
```

Do not add these helper names to `__all__`; the tests import them directly from
the module and they remain private implementation helpers.

- [ ] **Step 4: Run row-key tests**

Run:

```bash
pytest tests/symbolic/test_speculate_gold_non_empty.py::test_row_scoped_solver_key_includes_table_alias_and_row tests/symbolic/test_speculate_gold_non_empty.py::test_rows_from_flat_solver_assignments_decodes_physical_rows -q
```

Expected: both tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "feat: add row-scoped speculate bindings"
```

---

### Task 2: Rewrite Expressions Into Row-Scoped Solver Variables

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`

- [ ] **Step 1: Write expression rewrite tests**

Add:

```python
def test_rewrite_expr_for_row_scope_preserves_column_type():
    from parseval.symbolic.speculate import RowBinding, _rewrite_expr_for_row_scope

    col = exp.column("total", "o")
    col.type = exp.DataType.build("INT")
    expr = exp.GT(this=col, expression=exp.Literal.number(100))
    bindings = {
        "orders__o__r0": RowBinding(table="orders", alias="o", row=0),
    }

    rewritten = _rewrite_expr_for_row_scope(expr, bindings, {"o": "orders"})
    rewritten_col = next(rewritten.find_all(exp.Column))

    assert rewritten_col.table == "orders__o__r0"
    assert rewritten_col.name == "total"
    assert rewritten_col.type is not None
```

- [ ] **Step 2: Run the failing rewrite test**

Run:

```bash
pytest tests/symbolic/test_speculate_gold_non_empty.py::test_rewrite_expr_for_row_scope_preserves_column_type -q
```

Expected: import fails because `_rewrite_expr_for_row_scope` does not exist.

- [ ] **Step 3: Add row-scope rewrite helpers**

In `src/parseval/symbolic/speculate.py`, add:

```python
def _physical_table_for_alias(alias_or_table: str, alias_map) -> str:
    key = normalize_name(alias_or_table)
    if hasattr(alias_map, "resolve"):
        resolved = alias_map.resolve(key)
        return normalize_name(resolved or key)
    if hasattr(alias_map, "get"):
        return normalize_name(alias_map.get(key, key))
    return key


def _binding_for_column(
    col: exp.Column,
    row_bindings: Dict[str, RowBinding],
    alias_map,
    default_row: int = 0,
) -> Optional[RowBinding]:
    raw_table = normalize_name(col.table or "")
    physical = _physical_table_for_alias(raw_table, alias_map) if raw_table else ""
    for binding in row_bindings.values():
        if binding.row != default_row:
            continue
        if raw_table and normalize_name(binding.alias or "") == raw_table:
            return binding
        if physical and normalize_name(binding.table) == physical:
            return binding
    return None


def _rewrite_expr_for_row_scope(
    expr: exp.Expression,
    row_bindings: Dict[str, RowBinding],
    alias_map,
    default_row: int = 0,
) -> exp.Expression:
    rewritten = expr.copy()
    for col in rewritten.find_all(exp.Column):
        binding = _binding_for_column(col, row_bindings, alias_map, default_row)
        if binding is None:
            continue
        old_type = getattr(col, "type", None)
        col.set("table", exp.to_identifier(_solver_table_key(binding)))
        if old_type is not None:
            col.type = old_type
    return rewritten
```

- [ ] **Step 4: Run rewrite test**

Run:

```bash
pytest tests/symbolic/test_speculate_gold_non_empty.py::test_rewrite_expr_for_row_scope_preserves_column_type -q
```

Expected: test passes.

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "feat: rewrite speculate constraints by row scope"
```

---

### Task 3: Build And Materialize A Single-Table Gold Constraint

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`

- [ ] **Step 1: Write single-table materialization test**

Add:

```python
def test_gold_non_empty_materializes_single_table_filter_through_instance():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
    sql = "SELECT id FROM t WHERE val > 5"
    instance, plan = _plan(sql, schema)

    results = speculate(
        plan,
        instance,
        plan.alias_map,
        dialect="sqlite",
        objective="gold_non_empty",
    )

    assert results
    assert results[0][0] == "positive"
    assert instance.get_rows("t")
    assert _execute_candidate_rows(instance, sql, {}) 
```

- [ ] **Step 2: Run the failing materialization test**

Run:

```bash
pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_materializes_single_table_filter_through_instance -q
```

Expected: fails if current `speculate` returns candidate rows without persisting them or still expects nested solver assignments.

- [ ] **Step 3: Add build and materialize helpers**

In `src/parseval/symbolic/speculate.py`, import public solver API:

```python
from parseval.solver import SolverConstraint
```

Add:

```python
def _build_gold_row_bindings(spec: BranchSpec) -> Dict[str, RowBinding]:
    bindings: Dict[str, RowBinding] = {}
    for table_key, req in spec.requirements.items():
        physical = normalize_name(req.table.split("__", 1)[0] if "__" in req.table else req.table)
        if req.alias:
            alias = normalize_name(req.alias)
        elif "__" in table_key:
            alias = normalize_name(table_key.split("__", 1)[1])
        else:
            alias = physical
        for row_index in range(max(req.min_rows, 1)):
            binding = RowBinding(table=physical, alias=alias, row=row_index)
            bindings[_solver_table_key(binding)] = binding
    return bindings


def _build_gold_solver_constraint(
    spec: BranchSpec,
    instance: Instance,
    alias_map,
) -> Tuple[SolverConstraint, Dict[str, RowBinding]]:
    row_bindings = _build_gold_row_bindings(spec)
    constraints: List[exp.Expression] = []
    for req in spec.requirements.values():
        for constraint in req.constraints:
            if constraint.find(exp.Subquery):
                continue
            rewritten = _rewrite_expr_for_row_scope(constraint, row_bindings, alias_map)
            constraints.append(rewritten)
    return SolverConstraint(
        target_tables=tuple(row_bindings.keys()),
        constraints=constraints,
        join_equalities=[],
    ), row_bindings


def _materialize_rows(instance: Instance, rows: Dict[str, List[Dict[str, Any]]]) -> None:
    for table_name in instance._creation_order({table: {col: [row.get(col) for row in table_rows] for col in instance.tables.get(table, {})} for table, table_rows in rows.items()}):
        for row in rows.get(table_name, []):
            instance.create_row(table_name, values=row)
```

Add a gold solve helper:

```python
def _solve_and_materialize_gold(
    spec: BranchSpec,
    plan: Plan,
    instance: Instance,
    solver,
    alias_map,
    dialect: str,
) -> Dict[str, List[Dict[str, Any]]]:
    constraint, row_bindings = _build_gold_solver_constraint(spec, instance, alias_map)
    result = solver.solve(constraint)
    if not result.sat:
        return {}
    rows = _rows_from_solver_assignments(result.assignments, row_bindings, instance)
    checkpoint = instance.checkpoint()
    try:
        _materialize_rows(instance, rows)
        if validate_gold_non_empty_rows(plan, instance, {}, dialect=dialect):
            return rows
        instance.rollback(checkpoint)
        return {}
    except Exception:
        instance.rollback(checkpoint)
        return {}
```

- [ ] **Step 4: Wire gold objective through helper for positive spec**

In `speculate(...)`, in the `objective == "gold_non_empty"` branch, use:

```python
    if objective == "gold_non_empty":
        branch_specs = propagator.propagate_gold_non_empty()
        results = []
        for spec in branch_specs:
            rows = _solve_and_materialize_gold(
                spec,
                plan,
                instance,
                solver,
                alias_map,
                dialect,
            )
            if rows:
                results.append((spec.branch, rows))
        return results
```

Leave existing branch-coverage behavior unchanged.

- [ ] **Step 5: Run single-table gold tests**

Run:

```bash
pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_materializes_single_table_filter_through_instance tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_objective_returns_only_positive_rows_for_simple_filter -q
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "feat: materialize row-scoped gold witnesses"
```

---

### Task 4: Add Join And Self-Join Row-Scoped Constraints

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`

- [ ] **Step 1: Write join and self-join tests**

Add:

```python
def test_gold_non_empty_row_scoped_inner_join_uses_one_solver_batch():
    schema = (
        "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);"
        "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT, val INT);"
    )
    sql = (
        "SELECT parent.name "
        "FROM parent JOIN child ON parent.id = child.parent_id "
        "WHERE child.val > 5"
    )
    instance, plan = _plan(sql, schema)

    results = speculate(plan, instance, plan.alias_map, dialect="sqlite", objective="gold_non_empty")

    assert results
    assert _execute_candidate_rows(instance, sql, {})


def test_gold_non_empty_row_scoped_self_join_keeps_alias_rows_distinct():
    schema = "CREATE TABLE people (id INT PRIMARY KEY, manager_id INT, name TEXT);"
    sql = (
        "SELECT e.name "
        "FROM people e JOIN people m ON e.manager_id = m.id "
        "WHERE e.name = 'Alice' AND m.name = 'Bob'"
    )
    instance, plan = _plan(sql, schema)

    results = speculate(plan, instance, plan.alias_map, dialect="sqlite", objective="gold_non_empty")

    assert results
    assert len(instance.get_rows("people")) >= 2
    assert _execute_candidate_rows(instance, sql, {})
```

- [ ] **Step 2: Run failing join tests**

Run:

```bash
pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_row_scoped_inner_join_uses_one_solver_batch tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_row_scoped_self_join_keeps_alias_rows_distinct -q
```

Expected: at least one test fails until join equalities use row-scoped table keys.

- [ ] **Step 3: Convert equivalences to row-scoped join equalities**

Add helper:

```python
def _row_scoped_join_equalities(
    spec: BranchSpec,
    row_bindings: Dict[str, RowBinding],
    alias_map,
) -> List[Tuple[str, str, str, str]]:
    equalities: List[Tuple[str, str, str, str]] = []
    for _rep, members in spec.equivalences.groups().items():
        if len(members) < 2:
            continue
        scoped: List[Tuple[str, str]] = []
        for member in members:
            table_name, column_name = member.split(".", 1)
            fake_col = exp.column(column_name, table_name)
            binding = _binding_for_column(fake_col, row_bindings, alias_map)
            if binding is not None:
                scoped.append((_solver_table_key(binding), normalize_name(column_name)))
        for left, right in zip(scoped, scoped[1:]):
            equalities.append((left[0], left[1], right[0], right[1]))
    return equalities
```

Change `_build_gold_solver_constraint` to pass:

```python
        join_equalities=_row_scoped_join_equalities(spec, row_bindings, alias_map),
```

- [ ] **Step 4: Preserve self-join rows during materialization**

Update `_materialize_rows` so two decoded rows for the same physical table are both created:

```python
def _materialize_rows(instance: Instance, rows: Dict[str, List[Dict[str, Any]]]) -> None:
    ordered_tables = instance._creation_order(
        {
            table: {
                col: [row.get(col) for row in table_rows]
                for col in instance.tables.get(table, {})
            }
            for table, table_rows in rows.items()
        }
    )
    for table_name in ordered_tables:
        for row in rows.get(table_name, []):
            instance.create_row(table_name, values=row)
```

- [ ] **Step 5: Run join tests**

Run:

```bash
pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_row_scoped_inner_join_uses_one_solver_batch tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_row_scoped_self_join_keeps_alias_rows_distinct -q
```

Expected: both tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "feat: solve row-scoped gold joins"
```

---

### Task 5: Validate Scope And Clean Solver Boundary Imports

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`

- [ ] **Step 1: Replace solver internal imports touched by this work**

In `src/parseval/symbolic/speculate.py` and
`tests/symbolic/test_speculate_gold_non_empty.py`, replace any imports touched
by this plan such as:

```python
from parseval.solver.unified import Solver
from parseval.solver.unified import SolverConstraint
```

with:

```python
from parseval.solver import Solver, SolverConstraint
```

If a file only needs one name, import only that name:

```python
from parseval.solver import SolverConstraint
```

- [ ] **Step 2: Run import boundary search**

Run:

```bash
rg -n "parseval\\.solver\\.(unified|domain|types|smt|smt_solver|lowering)" src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
```

Expected: no matches in files modified by this plan. Existing matches elsewhere
in symbolic remain out of scope for this first slice.

- [ ] **Step 3: Run gold/non-empty first-slice tests**

Run:

```bash
pytest tests/symbolic/test_speculate_gold_non_empty.py -q
```

Expected: tests for simple filters, row-scoped decoding, materialization,
joins, and self-joins pass. Later-slice tests for groups, subqueries, and CASE
may still fail if they preexisted beyond this slice; record them before
committing.

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "chore: keep speculate on solver public api"
```

---

## Self-Review

- Spec coverage: The plan covers row-scoped variables, the existing flat solver
  assignment contract, helper functions in `speculate.py`, `Instance`
  materialization, validation, single-table filters, joins, and self-joins.
  Later features listed in the spec remain explicitly out of the first
  implementation slice.
- Placeholder scan: No open-ended implementation steps remain.
- Type consistency: `RowBinding`, `_solver_table_key`, `_rewrite_expr_for_row_scope`, `_rows_from_solver_assignments`, and `_solve_and_materialize_gold` are introduced before later tasks use them.
