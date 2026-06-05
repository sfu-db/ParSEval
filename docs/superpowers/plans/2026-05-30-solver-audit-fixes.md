# Solver Audit Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all issues found during solver module audit — complete lowering, improve propagation, fix result format, remove coupling.

**Architecture:** Incremental fixes to existing solver files. Each task is self-contained and testable.

**Tech Stack:** Python 3.10, sqlglot, z3-solver, pytest

---

## File Structure

| File | Changes |
|------|---------|
| `src/parseval/solver/types.py` | Add `_col_type`, `_type_family` helpers (shared) |
| `src/parseval/solver/domain.py` | Complete lowering, improve propagation, use shared helpers |
| `src/parseval/solver/unified.py` | Fix result format, use shared helpers |
| `src/parseval/solver/smt.py` | Remove `parseval.plan.rex.Const` import |
| `tests/solver/test_domain.py` | Add tests for new lowering + propagation |
| `tests/solver/test_solver.py` | Add tests for result format |

---

### Task 1: Extract shared helpers into types.py

**Files:**
- Modify: `src/parseval/solver/types.py:155`
- Modify: `src/parseval/solver/domain.py:20-52`
- Modify: `src/parseval/solver/unified.py:76-87`

- [ ] **Step 1: Write tests for shared helpers**

```python
# tests/solver/test_types.py — add at the end
from parseval.solver.types import col_type, type_family, TypeFamily
from sqlglot import exp
from sqlglot.expressions import DataType


def test_col_type_from_annotation():
    col = exp.column("age", table="t1")
    col.type = DataType.build("INT")
    assert col_type(col) == DataType.build("INT")


def test_col_type_none_when_missing():
    col = exp.column("age", table="t1")
    assert col_type(col) is None


def test_type_family_integer():
    assert type_family(DataType.build("INT")) == TypeFamily.INTEGER


def test_type_family_text():
    assert type_family(DataType.build("TEXT")) == TypeFamily.TEXT


def test_type_family_boolean():
    assert type_family(DataType.build("BOOLEAN")) == TypeFamily.BOOLEAN
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/solver/test_types.py -v -k "col_type or type_family"
```

Expected: FAIL — `col_type` and `type_family` not exported from types.py

- [ ] **Step 3: Add helpers to types.py**

Append to `src/parseval/solver/types.py`:

```python
from sqlglot import exp
from parseval.dtype import DataType


def col_type(col: exp.Column) -> Optional[DataType]:
    """Read the annotated type from a Column node, or None."""
    dtype = getattr(col, "type", None)
    if dtype is None:
        return None
    if isinstance(dtype, DataType):
        return dtype
    try:
        return DataType.build(str(dtype))
    except Exception:
        return None


def type_family(dtype: DataType) -> TypeFamily:
    """Map a DataType to a TypeFamily."""
    if dtype.is_type(*DataType.INTEGER_TYPES):
        return TypeFamily.INTEGER
    if dtype.is_type(*DataType.REAL_TYPES):
        return TypeFamily.DECIMAL
    if dtype.is_type(DataType.Type.BOOLEAN):
        return TypeFamily.BOOLEAN
    if dtype.is_type(
        DataType.Type.DATETIME, DataType.Type.DATETIME64,
        DataType.Type.TIMESTAMP, DataType.Type.TIMESTAMPLTZ,
        DataType.Type.TIMESTAMPTZ, DataType.Type.TIMESTAMP_MS,
        DataType.Type.TIMESTAMP_NS, DataType.Type.TIMESTAMP_S,
    ):
        return TypeFamily.DATETIME
    if dtype.is_type(DataType.Type.DATE):
        return TypeFamily.DATE
    if dtype.is_type(DataType.Type.TIME, DataType.Type.TIMETZ):
        return TypeFamily.TIME
    return TypeFamily.TEXT
```

- [ ] **Step 4: Update domain.py to use shared helpers**

Replace the local `_col_type` and `_type_family` in `domain.py` with imports:

```python
# domain.py — replace lines 20-52 with:
from .types import (
    CSPConstraint, CSPVariable, ColumnPredicate, TypeFamily, ValueSpace,
    col_type, type_family,
)
```

Update all calls: `_col_type(col)` → `col_type(col)`, `_type_family(dtype)` → `type_family(dtype)`.

- [ ] **Step 5: Update unified.py to use shared helpers**

Replace the local `_col_type` in `unified.py` with import:

```python
# unified.py — replace lines 76-87 with:
from .types import col_type
```

Update all calls: `_col_type(col)` → `col_type(col)`.

- [ ] **Step 6: Run all tests**

