# Unify Z3 Solver Usage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate `UExprToConstraint` by exposing a public API on `SMTSolver` that `engine.py` uses directly for self-join repair and NOT IN handling.

**Architecture:** Add `declare_variable`, `translate`, `add_raw`, `solve_raw`, and `apply_solution` to `SMTSolver`. Rewrite `_smt_repair_where` self-join path and `_repair_not_in_simple` in `engine.py` to use these methods. Delete `uexpr.py`.

**Tech Stack:** Python 3.10+, Z3 (z3-solver), sqlglot

---

## File Structure

| File | Responsibility | Change |
|------|---------------|--------|
| `src/parseval/solver/smt.py` | Z3 solver with Option-type NULL encoding | Add public API methods |
| `src/parseval/symbolic/engine.py` | Symbolic test generation engine | Rewrite self-join path, extract `_get_inner_query_values` |
| `src/parseval/symbolic/uexpr.py` | Legacy Z3 wrapper (raw sorts) | **Delete** |
| `src/parseval/logger.py` | Logger configuration | Remove `"uexpr"` entry |
| `tests/test_solver.py` | Solver tests | Add tests for new public API |

---

## Existing Types Reference

These types already exist in `src/parseval/solver/smt.py`:

```python
@dataclass(frozen=True)
class SMTTypeInfo:
    dtype: DataType
    logical_name: str       # "INT", "FLOAT", "TEXT", "BOOLEAN", "DATE", etc.
    family: str             # "int", "real", "text", "bool", "date", "time", etc.
    payload_sort: z3.SortRef  # z3.IntSort, z3.RealSort, z3.StringSort, z3.BoolSort
    logical_tag: z3.ExprRef

@dataclass(frozen=True)
class SMTValue:
    expr: Optional[z3.ExprRef]
    typeinfo: SMTTypeInfo
    is_null_literal: bool = False

class UnsupportedSMTError(NotImplementedError): ...
```

Key existing functions:
- `normalize_dtype(dtype: DataType, z3ctx=None, value=None) -> SMTTypeInfo` (line 344)
- `OptionTypeRegistry.get(base_sort: z3.SortRef, z3ctx=None) -> z3.DatatypeSortRef` (line 430)
- `encode_literal(dtype: DataType, value: Any, z3ctx=None) -> SMTValue` (line 503)
- `declare_column(variable: exp.Column, z3ctx=None) -> SMTValue` (line 514)

---

### Task 1: Add `declare_variable` and `col_sort_datatype` to SMTSolver

**Files:**
- Modify: `src/parseval/solver/smt.py:638-678` (SMTSolver.__init__)
- Modify: `src/parseval/solver/smt.py:617-636` (SMTSolver class, add methods)
- Test: `tests/test_solver.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_solver.py`:

```python
def test_declare_variable_creates_option_wrapped_z3_var():
    """declare_variable returns an Option-wrapped Z3 variable stored in context."""
    from sqlglot.expressions import DataType
    from parseval.solver.smt import SMTSolver

    solver = SMTSolver(variables=[], timeout_ms=1000)
    var = solver.declare_variable("t1[0].id", DataType.build("INT"))

    # Should be Option-wrapped (DatatypeSortRef)
    assert isinstance(var.sort(), z3.DatatypeSortRef)
    # Should be stored in context
    assert "t1[0].id" in solver.context.get("variable_to_z3", {})
    # Calling again returns the same object
    var2 = solver.declare_variable("t1[0].id", DataType.build("INT"))
    assert var is var2


def test_col_sort_datatype_resolves_from_instance():
    """col_sort_datatype resolves DataType from Instance schema."""
    from sqlglot.expressions import DataType
    from parseval.solver.smt import SMTSolver

    class MockTables:
        tables = {"users": {"id": "INTEGER", "name": "TEXT", "score": "REAL"}}

    solver = SMTSolver(variables=[], timeout_ms=1000, instance=MockTables())

    assert solver.col_sort_datatype("users", "id") == DataType.build("INTEGER")
    assert solver.col_sort_datatype("users", "name") == DataType.build("TEXT")
    assert solver.col_sort_datatype("users", "score") == DataType.build("REAL")
    # Unknown column defaults to TEXT
    assert solver.col_sort_datatype("users", "missing") == DataType.build("TEXT")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_solver.py::test_declare_variable_creates_option_wrapped_z3_var tests/test_solver.py::test_col_sort_datatype_resolves_from_instance -v`
