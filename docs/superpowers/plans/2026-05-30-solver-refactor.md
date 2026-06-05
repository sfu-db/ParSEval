# Solver Module Refactor — Pure Expression Solver

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `src/parseval/solver/` into a pure constraint solver that takes typed sqlglot expressions and returns satisfying values, with no dependency on `Instance`.

**Architecture:** The solver has two backends — a fast CSP-lite domain solver for simple predicates, and a Z3 SMT solver for complex expressions. Both read column types from `exp.Column.type` annotations set by the caller. The solver module owns its input type (`SolverConstraint`) and output type (`SolveResult`).

**Tech Stack:** Python, sqlglot (`exp.Expression`), z3-solver, dataclasses

---

## File Structure

| File | Responsibility |
|------|---------------|
| `src/parseval/solver/__init__.py` | Public API: exports `Solver`, `SolveResult`, `SolverConstraint` |
| `src/parseval/solver/unified.py` | `SolverConstraint`, `SolveResult`, `Solver` (orchestrator) |
| `src/parseval/solver/domain.py` | `DomainSolver` — CSP-lite with `ValueSpace` narrowing (NEW, replaces `value_space.py` + `lowering.py`) |
| `src/parseval/solver/smt.py` | `SMTSolver` — Z3 backend (MODIFY: remove `instance` param) |
| `src/parseval/solver/smt_types.py` | Z3 type system: `SMTTypeInfo`, `OptionTypeRegistry` (KEEP, internal) |
| `src/parseval/solver/smt_translate.py` | Z3 translation helpers (KEEP, internal) |
| `src/parseval/solver/types.py` | Shared types: `ValueSpace`, `CSPVariable`, `CSPConstraint`, `ColumnPredicate` (NEW) |
| `tests/solver/test_solver_constraint.py` | Tests for `SolverConstraint` construction |
| `tests/solver/test_domain.py` | Tests for `DomainSolver` |
| `tests/solver/test_smt.py` | Tests for `SMTSolver` |
| `tests/solver/test_solver.py` | Tests for unified `Solver` |

---

### Task 1: Define SolverConstraint and SolveResult

**Files:**
- Create: `src/parseval/solver/unified.py`
- Create: `tests/solver/test_solver_constraint.py`

- [ ] **Step 1: Write tests for SolverConstraint**

```python
# tests/solver/test_solver_constraint.py
from sqlglot import exp
from parseval.solver.unified import SolverConstraint, SolveResult


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = exp.DataType.build(dtype)
    return node


def test_solver_constraint_defaults():
    c = SolverConstraint(target_tables=("t1",))
    assert c.constraints == []
    assert c.join_equalities == []
    assert c.alias_map == {}
    assert c.atom is None


def test_solver_constraint_with_expressions():
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    c = SolverConstraint(
        target_tables=("t1",),
        constraints=[expr],
    )
    assert len(c.constraints) == 1


def test_solve_result_sat():
    r = SolveResult(sat=True, assignments={"t1": {"age": 20}})
    assert r.sat
    assert r.assignments["t1"]["age"] == 20


def test_solve_result_unsat():
    r = SolveResult(sat=False, reason="no solution")
    assert not r.sat
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/solver/test_solver_constraint.py -v
```

Expected: FAIL — module `parseval.solver.unified` does not exist.

- [ ] **Step 3: Implement SolverConstraint and SolveResult**

```python
# src/parseval/solver/unified.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from sqlglot import exp


@dataclass
class SolverConstraint:
    """Constraints for the solver to satisfy.

    Every ``exp.Column`` node inside *constraints* must have its ``.type``
    attribute set to a valid ``exp.DataType``.  The solver reads types from
    these annotations — it does not consult any external schema.
    """
    target_tables: Tuple[str, ...]
    constraints: List[exp.Expression] = field(default_factory=list)
    join_equalities: List[Tuple[str, str, str, str]] = field(default_factory=list)
    alias_map: Dict[str, str] = field(default_factory=dict)
    atom: Optional[exp.Expression] = None


@dataclass
class SolveResult:
    """Outcome of a solver invocation."""
    sat: bool
    assignments: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reason: str = ""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/solver/test_solver_constraint.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/unified.py tests/solver/test_solver_constraint.py
git commit -m "feat(solver): define SolverConstraint and SolveResult types"
```

