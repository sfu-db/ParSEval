# speculate.py Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose the Propagator god-class into per-step-type handlers and eliminate all string-based identity fallbacks for correctness.

**Architecture:** Three layers — orchestrator (branch types), step handlers (per-step constraint derivation), resolver (unchanged). The planner stamps `PARSEVAL_COLUMN_ID` on all columns; the speculator reads identity instead of re-deriving from strings.

**Tech Stack:** Python, sqlglot, pytest

**Design Spec:** `docs/superpowers/specs/2026-06-11-speculate-refactor-design.md`

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `tests/symbolic/test_propagator.py` | Create | Unit tests for Propagator step handlers |
| `src/parseval/plan/planner.py` | Modify | Fix identity gaps (SubPlan correlation, `_a_0` operands) |
| `src/parseval/symbolic/speculate.py` | Modify | Remove fallbacks, decompose handlers, clean orchestrator |

---

### Task 1: Create Propagator test helpers

**Files:**
- Create: `tests/symbolic/test_propagator.py`

Create a test file with helpers to build simple plans from SQL strings and run the Propagator against them.

- [ ] **Step 1: Write test helpers**

```python
"""Unit tests for the Propagator class in speculate.py."""
import pytest
from sqlglot import exp

from parseval.instance import Instance
from parseval.plan.planner import Plan, Scan, Filter, Join, Aggregate, Having, Project
from parseval.symbolic.speculate import Propagator, BranchSpec, SpeculateConfig


def _make_instance(tables: dict[str, dict[str, str]]) -> Instance:
    """Create a minimal Instance from a table->columns dict.

    tables = {"orders": {"id": "INT", "amount": "REAL", "status": "TEXT"}}
    """
    schema = {}
    for table_name, columns in tables.items():
        cols = []
        for col_name, col_type in columns.items():
            cols.append(f"{col_name} {col_type}")
        schema[table_name] = f"CREATE TABLE {table_name} ({', '.join(cols)})"
    return Instance.from_ddl(schema)


def _make_plan(sql: str, instance: Instance) -> Plan:
    """Build a Plan from a SQL string with identity resolved."""
    expression = exp.parse_one(sql, dialect="sqlite")
    plan = Plan(expression, instance=instance)
    return plan


def _propagate(sql: str, tables: dict[str, dict[str, str]],
               config: SpeculateConfig | None = None) -> list[BranchSpec]:
    """Run Propagator on a SQL string and return branch specs."""
    instance = _make_instance(tables)
    plan = _make_plan(sql, instance)
    propagator = Propagator(plan, instance, "sqlite", config=config)
    return propagator.propagate()
```

- [ ] **Step 2: Run test to verify helpers work**

Run: `python -c "from tests.symbolic.test_propagator import _make_instance, _make_plan, _propagate; print('OK')"`

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/symbolic/test_propagator.py
git commit -m "test: add Propagator test helpers"
```

---

### Task 2: Test positive branch propagation

**Files:**
- Modify: `tests/symbolic/test_propagator.py`

- [ ] **Step 1: Write the failing test**

```python
def test_positive_simple_select():
    """Positive branch for SELECT with WHERE should produce one spec."""
    sql = "SELECT x.id, x.amount FROM orders AS x WHERE x.amount > 100"
    tables = {"orders": {"id": "INT", "amount": "REAL"}}
    specs = _propagate(sql, tables, SpeculateConfig(positive=1, negative=0, null=0,
                                                     left_unmatched=0, right_unmatched=0,
                                                     having_fail=0, case_else=0, boundary=0))
    assert len(specs) == 1
    assert specs[0].branch == "positive"
    # Should have orders table in requirements
    assert any(tc.table == "orders" for tc in specs[0].requirements.values())
    # Should have the WHERE condition stored
    orders_req = next(tc for tc in specs[0].requirements.values() if tc.table == "orders")
    assert len(orders_req.constraints) >= 1  # WHERE condition + NOT NULL


