# create_rows Domain Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Instance.create_rows` / `create_row` produce one full constraint-valid row per listed batch entry via `DomainGenerator`, with a hard Instance/Domain boundary.

**Architecture:** Instance only orchestrates (normalize presets → ensure parent **rows** → call `complete_row` → `place_row`). Domain only invents cell values (FK bind, fill, unique freshen, CHECK) from plain dicts + `InstanceSchema`. Empty `{}` / `[]` payloads mean one fully generated row for that listed table.

**Tech Stack:** Python 3, sqlglot, unittest/`pytest`, existing `parseval.instance` + `parseval.domain`.

**Spec:** `docs/superpowers/specs/2026-07-10-create-rows-domain-boundary-design.md`

---

## File map

| File | Responsibility |
|------|----------------|
| `src/parseval/instance/core.py` | `create_rows` / `create_row` / `_normalize_batch` / `_ensure_fk_parents` / `_materialize_row` — orchestration + docs; no value invention |
| `src/parseval/domain/generator.py` | `complete_row` — value invention + constraint satisfaction (already present; only touch if a test exposes a gap) |
| `tests/instance/test_create_rows.py` | New: empty/partial `create_rows`, FK match, composite unique, boundary guards |
| `tests/instance/test_instance_stable.py` | Keep green; no need to duplicate new cases |

---

### Task 1: Failing tests for empty / partial `create_rows`

**Files:**
- Create: `tests/instance/test_create_rows.py`

- [ ] **Step 1: Write the failing (or not-yet-covered) tests**