Expected: FAIL with `AttributeError: 'SMTSolver' object has no attribute 'declare_variable'`

- [ ] **Step 3: Add `_VarRef` helper class and `instance` parameter**

In `src/parseval/solver/smt.py`, add this helper class before `SMTSolver` (e.g., after `SMTValue`):

```python
@dataclass
class _VarRef:
    """Lightweight stand-in for a sqlglot Column in z3_to_variable context.

    _z3_to_python expects context["z3_to_variable"][name] to have a .type
    attribute for temporal decoding. This wraps a DataType so declare_variable
    entries satisfy that contract without importing sqlglot Column.
    """
    type: DataType
```

Modify `__init__` (line 638):

```python
def __init__(
    self,
    variables,
    z3ctx: Optional[z3.Context] = None,
    verbose: bool = False,
    function_models: Optional[
        Union[Sequence[SpecialFunctionModel], Dict[str, SpecialFunctionModel]]
    ] = None,
    timeout_ms: Optional[int] = None,
    instance=None,
):
```

Add after line 666 (`self.timeout_ms = timeout_ms`):

```python
self.instance = instance
```

- [ ] **Step 4: Add `declare_variable` and `col_sort_datatype` methods**

Add to `SMTSolver` class after `_build_core_registry` (after line 727):

```python
def declare_variable(self, name: str, datatype: DataType) -> z3.ExprRef:
    """Declare an Option-wrapped Z3 variable with a custom name.

    Unlike _declare_or_get_column (which takes sqlglot Column objects),
    this accepts a string name and DataType directly. The variable is
    stored in the solver's context so translate() and solve() can find it.

    Returns the Option-wrapped Z3 expression.
    """
    if name in self.context.get("variable_to_z3", {}):
        return self.context["variable_to_z3"][name]
    type_info = normalize_dtype(datatype, self.z3ctx)
    option_type = OptionTypeRegistry.get(type_info.payload_sort, self.z3ctx)
    z3_var = z3.Const(name, option_type)
    self.context.setdefault("variable_to_z3", {})[name] = z3_var
    # Store _VarRef so _z3_to_python can access .type for temporal decoding
    self.context.setdefault("z3_to_variable", {})[name] = _VarRef(type=datatype)
    return z3_var

def col_sort_datatype(self, table: str, col: str) -> DataType:
    """Resolve a column's DataType from the Instance schema.

    Returns DataType.build("TEXT") for unknown columns.
    """
    if self.instance is None:
        return DataType.build("TEXT")
    col_type = str(self.instance.tables.get(table, {}).get(col, "TEXT"))
    return DataType.build(col_type)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_solver.py::test_declare_variable_creates_option_wrapped_z3_var tests/test_solver.py::test_col_sort_datatype_resolves_from_instance -v`
Expected: PASS

- [ ] **Step 6: Run full solver test suite**

Run: `pytest tests/test_solver.py -v`
Expected: All existing tests PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add src/parseval/solver/smt.py tests/test_solver.py
git commit -m "feat(solver): add declare_variable and col_sort_datatype to SMTSolver"
```

---

### Task 2: Add `translate` method with custom context to SMTSolver

**Files:**
- Modify: `src/parseval/solver/smt.py:1333-1370` (_to_z3_expr)
- Modify: `src/parseval/solver/smt.py` (add translate method)
- Test: `tests/test_solver.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_solver.py`:

```python
def test_translate_with_custom_context():
    """translate() uses caller-provided variable context for Column resolution."""
    from sqlglot import exp
    from sqlglot.expressions import DataType
    from parseval.solver.smt import SMTSolver

    solver = SMTSolver(variables=[], timeout_ms=1000)

    # Declare variables with custom names
    var_a = solver.declare_variable("alias1[0].x", DataType.build("INT"))
    var_b = solver.declare_variable("alias2[1].x", DataType.build("INT"))

    ctx = {"alias1.x": var_a, "alias2.x": var_b}

    # Build: alias1.x = alias2.x
    col_a = exp.column("x", "alias1")
    col_a.set("type", DataType.build("INT"))
    col_b = exp.column("x", "alias2")
    col_b.set("type", DataType.build("INT"))
    eq_expr = exp.EQ(this=col_a, expression=col_b)

    result = solver.translate(eq_expr, ctx=ctx)
    assert result is not None
    # Result should be a Z3 BoolRef
    assert z3.is_bool(result)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_solver.py::test_translate_with_custom_context -v`
Expected: FAIL with `AttributeError: 'SMTSolver' object has no attribute 'translate'`

- [ ] **Step 3: Add `translate` method and modify `_to_z3_expr`**

Add `translate` method to `SMTSolver` class (after `col_sort_datatype`):

```python
def translate(
    self, expr: exp.Expression, ctx: Optional[Dict[str, z3.ExprRef]] = None
) -> Optional[z3.BoolRef]:
    """Translate a sqlglot AST expression to Z3.

    If ctx is provided, Column nodes are resolved from ctx (keyed as
    "normalized_table.normalized_name") before the solver's default context.
    Returns a raw z3.BoolRef, or None on failure.
    """
    try:
        result = self._to_z3_expr(expr, ctx=ctx)
        if isinstance(result, SMTValue):
            return self._as_predicate(result)
        return result
    except Exception:
        return None
