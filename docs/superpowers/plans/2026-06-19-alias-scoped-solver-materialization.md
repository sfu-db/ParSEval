# Alias-Scoped Solver Materialization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve alias- and scope-specific solver variables while materializing each logical binding into the correct physical table row without null or last-write-wins collisions.

**Architecture:** Planner identities remain the source of logical binding identity, while `ColumnId.source_column_id` and `SolverConstraint.storage_relations` remain the physical-lineage channel. Constraint compilation must fail closed when a qualified nested column lacks scoped identity. Materialization groups by logical relation binding and row scope, then resolves the physical destination for each group.

**Tech Stack:** Python 3.10, sqlglot AST and scope metadata, ParSEval `RelationId`/`ColumnId`/`SolverVar`, unittest/pytest, SQLAlchemy, SQLite.

---

## File Map

- Modify `src/parseval/plan/planner.py`: preserve scoped identities on columns embedded in nested subquery predicates.
- Modify `src/parseval/symbolic/constraints.py`: require scoped physical lineage and stop manufacturing outer synthetic identities for unresolved qualified columns.
- Modify `src/parseval/symbolic/engine.py`: group assignments by logical binding and detect physical-cell conflicts.
- Modify `src/parseval/instance/core.py`: reject explicit nulls for non-nullable columns before row completion.
- Modify `tests/plan/test_identity_resolution.py`: prove nested aliases retain physical lineage and distinct scope identities.
- Modify `tests/symbolic/test_operator_flow_paths.py`: prove compiled solver variables retain alias identity and physical storage mappings.
- Modify `tests/symbolic/test_symbolic_engine.py`: prove same-table aliases materialize separate rows and conflicts fail closed.
- Modify `tests/test_instance_loader.py`: prove explicit null validation happens before SQLite persistence.
- Modify `tests/experiment/test_sqlite_datagen.py`: add the exact BIRD toxicology query 247 regression.

### Task 1: Preserve nested-query column identities in the plan

**Files:**
- Modify: `src/parseval/plan/planner.py:1283-1310`
- Modify: `src/parseval/plan/planner.py:1340-1425`
- Test: `tests/plan/test_identity_resolution.py`

- [ ] **Step 1: Write the failing nested-scope identity test**

Append a regression using the minimal toxicology schema. Inspect the plan's nested predicate columns by qualifier and assert both logical and physical identity:

```python
def test_nested_join_columns_keep_alias_scope_and_physical_lineage():
    schema = """
    CREATE TABLE atom (
      atom_id TEXT PRIMARY KEY,
      element TEXT
    );
    CREATE TABLE connected (
      atom_id TEXT NOT NULL,
      atom_id2 TEXT NOT NULL,
      PRIMARY KEY (atom_id, atom_id2),
      FOREIGN KEY (atom_id) REFERENCES atom(atom_id)
    );
    """
    sql = """
    SELECT DISTINCT T.element
    FROM atom AS T
    WHERE T.element NOT IN (
      SELECT DISTINCT T1.element
      FROM atom AS T1
      INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
    )
    """
    plan = _plan(sql, schema)
    instance = plan._instance

    for step in plan.ordered_steps:
        plan.annotation_for(step)

    columns = {
        (column.table.lower(), column.name.lower()): column_identity(column)
        for column in plan.expression.find_all(exp.Column)
    }
    outer = columns[("t", "element")]
    inner_atom = columns[("t1", "atom_id")]
    inner_connected = columns[("t2", "atom_id")]

    assert outer is not None
    assert inner_atom is not None
    assert inner_connected is not None
    assert len({outer.relation, inner_atom.relation, inner_connected.relation}) == 3
    assert (outer.source_column_id or outer).relation == instance.table_id("atom")
    assert (inner_atom.source_column_id or inner_atom).relation == instance.table_id("atom")
    assert (inner_connected.source_column_id or inner_connected).relation == instance.table_id("connected")


def test_reused_alias_text_is_distinguished_by_query_scope():
    schema = """
    CREATE TABLE atom (atom_id TEXT PRIMARY KEY, element TEXT);
    CREATE TABLE connected (atom_id TEXT NOT NULL, atom_id2 TEXT NOT NULL);
    """
    plan = _plan(
        """
        SELECT X.element
        FROM atom AS X
        WHERE EXISTS (
          SELECT 1 FROM connected AS X WHERE X.atom_id IS NOT NULL
        )
        """,
        schema,
    )
    for step in plan.ordered_steps:
        plan.annotation_for(step)

    outer = next(
        column_identity(column)
        for column in plan.expression.find_all(exp.Column)
        if column.name.lower() == "element"
    )
    inner = next(
        column_identity(column)
        for column in plan.expression.find_all(exp.Column)
        if column.name.lower() == "atom_id"
    )

    assert outer is not None
    assert inner is not None
    assert outer.relation != inner.relation
    assert outer.relation.scope_id != inner.relation.scope_id
    assert (outer.source_column_id or outer).relation.name.normalized == "atom"
    assert (inner.source_column_id or inner).relation.name.normalized == "connected"
```

