# Unified Constraint Representation — Speculate Rewrite Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the speculative layer so constraints are `List[exp.Expression]` throughout. The Propagator builds expressions directly, the Resolver delegates to the Solver, and the old `(col, op, value)` tuple path is removed entirely.

**Architecture:** Propagator walks the Plan top-down and stores `exp.Expression` objects on `TableConstraint.constraints`. Resolver packages them into `SolverConstraint` and calls the unified Solver. Old fields (`fixed_values`, `must_null`, `not_null`, `predicates`) and old Resolver methods (`_satisfy`, `_build_row`, etc.) are gone.

**Tech Stack:** Python 3.10+, sqlglot expressions, pytest

**Solver status:** `SolverConstraint`, `Solver`, `SolveResult`, `DomainSolver` exist in `src/parseval/solver/`. Solver accepts `constraints: List[exp.Expression]` and `join_equalities`.

---

## Prerequisites — Solver Gaps (handled separately)

The DomainSolver needs lowering support for IS NOT NULL, IN, NOT IN. This is handled outside this plan. The Propagator builds these expressions; the solver must consume them.

**Column type annotations:** The Propagator sets `.type` on `exp.Column` nodes it creates for schema constraints, so the solver can resolve datatypes.

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/parseval/symbolic/speculate.py` | Rewrite | New `TableConstraint`, new `Propagator`, new `Resolver` |
| `src/parseval/symbolic/constraints.py` | Modify | Unify `SolverConstraint` import from `solver.unified` |
| `src/parseval/symbolic/__init__.py` | Modify | Update import path |
| `src/parseval/symbolic/engine.py` | Modify | Update import path |
| `tests/symbolic/test_speculate_enhancements.py` | Rewrite | Tests for new API |
| `tests/symbolic/test_symbolic_engine.py` | Verify | Ensure engine still works |

---

### Task 1: Define `TableConstraint` and rewrite `Propagator`

**Files:**
- Rewrite: `src/parseval/symbolic/speculate.py` — data classes + Propagator
- Test: `tests/symbolic/test_speculate_enhancements.py`

- [ ] **Step 1: Write the `TableConstraint` definition**

```python
@dataclass
class TableConstraint:
    """Constraints on what one table needs for a specific branch."""
    table: str  # physical table name
    alias: Optional[str] = None
    constraints: List[exp.Expression] = field(default_factory=list)
    min_rows: int = 1
    duplicate_columns: List[str] = field(default_factory=list)
    group_key_columns: List[str] = field(default_factory=list)
```

- [ ] **Step 2: Write the `BranchSpec` definition**

```python
@dataclass
class BranchSpec:
    """Requirements for one branch outcome."""
    branch: str
    requirements: Dict[str, TableConstraint] = field(default_factory=dict)
    equivalences: ColumnUnionFind = field(default_factory=ColumnUnionFind)
    deferred: List[exp.Expression] = field(default_factory=list)

    def require(self, table: str) -> TableConstraint:
        if table not in self.requirements:
            self.requirements[table] = TableConstraint(table=table)
        return self.requirements[table]

    def equate(self, col_a: str, col_b: str) -> None:
        self.equivalences.union(col_a, col_b)