```

Modify `_to_z3_expr` (line 1333) to accept `ctx` parameter and check it for Column resolution:

```python
def _to_z3_expr(self, condition: exp.Expression, ctx: Optional[Dict[str, z3.ExprRef]] = None):
    """Recursively translate a sqlglot AST node into a Z3 expression.

    Handles: Paren, Column, Null, Boolean, Literal, Const, and any
    node matching a registered special function or core registry key.

    Args:
        condition: The sqlglot AST node to translate.
        ctx: Optional dict mapping "table.column" keys to pre-declared
             Z3 expressions. When provided, Column nodes are resolved
             from ctx first, then fall back to _declare_or_get_column.
    """
    if isinstance(condition, exp.Paren):
        return self._to_z3_expr(condition.this, ctx=ctx)
    if isinstance(condition, exp.Column):
        # Check caller's context first
        if ctx is not None:
            from parseval.helper import normalize_name
            col_key = (
                f"{normalize_name(condition.table)}.{normalize_name(condition.name)}"
                if condition.table
                else normalize_name(condition.name)
            )
            if col_key in ctx:
                raw = ctx[col_key]
                type_info = self._infer_type_info(condition)
                return SMTValue(raw, type_info)
        return self._declare_or_get_column(condition)
    if isinstance(condition, exp.Null):
        dtype = condition.args.get("_type") or DataType.build("NULL")
        return encode_literal(dtype, None, self.z3ctx)
    if isinstance(condition, exp.Boolean):
        return z3.BoolVal(bool(condition.this), ctx=self.z3ctx)
    if isinstance(condition, (exp.Literal, Const)):
        datatype = condition.datatype
        literal_value = condition.this
        if datatype.is_type(*DataType.TEMPORAL_TYPES) and isinstance(literal_value, str):
            return encode_literal(datatype, literal_value, self.z3ctx)
        if datatype.is_type(*DataType.TEXT_TYPES) and isinstance(literal_value, str):
            return encode_literal(datatype, literal_value, self.z3ctx)
        if datatype.is_type(DataType.Type.UNKNOWN) and isinstance(literal_value, str) and _is_temporal_string(literal_value):
            return encode_literal(_infer_temporal_dtype(literal_value), literal_value, self.z3ctx)
        return encode_literal(datatype, literal_value, self.z3ctx)

    function_result = self._resolve_special_function(condition)
    if function_result is not None:
        return function_result

    key = condition.key.upper()
    translator = self.core_registry.get(key)
    if translator is not None:
        return translator(condition)

    raise UnsupportedSMTError(
        f"{repr(condition)} not supported in SMT conversion, {type(condition)}"
    )
