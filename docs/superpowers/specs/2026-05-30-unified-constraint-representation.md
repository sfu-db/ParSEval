# Design Spec: Expression-Based Constraints in Speculate

## Problem

The Propagator extracts predicates from the plan into simplified `(col, op, value)` tuples and separate fields (`fixed_values`, `must_null`, `not_null`). This loses information and forces the Resolver to duplicate satisfaction logic that the solver already handles.

**Current flow:**
```
Plan → Propagator → (col, op, value) tuples → Resolver._satisfy() → heuristic values
```

**Desired flow:**
```
Plan → Propagator → List[exp.Expression] → Resolver → Solver → values
```

## Scope

**In scope:** Propagator + Resolver in `speculate.py`. How the Propagator builds constraint expressions. How the Resolver uses them.

**Out of scope:** ConstraintGenerator changes.

## Constraint Sources

A table's constraints come from three sources:

1. **Query predicates** — WHERE, JOIN ON, HAVING conditions from the plan
2. **Database schema** — NOT NULL, UNIQUE, FK constraints from `column_meta`
3. **Existing data** — values already in the instance (for UNIQUE avoidance, FK references)

All three become `List[exp.Expression]` on the `TableConstraint`.

## How to Build Constraint Expressions

### Source 1: Query Predicates

#### Filter Steps

Walk the WHERE condition. For AND: decompose into conjuncts. For each conjunct, resolve column table qualifiers through the alias map, then store the expression.

```python
# WHERE a.val > 5 AND a.name = 'test'
# Table 't1' (alias 'a') gets:
constraints = [
    exp.GT(this=Column(table='t1', name='val'), expression=Literal(5)),
    exp.EQ(this=Column(table='t1', name='name'), expression=Literal('test')),
]
```

**Negated predicates (negative branches):** Use `negate_predicate` from `plan.rex` to produce the negated expression.

```python
# NOT (a.val > 5) → a.val <= 5
negated = negate_predicate(condition)
constraints.append(negated)
```

**Self-join predicates:** Store per-alias expressions with resolved table qualifiers. Each alias gets its own `TableConstraint` with its own constraints.

```python
# WHERE T2.colour = 'Blue' AND T3.colour = 'Blond' (self-join on 'colour')
# Table 'colour__T2' gets: [colour = 'Blue']
# Table 'colour__T3' gets: [colour = 'Blond']
```

#### Join Steps

Extract the ON clause as expressions. Store on the joined table's constraints.

```python
# t1 JOIN t2 ON t1.id = t2.parent_id
# Table 't2' gets:
constraints = [
    exp.EQ(
        this=Column(table='t2', name='parent_id'),
        expression=Column(table='t1', name='id'),
    ),
]
```

Also add to `ColumnUnionFind` for equivalences (used by Resolver for coordination across tables).

#### Having Steps

Store the HAVING condition as expressions on the relevant tables.

```python
# HAVING COUNT(t2.id) > 3
# This is a group-level constraint, not a row-level constraint.
# Store on the counted table ('t2') as a min_rows hint:
min_rows = 4  # COUNT > 3 → need 4 rows
```

For per-row value constraints derived from aggregate thresholds (e.g., `HAVING AVG(t2.val) > 10`):

```python
# Store on 't2':
constraints.append(exp.GT(
    this=Column(table='t2', name='val'),
    expression=Literal(11),  # > 10 → 11
))
```

#### Aggregate Steps

GROUP BY columns are generation hints (`group_key_columns`), not data constraints.

Aggregate NULL detection: for columns inside COUNT/SUM/AVG, add IS NULL expression.

```python
# SELECT COUNT(name) FROM t
# Table 't' gets:
constraints.append(exp.Is(
    this=Column(table='t', name='name'),
    expression=exp.Null(),
))
```

#### SubPlan Steps

**EXISTS:** Add correlation predicate as expression.

```python
# WHERE EXISTS (SELECT * FROM t2 WHERE t2.t1_id = t1.id)
# Table 't1' gets correlation constraint:
constraints.append(exp.EQ(
    this=Column(table='t1', name='id'),
    expression=Column(table='t2', name='t1_id'),
))
```

**IN:** Link outer column to inner SELECT column.