```

- [ ] **Step 3: Write the Propagator class**

Key methods:
- `propagate()` → walks plan, builds specs for positive + negative branches
- `_propagate_step()` → recursive top-down walk, dispatches by step type
- `_split_conjuncts()` → splits AND into conjuncts
- `_resolve_columns()` → resolves column table qualifiers through alias map
- `_find_table_for_expr()` → finds primary table for an expression
- `_add_schema_constraints()` → adds NOT NULL, UNIQUE, FK as expressions
- `_add_column_type_annotations()` → sets `.type` on Column nodes from `column_meta`

```python
class Propagator:
    def __init__(self, plan: Plan, instance: Instance, alias_map, dialect: str):
        self.plan = plan
        self.instance = instance
        self.alias_map = alias_map
        self.dialect = dialect

    def propagate(self) -> List[BranchSpec]:
        specs = []
        pos = BranchSpec(branch="positive")
        self._propagate_step(self.plan.root, pos)
        self._add_schema_constraints(pos)
        self._annotate_column_types(pos)
        specs.append(pos)
        for step in self.plan.ordered_steps:
            if isinstance(step, Filter) and step.condition:
                neg = BranchSpec(branch="negative")
                self._propagate_step(self.plan.root, neg, negate_step=step)
                self._add_schema_constraints(neg)
                self._annotate_column_types(neg)
                specs.append(neg)
            elif isinstance(step, Join):
                left_un = BranchSpec(branch="left_unmatched")
                self._propagate_unmatched_left(step, left_un)
                self._add_schema_constraints(left_un)
                self._annotate_column_types(left_un)
                specs.append(left_un)
            elif isinstance(step, Having) and step.condition:
                fail = BranchSpec(branch="having_fail")
                self._propagate_step(self.plan.root, fail, negate_step=step)
                self._add_schema_constraints(fail)
                self._annotate_column_types(fail)
                specs.append(fail)
        return specs
```

- [ ] **Step 4: Implement `_propagate_step` for Filter**

```python
elif isinstance(step, Filter):
    for dep in step.chain_dependencies:
        self._propagate_step(dep, spec, negate_step)
    if step.condition:
        if step is negate_step:
            from parseval.plan.rex import negate_predicate
            negated = negate_predicate(step.condition.copy())
            self._store_expression(negated, spec)
        else:
            self._store_expression(step.condition, spec)
        # Detect scalar subquery atoms for deferred evaluation.
        for atom in self._iter_scalar_subquery_atoms(step.condition):
            spec.deferred.append(atom)
```

- [ ] **Step 5: Implement `_propagate_step` for Join**

```python
elif isinstance(step, Join):
    for dep in step.chain_dependencies:
        self._propagate_step(dep, spec, negate_step)
    for join_name, join_data in (step.joins or {}).items():
        join_table = self._resolve_table(join_name)
        source_keys = join_data.get("source_key", [])
        join_keys = join_data.get("join_key", [])
        for sk, jk in zip(source_keys, join_keys):
            sk_table_name = sk.table if hasattr(sk, "table") and sk.table else (step.source_name or step.name)
            sk_table = self._resolve_table(sk_table_name)
            sk_col = self._match_column(sk_table, sk.name if hasattr(sk, "name") else str(sk))
            jk_col = self._match_column(join_table, jk.name if hasattr(jk, "name") else str(jk))
            if sk_col and jk_col:
                spec.require(sk_table)
                spec.require(join_table)
                spec.equate(f"{sk_table}.{sk_col}", f"{join_table}.{jk_col}")
                # Store join equality as expression
                join_expr = exp.EQ(
                    this=exp.Column(
                        this=exp.to_identifier(jk_col),
                        table=exp.to_identifier(join_table),
                    ),
                    expression=exp.Column(
                        this=exp.to_identifier(sk_col),
                        table=exp.to_identifier(sk_table),
                    ),
                )
                spec.require(join_table).constraints.append(join_expr)
                req_jk = spec.require(join_table)
                if jk_col not in req_jk.group_key_columns:
                    req_jk.group_key_columns.append(jk_col)
```

- [ ] **Step 6: Implement `_propagate_step` for Having**

```python
elif isinstance(step, Having):
    for dep in step.chain_dependencies:
        self._propagate_step(dep, spec, negate_step)
    if step.condition and step is not negate_step:
        self._store_expression(step.condition, spec)
        counted_table = self._find_counted_table(step.condition)
        min_size = self._extract_min_group_size(step.condition)
        if counted_table and counted_table in spec.requirements:
            spec.requirements[counted_table].min_rows = max(
                spec.requirements[counted_table].min_rows, min_size
            )
        else:
            for req in spec.requirements.values():
                req.min_rows = max(req.min_rows, min_size)