---

### Task 2: Define shared types (ValueSpace, CSPVariable, ColumnPredicate)

**Files:**
- Create: `src/parseval/solver/types.py`
- Create: `tests/solver/test_types.py`

- [ ] **Step 1: Write tests for shared types**

```python
# tests/solver/test_types.py
from parseval.solver.types import (
    ValueSpace, CSPVariable, CSPConstraint, ColumnPredicate, TypeFamily,
)


def test_value_space_initial():
    vs = ValueSpace(family=TypeFamily.INTEGER)
    assert not vs.is_empty()
    assert vs.pick() is not None


def test_value_space_narrow_eq():
    vs = ValueSpace(family=TypeFamily.INTEGER)
    vs.narrow_eq(42)
    assert vs.pick() == 42


def test_value_space_narrow_range():
    vs = ValueSpace(family=TypeFamily.INTEGER)
    vs.narrow_min(10)
    vs.narrow_max(20)
    val = vs.pick()
    assert 10 <= val <= 20


def test_value_space_empty():
    vs = ValueSpace(family=TypeFamily.INTEGER)
    vs.must_null = True
    vs.not_null = True
    assert vs.is_empty()


def test_column_predicate():
    cp = ColumnPredicate(table="t1", column="age", op=">", value=18)
    assert cp.table == "t1"
    assert cp.op == ">"


def test_csp_variable():
    vs = ValueSpace(family=TypeFamily.TEXT)
    v = CSPVariable(name="t1.name", table="t1", column="name", space=vs)
    assert v.name == "t1.name"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/solver/test_types.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement shared types**

```python
# src/parseval/solver/types.py
"""Shared types for the solver module: ValueSpace, CSP structures, ColumnPredicate."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


class TypeFamily(Enum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    TEXT = "text"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    TIME = "time"


@dataclass
class ValueSpace:
    """The narrowed space of valid values for a variable."""
    family: TypeFamily = TypeFamily.TEXT
    min_val: Optional[Any] = None
    max_val: Optional[Any] = None
    equals: Optional[Any] = None
    not_equals: Set[Any] = field(default_factory=set)
    allowed: Optional[Set[Any]] = None
    must_null: bool = False
    not_null: bool = False
    like_pattern: Optional[str] = None
    max_length: Optional[int] = None

    def is_empty(self) -> bool:
        if self.must_null and self.not_null:
            return True
        if self.must_null:
            return False
        if self.equals is not None:
            if self.equals in self.not_equals:
                return True
            if self.min_val is not None and self.equals < self.min_val:
                return True
            if self.max_val is not None and self.equals > self.max_val:
                return True
            if self.allowed is not None and self.equals not in self.allowed:
                return True
            return False
        if self.min_val is not None and self.max_val is not None:
            if self.min_val > self.max_val:
                return True
        if self.allowed is not None:
            valid = self.allowed - self.not_equals
            if not valid:
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
        if self.family in (TypeFamily.INTEGER, TypeFamily.DECIMAL):
            return self._pick_numeric()
        elif self.family == TypeFamily.TEXT:
            return self._pick_text()
        elif self.family in (TypeFamily.DATE, TypeFamily.DATETIME):
            return self._pick_temporal()
        elif self.family == TypeFamily.BOOLEAN:
            return True if True not in self.not_equals else False
        return "value"

    def _pick_numeric(self) -> Any:
        lo = self.min_val if self.min_val is not None else 1
        hi = self.max_val if self.max_val is not None else lo + 100
        if lo > hi:
            return None
        mid = (lo + hi) // 2 if isinstance(lo, int) else (lo + hi) / 2
        if isinstance(lo, int):
            for offset in range(hi - lo + 1):
                for try_val in (mid + offset, mid - offset):
                    if lo <= try_val <= hi and try_val not in self.not_equals:
                        return try_val
        else:
            for try_val in (mid, lo, hi):
                if try_val not in self.not_equals:
                    return try_val
        return None

    def _pick_text(self) -> str:
        if self.like_pattern:
            return self.like_pattern.replace("%", "x").replace("_", "a")
        length = min(self.max_length or 10, 10)
        base = "value"[:length]
        i = 1
        while base in self.not_equals:
            base = f"val_{i}"[:length]
            i += 1
        return base

    def _pick_temporal(self) -> Any:
        if self.min_val and isinstance(self.min_val, (date, datetime)):
            return self.min_val
        return date(2024, 6, 15)

    def narrow_min(self, val: Any) -> None:
        if self.min_val is None or val > self.min_val:
            self.min_val = val

    def narrow_max(self, val: Any) -> None:
        if self.max_val is None or val < self.max_val:
            self.max_val = val

    def narrow_eq(self, val: Any) -> None:
        self.equals = val

    def narrow_neq(self, val: Any) -> None:
        self.not_equals.add(val)

    def narrow_in(self, values: Set[Any]) -> None:
        if self.allowed is None:
            self.allowed = values
        else:
            self.allowed &= values


@dataclass
class CSPVariable:
    """A column variable in the CSP solver."""
    name: str
    table: str
    column: str
    space: ValueSpace
    assigned: Optional[Any] = None


@dataclass
class CSPConstraint:
    """A relationship between two CSP variables."""
    kind: str
    left: str
    right: str


@dataclass
class ColumnPredicate:
    """A lowered constraint on a single column."""
    table: str
    column: str
    op: str
    value: Any
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/solver/test_types.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/types.py tests/solver/test_types.py
git commit -m "feat(solver): define shared types — ValueSpace, CSPVariable, ColumnPredicate"
```

---

### Task 3: Implement DomainSolver — CSP-lite with ValueSpace narrowing

**Files:**
- Create: `src/parseval/solver/domain.py`
- Create: `tests/solver/test_domain.py`

- [ ] **Step 1: Write tests for DomainSolver**

```python
# tests/solver/test_domain.py
from sqlglot import exp

from parseval.solver.domain import DomainSolver
from parseval.solver.types import ColumnPredicate


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = exp.DataType.build(dtype)
    return node


def test_simple_equality():
    expr = exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25))
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1",),
        expressions=[expr],
    )
    assert result is not None
    assert result["t1"]["age"] == 25


def test_greater_than():
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1",),
        expressions=[expr],
    )
    assert result is not None
    assert result["t1"]["age"] > 18


def test_conjunction():
    expr1 = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(10))
    expr2 = exp.LT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(20))
    expr = exp.And(this=expr1, expression=expr2)
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1",),
        expressions=[expr],
    )
    assert result is not None
    assert 10 < result["t1"]["age"] < 20


def test_is_null():
    expr = exp.Is(this=_col("t1", "name", "TEXT"), expression=exp.Null())
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1",),
        expressions=[expr],
    )
    assert result is not None
    assert result["t1"]["name"] is None


def test_join_equality():
    expr = exp.GT(this=_col("t1", "id", "INT"), expression=exp.Literal.number(0))
    solver = DomainSolver()
    result = solver.solve(
        target_tables=("t1", "t2"),
        expressions=[expr],
        join_equalities=[("t1", "id", "t2", "t1_id")],
    )
    assert result is not None
    assert result["t1"]["id"] == result["t2"]["t1_id"]


def test_empty_constraints():
    solver = DomainSolver()
    result = solver.solve(target_tables=("t1",), expressions=[])
    assert result is not None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py -v
```

Expected: FAIL

- [ ] **Step 3: Implement DomainSolver**

```python
# src/parseval/solver/domain.py
"""CSP-lite constraint solver using value-space narrowing."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.dtype import DataType
from parseval.helper import normalize_name