```

Add `_infer_type_info` helper method to `SMTSolver`:

```python
def _infer_type_info(self, col: exp.Column) -> SMTTypeInfo:
    """Infer SMTTypeInfo for a Column, using its .type attribute or Instance schema."""
    dtype = getattr(col, "type", None)
    if dtype is None or str(dtype) in ("", "UNKNOWN"):
        if self.instance is not None:
            from parseval.helper import normalize_name
            table = normalize_name(col.table or "")
            name = normalize_name(col.name)
            if table in self.instance.tables and name in self.instance.tables[table]:
                dtype = DataType.build(str(self.instance.tables[table][name]))
        if dtype is None or str(dtype) in ("", "UNKNOWN"):
            dtype = DataType.build("TEXT")
    return normalize_dtype(dtype, self.z3ctx)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_solver.py::test_translate_with_custom_context -v`
Expected: PASS

- [ ] **Step 5: Run full solver test suite**

Run: `pytest tests/test_solver.py -v`
Expected: All existing tests PASS (no regressions)

- [ ] **Step 6: Commit**

```bash
git add src/parseval/solver/smt.py tests/test_solver.py
git commit -m "feat(solver): add translate method with custom context to SMTSolver"
```

---

### Task 3: Add `add_raw`, `solve_raw`, and `apply_solution` to SMTSolver

**Files:**
- Modify: `src/parseval/solver/smt.py` (add methods to SMTSolver)
- Test: `tests/test_solver.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_solver.py`:

```python
def test_add_raw_and_solve_raw():
    """add_raw adds constraints directly; solve_raw extracts solutions."""
    from sqlglot.expressions import DataType
    from parseval.solver.smt import SMTSolver, encode_literal

    solver = SMTSolver(variables=[], timeout_ms=5000)

    var_a = solver.declare_variable("t[0].x", DataType.build("INT"))
    var_b = solver.declare_variable("t[0].y", DataType.build("INT"))

    # Add constraint: t[0].x = 42 (Option-wrapped)
    const_42 = encode_literal(DataType.build("INT"), 42).expr
    solver.add_raw(var_a == const_42)

    # Add constraint: t[0].y = t[0].x (Option equality)
    solver.add_raw(var_b == var_a)

    var_symbols = {"t[0].x": None, "t[0].y": None}
    status, solution = solver.solve_raw(var_symbols)

    assert status == "sat"
    assert solution.get("t[0].x") == 42
    assert solution.get("t[0].y") == 42


