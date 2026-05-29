# Speculative Layer Enhancement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Absorb 5 engine fixup methods into the speculative layer, then remove them from the engine, leaving a thin orchestrator with only `_smt_repair_where` as Z3 fallback.

**Architecture:** Enhance the Propagator to produce more accurate `BranchSpec` objects (table-specific `min_rows`, aggregate NULL tracking, scalar subquery detection), enhance the Resolver to handle FK-referenced tables and row validation, then remove the corresponding fixup methods from `engine.py` one at a time.

**Tech Stack:** Python 3.12, sqlglot, pytest, unittest

**Design Spec:** `docs/symbolic-engine-architecture.md`

---

## File Structure

| File | Role |
|------|------|
| `src/parseval/symbolic/speculate.py` | Propagator + Resolver — all enhancements land here |
| `src/parseval/symbolic/engine.py` | Engine — remove 5 fixup methods, keep `_smt_repair_where` |
| `src/parseval/symbolic/enrichment.py` | May become unused after Propagator handles aggregate NULL detection |
| `tests/symbolic/test_speculate_enhancements.py` | **New** — unit tests for each speculative enhancement |
| `tests/symbolic/test_symbolic_engine.py` | Existing — integration tests (must keep passing) |
| `tests/symbolic/test_symbolic_bird.py` | Existing — BIRD benchmark regression gate |

---

### Task 1: Establish BIRD Benchmark Baseline

**Files:**
- Read: `tests/symbolic/test_symbolic_bird.py`

- [ ] **Step 1: Run the BIRD benchmark and record the pass rate**

Run:
```bash
cd /home/chunyu/workspaces/projects/ParSEval
.venv/bin/python3 -m pytest tests/symbolic/test_symbolic_bird.py -v -s 2>&1 | tail -20
```

Expected: Output includes a line like `Non-empty results: N/1600 (XX%)`. Record this number. This is the baseline — no subsequent task may regress below it.

- [ ] **Step 2: Record baseline in a comment**

Add a comment at the top of `tests/symbolic/test_speculate_enhancements.py` (created in Task 2) recording the baseline:
```python
# BIRD benchmark baseline: XXXX/1600 (XX%) — recorded 2026-05-29
```

- [ ] **Step 3: Commit**

```bash
git add tests/symbolic/test_speculate_enhancements.py
git commit -m "test: record BIRD benchmark baseline for speculative layer enhancement"
```

---

### Task 2: FK-Referenced Table Rows in Resolver (Phase B, Step 5 — Highest Impact)

**Problem:** The Resolver only creates rows for tables in `spec.requirements`. If a child table has a FK to a parent table, but the parent isn't directly scanned (e.g., it's only referenced via FK), the parent gets no rows and FK insertion fails.

**Files:**
- Create: `tests/symbolic/test_speculate_enhancements.py`
- Modify: `src/parseval/symbolic/speculate.py:966-993` (Resolver._creation_order)

- [ ] **Step 1: Write the failing test**

```python
# tests/symbolic/test_speculate_enhancements.py
"""Unit tests for speculative layer enhancements.

Each test targets a specific enhancement to the Propagator or Resolver.
"""

# BIRD benchmark baseline: XXXX/1600 (XX%) — recorded 2026-05-29

import unittest
from parseval.instance import Instance
from parseval.symbolic.speculate import BranchSpec, Resolver, TableRequirement


SCHEMA_FK = (
    "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT NOT NULL);"
    "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT REFERENCES parent(id), val INT);"
)


class TestFKReferencedTableRows(unittest.TestCase):
    """Resolver should create rows for FK-referenced parent tables even if not in spec.requirements."""

    def test_parent_table_created_when_only_child_in_spec(self):
        instance = Instance(ddls=SCHEMA_FK, name="test_fk", dialect="sqlite")
        resolver = Resolver(instance, dialect="sqlite")

        spec = BranchSpec(branch="positive")
        # Only require child — parent is FK-referenced but not in spec
        spec.requirements["child"] = TableRequirement(table="child", min_rows=1)

        rows = resolver.resolve(spec)
        # Parent table should have rows created via FK resolution
        parent_rows = instance.get_rows("parent")
        self.assertGreater(len(parent_rows), 0,
            "Resolver should create parent rows for FK-referenced tables")
        # Child rows should reference a valid parent
        child_rows = instance.get_rows("child")
        self.assertGreater(len(child_rows), 0)
        parent_id_val = child_rows[0]["parent_id"].concrete
        parent_ids = {r["id"].concrete for r in parent_rows}
        self.assertIn(parent_id_val, parent_ids,
            "Child's parent_id should reference an existing parent row")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestFKReferencedTableRows::test_parent_table_created_when_only_child_in_spec -v
```

Expected: FAIL — parent table has no rows because `_creation_order` doesn't discover FK-referenced tables.

- [ ] **Step 3: Implement FK discovery in Resolver._creation_order**