from .types import (
    CSPConstraint,
    CSPVariable,
    ColumnPredicate,
    TypeFamily,
    ValueSpace,
)


def _col_type(col: exp.Column) -> Optional[DataType]:
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


def _type_family(dtype: DataType) -> TypeFamily:
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


def _lower_expression(
    expr: exp.Expression,
    tables: Tuple[str, ...],
    alias_map: Dict[str, str],
) -> List[ColumnPredicate]:
    """Lower a sqlglot expression into simple column predicates."""
    preds: List[ColumnPredicate] = []
    _lower_recursive(expr, tables, alias_map, preds)
    return preds


def _lower_recursive(
    expr: exp.Expression,
    tables: Tuple[str, ...],
    alias_map: Dict[str, str],
    out: List[ColumnPredicate],
) -> None:
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
    pred = _lower_atom(expr, tables, alias_map)
    if pred:
        out.append(pred)


def _lower_atom(
    atom: exp.Expression,
    tables: Tuple[str, ...],
    alias_map: Dict[str, str],
) -> Optional[ColumnPredicate]:
    col, val, op = None, None, None
    if isinstance(atom, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LITE)):
        col, val = _extract_col_literal(atom)
        op = {
            "eq": "=", "neq": "!=", "gt": ">",
            "gte": ">=", "lt": "<", "lte": "<=",
        }.get(atom.key, None)
    elif isinstance(atom, exp.Is):
        right = atom.expression
        if isinstance(atom.this, exp.Column) and isinstance(right, exp.Null):
            col = atom.this
            val = True
            op = "is_null"
    elif isinstance(atom, exp.Like):
        if isinstance(atom.this, exp.Column) and isinstance(atom.expression, exp.Literal):
            col = atom.this
            val = str(atom.expression.this)
            op = "like"

    if col is not None and val is not None and op is not None:
        table = _resolve_table(col, tables, alias_map)
        return ColumnPredicate(table=table, column=col.name, op=op, value=val)
    return None


