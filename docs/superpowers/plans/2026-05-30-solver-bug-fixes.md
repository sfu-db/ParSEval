# Solver Bug Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix solver bugs — domain solver returning unsatisfying results, silent type defaulting, redundant lowering, unused `atom` field.

**Architecture:** The Solver becomes a simple try-A-then-B orchestrator. Lowering is the domain solver's internal concern. Type annotations are validated before solving.

**Tech Stack:** Python 3.10, sqlglot, z3-solver, pytest

---

## File Structure

| File | Changes |
|------|---------|
| `src/parseval/solver/unified.py` | Remove `_lower()`, add `_validate_types()`, simplify `solve()`, remove `atom` from `SolverConstraint` |
| `src/parseval/solver/domain.py` | Return None when no predicates extracted |
| `tests/solver/test_solver.py` | Add tests for type validation, complex expressions |
| `tests/solver/test_domain.py` | Add test for no-predicates case |

---

### Task 1: Domain solver returns None when no predicates extracted

**Files:**
- Modify: `src/parseval/solver/domain.py:217-254`
- Modify: `tests/solver/test_domain.py`

- [ ] **Step 1: Write failing test**

Append to `tests/solver/test_domain.py`:

```python
def test_returns_none_for_complex_expressions():
    """Domain solver can't handle arithmetic — should return None."""
    add = exp.Add(
        this=_col("t1", "x", "INT"),
        expression=_col("t1", "y", "INT"),
    )
    expr = exp.GT(this=add, expression=exp.Literal.number(10))
    solver = DomainSolver()
    result = solver.solve(_constraint(("t1",), [expr]))
    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py::test_returns_none_for_complex_expressions -v
```

Expected: FAIL — domain solver returns `{'t1': {'x': 'value', 'y': 'value'}}` instead of None

- [ ] **Step 3: Add early return in DomainSolver.solve**

In `src/parseval/solver/domain.py`, update the `solve` method. After lowering and before propagation, add a check:

```python
def solve(self, constraint):
    target_tables = constraint.target_tables
    expressions = constraint.constraints
    join_equalities = constraint.join_equalities or []
    alias_map = constraint.alias_map or {}

    self._col_col_eqs = []

    # 1. Extract variables from expressions
    variables = self._extract_variables(target_tables, expressions, alias_map)

    # 2. Lower expressions to predicates
    all_preds: List[ColumnPredicate] = []
    for expr in expressions:
        all_preds.extend(_lower_expression(expr, target_tables, alias_map))

    # 3. If no predicates, no col-col eqs, and no join eqs — can't solve
    if not all_preds and not self._col_col_eqs and not join_equalities:
        return None

    # 4. Apply predicates to variables
    self._apply_predicates(variables, all_preds)

    # 5. Build equivalences from join equalities
    constraints = self._build_equivalences(variables, join_equalities, alias_map)

    # 5b. Add col-col equalities from expressions
    for left_key, right_key in self._col_col_eqs:
        if left_key in variables and right_key in variables:
            constraints.append(CSPConstraint(kind="eq", left=left_key, right=right_key))

    # 6. Propagate
    if not self._propagate(variables, constraints):
        return None

    # 7. Assign
    return self._assign(variables, target_tables, alias_map)
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/domain.py tests/solver/test_domain.py
git commit -m "fix(solver): domain solver returns None when no predicates extracted"
```

---

### Task 2: Remove `atom` from SolverConstraint

**Files:**
- Modify: `src/parseval/solver/unified.py:39-61`
- Modify: `tests/solver/test_solver_constraint.py`

- [ ] **Step 1: Remove `atom` field**

In `src/parseval/solver/unified.py`, remove the `atom` field from `SolverConstraint`:

```python
@dataclass
class SolverConstraint:
    """Constraints for the solver to satisfy.

    Every ``exp.Column`` node inside *constraints* must have its ``.type``
    attribute set to a valid ``exp.DataType`` (e.g.
    ``exp.DataType.build("INT")``).  The solver reads types from these
    annotations — it does not consult any external schema.

    Attributes:
        target_tables: Tables the solver should generate values for.
        constraints: All constraint expressions (comparisons, IS NULL, etc.).
        join_equalities: Cross-table equalities ``(left_table, left_col,
            right_table, right_col)`` that the solver enforces.
        alias_map: Table alias → real name mapping for column resolution.
    """

    target_tables: Tuple[str, ...]
    constraints: List[exp.Expression] = field(default_factory=list)
    join_equalities: List[Tuple[str, str, str, str]] = field(default_factory=list)
    alias_map: Dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 2: Update test for defaults**

In `tests/solver/test_solver_constraint.py`, remove the `atom` assertion:

```python
def test_solver_constraint_defaults():
    c = SolverConstraint(target_tables=("t1",))
    assert c.constraints == []
    assert c.join_equalities == []
    assert c.alias_map == {}
    # atom field removed