In `src/parseval/symbolic/speculate.py`, modify `_creation_order` (line 966). After the topological sort, discover FK-referenced tables not in `spec.requirements` and add them:

```python
    def _creation_order(self, spec: BranchSpec) -> List[str]:
        tables = list(spec.requirements.keys())
        deps: Dict[str, Set[str]] = {t: set() for t in tables}

        # Discover FK-referenced tables not in spec.requirements.
        # These are parent tables that must exist before child rows can be inserted.
        fk_discovered: Dict[str, TableRequirement] = {}
        for table in list(tables):
            physical = table.split("__")[0] if "__" in table else table
            if physical not in self.instance.tables:
                continue
            for fk in self.instance.get_foreign_key(physical):
                ref = fk.args.get("reference")
                if ref:
                    ref_table_node = ref.find(exp.Table)
                    if ref_table_node:
                        ref_table = normalize_name(ref_table_node.name)
                        if ref_table not in spec.requirements and ref_table in self.instance.tables:
                            req = TableRequirement(table=ref_table, min_rows=1)
                            fk_discovered[ref_table] = req
                            spec.requirements[ref_table] = req
                            tables.append(ref_table)

        # Build dependency graph
        deps = {t: set() for t in tables}
        for table in tables:
            physical = table.split("__")[0] if "__" in table else table
            if physical not in self.instance.tables:
                continue
            for fk in self.instance.get_foreign_key(physical):
                ref = fk.args.get("reference")
                if ref:
                    ref_table = ref.find(exp.Table)
                    if ref_table and normalize_name(ref_table.name) in deps:
                        deps[table].add(normalize_name(ref_table.name))

        ordered: List[str] = []
        ready = [t for t in tables if not deps[t]]
        while ready:
            t = ready.pop(0)
            ordered.append(t)
            for other in tables:
                if t in deps.get(other, set()):
                    deps[other].discard(t)
                    if not deps[other] and other not in ordered:
                        ready.append(other)
        for t in tables:
            if t not in ordered:
                ordered.append(t)
        return ordered
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestFKReferencedTableRows -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/symbolic/test_speculate_enhancements.py src/parseval/symbolic/speculate.py
git commit -m "feat(resolver): discover FK-referenced parent tables during row creation"
```

---

### Task 3: HAVING COUNT Table-Specific min_rows (Phase A, Step 1)

**Problem:** The Propagator's `_extract_min_group_size` returns a global `min_rows` that gets applied to ALL tables. For `HAVING COUNT(child.col) > 3`, only the child table needs 4 rows — the parent table doesn't.

**Files:**
- Modify: `tests/symbolic/test_speculate_enhancements.py`
- Modify: `src/parseval/symbolic/speculate.py:163-173` (Propagator Having handler)

- [ ] **Step 1: Write the failing test**

Append to `tests/symbolic/test_speculate_enhancements.py`:

```python
SCHEMA_JOIN = (
    "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);"
    "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT REFERENCES parent(id), val INT);"
)


class TestHavingCountTableSpecific(unittest.TestCase):
    """HAVING COUNT > N should set min_rows only on the counted table."""

    def test_min_rows_applied_to_counted_table_only(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator

        schema = SCHEMA_JOIN
        sql = "SELECT parent.id, COUNT(child.id) FROM parent JOIN child ON parent.id = child.parent_id GROUP BY parent.id HAVING COUNT(child.id) > 3"
        instance = Instance(ddls=schema, name="test_having", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        alias_map = plan.alias_map

        propagator = Propagator(plan, instance, alias_map, "sqlite")
        specs = propagator.propagate()

        pos_spec = specs[0]  # positive branch
        # child table should have min_rows >= 4 (COUNT > 3 → need 4)
        child_req = pos_spec.requirements.get("child")
        self.assertIsNotNone(child_req, "child table should be in requirements")
        self.assertGreaterEqual(child_req.min_rows, 4,
            f"child min_rows should be >= 4 for COUNT > 3, got {child_req.min_rows}")
        # parent table should NOT be forced to 4 rows
        parent_req = pos_spec.requirements.get("parent")
        if parent_req:
            self.assertLess(parent_req.min_rows, 4,
                f"parent min_rows should be < 4, got {parent_req.min_rows}")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestHavingCountTableSpecific -v
```

Expected: FAIL — parent table gets `min_rows=4` because the current code applies `min_group_size` to all tables.

- [ ] **Step 3: Implement table-specific min_rows in Having handler**

In `src/parseval/symbolic/speculate.py`, modify the Having handler in `_propagate_step` (line 163). Replace the global `min_rows` application with table-specific logic:

```python
        elif isinstance(step, Having):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition and step is not negate_step:
                self._extract_predicates(step.condition, spec)
                # HAVING with aggregate: derive min group size for counted table only.
                counted_table = self._find_counted_table(step.condition)
                min_size = self._extract_min_group_size(step.condition)
                if counted_table and counted_table in spec.requirements:
                    spec.requirements[counted_table].min_rows = max(
                        spec.requirements[counted_table].min_rows, min_size
                    )
                else:
                    # Fallback: apply globally if we can't identify the counted table.
                    for req in spec.requirements.values():
                        req.min_rows = max(req.min_rows, min_size)
                # Derive per-row value constraints from aggregate thresholds.
                self._extract_having_value_constraints(step.condition, spec, min_size)
```

