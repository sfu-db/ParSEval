# Row-Set Obligations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ad hoc multi-row final-result and HAVING generation patches with planner/evaluator-derived row-set obligations.

**Architecture:** Add a structured `RowSetObligation` to the existing symbolic types and attach it through `OperatorObligation`, keeping `BranchTree`, `PlanEvaluator`, and planner annotations as the source of truth. `ConstraintGenerator` lowers multi-row root and HAVING cardinality obligations through one shared path that applies scan, predicate, join, group, and distinct requirements per logical row scope. Large offsets keep true root-result coverage thresholds even when generation is capped.

**Tech Stack:** Python, sqlglot ASTs, ParSEval planner/evaluator/symbolic modules, pytest.

---

## File Structure

- Modify `src/parseval/symbolic/types.py`
  - Add `RowSetObligation`.
  - Extend `OperatorObligation` with optional `row_set`.
  - Keep coverage threshold logic tied to `root_result` row counts.
- Modify `src/parseval/symbolic/branch_tree.py`
  - Build root-result row-set obligations from the plan root.
  - Build HAVING cardinality row-set obligations from planner HAVING metadata.
  - Use `row_set` obligations directly for multi-row root and HAVING cardinality paths; do not keep `scan_exists` as a fallback for those cases.
- Modify `src/parseval/plan/planner.py`
  - Preserve existing group metadata.
  - Ensure direct and alias HAVING count predicates emit `required_rows`, `distinct`, and aggregate argument metadata.
  - Attach scalar subplans found inside aggregate expressions to the aggregate step.
- Modify `src/parseval/symbolic/evaluator.py`
  - Record one root-result observation per final output row.
  - Resolve scalar subqueries before replacing outer aggregate functions.
- Modify `src/parseval/symbolic/constraints.py`
  - Remove narrow `_multi_row_join_constraints()` and `_count_join_group_constraints()` patch helpers.
  - Add `_constraints_for_row_set_obligation()`.
  - Lower row-set scans, predicates, joins, group equality, counted non-null, and distinct inequalities through the shared path.
- Modify `tests/symbolic/test_operator_flow_paths.py`
  - Add/keep focused end-to-end regressions for root-result row counts, join LIMIT/OFFSET, HAVING count, HAVING distinct count, and scalar aggregate subquery evaluation.
- Modify `tests/symbolic/test_constraint_generation.py`
  - Add focused lowering tests for row-set obligations without requiring full engine generation.

## Current WIP Cleanup

The current working tree contains partial edits from the earlier patch attempt. Do not continue the `_multi_row_join_constraints()` approach.

Before implementing row-set lowering:

- Remove `ConstraintGenerator._multi_row_join_constraints()`.
- Remove `ConstraintGenerator._count_join_group_constraints()`.
- Remove the call to `_multi_row_join_constraints(path)` in `compile_path()`.
- Keep only changes that are part of the approved design:
  - root-result coverage threshold by true row count,
  - root-result evaluator records per final row,
  - scalar subquery aggregate scope fix,
  - planner HAVING direct aggregate metadata and aggregate scalar-subplan attachment,
  - tests, adjusted as needed for the row-set contract.

## Task 1: Define Row-Set Contract Tests

**Files:**
- Modify: `tests/symbolic/test_operator_flow_paths.py`
- Modify: `tests/symbolic/test_constraint_generation.py`

- [ ] **Step 1: Add root-result row-count evaluator tests**

In `tests/symbolic/test_operator_flow_paths.py`, keep or add:

```python
def test_limit_comma_offset_root_result_requires_all_surviving_rows():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 5, 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    for index in range(5):
        instance.create_row(
            "schools",
            values={
                "CDSCode": f"school-{index}",
                "Zip": f"old-{index}",
                "OpenDate": f"2020-01-0{index + 1}",
            },
        )
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)

    tree = PlanEvaluator(plan, instance).evaluate()
    root_node = next(node for node in tree.nodes if node.site == "root_result")

    assert root_node.observation_count(0, BranchType.ATOM_TRUE) == 0
    assert any(
        target.node is root_node
        and target.target_outcome == BranchType.ATOM_TRUE
        for target in tree.root_witness_targets
    )
```

Also keep or add:

```python
def test_root_result_records_one_observation_per_final_output_row():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 2"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    for index in range(2):
        instance.create_row(
            "schools",
            values={
                "CDSCode": f"school-{index}",
                "Zip": f"zip-{index}",
                "OpenDate": f"2020-01-0{index + 1}",
            },
        )
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)

    tree = PlanEvaluator(plan, instance).evaluate()
    root_node = next(node for node in tree.nodes if node.site == "root_result")

    assert root_node.observation_count(0, BranchType.ATOM_TRUE) == 2
```

- [ ] **Step 2: Add row-set obligation construction tests**

In `tests/symbolic/test_constraint_generation.py`, add:

```python
class TestRowSetObligations(unittest.TestCase):
    def test_limit_offset_join_root_target_has_row_set_obligation(self):
        schema = (
            "CREATE TABLE a (id INT PRIMARY KEY, score INT);"
            "CREATE TABLE b (id INT PRIMARY KEY, label TEXT);"
        )
        sql = (
            "SELECT b.label FROM a JOIN b ON a.id = b.id "
            "ORDER BY a.score DESC LIMIT 5, 1"
        )
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(t for t in tree.root_witness_targets if t.node.site == "root_result")
        row_sets = [o.row_set for o in target.node.obligations if o.kind == "row_set"]

        self.assertEqual(len(row_sets), 1)
        self.assertEqual(row_sets[0].required_rows, 6)
        self.assertEqual(row_sets[0].generation_rows, 6)
        self.assertEqual(len(row_sets[0].row_scopes), 6)
        self.assertTrue(row_sets[0].join_facts)
```

Add:

```python
    def test_large_offset_row_set_keeps_true_requirement_and_cap(self):
        schema = "CREATE TABLE schools (id INT PRIMARY KEY, zip TEXT, opened INT);"
        sql = "SELECT zip FROM schools ORDER BY opened DESC LIMIT 1 OFFSET 332"
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(t for t in tree.root_witness_targets if t.node.site == "root_result")
        row_set = next(o.row_set for o in target.node.obligations if o.kind == "row_set")

        self.assertEqual(row_set.required_rows, 333)
        self.assertEqual(row_set.generation_rows, 20)
```

Add:

```python
    def test_having_count_join_target_has_grouped_row_set_obligation(self):
        schema = (
            "CREATE TABLE events (event_id INT PRIMARY KEY, category TEXT);"
            "CREATE TABLE attendees (id INT PRIMARY KEY, link_to_event INT);"
        )
        sql = (
            "SELECT T1.category FROM events AS T1 "
            "JOIN attendees AS T2 ON T1.event_id = T2.link_to_event "
            "GROUP BY T1.category HAVING COUNT(T2.link_to_event) > 20"
        )
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(t for t in tree.root_witness_targets if t.node.site == "root_result")
        row_sets = [o.row_set for o in target.node.obligations if o.kind == "row_set"]

        self.assertTrue(any(rs.required_rows == 21 and rs.group_keys for rs in row_sets))
```

- [ ] **Step 3: Run tests and verify red**

Run:

```bash
pytest tests/symbolic/test_constraint_generation.py::TestRowSetObligations -q
pytest tests/symbolic/test_operator_flow_paths.py -q -k "limit_comma_offset_root_result_requires_all_surviving_rows or root_result_records_one_observation_per_final_output_row"
```

Expected before implementation:

- Row-set obligation tests fail because `OperatorObligation.row_set` and `RowSetObligation` do not exist.
- The per-final-row evaluator test may already pass if the WIP evaluator edit is present.

## Task 2: Add RowSetObligation Types

**Files:**
- Modify: `src/parseval/symbolic/types.py`

- [ ] **Step 1: Add dataclass**

In `src/parseval/symbolic/types.py`, add near `OperatorObligation`:

```python
@dataclass(frozen=True)
class RowSetObligation:
    """Logical upstream rows required to satisfy one operator target."""

    anchor_step_id: str
    required_rows: int
    generation_rows: int
    row_scopes: Tuple[str, ...]
    relations: Tuple[RelationId, ...] = ()
    join_facts: Tuple[JoinFact, ...] = ()
    path_predicates: Tuple[exp.Expression, ...] = ()
    group_keys: Tuple[ColumnId, ...] = ()
    counted_expression: Optional[exp.Expression] = None
    distinct_expression: Optional[exp.Expression] = None
    ordering: Tuple[exp.Expression, ...] = ()
```