```python
"""create_rows: domain-backed full rows, Instance/Domain boundary."""

from __future__ import annotations

import unittest

from parseval.instance import Instance
from parseval.instance.core import Instance as InstanceCore


DDL = """
CREATE TABLE parent (
    id INT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE child (
    id INT PRIMARY KEY,
    parent_id INT NOT NULL,
    label TEXT,
    FOREIGN KEY (parent_id) REFERENCES parent(id)
);
CREATE TABLE item (
    a_id INT NOT NULL,
    b_id INT NOT NULL,
    seq INT NOT NULL,
    PRIMARY KEY (a_id, b_id, seq)
);
"""


class TestCreateRowsEmptyPayload(unittest.TestCase):
    def test_empty_mapping_payload_generates_one_full_row(self):
        inst = Instance(ddls=DDL, name="empty_map", dialect="sqlite")
        results = inst.create_rows({"parent": {}})
        self.assertEqual(list(results), ["parent"])
        self.assertEqual(len(results["parent"]), 1)
        row = inst.get_rows("parent")[0]
        self.assertIsNotNone(row["id"].concrete)
        self.assertIsNotNone(row["name"].concrete)
        self.assertEqual(len(inst.get_rows("child")), 0)

    def test_empty_sequence_payload_generates_one_full_row(self):
        inst = Instance(ddls=DDL, name="empty_seq", dialect="sqlite")
        results = inst.create_rows({"parent": []})
        self.assertEqual(len(results["parent"]), 1)
        self.assertEqual(len(inst.get_rows("parent")), 1)

    def test_omitted_concretes_creates_nothing(self):
        inst = Instance(ddls=DDL, name="none", dialect="sqlite")
        self.assertEqual(inst.create_rows(), {})
        self.assertEqual(inst.create_rows({}), {})
        self.assertEqual(len(inst.get_rows("parent")), 0)


class TestCreateRowsConstraints(unittest.TestCase):
    def test_child_only_creates_parent_and_binds_fk(self):
        inst = Instance(ddls=DDL, name="fk", dialect="sqlite")
        inst.create_rows({"child": {}})
        self.assertEqual(len(inst.get_rows("parent")), 1)
        self.assertEqual(len(inst.get_rows("child")), 1)
        parent_id = inst.get_rows("parent")[0]["id"].concrete
        child = inst.get_rows("child")[0]
        self.assertEqual(child["parent_id"].concrete, parent_id)
        self.assertIsNotNone(child["id"].concrete)

    def test_two_empty_parents_have_distinct_pks(self):
        inst = Instance(ddls=DDL, name="uniq", dialect="sqlite")
        inst.create_rows({"parent": {}})
        inst.create_rows({"parent": {}})
        ids = [r["id"].concrete for r in inst.get_rows("parent")]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_partial_presets_fill_missing_columns(self):
        inst = Instance(ddls=DDL, name="partial", dialect="sqlite")
        inst.create_rows({"parent": [{"id": 7}]})
        row = inst.get_rows("parent")[0]
        self.assertEqual(row["id"].concrete, 7)
        self.assertIsNotNone(row["name"].concrete)

    def test_composite_pk_freshen_across_batch(self):
        inst = Instance(ddls=DDL, name="comp", dialect="sqlite")
        inst.create_rows(
            {
                "item": [
                    {"a_id": 1, "b_id": 1, "seq": 1},
                    {"a_id": 1, "b_id": 1},
                ]
            }
        )
        rows = inst.get_rows("item")
        self.assertEqual(len(rows), 2)
        keys = {
            (r["a_id"].concrete, r["b_id"].concrete, r["seq"].concrete)
            for r in rows
        }
        self.assertEqual(len(keys), 2)
        self.assertEqual(rows[0]["seq"].concrete, 1)
        self.assertNotEqual(rows[1]["seq"].concrete, 1)


class TestInstanceDomainBoundary(unittest.TestCase):
    def test_instance_has_no_value_invention_helpers(self):
        forbidden = {
            "_default_for_type",
            "_next_default_value",
            "_freshen_uniques",
        }
        methods = {name for name in dir(InstanceCore) if not name.startswith("__")}
        self.assertTrue(forbidden.isdisjoint(methods), methods & forbidden)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to see current gaps**

Run: `uv run pytest tests/instance/test_create_rows.py -v --tb=short`

Expected: most may already PASS (wiring exists); any FAIL shows the gap to fix in Task 2–3. If all PASS, still proceed to docstring/boundary polish in Task 3, then Task 4 regression.

- [ ] **Step 3: Commit the new tests**

```bash
git add tests/instance/test_create_rows.py
git commit -m "test: cover create_rows empty payloads and FK/composite constraints"
```

---

### Task 2: Normalize empty sequence + document `create_rows`

**Files:**
- Modify: `src/parseval/instance/core.py` (`create_row`, `create_rows`, `_normalize_batch`)

- [ ] **Step 1: Ensure `_normalize_batch` treats empty sequence like empty mapping**

Current code already has `return rows or [{}]` for sequences. Confirm and, if a Mapping-vs-list edge case fails, keep this exact behavior:

```python
def _normalize_batch(
    self,
    table: exp.Table,
    payload: (
        Mapping[exp.Identifier | str, Sequence[Any]]
        | Sequence[Mapping[exp.Identifier | str, Any]]
    ),
) -> List[Dict[exp.Identifier, Any]]:
    if isinstance(payload, Mapping):
        if not payload:
            return [{}]
        cols: Dict[exp.Identifier, Sequence[Any]] = {}
        for column, values in payload.items():
            col = self.resolve_column(table, column)
            cols[col] = values if isinstance(values, (list, tuple)) else [values]
        n = max(len(v) for v in cols.values())
        return [
            {c: vals[i] for c, vals in cols.items() if i < len(vals)}
            for i in range(n)
        ]
    rows: List[Dict[exp.Identifier, Any]] = []
    for row in payload:
        rows.append(
            {self.resolve_column(table, c): v for c, v in row.items()}
        )
    return rows or [{}]
```

- [ ] **Step 2: Add boundary docstrings on `create_row` and `create_rows`**

Replace/extend the method bodies’ leading docs (keep signatures unchanged):

```python
def create_row(
    self,
    table: exp.Table | str,
    values: Mapping[exp.Identifier | str | exp.Column, Any] | None = None,
) -> RowCreationResult:
    """Create one constraint-valid row.

    Instance orchestrates FK parent *rows* and placement. Missing cell
    values are invented only by ``DomainGenerator.complete_row`` (no
    Instance type-defaults / unique-freshen / FK value picking).
    """
    # ... existing body unchanged ...