```python
# WHERE t1.id IN (SELECT t2.t1_id FROM t2)
# Table 't1' gets:
constraints.append(exp.In(
    this=Column(table='t1', name='id'),
    expressions=[Column(table='t2', name='t1_id')],
))
```

**Scalar subquery:** Store for deferred evaluation (same as current).

### Source 2: Database Schema Constraints

For each column in the table, check `column_meta` and add constraints:

```python
for col_name, col_type in instance.tables[table].items():
    # NOT NULL
    if not instance.nullable(table, col_name):
        constraints.append(exp.Is(
            this=exp.Column(this=exp.to_identifier(col_name)),
            expression=exp.Null(),
        ).not_())

    # UNIQUE
    if instance.is_unique(table, col_name):
        existing = {s.concrete for s in instance.get_column_data(table, col_name) if s.concrete is not None}
        if existing:
            constraints.append(exp.Not(this=exp.In(
                this=exp.Column(this=exp.to_identifier(col_name)),
                expressions=[exp.Literal(v) for v in existing],
            )))

    # FK
    for fk in instance.get_foreign_key(table):
        local_col = fk.expressions[0].name
        ref_table = fk.args.get("reference").find(exp.Table).name
        ref_col = instance.resolve_fk_ref_column(fk)
        parent_rows = instance.get_rows(ref_table)
        if parent_rows:
            parent_vals = [r[ref_col].concrete for r in parent_rows if r[ref_col].concrete is not None]
            if parent_vals:
                constraints.append(exp.In(
                    this=exp.Column(this=exp.to_identifier(local_col)),
                    expressions=[exp.Literal(v) for v in parent_vals],
                ))
```

### Source 3: Existing Data Context

For tables that already have rows, the solver may need to reference existing values. This is handled by the UNIQUE and FK constraints above (they reference existing values).

For JOIN coordination: the solver needs to know the current values of join key columns from the other table. This is handled by the equivalences → `join_equalities` conversion.

## TableConstraint

```python
@dataclass
class TableConstraint:
    """Constraints on what one table needs for a specific branch."""
    table: str
    alias: Optional[str] = None
    # All constraints as sqlglot expressions.
    constraints: List[exp.Expression] = field(default_factory=list)
    # Generation hints.
    min_rows: int = 1
    duplicate_columns: List[str] = field(default_factory=list)
    group_key_columns: List[str] = field(default_factory=list)
```

**Removed:** `fixed_values`, `must_null`, `not_null`, `predicates`.

## SolverConstraint Redesign

```python
@dataclass
class SolverConstraint:
    """Constraints for the solver to satisfy."""
    target_tables: Tuple[str, ...]
    target_outcome: BranchType
    constraints: List[exp.Expression] = field(default_factory=list)
    join_equalities: List[Tuple[str, str, str, str]] = field(default_factory=list)
    alias_map: Dict[str, str] = field(default_factory=dict)
    atom: Optional[exp.Expression] = None  # Phase 2 only
```

**Removed:** `path_predicates`, `null_columns`, `not_null_columns`, `avoid_values`, `foreign_keys`. All encoded as expressions in `constraints`.

**Kept separate:** `join_equalities` — solver needs special cross-table coordination (shared Z3 variables, value propagation).

## Revised Speculate Function

### Propagator

The Propagator walks the plan top-down and builds `TableConstraint` for each table in each branch.