def test_apply_solution():
    """apply_solution writes values into Variable symbols."""
    from parseval.solver.smt import SMTSolver

    class FakeSymbol:
        def __init__(self):
            self.values = {}
        def set(self, key, val):
            self.values[key] = val

    sym_x = FakeSymbol()
    sym_y = FakeSymbol()
    var_symbols = {"t[0].x": sym_x, "t[0].y": sym_y}
    solution = {"t[0].x": 42, "t[0].y": 99}

    SMTSolver.apply_solution(var_symbols, solution)

    assert sym_x.values == {"concrete": 42, "is_bound": True, "is_null": False}
    assert sym_y.values == {"concrete": 99, "is_bound": True, "is_null": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_solver.py::test_add_raw_and_solve_raw tests/test_solver.py::test_apply_solution -v`
Expected: FAIL with `AttributeError: 'SMTSolver' object has no attribute 'add_raw'`

- [ ] **Step 3: Add `add_raw` method**

Add to `SMTSolver` class (after `translate`):

```python
def add_raw(self, constraint: z3.BoolRef) -> None:
    """Add a raw Z3 boolean expression directly to the solver.

    Unlike add(), this does not convert SMTValue or track variables
    via get_vars. Use for constraints built outside translate()
    (e.g., JOIN equalities between declared variables).
    """
    self.solver.add(constraint)
```

- [ ] **Step 4: Add `solve_raw` method**

Add to `SMTSolver` class (after `add_raw`):

```python
def solve_raw(
    self, var_symbols: Dict[str, Any]
) -> Tuple[str, Dict[str, Any]]:
    """Solve and extract values for variables declared via declare_variable.

    Args:
        var_symbols: Maps variable name (str) to Variable symbol.
            Only variables in this dict are extracted from the model.

    Returns:
        ("sat", {var_name: python_value}) or ("unsat", {})
    """
    if not self._domain_constraints_applied:
        for var_name, z3var in self.context.get("variable_to_z3", {}).items():
            typeinfo = self._infer_type_from_context(var_name)
            if typeinfo is not None:
                if typeinfo.family in {"date", "time", "datetime", "timestamp"}:
                    self._ensure_temporal_bounds(z3var, typeinfo)
                if typeinfo.family == "text":
                    self._ensure_str_printable(z3var)
                    self._ensure_str_length(z3var, 0)
        self._domain_constraints_applied = True

    status = self.solver.check()
    if status != z3.sat:
        return ("unsat", {})

    model = self.solver.model()
    solution = {}
    for var_name in var_symbols:
        z3_var = self.context.get("variable_to_z3", {}).get(var_name)
        if z3_var is None:
            continue
        z3_val = model.evaluate(z3_var, model_completion=True)
        python_val = self._z3_to_python(z3_val, var_name)
        if python_val is not None:
            solution[var_name] = python_val
    return ("sat", solution)

def _infer_type_from_context(self, var_name: str) -> Optional[SMTTypeInfo]:
    """Infer SMTTypeInfo for a declared variable from its _VarRef in context."""
    ref = self.context.get("z3_to_variable", {}).get(var_name)
    if ref is not None and hasattr(ref, "type"):
        return normalize_dtype(ref.type, self.z3ctx)
    return None
```

- [ ] **Step 5: Add `apply_solution` static method**

Add to `SMTSolver` class (after `solve_raw`):

```python
@staticmethod
def apply_solution(
    var_symbols: Dict[str, Any], solution: Dict[str, Any]
) -> None:
    """Write solution values back into Variable symbols.

    Args:
        var_symbols: Maps variable name → Variable symbol (has .set method).
        solution: Maps variable name → Python value (from solve_raw).
    """
    for var_name, value in solution.items():
        sym = var_symbols.get(var_name)
        if sym is not None and value is not None:
            sym.set("concrete", value)
            sym.set("is_bound", True)
            sym.set("is_null", False)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_solver.py::test_add_raw_and_solve_raw tests/test_solver.py::test_apply_solution -v`
Expected: PASS

- [ ] **Step 7: Run full solver test suite**

Run: `pytest tests/test_solver.py -v`
Expected: All existing tests PASS (no regressions)

- [ ] **Step 8: Commit**

```bash
git add src/parseval/solver/smt.py tests/test_solver.py
git commit -m "feat(solver): add add_raw, solve_raw, and apply_solution to SMTSolver"
```

---

### Task 4: Rewrite `_smt_repair_where` self-join path

**Files:**
- Modify: `src/parseval/symbolic/engine.py:471-560` (self-join block in _smt_repair_where)
- Modify: `src/parseval/symbolic/engine.py:796-850` (_repair_not_in_simple)
- Test: `tests/symbolic/test_engine.py` (existing tests)

- [ ] **Step 1: Read the current self-join path code**

Read `src/parseval/symbolic/engine.py:471-560` to understand the exact code to replace.

- [ ] **Step 2: Add `_get_inner_query_values` as module-level function**

Add this function to `src/parseval/symbolic/engine.py` (before the `SymbolicEngine` class definition, or at module level):

```python
def _get_inner_query_values(
    in_node: exp.In,
    instance,
    alias_map,
) -> List[Any]:
    """Evaluate the inner query of an IN expression against current instance.

    Pure query execution — no Z3 involved. Returns list of concrete values
    from the inner subquery's projected column.
    """
    from parseval.plan.rex import concrete, Environment
    from parseval.helper import normalize_name

    subq = in_node.find(exp.Subquery)
    if not subq:
        return []
    inner_select = subq.this
    if not isinstance(inner_select, exp.Select):
        return []
    from_clause = inner_select.args.get("from")
    if not from_clause:
        return []
    from_table = from_clause.this
    if not isinstance(from_table, exp.Table):
        return []
    table_name = normalize_name(from_table.alias_or_name)
    table_name = alias_map.get(table_name, table_name)
    if table_name not in instance.tables:
        return []
    projections = inner_select.expressions
    if not projections:
        return []
    proj_col = None
    for col in projections[0].find_all(exp.Column):
        proj_col = normalize_name(col.name)
        break
    if not proj_col:
        return []
    rows = instance.get_rows(table_name)
    where = inner_select.args.get("where")
    values = []
    for row in rows:
        if where:
            env = Environment({c: s.concrete for c, s in row.items()})
            if concrete(where.this, env) is not True:
                continue
        if proj_col in row.columns:
            v = row[proj_col].concrete
            if v is not None:
                values.append(v)
    return values
```

- [ ] **Step 3: Rewrite the self-join path in `_smt_repair_where`**

Replace lines 471-560 (the `try: from .uexpr import UExprToConstraint ...` block) with:

```python
            # Not satisfied — use SMTSolver with per-alias awareness
            try:
                from parseval.solver.smt import SMTSolver, encode_literal

                smt = SMTSolver(variables=[], timeout_ms=10000, instance=self.instance)
                ctx: Dict[str, Any] = {}
                var_symbols: Dict[str, Any] = {}

                # Include all aliases involved in JOINs with WHERE aliases
                all_aliases = set(aliases_in_condition)
                for jstep in self.plan.ordered_steps:
                    if not isinstance(jstep, Join):
                        continue
                    src_alias = normalize_name(jstep.source_name or jstep.name)
                    for jn in (jstep.joins or {}):
                        jn_alias = normalize_name(jn)
                        if src_alias in aliases_in_condition or jn_alias in aliases_in_condition:
                            all_aliases.add(src_alias)
                            all_aliases.add(jn_alias)

                # Declare per-alias variables
                for alias in all_aliases:
                    table = normalize_name(self.alias_map.get(alias, alias))
                    if table not in self.instance.tables:
                        continue
                    row_idx = self.alias_map.row_index(alias)
                    rows = self.instance.get_rows(table)
                    if row_idx >= len(rows):
                        continue
                    row = rows[row_idx]
                    for col_name, sym in row.items():
                        var_name = f"{alias}[{row_idx}].{col_name}"
                        datatype = smt.col_sort_datatype(table, col_name)
                        var = smt.declare_variable(var_name, datatype)
                        ctx[f"{alias}.{col_name}"] = var
                        var_symbols[var_name] = sym

                # Translate and solve
                if has_subquery:
                    _translate_non_subquery_parts(smt, condition, ctx)
                    _add_not_in_constraints(smt, condition, ctx, instance, alias_map)
                else:
                    z3_pred = smt.translate(condition, ctx=ctx)
                    if z3_pred is not None:
                        smt.add_raw(z3_pred)

                # Add JOIN constraints between aliases
                for jstep in self.plan.ordered_steps:
                    if not isinstance(jstep, Join):
                        continue
                    src_alias = normalize_name(jstep.source_name or jstep.name)
                    for jn, jd in (jstep.joins or {}).items():
                        jn_alias = normalize_name(jn)
                        for sk, jk in zip(jd.get("source_key", []), jd.get("join_key", [])):
                            sk_name = normalize_name(sk.name if hasattr(sk, "name") else str(sk))
                            jk_name = normalize_name(jk.name if hasattr(jk, "name") else str(jk))
                            sk_key = f"{src_alias}.{sk_name}"
                            jk_key = f"{jn_alias}.{jk_name}"
                            if sk_key in ctx and jk_key in ctx:
                                try:
                                    smt.add_raw(ctx[sk_key] == ctx[jk_key])
                                except Exception:
                                    pass

                # Self-join: distinct PK values
                for table, aliases in self.alias_map.self_join_tables().items():
                    active = [a for a in aliases if a in aliases_in_condition]
                    if len(active) < 2:
                        continue
                    pk_col = next(
                        (c for c in self.instance.tables.get(table, {}) if 'id' in c.lower()),
                        None
                    )
                    if pk_col:
                        for i in range(len(active)):
                            for j in range(i + 1, len(active)):
                                ki = f"{active[i]}.{pk_col}"
                                kj = f"{active[j]}.{pk_col}"
                                if ki in ctx and kj in ctx:
                                    try:
                                        smt.add_raw(ctx[ki] != ctx[kj])
                                    except Exception:
                                        pass

                # Solve and apply
                status, solution = smt.solve_raw(var_symbols)
                if status == "sat":
                    SMTSolver.apply_solution(var_symbols, solution)
            except Exception:
                pass
```

- [ ] **Step 4: Add helper functions for subquery handling**

Add these module-level functions to `src/parseval/symbolic/engine.py` (near `_get_inner_query_values`):

```python
def _translate_non_subquery_parts(
    smt, condition: exp.Expression, ctx: Dict[str, Any]
) -> None:
    """Translate parts of a condition that don't contain subqueries."""
    from sqlglot import exp
    if isinstance(condition, exp.And):
        _translate_non_subquery_parts(smt, condition.left, ctx)
        _translate_non_subquery_parts(smt, condition.right, ctx)
    elif isinstance(condition, exp.Paren):
        _translate_non_subquery_parts(smt, condition.this, ctx)
    elif not condition.find(exp.Subquery):
        z3_p = smt.translate(condition, ctx=ctx)
        if z3_p is not None:
            smt.add_raw(z3_p)


def _add_not_in_constraints(
    smt, condition: exp.Expression, ctx: Dict[str, Any], instance, alias_map
) -> None:
    """Add NOT IN anti-value constraints from a condition."""
    from sqlglot import exp
    from parseval.helper import normalize_name
    from parseval.solver.smt import encode_literal

    for in_node in condition.find_all(exp.In):
        if not in_node.find(exp.Subquery):
            continue
        parent = in_node.parent
        is_not_in = isinstance(parent, exp.Not)
        if not is_not_in:
            continue
        outer_col = in_node.this
        if not isinstance(outer_col, exp.Column):
            continue
        col_name = normalize_name(outer_col.name)
        alias_key = (
            f"{normalize_name(outer_col.table)}.{col_name}"
            if outer_col.table
            else None
        )
        var = ctx.get(alias_key) if alias_key else None
        if var is None:
            for k, v in ctx.items():
                if k.endswith(f".{col_name}"):
                    var = v
                    break
        if var is None:
            continue
        inner_vals = _get_inner_query_values(in_node, instance, alias_map)
        col_type = DataType.build(str(instance.tables.get(
            alias_map.get(normalize_name(outer_col.table or ""), ""),
            {}
        ).get(col_name, "TEXT")))
        for v in inner_vals:
            try:
                const = encode_literal(col_type, v, smt.z3ctx).expr
                smt.add_raw(var != const)
            except Exception:
                pass
```

- [ ] **Step 5: Update `_repair_not_in_simple` to use local `_get_inner_query_values`**

In `src/parseval/symbolic/engine.py`, in `_repair_not_in_simple` (line 796), replace:

```python
            # Get inner query values
            from .uexpr import UExprToConstraint
            uexpr = UExprToConstraint(self.plan, self.instance, self.dialect)
            inner_vals = set(uexpr._get_inner_query_values(in_node))
```

With:

```python
            # Get inner query values
            inner_vals = set(_get_inner_query_values(in_node, self.instance, self.alias_map))
```

- [ ] **Step 6: Remove `import z3` from the self-join path**

The self-join path currently has `import z3` at line 477. Remove it (z3 is no longer needed directly in engine.py).

- [ ] **Step 7: Run engine tests**

Run: `pytest tests/symbolic/ -v`
Expected: All tests PASS

- [ ] **Step 8: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/parseval/symbolic/engine.py
git commit -m "refactor(engine): rewrite self-join path to use SMTSolver public API"
```

---

### Task 5: Delete `uexpr.py` and clean up references

**Files:**
- Delete: `src/parseval/symbolic/uexpr.py`
- Modify: `src/parseval/symbolic/engine.py` (remove uexpr import)
- Modify: `src/parseval/logger.py:40` (remove "uexpr" entry)
- Modify: `tests/test_instance.py:144,244` (remove dead UExprToConstraint refs)

- [ ] **Step 1: Remove `from .uexpr import UExprToConstraint` from engine.py**

In `src/parseval/symbolic/engine.py`, search for any remaining `from .uexpr import` or `from .uexpr` imports and remove them.

- [ ] **Step 2: Remove "uexpr" logger entry**

In `src/parseval/logger.py` (line 40), remove the `"uexpr"` entry from the logger configuration dict.

- [ ] **Step 3: Clean up test_instance.py**

In `tests/test_instance.py` (lines 144, 244), remove or update references to `UExprToConstraint`. These import from `src.parseval.uexpr.uexprs` (a different package), not from `src.parseval.symbolic.uexpr`. Check if they're actually used or already skipped.

- [ ] **Step 4: Delete `src/parseval/symbolic/uexpr.py`**

```bash
rm src/parseval/symbolic/uexpr.py
```

- [ ] **Step 5: Run full test suite**

Run: `pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Verify no remaining references to uexpr**

```bash
grep -r "uexpr" src/parseval/ --include="*.py"
grep -r "UExprToConstraint" src/parseval/ --include="*.py"
```
Expected: No output (no remaining references)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: delete uexpr.py and clean up all references"
```

---

## Verification Checklist

After all tasks are complete:

1. `pytest tests/test_solver.py -v` — solver tests pass
2. `pytest tests/symbolic/ -v` — engine tests pass
3. `pytest tests/ -v` — full test suite passes
4. `grep -r "uexpr" src/parseval/ --include="*.py"` — no remaining references
5. `grep -r "UExprToConstraint" src/parseval/ --include="*.py"` — no remaining references