- [ ] **Step 2: Extend OperatorObligation**

Update `OperatorObligation`:

```python
@dataclass(frozen=True)
class OperatorObligation:
    """An operator-level requirement for a generated witness row."""

    kind: str
    step_id: str
    site: str
    relation: Optional[RelationId] = None
    storage_relation: Optional[RelationId] = None
    columns: Tuple[ColumnId, ...] = ()
    row_scope: Optional[str] = None
    row_count: int = 1
    expression: Optional[exp.Expression] = None
    row_set: Optional[RowSetObligation] = None
```

- [ ] **Step 3: Run type import smoke test**

Run:

```bash
python - <<'PY'
from parseval.symbolic.types import RowSetObligation, OperatorObligation
print(RowSetObligation.__name__, OperatorObligation.__name__)
PY
```

Expected: prints `RowSetObligation OperatorObligation`.

## Task 3: Build Row-Set Obligations in BranchTree

**Files:**
- Modify: `src/parseval/symbolic/branch_tree.py`
- Modify: `src/parseval/symbolic/types.py`

- [ ] **Step 1: Import RowSetObligation**

In `src/parseval/symbolic/branch_tree.py`, add `RowSetObligation` to the import from `.types`.

- [ ] **Step 2: Add cap constant**

In `src/parseval/symbolic/branch_tree.py`, define:

```python
MAX_ROOT_GENERATION_ROWS = 20
```

- [ ] **Step 3: Add root required-row helper**

Inside `_build_branch_tree()`, replace the capped root helper with:

```python
def _root_required_row_count(root: Step) -> int:
    if isinstance(root, Limit):
        offset = max(int(getattr(root, "offset", 0) or 0), 0)
        limit = getattr(root, "limit", 1)
        limit_value = 1 if limit == float("inf") else max(int(limit or 0), 1)
        return max(offset + limit_value, 1)
    return 1
```

- [ ] **Step 4: Add row scopes helper**

Inside `_build_branch_tree()`, add:

```python
def _row_scopes(prefix: str, count: int) -> tuple[str, ...]:
    return tuple(f"{prefix}{index}" for index in range(max(count, 0)))
```

- [ ] **Step 5: Add upstream path helper for root**

Inside `_build_branch_tree()`, add:

```python
def _root_path_data(root: Step) -> tuple[tuple[exp.Expression, ...], tuple[JoinFact, ...]]:
    predicates: list[exp.Expression] = []
    join_facts: list[JoinFact] = []
    visited: set[int] = set()

    def walk(step: Step) -> None:
        if id(step) in visited:
            return
        visited.add(id(step))
        condition = getattr(step, "condition", None)
        if isinstance(condition, exp.Expression):
            predicates.append(condition)
        if isinstance(step, Join):
            join_facts.extend(_join_facts_for_step(plan, step))
        for dep in step.chain_dependencies:
            walk(dep)

    for dep in root.chain_dependencies:
        walk(dep)
    return tuple(predicates), tuple(join_facts)
```

- [ ] **Step 6: Build root row-set obligation**

In `_root_obligations(root)`, create a `row_set` obligation before scan obligations:

```python
row_count = _root_required_row_count(root)
generation_row_count = min(row_count, MAX_ROOT_GENERATION_ROWS)
root_relations = _canonical_relations(
    annotation.source_relations + _lineage_relations(root)
)
path_predicates, join_facts = _root_path_data(root)
root_row_set = RowSetObligation(
    anchor_step_id=annotation.step_id,
    required_rows=row_count,
    generation_rows=generation_row_count,
    row_scopes=_row_scopes("out", generation_row_count),
    relations=root_relations,
    join_facts=join_facts,
    path_predicates=path_predicates,
)
obligations = [
    OperatorObligation(
        kind="root_result",
        step_id=annotation.step_id,
        site="root_result",
        row_count=row_count,
    ),
    OperatorObligation(
        kind="row_set",
        step_id=annotation.step_id,
        site="root_result",
        row_count=generation_row_count,
        row_set=root_row_set,
    ),
]
```