def _extract_col_literal(node: exp.Expression):
    left, right = node.this, node.expression
    if isinstance(left, exp.Column) and isinstance(right, (exp.Literal, exp.Boolean)):
        return left, _literal_value(right)
    if isinstance(right, exp.Column) and isinstance(left, (exp.Literal, exp.Boolean)):
        return right, _literal_value(left)
    return None, None


def _literal_value(node: exp.Expression):
    if isinstance(node, exp.Literal):
        if node.is_int:
            return int(node.this)
        if node.is_number:
            return float(node.this)
        return str(node.this)
    if isinstance(node, exp.Boolean):
        return bool(node.this)
    return None


def _resolve_table(col: exp.Column, tables: Tuple[str, ...], alias_map: Dict[str, str]) -> str:
    if col.table:
        name = normalize_name(col.table)
        name = alias_map.get(name, name)
        for t in tables:
            if normalize_name(t) == name:
                return t
    return tables[0] if tables else ""


class DomainSolver:
    """CSP-lite solver using value-space narrowing."""

    def solve(
        self,
        target_tables: Tuple[str, ...],
        expressions: List[exp.Expression],
        join_equalities: List[Tuple[str, str, str, str]] = None,
        alias_map: Dict[str, str] = None,
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        """Solve constraints and return assignments per table."""
        join_equalities = join_equalities or []
        alias_map = alias_map or {}

        # 1. Extract variables from expressions
        variables = self._extract_variables(target_tables, expressions, alias_map)

        # 2. Lower expressions to predicates
        all_preds: List[ColumnPredicate] = []
        for expr in expressions:
            all_preds.extend(_lower_expression(expr, target_tables, alias_map))

        # 3. Apply predicates to variables
        self._apply_predicates(variables, all_preds)

        # 4. Build equivalences from join equalities
        constraints = self._build_equivalences(variables, join_equalities, alias_map)

        # 5. Propagate
        if not self._propagate(variables, constraints):
            return None

        # 6. Assign
        return self._assign(variables)

    def _extract_variables(
        self,
        tables: Tuple[str, ...],
        expressions: List[exp.Expression],
        alias_map: Dict[str, str],
    ) -> Dict[str, CSPVariable]:
        variables: Dict[str, CSPVariable] = {}
        for expr in expressions:
            for col in expr.find_all(exp.Column):
                table = _resolve_table(col, tables, alias_map)
                name = f"{table}.{col.name}"
                if name not in variables:
                    dtype = _col_type(col)
                    family = _type_family(dtype) if dtype else TypeFamily.TEXT
                    space = ValueSpace(family=family)
                    variables[name] = CSPVariable(
                        name=name, table=table, column=col.name, space=space,
                    )
        return variables

    def _apply_predicates(
        self,
        variables: Dict[str, CSPVariable],
        predicates: List[ColumnPredicate],
    ) -> None:
        for pred in predicates:
            name = f"{pred.table}.{pred.column}"
            if name not in variables:
                space = ValueSpace()
                variables[name] = CSPVariable(
                    name=name, table=pred.table, column=pred.column, space=space,
                )
            space = variables[name].space
            op, val = pred.op, pred.value
            if op == "=":
                space.narrow_eq(val)
            elif op == ">" and isinstance(val, (int, float)):
                space.narrow_min(val + 1 if isinstance(val, int) else val + 0.01)
            elif op == ">=" and isinstance(val, (int, float)):
                space.narrow_min(val)
            elif op == "<" and isinstance(val, (int, float)):
                space.narrow_max(val - 1 if isinstance(val, int) else val - 0.01)
            elif op == "<=" and isinstance(val, (int, float)):
                space.narrow_max(val)
            elif op == "!=":
                space.narrow_neq(val)
            elif op == "like":
                space.like_pattern = val
            elif op == "is_null":
                space.must_null = True

    def _build_equivalences(
        self,
        variables: Dict[str, CSPVariable],
        join_equalities: List[Tuple[str, str, str, str]],
        alias_map: Dict[str, str],
    ) -> List[CSPConstraint]:
        constraints: List[CSPConstraint] = []
        for lt, lc, rt, rc in join_equalities:
            lt_real = normalize_name(lt)
            rt_real = normalize_name(rt)
            lt_real = alias_map.get(lt_real, lt_real)
            rt_real = alias_map.get(rt_real, rt_real)
            left_key = f"{lt_real}.{lc}"
            right_key = f"{rt_real}.{rc}"
            if left_key in variables and right_key in variables:
                constraints.append(CSPConstraint(kind="eq", left=left_key, right=right_key))
        return constraints

    def _propagate(
        self,
        variables: Dict[str, CSPVariable],
        constraints: List[CSPConstraint],
    ) -> bool:
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
                        if left.space.equals is not None and right.space.equals is None:
                            right.space.narrow_eq(left.space.equals)
                            changed = True
                        elif right.space.equals is not None and left.space.equals is None:
                            left.space.narrow_eq(right.space.equals)
                            changed = True
            for var in variables.values():
                if var.space.is_empty():
                    return False
        return True

    def _assign(
        self, variables: Dict[str, CSPVariable],
    ) -> Optional[Dict[str, Dict[str, Any]]]:
        result: Dict[str, Dict[str, Any]] = {}
        for var in variables.values():
            val = var.space.pick()
            var.assigned = val
            result.setdefault(var.table, {})[var.column] = val
        return result if result else None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/solver/test_domain.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/domain.py tests/solver/test_domain.py
git commit -m "feat(solver): implement DomainSolver — CSP-lite with ValueSpace narrowing"
```

---

### Task 4: Modify SMTSolver — remove Instance dependency

**Files:**
- Modify: `src/parseval/solver/smt.py` (existing `smt_solver.py`, rename)
- Keep: `src/parseval/solver/smt_types.py` (unchanged, internal)
- Keep: `src/parseval/solver/smt_translate.py` (unchanged, internal)
- Create: `tests/solver/test_smt.py`

- [ ] **Step 1: Write tests for SMTSolver**

```python
# tests/solver/test_smt.py
from sqlglot import exp
from sqlglot.expressions import DataType

from parseval.solver.smt import SMTSolver


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = DataType.build(dtype)
    return node


def test_integer_gt():
    solver = SMTSolver()
    expr = exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18))
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.age"] > 18