```bash
.venv/bin/python -m pytest tests/solver/ -v
```

Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add src/parseval/solver/types.py src/parseval/solver/domain.py src/parseval/solver/unified.py tests/solver/test_types.py
git commit -m "refactor(solver): extract shared col_type/type_family helpers into types.py"
```

---

### Task 2: Complete lowering — add NOT, IS NOT NULL

**Files:**
- Modify: `src/parseval/solver/domain.py:66-126`
- Modify: `tests/solver/test_domain.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/solver/test_domain.py`:

```python
def test_is_not_null():
    expr = exp.Is(
        this=_col("t1", "name", "TEXT"),
        expression=exp.Not(this=exp.Null()),
    )
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["name"] is not None


def test_not_gt():
    """NOT(col > 10) should lower to col <= 10."""
    inner = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(10))
    expr = exp.Not(this=inner)
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["age"] <= 10


def test_not_eq():
    """NOT(col = 5) should lower to col != 5."""
    inner = exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(5))
    expr = exp.Not(this=inner)
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["x"] != 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py -v -k "is_not_null or not_gt or not_eq"
```

Expected: FAIL — NOT and IS NOT NULL not lowered

- [ ] **Step 3: Add NOT and IS NOT NULL lowering**

In `domain.py`, update `_lower_recursive` to handle `exp.Not`:

```python
def _lower_recursive(expr, tables, alias_map, out):
    if isinstance(expr, exp.And):
        _lower_recursive(expr.left, tables, alias_map, out)
        _lower_recursive(expr.right, tables, alias_map, out)
        return
    if isinstance(expr, exp.Paren):
        _lower_recursive(expr.this, tables, alias_map, out)
        return
    if isinstance(expr, exp.Or):
        _lower_recursive(expr.left, tables, alias_map, out)
        return
    if isinstance(expr, exp.Not):
        _lower_not(expr.this, tables, alias_map, out)
        return
    pred = _lower_atom(expr, tables, alias_map)
    if pred:
        out.append(pred)


_NEGATED_OPS = {"=": "!=", "!=": "=", ">": "<=", ">=": "<", "<": ">=", "<=": ">"}


def _lower_not(inner, tables, alias_map, out):
    """Lower NOT(inner) by negating the predicate."""
    # NOT(IS NULL) → IS NOT NULL
    if isinstance(inner, exp.Is):
        if isinstance(inner.this, exp.Column) and isinstance(inner.expression, exp.Null):
            table = _resolve_table(inner.this, tables, alias_map)
            out.append(ColumnPredicate(table=table, column=inner.this.name, op="not_null", value=True))
            return
    # NOT(comparison) → flip operator
    _OP_MAP = {exp.EQ: "=", exp.NEQ: "!=", exp.GT: ">", exp.GTE: ">=", exp.LT: "<", exp.LTE: "<="}
    for cls, op in _OP_MAP.items():
        if isinstance(inner, cls):
            col, val = _extract_col_literal(inner)
            if col is not None and val is not None:
                neg_op = _NEGATED_OPS.get(op, op)
                table = _resolve_table(col, tables, alias_map)
                out.append(ColumnPredicate(table=table, column=col.name, op=neg_op, value=val))
                return
    # Fallback: lower the inner expression as-is (satisfiability)
    _lower_recursive(inner, tables, alias_map, out)
```

Also add `not_null` handling in `_apply_predicates`:

```python
elif op == "not_null":
    space.not_null = True
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py -v -k "is_not_null or not_gt or not_eq"
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/domain.py tests/solver/test_domain.py
git commit -m "feat(solver): add NOT and IS NOT NULL lowering"
```

---

### Task 3: Complete lowering — add IN, Between

**Files:**
- Modify: `src/parseval/solver/domain.py:87-126`
- Modify: `tests/solver/test_domain.py`

- [ ] **Step 1: Write failing tests**

```python
def test_in_list():
    expr = exp.In(
        this=_col("t1", "status", "TEXT"),
        expressions=[exp.Literal.string("active"), exp.Literal.string("pending")],
    )
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert result["t1"]["status"] in ("active", "pending")


def test_between():
    expr = exp.Between(
        this=_col("t1", "age", "INT"),
        low=exp.Literal.number(18),
        high=exp.Literal.number(65),
    )
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is not None
    assert 18 <= result["t1"]["age"] <= 65
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py -v -k "in_list or between"
```

Expected: FAIL

- [ ] **Step 3: Add IN and Between lowering**

In `_lower_atom`, add after the `Like` block:

```python
elif isinstance(atom, exp.In):
    col = atom.this
    expressions = atom.args.get("expressions") or []
    if isinstance(col, exp.Column) and expressions:
        values = []
        for e in expressions:
            v = _literal_value(e)
            if v is not None:
                values.append(v)
        if values:
            table = _resolve_table(col, tables, alias_map)
            return ColumnPredicate(table=table, column=col.name, op="in", value=values)