def test_positive_requires_identity_on_columns():
    """Every Column in positive branch constraints must carry PARSEVAL_COLUMN_ID."""
    from parseval.identity import column_identity
    sql = "SELECT x.id FROM orders AS x WHERE x.amount > 100"
    tables = {"orders": {"id": "INT", "amount": "REAL"}}
    specs = _propagate(sql, tables, SpeculateConfig(positive=1, negative=0, null=0,
                                                     left_unmatched=0, right_unmatched=0,
                                                     having_fail=0, case_else=0, boundary=0))
    spec = specs[0]
    for tc in spec.requirements.values():
        for constraint in tc.constraints:
            for col in constraint.find_all(exp.Column):
                cid = column_identity(col)
                assert cid is not None, f"Column {col.sql()} lacks identity"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_propagator.py::test_positive_simple_select tests/symbolic/test_propagator.py::test_positive_requires_identity_on_columns -v`

Expected: PASS (the planner already stamps identity on WHERE columns)

- [ ] **Step 3: Commit**

```bash
git add tests/symbolic/test_propagator.py
git commit -m "test: add positive branch propagation tests"
```

---

### Task 3: Test negative branch propagation

**Files:**
- Modify: `tests/symbolic/test_propagator.py`

- [ ] **Step 1: Write the failing test**

```python
def test_negative_branch_negates_filter():
    """Negative branch should negate the WHERE condition."""
    from parseval.plan.rex import negate_predicate
    sql = "SELECT x.id FROM orders AS x WHERE x.amount > 100"
    tables = {"orders": {"id": "INT", "amount": "REAL"}}
    config = SpeculateConfig(positive=0, negative=1, null=0,
                             left_unmatched=0, right_unmatched=0,
                             having_fail=0, case_else=0, boundary=0)
    specs = _propagate(sql, tables, config)
    assert len(specs) >= 1
    neg_spec = specs[0]
    assert neg_spec.branch.startswith("negative")
    # Should have a negated condition (<= instead of >)
    orders_req = next(tc for tc in neg_spec.requirements.values() if tc.table == "orders")
    has_negated = False
    for constraint in orders_req.constraints:
        if isinstance(constraint, (exp.LTE, exp.LT)):
            has_negated = True
    assert has_negated, "Expected negated condition (LTE/LT) in negative branch"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_propagator.py::test_negative_branch_negates_filter -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/symbolic/test_propagator.py
git commit -m "test: add negative branch propagation tests"
```

---

### Task 4: Test join propagation

**Files:**
- Modify: `tests/symbolic/test_propagator.py`

- [ ] **Step 1: Write the failing test**

```python
def test_join_creates_equivalence():
    """Join should link source_key and join_key via Union-Find."""
    sql = """
        SELECT o.id, c.name
        FROM orders AS o
        JOIN customers AS c ON o.customer_id = c.id
    """
    tables = {
        "orders": {"id": "INT", "customer_id": "INT", "amount": "REAL"},
        "customers": {"id": "INT", "name": "TEXT"},
    }
    specs = _propagate(sql, tables, SpeculateConfig(positive=1, negative=0, null=0,
                                                     left_unmatched=0, right_unmatched=0,
                                                     having_fail=0, case_else=0, boundary=0))
    assert len(specs) == 1
    spec = specs[0]
    # Both tables should be in requirements
    table_names = {tc.table for tc in spec.requirements.values()}
    assert "orders" in table_names
    assert "customers" in table_names
    # Union-Find should have equivalence groups
    groups = spec.equivalences.groups()
    assert len(groups) >= 1, "Expected at least one equivalence group from join"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_propagator.py::test_join_creates_equivalence -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/symbolic/test_propagator.py
git commit -m "test: add join propagation tests"
```

---

### Task 5: Test HAVING/GROUP BY propagation

**Files:**
- Modify: `tests/symbolic/test_propagator.py`

- [ ] **Step 1: Write the failing test**

```python
def test_having_sets_min_rows():
    """HAVING with COUNT should set min_rows on the table."""
    sql = """
        SELECT o.customer_id, COUNT(*) AS cnt
        FROM orders AS o
        GROUP BY o.customer_id
        HAVING COUNT(*) > 2
    """
    tables = {"orders": {"id": "INT", "customer_id": "INT", "amount": "REAL"}}
    specs = _propagate(sql, tables, SpeculateConfig(positive=1, negative=0, null=0,
                                                     left_unmatched=0, right_unmatched=0,
                                                     having_fail=0, case_else=0, boundary=0))
    assert len(specs) == 1
    spec = specs[0]
    # min_rows should be > 1 due to COUNT > 2
    for tc in spec.requirements.values():
        if tc.table == "orders":
            assert tc.min_rows >= 3, f"Expected min_rows >= 3 for COUNT > 2, got {tc.min_rows}"
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_propagator.py::test_having_sets_min_rows -v`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/symbolic/test_propagator.py
git commit -m "test: add HAVING/GROUP BY propagation tests"
```

