# Coverage Model Improvement Design

**Date:** 2026-05-28
**Status:** Draft
**Scope:** Incremental refactor of ParSEval's branch coverage tracking and constraint generation

---

## Problem Statement

ParSEval's evaluator and constraint generator have gaps in branch type coverage. The system tracks filter/join/having atoms well, but misses SubPlan branches (EXISTS, IN), DISTINCT branches, and has incomplete constraint generation for GROUP and CASE arms.

### Current Coverage Gaps

| Branch Type | Evaluator Tracks | ConstraintGenerator Handles |
|---|---|---|
| Filter atoms (ATOM_TRUE/FALSE/NULL) | Yes | Yes |
| Join atoms | Yes | Yes |
| Having atoms | Yes | Yes |
| CASE arms | Yes | Partial |
| GROUP_SINGLE/MULTI | Yes | No |
| EXISTS_TRUE/FALSE | No | No |
| IN_MATCH/NO_MATCH | No | No |
| DISTINCT_UNIQUE/DUPLICATE | No | No |

### Consequences

- **EXISTS/IN queries** — the system cannot generate data that exercises both EXISTS_TRUE and EXISTS_FALSE branches, leading to incomplete coverage for correlated subqueries.
- **DISTINCT queries** — the system cannot intentionally generate duplicate rows to test DISTINCT behavior, missing cases where queries with/without DISTINCT produce different results.
- **GROUP BY** — the evaluator tracks group cardinality but the constraint generator cannot target GROUP_MULTI specifically, so coverage depends on speculative generation luck.

---

## Design Goals

1. **Complete branch tracking** — the evaluator records observations for ALL branch types defined in `BranchType`.
2. **Targeted constraint generation** — the constraint generator produces `SolverConstraint` for every branch type, enabling the solver to fill any coverage gap.
3. **Static-first, concolic fallback** — try to derive constraints from plan structure; fall back to concrete evaluation when static analysis is insufficient.
4. **Modular SMT solver** — split `smt.py` into focused files for maintainability.

---

## Architecture

### Phase 1: Evaluator Coverage Extension

**File:** `src/parseval/symbolic/evaluator.py`

#### 1.1 SubPlan Evaluation

Add `_eval_subplan` method to `PlanEvaluator`:

```python
def _eval_subplan(self, step: SubPlan, ctx: Context, tree: BranchTree) -> Context:
    """Evaluate a SubPlan and record branch observations."""
    if step.kind == SubPlanKind.EXISTS:
        return self._eval_exists(step, ctx, tree)
    elif step.kind == SubPlanKind.IN:
        return self._eval_in(step, ctx, tree)
    elif step.kind == SubPlanKind.SCALAR:
        return self._eval_scalar(step, ctx, tree)
    return ctx
```

**EXISTS evaluation:**

For each outer row that reaches the SubPlan:
1. Build an `Environment` with correlation bindings from the outer row
2. Evaluate the inner plan against the instance
3. Check if the inner query returns rows
4. Record `EXISTS_TRUE` (inner non-empty) or `EXISTS_FALSE` (inner empty)

The inner plan evaluation reuses the same `_walk` mechanism. The `Context` passed to the inner plan includes the correlation bindings.

**IN evaluation:**

For each outer row:
1. Evaluate the inner query to get the value set
2. Check if the outer column's value is in that set
3. Record `IN_MATCH` or `IN_NO_MATCH`

**Correlation handling:**

The `SubPlan.correlation` field contains the outer columns referenced inside. When evaluating:

1. For each outer row, build a correlation `Environment` mapping `outer_col_name → value`
2. Create a `Context` for the inner plan that includes ALL instance tables (not just inner ones)
3. Walk the inner plan using `_walk(subplan.inner, ctx, tree)`
4. When the inner plan's Filter encounters a column reference that matches a correlation column, resolve it from the correlation environment (via scope chaining in `Environment`)

The inner plan evaluation sees the same `Instance` as the outer plan — it has access to all tables. The correlation bindings are passed through the `Environment`'s scope chain, not through the `Context`.

**Branch node creation:**