elif isinstance(atom, exp.Between):
    col = atom.this
    low = atom.args.get("low")
    high = atom.args.get("high")
    if isinstance(col, exp.Column) and low and high:
        low_val = _literal_value(low)
        high_val = _literal_value(high)
        if low_val is not None and high_val is not None:
            table = _resolve_table(col, tables, alias_map)
            return ColumnPredicate(table=table, column=col.name, op="between", value=(low_val, high_val))
```

Add `in` and `between` handling in `_apply_predicates`:

```python
elif op == "in" and isinstance(val, list):
    space.narrow_in(set(val))
elif op == "between" and isinstance(val, tuple):
    space.narrow_min(val[0])
    space.narrow_max(val[1])
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py -v -k "in_list or between"
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/domain.py tests/solver/test_domain.py
git commit -m "feat(solver): add IN and Between lowering"
```

---

### Task 4: Improve propagation — propagate bounds across eq constraints

**Files:**
- Modify: `src/parseval/solver/domain.py:301-335`
- Modify: `tests/solver/test_domain.py`

- [ ] **Step 1: Write failing test**

```python
def test_bounds_propagation_across_eq():
    """a.x > 10 AND a.x = b.y → b.y should also be > 10."""
    solver = DomainSolver()
    result = solver.solve(_constraint(
        ("a", "b"),
        [exp.GT(this=_col("a", "x", "INT"), expression=exp.Literal.number(10))],
        join_equalities=[("a", "x", "b", "y")],
    ))
    assert result is not None
    assert result["b"]["y"] > 10
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py::test_bounds_propagation_across_eq -v
```

Expected: PASS (currently passes via finalize, but bounds not actually propagated)

- [ ] **Step 3: Improve _propagate to propagate bounds**

Update `_propagate` in `domain.py` to propagate min/max across eq constraints:

```python
def _propagate(self, variables, constraints):
    changed = True
    iterations = 0
    while changed and iterations < 10:
        changed = False
        iterations += 1
        for c in constraints:
            if c.kind == "eq":
                left = variables.get(c.left)
                right = variables.get(c.right)
                if left and right:
                    # Propagate equals
                    if left.space.equals is not None and right.space.equals is None:
                        right.space.narrow_eq(left.space.equals)
                        changed = True
                    elif right.space.equals is not None and left.space.equals is None:
                        left.space.narrow_eq(right.space.equals)
                        changed = True
                    # Propagate bounds (bidirectional)
                    if left.space.min_val is not None:
                        if right.space.min_val is None or left.space.min_val > right.space.min_val:
                            right.space.narrow_min(left.space.min_val)
                            changed = True
                    if right.space.min_val is not None:
                        if left.space.min_val is None or right.space.min_val > left.space.min_val:
                            left.space.narrow_min(right.space.min_val)
                            changed = True
                    if left.space.max_val is not None:
                        if right.space.max_val is None or left.space.max_val < right.space.max_val:
                            right.space.narrow_max(left.space.max_val)
                            changed = True
                    if right.space.max_val is not None:
                        if left.space.max_val is None or right.space.max_val < left.space.max_val:
                            left.space.narrow_max(right.space.max_val)
                            changed = True
        for var in variables.values():
            if var.space.is_empty():
                return False
    # Finalize: pick values for eq-constrained pairs that still lack equals
    for c in constraints:
        if c.kind == "eq":
            left = variables.get(c.left)
            right = variables.get(c.right)
            if left and right and left.space.equals is None and right.space.equals is None:
                val = left.space.pick()
                if val is not None:
                    left.space.narrow_eq(val)
                    right.space.narrow_eq(val)
    return True
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/domain.py tests/solver/test_domain.py
git commit -m "feat(solver): propagate bounds across eq constraints"
```

---

### Task 5: Fix result format — return physical table names

**Files:**
- Modify: `src/parseval/solver/domain.py:337-351`
- Modify: `src/parseval/solver/unified.py:115-142`
- Modify: `tests/solver/test_solver.py`

- [ ] **Step 1: Write failing test**

```python
def test_result_uses_physical_table_names():
    """When alias_map maps aliases to physical tables, result keys should be physical names."""
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("a", "b"),
        constraints=[
            exp.EQ(this=_col("a", "name", "TEXT"), expression=exp.Literal.string("Alice")),
        ],
        alias_map={"a": "people", "b": "people"},
    )
    result = solver.solve(constraint)
    assert result.sat
    # Keys should be physical table names, not aliases
    assert "people" in result.assignments
    assert "a" not in result.assignments
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/solver/test_solver.py::test_result_uses_physical_table_names -v
```

Expected: FAIL — keys are aliases

- [ ] **Step 3: Add alias→physical mapping to DomainSolver._assign**

Update `_assign` in `domain.py`:

```python
def _assign(self, variables, target_tables, alias_map=None):
    alias_map = alias_map or {}
    result: Dict[str, Dict[str, Any]] = {}
    for var in variables.values():
        val = var.space.pick()
        var.assigned = val
        # Map alias to physical table name
        physical = alias_map.get(var.table, var.table)
        result.setdefault(physical, {})[var.column] = val
    if not result:
        for t in target_tables:
            physical = alias_map.get(t, t)
            result[physical] = {}
    return result