```

- [ ] **Step 3: Run tests**

```bash
.venv/bin/python -m pytest tests/solver/ -v
```

Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/parseval/solver/unified.py tests/solver/test_solver_constraint.py
git commit -m "refactor(solver): remove unused atom field from SolverConstraint"
```

---

### Task 3: Add type validation to Solver

**Files:**
- Modify: `src/parseval/solver/unified.py:78-126`
- Modify: `tests/solver/test_solver.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/solver/test_solver.py`:

```python
def test_rejects_unannotated_columns():
    """Solver should reject columns without type annotations."""
    solver = Solver()
    # Column without .type annotation
    col = exp.column("age", table="t1")
    expr = exp.GT(this=col, expression=exp.Literal.number(18))
    constraint = SolverConstraint(target_tables=("t1",), constraints=[expr])
    result = solver.solve(constraint)
    assert not result.sat
    assert "type annotation" in result.reason


def test_accepts_annotated_columns():
    """Solver should accept columns with type annotations."""
    solver = Solver()
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    constraint = SolverConstraint(target_tables=("t1",), constraints=[expr])
    result = solver.solve(constraint)
    assert result.sat
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/solver/test_solver.py -v -k "unannotated or annotated"
```

Expected: FAIL — `test_rejects_unannotated_columns` passes (should fail), `test_accepts_annotated_columns` passes

- [ ] **Step 3: Add _validate_types and update solve**

In `src/parseval/solver/unified.py`, add the validation method and update `solve`:

```python
class Solver:
    # ... __init__ unchanged ...

    def solve(self, constraint: SolverConstraint) -> SolveResult:
        """Satisfy *constraint* using domain + SMT solving."""
        if not constraint.constraints and not constraint.join_equalities:
            return SolveResult(sat=True, assignments={})

        # Validate type annotations
        ok, reason = self._validate_types(constraint)
        if not ok:
            return SolveResult(sat=False, reason=reason)

        # Tier 1: Domain solver
        domain_result = self._try_domain(constraint)
        if domain_result is not None:
            return SolveResult(sat=True, assignments=domain_result)

        # Tier 2: SMT solver
        smt_result = self._try_smt(constraint)
        if smt_result is not None:
            return SolveResult(sat=True, assignments=smt_result)

        return SolveResult(sat=False, reason="all tiers exhausted")

    def _validate_types(self, constraint: SolverConstraint) -> Tuple[bool, str]:
        """Check that all Column nodes have type annotations."""
        for expr in constraint.constraints:
            for col in expr.find_all(exp.Column):
                if col_type(col) is None:
                    return False, f"Column {col.table or '?'}.{col.name} has no type annotation"
        # Also check join equality columns
        for lt, lc, rt, rc in constraint.join_equalities:
            # Join columns don't have expression nodes, so we can't check them here.
            # The caller is responsible for annotating columns in constraints.
            pass
        return True, ""
```

Remove the `_lower` method entirely:

```python
# DELETE the _lower method and its imports
```

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/solver/ -v
```

Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/unified.py tests/solver/test_solver.py
git commit -m "fix(solver): validate type annotations, remove _lower from Solver"
```

---

### Task 4: Verify complex expressions fall through to SMT

**Files:**
- Modify: `tests/solver/test_solver.py`

- [ ] **Step 1: Write test**

```python
def test_complex_expression_uses_smt():
    """Arithmetic expressions should fall through to SMT solver."""
    solver = Solver()
    add = exp.Add(
        this=_col("t1", "a", "INT"),
        expression=_col("t1", "b", "INT"),
    )
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[exp.GT(this=add, expression=exp.Literal.number(10))],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["a"] + result.assignments["t1"]["b"] > 10
```

- [ ] **Step 2: Run test**

```bash
.venv/bin/python -m pytest tests/solver/test_solver.py::test_complex_expression_uses_smt -v
```

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/solver/test_solver.py
git commit -m "test(solver): verify complex expressions fall through to SMT"
```