```

- [ ] **Step 7: Implement `_propagate_step` for Aggregate**

```python
elif isinstance(step, Aggregate):
    for dep in step.chain_dependencies:
        self._propagate_step(dep, spec, negate_step)
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
    for agg_expr in step.aggregations:
        self._add_aggregate_null_constraints(agg_expr, spec)
```

- [ ] **Step 8: Implement `_store_expression` helper**

```python
def _store_expression(self, expr: exp.Expression, spec: BranchSpec):
    """Decompose AND, resolve columns, store per-table."""
    for conjunct in self._split_conjuncts(expr):
        resolved = self._resolve_columns(conjunct.copy())
        table = self._find_table_for_expr(resolved)
        if table:
            spec.require(table).constraints.append(resolved)
```

- [ ] **Step 9: Implement `_add_schema_constraints`**

```python
def _add_schema_constraints(self, spec: BranchSpec):
    for table_key, req in spec.requirements.items():
        physical = req.table
        if "__" in physical:
            physical = physical.split("__")[0]
        if physical not in self.instance.tables:
            continue
        for col_name, col_type in self.instance.tables[physical].items():
            col = exp.Column(
                this=exp.to_identifier(col_name),
                table=exp.to_identifier(physical),
            )
            # NOT NULL
            if not self.instance.nullable(physical, col_name):
                req.constraints.append(
                    exp.Is(this=col, expression=exp.Null()).not_()
                )
            # UNIQUE — avoid existing values
            if self.instance.is_unique(physical, col_name):
                existing = {
                    s.concrete for s in self.instance.get_column_data(physical, col_name)
                    if s.concrete is not None
                }
                if existing:
                    req.constraints.append(
                        exp.Not(this=exp.In(
                            this=col.copy(),
                            expressions=[exp.Literal(v) for v in existing],
                        ))
                    )
        # FK constraints
        for fk in self.instance.get_foreign_key(physical):
            local_col = normalize_name(fk.expressions[0].name)
            ref = fk.args.get("reference")
            if ref:
                ref_table_node = ref.find(exp.Table)
                if ref_table_node:
                    ref_table = normalize_name(ref_table_node.name)
                    ref_col = self.instance.resolve_fk_ref_column(fk)
                    if ref_col:
                        parent_rows = self.instance.get_rows(ref_table)
                        if parent_rows:
                            parent_vals = [
                                r[ref_col].concrete for r in parent_rows
                                if r[ref_col].concrete is not None
                            ]
                            if parent_vals:
                                req.constraints.append(exp.In(
                                    this=exp.Column(
                                        this=exp.to_identifier(local_col),
                                        table=exp.to_identifier(physical),
                                    ),
                                    expressions=[exp.Literal(v) for v in parent_vals],
                                ))
```

- [ ] **Step 10: Implement `_annotate_column_types`**

```python
def _annotate_column_types(self, spec: BranchSpec):
    """Set .type on Column nodes from enriched column_meta."""
    for table_key, req in spec.requirements.items():
        physical = req.table
        if "__" in physical:
            physical = physical.split("__")[0]
        for expr in req.constraints:
            for col in expr.find_all(exp.Column):
                if col.type is not None:
                    continue
                meta = column_meta(col)
                if meta and meta.get("domain"):
                    col.set("type", meta["domain"])
                elif physical in self.instance.tables:
                    col_name = self._match_column(physical, col.name)
                    if col_name:
                        dtype_str = str(self.instance.tables[physical].get(col_name, "TEXT"))
                        try:
                            col.set("type", exp.DataType.build(dtype_str))
                        except Exception:
                            pass