```python
node = tree.get_or_create_node(
    step_id=annotation.step_id,
    step_type="SubPlan",
    site="exists",  # or "in", "scalar"
    predicate=step.anchor,  # the EXISTS/IN expression
    atoms=(step.anchor,),
    tables=annotation.source_tables,
)
```

#### 1.2 DISTINCT Evaluation

Add `_eval_distinct` handling in `_eval_project`:

When `step.distinct = True`:
1. After evaluating projections, check if output rows have duplicates
2. If duplicates exist → record `DISTINCT_DUPLICATE`
3. If all rows unique → record `DISTINCT_UNIQUE`

```python
if step.distinct:
    # Check for duplicate projected values
    seen = set()
    has_duplicates = False
    for row in passing_rows:
        key = tuple(row[col] for col in projected_columns)
        if key in seen:
            has_duplicates = True
            break
        seen.add(key)
    
    outcome = BranchType.DISTINCT_DUPLICATE if has_duplicates else BranchType.DISTINCT_UNIQUE
    tree.record_observation(node, AtomObservation(atom_id=0, outcome=outcome))
```

#### 1.3 SetOperation Evaluation

For UNION/INTERSECT/EXCEPT:
1. Evaluate each child plan
2. Record whether each branch contributes rows
3. Create a BranchNode with site="setop"

---

### Phase 2: Constraint Generation Extension

**File:** `src/parseval/symbolic/constraints.py`

Add handlers for each new branch type in `ConstraintGenerator.generate`:

#### 2.1 EXISTS Constraints

**EXISTS_TRUE target:** Generate rows that make the inner query return non-empty.
- Extract the inner query's WHERE predicates
- Generate inner table rows satisfying those predicates
- Ensure correlation columns match

**EXISTS_FALSE target:** Generate rows that make the inner query return empty.
- Option A: Generate an outer row with correlation value not present in the inner table
- Option B: Ensure all inner rows fail the inner WHERE clause for the given correlation

```python
def _generate_exists_false(self, target: CoverageTarget) -> SolverConstraint:
    """Generate constraints for EXISTS returning FALSE."""
    subplan = self._find_subplan(target.node.step_id)
    if not subplan or not subplan.correlation:
        return None
    
    # Strategy: generate outer row with correlation value not in inner table
    corr_col = subplan.correlation[0]
    inner_table = self._find_inner_scan_table(subplan)
    
    # Collect existing values in inner table's correlation column
    existing_values = set()
    for row in self.instance.get_rows(inner_table):
        val = row[corr_col.name].concrete
        if val is not None:
            existing_values.add(val)
    
    # Generate a value not in the set
    fresh_value = self._fresh_value_not_in(existing_values, corr_col.type)
    
    return SolverConstraint(
        target_tables=(self._resolve_table(corr_col),),
        atom=exp.EQ(this=corr_col, expression=exp.Literal.number(fresh_value)),
        target_outcome=BranchType.ATOM_TRUE,
        # ... other fields
    )
```

#### 2.2 IN Constraints

**IN_MATCH target:** Generate outer row where column value equals one of the inner query's results.

**IN_NO_MATCH target:** Generate outer row where column value is not in the inner result set.
- Already partially handled by `engine._repair_not_in_simple`
- Move this logic into `ConstraintGenerator` for consistency

#### 2.3 GROUP Constraints

**GROUP_MULTI target:** Generate multiple rows with the same GROUP BY key values.
- Extract GROUP BY columns from the Aggregate step
- Generate N rows with identical GROUP BY values but different other columns

**GROUP_SINGLE target:** Ensure exactly one row per group.
- This is the default behavior, usually already covered

#### 2.4 DISTINCT Constraints

**DISTINCT_DUPLICATE target:** Generate duplicate rows for projected columns.
- Extract projected columns
- Generate two rows with identical projected values

**DISTINCT_UNIQUE target:** Generate unique rows.
- This is the default behavior

---

### Phase 3: SMT Solver Modularization

**Current:** `src/parseval/solver/smt.py` (1861 lines)

**Split into:**

#### 3.1 `smt_types.py` (~200 lines)

