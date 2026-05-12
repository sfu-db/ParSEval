# UExprToConstraint — Revised Design

## Location

```
src/parseval/symbolic/uexpr.py
```

Part of the symbolic module — it reasons about query semantics to build
constraints, sitting between the evaluator (which finds coverage gaps)
and the SMT solver (which finds satisfying values).

---

## Core Principle: Work Directly on Instance

No `Witness` or `WitnessRow` classes. `UExprToConstraint` operates on the
Instance directly:

1. **Creates rows** in the Instance (via `create_row`) with unbound Variables
2. **Builds Z3 constraints** over those Variables + existing row values
3. **Solves** and writes concrete values back into the Variables
4. If UNSAT, rolls back via `instance.checkpoint()`/`rollback()`

```python
class UExprToConstraint:
    def __init__(self, plan: Plan, instance: Instance, dialect: str = "sqlite"):
        self.plan = plan
        self.instance = instance
        self.dialect = dialect
        self.alias_map = _build_alias_map(plan)

    def solve_uncovered(self, target: CoverageTarget) -> bool:
        """Solve a specific uncovered branch target.

        Creates rows if needed, builds constraints from the plan context,
        invokes Z3, and updates Variable concrete values in-place.
        Returns True if the target was satisfied.
        """
        ...

    def ensure_nonempty(self) -> bool:
        """Ensure the query returns non-empty results.

        Builds constraints from the full WHERE + JOIN + HAVING,
        considering existing rows. Creates additional rows only if needed.
        Returns True if successful.
        """
        ...
```

---

## Integration Point

```python
# In engine.py generate(), the coverage loop becomes:

for iteration in range(self.max_iterations):
    if tree.fully_covered:
        break

    target = self._prioritize(tree.uncovered_targets)

    # Quick infeasibility check
    if is_infeasible(target.node, target.atom_id, target.target_outcome, self.instance):
        tree.mark_infeasible(...)
        continue

    # Try UExprToConstraint (replaces ConstraintGenerator + Solver)
    cp = self.instance.checkpoint()
    uexpr = UExprToConstraint(self.plan, self.instance, self.dialect)
    success = uexpr.solve_uncovered(target)

    if success:
        tree = evaluator.evaluate(tree)
    else:
        self.instance.rollback(cp)
        tree.mark_infeasible(...)
```

And after the coverage loop:

```python
# Post-loop: ensure non-empty results
if not self._query_returns_nonempty():
    cp = self.instance.checkpoint()
    uexpr = UExprToConstraint(self.plan, self.instance, self.dialect)
    if not uexpr.ensure_nonempty():
        self.instance.rollback(cp)
```

---

## Design: Respecting Existing Instance State

### Existing rows are constants

When building Z3 constraints, existing rows' concrete values become Z3
constants (not variables). Only newly-created or unbound Variables become
Z3 variables to solve for.

```python
def _encode_existing_rows(self, table: str) -> List[Dict[str, z3.ExprRef]]:
    """Encode existing rows as Z3 constants."""
    rows = self.instance.get_rows(table)
    encoded = []
    for row in rows:
        row_consts = {}
        for col, sym in row.items():
            if sym.concrete is not None:
                row_consts[col] = self._to_z3_const(sym.concrete, table, col)
            else:
                row_consts[col] = self._to_z3_null(table, col)
        encoded.append(row_consts)
    return encoded
```

### New rows have Z3 variables

```python
def _create_solvable_row(self, table: str, hint_values: Dict[str, Any] = None) -> Dict[str, z3.ExprRef]:
    """Create a new row in the Instance and return Z3 variables for its columns.

    The row is created with unbound Variables. Z3 will determine their
    concrete values. hint_values pre-fills columns we already know.
    """
    row_result = self.instance.create_row(table, values=hint_values or {})
    row_idx = row_result.positions[table]
    row = self.instance.get_rows(table)[row_idx]

    z3_vars = {}
    for col, sym in row.items():
        if sym.concrete is not None:
            # Already bound (from hint or FK fill) — treat as constant
            z3_vars[col] = self._to_z3_const(sym.concrete, table, col)
        else:
            # Unbound — declare Z3 variable
            z3_vars[col] = self._declare_variable(f"{table}[{row_idx}].{col}", table, col)
    return z3_vars
```

### Schema constraints from Instance