Then add the `_find_counted_table` method to the `Propagator` class:

```python
    def _find_counted_table(self, condition: exp.Expression) -> Optional[str]:
        """Find the table containing the column inside COUNT(col).

        For HAVING COUNT(child.id) > 3, returns 'child'.
        Returns None for COUNT(*) or COUNT(DISTINCT col) where table can't be resolved.
        """
        for step in self.plan.ordered_steps:
            if isinstance(step, Aggregate):
                for agg_expr in step.aggregations:
                    for count_node in agg_expr.find_all(exp.Count):
                        # Skip COUNT(*)
                        if isinstance(count_node.this, exp.Star):
                            continue
                        for col in count_node.find_all(exp.Column):
                            table = self._resolve_table(col.table or "")
                            if table and table in self.instance.tables:
                                return table
        # Also check the HAVING condition directly
        for count_node in condition.find_all(exp.Count):
            if isinstance(count_node.this, exp.Star):
                continue
            for col in count_node.find_all(exp.Column):
                table = self._resolve_table(col.table or "")
                if table and table in self.instance.tables:
                    return table
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestHavingCountTableSpecific -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_enhancements.py
git commit -m "feat(propagator): apply HAVING COUNT min_rows to counted table only"
```

---

### Task 4: OFFSET Table-Specific min_rows (Phase A, Step 3)

**Problem:** The Limit handler applies `min_rows = offset + limit` to ALL tables. For multi-table queries, only the driving table (the one producing output rows) needs enough rows for the offset.

**Files:**
- Modify: `tests/symbolic/test_speculate_enhancements.py`
- Modify: `src/parseval/symbolic/speculate.py:132-140` (Propagator Limit handler)

- [ ] **Step 1: Write the failing test**

Append to `tests/symbolic/test_speculate_enhancements.py`:

```python
class TestOffsetTableSpecific(unittest.TestCase):
    """OFFSET should set min_rows on the driving table only, not all tables."""

    def test_offset_applies_to_driving_table_only(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator

        schema = SCHEMA_JOIN
        sql = "SELECT parent.id FROM parent JOIN child ON parent.id = child.parent_id LIMIT 5 OFFSET 10"
        instance = Instance(ddls=schema, name="test_offset", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        alias_map = plan.alias_map

        propagator = Propagator(plan, instance, alias_map, "sqlite")
        specs = propagator.propagate()

        pos_spec = specs[0]
        # Driving table (parent) should have min_rows >= 15 (offset 10 + limit 5)
        parent_req = pos_spec.requirements.get("parent")
        self.assertIsNotNone(parent_req)
        self.assertGreaterEqual(parent_req.min_rows, 15,
            f"parent (driving table) min_rows should be >= 15, got {parent_req.min_rows}")
        # Non-driving table (child) should NOT be forced to 15
        child_req = pos_spec.requirements.get("child")
        if child_req:
            self.assertLess(child_req.min_rows, 15,
                f"child min_rows should be < 15, got {child_req.min_rows}")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestOffsetTableSpecific -v
```

Expected: FAIL — child table gets `min_rows=15` because the current code applies to all tables.

- [ ] **Step 3: Implement driving-table-only logic in Limit handler**

In `src/parseval/symbolic/speculate.py`, modify the Limit handler in `_propagate_step` (line 132):

```python
        if isinstance(step, Limit):
            offset = getattr(step, "offset", 0) or 0
            limit_val = step.limit if step.limit != float("inf") else 1
            needed = offset + int(limit_val)
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # Apply min_rows to the driving table only (first table in alias_map).
            driving_table = next(
                (v for v in self.alias_map.values() if v in self.instance.tables), None
            )
            if driving_table and driving_table in spec.requirements:
                spec.requirements[driving_table].min_rows = max(
                    spec.requirements[driving_table].min_rows, needed
                )
            elif driving_table:
                # Table not yet in spec — add it with the needed rows.
                spec.requirements[driving_table] = TableRequirement(
                    table=driving_table, min_rows=needed
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestOffsetTableSpecific -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_enhancements.py
git commit -m "feat(propagator): apply OFFSET min_rows to driving table only"
```

---

### Task 5: Aggregate NULL Columns in Propagator (Phase A, Step 2)

**Problem:** The Propagator doesn't track that COUNT/SUM/AVG columns need a NULL row to test NULL-handling semantics. The evaluator can't observe ATOM_NULL without a NULL row.

**Files:**
- Modify: `tests/symbolic/test_speculate_enhancements.py`
- Modify: `src/parseval/symbolic/speculate.py:175-192` (Propagator Aggregate handler)

