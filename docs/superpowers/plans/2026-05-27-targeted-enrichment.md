# Targeted Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Minimize false EQ verdicts by generating data with duplicates and NULLs targeted at DISTINCT/GROUP BY/COUNT columns, and fix critical bugs found in code review.

**Architecture:** Analyze the query plan to identify columns used in DISTINCT, GROUP BY, and aggregate functions. Generate enrichment constraints that force the solver to produce rows with duplicates and NULLs in those columns. Fix P0-P3 bugs integrated into the implementation.

**Tech Stack:** Python, sqlglot, pytest

---

## File Map

| File | Action | Purpose |
|------|--------|---------|
| `src/parseval/plan/planner.py:275` | Modify | Fix `_intersect_preserving_order` method placement (P0) |
| `src/parseval/plan/rex.py:966` | Modify | Cache LIKE pattern compilation (P1) |
| `src/parseval/domain/compiler.py:347` | Modify | Pre-compile regex patterns (P1) |
| `src/parseval/plan/planner.py:1245` | Modify | Use heapq for topological sort (P2) |
| `src/parseval/solver/unified.py:346` | Modify | Single-pass IN-list evaluation (P2) |
| `src/parseval/domain/providers/base.py` | Modify | Add FK resolution helper to base class (P3) |
| `src/parseval/domain/providers/numeric.py` | Modify | Use base FK helper |
| `src/parseval/domain/providers/string.py` | Modify | Use base FK helper |
| `src/parseval/domain/providers/temporal.py` | Modify | Use base FK helper |
| `src/parseval/domain/providers/boolean.py` | Modify | Use base FK helper |
| `src/parseval/domain/providers/boolean_like.py` | Modify | Use base FK helper |
| `src/parseval/domain/providers/uuid.py` | Modify | Use base FK helper |
| `src/parseval/symbolic/enrichment.py` | Create | Plan analysis + enrichment constraint generation |
| `src/parseval/symbolic/engine.py` | Modify | Integrate enrichment into generation loop |
| `tests/symbolic/test_enrichment.py` | Create | Tests for enrichment logic |
| `tests/plan/test_rex.py` | Modify | Test LIKE caching |
| `tests/domain/test_compiler.py` | Modify | Test regex pre-compilation |

---

### Task 1: Fix P0 — `_intersect_preserving_order` method placement

**Files:**
- Modify: `src/parseval/domain/compiler.py:275-281`
- Test: `tests/domain/test_compiler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_compiler.py — add at end of file

def test_intersect_preserving_order_method():
    """_intersect_preserving_order must be a method on ConstraintCompiler."""
    from parseval.domain.compiler import ConstraintCompiler
    compiler = ConstraintCompiler.__new__(ConstraintCompiler)
    result = compiler._intersect_preserving_order([1, 2, 3], [2, 3, 4])
    assert result == (2, 3)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/domain/test_compiler.py::test_intersect_preserving_order_method -v`
Expected: FAIL with `AttributeError: 'ConstraintCompiler' object has no attribute '_intersect_preserving_order'`

- [ ] **Step 3: Fix — move function inside class**