Extend the existing identity import to:

```python
from parseval.identity import PARSEVAL_COLUMN_ID, ColumnId, column_identity
```

- [ ] **Step 2: Run the focused test and verify the missing nested identities**

Run:

```bash
.venv/bin/pytest tests/plan/test_identity_resolution.py::test_nested_join_columns_keep_alias_scope_and_physical_lineage -q
```

Expected: FAIL because `T1.atom_id` and `T2.atom_id` in `plan.expression` have no `ColumnId`, or because they resolve to the wrong relation.

- [ ] **Step 3: Add one plan-level scoped-expression annotation pass**

In `Plan._annotate()`, after all outer and inner steps have had `_prepare_step_identity()` called, annotate the canonical `plan.expression` using the already-prepared scan relations. Add focused helpers in `planner.py`:

```python
def _all_identity_steps(plan: "Plan") -> t.Iterator["Step"]:
    seen: t.Set[int] = set()

    def walk(step: "Step") -> t.Iterator["Step"]:
        if id(step) in seen:
            return
        seen.add(id(step))
        yield step
        if isinstance(step, SubPlan) and step.inner is not None:
            yield from walk(step.inner)
        for dependency in step.dependencies:
            yield from walk(dependency)

    yield from walk(plan.root)


def _scan_identity_index(plan: "Plan") -> t.Dict[t.Tuple[str, str], t.List[Scan]]:
    index: t.Dict[t.Tuple[str, str], t.List[Scan]] = {}
    for step in _all_identity_steps(plan):
        if not isinstance(step, Scan) or step.relation_id is None:
            continue
        visible = step.relation_id.alias or step.relation_id.name
        if visible is None:
            continue
        source = step.source
        base = normalize_name(source.name) if isinstance(source, exp.Table) else ""
        index.setdefault((normalize_name(visible.raw), base), []).append(step)
    return index
```

Use `sqlglot.optimizer.scope.traverse_scope(plan.expression)` to visit each SQL scope. For each scope-local table source, locate exactly one prepared `Scan` with the same visible alias and physical table name. Stamp each `scope.columns` entry from the matching scan's `output_column_ids`, matching by normalized column name. Copy the complete alias-scoped `ColumnId`, including `scope_id`, ordinal, and `source_column_id`, and call `_enrich_identity_column()` so type/nullability metadata stays aligned.

Do not search parent scopes for a qualified column owned by a local source. Correlated columns may resolve through the parent scope only when `scope.external_columns` identifies them as external.

- [ ] **Step 4: Run the plan identity slice**

Run:

```bash
.venv/bin/pytest tests/plan/test_identity_resolution.py tests/plan/test_annotations.py -q
```

Expected: PASS, including the new nested-scope regression.

- [ ] **Step 5: Commit the planner identity change**

```bash
git add src/parseval/plan/planner.py tests/plan/test_identity_resolution.py
git commit -m "fix: preserve nested alias column identities"
```

### Task 2: Fail closed on unresolved constraint columns

**Files:**
- Modify: `src/parseval/symbolic/constraints.py:1257-1335`
- Test: `tests/symbolic/test_operator_flow_paths.py`

- [ ] **Step 1: Write the failing constraint-lineage regression**