---

### Task 6: Fix planner identity gap for SubPlan.correlation columns

**Files:**
- Modify: `src/parseval/plan/planner.py:165-194`

The `_annotate` method processes SubPlan correlation columns with `allow_unresolved=True`, and `_visible_columns(subplan)` returns empty because SubPlan has no chain dependencies. Fix: resolve correlation columns against the SubPlan's **consumer** step instead.

- [ ] **Step 1: Write the failing test**

```python
def test_subplan_correlation_has_identity():
    """SubPlan correlation columns must carry PARSEVAL_COLUMN_ID."""
    from parseval.identity import column_identity, PARSEVAL_COLUMN_ID
    from parseval.plan.planner import SubPlan

    sql = """
        SELECT o.id FROM orders AS o
        WHERE EXISTS (SELECT 1 FROM customers AS c WHERE c.id = o.customer_id)
    """
    tables = {
        "orders": {"id": "INT", "customer_id": "INT"},
        "customers": {"id": "INT", "name": "TEXT"},
    }
    instance = _make_instance(tables)
    plan = _make_plan(sql, instance)

    # Find the SubPlan
    subplans = [s for s in plan.ordered_steps if isinstance(s, SubPlan)]
    assert len(subplans) >= 1
    sub = subplans[0]

    # Correlation columns should have identity
    for col in sub.correlation:
        cid = column_identity(col)
        assert cid is not None, f"Correlation column {col.sql()} lacks identity"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/symbolic/test_propagator.py::test_subplan_correlation_has_identity -v`

Expected: FAIL — correlation columns return `None` from `column_identity`

- [ ] **Step 3: Fix the planner**

In `src/parseval/plan/planner.py`, in the `_annotate` method (around line 165), change the SubPlan branch to resolve correlation columns against the consumer step:

```python
# In _annotate, inside the SubPlan branch (around line 165-194):
if isinstance(step, SubPlan):
    # ... existing inner step processing ...
    resolve_exprs = tuple(
        col for col in (getattr(step, "correlation", None) or ())
        if isinstance(col, exp.Expression)
    )
    # Resolve correlation columns against the consumer (outer step)
    consumer = getattr(step, "consumer", None)
    for col in resolve_exprs:
        if consumer is not None:
            resolved_id = _resolve_column_id(
                col, consumer, self._instance, allow_unresolved=True,
            )
            if resolved_id is not None:
                col.meta[PARSEVAL_COLUMN_ID] = resolved_id
                _enrich_identity_column(col, resolved_id, self._instance, set_column_meta, DataType)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_propagator.py::test_subplan_correlation_has_identity -v`

Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `python -m pytest tests/symbolic/ tests/plan/ tests/experiment/ -x -q`

Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add src/parseval/plan/planner.py tests/symbolic/test_propagator.py
git commit -m "fix(planner): stamp identity on SubPlan correlation columns"
```

---

### Task 7: Remove string fallbacks from _propagate_step

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Replace all `if col_id and col_id.relation ... else self._rel(...)` patterns in `_propagate_step` with identity-only paths. The affected branches are: Aggregate (lines 781-789, 810-818), Filter (lines 843-861), Join (lines 870-888).

- [ ] **Step 1: Write a test that fails if fallback is used**

```python
def test_propagate_uses_identity_not_strings():
    """Propagator must not use string-based table resolution."""
    from unittest.mock import patch
    from parseval.symbolic.speculate import _relation_for_table

    sql = "SELECT x.id FROM orders AS x WHERE x.amount > 100"
    tables = {"orders": {"id": "INT", "amount": "REAL"}}
    instance = _make_instance(tables)
    plan = _make_plan(sql, instance)

    call_count = 0
    original = _relation_for_table

    def patched_rel(inst, name, alias_map=None):
        nonlocal call_count
        call_count += 1
        return original(inst, name, alias_map=alias_map)

    with patch("parseval.symbolic.speculate._relation_for_table", side_effect=patched_rel):
        propagator = Propagator(plan, instance, "sqlite")
        specs = propagator.propagate()

    # After removing fallbacks, _relation_for_table should only be called
    # for Scan steps (to register the table), not for column resolution.
    # A simple SELECT has 1 Scan, so expect ~1 call.
    assert call_count <= 2, f"Expected <=2 calls to _relation_for_table, got {call_count}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/symbolic/test_propagator.py::test_propagate_uses_identity_not_strings -v`

Expected: FAIL — current code calls `_relation_for_table` many times for column resolution