def test_text_equality():
    solver = SMTSolver()
    expr = exp.EQ(
        this=_col("t1", "name", "TEXT"),
        expression=exp.Literal.string("Alice"),
    )
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.name"] == "Alice"


def test_conjunction():
    solver = SMTSolver()
    gt = exp.GT(this=_col("t1", "x", "INT"), expression=exp.Literal.number(0))
    lt = exp.LT(this=_col("t1", "x", "INT"), expression=exp.Literal.number(100))
    expr = exp.And(this=gt, expression=lt)
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "sat"
    assert 0 < model["t1.x"] < 100


def test_unsat():
    solver = SMTSolver()
    gt = exp.GT(this=_col("t1", "x", "INT"), expression=exp.Literal.number(100))
    lt = exp.LT(this=_col("t1", "x", "INT"), expression=exp.Literal.number(0))
    expr = exp.And(this=gt, expression=lt)
    z3_expr = solver._to_z3_expr(expr)
    solver.add(z3_expr)
    status, model = solver.solve()
    assert status == "unsat"


def test_declare_variable_and_solve():
    solver = SMTSolver()
    solver.declare_variable("t1.id", DataType.build("INT"))
    z3_var = solver.context["variable_to_z3"]["t1.id"]
    solver.add_raw(z3_var == solver._to_z3_expr(exp.Literal.number(42)))
    status, model = solver.solve()
    assert status == "sat"
    assert model["t1.id"] == 42
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/solver/test_smt.py -v
```

Expected: FAIL — `SMTSolver` still requires `instance` parameter.

- [ ] **Step 3: Rename smt_solver.py → smt.py and remove instance**

```bash
cp src/parseval/solver/smt_solver.py src/parseval/solver/smt.py
```

Then make these specific changes to `src/parseval/solver/smt.py`:

1. **Remove `instance` from `__init__`** (line ~94-104):
```python
# BEFORE:
def __init__(self, variables, z3ctx=None, verbose=False,
             function_models=None, timeout_ms=None, instance=None):
    ...
    self.instance = instance