```

Update the call in `solve()`:

```python
return self._assign(variables, target_tables, alias_map)
```

- [ ] **Step 4: Update Solver._try_smt to map aliases to physical names**

In `unified.py`, update the assignments grouping in `_try_smt`:

```python
# Group assignments by physical table name.
alias_map = constraint.alias_map or {}
assignments: Dict[str, Dict[str, Any]] = {}
for var_name, value in solutions.items():
    parts = var_name.split(".")
    if len(parts) == 2:
        table, col = parts
    else:
        table = constraint.target_tables[0] if constraint.target_tables else ""
        col = var_name
    physical = alias_map.get(table, table)
    assignments.setdefault(physical, {})[col] = value
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/solver/ -v
```

Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/parseval/solver/domain.py src/parseval/solver/unified.py tests/solver/test_solver.py
git commit -m "fix(solver): return physical table names in result, not aliases"
```

---

### Task 6: Remove parseval.plan import from SMT solver

**Files:**
- Modify: `src/parseval/solver/smt.py:14`

- [ ] **Step 1: Check what Const is used for**

```bash
grep -n "Const" src/parseval/solver/smt.py | head -10
```

- [ ] **Step 2: Remove or inline the import**

If `Const` is only used in `_to_z3_expr` for type checking, replace with `isinstance` check on the literal's datatype attribute. If it's not used at all, just remove the import.

Replace:
```python
from parseval.plan.rex import Const
```

With nothing (if unused), or with an inline check.

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest tests/solver/test_smt.py -v
```

Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/parseval/solver/smt.py
git commit -m "refactor(solver): remove parseval.plan import from SMT solver"
```

---

### Task 7: Add column-column comparison lowering

**Files:**
- Modify: `src/parseval/solver/domain.py:87-126`
- Modify: `tests/solver/test_domain.py`

- [ ] **Step 1: Write failing test**

```python
def test_column_column_equality():
    """a.x = b.y without join_equalities — should create eq constraint."""
    solver = DomainSolver()
    expr = exp.EQ(this=_col("a", "x", "INT"), expression=_col("b", "y", "INT"))
    result = solver.solve(_constraint(("a", "b"), [expr]))
    assert result is not None
    assert result["a"]["x"] == result["b"]["y"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py::test_column_column_equality -v
```

Expected: FAIL — `_extract_col_literal` only handles col-literal

- [ ] **Step 3: Add col-col extraction**

In `domain.py`, add a new extraction function:

```python
def _extract_col_col(node: exp.Expression):
    """Extract (left_col, right_col) from a column-column comparison."""
    left, right = node.this, node.expression
    if isinstance(left, exp.Column) and isinstance(right, exp.Column):
        return left, right
    return None, None
```

Update `_lower_atom` to handle col-col EQ:

```python
if isinstance(atom, exp.EQ):
    col, val = _extract_col_literal(atom)
    if col and val is not None:
        op = "="
    else:
        left_col, right_col = _extract_col_col(atom)
        if left_col and right_col:
            lt = _resolve_table(left_col, tables, alias_map)
            rt = _resolve_table(right_col, tables, alias_map)
            return ColumnPredicate(
                table=lt, column=left_col.name,
                op="eq_col", value=f"{rt}.{right_col.name}",
            )
```

Handle `eq_col` in `_apply_predicates` by creating a CSPConstraint:

Actually, `eq_col` predicates need special handling — they create eq constraints, not value narrowing. The cleanest approach: return `None` from `_lower_atom` for col-col, and handle them in `DomainSolver.solve()` by extracting col-col equalities from expressions directly.

Simpler approach — extract col-col equalities in `_extract_variables`:

```python
# In _extract_variables, also detect col-col equalities
self._col_col_eqs = []
for expr in expressions:
    if isinstance(expr, exp.EQ):
        left, right = expr.this, expr.expression
        if isinstance(left, exp.Column) and isinstance(right, exp.Column):
            lt = _resolve_table(left, tables, alias_map)
            rt = _resolve_table(right, tables, alias_map)
            self._col_col_eqs.append((f"{lt}.{left.name}", f"{rt}.{right.name}"))
```

Then in `solve()`, add these as eq constraints:

```python
# 4b. Add col-col equalities
for left_key, right_key in self._col_col_eqs:
    if left_key in variables and right_key in variables:
        constraints.append(CSPConstraint(kind="eq", left=left_key, right=right_key))
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/domain.py tests/solver/test_domain.py
git commit -m "feat(solver): add column-column equality lowering"
```