- [ ] **Step 3: Remove fallbacks from Aggregate branch (GROUP BY)**

In `_propagate_step`, the Aggregate GROUP BY handling (around line 778-797):

```python
# BEFORE:
col_id = column_identity(col)
if col_id and col_id.relation:
    relation = col_id.relation
    matched = col_id.name.normalized
else:
    relation = self._rel(col.table or "")
    matched = col.name

# AFTER:
col_id = column_identity(col)
if col_id is None:
    raise ValueError(f"Column {col.sql()} lacks identity — planner must stamp PARSEVAL_COLUMN_ID")
relation = col_id.relation
matched = col_id.name.normalized
```

- [ ] **Step 4: Remove fallbacks from Aggregate branch (COUNT NULL detection)**

Same pattern around line 810-818.

- [ ] **Step 5: Remove fallbacks from Join branch**

Around line 870-888. Replace the fallback with:

```python
sk_id = column_identity(sk) if isinstance(sk, exp.Column) else None
jk_id = column_identity(jk) if isinstance(jk, exp.Column) else None
if sk_id is None or jk_id is None:
    raise ValueError(f"Join key lacks identity: sk={sk}, jk={jk}")
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/symbolic/test_propagator.py::test_propagate_uses_identity_not_strings -v`

Expected: PASS

- [ ] **Step 7: Run full test suite**

Run: `python -m pytest tests/symbolic/ tests/plan/ tests/experiment/ -x -q`

Expected: All existing tests still pass

- [ ] **Step 8: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_propagator.py
git commit -m "refactor(speculate): remove string fallbacks from _propagate_step"
```

---

### Task 8: Remove string fallbacks from helper methods

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Remove fallback patterns from:
- `_collect_null_target_columns` (lines 1264-1274, 1286-1304, 1318-1334)
- `_annotate_column_types` (lines 1552-1557, 1577-1582)
- `_extract_boundary_from_conjunct` (lines 1502-1510)
- `_add_null_constraint_for_col` (lines 1620-1628)
- `_find_inner_corr_column` (lines 2011-2019)
- `_find_corr_inner_column` (lines 2048-2056)
- `_find_counted_table` (lines 2143-2156, 2172-2187)
- `_extract_agg_value_from_expr` (lines 2316-2324)
- `_find_table_for_expr` (lines 1088-1119)

Each instance follows the same pattern: replace `if col_id ... else ...` with `if col_id is None: raise ValueError(...)`.

- [ ] **Step 1: Remove fallbacks from `_collect_null_target_columns`**

Three instances. Each uses the pattern:
```python
# BEFORE:
col_id = column_identity(col)
if col_id and col_id.relation:
    tname = col_id.relation.name.normalized
    matched = col_id.name.normalized
else:
    tname = col.table or ""
    matched = col.name

# AFTER:
col_id = column_identity(col)
if col_id is None or col_id.relation is None:
    raise ValueError(f"Column {col.sql()} lacks identity")
tname = col_id.relation.name.normalized if col_id.relation.name else ""
matched = col_id.name.normalized
```

- [ ] **Step 2: Remove fallbacks from `_annotate_column_types`**

Two instances. Replace `_lookup_col_type` calls with `column_meta`:
```python
# BEFORE:
col_type_str = _lookup_col_type(self.instance, col_relation, col.name)
if col_type_str:
    col.type = DataType.build(col_type_str)

# AFTER:
meta = column_meta(col)
if meta and "domain" in meta:
    col.type = meta["domain"]
```

- [ ] **Step 3: Remove fallbacks from remaining helpers**

Apply the same pattern to `_extract_boundary_from_conjunct`, `_add_null_constraint_for_col`, `_find_inner_corr_column`, `_find_corr_inner_column`, `_find_counted_table`, `_extract_agg_value_from_expr`, `_find_table_for_expr`.

- [ ] **Step 4: Run test suite**

Run: `python -m pytest tests/symbolic/ tests/plan/ tests/experiment/ -x -q`

Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): remove string fallbacks from helper methods"
```

---

### Task 9: Remove dead helper functions

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Remove functions that are no longer needed after fallback removal:
- `_build_alias_map` — alias info comes from `RelationId`
- `_lookup_col_type` — types come from `column_meta`
- `_ensure_solver_var` — columns already carry identity
- `_rel(name: str)` on Propagator — no string-to-RelationId resolution
- `_solver_column` — simplify to take `ColumnId` + `RelationId` directly
- `_alias_map` attribute on Propagator