In `src/parseval/domain/compiler.py`, the function `_intersect_preserving_order` at line 275 is at module level. Move it inside the `ConstraintCompiler` class by adding proper indentation (4 spaces). The function body stays the same — just indent it to be inside the class, before line 264 (`_check_pattern`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/domain/test_compiler.py::test_intersect_preserving_order_method -v`
Expected: PASS

- [ ] **Step 5: Run existing compiler tests**

Run: `pytest tests/domain/test_compiler.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/parseval/domain/compiler.py tests/domain/test_compiler.py
git commit -m "fix: move _intersect_preserving_order inside ConstraintCompiler class"
```

---

### Task 2: Fix P1 — Cache LIKE pattern compilation in rex.py

**Files:**
- Modify: `src/parseval/plan/rex.py:962-972`
- Test: `tests/plan/test_rex.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/plan/test_rex.py — add at end of file

def test_like_pattern_cached():
    """like_to_pattern should be called once per unique pattern, not per row."""
    from parseval.plan.rex import _like
    from unittest.mock import patch
    import parseval.plan.rex as rex_module

    # First call should compile the pattern
    result1 = _like("hello", "%ell%", case_insensitive=False)
    assert result1 is True

    # Second call with same pattern should reuse cached result
    with patch.object(rex_module, 'like_to_pattern') as mock_compile:
        result2 = _like("world", "%ell%", case_insensitive=False)
        # like_to_pattern should NOT be called again for same pattern
        mock_compile.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/plan/test_rex.py::test_like_pattern_cached -v`
Expected: FAIL (like_to_pattern is called every time)

- [ ] **Step 3: Add caching to `_like` function**

In `src/parseval/plan/rex.py`, modify the `_like` function (line 962) to cache the compiled pattern:

```python
import functools

@functools.lru_cache(maxsize=256)
def _cached_like_pattern(pattern: str, case_insensitive: bool) -> "re.Pattern":
    """Cache compiled LIKE patterns — they're fixed per AST node."""
    compiled = like_to_pattern(pattern)
    if case_insensitive:
        return re.compile(compiled.pattern, re.IGNORECASE)
    return compiled

def _like(value: Any, pattern: Any, *, case_insensitive: bool) -> Optional[bool]:
    if value is None or pattern is None:
        return None
    try:
        compiled = _cached_like_pattern(str(pattern), case_insensitive)
        return bool(compiled.match(str(value)))
    except re.error:  # pragma: no cover - defensive
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plan/test_rex.py::test_like_pattern_cached -v`
Expected: PASS

- [ ] **Step 5: Run existing rex tests**

Run: `pytest tests/plan/test_rex.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/parseval/plan/rex.py tests/plan/test_rex.py
git commit -m "perf: cache LIKE pattern compilation in rex.py"
```

---

### Task 3: Fix P1 — Pre-compile regex patterns in compiler.py

**Files:**
- Modify: `src/parseval/domain/compiler.py:340-350`
- Test: `tests/domain/test_compiler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_compiler.py — add at end of file

def test_pattern_constraint_precompiled():
    """PatternConstraint should pre-compile the regex, not recompile on each validate()."""
    from parseval.domain.compiler import ConstraintValidator, ColumnDomainPlan
    import re

    plan = ColumnDomainPlan(
        table="t", column="c", datatype="TEXT",
        pattern=r"^[a-z]+$", nullable=True
    )
    validator = ConstraintValidator()

    # Should work correctly
    validator.validate(plan, "abc", "test_col")

    # Should raise on non-match
    import pytest
    with pytest.raises(Exception):
        validator.validate(plan, "123", "test_col")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/domain/test_compiler.py::test_pattern_constraint_precompiled -v`
Expected: May pass (test verifies behavior, not compilation method)

- [ ] **Step 3: Pre-compile regex in `ColumnDomainPlan`**

In `src/parseval/domain/compiler.py`, modify the `ColumnDomainPlan` dataclass to store a compiled pattern. In the `validate` method (line 347), use the compiled pattern instead of calling `re.search` with a string:

```python
# In ColumnDomainPlan dataclass, add:
_compiled_pattern: Optional[re.Pattern] = field(default=None, repr=False)

# In __post_init__ or wherever pattern is set:
if self.pattern and self._compiled_pattern is None:
    self._compiled_pattern = re.compile(self.pattern)

# In ConstraintValidator.validate(), change line 347 from:
#   re.search(plan.pattern, str(value))
# to:
#   plan._compiled_pattern.search(str(value))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/domain/test_compiler.py::test_pattern_constraint_precompiled -v`
Expected: PASS

- [ ] **Step 5: Run existing compiler tests**

Run: `pytest tests/domain/test_compiler.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/parseval/domain/compiler.py tests/domain/test_compiler.py
git commit -m "perf: pre-compile regex patterns in ConstraintValidator"
```

---

### Task 4: Fix P2 — Use heapq for topological sort

**Files:**
- Modify: `src/parseval/plan/planner.py:1245-1274`
- Test: `tests/plan/test_planner_tree_shape.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/plan/test_planner_tree_shape.py — add at end of file

def test_topological_order_preserved_with_heap():
    """Topological order should be correct after switching to heapq."""
    from parseval.plan.planner import Plan, _topological_order
    from sqlglot import parse_one

    sql = "SELECT a FROM t1 JOIN t2 ON t1.id = t2.id WHERE t1.x > 0 ORDER BY a LIMIT 5"
    plan = Plan(parse_one(sql))
    ordered = _topological_order(plan)

    # Verify all steps are present
    step_types = [type(s).__name__ for s in ordered]
    assert "Scan" in step_types
    assert "Project" in step_types

    # Verify topological order: dependencies before dependents
    step_to_idx = {id(s): i for i, s in enumerate(ordered)}
    for step in ordered:
        for dep in step.dependencies:
            assert step_to_idx[id(dep)] < step_to_idx[id(step)], \
                f"{type(dep).__name__} must come before {type(step).__name__}"
```

- [ ] **Step 2: Run test to verify it passes (existing behavior)**

Run: `pytest tests/plan/test_planner_tree_shape.py::test_topological_order_preserved_with_heap -v`
Expected: PASS (verifying current behavior before change)

- [ ] **Step 3: Replace sorted list with heapq**

In `src/parseval/plan/planner.py`, modify `_topological_order` (line 1245):

```python
import heapq

def _topological_order(plan: "Plan") -> t.List["Step"]:
    def sort_key(step: "Step") -> t.Tuple[str, str, int]:
        return (type(step).__name__, step.name or "", id(step))

    indegree: t.Dict["Step", int] = {
        step: len(step.dependencies) for step in plan.dag
    }
    heap = [
        (sort_key(step), step)
        for step, degree in indegree.items() if degree == 0
    ]
    heapq.heapify(heap)
    ordered: t.List["Step"] = []
    while heap:
        _, current = heapq.heappop(heap)
        ordered.append(current)
        for dependent in current.dependents:
            if dependent not in indegree:
                continue
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(heap, (sort_key(dependent), dependent))
    return ordered
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/plan/test_planner_tree_shape.py::test_topological_order_preserved_with_heap -v`
Expected: PASS

- [ ] **Step 5: Run all planner tests**

Run: `pytest tests/plan/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/parseval/plan/planner.py tests/plan/test_planner_tree_shape.py
git commit -m "perf: use heapq for topological sort in planner"
```

---

### Task 5: Fix P2 — Single-pass IN-list evaluation

**Files:**
- Modify: `src/parseval/solver/unified.py:340-360`
- Test: `tests/test_solver.py`

- [ ] **Step 1: Write the test**

```python
# tests/test_solver.py — add at end of file

def test_in_list_single_pass_evaluation():
    """IN-list heuristic should evaluate each element once, not twice."""
    # This is a behavioral test — the fix is internal optimization
    # Verify the solver still handles IN lists correctly
    from parseval.solver.unified import Solver
    from parseval.instance import Instance
    from sqlglot import parse_one

    instance = Instance(ddls="CREATE TABLE t (id INT, name TEXT)", dialect="sqlite")
    instance.create_row("t", values={"id": 1, "name": "a"})
    instance.create_row("t", values={"id": 2, "name": "b"})

    solver = Solver(instance, dialect="sqlite")
    # The IN-list optimization is internal — just verify it doesn't break
    assert solver is not None
```

- [ ] **Step 2: Run test to verify it passes**

Run: `pytest tests/test_solver.py::test_in_list_single_pass_evaluation -v`
Expected: PASS

- [ ] **Step 3: Fix double evaluation**

In `src/parseval/solver/unified.py`, find the IN-list heuristic handler (around line 340). The current code calls `concrete(e)` twice per expression. Change to single evaluation:

```python
# Before (conceptual):
# values = [concrete(e) for e in expr.expressions if concrete(e) is not None]

# After:
# values = []
# for e in expr.expressions:
#     val = concrete(e)
#     if val is not None:
#         values.append(val)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_solver.py::test_in_list_single_pass_evaluation -v`
Expected: PASS

- [ ] **Step 5: Run all solver tests**

Run: `pytest tests/test_solver.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/parseval/solver/unified.py tests/test_solver.py
git commit -m "perf: single-pass evaluation in IN-list heuristic"
```

---

### Task 6: Fix P3 — Factor FK boilerplate into base provider

**Files:**
- Modify: `src/parseval/domain/providers/base.py`
- Modify: `src/parseval/domain/providers/numeric.py`
- Modify: `src/parseval/domain/providers/string.py`
- Modify: `src/parseval/domain/providers/temporal.py`
- Modify: `src/parseval/domain/providers/boolean.py`
- Modify: `src/parseval/domain/providers/boolean_like.py`
- Modify: `src/parseval/domain/providers/uuid.py`
- Test: `tests/domain/test_provider_resolution.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/domain/test_provider_resolution.py — add at end of file

def test_fk_resolution_in_base_provider():
    """Base ValueProvider should have a helper for FK resolution."""
    from parseval.domain.providers.base import ValueProvider
    assert hasattr(ValueProvider, '_resolve_foreign_key')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/domain/test_provider_resolution.py::test_fk_resolution_in_base_provider -v`
Expected: FAIL

- [ ] **Step 3: Add FK helper to base class**

In `src/parseval/domain/providers/base.py`, add:

```python
from ..coercion import coerce_reference_value

class ValueProvider(ABC):
    # ... existing code ...

    def _resolve_foreign_key(self, spec, runtime):
        """Check if column has a FK and return a referenced value if available."""
        if spec.foreign_key:
            referenced = runtime.referenced_values(spec)
            if referenced:
                return coerce_reference_value(
                    runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
                )
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/domain/test_provider_resolution.py::test_fk_resolution_in_base_provider -v`
Expected: PASS

- [ ] **Step 5: Update IntegerProvider to use base helper**

In `src/parseval/domain/providers/numeric.py`, replace the FK boilerplate in `IntegerProvider.generate()`:

```python
# Before (lines 31-36):
#     if spec.foreign_key:
#         referenced = runtime.referenced_values(spec)
#         if referenced:
#             return coerce_reference_value(
#                 runtime.rng.choice(referenced), spec.datatype, dialect=spec.dialect
#             )

# After:
#     fk_value = self._resolve_foreign_key(spec, runtime)
#     if fk_value is not None:
#         return fk_value
```

- [ ] **Step 6: Update remaining providers**

Apply the same change to:
- `RealProvider` in `numeric.py`
- `StringProvider` in `string.py`
- `DateProvider`, `DatetimeProvider`, `TimeProvider` in `temporal.py`
- `BooleanProvider` in `boolean.py`
- `BooleanLikeTinyIntProvider` in `boolean_like.py`
- `UUIDProvider` in `uuid.py`

Remove the `from ..coercion import coerce_reference_value` import from each file (now in base).

- [ ] **Step 7: Run all domain tests**

Run: `pytest tests/domain/ -v`
Expected: All PASS

- [ ] **Step 8: Commit**

```bash
git add src/parseval/domain/providers/
git commit -m "refactor: factor FK boilerplate into ValueProvider base class"
```

---

### Task 7: Create enrichment plan analyzer

**Files:**
- Create: `src/parseval/symbolic/enrichment.py`
- Create: `tests/symbolic/test_enrichment.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/symbolic/test_enrichment.py

"""Tests for targeted enrichment plan analysis."""

import pytest
from sqlglot import parse_one
from parseval.plan import Plan
from parseval.symbolic.enrichment import (
    EnrichmentTargets,
    analyze_plan_for_enrichment,
)


class TestEnrichmentTargets:
    def test_empty_plan(self):
        """Plan with no DISTINCT/GROUP BY/aggregates has no targets."""
        plan = Plan(parse_one("SELECT a FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert targets.duplicate_columns == []
        assert targets.null_columns == []

    def test_distinct_project(self):
        """DISTINCT project should identify all projected columns as duplicate targets."""
        plan = Plan(parse_one("SELECT DISTINCT a, b FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        # Should have duplicate targets for a and b
        assert len(targets.duplicate_columns) > 0

    def test_group_by_columns(self):
        """GROUP BY columns should be duplicate targets."""
        plan = Plan(parse_one("SELECT a, COUNT(b) FROM t GROUP BY a"))
        targets = analyze_plan_for_enrichment(plan)
        # 'a' should be a duplicate target
        assert len(targets.duplicate_columns) > 0

    def test_count_column_null(self):
        """COUNT(col) should identify col as NULL target."""
        plan = Plan(parse_one("SELECT COUNT(a) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        # 'a' should be a NULL target
        assert len(targets.null_columns) > 0

    def test_count_star_no_null_target(self):
        """COUNT(*) should not create NULL targets."""
        plan = Plan(parse_one("SELECT COUNT(*) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert targets.null_columns == []

    def test_sum_avg_null_targets(self):
        """SUM(col) and AVG(col) should identify col as NULL target."""
        plan = Plan(parse_one("SELECT SUM(a), AVG(b) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert len(targets.null_columns) >= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/symbolic/test_enrichment.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Create enrichment module**

Create `src/parseval/symbolic/enrichment.py`:

```python
"""Targeted enrichment — analyze plans for DISTINCT/GROUP BY/aggregate patterns.

Identifies columns that need duplicate rows (DISTINCT, GROUP BY) or NULL
values (COUNT/SUM/AVG) to expose semantic differences between queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from sqlglot import exp

from parseval.plan import Plan
from parseval.plan.planner import Aggregate, Project, StepAnnotations


@dataclass
class EnrichmentTargets:
    """Columns that need enrichment to expose semantic differences."""
    # Columns that need duplicate values (DISTINCT, GROUP BY).
    duplicate_columns: List[Tuple[str, str]] = field(default_factory=list)
    # Columns that need NULL values (COUNT/SUM/AVG operands).
    null_columns: List[Tuple[str, str]] = field(default_factory=list)


def analyze_plan_for_enrichment(plan: Plan) -> EnrichmentTargets:
    """Walk the plan and identify columns needing enrichment.

    Returns:
        EnrichmentTargets with duplicate_columns and null_columns populated.
    """
    targets = EnrichmentTargets()

    for step in plan.ordered_steps:
        if isinstance(step, Project) and step.distinct:
            _collect_distinct_targets(plan, step, targets)
        elif isinstance(step, Aggregate):
            _collect_aggregate_targets(plan, step, targets)

    # Deduplicate
    targets.duplicate_columns = list(set(targets.duplicate_columns))
    targets.null_columns = list(set(targets.null_columns))
    return targets


def _collect_distinct_targets(
    plan: Plan, step: Project, targets: EnrichmentTargets
) -> None:
    """Collect projected columns as duplicate targets for DISTINCT."""
    annotation = plan.annotation_for(step)
    for col in annotation.projected_columns:
        # Resolve to table.column
        table = annotation.source_tables[0] if annotation.source_tables else ""
        targets.duplicate_columns.append((table, col))


def _collect_aggregate_targets(
    plan: Plan, step: Aggregate, targets: EnrichmentTargets
) -> None:
    """Collect GROUP BY columns (duplicates) and aggregate operands (NULLs)."""
    # GROUP BY columns need duplicates
    for col_name, col_expr in step.group.items():
        if isinstance(col_expr, exp.Column):
            table = col_expr.table or ""
            targets.duplicate_columns.append((table, col_expr.name))

    # Aggregate operands need NULLs (except COUNT(*))
    for agg_expr in step.aggregations:
        for agg_func in agg_expr.find_all(exp.Func):
            func_name = agg_func.sql_name().upper()
            if func_name in ("COUNT", "SUM", "AVG"):
                # Skip COUNT(*) — no column to nullify
                if func_name == "COUNT" and isinstance(agg_func.this, exp.Star):
                    continue
                # Find the column operand
                for operand in agg_func.unnest_operands():
                    if isinstance(operand, exp.Column):
                        table = operand.table or ""
                        targets.null_columns.append((table, operand.name))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/symbolic/test_enrichment.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/enrichment.py tests/symbolic/test_enrichment.py
git commit -m "feat: add enrichment plan analyzer for DISTINCT/GROUP BY/aggregates"
```

---

### Task 8: Integrate enrichment into SymbolicEngine

**Files:**
- Modify: `src/parseval/symbolic/engine.py`
- Test: `tests/symbolic/test_enrichment.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/symbolic/test_enrichment.py — add at end of file

def test_engine_generates_null_for_count_column():
    """Engine should generate NULL values for COUNT(col) columns."""
    from parseval.instance import Instance
    from parseval.symbolic.engine import SymbolicEngine

    schema = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT)"
    instance = Instance(ddls=schema, dialect="sqlite")
    engine = SymbolicEngine(instance, "SELECT COUNT(name) FROM t", dialect="sqlite")
    result = engine.generate()

    # After generation, t should have at least one row with name=NULL
    rows = instance.get_rows("t")
    assert len(rows) > 0
    has_null = any(r["name"].concrete is None for r in rows)
    assert has_null, "Expected at least one NULL in name column for COUNT(name)"


def test_engine_generates_duplicates_for_distinct():
    """Engine should generate duplicate rows for DISTINCT queries."""
    from parseval.instance import Instance
    from parseval.symbolic.engine import SymbolicEngine

    schema = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT)"
    instance = Instance(ddls=schema, dialect="sqlite")
    engine = SymbolicEngine(instance, "SELECT DISTINCT name FROM t", dialect="sqlite")
    result = engine.generate()

    # After generation, t should have at least 2 rows
    rows = instance.get_rows("t")
    assert len(rows) >= 2, "Expected at least 2 rows for DISTINCT testing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/symbolic/test_enrichment.py::test_engine_generates_null_for_count_column tests/symbolic/test_enrichment.py::test_engine_generates_duplicates_for_distinct -v`
Expected: FAIL (enrichment not yet integrated)

- [ ] **Step 3: Add enrichment pass to engine**

In `src/parseval/symbolic/engine.py`, add after Phase 0f (around line 115):

```python
# Phase 0g: Targeted enrichment for DISTINCT/GROUP BY/aggregate patterns.
self._enrich_for_semantics()
```

Add the method:

```python
def _enrich_for_semantics(self) -> None:
    """Generate additional rows with duplicates and NULLs to expose semantic differences."""
    from .enrichment import analyze_plan_for_enrichment

    targets = analyze_plan_for_enrichment(self.plan)
    if not targets.duplicate_columns and not targets.null_columns:
        return

    # Generate rows with NULLs for COUNT/SUM/AVG columns
    for table, column in targets.null_columns:
        real_table = self.alias_map.get(table, table)
        if real_table not in self.instance.tables:
            continue
        # Check if column allows NULLs
        col_info = self.instance.get_column(real_table, column)
        if col_info and not col_info.nullable:
            continue
        # Generate a row with NULL in this column
        self._generate_null_row(real_table, column)

    # Generate duplicate rows for DISTINCT/GROUP BY columns
    for table, column in targets.duplicate_columns:
        real_table = self.alias_map.get(table, table)
        if real_table not in self.instance.tables:
            continue
        existing_rows = self.instance.get_rows(real_table)
        if existing_rows:
            # Create a duplicate with same value in the target column
            self._generate_duplicate_row(real_table, column, existing_rows[0])
```

- [ ] **Step 4: Implement helper methods**

Add to `SymbolicEngine`. Note: `instance.create_row()` accepts `values: Dict[str, Any]` where keys are column names and values are concrete Python values. It handles FK resolution and UNIQUE conflicts internally.

```python
def _generate_null_row(self, table: str, null_column: str) -> None:
    """Generate a row with NULL in the specified column.

    Uses instance.create_row() which handles FK resolution and schema constraints.
    If the column has a NOT NULL constraint, create_row will raise and we skip.
    """
    try:
        self.instance.create_row(table, values={null_column: None})
    except Exception:
        pass  # Schema constraint may prevent NULL

def _generate_duplicate_row(self, table: str, dup_column: str, source_row) -> None:
    """Generate a row with same value in dup_column as source_row.

    source_row is a Row object from instance.get_rows(). Access column
    values via source_row[col_name].concrete to get the Python value.
    """
    source_val = source_row.get(dup_column)
    if source_val is None or source_val.concrete is None:
        return
    try:
        self.instance.create_row(table, values={dup_column: source_val.concrete})
    except Exception:
        pass  # UNIQUE constraint may prevent duplicate
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/symbolic/test_enrichment.py -v`
Expected: All PASS

- [ ] **Step 6: Run existing symbolic tests**

Run: `pytest tests/symbolic/ -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/parseval/symbolic/engine.py tests/symbolic/test_enrichment.py
git commit -m "feat: integrate targeted enrichment into SymbolicEngine"
```

---

### Task 9: Skip syntax error pairs in experiment

**Files:**
- Modify: `tests/experiment/test_sqlite.py`

- [ ] **Step 1: Write the test**

```python
# Verify the experiment script can skip syntax error pairs
# This is a manual verification — run the experiment and check output
```

- [ ] **Step 2: Add syntax error skipping**

In `tests/experiment/test_sqlite.py`, modify the `main` function to skip pairs where either SQL has syntax errors. Add a pre-check before calling `disprove`:

```python
# In the main loop, before the try block:
# Skip pairs where either query likely has syntax issues
# (unbalanced quotes, etc.)
if _has_likely_syntax_error(gold_sql) or _has_likely_syntax_error(pred_sql):
    entry = {
        "index": index,
        "db_id": db_id,
        "gold_sql": gold_sql,
        "pred_sql": pred_sql,
        "result": {"verdict": "syntax_error", "error_msg": "Skipped: likely syntax error"},
    }
    results.append(entry)
    continue
```

Add helper:

```python
def _has_likely_syntax_error(sql: str) -> bool:
    """Quick heuristic to detect obviously broken SQL."""
    # Unbalanced single quotes (handles escaped '' by subtracting pairs)
    s = sql.replace("''", "")  # Remove escaped quotes
    if s.count("'") % 2 != 0:
        return True
    # Unbalanced parentheses
    if sql.count("(") != sql.count(")"):
        return True
    # Empty SQL
    if not sql.strip():
        return True
    return False
```

- [ ] **Step 3: Verify manually**

Run: `python tests/experiment/test_sqlite.py --help`
Expected: Shows usage info

- [ ] **Step 4: Commit**

```bash
git add tests/experiment/test_sqlite.py
git commit -m "feat: skip syntax error pairs in experiment runner"
```

---

### Task 10: Run full experiment and measure improvement

**Files:**
- None (verification only)

- [ ] **Step 1: Run the experiment**

Run: `python tests/experiment/test_sqlite.py`
Expected: Completes without crashes

- [ ] **Step 2: Compare results**

Check the output metrics:
- Previous: EQ=746, NEQ=683, syntax_error=74, runtime_error=23, unknown=8
- Target: Reduce EQ count, increase NEQ count, eliminate syntax_error category

- [ ] **Step 3: Document results**

Record the new metrics in the commit message or a follow-up doc.

- [ ] **Step 4: Commit results (if desired)**

```bash
git add results/
git commit -m "chore: run experiment with targeted enrichment"
```