```

- [ ] **Step 11: Write the test**

```python
def test_propagator_builds_expression_constraints():
    from parseval.instance import Instance
    from parseval.plan import Plan
    from parseval.query import preprocess_sql
    from parseval.symbolic.speculate import Propagator
    from sqlglot import exp

    schema = "CREATE TABLE t1 (id INT PRIMARY KEY, val INT NOT NULL);"
    sql = "SELECT * FROM t1 WHERE t1.val > 5"
    instance = Instance(ddls=schema, name="test_prop", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr)

    propagator = Propagator(plan, instance, plan.alias_map, "sqlite")
    specs = propagator.propagate()

    pos = specs[0]
    t1 = pos.requirements.get("t1")
    assert t1 is not None
    # Query predicate
    has_gt = any(isinstance(e, exp.GT) for e in t1.constraints)
    assert has_gt, "should have GT for val > 5"
    # Schema: NOT NULL
    has_not_null = any(
        isinstance(e, exp.Is) and isinstance(e.expression, exp.Null) and e.args.get("not")
        for e in t1.constraints
    )
    assert has_not_null, "should have IS NOT NULL for NOT NULL column"
```

- [ ] **Step 12: Run the test**

Run: `python -m pytest tests/symbolic/test_speculate_enhancements.py::test_propagator_builds_expression_constraints -v`
Expected: PASS

- [ ] **Step 13: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_enhancements.py
git commit -m "feat(speculate): rewrite Propagator — builds expressions directly"
```

---

### Task 2: Rewrite `Resolver`

**Files:**
- Rewrite: `src/parseval/symbolic/speculate.py` — Resolver class

- [ ] **Step 1: Write the Resolver class**

```python
class Resolver:
    """Turn TableConstraints into concrete row values via the Solver."""

    def __init__(self, instance: Instance, dialect: str = "sqlite", *, solver=None):
        self.instance = instance
        self.dialect = dialect
        self.solver = solver

    def resolve(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
        self._discover_fk_parents(spec)
        join_equalities = self._equivalences_to_join_equalities(spec)
        order = self._creation_order(spec)
        result: Dict[str, List[Dict[str, Any]]] = {}

        for table_key in order:
            if table_key not in spec.requirements:
                continue
            req = spec.requirements[table_key]
            physical = req.table
            if "__" in physical:
                physical = physical.split("__")[0]

            for i in range(req.min_rows):
                row = self._solve_row(physical, req, spec, join_equalities, result)
                if row:
                    if req.duplicate_columns and i > 0 and physical in result:
                        base = result[physical][0]
                        for col in req.duplicate_columns:
                            if col in base:
                                row[col] = base[col]
                    if req.group_key_columns and i > 0 and physical in result:
                        base = result[physical][0]
                        for col in req.group_key_columns:
                            if col in base:
                                row[col] = base[col]
                    result.setdefault(physical, []).append(row)

        if spec.deferred:
            self._resolve_deferred(spec, result)

        return result

    def _solve_row(self, table, req, spec, join_equalities, result):
        from parseval.solver.unified import SolverConstraint

        all_constraints = list(req.constraints)
        # Cross-table coordination: add EQ for already-solved tables
        for other_table, rows in result.items():
            if rows:
                for col, val in rows[0].items():
                    all_constraints.append(exp.EQ(
                        this=exp.Column(
                            this=exp.to_identifier(col),
                            table=exp.to_identifier(other_table),
                        ),
                        expression=exp.Literal(val) if val is not None else exp.Null(),
                    ))

        constraint = SolverConstraint(
            target_tables=(table,),
            constraints=all_constraints,
            join_equalities=join_equalities,
        )
        solve_result = self.solver.solve(constraint)
        if solve_result.sat:
            return solve_result.assignments.get(table, {})
        return {}

    def _equivalences_to_join_equalities(self, spec):
        equalities = []
        for rep, members in spec.equivalences.groups().items():
            if len(members) >= 2:
                for i in range(len(members) - 1):
                    t1, c1 = members[i].split(".", 1)
                    t2, c2 = members[i + 1].split(".", 1)
                    equalities.append((t1, c1, t2, c2))
        return equalities

    def _discover_fk_parents(self, spec):
        """Add FK-referenced parent tables to spec if missing."""
        tables = list(spec.requirements.keys())
        i = 0
        while i < len(tables):
            table = tables[i]
            i += 1
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
                            spec.requirements[ref_table] = TableConstraint(table=ref_table)
                            tables.append(ref_table)

    def _creation_order(self, spec):
        # Topological sort by FK dependencies (same logic as before)
        ...

    def _resolve_deferred(self, spec, result):
        # Evaluate deferred scalar subqueries (same logic as before)
        ...
```