- [ ] **Step 1: Remove `_build_alias_map`**

Delete the function (lines 95-105) and remove `self._alias_map = _build_alias_map(plan)` from `Propagator.__init__`.

- [ ] **Step 2: Remove `_lookup_col_type`**

Delete the function (lines 69-86). All callers now use `column_meta` instead.

- [ ] **Step 3: Remove `_ensure_solver_var`**

Delete the function (lines 153-174). All callers now read identity directly.

- [ ] **Step 4: Remove `_rel` and `_alias_map` from Propagator**

Delete the `_rel` method and `self._alias_map` from `__init__`. Update `_solver_column` to take `ColumnId` + `RelationId` directly instead of string table name.

- [ ] **Step 5: Simplify `_solver_column`**

Change signature from `_solver_column(instance, table, col_name, row_scope, alias_map)` to `_solver_column(instance, relation, col_name, row_scope)` that takes `RelationId` directly.

Note: `_solver_column` is still needed — it creates new `exp.Column` nodes with `SolverVar` + type annotations. The change is that it takes `RelationId` directly instead of a string table name.

Also ensure `speculate()` calls `plan.annotations` before creating the Propagator, to trigger identity preparation on all steps:

```python
def speculate(plan, instance, dialect="sqlite", config=None):
    if config is None:
        config = SpeculateConfig.gold_non_empty()
    _ = plan.annotations  # Ensure identity is prepared on all steps
    propagator = Propagator(plan, instance, dialect, config=config)
    ...
```

- [ ] **Step 6: Run test suite**

Run: `python -m pytest tests/symbolic/ tests/plan/ tests/experiment/ -x -q`

Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): remove dead helper functions"
```

---

### Task 10: Add dispatch table and _walk_step method

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Add the dispatch mechanism that will replace `_propagate_step`.

- [ ] **Step 1: Add negation context and dispatch table to Propagator**

```python
class Propagator:
    # ... existing __init__ ...

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

    def __init__(self, ...):
        # ... existing init ...
        self._negate_step: Optional[Step] = None
        self._negate_conjunct: int = 0
```

- [ ] **Step 2: Add `_walk_step` method**

```python
def _walk_step(self, step: Step, spec: BranchSpec) -> None:
    """Walk the plan top-down, dispatching to step handlers."""
    handler_name = self._HANDLER_MAP.get(type(step))
    if handler_name:
        getattr(self, handler_name)(step, spec)
    for dep in step.chain_dependencies:
        self._walk_step(dep, spec)
    for sub in step.subplan_dependencies:
        self._derive_subplan(sub, spec, parent_condition=getattr(step, "condition", None))
```

- [ ] **Step 3: Verify the dispatch table maps to existing methods**

Run: `python -c "from parseval.symbolic.speculate import Propagator; print(list(Propagator._HANDLER_MAP.values()))"`

Expected: List of method names (they don't exist yet, but the table is defined)

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): add dispatch table and _walk_step"
```

---

### Task 11: Extract _derive_scan handler

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Extract the Scan branch from `_propagate_step` into `_derive_scan`.

- [ ] **Step 1: Write the handler**

```python
def _derive_scan(self, step: Scan, spec: BranchSpec) -> None:
    """Register table requirement for a Scan step."""
    name = step.name or ""
    # Use step.relation_id if available, else create synthetic
    relation = getattr(step, "relation_id", None)
    if relation is None:
        from parseval.identity import relation_id, identifier_name, RelationKind
        relation = relation_id(RelationKind.TABLE, identifier_name(name))
    table_name = relation.name.normalized if relation.name else ""
    if table_name in self.instance.tables:
        spec.require(relation)
    # For FROM-subquery scans, propagate into the inner plan.
    for sub in step.subplan_dependencies:
        if sub.inner:
            self._walk_step(sub.inner, spec)
```

- [ ] **Step 2: Run test suite**