Keep `_scan_obligations(... row_count=generation_row_count)` appended after these.

- [ ] **Step 7: Run row-set construction tests**

Run:

```bash
pytest tests/symbolic/test_constraint_generation.py::TestRowSetObligations::test_limit_offset_join_root_target_has_row_set_obligation -q
pytest tests/symbolic/test_constraint_generation.py::TestRowSetObligations::test_large_offset_row_set_keeps_true_requirement_and_cap -q
```

Expected: both pass.

## Task 4: Root Coverage Threshold and Evaluator Observations

**Files:**
- Modify: `src/parseval/symbolic/types.py`
- Modify: `src/parseval/symbolic/branch_tree.py`
- Modify: `src/parseval/symbolic/evaluator.py`

- [ ] **Step 1: Root target threshold uses root_result obligation**

In `BranchTree._target_specs_for_node()`, add:

```python
def root_result_row_count() -> int:
    counts = [
        obligation.row_count
        for obligation in node.obligations
        if obligation.kind == "root_result"
    ]
    return max(counts or [1])
```

When adding root-result `ATOM_TRUE`, use `root_result_row_count()` instead of `1`.

- [ ] **Step 2: Root witness targets use same threshold**

In `CoverageAnalyzer.root_witness_targets`, compute the same count:

```python
required = self._root_result_row_count(node)
if node.observation_count(0, BranchType.ATOM_TRUE) < required:
    targets.append(...)
```

Add helper:

```python
def _root_result_row_count(self, node: BranchNode) -> int:
    counts = [
        obligation.row_count
        for obligation in node.obligations
        if obligation.kind == "root_result"
    ]
    return max(counts or [1])
```

- [ ] **Step 3: Record every final output row**

In `PlanEvaluator._record_root_result()`, replace first-row-only observation with:

```python
for row in rows:
    self._observe(
        root_node,
        AtomObservation(
            atom_id=0,
            outcome=BranchType.ATOM_TRUE,
            row_ids=_row_ids(row),
        ),
    )
```

- [ ] **Step 4: Run root coverage tests**

Run:

```bash
pytest tests/symbolic/test_operator_flow_paths.py -q -k "limit_comma_offset_root_result_requires_all_surviving_rows or root_result_records_one_observation_per_final_output_row or large_limit_offset_root_obligation"
```

Expected: selected tests pass.

## Task 5: Planner HAVING and Scalar Aggregate Metadata

**Files:**
- Modify: `src/parseval/plan/planner.py`
- Test: `tests/plan/test_annotations.py`

- [ ] **Step 1: Preserve aggregate distinct metadata**

In `_aggregation_metadata()`, include:

```python
"distinct": _aggregate_is_distinct(aggregate_function),
```

Add helper near `_aggregate_argument_id()`:

```python
def _aggregate_is_distinct(expression: exp.AggFunc) -> bool:
    return isinstance(expression.this, exp.Distinct)
```

- [ ] **Step 2: Ensure direct HAVING aggregates produce constraints**

In `_having_constraints()`, keep alias replacement, but also allow direct aggregate comparisons:

```python
if not matched and _aggregate_comparison_constraint(rewritten) is None:
    continue
```

- [ ] **Step 3: Attach scalar subplans in aggregate expressions**

After `aggregate.add_dependency(step)`, attach subplans from aggregate expressions:

```python
for aggregation in aggregate.aggregations:
    if isinstance(aggregation, exp.Expression):
        _attach_subplans(aggregate, aggregation, ctes, correlations)
```

- [ ] **Step 4: Add planner metadata assertion**

In `tests/plan/test_annotations.py`, add:

```python
def test_count_distinct_having_metadata_marks_distinct_required_rows(self):
    plan = _plan(
        "SELECT dept FROM sales GROUP BY dept HAVING COUNT(DISTINCT employee_id) > 1",
        "CREATE TABLE sales (dept TEXT, employee_id INT);",
    )

    having = _first_step_of_type(plan, Having)
    constraints = plan.annotation_for(having).metadata["having_constraints"]

    self.assertEqual(constraints[0]["function"], "count")
    self.assertEqual(constraints[0]["required_rows"], 2)
```

Also assert aggregate output metadata includes `distinct` for the generated count output:

```python
aggregate = _first_step_of_type(plan, Aggregate)
outputs = plan.annotation_for(aggregate).metadata["aggregation"]["aggregate_outputs"]
count_output = next(item for item in outputs.values() if item["function"] == "count")
self.assertTrue(count_output["distinct"])
```

- [ ] **Step 5: Run planner annotation tests**

Run:

```bash
pytest tests/plan/test_annotations.py -q
```

Expected: pass.

## Task 6: HAVING Row-Set Obligations

**Files:**
- Modify: `src/parseval/symbolic/branch_tree.py`

- [ ] **Step 1: Add HAVING metadata lookup**

Inside `_build_branch_tree()`, add:

```python
def _having_cardinality_constraints(root: Step) -> tuple[dict[str, Any], ...]:
    constraints: list[dict[str, Any]] = []
    for step in plan.ordered_steps:
        if isinstance(step, Having):
            metadata = plan.annotation_for(step).metadata
            constraints.extend(metadata.get("having_constraints", ()))
    return tuple(constraints)
```

- [ ] **Step 2: Add group key lookup**

Inside `_build_branch_tree()`, add:

```python
def _aggregate_group_keys_for_having() -> tuple[ColumnId, ...]:
    for step in plan.ordered_steps:
        if isinstance(step, Aggregate):
            metadata = plan.annotation_for(step).metadata.get("aggregation", {})
            return tuple(metadata.get("group_keys", ()))
    return ()
```

- [ ] **Step 3: Add HAVING row-set obligations to root obligations**

In `_root_obligations(root)`, after root row-set creation, append for each HAVING count constraint:

```python
for index, constraint in enumerate(_having_cardinality_constraints(root)):
    required_rows = constraint.get("required_rows")
    if not isinstance(required_rows, int) or required_rows <= 0:
        continue
    counted = constraint.get("argument")
    counted_expression = (
        _column_expr_from_id(counted)
        if isinstance(counted, ColumnId)
        else None
    )
    row_set = RowSetObligation(
        anchor_step_id=annotation.step_id,
        required_rows=required_rows,
        generation_rows=min(required_rows, MAX_ROOT_GENERATION_ROWS),
        row_scopes=_row_scopes(f"having{index}_", min(required_rows, MAX_ROOT_GENERATION_ROWS)),
        relations=root_relations,
        join_facts=join_facts,
        path_predicates=path_predicates,
        group_keys=_aggregate_group_keys_for_having(),
        counted_expression=counted_expression,
        distinct_expression=(
            counted_expression.copy()
            if constraint.get("distinct") and counted_expression is not None
            else None
        ),
    )
    obligations.append(
        OperatorObligation(
            kind="row_set",
            step_id=annotation.step_id,
            site="having",
            row_count=row_set.generation_rows,
            row_set=row_set,
        )
    )
```

If `_column_expr_from_id` is not imported in `branch_tree.py`, add a small local helper:

```python
def _column_expr_from_id(column: ColumnId) -> exp.Column:
    relation = column.relation
    table_name = ""
    if relation is not None:
        visible = relation.alias or relation.name
        if visible is not None:
            table_name = visible.raw
    return exp.Column(
        this=exp.to_identifier(column.name.raw, quoted=column.name.quoted),
        table=exp.to_identifier(table_name) if table_name else None,
    )
```

- [ ] **Step 4: Run HAVING row-set construction test**

Run:

```bash
pytest tests/symbolic/test_constraint_generation.py::TestRowSetObligations::test_having_count_join_target_has_grouped_row_set_obligation -q
```

Expected: pass.

## Task 7: Lower Row-Set Obligations in ConstraintGenerator

**Files:**
- Modify: `src/parseval/symbolic/constraints.py`
- Test: `tests/symbolic/test_constraint_generation.py`

- [ ] **Step 1: Remove rejected patch helpers**

Remove:

```python
self._multi_row_join_constraints(path)
```

from `compile_path()`.

Delete these helper definitions if present:

```python
def _multi_row_join_constraints(...)
def _count_join_group_constraints(...)
```

- [ ] **Step 2: Call shared row-set lowering**

In `compile_path()`, after path predicate constraints and before DB constraints, add:

```python
constraints.extend(self._constraints_for_row_set_obligations(path.obligations))
```

- [ ] **Step 3: Add row-set lowering entrypoint**

Add to `ConstraintGenerator`:

```python
def _constraints_for_row_set_obligations(
    self,
    obligations: Tuple[OperatorObligation, ...],
) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    for obligation in obligations:
        if obligation.kind != "row_set" or obligation.row_set is None:
            continue
        constraints.extend(self._constraints_for_row_set(obligation.row_set))
    return constraints
```

- [ ] **Step 4: Add row-set lowering implementation**

Add:

```python
def _constraints_for_row_set(self, row_set: RowSetObligation) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    for row_scope in row_set.row_scopes:
        constraints.extend(self._row_set_scan_constraints(row_set, row_scope))
        constraints.extend(self._row_set_join_constraints(row_set, row_scope))
        constraints.extend(self._row_set_predicate_constraints(row_set, row_scope))
        if row_set.counted_expression is not None:
            counted = self._scoped_expression(
                row_set.counted_expression,
                row_set.relations,
                row_scope,
            )
            constraints.append(
                exp.Is(this=counted, expression=exp.Not(this=exp.Null()))
            )
    constraints.extend(self._row_set_group_constraints(row_set))
    constraints.extend(self._row_set_distinct_constraints(row_set))
    return constraints
```

- [ ] **Step 5: Add scan constraints per logical row**

Add:

```python
def _row_set_scan_constraints(
    self,
    row_set: RowSetObligation,
    row_scope: str,
) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    for relation in row_set.relations:
        columns = tuple(
            physical_column(name, relation, dialect=self.dialect)
            for name in self.instance.tables.get(_relation_table_name(relation), {})
        )
        if not columns:
            continue
        identity_col = columns[0]
        constraints.append(
            exp.Is(
                this=self._constraint_column(identity_col, row_scope=row_scope),
                expression=exp.Not(this=exp.Null()),
            )
        )
    return constraints
```

- [ ] **Step 6: Add join constraints per logical row**

Add:

```python
def _row_set_join_constraints(
    self,
    row_set: RowSetObligation,
    row_scope: str,
) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    for fact in row_set.join_facts:
        for left, right in fact.equalities:
            constraints.append(
                exp.EQ(
                    this=self._constraint_column(left, row_scope=row_scope),
                    expression=self._constraint_column(right, row_scope=row_scope),
                )
            )
    return constraints
```

- [ ] **Step 7: Add predicate constraints per logical row**

Add:

```python
def _row_set_predicate_constraints(
    self,
    row_set: RowSetObligation,
    row_scope: str,
) -> List[exp.Expression]:
    constraints: List[exp.Expression] = []
    for predicate in row_set.path_predicates:
        if predicate.find(exp.Subquery):
            continue
        scoped = self._scoped_expression(predicate, row_set.relations, row_scope)
        if not _is_trivial_true(scoped):
            constraints.append(scoped)
    return constraints
```

- [ ] **Step 8: Add group key equality constraints**

Add:

```python
def _row_set_group_constraints(
    self,
    row_set: RowSetObligation,
) -> List[exp.Expression]:
    if not row_set.group_keys or len(row_set.row_scopes) < 2:
        return []
    constraints: List[exp.Expression] = []
    first_scope = row_set.row_scopes[0]
    for row_scope in row_set.row_scopes[1:]:
        for group_key in row_set.group_keys:
            constraints.append(
                exp.EQ(
                    this=self._constraint_column(group_key, row_scope=row_scope),
                    expression=self._constraint_column(group_key, row_scope=first_scope),
                )
            )
    return constraints
```

- [ ] **Step 9: Add distinct expression constraints**

Add:

```python
def _row_set_distinct_constraints(
    self,
    row_set: RowSetObligation,
) -> List[exp.Expression]:
    if row_set.distinct_expression is None or len(row_set.row_scopes) < 2:
        return []
    constraints: List[exp.Expression] = []
    scoped_values = [
        self._scoped_expression(row_set.distinct_expression, row_set.relations, scope)
        for scope in row_set.row_scopes
    ]
    for left_index, left in enumerate(scoped_values):
        for right in scoped_values[left_index + 1:]:
            constraints.append(exp.NEQ(this=left.copy(), expression=right.copy()))
    return constraints
```