Add the toxicology schema and query to `tests/symbolic/test_operator_flow_paths.py`. Compile the filter target, then inspect every column's `SolverVar`:

```python
def test_nested_alias_solver_vars_keep_distinct_bindings_and_storage():
    schema = """
    CREATE TABLE atom (atom_id TEXT PRIMARY KEY, element TEXT);
    CREATE TABLE connected (
      atom_id TEXT NOT NULL,
      atom_id2 TEXT NOT NULL,
      PRIMARY KEY (atom_id, atom_id2),
      FOREIGN KEY (atom_id) REFERENCES atom(atom_id)
    );
    """
    sql = """
    SELECT DISTINCT T.element
    FROM atom AS T
    WHERE T.element NOT IN (
      SELECT DISTINCT T1.element
      FROM atom AS T1
      INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
    )
    """
    instance, _plan, _tree, _target, constraint = _compile_uncovered(
        sql, schema, site="filter"
    )
    vars_by_qualifier = {}
    for expression in constraint.constraints:
        for column in expression.find_all(exp.Column):
            variable = solver_var(column)
            assert variable is not None
            vars_by_qualifier.setdefault(column.table.lower(), set()).add(variable)

    assert {var.relation_id.display.lower() for var in vars_by_qualifier["t1"]} == {"t1"}
    assert {var.relation_id.display.lower() for var in vars_by_qualifier["t2"]} == {"t2"}
    assert all(var.column_id.kind is not ColumnKind.SYNTHETIC for var in vars_by_qualifier["t1"])
    assert all(var.column_id.kind is not ColumnKind.SYNTHETIC for var in vars_by_qualifier["t2"])
    assert {
        constraint.storage_relations[var].name.normalized
        for var in vars_by_qualifier["t1"]
    } == {"atom"}
    assert {
        constraint.storage_relations[var].name.normalized
        for var in vars_by_qualifier["t2"]
    } == {"connected"}


def test_unresolved_qualified_constraint_column_fails_closed():
    instance = Instance(
        ddls="CREATE TABLE atom (atom_id TEXT PRIMARY KEY);",
        name="unresolved_constraint",
        dialect="sqlite",
    )
    plan = Plan(preprocess_sql("SELECT atom_id FROM atom", instance, dialect="sqlite"), instance)
    column = exp.column("atom_id", table="missing_alias")
    predicate = exp.EQ(this=column, expression=exp.Literal.string("x"))

    with pytest.raises(UnresolvedScopedColumnError, match="unresolved_scoped_column"):
        ConstraintGenerator(plan, instance)._annotate_solver_vars(
            [predicate],
            (instance.table_id("atom"),),
        )
```

Add these imports and extend the existing constraints import:

```python
import pytest
from parseval.identity import ColumnKind
from parseval.solver.types import solver_var
from parseval.symbolic.constraints import ConstraintGenerator, UnresolvedScopedColumnError
```

- [ ] **Step 2: Run the focused test and verify the synthetic outer alias appears**

Run:

```bash
.venv/bin/pytest tests/symbolic/test_operator_flow_paths.py::test_nested_alias_solver_vars_keep_distinct_bindings_and_storage -q
```

Expected: FAIL because the inner join columns are synthetic variables associated with `T`, or have incorrect storage lineage.

- [ ] **Step 3: Replace synthetic physical-column fallback with strict resolution**

Add a private exception local to `constraints.py`:

```python
class UnresolvedScopedColumnError(ValueError):
    pass
```

Change `_column_id_for_expr()` so it follows this order:

```python
col_id = column_identity(col)
if col_id is not None:
    return col_id
if col.table:
    raise UnresolvedScopedColumnError(
        f"unresolved_scoped_column:{col.sql(dialect=self.dialect)}"
    )
rel = self._resolve_relation(col, tables)
if rel is None:
    return None
return column_id(
    ColumnKind.SYNTHETIC,
    identifier_name(col.name, dialect=self.dialect),
    rel,
)
```

This retains synthetic fallback only for unqualified derived values. Do not catch this exception inside `_annotate_solver_vars()`; a malformed constraint must not reach the solver or materializer.

Update `_storage_relation_for_column_id()` to return `None` for a synthetic or derived column without `source_column_id`. This prevents name-only physical inference.