# AFTER:
def __init__(self, variables=None, z3ctx=None, verbose=False,
             function_models=None, timeout_ms=None):
    ...
    self.instance = None
```

2. **Remove instance fallback from `_infer_type_info`** (line ~313-325):
```python
# BEFORE:
def _infer_type_info(self, col):
    dtype = getattr(col, "type", None)
    if dtype is None or str(dtype) in ("", "UNKNOWN"):
        if self.instance is not None:
            ...
    return normalize_dtype(dtype, self.z3ctx)

# AFTER:
def _infer_type_info(self, col):
    dtype = getattr(col, "type", None)
    if dtype is None or str(dtype) in ("", "UNKNOWN"):
        dtype = DataType.build("TEXT")
    return normalize_dtype(dtype, self.z3ctx)
```

3. **Remove `col_sort_datatype` method** (line ~208-216) — no longer needed.

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/solver/test_smt.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/smt.py tests/solver/test_smt.py
git commit -m "feat(solver): remove Instance from SMTSolver — reads types from Column.type only"
```

---

### Task 5: Implement unified Solver — orchestrator

**Files:**
- Modify: `src/parseval/solver/unified.py`
- Create: `tests/solver/test_solver.py`

- [ ] **Step 1: Write tests for unified Solver**

```python
# tests/solver/test_solver.py
from sqlglot import exp
from sqlglot.expressions import DataType

from parseval.solver.unified import Solver, SolverConstraint, SolveResult


def _col(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = DataType.build(dtype)
    return node


def test_solve_simple_equality():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.EQ(this=_col("t1", "age", "INT"), expression=exp.Literal.number(25)),
        ],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["age"] == 25


def test_solve_gt():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.GT(this=_col("t1", "age", "INT"), expression=exp.Literal.number(18)),
        ],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["age"] > 18


def test_solve_empty():
    solver = Solver()
    constraint = SolverConstraint(target_tables=("t1",))
    result = solver.solve(constraint)
    assert result.sat


def test_solve_join_equality():
    solver = Solver()
    constraint = SolverConstraint(
        target_tables=("t1", "t2"),
        constraints=[
            exp.GT(this=_col("t1", "id", "INT"), expression=exp.Literal.number(0)),
        ],
        join_equalities=[("t1", "id", "t2", "t1_id")],
    )
    result = solver.solve(constraint)
    assert result.sat
    assert result.assignments["t1"]["id"] == result.assignments["t2"]["t1_id"]


def test_solve_complex_expression():
    """Test that complex expressions fall through to SMT."""
    solver = Solver()
    # a + b > 10 — arithmetic, needs SMT
    add = exp.Add(
        this=_col("t1", "a", "INT"),
        expression=_col("t1", "b", "INT"),
    )
    constraint = SolverConstraint(
        target_tables=("t1",),
        constraints=[
            exp.GT(this=add, expression=exp.Literal.number(10)),
        ],
    )
    result = solver.solve(constraint)
    assert result.sat
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/solver/test_solver.py -v
```