- [ ] **Step 10: Add constraint lowering assertion**

In `tests/symbolic/test_constraint_generation.py`, add:

```python
    def test_row_set_lowering_scopes_each_joined_output_row(self):
        schema = (
            "CREATE TABLE a (id INT PRIMARY KEY, score INT);"
            "CREATE TABLE b (id INT PRIMARY KEY, label TEXT);"
        )
        sql = (
            "SELECT b.label FROM a JOIN b ON a.id = b.id "
            "ORDER BY a.score DESC LIMIT 5, 1"
        )
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(t for t in tree.root_witness_targets if t.node.site == "root_result")

        constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
        scopes = {
            solver_var(column).row_scope
            for expression in constraint.constraints
            for column in expression.find_all(exp.Column)
            if solver_var(column) is not None
        }

        self.assertTrue({"out0", "out1", "out2", "out3", "out4", "out5"} <= scopes)
```

- [ ] **Step 11: Run constraint tests**

Run:

```bash
pytest tests/symbolic/test_constraint_generation.py -q
```

Expected: pass.

## Task 8: Scalar Aggregate Subquery Evaluation

**Files:**
- Modify: `src/parseval/symbolic/evaluator.py`
- Test: `tests/symbolic/test_operator_flow_paths.py`

- [ ] **Step 1: Add/keep scalar ratio regression**

In `tests/symbolic/test_operator_flow_paths.py`, keep or add:

```python
def test_scalar_subquery_count_ratio_evaluates_inner_aggregate_scope():
    schema = """
    CREATE TABLE posts (
      PostId INT PRIMARY KEY
    );
    CREATE TABLE users (
      UserId INT PRIMARY KEY
    );
    """
    sql = """
    SELECT COUNT(T1.PostId) * 1.0 / (SELECT COUNT(UserId) FROM users) AS ratio
    FROM posts AS T1
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    instance.create_row("posts", values={"PostId": 1})
    instance.create_row("users", values={"UserId": 10})
    instance.create_row("users", values={"UserId": 11})
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)

    output = PlanEvaluator(plan, instance).evaluate_context()

    assert len(next(iter(output.tables.values())).rows) == 1
```

- [ ] **Step 2: Resolve scalar subqueries before aggregate replacement**

In `_aggregate_expression_value()`, before `replace_aggregate`, add:

```python
aggregate_expr = aggregate.this if isinstance(aggregate, exp.Alias) else aggregate
if subplans and aggregate_expr.find(exp.Subquery):
    aggregate_expr = self._resolve_subquery_predicates(
        aggregate_expr,
        subplans,
        outer_bindings or {},
    )
```

Remove the later post-aggregate-replacement subquery resolution block.

- [ ] **Step 3: Match copied scalar subqueries safely**

In `_resolve_subquery_predicates()`, keep the metadata match and add SQL matching:

```python
scalar_values_by_sql: Dict[str, Any] = {}
...
anchor_sql = subplan.anchor.sql(dialect=self.dialect)
...
scalar_values_by_sql[anchor_sql] = scalar_values[key]
...
if isinstance(node, exp.Subquery):
    scalar_sql = node.sql(dialect=self.dialect)
    if scalar_sql in scalar_values_by_sql:
        return exp.convert(scalar_values_by_sql[scalar_sql])
```

- [ ] **Step 4: Run scalar regression**

Run:

```bash
pytest tests/symbolic/test_operator_flow_paths.py -q -k "scalar_subquery_count_ratio_evaluates_inner_aggregate_scope"
```

Expected: pass.

## Task 9: End-to-End Row-Set Generation Regressions

**Files:**
- Modify: `tests/symbolic/test_operator_flow_paths.py`

- [ ] **Step 1: Add LIMIT over join regression**

Use:

```python
def test_limit_comma_offset_join_engine_generates_required_final_rows():
    sql = """
    SELECT T2.MailStreet, T2.Zip
    FROM frpm AS T1
    INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
    ORDER BY T1.`FRPM Count (K-12)` DESC
    LIMIT 5, 1
    """
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")

    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 12
    assert len(next(iter(output.tables.values())).rows) == 1
```