- [ ] **Step 4: Run constraint and solver identity tests**

Run:

```bash
.venv/bin/pytest tests/symbolic/test_operator_flow_paths.py tests/symbolic/test_plausible_constraints.py tests/solver/test_solver_identity.py -q
```

Expected: PASS. The solver identity tests must continue proving that alias variables remain distinct without giving the solver database responsibilities.

- [ ] **Step 5: Commit strict constraint resolution**

```bash
git add src/parseval/symbolic/constraints.py tests/symbolic/test_operator_flow_paths.py
git commit -m "fix: resolve solver columns through scoped plan identity"
```

### Task 3: Materialize rows by logical binding

**Files:**
- Modify: `src/parseval/symbolic/engine.py:173-226`
- Test: `tests/symbolic/test_symbolic_engine.py`

- [ ] **Step 1: Add failing tests for separate alias rows and assignment conflicts**

Add imports for `DataType`, identity constructors, `SolverConstraint`, `SolveResult`, `SolverVar`, and `ConstraintConflict`. Add a deterministic solver stub and two tests:

```python
class _FixedSolver:
    def __init__(self, assignments):
        self.assignments = assignments

    def solve(self, _constraint):
        return SolveResult(sat=True, assignments=self.assignments)


def test_same_table_aliases_materialize_as_separate_rows():
    instance = Instance(
        ddls="CREATE TABLE people (id INT PRIMARY KEY, manager_id INT, name TEXT NOT NULL);",
        name="self_join_materialization",
        dialect="sqlite",
    )
    physical = instance.table_id("people")
    physical_id = instance.column_id("people", "id")
    physical_name = instance.column_id("people", "name")
    left = relation_id(RelationKind.TABLE, physical.name, alias=identifier_name("a"), scope_id="left")
    right = relation_id(RelationKind.TABLE, physical.name, alias=identifier_name("b"), scope_id="right")
    left_id = column_id(ColumnKind.PHYSICAL, identifier_name("id"), left, source_column_id=physical_id)
    left_name = column_id(ColumnKind.PHYSICAL, identifier_name("name"), left, source_column_id=physical_name)
    right_id = column_id(ColumnKind.PHYSICAL, identifier_name("id"), right, source_column_id=physical_id)
    right_name = column_id(ColumnKind.PHYSICAL, identifier_name("name"), right, source_column_id=physical_name)
    assignments = {
        SolverVar(left_id, left, "r0"): 1,
        SolverVar(left_name, left, "r0"): "Alice",
        SolverVar(right_id, right, "r0"): 2,
        SolverVar(right_name, right, "r0"): "Bob",
    }
    constraint = SolverConstraint(
        target_relations=(left, right),
        storage_relations={variable: physical for variable in assignments},
    )
    engine = SymbolicEngine(
        instance,
        "SELECT a.name FROM people a JOIN people b ON a.id = b.manager_id",
        solver=_FixedSolver(assignments),
        max_iterations=1,
    )

    assert engine._solve_and_materialize(constraint)
    assert sorted(
        (row[physical_id].concrete, row[physical_name].concrete)
        for row in instance.get_rows("people")
    ) == [(1, "Alice"), (2, "Bob")]
```

For the conflict test, construct two `SolverVar` objects with the same logical relation and row scope whose `source_column_id` is `people.name`, assign `"Alice"` and `"Bob"`, and assert `_solve_and_materialize()` returns `False` and creates no rows.

- [ ] **Step 2: Run both focused tests and verify current grouping collapses aliases**

Run:

```bash
.venv/bin/pytest tests/symbolic/test_symbolic_engine.py -k "same_table_aliases_materialize_as_separate_rows or conflicting_materialized_assignment" -q
```

Expected: FAIL because grouping by `(storage_relation, row_scope)` merges `a` and `b`, and duplicate physical columns use last-write-wins.

- [ ] **Step 3: Introduce a focused assignment-to-row helper**

Add a private immutable logical-row key and helper in `engine.py`:

```python
@dataclass(frozen=True)
class _LogicalRowKey:
    relation: RelationId
    row_scope: str


def _materialized_rows(
    constraint: SolverConstraint,
    assignments: Dict[SolverVar, Any],
) -> Dict[_LogicalRowKey, Tuple[RelationId, Dict[str, Any]]]:
    rows: Dict[_LogicalRowKey, Tuple[RelationId, Dict[str, Any]]] = {}
    for variable, value in assignments.items():
        storage_relation = constraint.storage_relations.get(variable)
        storage_column = variable.column_id.source_column_id or variable.column_id
        if storage_relation is None or storage_column.source_column_id is None and storage_column.kind is not ColumnKind.PHYSICAL:
            raise ConstraintConflict(f"missing_physical_lineage:{variable.display}")
        key = _LogicalRowKey(variable.relation_id, variable.row_scope or "r0")
        existing = rows.get(key)
        if existing is None:
            values: Dict[str, Any] = {}
            rows[key] = (storage_relation, values)
        else:
            existing_relation, values = existing
            if existing_relation != storage_relation:
                raise ConstraintConflict(f"conflicting_storage_relation:{variable.display}")
        column_name = storage_column.name.normalized
        if column_name in values and values[column_name] != value:
            raise ConstraintConflict(
                f"conflicting_materialized_assignment:{variable.display}"
            )
        values[column_name] = value
    return rows
```

Import `dataclass`, `ColumnKind`, `RelationId`, and `ConstraintConflict`. In `_solve_and_materialize()`, call this helper inside the existing failure boundary, order its physical relations with `Instance._creation_order()`, and invoke `create_row()` once for every logical key. On `ConstraintConflict`, return `False` without adding rows; retain the engine checkpoint/rollback behavior in `generate()`.

- [ ] **Step 4: Run symbolic engine and operator-flow tests**

Run:

```bash
.venv/bin/pytest tests/symbolic/test_symbolic_engine.py tests/symbolic/test_operator_flow_paths.py -q
```

Expected: PASS. Existing row-scope tests and quoted-storage tests must remain green.

- [ ] **Step 5: Commit logical-binding materialization**

```bash
git add src/parseval/symbolic/engine.py tests/symbolic/test_symbolic_engine.py
git commit -m "fix: materialize solver rows by logical binding"
```

### Task 4: Reject explicit nulls for non-nullable columns

**Files:**
- Modify: `src/parseval/instance/core.py:1243-1257`
- Test: `tests/test_instance_loader.py`

- [ ] **Step 1: Write the failing instance-boundary test**

Add:

```python
def test_create_row_rejects_explicit_null_for_non_nullable_column(self):
    instance = Instance(ddls=SCHEMA, name="nonnull_case", dialect="sqlite")

    with self.assertRaisesRegex(
        ConstraintViolationError,
        "explicit_null_for_non_nullable_column:users.id",
    ):
        instance.create_row("users", {"id": None, "name": "Alice"})

    self.assertEqual(instance.get_rows("users"), [])
```

Import `ConstraintViolationError` from `parseval.domain.exceptions`.

- [ ] **Step 2: Run the test and verify the null currently reaches row completion**

Run:

```bash
.venv/bin/pytest tests/test_instance_loader.py::InstanceLoaderTests::test_create_row_rejects_explicit_null_for_non_nullable_column -q
```

Expected: FAIL because `create_row()` currently treats the explicit `None` as a preset value.

- [ ] **Step 3: Validate only explicitly supplied values**

Immediately after `_normalize_row_values_by_id()` in `Instance.create_row()`, add:

```python
for column, value in values_by_id.items():
    if value is None and not self.nullable(relation, column):
        raise ConstraintViolationError(
            "explicit_null_for_non_nullable_column:"
            f"{relation.display}.{column.name.normalized}"
        )
```

Import `ConstraintViolationError` beside the existing domain exceptions. Do not validate omitted columns here; `DatabaseBuilder.complete_row()` must remain able to generate them.

- [ ] **Step 4: Run instance tests**

Run:

```bash
.venv/bin/pytest tests/test_instance_loader.py tests/instance/test_row_identity.py -q
```

Expected: PASS, including circular-FK and omitted-primary-key generation tests.

- [ ] **Step 5: Commit the instance guard**

```bash
git add src/parseval/instance/core.py tests/test_instance_loader.py
git commit -m "fix: reject explicit nulls for non-nullable columns"
```

