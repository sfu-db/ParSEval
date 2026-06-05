# Speculate Gold Non-Empty Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a positive witness mode to `speculate` that generates rows making the gold SQL return at least one row, including positive CASE WHEN witnesses.

**Architecture:** Keep the solver boundary pure: `speculate` builds typed `exp.Expression` constraints from the annotated plan, then calls `SolverConstraint` to satisfy those expressions. Add a gold non-empty path beside the existing branch-diverse path, validate candidate rows by executing the gold expression against an in-memory SQLite database, and opt the engine into this mode for the BIRD experiment path.

**Tech Stack:** Python 3.12, sqlglot, SQLite, pytest, unittest, existing ParSEval `Instance`, `Plan`, `Solver`, and `SymbolicEngine`

**Design Spec:** `docs/superpowers/specs/2026-05-31-speculate-gold-non-empty-design.md`

---

## File Structure

| File | Role |
|------|------|
| `src/parseval/symbolic/speculate.py` | Add gold non-empty objective, positive-only propagation, CASE positive specs, row validation helper, and positive resolver entrypoint |
| `src/parseval/symbolic/engine.py` | Opt `_speculate_all_branches()` into the gold non-empty objective for the first generation phase |
| `tests/symbolic/test_speculate_gold_non_empty.py` | New focused tests that execute generated rows in SQLite and assert non-empty gold results |
| `tests/symbolic/test_speculate_enhancements.py` | Existing tests must remain passing; no planned edits |
| `tests/experiment/test_sqlite.py` | No source edit in this plan; run a small smoke subset after engine opt-in |

---

### Task 1: Add Gold Non-Empty Test Harness and Objective API

**Files:**
- Create: `tests/symbolic/test_speculate_gold_non_empty.py`
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/symbolic/test_speculate_gold_non_empty.py`:

```python
"""Positive-witness tests for speculate gold non-empty mode."""

from __future__ import annotations

import sqlite3

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.speculate import speculate