Run: `python -m pytest tests/symbolic/test_propagator.py -x -q`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): extract _derive_scan handler"
```

---

### Task 12: Extract _derive_filter handler

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Extract the Filter branch from `_propagate_step` into `_derive_filter`.

- [ ] **Step 1: Write the handler**

```python
def _derive_filter(self, step: Filter, spec: BranchSpec) -> None:
    """Store WHERE conditions, handle negation, extract equalities."""
    if step.condition:
        if step is self._negate_step:
            conjuncts = self._split_conjuncts(step.condition)
            if len(conjuncts) > 1:
                for idx, conjunct in enumerate(conjuncts):
                    if idx == self._negate_conjunct:
                        negated = negate_predicate(conjunct.copy())
                        self._store_expression(negated, spec)
                    else:
                        self._store_expression(conjunct, spec)
            else:
                negated = negate_predicate(step.condition.copy())
                self._store_expression(negated, spec)
        else:
            self._store_expression(step.condition, spec)
        self._extract_column_equalities(step.condition, spec)
        for atom in self._iter_scalar_subquery_atoms(step.condition):
            spec.deferred.append(atom)
```

- [ ] **Step 2: Run test suite**

Run: `python -m pytest tests/symbolic/test_propagator.py -x -q`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): extract _derive_filter handler"
```

---

### Task 13: Extract _derive_join handler

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Extract the Join branch from `_propagate_step` into `_derive_join`.

- [ ] **Step 1: Write the handler**

```python
def _derive_join(self, step: Join, spec: BranchSpec) -> None:
    """Link join keys via equivalences, store join equality constraints."""
    for join_name, join_data in (step.joins or {}).items():
        source_keys = join_data.get("source_key", [])
        join_keys = join_data.get("join_key", [])
        for sk, jk in zip(source_keys, join_keys):
            sk_id = column_identity(sk) if isinstance(sk, exp.Column) else None
            jk_id = column_identity(jk) if isinstance(jk, exp.Column) else None
            if sk_id is None or jk_id is None:
                raise ValueError(f"Join key lacks identity: sk={sk}, jk={jk}")
            sk_rel = sk_id.relation
            jk_rel = jk_id.relation
            if sk_rel and jk_rel:
                spec.require(sk_rel)
                spec.require(jk_rel)
                spec.equate(sk_id, jk_id)
                # Store join equality as expression
                sk_table = sk_rel.name.normalized if sk_rel.name else ""
                jk_table = jk_rel.name.normalized if jk_rel.name else ""
                if sk_table and jk_table:
                    eq_expr = exp.EQ(
                        this=self._solver_col(sk_table, sk_id.name.normalized),
                        expression=self._solver_col(jk_table, jk_id.name.normalized),
                    )
                    spec.requirements[sk_rel].constraints.append(eq_expr)
                    spec.requirements[jk_rel].constraints.append(eq_expr)
                    req_jk = spec.require(jk_rel)
                    if jk_id not in req_jk.group_key_columns:
                        req_jk.group_key_columns.append(jk_id)
```

- [ ] **Step 2: Run test suite**

Run: `python -m pytest tests/symbolic/test_propagator.py -x -q`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): extract _derive_join handler"
```

---

### Task 14: Extract _derive_aggregate handler

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Extract the Aggregate branch from `_propagate_step` into `_derive_aggregate`.

- [ ] **Step 1: Write the handler**

```python
def _derive_aggregate(self, step: Aggregate, spec: BranchSpec) -> None:
    """Mark group key columns, add aggregate NULL constraints."""
    if step.group:
        for group_expr in step.group.values():
            for col in group_expr.find_all(exp.Column):
                col_id = column_identity(col)
                if col_id is None:
                    raise ValueError(f"Group column {col.sql()} lacks identity")
                relation = col_id.relation
                matched = col_id.name.normalized
                if matched and relation and relation.name:
                    table_name = relation.name.normalized
                    if table_name in self.instance.tables:
                        req = spec.require(relation)
                        gid = physical_column(matched, relation)
                        spec.equivalences.find(gid)
                        if gid not in req.group_key_columns:
                            req.group_key_columns.append(gid)
    # Aggregate NULL detection
    if not self._is_gold_mode:
        for agg_expr in step.aggregations:
            self._add_aggregate_null_constraints(agg_expr, spec)
    else:
        for agg_expr in step.aggregations:
            for count_node in agg_expr.find_all(exp.Count):
                if isinstance(count_node.this, exp.Star):
                    continue
                if count_node.args.get("distinct"):
                    continue
                for col in count_node.find_all(exp.Column):
                    col_id = column_identity(col)
                    if col_id is None:
                        raise ValueError(f"Count column {col.sql()} lacks identity")
                    relation = col_id.relation
                    matched = col_id.name.normalized
                    if matched and relation and relation.name and relation.name.normalized in self.instance.tables:
                        req = spec.require(relation)
                        if not _has_is_null(req.constraints, matched) and not _has_is_not_null(req.constraints, matched):
                            col_node = self._solver_col(relation.name.normalized, matched)
                            req.constraints.append(_make_is_not_null(col_node))