### Task 5: Add and verify the exact BIRD regression

**Files:**
- Modify: `tests/experiment/test_sqlite_datagen.py`

- [ ] **Step 1: Add a fixture-independent toxicology regression**

Add a test that embeds the four toxicology tables and exact query so it does not depend on optional BIRD files:

```python
def test_toxicology_not_in_subquery_persists_non_null_atom_keys(tmp_path):
    from parseval.main import instantiate_db

    schema = """
    CREATE TABLE atom (
      atom_id TEXT NOT NULL PRIMARY KEY,
      molecule_id TEXT,
      element TEXT,
      FOREIGN KEY (molecule_id) REFERENCES molecule(molecule_id)
    );
    CREATE TABLE bond (
      bond_id TEXT NOT NULL PRIMARY KEY,
      molecule_id TEXT,
      bond_type TEXT,
      FOREIGN KEY (molecule_id) REFERENCES molecule(molecule_id)
    );
    CREATE TABLE connected (
      atom_id TEXT NOT NULL,
      atom_id2 TEXT NOT NULL,
      bond_id TEXT,
      PRIMARY KEY (atom_id, atom_id2),
      FOREIGN KEY (atom_id) REFERENCES atom(atom_id),
      FOREIGN KEY (atom_id2) REFERENCES atom(atom_id),
      FOREIGN KEY (bond_id) REFERENCES bond(bond_id)
    );
    CREATE TABLE molecule (
      molecule_id TEXT NOT NULL PRIMARY KEY,
      label TEXT
    );
    """
    sql = """
    SELECT DISTINCT T.element
    FROM atom AS T
    WHERE T.element NOT IN (
      SELECT DISTINCT T1.element
      FROM atom AS T1
      INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
    )
    """
    database_path = tmp_path / "toxicology_247.sqlite"
    result = instantiate_db(
        sql,
        schema,
        f"sqlite:///{database_path}",
        "sqlite",
        db_id="toxicology_247",
        max_iterations=10,
        atom_null=0,
        atom_dup=1,
    )

    assert result.success, result.error_msg
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("SELECT atom_id FROM atom").fetchall()
    assert rows
    assert all(atom_id is not None for (atom_id,) in rows)
```

Import `sqlite3` at module level.

- [ ] **Step 2: Run the exact regression**

Run:

```bash
.venv/bin/pytest tests/experiment/test_sqlite_datagen.py::test_toxicology_not_in_subquery_persists_non_null_atom_keys -q
```

Expected: PASS. The previous failure was `NOT NULL constraint failed: atom.atom_id`.

- [ ] **Step 3: Run all focused identity and persistence slices**

Run:

```bash
.venv/bin/pytest \
  tests/plan/test_identity_resolution.py \
  tests/plan/test_annotations.py \
  tests/solver/test_solver_identity.py \
  tests/symbolic/test_plausible_constraints.py \
  tests/symbolic/test_operator_flow_paths.py \
  tests/symbolic/test_symbolic_engine.py \
  tests/test_instance_loader.py \
  tests/instance/test_row_identity.py \
  tests/experiment/test_sqlite_datagen.py::test_toxicology_not_in_subquery_persists_non_null_atom_keys \
  -q
```

Expected: PASS with no integrity errors or materialization conflicts.

- [ ] **Step 4: Run the broader suite**

Run:

```bash
.venv/bin/pytest tests/plan tests/solver tests/symbolic tests/instance tests/test_instance_loader.py -q
```

Expected: PASS. If an unrelated pre-existing failure appears, record its exact test and error separately; do not broaden this change to repair it.

- [ ] **Step 5: Commit the BIRD regression**

```bash
git add tests/experiment/test_sqlite_datagen.py
git commit -m "test: cover nested alias database materialization"
```

## Final Review

- [ ] Confirm every changed production line traces to scoped identity, physical lineage, logical-row grouping, collision detection, or non-null validation.
- [ ] Confirm no solver code depends on `Instance` or physical database state.
- [ ] Confirm `git diff --check` is clean.
- [ ] Confirm the exact toxicology regression and the broader focused suite pass from a clean command invocation.