```python
def _add_schema_constraints(self, table: str, z3_row: Dict[str, z3.ExprRef]):
    """Add NOT NULL, UNIQUE, FK constraints for a row."""
    # NOT NULL
    for col, var in z3_row.items():
        if not self.instance.nullable(table, col):
            self.solver.add(var != self.NULL_SENTINEL)

    # UNIQUE: new value must differ from all existing values
    for col, var in z3_row.items():
        if self.instance.is_unique(table, col):
            existing = [s.concrete for s in self.instance.get_column_data(table, col)
                        if s.concrete is not None]
            for ev in existing:
                self.solver.add(var != self._to_z3_const(ev, table, col))

    # FK: value must exist in parent table
    for fk in self.instance.get_foreign_key(table):
        local_col = fk.expressions[0].name
        ref_col = self.instance.resolve_fk_ref_column(fk)
        ref_table = ...
        parent_values = [s.concrete for s in self.instance.get_column_data(ref_table, ref_col)
                         if s.concrete is not None]
        if parent_values and local_col in z3_row:
            # FK must be one of the existing parent values
            self.solver.add(z3.Or(*[z3_row[local_col] == self._to_z3_const(v, table, local_col)
                                     for v in parent_values]))
```

---

## Coverage-Driven Constraint Building

### For `solve_uncovered(target)`:

The target tells us:
- **Which atom** needs to be satisfied (the predicate expression)
- **Which outcome** we want (TRUE, FALSE, or NULL)
- **Which step** it belongs to (Filter, Join, Having)
- **Which tables** are involved

The constraint building:

```python
def solve_uncovered(self, target: CoverageTarget) -> bool:
    atom = target.atom
    outcome = target.target_outcome
    node = target.node
    tables = node.tables

    # 1. Determine which tables need new rows vs. can reuse existing
    tables_needing_rows = self._identify_tables_needing_rows(target)

    # 2. Create rows (with unbound Variables) for tables that need them
    z3_rows = {}
    for table in tables_needing_rows:
        z3_rows[table] = self._create_solvable_row(table)

    # 3. Build the target constraint
    if outcome == BranchType.ATOM_TRUE:
        self._assert_predicate(atom, z3_rows, positive=True)
    elif outcome == BranchType.ATOM_FALSE:
        self._assert_predicate(atom, z3_rows, positive=False)
    elif outcome == BranchType.ATOM_NULL:
        self._assert_null_propagation(atom, z3_rows)

    # 4. Add path constraints (upstream WHERE/JOIN that must hold)
    for path_pred in self._collect_path_predicates(target):
        self._assert_predicate(path_pred, z3_rows, positive=True)

    # 5. Add schema constraints
    for table, row_vars in z3_rows.items():
        self._add_schema_constraints(table, row_vars)

    # 6. Solve
    if self.solver.check() == z3.sat:
        model = self.solver.model()
        self._apply_model(model, z3_rows)
        return True
    return False
```

### For `ensure_nonempty()`:

This handles the "query returns empty" case — typically HAVING COUNT,
NOT IN, self-JOINs, scalar subqueries.

```python
def ensure_nonempty(self) -> bool:
    # 1. Analyze the plan to determine what's needed
    requirements = self._analyze_nonempty_requirements()

    # 2. Create rows as needed
    z3_rows = {}  # table → [row_vars_0, row_vars_1, ...]
    for table, count in requirements.row_counts.items():
        existing = len(self.instance.get_rows(table))
        for i in range(count - existing):
            row_vars = self._create_solvable_row(table)
            z3_rows.setdefault(table, []).append(row_vars)

    # 3. Add all query constraints
    self._assert_where_constraints(z3_rows)
    self._assert_join_constraints(z3_rows)
    self._assert_having_constraints(z3_rows)
    self._assert_subplan_constraints(z3_rows)

    # 4. Add schema constraints for all new rows
    for table, row_list in z3_rows.items():
        for row_vars in row_list:
            self._add_schema_constraints(table, row_vars)

    # 5. Solve and apply
    if self.solver.check() == z3.sat:
        self._apply_model(self.solver.model(), z3_rows)
        return True
    return False
```

---

## Using `concrete()` for Evaluation

The `concrete()` function from `rex.py` is used to:

1. **Check if existing rows already satisfy a predicate** (before invoking Z3)
2. **Evaluate subqueries** against current instance data to get scalar values
3. **Verify the solution** after Z3 assigns values

```python
def _existing_rows_satisfy(self, predicate: exp.Expression, tables: Tuple[str, ...]) -> bool:
    """Check if any existing row combination satisfies the predicate."""
    from parseval.plan.rex import concrete, Environment

    for table in tables:
        for row in self.instance.get_rows(table):
            env = Environment({col: sym.concrete for col, sym in row.items()})
            if concrete(predicate, env) is True:
                return True
    return False
```

---

## Handling Multi-Row Requirements (HAVING COUNT)

For `HAVING COUNT(T2.col) > N`:

```python
def _assert_having_constraints(self, z3_rows):
    for step in self.plan.ordered_steps:
        if not isinstance(step, Having):
            continue
        # Find the Aggregate step's actual condition
        agg_step = self._find_aggregate_step()
        count_threshold = self._extract_count_threshold(agg_step)
        if count_threshold is None:
            continue

        # The counted table's rows must all share the same JOIN key
        counted_table = self._identify_counted_table(agg_step)
        if counted_table and counted_table in z3_rows:
            rows = z3_rows[counted_table]
            if len(rows) >= 2:
                # All rows share the same JOIN key
                join_col = self._find_join_key(counted_table)
                if join_col:
                    for row in rows[1:]:
                        self.solver.add(row[join_col] == rows[0][join_col])
```

---

## Handling NOT IN

```python
def _assert_not_in_constraint(self, sub: SubPlan, z3_rows):
    """For NOT IN: outer value must not appear in inner result."""
    anchor = sub.anchor  # exp.In node (negated)
    outer_col = anchor.this  # the outer column

    # Get the outer row's Z3 variable
    outer_table = self._resolve_table(outer_col)
    outer_var = z3_rows[outer_table][0][outer_col.name]

    # Get all existing values from the inner query's result
    inner_values = self._evaluate_inner_query(sub)

    # Assert outer value ≠ each inner value
    for val in inner_values:
        self.solver.add(outer_var != self._to_z3_const(val, outer_table, outer_col.name))

    # If inner query has no rows yet, no constraint needed (NOT IN is vacuously true)
```

---

## Handling Self-JOINs

```python
def _assert_join_constraints(self, z3_rows):
    for step in self.plan.ordered_steps:
        if not isinstance(step, Join):
            continue
        for join_name, join_data in step.joins.items():
            source_table = self._resolve_alias(step.source_name)
            join_table = self._resolve_alias(join_name)

            # Link join keys
            for sk, jk in zip(join_data["source_key"], join_data["join_key"]):
                src_var = self._get_var(source_table, sk.name, z3_rows)
                jk_var = self._get_var(join_table, jk.name, z3_rows)
                if src_var is not None and jk_var is not None:
                    self.solver.add(src_var == jk_var)

            # Self-join: if two aliases map to same physical table,
            # their PK values must differ (they're different rows)
            if source_table == join_table:
                # This is handled by UNIQUE constraints on PK
                pass
            elif self.alias_map.get(step.source_name) == self.alias_map.get(join_name):
                # Same physical table, different aliases → different rows
                pk_col = self._get_pk_column(self.alias_map[join_name])
                if pk_col:
                    src_pk = self._get_var(source_table, pk_col, z3_rows)
                    jk_pk = self._get_var(join_table, pk_col, z3_rows)
                    if src_pk is not None and jk_pk is not None:
                        self.solver.add(src_pk != jk_pk)
```

---

## Applying the Z3 Model Back to Instance

```python
def _apply_model(self, model: z3.ModelRef, z3_rows: Dict):
    """Write Z3 solution back into Instance Variables."""
    for table, row_list in z3_rows.items():
        rows_in_instance = self.instance.get_rows(table)
        for row_vars in (row_list if isinstance(row_list, list) else [row_list]):
            # Find the corresponding Instance row by matching the Z3 var names
            for col, z3_var in row_vars.items():
                if not z3.is_const(z3_var):  # Only update Z3 variables, not constants
                    z3_val = model.evaluate(z3_var, model_completion=True)
                    python_val = self._z3_to_python(z3_val, table, col)
                    # Find the Variable in the Instance and update it
                    self._update_variable(table, col, z3_var, python_val)
```

---

## Summary: How It Differs from Current Approach

| Aspect | Current (ConstraintGenerator + Solver) | UExprToConstraint |
|--------|---------------------------------------|-------------------|
| Scope | Per-atom, one predicate at a time | Full query context |
| Rows | Solver produces values, engine materializes | Creates rows in Instance, Z3 fills values |
| Existing data | Ignored (FK fixup after) | Constants in Z3 formula |
| Multi-row | Can't coordinate | Declares N rows, links them |
| Self-JOIN | Doesn't detect | Separate vars per alias |
| NOT IN | Can't handle | Anti-value constraints |
| HAVING COUNT | Only sets min_rows heuristic | Declares exact row count, coordinates keys |
| Invocation | Every uncovered branch | Uncovered branches + empty-result fallback |

---

## Implementation Order

1. **Core class** + `solve_uncovered` for simple Filter atoms (replaces current solver path)
2. **JOIN constraint encoding** (including self-join detection)
3. **`ensure_nonempty`** with HAVING COUNT multi-row coordination
4. **NOT IN / IN subplan** constraint encoding
5. **Scalar subquery** evaluation + constraint
6. **Integration** into engine.py (replace ConstraintGenerator usage)
7. **Tests** against the 21 remaining Bird failures
