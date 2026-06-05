# DomainSolver Soundness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `DomainSolver` a sound tri-state fast path that returns `sat`, `unsat`, or `unknown`, short-circuits unified solving on domain `unsat`, trusts domain `sat`, and falls back to SMT only on domain `unknown`.

**Architecture:** Keep the implementation inside the existing solver files to minimize churn: `domain.py` owns domain analysis and assignment building, `types.py` owns value-space consistency checks, and `unified.py` owns orchestration. The domain tier becomes conservative and explicit about unsupported formulas, while the SMT tier becomes strict about unsupported translation instead of silently solving subsets.

**Tech Stack:** Python 3.9+, `sqlglot`, `z3-solver`, `pytest`

---

## File Map

- Modify: `src/parseval/solver/domain.py`
  - Add `DomainResult` and internal tri-state analysis flow.
  - Make `OR` / `NOT` / unsupported expressions return `unknown` instead of silently lowering partial constraints.
  - Keep assignment keys alias-qualified instead of remapping them to physical table names.

- Modify: `src/parseval/solver/types.py`
  - Tighten `ValueSpace.is_empty()` and `ValueSpace.pick()` so boolean and finite-domain contradictions are detected correctly.

- Modify: `src/parseval/solver/unified.py`
  - Consume `DomainResult`.
  - Short-circuit on domain `unsat`.
  - Retry the original full constraint set in SMT only on domain `unknown`.
  - Reject partial SMT translation.

- Modify: `tests/solver/test_domain.py`
  - Replace dict-only expectations with tri-state expectations.
  - Add regression tests for `unknown`, `unsat`, boolean exhaustion, and alias-preserving output.

- Modify: `tests/solver/test_solver.py`
  - Add unified orchestration tests for domain short-circuiting, domain fallback, and strict SMT failure behavior.

- Modify: `tests/solver/test_smt.py`
  - Add a regression test for unsupported SMT translation staying non-`sat`.

- Modify: `docs/solver.md`
  - Update public solver semantics to describe tri-state domain behavior and strict SMT fallback.

## Task 1: Introduce `DomainResult` and Convert the Domain API

**Files:**
- Modify: `src/parseval/solver/domain.py`
- Modify: `tests/solver/test_domain.py`

- [ ] **Step 1: Write the failing tri-state tests**

Add these tests near the top of `tests/solver/test_domain.py`:

```python
def test_domain_returns_sat_result_for_simple_equality():
    expr = exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25))
    result = DomainSolver().solve(_constraint(("t1",), [expr]))
    assert result.status == "sat"
    assert result.assignments == {"t1": {"age": 25}}
    assert result.reason == ""


def test_domain_returns_unsat_for_conflicting_equalities():
    result = DomainSolver().solve(_constraint(
        ("t1",),
        [
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25)),
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(30)),
        ],
    ))
    assert result.status == "unsat"
    assert result.assignments is None
    assert result.reason


def test_domain_returns_unknown_for_arithmetic_predicate():
    expr = exp.GT(
        this=exp.Add(this=_col("t1", "x", "INT"), expression=_col("t1", "y", "INT")),
        expression=exp.Literal.number(10),
    )
    result = DomainSolver().solve(_constraint(("t1",), [expr]))
    assert result.status == "unknown"
    assert result.assignments is None
    assert result.reason == "unsupported_arithmetic"
```

- [ ] **Step 2: Run the focused tests to verify they fail**

Run:

```bash
pytest tests/solver/test_domain.py -k "tri_state or unknown_for_arithmetic or conflicting_equalities or simple_equality" -q
```

Expected:

```text
FAIL ... AttributeError: 'dict' object has no attribute 'status'
```

- [ ] **Step 3: Add `DomainResult` and return it from `DomainSolver.solve()`**

Start by adding a small dataclass at the top of `src/parseval/solver/domain.py` and returning it everywhere from `solve()`:

```python
from dataclasses import dataclass


@dataclass
class DomainResult:
    status: str
    assignments: Optional[Dict[str, Dict[str, Any]]] = None
    reason: str = ""


class DomainSolver:
    def solve(self, constraint) -> DomainResult:
        target_tables = constraint.target_tables
        expressions = constraint.constraints
        join_equalities = constraint.join_equalities or []
        alias_map = constraint.alias_map or {}

        if not expressions and not join_equalities:
            return DomainResult(status="sat", assignments={t: {} for t in target_tables})

        variables = self._extract_variables(target_tables, expressions, alias_map)
        all_preds: List[ColumnPredicate] = []
        for expr in expressions:
            lowered = _lower_expression(expr, target_tables, alias_map)
            all_preds.extend(lowered)

        if expressions and not all_preds and not self._col_col_eqs and not join_equalities:
            return DomainResult(status="unknown", reason="unsupported_expression")

        self._apply_predicates(variables, all_preds)
        constraints = self._build_equivalences(variables, join_equalities, alias_map)
        for left_key, right_key in self._col_col_eqs:
            if left_key in variables and right_key in variables:
                constraints.append(CSPConstraint(kind="eq", left=left_key, right=right_key))

        if not self._propagate(variables, constraints):
            return DomainResult(status="unsat", reason="contradictory_bounds")

        return DomainResult(status="sat", assignments=self._assign(variables, target_tables))
```