```python
class Propagator:
    def propagate(self) -> List[BranchSpec]:
        specs = []
        # Positive branch
        pos = BranchSpec(branch="positive")
        self._propagate_step(self.plan.root, pos)
        self._add_schema_constraints(pos)  # NOT NULL, UNIQUE, FK
        specs.append(pos)
        # Negative branches per decision site
        for step in self.plan.ordered_steps:
            if isinstance(step, Filter) and step.condition:
                neg = BranchSpec(branch="negative")
                self._propagate_step(self.plan.root, neg, negate_step=step)
                self._add_schema_constraints(neg)
                specs.append(neg)
            # ... (left_unmatched, having_fail same as current)
        return specs

    def _propagate_step(self, step, spec, negate_step=None):
        if isinstance(step, Filter):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition:
                if step is negate_step:
                    expr = negate_predicate(step.condition)
                else:
                    expr = step.condition
                # Decompose AND into conjuncts, resolve tables, store as expressions.
                for conjunct in self._split_conjuncts(expr):
                    resolved = self._resolve_columns(conjunct)
                    table = self._find_table_for_expr(resolved)
                    if table:
                        spec.require(table).constraints.append(resolved)

        elif isinstance(step, Join):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            for join_name, join_data in (step.joins or {}).items():
                join_table = self._resolve_table(join_name)
                for sk, jk in zip(join_data["source_key"], join_data["join_key"]):
                    # Add join equality as expression on the joined table
                    join_expr = exp.EQ(
                        this=self._make_column(join_table, jk),
                        expression=self._make_column(source_table, sk),
                    )
                    spec.require(join_table).constraints.append(join_expr)
                    # Also add to Union-Find for coordination
                    spec.equate(f"{source_table}.{sk_col}", f"{join_table}.{jk_col}")

        elif isinstance(step, Having):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.condition and step is not negate_step:
                # Store HAVING condition as expression
                resolved = self._resolve_columns(step.condition)
                for table in spec.requirements:
                    if self._expr_references_table(resolved, table):
                        spec.require(table).constraints.append(resolved)
                # min_group_size is a generation hint
                min_size = self._extract_min_group_size(step.condition)
                counted = self._find_counted_table(step.condition)
                if counted and counted in spec.requirements:
                    spec.require(counted).min_rows = max(
                        spec.require(counted).min_rows, min_size
                    )

        elif isinstance(step, Aggregate):
            for dep in step.chain_dependencies:
                self._propagate_step(dep, spec, negate_step)
            if step.group:
                for group_expr in step.group.values():
                    for col in group_expr.find_all(exp.Column):
                        table = self._resolve_table(col.table or "")
                        matched = self._match_column(table, col.name)
                        if matched:
                            spec.require(table).group_key_columns.append(matched)
            # Aggregate NULL: add IS NULL expression for COUNT/SUM/AVG columns
            for agg_expr in step.aggregations:
                self._add_aggregate_null_constraints(agg_expr, spec)

        elif isinstance(step, Scan):
            table = self._resolve_table(step.name)
            if table in self.instance.tables:
                spec.require(table)

        # SubPlan handling (EXISTS, IN, scalar) — same as current
        for sub in step.subplan_dependencies:
            self._propagate_subplan(sub, spec)

    def _add_schema_constraints(self, spec: BranchSpec):
        """Add NOT NULL, UNIQUE, FK constraints as expressions."""
        for table, req in spec.requirements.items():
            if table not in self.instance.tables:
                continue
            for col_name, col_type in self.instance.tables[table].items():
                col = exp.Column(this=exp.to_identifier(col_name),
                                 table=exp.to_identifier(table))
                # NOT NULL
                if not self.instance.nullable(table, col_name):
                    req.constraints.append(
                        exp.Is(this=col, expression=exp.Null()).not_()
                    )
                # UNIQUE
                if self.instance.is_unique(table, col_name):
                    existing = {s.concrete for s in self.instance.get_column_data(table, col_name)
                                if s.concrete is not None}
                    if existing:
                        req.constraints.append(
                            exp.Not(this=exp.In(
                                this=col,
                                expressions=[exp.Literal(v) for v in existing],
                            ))
                        )
            # FK
            for fk in self.instance.get_foreign_key(table):
                local_col = normalize_name(fk.expressions[0].name)
                ref = fk.args.get("reference")
                if ref:
                    ref_table = normalize_name(ref.find(exp.Table).name)
                    ref_col = self.instance.resolve_fk_ref_column(fk)
                    parent_rows = self.instance.get_rows(ref_table)
                    if parent_rows and ref_col:
                        parent_vals = [r[ref_col].concrete for r in parent_rows
                                       if r[ref_col].concrete is not None]
                        if parent_vals:
                            req.constraints.append(exp.In(
                                this=exp.Column(this=exp.to_identifier(local_col),
                                                table=exp.to_identifier(table)),
                                expressions=[exp.Literal(v) for v in parent_vals],
                            ))

    def _add_aggregate_null_constraints(self, agg_expr, spec):
        """Add IS NULL for columns inside COUNT/SUM/AVG."""
        for count_node in agg_expr.find_all(exp.Count):
            if isinstance(count_node.this, exp.Star) or count_node.args.get("distinct"):
                continue
            for col in count_node.find_all(exp.Column):
                table = self._resolve_table(col.table or "")
                matched = self._match_column(table, col.name)
                if matched and table in self.instance.tables:
                    req = spec.require(table)
                    null_expr = exp.Is(
                        this=exp.Column(this=exp.to_identifier(matched),
                                        table=exp.to_identifier(table)),
                        expression=exp.Null(),
                    )
                    # Only add if not conflicting with a fixed value constraint
                    if not self._has_equality_constraint(req.constraints, matched):
                        req.constraints.append(null_expr)
                        req.min_rows = max(req.min_rows, 2)
        # Similar for SUM, AVG
```