Expected: FAIL — `Solver` class not yet complete.

- [ ] **Step 3: Implement Solver**

Add the `Solver` class to `src/parseval/solver/unified.py`:

```python
class Solver:
    """Unified constraint solver with tiered resolution."""

    def __init__(self, dialect: str = "sqlite", *, timeout_ms: int = 5000, seed: int = 42):
        self.dialect = dialect
        self.timeout_ms = timeout_ms
        self._rng = random.Random(seed)

    def solve(self, constraint: SolverConstraint) -> SolveResult:
        if not constraint.constraints and not constraint.join_equalities:
            return SolveResult(sat=True, assignments={})

        # Tier 1: Domain solver
        from .domain import DomainSolver
        ds = DomainSolver()
        domain_result = ds.solve(
            target_tables=constraint.target_tables,
            expressions=constraint.constraints,
            join_equalities=constraint.join_equalities,
            alias_map=constraint.alias_map,
        )

        # Check if all predicates were simple (no residuals)
        residuals = self._find_residuals(constraint)
        if domain_result is not None and not residuals:
            return SolveResult(sat=True, assignments=domain_result)

        # Tier 2: SMT solver
        smt_result = self._try_smt(constraint, residuals)
        if smt_result is not None:
            return self._postprocess(smt_result, constraint)

        # Fallback
        if domain_result is not None:
            return SolveResult(sat=True, assignments=domain_result)

        return SolveResult(sat=False, reason="all tiers exhausted")

    def _find_residuals(self, constraint: SolverConstraint):
        """Find expressions that the domain solver can't handle."""
        from .domain import _lower_expression
        residuals = []
        for expr in constraint.constraints:
            preds = _lower_expression(expr, constraint.target_tables, constraint.alias_map or {})
            # If we couldn't fully lower, it's a residual
            # Simple heuristic: if no preds extracted, it's a residual
            if not preds and not isinstance(expr, (exp.EQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.NEQ, exp.Is, exp.Like)):
                residuals.append(expr)
        return residuals

    def _try_smt(self, constraint, residuals):
        """Solve with Z3."""
        from .smt import SMTSolver, UnsupportedSMTError
        try:
            smt = SMTSolver(timeout_ms=self.timeout_ms)
            # Declare variables
            for expr in constraint.constraints:
                for col in expr.find_all(exp.Column):
                    key = f"{col.table or ''}.{col.name}"
                    dtype = getattr(col, "type", None) or DataType.build("TEXT")
                    smt.declare_variable(key, dtype)
            # Add constraints
            for expr in constraint.constraints:
                try:
                    z3_expr = smt.translate(expr)
                    if z3_expr is not None:
                        smt.add(z3_expr)
                except (UnsupportedSMTError, Exception):
                    pass
            # Add join equalities
            for lt, lc, rt, rc in constraint.join_equalities:
                left_key = f"{lt}.{lc}"
                right_key = f"{rt}.{rc}"
                left_z3 = smt.context.get("variable_to_z3", {}).get(left_key)
                right_z3 = smt.context.get("variable_to_z3", {}).get(right_key)
                if left_z3 is not None and right_z3 is not None:
                    smt.add_raw(left_z3 == right_z3)
            status, solutions = smt.solve()
            if status != "sat":
                return None
            assignments = {}
            for var_name, value in solutions.items():
                parts = var_name.split(".")
                if len(parts) == 2:
                    assignments.setdefault(parts[0], {})[parts[1]] = value
            return assignments or None
        except Exception:
            return None

    def _postprocess(self, raw, constraint):
        """Apply join equalities."""
        result = {k: dict(v) for k, v in raw.items()}
        for lt, lc, rt, rc in constraint.join_equalities:
            if lt in result and lc in result[lt]:
                result.setdefault(rt, {})[rc] = result[lt][lc]
            elif rt in result and rc in result[rt]:
                result.setdefault(lt, {})[lc] = result[rt][rc]
        return SolveResult(sat=True, assignments=result)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/solver/test_solver.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/solver/unified.py tests/solver/test_solver.py
git commit -m "feat(solver): implement unified Solver — domain + SMT orchestrator"
```