This is only an API conversion. Do not solve the soundness issues in this task yet.

- [ ] **Step 4: Run the focused tests to verify the API shape now works**

Run:

```bash
pytest tests/solver/test_domain.py -k "simple_equality or conflicting_equalities or arithmetic_predicate" -q
```

Expected:

```text
..F
```

The arithmetic test should still fail because the solver still returns `sat` instead of `unknown`. That is the next task.

- [ ] **Step 5: Commit the API conversion**

```bash
git add src/parseval/solver/domain.py tests/solver/test_domain.py
git commit -m "refactor domain solver to return tri-state results"
```

## Task 2: Make Domain Analysis Sound for Unsupported Expressions, `OR`, and `NOT`

**Files:**
- Modify: `src/parseval/solver/domain.py`
- Modify: `tests/solver/test_domain.py`

- [ ] **Step 1: Write the failing soundness regression tests**

Add these tests to `tests/solver/test_domain.py`:

```python
def test_domain_returns_unknown_for_mixed_supported_and_unsupported_and():
    supported = exp.EQ(this=_col("t1", "name", "TEXT"), expression=exp.Literal.string("Alice"))
    unsupported = exp.GT(
        this=exp.Add(this=_col("t1", "a", "INT"), expression=_col("t1", "b", "INT")),
        expression=exp.Literal.number(1000),
    )
    result = DomainSolver().solve(_constraint(("t1",), [exp.And(this=supported, expression=unsupported)]))
    assert result.status == "unknown"
    assert result.assignments is None
    assert result.reason == "unsupported_arithmetic"


def test_domain_returns_unknown_for_not_or_expression():
    expr = exp.Not(this=exp.Or(
        this=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(1)),
        expression=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(2)),
    ))
    result = DomainSolver().solve(_constraint(("t1",), [expr]))
    assert result.status == "unknown"
    assert result.assignments is None
    assert result.reason == "unsupported_not"


def test_domain_returns_unsat_for_or_with_two_unsat_branches():
    left = exp.And(
        this=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(1)),
        expression=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(2)),
    )
    right = exp.And(
        this=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(3)),
        expression=exp.EQ(this=_col("t1", "x", "INT"), expression=exp.Literal.number(4)),
    )
    result = DomainSolver().solve(_constraint(("t1",), [exp.Or(this=left, expression=right)]))
    assert result.status == "unsat"
    assert result.assignments is None
```

- [ ] **Step 2: Run the new regressions to verify they fail**

Run:

```bash
pytest tests/solver/test_domain.py -k "mixed_supported_and_unsupported or not_or_expression or two_unsat_branches" -q
```

Expected:

```text
FFF
```

The current solver will either return `sat` incorrectly or lower only one branch.

- [ ] **Step 3: Introduce an internal analysis pass that composes tri-state outcomes**

Refactor `src/parseval/solver/domain.py` so AST walking decides `sat | unsat | unknown` before assignment building:

```python
@dataclass
class LoweringOutcome:
    status: str
    predicates: List[ColumnPredicate] = field(default_factory=list)
    equalities: List[Tuple[str, str]] = field(default_factory=list)
    reason: str = ""


def _merge_and(left: LoweringOutcome, right: LoweringOutcome) -> LoweringOutcome:
    if left.status == "unsat":
        return left
    if right.status == "unsat":
        return right
    if left.status == "unknown":
        return left
    if right.status == "unknown":
        return right
    return LoweringOutcome(
        status="sat",
        predicates=[*left.predicates, *right.predicates],
        equalities=[*left.equalities, *right.equalities],
    )


def _merge_or(left: LoweringOutcome, right: LoweringOutcome) -> LoweringOutcome:
    if left.status == "unsat" and right.status == "unsat":
        return LoweringOutcome(status="unsat", reason=left.reason or right.reason)
    if left.status == "sat" and right.status == "unsat":
        return left
    if right.status == "sat" and left.status == "unsat":
        return right
    if left.status == "sat" and right.status == "sat":
        return left
    return LoweringOutcome(status="unknown", reason="unsupported_or")


def _analyze_expression(expr: exp.Expression, tables, alias_map) -> LoweringOutcome:
    if isinstance(expr, exp.And):
        return _merge_and(
            _analyze_expression(expr.left, tables, alias_map),
            _analyze_expression(expr.right, tables, alias_map),
        )
    if isinstance(expr, exp.Or):
        return _merge_or(
            _analyze_expression(expr.left, tables, alias_map),
            _analyze_expression(expr.right, tables, alias_map),
        )
    if isinstance(expr, exp.Not):
        lowered = _lower_negated_atom(expr.this, tables, alias_map)
        if lowered is None:
            return LoweringOutcome(status="unknown", reason="unsupported_not")
        return LoweringOutcome(status="sat", predicates=[lowered])

    pred = _lower_atom(expr, tables, alias_map)
    if pred is None:
        return LoweringOutcome(status="unknown", reason=_unsupported_reason(expr))
    return LoweringOutcome(status="sat", predicates=[pred])
```

Then make `DomainSolver.solve()` consume `LoweringOutcome` instead of blindly appending lowered predicates.

- [ ] **Step 4: Run the soundness-focused domain suite**

Run:

```bash
pytest tests/solver/test_domain.py -k "mixed_supported_and_unsupported or not_or_expression or two_unsat_branches or arithmetic_predicate" -q
```

Expected:

```text
....
```

- [ ] **Step 5: Commit the sound analysis refactor**

```bash
git add src/parseval/solver/domain.py tests/solver/test_domain.py
git commit -m "fix domain solver soundness for unsupported logic"
```

## Task 3: Fix `ValueSpace` Contradictions and Preserve Alias Keys in Assignments

**Files:**
- Modify: `src/parseval/solver/types.py`
- Modify: `src/parseval/solver/domain.py`
- Modify: `tests/solver/test_domain.py`

- [ ] **Step 1: Write the failing contradiction and alias tests**

Add these tests to `tests/solver/test_domain.py`:

```python
def test_domain_detects_empty_boolean_domain():
    expr = exp.And(
        this=exp.NEQ(this=_col("t1", "flag", "BOOLEAN"), expression=exp.Boolean(this=True)),
        expression=exp.NEQ(this=_col("t1", "flag", "BOOLEAN"), expression=exp.Boolean(this=False)),
    )
    result = DomainSolver().solve(_constraint(("t1",), [expr]))
    assert result.status == "unsat"
    assert result.assignments is None
    assert result.reason == "empty_boolean_domain"


def test_domain_preserves_aliases_for_self_join_assignments():
    result = DomainSolver().solve(_constraint(
        ("a", "b"),
        [
            exp.EQ(this=_col("a", "name", "TEXT"), expression=exp.Literal.string("Alice")),
            exp.EQ(this=_col("b", "name", "TEXT"), expression=exp.Literal.string("Bob")),
        ],
        join_equalities=[("a", "manager_id", "b", "id")],
    ))
    assert result.status == "sat"
    assert result.assignments == {
        "a": {"name": "Alice", "manager_id": result.assignments["a"]["manager_id"]},
        "b": {"name": "Bob", "id": result.assignments["b"]["id"]},
    }
    assert result.assignments["a"]["manager_id"] == result.assignments["b"]["id"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run:

```bash
pytest tests/solver/test_domain.py -k "empty_boolean_domain or preserves_aliases_for_self_join_assignments" -q
```

Expected:

```text
FF
```

The current boolean logic returns a value instead of `unsat`, and alias handling still remaps through `alias_map`.

- [ ] **Step 3: Tighten finite-domain emptiness and stop remapping aliases in domain assignments**

Update `src/parseval/solver/types.py`:

```python
def is_empty(self) -> bool:
    if self.must_null and self.not_null:
        return True
    if self.must_null:
        return False
    if self.equals is not None:
        if self.equals in self.not_equals:
            return True
        if self.allowed is not None and self.equals not in self.allowed:
            return True
        if self.min_val is not None and self.equals < self.min_val:
            return True
        if self.max_val is not None and self.equals > self.max_val:
            return True
        return False
    if self.family == TypeFamily.BOOLEAN and self.not_equals >= {True, False}:
        return True
    if self.min_val is not None and self.max_val is not None and self.min_val > self.max_val:
        return True
    if self.allowed is not None and not (self.allowed - self.not_equals):
        return True
    return False