### Resolver

The Resolver packages constraints into `SolverConstraint` and calls the solver.

```python
class Resolver:
    def __init__(self, instance, dialect="sqlite", *, solver=None):
        self.instance = instance
        self.dialect = dialect
        self.solver = solver

    def resolve(self, spec: BranchSpec) -> Dict[str, List[Dict[str, Any]]]:
        self._discover_fk_parents(spec)
        join_equalities = self._equivalences_to_join_equalities(spec)
        order = self._creation_order(spec)
        result = {}

        for table_key in order:
            if table_key not in spec.requirements:
                continue
            req = spec.requirements[table_key]
            physical = req.table if not req.alias else req.table
            if "__" in physical:
                physical = physical.split("__")[0]

            for i in range(req.min_rows):
                row = self._solve_row(physical, req, spec, join_equalities, result)
                if row:
                    # Apply duplicate_columns: copy from base row
                    if req.duplicate_columns and i > 0 and physical in result:
                        base = result[physical][0]
                        for col in req.duplicate_columns:
                            if col in base:
                                row[col] = base[col]
                    # Apply group_key_columns: share across rows
                    if req.group_key_columns and i > 0 and physical in result:
                        base = result[physical][0]
                        for col in req.group_key_columns:
                            if col in base:
                                row[col] = base[col]
                    result.setdefault(physical, []).append(row)

        # Deferred scalar subqueries
        if spec.deferred:
            self._resolve_deferred(spec, result)

        return result

    def _solve_row(self, table, req, spec, join_equalities, result):
        """Call the solver to produce one row."""
        # Merge constraints from other tables' solved values
        all_constraints = list(req.constraints)
        for other_table, rows in result.items():
            if rows:
                for col, val in rows[0].items():
                    # Add as EQ constraint for cross-table coordination
                    all_constraints.append(exp.EQ(
                        this=exp.Column(this=exp.to_identifier(col),
                                        table=exp.to_identifier(other_table)),
                        expression=exp.Literal(val) if val is not None else exp.Null(),
                    ))

        constraint = SolverConstraint(
            target_tables=(table,),
            target_outcome=BranchType.ATOM_TRUE,
            constraints=all_constraints,
            join_equalities=join_equalities,
            alias_map=spec.equivalences,  # or extract alias_map from somewhere
        )
        result = self.solver.solve(constraint)
        if result.sat:
            return result.assignments.get(table, {})
        return {}

    def _equivalences_to_join_equalities(self, spec):
        """Convert ColumnUnionFind groups to join_equalities tuples."""
        equalities = []
        for rep, members in spec.equivalences.groups().items():
            if len(members) >= 2:
                for i in range(len(members) - 1):
                    t1, c1 = members[i].split(".", 1)
                    t2, c2 = members[i + 1].split(".", 1)
                    equalities.append((t1, c1, t2, c2))
        return equalities
```

### What's Removed from Resolver

- `_satisfy` / `_satisfy_all` — solver handles satisfaction
- `_default_value` — solver generates defaults
- `_validate_row` — solver guarantees validity
- `_build_row` — replaced by `_solve_row`

### What's Removed from Propagator

- `_extract_predicates` — replaced by direct expression extraction
- `_extract_negated_predicates` — replaced by `negate_predicate` + expression storage
- `_extract_column_equalities` — join equalities stored as expressions
- `_extract_having_value_constraints` — HAVING stored as expression
- `_extract_min_group_size` — kept (generation hint)
- `_extract_self_join_predicates` — replaced by per-alias expression storage