- [ ] **Step 1: Write the failing test**

Append to `tests/symbolic/test_speculate_enhancements.py`:

```python
class TestAggregateNullColumns(unittest.TestCase):
    """Propagator should mark COUNT/SUM/AVG columns as must_null."""

    def test_count_column_marked_must_null(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator

        schema = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT);"
        sql = "SELECT COUNT(name) FROM t"
        instance = Instance(ddls=schema, name="test_agg_null", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        alias_map = plan.alias_map

        propagator = Propagator(plan, instance, alias_map, "sqlite")
        specs = propagator.propagate()

        pos_spec = specs[0]
        t_req = pos_spec.requirements.get("t")
        self.assertIsNotNone(t_req)
        self.assertIn("name", t_req.must_null,
            f"'name' should be in must_null for COUNT(name), got must_null={t_req.must_null}")
        self.assertGreaterEqual(t_req.min_rows, 2,
            f"min_rows should be >= 2 (one NULL + one non-NULL), got {t_req.min_rows}")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestAggregateNullColumns -v
```

Expected: FAIL — `must_null` is empty because the Aggregate handler doesn't track aggregate operand columns.

- [ ] **Step 3: Implement aggregate NULL detection in Aggregate handler**

In `src/parseval/symbolic/speculate.py`, modify the Aggregate handler in `_propagate_step` (line 175). Add NULL tracking after the GROUP BY logic:

```python
        elif isinstance(step, Aggregate):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            # GROUP BY: mark group columns as needing the same value across rows.
            if step.group:
                for group_expr in step.group.values():
                    for col in group_expr.find_all(exp.Column):
                        table = self._resolve_table(col.table or "")
                        matched = self._match_column(table, col.name)
                        if matched:
                            req = spec.require(table)
                            spec.equivalences.find(f"{table}.{matched}")
                            if matched not in req.group_key_columns:
                                req.group_key_columns.append(matched)
            # Aggregate NULL detection: COUNT/SUM/AVG columns need a NULL row
            # to test NULL-handling semantics.
            for agg_expr in step.aggregations:
                self._mark_aggregate_null_columns(agg_expr, spec)
```

Then add `_mark_aggregate_null_columns` to the `Propagator` class:

```python
    def _mark_aggregate_null_columns(self, agg_expr: exp.Expression, spec: BranchSpec):
        """Mark columns inside COUNT/SUM/AVG as must_null.

        Skips COUNT(*) and COUNT(DISTINCT col) — those don't need NULL testing.
        """
        for count_node in agg_expr.find_all(exp.Count):
            # Skip COUNT(*)
            if isinstance(count_node.this, exp.Star):
                continue
            # Skip COUNT(DISTINCT col) — DISTINCT ignores NULLs
            if count_node.args.get("distinct"):
                continue
            for col in count_node.find_all(exp.Column):
                table = self._resolve_table(col.table or "")
                matched = self._match_column(table, col.name)
                if matched and table in self.instance.tables:
                    req = spec.require(table)
                    req.must_null.add(matched)
                    req.min_rows = max(req.min_rows, 2)

        for sum_node in agg_expr.find_all(exp.Sum):
            for col in sum_node.find_all(exp.Column):
                table = self._resolve_table(col.table or "")
                matched = self._match_column(table, col.name)
                if matched and table in self.instance.tables:
                    req = spec.require(table)
                    req.must_null.add(matched)
                    req.min_rows = max(req.min_rows, 2)

        for avg_node in agg_expr.find_all(exp.Avg):
            for col in avg_node.find_all(exp.Column):
                table = self._resolve_table(col.table or "")
                matched = self._match_column(table, col.name)
                if matched and table in self.instance.tables:
                    req = spec.require(table)
                    req.must_null.add(matched)
                    req.min_rows = max(req.min_rows, 2)
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestAggregateNullColumns -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_enhancements.py
git commit -m "feat(propagator): mark COUNT/SUM/AVG columns as must_null"
```

---

### Task 6: Row Validation in Resolver (Phase B, Step 6)

**Problem:** The Resolver generates values via `_satisfy_all` but doesn't verify they actually satisfy the predicates. If `_satisfy_all` produces a bad value, the row silently fails.

**Files:**
- Modify: `tests/symbolic/test_speculate_enhancements.py`
- Modify: `src/parseval/symbolic/speculate.py:862-885` (Resolver._build_row)

- [ ] **Step 1: Write the failing test**

Append to `tests/symbolic/test_speculate_enhancements.py`:

```python
class TestRowValidation(unittest.TestCase):
    """Resolver should validate generated rows and retry on predicate failure."""

    def test_row_satisfies_predicates(self):
        from parseval.instance import Instance
        from parseval.symbolic.speculate import Resolver, TableRequirement

        schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
        instance = Instance(ddls=schema, name="test_validate", dialect="sqlite")
        resolver = Resolver(instance, dialect="sqlite")

        spec = BranchSpec(branch="positive")
        # val > 100 AND val < 50 — impossible range, but _satisfy_all should handle it
        spec.requirements["t"] = TableRequirement(
            table="t",
            min_rows=1,
            predicates=[("val", ">", 10), ("val", "<", 20)],
        )

        rows = resolver.resolve(spec)
        t_rows = instance.get_rows("t")
        self.assertGreater(len(t_rows), 0)
        val = t_rows[0]["val"].concrete
        self.assertGreater(val, 10, f"val should be > 10, got {val}")
        self.assertLess(val, 20, f"val should be < 20, got {val}")
```

- [ ] **Step 2: Run test to verify it passes (this tests existing behavior)**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestRowValidation -v
```

Expected: PASS — `_satisfy_all` already handles numeric ranges correctly. This test validates the happy path. The actual validation enhancement catches edge cases where `_satisfy_all` returns a bad value.

- [ ] **Step 3: Add validation as a safety net**

In `src/parseval/symbolic/speculate.py`, add a `_validate_row` method to `Resolver` and call it from `_build_row`:

```python
    def _validate_row(self, table: str, req: TableRequirement, row: Dict[str, Any]) -> bool:
        """Check that generated row values satisfy all predicates.

        Returns True if all predicates are satisfied, False otherwise.
        """
        from parseval.plan.rex import concrete as _concrete, Environment
        for col, op, value in req.predicates:
            if col not in row or row[col] is None:
                continue
            actual = row[col]
            if op == ">" and isinstance(value, (int, float)):
                if not (isinstance(actual, (int, float)) and actual > value):
                    return False
            elif op == ">=" and isinstance(value, (int, float)):
                if not (isinstance(actual, (int, float)) and actual >= value):
                    return False
            elif op == "<" and isinstance(value, (int, float)):
                if not (isinstance(actual, (int, float)) and actual < value):
                    return False
            elif op == "<=" and isinstance(value, (int, float)):
                if not (isinstance(actual, (int, float)) and actual <= value):
                    return False
            elif op == "=":
                if actual != value:
                    return False
        return True