- [ ] **Step 2: Write the test**

```python
def test_resolver_delegates_to_solver():
    from parseval.instance import Instance
    from parseval.solver.unified import Solver
    from parseval.symbolic.speculate import BranchSpec, Resolver, TableConstraint
    from sqlglot import exp

    schema = "CREATE TABLE t1 (id INT PRIMARY KEY, val INT);"
    instance = Instance(ddls=schema, name="test_res", dialect="sqlite")
    solver = Solver(dialect="sqlite")
    resolver = Resolver(instance, dialect="sqlite", solver=solver)

    spec = BranchSpec(branch="positive")
    req = TableConstraint(table="t1")
    col = exp.Column(this=exp.to_identifier("val"), table=exp.to_identifier("t1"))
    col.set("type", exp.DataType.build("INT"))
    req.constraints.append(exp.GT(this=col, expression=exp.Literal.number(5)))
    spec.requirements["t1"] = req

    rows = resolver.resolve(spec)
    assert "t1" in rows
    assert rows["t1"][0]["val"] > 5
```

- [ ] **Step 3: Run the test**

Run: `python -m pytest tests/symbolic/test_speculate_enhancements.py::test_resolver_delegates_to_solver -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_enhancements.py
git commit -m "feat(speculate): rewrite Resolver — delegates to Solver"
```

---

### Task 3: Wire up `speculate()` and backward-compat wrappers

**Files:**
- Modify: `src/parseval/symbolic/speculate.py` — top-level API

- [ ] **Step 1: Write `speculate()` function**

```python
def speculate(
    plan: Plan,
    instance: Instance,
    alias_map,
    dialect: str = "sqlite",
) -> List[Tuple[str, Dict[str, List[Dict[str, Any]]]]]:
    from parseval.solver.unified import Solver
    propagator = Propagator(plan, instance, alias_map, dialect)
    solver = Solver(dialect=dialect)
    resolver = Resolver(instance, dialect, solver=solver)
    branch_specs = propagator.propagate()

    results = []
    for spec in branch_specs:
        if spec.requirements:
            rows = resolver.resolve(spec)
            results.append((spec.branch, rows))
    return results
```

- [ ] **Step 2: Write backward-compat wrappers**

```python
def build_spec(plan, instance, *, alias_map, target_outcome="positive", negate_atom=None):
    propagator = Propagator(plan, instance, alias_map, dialect="sqlite")
    if target_outcome == "positive":
        specs = propagator.propagate()
        return specs[0] if specs else BranchSpec(branch="positive")
    return BranchSpec(branch=target_outcome)

def resolve_spec(spec, instance, dialect="sqlite"):
    solver = Solver(dialect=dialect)
    resolver = Resolver(instance, dialect, solver=solver)
    rows = resolver.resolve(spec)
    return {table: row_list[0] if row_list else {} for table, row_list in rows.items()}
```

- [ ] **Step 3: Write the end-to-end test**

```python
def test_speculate_end_to_end():
    from parseval.instance import Instance
    from parseval.plan import Plan
    from parseval.query import preprocess_sql
    from parseval.symbolic.speculate import speculate

    schema = "CREATE TABLE t1 (id INT PRIMARY KEY, val INT);"
    sql = "SELECT * FROM t1 WHERE t1.val > 5"
    instance = Instance(ddls=schema, name="test_e2e", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr)

    results = speculate(plan, instance, plan.alias_map, dialect="sqlite")
    assert len(results) >= 1
    branch, rows = results[0]
    assert branch == "positive"
    assert rows["t1"][0]["val"] > 5
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/symbolic/test_speculate_enhancements.py::test_speculate_end_to_end -v`
Expected: PASS