def create_rows(
    self,
    concretes: Mapping[
        exp.Table | str,
        Mapping[exp.Identifier | str, Sequence[Any]]
        | Sequence[Mapping[exp.Identifier | str, Any]],
    ]
    | None = None,
) -> Dict[str, List[RowCreationResult]]:
    """Create rows for explicitly listed tables only.

    Empty ``{}`` or ``[]`` for a table means one fully domain-completed
    row. ``None`` / ``{}`` overall creates nothing. Unlisted tables stay
    empty unless created as FK parents of a listed table.
    """
    # ... existing body unchanged ...
```

- [ ] **Step 3: Re-run Task 1 tests**

Run: `uv run pytest tests/instance/test_create_rows.py -v --tb=short`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/parseval/instance/core.py
git commit -m "docs: clarify create_rows Instance/Domain boundary and empty payloads"
```

---

### Task 3: Fix any constraint gaps exposed by tests

**Files:**
- Modify only if tests fail: `src/parseval/instance/core.py` and/or `src/parseval/domain/generator.py`

- [ ] **Step 1: If `test_child_only_creates_parent_and_binds_fk` fails**

Verify `_ensure_fk_parents` creates a parent when child presets omit FK columns and parent table is empty (already intended). Domain `_bind_foreign_keys` must copy parent key into child when `parent_rows` is non-empty. Do **not** invent FK values in Instance.

- [ ] **Step 2: If `test_composite_pk_freshen_across_batch` fails**

In `DomainGenerator._freshen_uniques`, unlocked composite members must be bumpable; do **not** add generated columns to `locked` inside `_fill_missing` (presets-only lock).

- [ ] **Step 3: If `test_partial_presets_fill_missing_columns` fails**

Ensure `_materialize_row` passes presets by name into `complete_row` and `place_row` receives the full completed dict (every schema column).

- [ ] **Step 4: Re-run focused tests**

Run: `uv run pytest tests/instance/test_create_rows.py -v --tb=short`

Expected: PASS

- [ ] **Step 5: Commit only if code changed**

```bash
git add src/parseval/instance/core.py src/parseval/domain/generator.py
git commit -m "fix: satisfy FK bind and composite uniqueness via domain on create_rows"
```

(Skip commit if no code changes were required.)

---

### Task 4: Regression — Instance + domain suites

**Files:** none (verification only)

- [ ] **Step 1: Run Instance + domain tests**

Run:

```bash
uv run pytest tests/instance/ tests/domain/ tests/test_instance_loader.py tests/test_instance_snapshot.py -q --tb=line
```

Expected: all PASS (including BIRD generate subtests if present under `tests/domain/`).

- [ ] **Step 2: Boundary spot-check**

Run:

```bash
uv run python -c "
from parseval.instance.core import Instance
assert not hasattr(Instance, '_default_for_type')
assert not hasattr(Instance, '_freshen_uniques')
assert not hasattr(Instance, '_next_default_value')
print('boundary ok')
"
```

Expected: `boundary ok`

- [ ] **Step 3: Final commit only if Task 3 left uncommitted fixes; otherwise done**

No empty commit.

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Explicit tables only; empty overall → nothing | Task 1 tests + Task 2 docs |
| `{}` / `[]` → one full row | Task 1 + Task 2 |
| Partial presets + domain fill | Task 1 |
| FK: Instance parents, Domain bind | Task 1 + Task 3 |
| Composite unique freshen | Task 1 + Task 3 |
| No Instance value-invention helpers | Task 1 boundary test + Task 4 |
| Docstrings for boundary / empty semantics | Task 2 |
| Suites stay green | Task 4 |

## Out of scope (do not implement)

- Whole-schema `create_rows()` fill
- Count-based N-row API
- CSP search / solver rewrite
- Rewriting `docs/domain.md`