## Example: End-to-End

Query: `SELECT * FROM t1 JOIN t2 ON t1.id = t2.t1_id WHERE t1.val > 5 AND t2.name = 'test'`

Schema: `t1(id INT UNIQUE, val INT)`, `t2(id INT, t1_id INT REFERENCES t1(id), name TEXT)`

Existing data: `t1` has rows with `id = 1, 2, 3`.

**Propagator extracts for `t1`:**

```python
constraints = [
    # Query predicate
    exp.GT(this=Column('t1', 'val'), expression=Literal(5)),
    # Schema: id is UNIQUE, avoid existing values
    exp.Not(this=exp.In(
        this=Column('t1', 'id'),
        expressions=[Literal(1), Literal(2), Literal(3)],
    )),
]
```

**Propagator extracts for `t2`:**

```python
constraints = [
    # Query predicate
    exp.EQ(this=Column('t2', 'name'), expression=Literal('test')),
    # Join predicate
    exp.EQ(this=Column('t2', 't1_id'), expression=Column('t1', 'id')),
    # Schema: FK to t1.id
    exp.In(
        this=Column('t2', 't1_id'),
        expressions=[Literal(1), Literal(2), Literal(3)],
    ),
]
```

**Resolver builds SolverConstraint for `t1`:**

```python
SolverConstraint(
    target_tables=('t1',),
    target_outcome=BranchType.ATOM_TRUE,
    constraints=[
        GT(Column('t1', 'val'), Literal(5)),           # query predicate
        NOT IN(Column('t1', 'id'), [1, 2, 3]),         # UNIQUE avoidance
        IS NOT NULL(Column('t1', 'id')),                # NOT NULL schema
    ],
    join_equalities=[],
)
```

Solver: `t1.id = 4, t1.val = 6`.

**Resolver builds SolverConstraint for `t2`:**

```python
SolverConstraint(
    target_tables=('t2',),
    target_outcome=BranchType.ATOM_TRUE,
    constraints=[
        EQ(Column('t2', 'name'), Literal('test')),     # query predicate
        EQ(Column('t2', 't1_id'), Column('t1', 'id')), # join predicate
        IN(Column('t2', 't1_id'), [1, 2, 3]),          # FK reference
        IS NOT NULL(Column('t2', 'id')),                # NOT NULL schema
    ],
    join_equalities=[('t1', 'id', 't2', 't1_id')],
)
```

Solver: `t2.id = 1, t2.t1_id = 1, t2.name = 'test'`.

## Implementation Steps

### Step 1: Redesign SolverConstraint

Change `SolverConstraint` to use `constraints: List[exp.Expression]` instead of separate fields. Update the solver's `solve()` method to extract fixed_values/predicates from the unified constraints list. Keep `join_equalities` separate.

### Step 2: Add `constraints` field to TableRequirement

Add `constraints: List[exp.Expression]` alongside existing fields. Backward-compatible.

### Step 3: Populate `constraints` in Propagator

Change `_extract_predicates` to also append expressions to `constraints`. Keep populating old fields for backward compatibility.

### Step 4: Add schema constraints to Propagator

Add `_add_schema_constraints` method that adds NOT NULL, UNIQUE, FK as expressions. Call after `_propagate_step`.

### Step 5: Add solver to Resolver

Pass solver instance to Resolver. Add `_solve_row` method that builds `SolverConstraint` from `constraints` + `join_equalities` and calls solver.

### Step 6: Migrate Propagator to use only `constraints`

Stop populating `fixed_values`, `must_null`, `not_null`, `predicates`. Use only `constraints`.

### Step 7: Remove old fields and methods

Remove `fixed_values`, `must_null`, `not_null`, `predicates` from `TableRequirement`. Remove `_satisfy`, `_satisfy_all`, `_default_value`, `_validate_row`, `_build_row` from Resolver. Remove `_extract_predicates` and related helpers from Propagator.

## Verification

```
.venv/bin/python3 -m unittest discover tests/symbolic/ -v
```

After each step. BIRD benchmark regression gate.

## Files Modified

- `src/parseval/symbolic/speculate.py` — Propagator + Resolver + TableConstraint
- `tests/symbolic/` — update tests