def pick(self) -> Any:
    if self.must_null:
        return None
    if self.equals is not None:
        return self.equals
    if self.allowed is not None:
        valid = self.allowed - self.not_equals
        return min(valid) if valid else None
    if self.family == TypeFamily.BOOLEAN:
        for candidate in (False, True):
            if candidate not in self.not_equals:
                return candidate
        return None
```

Update `src/parseval/solver/domain.py` assignment building:

```python
def _assign(
    self,
    variables: Dict[str, CSPVariable],
    target_tables: Tuple[str, ...],
) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for var in variables.values():
        result.setdefault(var.table, {})[var.column] = var.space.pick()
    if not result:
        for table in target_tables:
            result[table] = {}
    return result
```

Also map empty-space reasons inside domain propagation so boolean exhaustion reports `empty_boolean_domain` instead of the generic contradiction reason.

- [ ] **Step 4: Run the full domain suite**

Run:

```bash
pytest tests/solver/test_domain.py -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 5: Commit the contradiction and alias fixes**

```bash
git add src/parseval/solver/types.py src/parseval/solver/domain.py tests/solver/test_domain.py
git commit -m "fix domain contradictions and alias-preserving assignments"
```

## Task 4: Update Unified Solver Orchestration and Make SMT Fallback Strict

**Files:**
- Modify: `src/parseval/solver/unified.py`
- Modify: `tests/solver/test_solver.py`
- Modify: `tests/solver/test_smt.py`

- [ ] **Step 1: Write the failing unified and SMT fallback tests**

Add these tests to `tests/solver/test_solver.py`:

```python
def test_solver_skips_smt_when_domain_returns_unsat(monkeypatch):
    solver = Solver()
    called = False

    def fail_if_called(_constraint):
        nonlocal called
        called = True
        raise AssertionError("SMT should not run for domain unsat")

    monkeypatch.setattr(solver, "_try_smt", fail_if_called)
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(1)),
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(2)),
        ],
    )
    result = solver.solve(constraint)
    assert not result.sat
    assert result.reason
    assert called is False


def test_solver_uses_smt_only_for_domain_unknown():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.GT(
                this=exp.Add(this=_col("t1", "a", "INT"), expression=_col("t1", "b", "INT")),
                expression=exp.Literal.number(10),
            ),
        ],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["a"] + result.assignments["t1"]["b"] > 10


def test_solver_rejects_partial_smt_translation():
    unsupported = exp.EQ(
        this=exp.Anonymous(this="MYSTERY", expressions=[_col("t1", "x", "INT")]),
        expression=exp.Literal.number(1),
    )
    supported = exp.EQ(this=_col("t1", "y", "INT"), expression=exp.Literal.number(7))
    result = Solver().solve(SolverConstraint(target_tables=("t1",), constraints=[unsupported, supported]))
    assert not result.sat
    assert result.reason == "unsupported_smt_expression"
```

Add this regression to `tests/solver/test_smt.py`:

```python
def test_translate_returns_none_for_unsupported_expression():
    solver = SMTSolver()
    expr = exp.EQ(
        this=exp.Anonymous(this="MYSTERY", expressions=[_col("t1", "x", "INT")]),
        expression=exp.Literal.number(1),
    )
    assert solver.translate(expr) is None
```

- [ ] **Step 2: Run the focused unified and SMT tests to verify they fail**

Run:

```bash
pytest tests/solver/test_solver.py -k "skips_smt_when_domain_returns_unsat or uses_smt_only_for_domain_unknown or rejects_partial_smt_translation" -q
pytest tests/solver/test_smt.py -k "unsupported_expression" -q
```

Expected:

```text
FFF
.
```

The SMTSolver translation helper already returns `None`; the failure is in unified orchestration still accepting subset solutions.

- [ ] **Step 3: Consume `DomainResult` in `Solver.solve()` and fail closed on SMT translation gaps**

Refactor `src/parseval/solver/unified.py` like this:

```python
def solve(self, constraint: SolverConstraint) -> SolveResult:
    if not constraint.constraints and not constraint.join_equalities:
        return SolveResult(sat=True, assignments={})

    ok, reason = self._validate_types(constraint)
    if not ok:
        return SolveResult(sat=False, reason=reason)

    domain_result = self._try_domain(constraint)
    if domain_result.status == "unsat":
        return SolveResult(sat=False, reason=domain_result.reason)
    if domain_result.status == "sat":
        return SolveResult(sat=True, assignments=domain_result.assignments or {})

    return self._try_smt(constraint)


def _try_smt(self, constraint: SolverConstraint) -> SolveResult:
    from .smt import SMTSolver

    smt = SMTSolver(timeout_ms=self.timeout_ms)
    unsupported = False

    for expr in constraint.constraints:
        for col in expr.find_all(exp.Column):
            col_key = f"{normalize_name(col.table or '')}.{normalize_name(col.name)}"
            smt.declare_variable(col_key, col_type(col) or DataType.build("TEXT"))

    for expr in constraint.constraints:
        z3_expr = smt.translate(expr)
        if z3_expr is None:
            unsupported = True
            break
        smt.add(z3_expr)

    if unsupported:
        return SolveResult(sat=False, reason="unsupported_smt_expression")

    for lt, lc, rt, rc in constraint.join_equalities:
        left_key = f"{normalize_name(lt)}.{normalize_name(lc)}"
        right_key = f"{normalize_name(rt)}.{normalize_name(rc)}"
        smt.add_raw(smt.context["variable_to_z3"][left_key] == smt.context["variable_to_z3"][right_key])

    status, solutions = smt.solve()
    if status != "sat":
        return SolveResult(sat=False, reason="unsat")

    assignments: Dict[str, Dict[str, Any]] = {}
    for var_name, value in solutions.items():
        table, col = var_name.split(".", 1)
        assignments.setdefault(table, {})[col] = value
    return SolveResult(sat=True, assignments=assignments)
```

Keep the fallback strict: the moment translation is incomplete, return failure instead of solving a subset.

- [ ] **Step 4: Run the unified solver suite**

Run:

```bash
pytest tests/solver/test_solver.py -q
pytest tests/solver/test_smt.py -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 5: Commit the unified orchestration changes**

```bash
git add src/parseval/solver/unified.py tests/solver/test_solver.py tests/solver/test_smt.py
git commit -m "enforce sound domain to smt solver orchestration"
```

## Task 5: Update Solver Docs and Run the Full Regression Suite

**Files:**
- Modify: `docs/solver.md`
- Modify: `src/parseval/solver/__init__.py`
- Modify: `src/parseval/solver/unified.py`

- [ ] **Step 1: Write the documentation updates**

Update the public-facing docs and module docstrings so they no longer describe the old best-effort domain behavior. Add language like this to `docs/solver.md`:

```md
## Solver Flow

ParSEval uses a two-tier solver:

1. `DomainSolver` is a sound fast path. It returns one of:
   - `sat`: the domain tier handled the full formula and produced assignments
   - `unsat`: the domain tier proved the constraints contradictory
   - `unknown`: the domain tier cannot soundly handle the full formula
2. `SMTSolver` runs only when the domain tier returns `unknown`.

The SMT fallback is strict: if SQL-to-SMT translation is incomplete, the solver returns failure instead of solving a subset of the input constraints.
```

Update the top-level module docstring in `src/parseval/solver/__init__.py` to stop promising only `Solver`, `SolveResult`, and `SolverConstraint` semantics without explaining the domain tri-state.

- [ ] **Step 2: Run docs-adjacent regression tests**

Run:

```bash
pytest tests/solver/test_domain.py tests/solver/test_solver.py tests/solver/test_smt.py -q
```

Expected:

```text
all tests passed
```

- [ ] **Step 3: Run the broader solver and symbolic regression slice**

Run:

```bash
pytest tests/solver tests/symbolic/test_constraint_generation.py tests/symbolic/test_speculate_enhancements.py -q
```

Expected:

```text
all tests passed
```

If any symbolic test depends on the old physical-table remapping behavior, update those call sites and assertions in the same commit rather than weakening the new contract.

- [ ] **Step 4: Run the repository standard unittest command as a final check**

Run:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

Expected:

```text
OK
```

If pre-existing unrelated failures appear, record them in the final implementation notes and do not mask them.

- [ ] **Step 5: Commit the docs and final verification pass**

```bash
git add docs/solver.md src/parseval/solver/__init__.py src/parseval/solver/unified.py
git commit -m "document sound domain solver semantics"
```

## Self-Review

### Spec Coverage

- Tri-state `DomainSolver`: Task 1
- Sound `AND` / `OR` / `NOT` / unsupported handling: Task 2
- Boolean exhaustion and value-space contradiction detection: Task 3
- Alias-preserving assignment identity: Task 3
- Unified short-circuit and SMT fallback flow: Task 4
- Strict SMT non-subset behavior: Task 4
- Documentation and regression coverage: Task 5

No spec section is left without a task.

### Placeholder Scan

- No `TODO`, `TBD`, or “implement later” markers
- All tasks include exact files, commands, and code snippets
- All verification steps specify expected pass/fail behavior

### Type Consistency

- `DomainResult.status` is used consistently as `sat | unsat | unknown`
- `assignments` is `None` for non-`sat` results throughout the plan
- `SolveResult` remains the unified public return type