- [ ] **Step 2: Add HAVING count over join regression**

Use the test from Task 1:

```python
def test_having_count_threshold_over_join_generates_same_group_rows():
    ...
    assert len(instance.get_rows("attendees")) >= 21
    assert len(next(iter(output.tables.values())).rows) == 1
```

- [ ] **Step 3: Add HAVING count distinct regression**

Use:

```python
def test_having_count_distinct_threshold_generates_distinct_group_values():
    ...
    assert len(event_ids) >= 2
    assert len(next(iter(output.tables.values())).rows) >= 1
```

- [ ] **Step 4: Run operator-flow tests**

Run:

```bash
pytest tests/symbolic/test_operator_flow_paths.py -q
```

Expected: pass.

## Task 10: BIRD Slice Verification

**Files:**
- No code changes unless this exposes a failing focused regression.

- [ ] **Step 1: Run focused suites**

Run:

```bash
pytest tests/symbolic/test_operator_flow_paths.py -q
pytest tests/symbolic/test_constraint_generation.py -q
```

Expected:

- `tests/symbolic/test_operator_flow_paths.py`: all tests pass.
- `tests/symbolic/test_constraint_generation.py`: all tests pass.

- [ ] **Step 2: Run BIRD diagnostic slice**

Run:

```bash
python - <<'PY'
import json
from parseval.instance import Instance
from parseval.symbolic import SymbolicEngine, CoverageThresholds
from tests.symbolic.test_symbolic_bird import _write_and_execute

indices = [50, 57, 614, 720, 1322, 1323, 1381, 1451]
with open("data/sqlite/dev.json") as f:
    dev = json.load(f)
with open("data/sqlite/schema.json") as f:
    schemas = json.load(f)

for i in indices:
    row = dev[i]
    db_id = row["db_id"]
    sql = row["SQL"]
    ddls = ";".join(schemas[db_id])
    try:
        instance = Instance(ddls=ddls, name=f"{db_id}_{i}", dialect="sqlite")
        engine = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=5)
        result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
        rows = _write_and_execute(instance, sql)
        print(
            f"{i}: db={db_id} generated={result.rows_generated} "
            f"coverage={result.coverage:.3f} sqlite_rows={len(rows)}"
        )
    except Exception as exc:
        print(f"{i}: db={db_id} ERROR {type(exc).__name__}: {str(exc)[:160]}")
PY
```

Expected:

- Index `614` must return `sqlite_rows >= 1`.
- Indices matching the focused row-set families (`50`, `720`, `1322`, `1323`, `1381`, `1451`) should improve or expose a new focused regression.
- Index `57` may remain capped/deferred because it requires 333 rows under the default cap, but it must not report full coverage with `sqlite_rows=0`.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
git diff -- src/parseval/symbolic/types.py src/parseval/symbolic/branch_tree.py src/parseval/symbolic/constraints.py src/parseval/symbolic/evaluator.py src/parseval/plan/planner.py tests/symbolic/test_operator_flow_paths.py tests/symbolic/test_constraint_generation.py
```

Expected:

- No `_multi_row_join_constraints`.
- No `_count_join_group_constraints`.
- Row-set lowering is centralized under `_constraints_for_row_set`.
- Existing user-owned changes outside these files are not reverted.

## Self-Review

Spec coverage:

- Root-result true row count and cap behavior: Tasks 1, 3, 4, 9, 10.
- HAVING direct and alias count cardinality: Tasks 5, 6, 7, 9.
- `COUNT(DISTINCT ...)` distinct values: Tasks 5, 6, 7, 9.
- Scalar aggregate subquery scope: Tasks 5 and 8.
- No standalone witness model and no `speculate` merge: all tasks keep changes in planner, branch-tree, evaluator, constraints, and tests.
- Rejected patch replacement: Current WIP Cleanup and Task 7.

Placeholder scan:

- No TBD/TODO placeholders.
- Every code-changing step names concrete files and code snippets.

Type consistency:

- `RowSetObligation.required_rows`, `generation_rows`, `row_scopes`, `relations`, `join_facts`, `path_predicates`, `group_keys`, `counted_expression`, and `distinct_expression` are used consistently.
- `OperatorObligation.row_set` is optional and only required for `kind == "row_set"`.