def _plan(sql: str, schema: str) -> tuple[Instance, Plan]:
    instance = Instance(ddls=schema, name="gold_non_empty", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    return instance, Plan(expr, instance)


def _execute_candidate_rows(
    instance: Instance,
    sql: str,
    rows_per_table: dict[str, list[dict[str, object]]],
) -> list[tuple]:
    conn = sqlite3.connect(":memory:")
    try:
        for ddl in instance.ddls.split(";"):
            ddl = ddl.strip()
            if ddl:
                conn.execute(ddl)

        for table_name in instance.tables:
            existing_rows = instance.get_rows(table_name)
            candidate_rows = rows_per_table.get(table_name, [])
            cols = list(instance.tables[table_name].keys())
            if not existing_rows and not candidate_rows:
                continue
            placeholders = ",".join(["?"] * len(cols))
            quoted_cols = ",".join(f'"{col}"' for col in cols)
            stmt = f'INSERT INTO "{table_name}" ({quoted_cols}) VALUES ({placeholders})'

            for row in existing_rows:
                values = []
                for col in cols:
                    value = row[col].concrete if col in row.columns else None
                    if value is not None and not isinstance(value, (int, float, str, bytes)):
                        value = str(value)
                    values.append(value)
                conn.execute(stmt, values)

            for row in candidate_rows:
                values = []
                for col in cols:
                    value = row.get(col)
                    if value is not None and not isinstance(value, (int, float, str, bytes)):
                        value = str(value)
                    values.append(value)
                conn.execute(stmt, values)

        conn.commit()
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _gold_non_empty_results(schema: str, sql: str):
    instance, plan = _plan(sql, schema)
    return instance, plan, speculate(
        plan,
        instance,
        plan.alias_map,
        dialect="sqlite",
        objective="gold_non_empty",
    )


def test_gold_non_empty_objective_returns_only_positive_rows_for_simple_filter():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
    sql = "SELECT id FROM t WHERE val > 5"

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert results
    assert [branch for branch, _rows in results] == ["positive"]
    rows = _execute_candidate_rows(instance, sql, results[0][1])
    assert rows
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
cd /home/chunyu/workspaces/projects/ParSEval
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_objective_returns_only_positive_rows_for_simple_filter -v
```

Expected: FAIL with `TypeError: speculate() got an unexpected keyword argument 'objective'`.

- [ ] **Step 3: Add the objective argument and positive-only dispatch**

In `src/parseval/symbolic/speculate.py`, update `Propagator.__init__`:

```python
    def __init__(
        self,
        plan: Plan,
        instance: Instance,
        alias_map,
        dialect: str,
        objective: str = "branch_coverage",
    ):
        self.plan = plan
        self.instance = instance
        self.alias_map = alias_map
        self.dialect = dialect
        self.objective = objective
```

Add this method inside `Propagator` after `propagate()`:

```python
    def propagate_gold_non_empty(self) -> List[BranchSpec]:
        """Produce positive witness specs only.

        This mode avoids negative, NULL, boundary, and unmatched-join coverage
        rows. CASE WHEN arms are handled as positive witnesses in a later task.
        """
        _ = self.plan.annotations
        spec = BranchSpec(branch="positive")
        self._propagate_step(self.plan.root, spec)
        self._add_schema_constraints(spec)
        self._annotate_column_types(spec)
        return [spec]
```

Update `speculate()` signature and branch-spec selection:

```python
def speculate(
    plan: Plan,
    instance: Instance,
    alias_map,
    dialect: str = "sqlite",
    objective: str = "branch_coverage",
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    """One-call API: propagate + resolve -> list of branch rows."""
    from parseval.solver.unified import Solver
    propagator = Propagator(plan, instance, alias_map, dialect, objective=objective)
    solver = Solver(dialect=dialect)
    resolver = Resolver(instance, dialect, solver=solver)
    if objective == "gold_non_empty":
        branch_specs = propagator.propagate_gold_non_empty()
    else:
        branch_specs = propagator.propagate()
    logger.info("Generated %d branch specs", len(branch_specs))

    results = []
    for spec in branch_specs:
        if spec.requirements:
            rows = resolver.resolve(spec)
            results.append((spec.branch, rows))
    return results
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_objective_returns_only_positive_rows_for_simple_filter -v
```

Expected: PASS.

- [ ] **Step 5: Run existing speculate tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py tests/symbolic/test_speculate_gold_non_empty.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "feat(speculate): add gold non-empty objective"
```

---

### Task 2: Remove Branch-Coverage Constraints From Gold Non-Empty Propagation

**Files:**
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Add failing aggregate and branch-shape tests**

Append to `tests/symbolic/test_speculate_gold_non_empty.py`:

```python
def test_gold_non_empty_count_column_does_not_force_null_branch_rows():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT);"
    sql = "SELECT COUNT(name) FROM t"

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert [branch for branch, _rows in results] == ["positive"]
    rows_per_table = results[0][1]
    assert rows_per_table["t"]
    assert all(row.get("name") is not None for row in rows_per_table["t"])
    rows = _execute_candidate_rows(instance, sql, rows_per_table)
    assert rows


def test_gold_non_empty_filter_does_not_emit_negative_or_null_branches():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
    sql = "SELECT id FROM t WHERE val > 5 AND val < 20"

    _instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert [branch for branch, _rows in results] == ["positive"]
```

- [ ] **Step 2: Run tests to verify the aggregate test fails if NULL constraints leak**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_count_column_does_not_force_null_branch_rows tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_filter_does_not_emit_negative_or_null_branches -v
```

Expected before implementation: the branch-shape test passes from Task 1; the aggregate test may fail because `_add_aggregate_null_constraints()` can add `name IS NULL` to the positive spec.

- [ ] **Step 3: Gate aggregate NULL constraints by objective**

In `src/parseval/symbolic/speculate.py`, replace this block in the `Aggregate` case of `_propagate_step`:

```python
            for agg_expr in step.aggregations:
                self._add_aggregate_null_constraints(agg_expr, spec)
```

with:

```python
            if self.objective != "gold_non_empty":
                for agg_expr in step.aggregations:
                    self._add_aggregate_null_constraints(agg_expr, spec)
```

In the `Project` case, keep projected-column `IS NOT NULL` behavior unchanged for this task. The tests in this task only remove aggregate NULL coverage from positive witnesses.

- [ ] **Step 4: Run focused tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py -v
```

Expected: PASS.

- [ ] **Step 5: Run existing speculate enhancement tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py -v
```

Expected: PASS. Existing branch-coverage tests should still see aggregate NULL constraints because they use the default objective.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "fix(speculate): skip aggregate null coverage in gold non-empty mode"
```

---

### Task 3: Add Candidate Row Validation for Gold Non-Empty Results

**Files:**
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Add a validation unit test**

Append to `tests/symbolic/test_speculate_gold_non_empty.py`:

```python
def test_validate_candidate_rows_rejects_empty_gold_result():
    from parseval.symbolic.speculate import validate_gold_non_empty_rows

    schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
    sql = "SELECT id FROM t WHERE val > 5"
    instance, plan = _plan(sql, schema)

    assert validate_gold_non_empty_rows(
        plan,
        instance,
        {"t": [{"id": 1, "val": 10}]},
        dialect="sqlite",
    )
    assert not validate_gold_non_empty_rows(
        plan,
        instance,
        {"t": [{"id": 1, "val": 3}]},
        dialect="sqlite",
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_validate_candidate_rows_rejects_empty_gold_result -v
```

Expected: FAIL with `ImportError` or `AttributeError` because `validate_gold_non_empty_rows` does not exist.

- [ ] **Step 3: Add validation helper**

In `src/parseval/symbolic/speculate.py`, add `sqlite3` import near the top:

```python
import sqlite3
```

Add this helper above `speculate()`:

```python
def validate_gold_non_empty_rows(
    plan: Plan,
    instance: Instance,
    rows_per_table: Dict[str, List[Dict[str, Any]]],
    dialect: str = "sqlite",
) -> bool:
    """Return True when candidate rows make the plan SQL return rows in SQLite."""
    if dialect != "sqlite":
        return True

    sql = plan.expression.sql(dialect=dialect)
    conn = sqlite3.connect(":memory:")
    try:
        for ddl in instance.ddls.split(";"):
            ddl = ddl.strip()
            if ddl:
                conn.execute(ddl)

        for table_name, schema in instance.tables.items():
            cols = list(schema.keys())
            existing_rows = instance.get_rows(table_name)
            candidate_rows = rows_per_table.get(table_name, [])
            if not existing_rows and not candidate_rows:
                continue

            placeholders = ",".join(["?"] * len(cols))
            quoted_cols = ",".join(f'"{col}"' for col in cols)
            stmt = f'INSERT INTO "{table_name}" ({quoted_cols}) VALUES ({placeholders})'

            for row in existing_rows:
                values = []
                for col in cols:
                    value = row[col].concrete if col in row.columns else None
                    if value is not None and not isinstance(value, (int, float, str, bytes)):
                        value = str(value)
                    values.append(value)
                conn.execute(stmt, values)

            for row in candidate_rows:
                values = []
                for col in cols:
                    value = row.get(col)
                    if value is not None and not isinstance(value, (int, float, str, bytes)):
                        value = str(value)
                    values.append(value)
                conn.execute(stmt, values)

        conn.commit()
        return bool(conn.execute(sql).fetchone())
    except Exception as exc:
        logger.debug("gold_non_empty validation failed: %s", exc)
        return False
    finally:
        conn.close()
```

Add it to `__all__` at the bottom of `speculate.py`:

```python
    "validate_gold_non_empty_rows",
```

- [ ] **Step 4: Use validation in `speculate()` for the gold objective**

Inside `speculate()`, replace:

```python
    results = []
    for spec in branch_specs:
        if spec.requirements:
            rows = resolver.resolve(spec)
            results.append((spec.branch, rows))
    return results
```

with:

```python
    results = []
    for spec in branch_specs:
        if not spec.requirements:
            continue
        rows = resolver.resolve(spec)
        if objective == "gold_non_empty":
            if validate_gold_non_empty_rows(plan, instance, rows, dialect=dialect):
                results.append((spec.branch, rows))
            else:
                logger.info("Dropping gold_non_empty spec that validated empty: %s", spec.branch)
        else:
            results.append((spec.branch, rows))
    return results
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py -v
```

Expected: PASS.

- [ ] **Step 6: Run existing symbolic tests touched by speculate**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py tests/symbolic/test_symbolic_engine.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "feat(speculate): validate gold non-empty candidate rows"
```

---

### Task 4: Coordinate Positive Join Witnesses

**Files:**
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Add join non-empty regression test**

Append to `tests/symbolic/test_speculate_gold_non_empty.py`:

```python
def test_gold_non_empty_inner_join_generates_matching_rows():
    schema = (
        "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);"
        "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT REFERENCES parent(id), val INT);"
    )
    sql = (
        "SELECT parent.name "
        "FROM parent JOIN child ON parent.id = child.parent_id "
        "WHERE child.val > 5"
    )

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert results
    assert results[0][0] == "positive"
    rows = _execute_candidate_rows(instance, sql, results[0][1])
    assert rows
```

- [ ] **Step 2: Run test to verify current behavior**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_inner_join_generates_matching_rows -v
```

Expected before implementation: FAIL if the generated parent and child join keys do not match or if validation drops the empty candidate.

- [ ] **Step 3: Add a positive resolver entrypoint**

In `Resolver`, add this method before `resolve()`:

```python
    def resolve_positive(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
        """Resolve a positive witness with join coordination enabled."""
        self._discover_fk_parents(spec)
        join_equalities = self._equivalences_to_join_equalities(spec)
        order = self._creation_order(spec)
        result: Dict[str, List[Dict[str, Any]]] = {}

        for table_key in order:
            if table_key not in spec.requirements:
                continue
            req = spec.requirements[table_key]
            physical = req.table.split("__")[0] if "__" in req.table else req.table
            needed = max(req.min_rows, 1)
            for row_index in range(needed):
                row = self._solve_row(
                    physical,
                    req,
                    spec,
                    join_equalities,
                    result,
                    row_index=row_index,
                )
                if row:
                    result.setdefault(physical, []).append(row)

        return result
```

Update `speculate()` so gold mode calls `resolve_positive()`:

```python
        if objective == "gold_non_empty":
            rows = resolver.resolve_positive(spec)
            if validate_gold_non_empty_rows(plan, instance, rows, dialect=dialect):
                results.append((spec.branch, rows))
            else:
                logger.info("Dropping gold_non_empty spec that validated empty: %s", spec.branch)
        else:
            rows = resolver.resolve(spec)
            results.append((spec.branch, rows))
```

This keeps the branch-diverse resolver unchanged while giving the positive path a separate place for subsequent join and retry improvements.

- [ ] **Step 4: Ensure join equalities are stored only once per table**

In the `Join` case of `Propagator._propagate_step`, keep `spec.equate(...)` unchanged. Leave the existing per-table `eq_expr` appends in place for this task; the goal is to create a positive resolver hook without changing equality semantics yet.

- [ ] **Step 5: Run join test**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_inner_join_generates_matching_rows -v
```

Expected: PASS.

- [ ] **Step 6: Run all gold non-empty tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "feat(speculate): add positive resolver path for join witnesses"
```

---

### Task 5: Handle HAVING COUNT Positive Groups

**Files:**
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Add HAVING COUNT non-empty test**

Append to `tests/symbolic/test_speculate_gold_non_empty.py`:

```python
def test_gold_non_empty_having_count_creates_one_surviving_group():
    schema = (
        "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);"
        "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT REFERENCES parent(id), val INT);"
    )
    sql = (
        "SELECT parent.id, COUNT(child.id) "
        "FROM parent JOIN child ON parent.id = child.parent_id "
        "GROUP BY parent.id HAVING COUNT(child.id) > 2"
    )

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert results
    rows_per_table = results[0][1]
    assert len(rows_per_table.get("child", [])) >= 3
    rows = _execute_candidate_rows(instance, sql, rows_per_table)
    assert rows
```

- [ ] **Step 2: Run test to verify current behavior**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_having_count_creates_one_surviving_group -v
```

Expected before implementation: FAIL if child rows do not share the same parent join key or if fewer than 3 child rows are generated.

- [ ] **Step 3: Keep group keys stable across duplicate positive rows**

In `_solve_row()`, keep the existing `group_key_columns` branch for `row_index > 0`. If the test fails because child rows get different `parent_id` values, update the join equality value selection to use row 0 of the parent side for group-key joins.

Replace both `joined_row = min(row_index, len(result[other]) - 1)` assignments in `_solve_row()` with:

```python
                joined_row = 0 if rc in req.group_key_columns or lc in req.group_key_columns else min(row_index, len(result[rt]) - 1)
```

for the `lt == table` branch, and:

```python
                joined_row = 0 if lc in req.group_key_columns or rc in req.group_key_columns else min(row_index, len(result[lt]) - 1)
```

for the `rt == table` branch.

If the line is too long after insertion, split it as:

```python
                use_group_row = rc in req.group_key_columns or lc in req.group_key_columns
                joined_row = 0 if use_group_row else min(row_index, len(result[rt]) - 1)
```

and:

```python
                use_group_row = lc in req.group_key_columns or rc in req.group_key_columns
                joined_row = 0 if use_group_row else min(row_index, len(result[lt]) - 1)
```

- [ ] **Step 4: Ensure counted table min_rows is already positive**

Do not change `_extract_min_group_size()` in this task unless the test shows `child.min_rows < 3`. If it does, add this assertion-oriented fix in the `Having` case after `counted_table = self._find_counted_table(step.condition)`:

```python
                if counted_table and counted_table in spec.requirements:
                    spec.requirements[counted_table].min_rows = max(
                        spec.requirements[counted_table].min_rows,
                        min_size,
                    )
```

The current file already contains this logic; the implementation worker should verify it remains present.

- [ ] **Step 5: Run HAVING test**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_having_count_creates_one_surviving_group -v
```

Expected: PASS.

- [ ] **Step 6: Run focused suite**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py tests/symbolic/test_speculate_enhancements.py -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "fix(speculate): keep positive having count rows in one group"
```

---

### Task 6: Generate Positive IN and EXISTS Subquery Witnesses

**Files:**
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Add IN and EXISTS tests**

Append to `tests/symbolic/test_speculate_gold_non_empty.py`:

```python
def test_gold_non_empty_in_subquery_creates_matching_inner_row():
    schema = (
        "CREATE TABLE outer_t (id INT PRIMARY KEY, code INT);"
        "CREATE TABLE inner_t (id INT PRIMARY KEY, code INT);"
    )
    sql = "SELECT id FROM outer_t WHERE code IN (SELECT code FROM inner_t WHERE code > 3)"

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert results
    rows_per_table = results[0][1]
    assert rows_per_table.get("outer_t")
    assert rows_per_table.get("inner_t")
    rows = _execute_candidate_rows(instance, sql, rows_per_table)
    assert rows


def test_gold_non_empty_exists_subquery_creates_correlated_inner_row():
    schema = (
        "CREATE TABLE outer_t (id INT PRIMARY KEY, code INT);"
        "CREATE TABLE inner_t (id INT PRIMARY KEY, code INT);"
    )
    sql = (
        "SELECT id FROM outer_t "
        "WHERE EXISTS (SELECT 1 FROM inner_t WHERE inner_t.code = outer_t.code)"
    )

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert results
    rows_per_table = results[0][1]
    assert rows_per_table.get("outer_t")
    assert rows_per_table.get("inner_t")
    rows = _execute_candidate_rows(instance, sql, rows_per_table)
    assert rows
```

- [ ] **Step 2: Run tests to verify current behavior**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_in_subquery_creates_matching_inner_row tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_exists_subquery_creates_correlated_inner_row -v
```

Expected before implementation: one or both tests fail because subquery rows or correlations are not coordinated enough for SQLite validation.

- [ ] **Step 3: Preserve subplan propagation in positive mode**

In `Propagator._propagate_subplan()`, keep the existing calls:

```python
        elif sub.kind.value == "in":
            self._propagate_in_subplan(sub, spec)

        elif sub.kind.value == "scalar":
            self._propagate_scalar_subplan(sub, spec)

        if sub.inner:
            self._propagate_step(sub.inner, spec)
            self._fix_inner_filter_tables(sub.inner, spec)
```

No branch-coverage code should be added here. This task only makes positive `IN` and `EXISTS` witnesses validate.

- [ ] **Step 4: Add equality expression for IN subquery witness**

In `_propagate_in_subplan()`, after:

```python
            spec.require(outer_table)
            spec.equate(f"{outer_table}.{outer_matched}", inner_col_key)
```

add:

```python
            inner_table, inner_col = inner_col_key.split(".", 1)
            eq_expr = exp.EQ(
                this=exp.column(outer_matched, outer_table),
                expression=exp.column(inner_col, inner_table),
            )
            spec.requirements[outer_table].constraints.append(eq_expr)
```

- [ ] **Step 5: Add equality expression for EXISTS correlation when needed**

In `_propagate_subplan()`, inside the `exists` branch after:

```python
                    if inner_key:
                        spec.equate(outer_key, inner_key)
```

add:

```python
                        inner_table, inner_col = inner_key.split(".", 1)
                        eq_expr = exp.EQ(
                            this=exp.column(matched, outer_table),
                            expression=exp.column(inner_col, inner_table),
                        )
                        spec.requirements[outer_table].constraints.append(eq_expr)
```

- [ ] **Step 6: Run subquery tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_in_subquery_creates_matching_inner_row tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_exists_subquery_creates_correlated_inner_row -v
```

Expected: PASS.

- [ ] **Step 7: Run focused suite**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py tests/symbolic/test_subplan_eval.py -v
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "feat(speculate): coordinate positive subquery witnesses"
```

---

### Task 7: Add Positive CASE WHEN Witnesses

**Files:**
- Modify: `tests/symbolic/test_speculate_gold_non_empty.py`
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Add CASE positive witness test**

Append to `tests/symbolic/test_speculate_gold_non_empty.py`:

```python
def test_gold_non_empty_case_when_generates_positive_case_witnesses():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, score INT);"
    sql = (
        "SELECT CASE "
        "WHEN score >= 90 THEN 'high' "
        "WHEN score >= 50 THEN 'mid' "
        "ELSE 'low' END AS bucket "
        "FROM t"
    )

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    branches = [branch for branch, _rows in results]
    assert "positive" in branches
    assert "positive_case_0_when_0" in branches
    assert "positive_case_0_when_1" in branches
    for _branch, rows_per_table in results:
        rows = _execute_candidate_rows(instance, sql, rows_per_table)
        assert rows
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_case_when_generates_positive_case_witnesses -v
```

Expected: FAIL because `propagate_gold_non_empty()` returns only `"positive"`.

- [ ] **Step 3: Add CASE positive specs**

In `Propagator`, add this helper near `_collect_case_when_conditions()`:

```python
    def _collect_case_when_positive_conditions(self) -> List[List[exp.Expression]]:
        """Collect CASE WHEN conditions that can produce positive output rows."""
        return self._collect_case_when_conditions()
```

Update `propagate_gold_non_empty()`:

```python
    def propagate_gold_non_empty(self) -> List[BranchSpec]:
        """Produce positive witness specs only."""
        _ = self.plan.annotations
        specs: List[BranchSpec] = []

        base = BranchSpec(branch="positive")
        self._propagate_step(self.plan.root, base)
        self._add_schema_constraints(base)
        self._annotate_column_types(base)
        specs.append(base)

        for case_idx, when_conditions in enumerate(self._collect_case_when_positive_conditions()):
            prior_conditions: List[exp.Expression] = []
            for when_idx, cond in enumerate(when_conditions):
                case_spec = BranchSpec(branch=f"positive_case_{case_idx}_when_{when_idx}")
                self._propagate_step(self.plan.root, case_spec)
                for prior in prior_conditions:
                    self._store_expression(negate_predicate(prior.copy()), case_spec)
                self._store_expression(cond.copy(), case_spec)
                self._add_schema_constraints(case_spec)
                self._annotate_column_types(case_spec)
                specs.append(case_spec)
                prior_conditions.append(cond)

        return specs
```

This creates one positive row objective per CASE WHEN arm. For later WHEN arms, prior WHEN predicates are negated so the intended arm is reachable.

- [ ] **Step 4: Run CASE test**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py::test_gold_non_empty_case_when_generates_positive_case_witnesses -v
```

Expected: PASS.

- [ ] **Step 5: Run all gold non-empty tests**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "feat(speculate): generate positive case when witnesses"
```

---

### Task 8: Opt SymbolicEngine Into Gold Non-Empty Speculation

**Files:**
- Modify: `src/parseval/symbolic/engine.py`
- Modify: `tests/symbolic/test_symbolic_engine.py`

- [ ] **Step 1: Add engine integration test**

Append to `tests/symbolic/test_symbolic_engine.py`:

```python
def test_engine_uses_gold_non_empty_speculation_for_initial_generation():
    from tests.symbolic.test_symbolic_bird import _write_and_execute

    schema = (
        "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);"
        "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT REFERENCES parent(id), val INT);"
    )
    sql = (
        "SELECT parent.name "
        "FROM parent JOIN child ON parent.id = child.parent_id "
        "WHERE child.val > 5"
    )
    instance = Instance(ddls=schema, name="engine_gold_non_empty", dialect="sqlite")
    engine = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=5)

    engine.generate(thresholds=CoverageThresholds(atom_null=0))

    rows = _write_and_execute(instance, sql)
    assert rows
```

- [ ] **Step 2: Run test to verify current behavior**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_symbolic_engine.py::test_engine_uses_gold_non_empty_speculation_for_initial_generation -v
```

Expected before implementation: FAIL if `_speculate_all_branches()` still emits noisy branch rows or validation is not used by the engine path.

- [ ] **Step 3: Update engine speculation call**

In `src/parseval/symbolic/engine.py`, replace:

```python
        branch_results = speculate(self.plan, self.instance, self.alias_map, self.dialect)
```

with:

```python
        branch_results = speculate(
            self.plan,
            self.instance,
            self.alias_map,
            self.dialect,
            objective="gold_non_empty",
        )
```

Do not rename `_speculate_all_branches()` in this task. Keeping the method name avoids unrelated churn.

- [ ] **Step 4: Run engine test**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_symbolic_engine.py::test_engine_uses_gold_non_empty_speculation_for_initial_generation -v
```

Expected: PASS.

- [ ] **Step 5: Run focused engine and speculate suites**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py tests/symbolic/test_symbolic_engine.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/engine.py tests/symbolic/test_symbolic_engine.py
git commit -m "feat(engine): use gold non-empty speculation for initial generation"
```

---

### Task 9: Run BIRD Smoke Verification and Guard Against Regressions

**Files:**
- Read: `tests/symbolic/test_symbolic_bird.py`
- Read: `tests/experiment/test_sqlite.py`

- [ ] **Step 1: Run focused unit suites**

Run:

```bash
cd /home/chunyu/workspaces/projects/ParSEval
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_gold_non_empty.py tests/symbolic/test_speculate_enhancements.py tests/symbolic/test_symbolic_engine.py tests/solver/test_solver.py tests/solver/test_domain.py -v
```

Expected: PASS.

- [ ] **Step 2: Run BIRD non-empty benchmark test**

Run:

```bash
.venv/bin/python3 -m pytest tests/symbolic/test_symbolic_bird.py -v -s
```

Expected: PASS. The printed `Non-empty results: N/total` line should not be lower than the baseline recorded in the current branch. If the line is absent because the test aborts, inspect the first failing traceback before changing code.

- [ ] **Step 3: Run a small SQLite experiment smoke command**

Run:

```bash
.venv/bin/python3 tests/experiment/test_sqlite.py \
  --schema_fp data/sqlite/schema.json \
  --gold_fp data/sqlite/dev.json \
  --preds_fp data/sqlite/dail.txt \
  --output_dir results \
  --workers 1
```

Expected: The command writes `sqlite_results_*.json` and `sqlite_metrics_*.json` under `results/` and does not crash. If this full dataset run is too slow for the execution environment, stop it with Ctrl-C after the first progress updates and record that the full experiment was not completed.

- [ ] **Step 4: Check git diff**

Run:

```bash
git diff -- src/parseval/symbolic/speculate.py src/parseval/symbolic/engine.py tests/symbolic/test_speculate_gold_non_empty.py tests/symbolic/test_symbolic_engine.py
```

Expected: Diff contains only the positive witness objective, tests, validation helper, CASE positive witnesses, and engine opt-in.

- [ ] **Step 5: Commit verification note if test metadata was updated**

If no files changed during verification, do not commit.

If a test comment or benchmark marker was updated, run:

```bash
git add tests/symbolic/test_symbolic_bird.py tests/symbolic/test_speculate_gold_non_empty.py
git commit -m "test: record gold non-empty BIRD smoke result"
```

Expected: Commit created only when verification metadata changed.

---

## Self-Review Checklist

- Spec coverage: Tasks 1-3 implement objective mode and validation. Tasks 4-6 cover joins, HAVING, `IN`, and `EXISTS`. Task 7 covers positive CASE WHEN witnesses. Task 8 opts the engine path into the objective. Task 9 verifies focused tests and BIRD smoke behavior.
- Solver boundary: No task adds planner, FK, aggregate, subquery, validation, or BIRD logic to the solver.
- Scope: Negative filter branches, NULL branch coverage, boundary rows, unmatched joins, and prediction-vs-gold disproof quality remain outside this plan.
- Test policy: Each behavior starts with a failing test and a focused command, then adds minimal implementation and reruns the command.