- `make_option_type()` — Option type construction
- `LogicalTypeRegistry` — sort/tag cache
- `infer()` — Python value → DataType inference
- `encode_literal()` — Python value → Z3 expression
- `UnsupportedSMTError` exception

#### 3.2 `smt_translate.py` (~600 lines)

- `SMTTranslator` class — sqlglot expression → Z3 translation
- Handler registry for each expression type
- `translate_expression()` — main entry point

#### 3.3 `smt_solver.py` (~400 lines)

- `SMTSolver` class — Z3 solver wrapper
- `solve()` — run Z3 and extract solutions
- `apply_solution()` — apply Z3 results to instance rows
- `checkpoint()` context manager

**Import structure:**

```python
# smt.py becomes a thin re-export layer
from .smt_types import UnsupportedSMTError, encode_literal, infer, make_option_type
from .smt_translate import SMTTranslator
from .smt_solver import SMTSolver
```

This preserves backward compatibility — existing code importing from `parseval.solver.smt` continues to work.

---

### Phase 4: Engine Cleanup (Optional, Future)

Once phases 1-3 are complete, the engine's Phase 0 sub-phases can be simplified:

- `_resolve_subquery_predicates` → replaced by evaluator's SubPlan tracking + constraint generator
- `_repair_not_in_simple` → replaced by constraint generator's IN_NO_MATCH handler
- `_create_having_count_rows` → replaced by constraint generator's GROUP_MULTI handler

These can be deprecated incrementally as the new paths are validated.

---

## Implementation Order

1. **Phase 3 first** — modularize SMT solver (no behavior change, just reorganization)
2. **Phase 1** — extend evaluator with SubPlan/DISTINCT tracking
3. **Phase 2** — extend constraint generation for new branch types
4. **Phase 4** — clean up engine sub-phases (optional)

This order minimizes risk: Phase 3 is pure refactoring, Phase 1 adds tracking without changing generation, Phase 2 adds generation for the newly-tracked branches.

---

## Testing Strategy

### Unit Tests

- **Evaluator tests:** For each new branch type, create a minimal plan + instance, evaluate, verify the BranchTree has the expected observations.
- **Constraint generator tests:** For each new branch type, create a CoverageTarget, generate constraints, verify the SolverConstraint has the right structure.
- **SMT modularization tests:** Verify all existing solver tests pass after the split.

### Integration Tests

- **EXISTS queries:** `SELECT * FROM t1 WHERE EXISTS (SELECT * FROM t2 WHERE t2.x = t1.x)` — verify both EXISTS_TRUE and EXISTS_FALSE are covered.
- **IN queries:** `SELECT * FROM t1 WHERE t1.x IN (SELECT t2.x FROM t2)` — verify IN_MATCH and IN_NO_MATCH.
- **DISTINCT queries:** `SELECT DISTINCT x FROM t1` — verify DISTINCT_UNIQUE and DISTINCT_DUPLICATE.
- **GROUP BY queries:** `SELECT x, COUNT(*) FROM t1 GROUP BY x HAVING COUNT(*) > 2` — verify GROUP_MULTI.

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| SubPlan evaluation is expensive (per-outer-row inner plan evaluation) | Performance regression | Cache inner plan results when correlation values repeat; limit evaluation to first N outer rows |
| Correlated subplan constraint generation is complex | Incomplete implementation | Start with non-correlated EXISTS/IN; add correlation support incrementally |
| SMT modularization breaks imports | Test failures | Keep `smt.py` as re-export layer; run full test suite after each file split |
| New branch types increase solver invocations | Slower generation | Batch related constraints; use speculative solver as fast path |

---

## Success Criteria

1. All existing tests pass after each phase.
2. Evaluator tracks EXISTS_TRUE/FALSE, IN_MATCH/NO_MATCH, DISTINCT_UNIQUE/DUPLICATE.
3. Constraint generator produces valid SolverConstraint for each new branch type.
4. `smt.py` is split into 3 files with no behavior change.
5. Integration tests demonstrate improved coverage for EXISTS/IN/DISTINCT queries.