---

### Task 6: Update __init__.py

**Files:**
- Modify: `src/parseval/solver/__init__.py`

- [ ] **Step 1: Update exports**

```python
# src/parseval/solver/__init__.py
"""ParSEval constraint solver.

Public API::

    from parseval.solver import Solver, SolveResult, SolverConstraint

    solver = Solver(dialect="sqlite")
    result = solver.solve(constraint)
"""
from .unified import Solver, SolveResult, SolverConstraint

__all__ = ["Solver", "SolveResult", "SolverConstraint"]
```

- [ ] **Step 2: Verify imports work**

```bash
.venv/bin/python -c "from parseval.solver import Solver, SolveResult, SolverConstraint; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Run all solver tests**

```bash
.venv/bin/python -m pytest tests/solver/ -v
```

Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/parseval/solver/__init__.py
git commit -m "feat(solver): update __init__.py — export Solver, SolveResult, SolverConstraint"
```

---

### Task 7: Clean up old files

**Files:**
- Remove: `src/parseval/solver/value_space.py` (replaced by `domain.py`)
- Remove: `src/parseval/solver/smt_solver.py` (replaced by `smt.py`)
- Remove: `src/parseval/solver/lowering.py` (merged into `domain.py`)

- [ ] **Step 1: Remove old files**

```bash
rm src/parseval/solver/value_space.py
rm src/parseval/solver/smt_solver.py
rm src/parseval/solver/lowering.py
```

- [ ] **Step 2: Run all solver tests**

```bash
.venv/bin/python -m pytest tests/solver/ -v
```

Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add -A src/parseval/solver/
git commit -m "refactor(solver): remove old files — clean solver module"
```

---

## Final Module Structure

```
src/parseval/solver/
  __init__.py      # Public: Solver, SolveResult, SolverConstraint
  unified.py       # SolverConstraint, SolveResult, Solver (orchestrator)
  domain.py        # DomainSolver — CSP-lite with ValueSpace narrowing (NEW)
  smt.py           # SMTSolver — Z3 backend (MODIFIED from smt_solver.py)
  smt_types.py     # Z3 type system (KEEP, internal)
  smt_translate.py # Z3 translation helpers (KEEP, internal)
  types.py         # ValueSpace, CSPVariable, CSPConstraint, ColumnPredicate (NEW)

tests/solver/
  test_solver_constraint.py
  test_types.py
  test_domain.py
  test_smt.py
  test_solver.py
```