```

- [ ] **Step 2: Run test suite**

Run: `python -m pytest tests/symbolic/test_propagator.py -x -q`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): extract _derive_aggregate handler"
```

---

### Task 15: Extract remaining step handlers

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Extract `_derive_having`, `_derive_project`, `_derive_limit`, `_derive_sort`, `_derive_set_op`, and `_derive_subplan` from `_propagate_step`.

- [ ] **Step 1: Write `_derive_having`**

```python
def _derive_having(self, step: Having, spec: BranchSpec) -> None:
    """Store HAVING conditions, extract min group size."""
    if step.condition and step is not self._negate_step:
        if self._is_gold_mode:
            for scalar_cond in self._gold_having_scalar_constraints(step.condition):
                self._store_expression(scalar_cond, spec)
        else:
            self._store_expression(step.condition, spec)
        counted_relation = self._find_counted_table(step.condition)
        min_size = self._extract_min_group_size(step.condition)
        if counted_relation and counted_relation in spec.requirements:
            spec.requirements[counted_relation].min_rows = max(
                spec.requirements[counted_relation].min_rows, min_size,
            )
        else:
            for req in spec.requirements.values():
                req.min_rows = max(req.min_rows, min_size)
        self._extract_having_value_constraints(step.condition, spec, min_size)
    elif step is self._negate_step and step.condition:
        negated = negate_predicate(step.condition.copy())
        self._store_expression(negated, spec)
```

- [ ] **Step 2: Write `_derive_project`**

```python
def _derive_project(self, step: Project, spec: BranchSpec) -> None:
    """Add IS NOT NULL for projected columns, handle DISTINCT."""
    projected = self._projected_columns(step)
    for col_name, table_alias in projected:
        alias_norm = normalize_name(table_alias)
        for relation_id, tc in spec.requirements.items():
            # Match by table name or alias
            if tc.table != alias_norm and normalize_name(tc.alias or "") != alias_norm:
                continue
            if not _has_is_not_null(tc.constraints, col_name):
                col_node = self._solver_col(tc.table, col_name)
                tc.constraints.append(_make_is_not_null(col_node))
            break
    # DISTINCT handling
    for relation_id, tc in spec.requirements.items():
        dup_ids = []
        for col_name, table_alias in projected:
            alias_norm = normalize_name(table_alias)
            if tc.table == alias_norm or normalize_name(tc.alias or "") == alias_norm:
                dup_ids.append(physical_column(col_name, relation_id))
        if step.distinct and dup_ids:
            tc.duplicate_columns = dup_ids
            tc.min_rows = max(tc.min_rows, 2)
```

- [ ] **Step 3: Write `_derive_limit`, `_derive_sort`, `_derive_set_op`**

```python
def _derive_limit(self, step: Limit, spec: BranchSpec) -> None:
    """Set min_rows on the driving table."""
    offset = getattr(step, "offset", 0) or 0
    limit_val = step.limit if step.limit != float("inf") else 1
    if self._is_gold_mode:
        needed = offset + 1 if int(limit_val) > 0 else 0
    else:
        needed = offset + int(limit_val)
    driving_alias = normalize_name(getattr(step, "source", None) or "")
    if driving_alias:
        # Find the matching relation in spec.requirements by table name
        for relation, tc in spec.requirements.items():
            if tc.table == driving_alias:
                tc.min_rows = max(tc.min_rows, needed)
                break
        else:
            # Create a new requirement if not found
            from parseval.identity import relation_id, identifier_name, RelationKind
            driving_relation = relation_id(RelationKind.TABLE, identifier_name(driving_alias))
            spec.requirements[driving_relation] = TableConstraint(
                relation=driving_relation, min_rows=needed,
            )

def _derive_sort(self, step: Sort, spec: BranchSpec) -> None:
    """No-op for Sort steps."""
    pass

def _derive_set_op(self, step: SetOperation, spec: BranchSpec) -> None:
    """No-op for SetOperation steps."""
    pass
```

- [ ] **Step 4: Write `_derive_subplan`** (move existing `_propagate_subplan` logic)

Rename `_propagate_subplan` to `_derive_subplan` and update its internal calls to use `self._walk_step` instead of `self._propagate_step`.

- [ ] **Step 5: Run test suite**

Run: `python -m pytest tests/symbolic/test_propagator.py -x -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): extract remaining step handlers"
```