- [ ] **Step 5: Update `__all__` exports**

```python
__all__ = [
    "BranchSpec",
    "Propagator",
    "Resolver",
    "TableConstraint",
    "build_spec",
    "resolve_spec",
    "speculate",
]
```

- [ ] **Step 6: Commit**

```bash
git add src/parseval/symbolic/speculate.py tests/symbolic/test_speculate_enhancements.py
git commit -m "feat(speculate): wire up speculate() with Solver integration"
```

---

### Task 4: Unify `SolverConstraint` imports

**Files:**
- Modify: `src/parseval/symbolic/constraints.py`
- Modify: `src/parseval/symbolic/__init__.py`
- Modify: `src/parseval/symbolic/engine.py`

- [ ] **Step 1: Update `symbolic/constraints.py`**

Replace local `SolverConstraint` with:

```python
from parseval.solver.unified import SolverConstraint
```

Update `ConstraintGenerator.generate()` to encode `null_columns`, `not_null_columns`, `avoid_values`, `foreign_keys` as expressions in `constraints`:

```python
# null_columns → IS NULL expressions
for col in null_columns:
    constraints_list.append(exp.Is(this=col.copy(), expression=exp.Null()))

# not_null_columns → IS NOT NULL expressions
for (table, col_name) in not_null_columns:
    constraints_list.append(exp.Is(
        this=exp.Column(this=exp.to_identifier(col_name), table=exp.to_identifier(table)),
        expression=exp.Null(),
    ).not_())

# avoid_values → NOT IN expressions
for key, vals in avoid_values.items():
    table, col_name = key.split(".", 1)
    constraints_list.append(exp.Not(this=exp.In(
        this=exp.Column(this=exp.to_identifier(col_name), table=exp.to_identifier(table)),
        expressions=[exp.Literal(v) for v in vals],
    )))

# foreign_keys → IN expressions (deferred: solver handles via join_equalities)
```

- [ ] **Step 2: Update `symbolic/__init__.py`**

```python
from parseval.solver.unified import SolverConstraint
from .constraints import ConstraintGenerator
```

- [ ] **Step 3: Update `symbolic/engine.py`**

```python
from parseval.solver.unified import SolverConstraint
```

- [ ] **Step 4: Run full symbolic + solver tests**

Run: `python -m pytest tests/symbolic/ tests/solver/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/parseval/symbolic/constraints.py src/parseval/symbolic/__init__.py src/parseval/symbolic/engine.py
git commit -m "refactor(symbolic): unify SolverConstraint — import from solver.unified"
```

---

### Task 5: BIRD benchmark regression gate

**Files:** None (verification only)

- [ ] **Step 1: Run symbolic engine tests**

Run: `python -m pytest tests/symbolic/test_symbolic_engine.py -v`
Expected: All PASS

- [ ] **Step 2: Run BIRD benchmark**

Run: `python -m pytest tests/symbolic/test_symbolic_bird.py -v`
Expected: No regressions from baseline (1508/1534 = 98%)

- [ ] **Step 3: Run full test suite**

Run: `python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Debug any regressions**

Common failure modes:
- Missing `.type` on Column nodes → check `_annotate_column_types`
- DomainSolver can't lower an expression → check Task 1 coverage
- Join equality propagation → check `_equivalences_to_join_equalities`
- FK parent tables missing → check `_discover_fk_parents`

---

## Verification

After each task:
```
python -m pytest tests/symbolic/ -v
python -m pytest tests/solver/ -v
```

Final gate:
```
python -m pytest tests/ -v
python -m pytest tests/symbolic/test_symbolic_bird.py -v
```