```

Then modify `_build_row` to validate and retry:

```python
    def _build_row(self, table: str, req: TableRequirement, shared: Dict[str, Any]) -> Dict[str, Any]:
        for attempt in range(3):
            row: Dict[str, Any] = {}
            # Equivalence class values (JOIN/GROUP BY coordination).
            for col in self.instance.tables.get(table, {}):
                key = f"{table}.{col}"
                if key in shared:
                    row[col] = shared[key]
            # Fixed values override equivalences (WHERE constraints are more specific).
            row.update(req.fixed_values)
            # Predicates: collect per-column, then satisfy all.
            col_preds: Dict[str, List[Tuple[str, Any]]] = {}
            for col, op, value in req.predicates:
                if col not in row:
                    col_preds.setdefault(col, []).append((op, value))
            for col, preds in col_preds.items():
                row[col] = self._satisfy_all(preds)
            # NOT NULL enforcement: generate defaults for columns that must not be NULL.
            for col in req.not_null:
                if col not in row or row[col] is None:
                    row[col] = self._default_value(table, col)
            # Must NULL (overrides NOT NULL — the query predicate takes precedence).
            for col in req.must_null:
                row[col] = None
            # Validate and retry if needed.
            if self._validate_row(table, req, row):
                return row
        return row  # Return last attempt even if validation fails
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestRowValidation -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_enhancements.py
git commit -m "feat(resolver): validate generated rows against predicates with retry"
```

---

### Task 7: Scalar Subquery Detection in Propagator (Phase A, Step 4)

**Problem:** The evaluator skips atoms containing subqueries (`decompose_atoms` filters them). These must be resolved before evaluation. Currently `_resolve_subquery_predicates` in the engine handles this, but it should move to the speculative layer.

**Files:**
- Modify: `tests/symbolic/test_speculate_enhancements.py`
- Modify: `src/parseval/symbolic/speculate.py:194-201` (Propagator Filter handler)
- Modify: `src/parseval/symbolic/speculate.py:63-68` (BranchSpec dataclass)

- [ ] **Step 1: Write the failing test**

Append to `tests/symbolic/test_speculate_enhancements.py`:

```python
class TestScalarSubqueryDetection(unittest.TestCase):
    """Propagator should detect scalar subqueries in Filter atoms and mark them for deferred evaluation."""

    def test_scalar_subquery_detected(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator

        schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
        sql = "SELECT * FROM t WHERE val > (SELECT AVG(val) FROM t)"
        instance = Instance(ddls=schema, name="test_scalar", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        alias_map = plan.alias_map

        propagator = Propagator(plan, instance, alias_map, "sqlite")
        specs = propagator.propagate()

        pos_spec = specs[0]
        self.assertTrue(hasattr(pos_spec, 'deferred'),
            "BranchSpec should have a 'deferred' field")
        self.assertGreater(len(pos_spec.deferred), 0,
            "Should have at least one deferred subquery atom")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestScalarSubqueryDetection -v
```

Expected: FAIL — `BranchSpec` has no `deferred` field.

- [ ] **Step 3: Add deferred field to BranchSpec and detection to Filter handler**

In `src/parseval/symbolic/speculate.py`, add `deferred` field to `BranchSpec` (line 63):

```python
@dataclass
class BranchSpec:
    """Requirements for one branch outcome."""
    branch: str
    requirements: Dict[str, TableRequirement] = field(default_factory=dict)
    equivalences: ColumnUnionFind = field(default_factory=ColumnUnionFind)
    deferred: List[exp.Expression] = field(default_factory=list)
```

Then modify the Filter handler in `_propagate_step` (line 194) to detect scalar subqueries:

```python
        elif isinstance(step, Filter):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition:
                if step is negate_step:
                    self._extract_negated_predicates(step.condition, spec)
                else:
                    self._extract_predicates(step.condition, spec)
                # Detect scalar subquery atoms for deferred evaluation.
                for atom in self._iter_scalar_subquery_atoms(step.condition):
                    spec.deferred.append(atom)
```

Then add `_iter_scalar_subquery_atoms` to the `Propagator` class:

```python
    def _iter_scalar_subquery_atoms(self, predicate: exp.Expression):
        """Yield atoms that contain a scalar subquery comparison."""
        if isinstance(predicate, exp.And):
            yield from self._iter_scalar_subquery_atoms(predicate.left)
            yield from self._iter_scalar_subquery_atoms(predicate.right)
        elif isinstance(predicate, exp.Paren):
            yield from self._iter_scalar_subquery_atoms(predicate.this)
        elif isinstance(predicate, exp.Or):
            yield from self._iter_scalar_subquery_atoms(predicate.left)
        else:
            if predicate.find(exp.Subquery) and isinstance(predicate, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
                yield predicate
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestScalarSubqueryDetection -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_enhancements.py
git commit -m "feat(propagator): detect scalar subquery atoms for deferred evaluation"
```

---

### Task 8: Deferred Subquery Resolution in Resolver (Phase B, Step 7)

**Problem:** After the Resolver generates initial rows, scalar subqueries like `col > (SELECT AVG(x) FROM t)` need concrete evaluation against the seeded data, then outer rows need adjustment.

**Files:**
- Modify: `tests/symbolic/test_speculate_enhancements.py`
- Modify: `src/parseval/symbolic/speculate.py` (Resolver class)

- [ ] **Step 1: Write the failing test**

Append to `tests/symbolic/test_speculate_enhancements.py`:

```python
class TestDeferredSubqueryResolution(unittest.TestCase):
    """Resolver should evaluate deferred scalar subqueries and adjust outer rows."""

    def test_deferred_subquery_adjusts_outer_value(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import speculate

        schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
        # Insert a row with val=10 so AVG(val)=10
        instance = Instance(ddls=schema, name="test_deferred", dialect="sqlite")
        instance.create_row("t", values={"val": 10})

        sql = "SELECT * FROM t WHERE val > (SELECT AVG(val) FROM t)"
        result = speculate(
            Plan(preprocess_sql(sql, instance, dialect="sqlite")),
            instance, instance.alias_map, "sqlite"
        )

        # After deferred resolution, there should be a row with val > 10
        t_rows = instance.get_rows("t")
        vals = [r["val"].concrete for r in t_rows if r["val"].concrete is not None]
        has_gt_avg = any(v > 10 for v in vals if isinstance(v, (int, float)))
        self.assertTrue(has_gt_avg,
            f"Should have a row with val > 10 (AVG), got vals={vals}")
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestDeferredSubqueryResolution -v
```

Expected: FAIL — the Resolver doesn't evaluate deferred subqueries.

- [ ] **Step 3: Implement deferred subquery resolution in Resolver**

In `src/parseval/symbolic/speculate.py`, add `_resolve_deferred` to `Resolver` and call it from `resolve`:

```python
    def resolve(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
        """Produce concrete rows for each table in the spec."""
        shared_values = self._resolve_equivalences(spec)
        order = self._creation_order(spec)
        result: Dict[str, List[Dict[str, Any]]] = {}

        for table_key in order:
            if table_key not in spec.requirements:
                continue
            req = spec.requirements[table_key]
            physical_table = req.table if not req.alias else req.table
            if "__" in physical_table:
                physical_table = physical_table.split("__")[0]
            rows = self._resolve_table(physical_table, req, shared_values)
            result.setdefault(physical_table, []).extend(rows)

        # Resolve deferred scalar subqueries after initial rows are generated.
        if spec.deferred:
            self._resolve_deferred(spec)

        return result

    def _resolve_deferred(self, spec: BranchSpec):
        """Evaluate deferred scalar subqueries and adjust outer rows.

        For atoms like `col > (SELECT AVG(val) FROM t)`:
        1. Evaluate the subquery against the current instance.
        2. Adjust the outer row's column value to satisfy the comparison.
        """
        from parseval.plan.rex import concrete as _concrete, Environment

        for atom in spec.deferred:
            if not isinstance(atom, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ)):
                continue

            left, right = atom.this, atom.expression
            subq_side, outer_side = None, None
            if right and right.find(exp.Subquery):
                subq_side, outer_side = right, left
            elif left and left.find(exp.Subquery):
                subq_side, outer_side = left, right
            if subq_side is None:
                continue

            # Evaluate the subquery
            subq_result = self._evaluate_scalar_subquery(subq_side)
            if subq_result is None:
                continue

            # Find the outer column and adjust its value
            if not isinstance(outer_side, exp.Column):
                continue
            table = self._resolve_table_name(outer_side.table or "")
            col_name = outer_side.name
            if table not in self.instance.tables:
                continue

            rows = self.instance.get_rows(table)
            if not rows:
                continue

            # Compute the target value based on the comparison operator
            target = self._compute_target_value(atom, subq_result)
            if target is not None:
                # Adjust the first row
                if col_name in rows[0].columns:
                    rows[0][col_name].set("concrete", target)
                    rows[0][col_name].set("is_bound", True)

    def _evaluate_scalar_subquery(self, subq_node: exp.Expression) -> Optional[Any]:
        """Evaluate a scalar subquery against the current instance."""
        from parseval.plan.rex import concrete as _concrete, Environment

        subq = subq_node.find(exp.Subquery) if isinstance(subq_node, exp.Subquery) else subq_node
        if not isinstance(subq, exp.Subquery):
            subq = subq_node
        inner_select = subq.this if isinstance(subq, exp.Subquery) else subq
        if not isinstance(inner_select, exp.Select):
            return None

        from_clause = inner_select.args.get("from")
        if not from_clause:
            return None
        from_table = from_clause.this
        if not isinstance(from_table, exp.Table):
            return None

        table_name = from_table.alias_or_name
        if table_name not in self.instance.tables:
            return None

        rows = self.instance.get_rows(table_name)
        if not rows:
            return None

        # Simple AVG/COUNT/SUM evaluation
        projections = inner_select.expressions
        if not projections:
            return None

        proj = projections[0]
        values = []
        for row in rows:
            for col in proj.find_all(exp.Column):
                if col.name in row.columns:
                    v = row[col.name].concrete
                    if v is not None:
                        values.append(v)

        if not values:
            return None

        if proj.find(exp.Avg):
            return sum(values) / len(values)
        elif proj.find(exp.Count):
            return len(values)
        elif proj.find(exp.Sum):
            return sum(values)
        elif proj.find(exp.Max):
            return max(values)
        elif proj.find(exp.Min):
            return min(values)

        # Non-aggregate: return first value
        return values[0] if values else None

    def _resolve_table_name(self, name: str) -> str:
        """Resolve alias to physical table name."""
        from parseval.helper import normalize_name
        return normalize_name(name) if name else ""

    def _compute_target_value(self, atom: exp.Expression, subq_result: Any) -> Optional[Any]:
        """Compute a value that satisfies the comparison atom."""
        op = type(atom).__name__.lower()
        if isinstance(atom, exp.GT):
            if isinstance(subq_result, (int, float)):
                return int(subq_result) + 1
        elif isinstance(atom, exp.GTE):
            if isinstance(subq_result, (int, float)):
                return int(subq_result)
        elif isinstance(atom, exp.LT):
            if isinstance(subq_result, (int, float)):
                return int(subq_result) - 1
        elif isinstance(atom, exp.LTE):
            if isinstance(subq_result, (int, float)):
                return int(subq_result)
        elif isinstance(atom, exp.EQ):
            return subq_result
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_speculate_enhancements.py::TestDeferredSubqueryResolution -v
```

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_enhancements.py
git commit -m "feat(resolver): evaluate deferred scalar subqueries and adjust outer rows"
```

---

### Task 9: Remove `_ensure_base_rows` from Engine (Phase C, Step 8)

**Files:**
- Modify: `src/parseval/symbolic/engine.py:214` (remove call)
- Modify: `src/parseval/symbolic/engine.py:863-879` (remove method)

- [ ] **Step 1: Remove the call from generate()**

In `src/parseval/symbolic/engine.py`, remove the Phase 0b call (line 214):

```python
        # Remove this line:
        # self._ensure_base_rows()
```

- [ ] **Step 2: Remove the method**

Delete `_ensure_base_rows` method (lines 863-879).

- [ ] **Step 3: Run full test suite + BIRD benchmark**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass. BIRD benchmark pass rate >= baseline.

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/engine.py
git commit -m "refactor(engine): remove _ensure_base_rows, handled by Resolver FK discovery"
```

---

### Task 10: Remove `_seed_for_offset` from Engine (Phase C, Step 9)

**Files:**
- Modify: `src/parseval/symbolic/engine.py:217` (remove call)
- Modify: `src/parseval/symbolic/engine.py:390-410` (remove method)

- [ ] **Step 1: Remove the call from generate()**

In `src/parseval/symbolic/engine.py`, remove the Phase 0c call (line 217):

```python
        # Remove this line:
        # self._seed_for_offset()
```

- [ ] **Step 2: Remove the method**

Delete `_seed_for_offset` method (lines 390-410).

- [ ] **Step 3: Run full test suite + BIRD benchmark**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass. BIRD benchmark pass rate >= baseline.

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/engine.py
git commit -m "refactor(engine): remove _seed_for_offset, handled by Propagator Limit handler"
```

---

### Task 11: Remove `_create_having_count_rows` from Engine (Phase C, Step 10)

**Files:**
- Modify: `src/parseval/symbolic/engine.py:220` (remove call)
- Modify: `src/parseval/symbolic/engine.py:961-1018` (remove method)

- [ ] **Step 1: Remove the call from generate()**

In `src/parseval/symbolic/engine.py`, remove the Phase 0e call (line 220):

```python
        # Remove this line:
        # self._create_having_count_rows()
```

- [ ] **Step 2: Remove the method**

Delete `_create_having_count_rows` method (lines 961-1018).

- [ ] **Step 3: Run full test suite + BIRD benchmark**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass. BIRD benchmark pass rate >= baseline.

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/engine.py
git commit -m "refactor(engine): remove _create_having_count_rows, handled by Propagator HAVING handler"
```

---

### Task 12: Remove `_enrich_for_semantics` from Engine (Phase C, Step 11)

**Files:**
- Modify: `src/parseval/symbolic/engine.py:229` (remove call)
- Modify: `src/parseval/symbolic/engine.py:1020-1076` (remove method + helper)

- [ ] **Step 1: Remove the call from generate()**

In `src/parseval/symbolic/engine.py`, remove the Phase 0g call (line 229):

```python
        # Remove this line:
        # self._enrich_for_semantics()
```

- [ ] **Step 2: Remove the method and helper**

Delete `_enrich_for_semantics` method (lines 1020-1068) and `_generate_null_row` helper (lines 1070-1075).

- [ ] **Step 3: Run full test suite + BIRD benchmark**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass. BIRD benchmark pass rate >= baseline.

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/engine.py
git commit -m "refactor(engine): remove _enrich_for_semantics, handled by Propagator aggregate NULL detection"
```

---

### Task 13: Remove `_resolve_subquery_predicates` from Engine (Phase C, Step 12)

**Files:**
- Modify: `src/parseval/symbolic/engine.py:220` (remove call)
- Modify: `src/parseval/symbolic/engine.py:647-700` (remove method + helpers)

- [ ] **Step 1: Remove the call from generate()**

In `src/parseval/symbolic/engine.py`, remove the Phase 0d call (line 220):

```python
        # Remove this line:
        # self._resolve_subquery_predicates()
```

- [ ] **Step 2: Remove the method and helpers**

Delete `_resolve_subquery_predicates` (lines 647-666), `_iter_subquery_atoms` (lines 668-680), and `_resolve_one_subquery_atom` (lines 682-699).

- [ ] **Step 3: Run full test suite + BIRD benchmark**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass. BIRD benchmark pass rate >= baseline.

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/engine.py
git commit -m "refactor(engine): remove _resolve_subquery_predicates, handled by Propagator deferred evaluation"
```

---

### Task 14: Verify Clean Engine State (Phase C, Step 13)

**Files:**
- Read: `src/parseval/symbolic/engine.py`

- [ ] **Step 1: Verify the engine's generate() method is thin**

Read `engine.py` and verify the `generate()` method has this clean flow:

```python
def generate(self, thresholds=None):
    # Phase 0: Speculate (comprehensive seeding)
    self._speculate_all_branches()

    # Phase 0f: SMT repair (Z3 fallback for self-joins, NOT IN, complex OR)
    self._smt_repair_where()

    # Phase 1: Evaluate
    tree = evaluator.evaluate(tree)

    # Phase 2: Cover gaps (solver loop)
    for iteration in range(self.max_iterations):
        ...
```

The only Phase 0 methods remaining should be `_speculate_all_branches` and `_smt_repair_where`.

- [ ] **Step 2: Verify _smt_repair_where contract**

Confirm `_smt_repair_where` handles exactly:
- (a) Self-join predicates requiring per-alias Z3 variables
- (b) NOT IN predicates requiring anti-value constraints
- (c) Complex OR predicates where the speculative layer's conjunctive heuristic fails
- (d) Short-circuits when rows already satisfy the WHERE clause

- [ ] **Step 3: Run BIRD benchmark one final time**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/test_symbolic_bird.py -v -s 2>&1 | tail -20
```

Expected: Pass rate >= baseline from Task 1.

- [ ] **Step 4: Run full test suite**

Run:
```bash
.venv/bin/python3 -m pytest tests/symbolic/ -v
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/engine.py
git commit -m "refactor(engine): clean engine state — only _smt_repair_where remains as Z3 fallback"
```