---

### Task 16: Replace _propagate_step with dispatch

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

Replace all calls to `self._propagate_step(step, spec, negate_step, negate_conjunct)` with `self._walk_step(step, spec)`. Update `propagate()` to set `self._negate_step` and `self._negate_conjunct` before calling `_walk_plan`.

- [ ] **Step 1: Add `_walk_plan` convenience method**

```python
def _walk_plan(self, spec: BranchSpec) -> None:
    """Walk the full plan from root."""
    self._walk_step(self.plan.root, spec)
```

- [ ] **Step 2: Update `propagate()` to use `_walk_plan`**

Replace all `self._propagate_step(self.plan.root, spec, ...)` calls with:
```python
self._negate_step = step  # or None
self._negate_conjunct = idx  # or 0
self._walk_plan(spec)
```

- [ ] **Step 3: Delete `_propagate_step`**

The old 300-line method is now fully replaced by the dispatch table + individual handlers.

- [ ] **Step 4: Run test suite**

Run: `python -m pytest tests/symbolic/test_propagator.py -x -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): replace _propagate_step with dispatch"
```

---

### Task 17: Extract branch-type methods from propagate()

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

The `propagate()` method is still long with inline branch-type logic. Extract each into a focused method.

- [ ] **Step 1: Extract `_positive_spec`**

```python
def _positive_spec(self) -> BranchSpec | None:
    """Build the positive branch spec."""
    if self.config.positive <= 0:
        return None
    try:
        spec = BranchSpec(branch="positive")
        self._walk_plan(spec)
        if self.config.boundary > 0:
            self._collect_boundary_values(spec)
        return spec
    except Exception as exc:
        logger.debug("positive spec propagation failed: %s", exc)
        return None
```

- [ ] **Step 2: Extract `_negative_specs`**

```python
def _negative_specs(self) -> list[BranchSpec]:
    """Build negative branch specs (one per filter conjunct)."""
    if self.config.negative <= 0:
        return []
    specs = []
    for step in self.plan.ordered_steps:
        try:
            if isinstance(step, Filter) and step.condition:
                conjuncts = self._split_conjuncts(step.condition)
                for idx in range(len(conjuncts)):
                    spec = BranchSpec(branch=f"negative_c{idx}")
                    self._negate_step = step
                    self._negate_conjunct = idx
                    self._walk_plan(spec)
                    specs.append(spec)
        except Exception as exc:
            logger.debug("negative spec propagation failed for step %s: %s", type(step).__name__, exc)
    self._negate_step = None
    self._negate_conjunct = 0
    return specs
```

- [ ] **Step 3: Extract `_unmatched_join_specs`, `_having_fail_specs`, `_null_specs`, `_case_else_specs`**

Same pattern for each branch type.

- [ ] **Step 4: Simplify `propagate()`**

```python
def propagate(self) -> list[BranchSpec]:
    specs = []
    pos = self._positive_spec()
    if pos:
        specs.append(pos)
    specs.extend(self._negative_specs())
    specs.extend(self._unmatched_join_specs())
    specs.extend(self._having_fail_specs())
    specs.extend(self._null_specs(pos))
    specs.extend(self._case_else_specs())
    for spec in specs:
        self._add_schema_constraints(spec)
        self._annotate_column_types(spec)
    return specs
```

- [ ] **Step 5: Run test suite**

Run: `python -m pytest tests/symbolic/test_propagator.py -x -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): extract branch-type methods from propagate()"
```

---

### Task 18: Final verification and cleanup

**Files:**
- Modify: `src/parseval/symbolic/speculate.py`

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -x -q --ignore=tests/solver/test_domain.py --ignore=tests/test_solver.py`

Expected: All tests pass

- [ ] **Step 2: Verify line count reduction**

Run: `wc -l src/parseval/symbolic/speculate.py`

Expected: ~1800-2000 lines (down from ~3500)

- [ ] **Step 3: Verify no remaining string fallbacks**

Run: `grep -n "self._rel(" src/parseval/symbolic/speculate.py`

Expected: No matches (all `_rel` calls removed)

- [ ] **Step 4: Verify no remaining `_lookup_col_type` calls**

Run: `grep -n "_lookup_col_type" src/parseval/symbolic/speculate.py`

Expected: No matches

- [ ] **Step 5: Commit final state**

```bash
git add src/parseval/symbolic/speculate.py
git commit -m "refactor(speculate): final cleanup — verify line count and no remaining fallbacks"
```
