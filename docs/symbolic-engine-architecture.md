# Design Spec: Symbolic Engine Architecture

## Current State

The engine has three components that overlap and have unclear boundaries:

```
Phase 0:  speculate() + 6 fixup patches
Phase 1:  evaluator.evaluate(tree)
Phase 2:  constraint_gen + solver loop
```

The 6 fixup methods in the engine (`_ensure_base_rows`, `_seed_for_offset`, `_resolve_subquery_predicates`, `_create_having_count_rows`, `_smt_repair_where`, `_enrich_for_semantics`) exist because the speculative layer doesn't handle their concerns. This makes the engine bloated and the responsibilities unclear.

## Desired Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Phase 1: Speculate                                      │
│  Input:  Plan, Instance, AliasMap                        │
│  Output: Instance seeded with rows for all branches      │
│                                                          │
│  Responsibilities:                                        │
│  - Positive branch: rows that make the query return data  │
│  - Negative branch: rows that exercise FALSE outcomes     │
│  - NULL branch: rows that exercise NULL outcomes          │
│  - JOIN unmatched: rows with no matching join partner     │
│  - HAVING fail: rows that fail HAVING conditions          │
│  - Subquery: rows for EXISTS/IN/scalar subqueries         │
│  - Semantic: duplicate rows for DISTINCT, NULL for COUNT  │
│  - FK ordering: parent tables seeded before child tables  │
│  - OFFSET/LIMIT: enough rows to satisfy offset + limit    │
│  - Self-join: separate rows per alias                     │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 2: Evaluate                                       │
│  Input:  Plan, Instance (seeded)                         │
│  Output: BranchTree with observed outcomes               │
│                                                          │
│  Responsibilities:                                        │
│  - Walk plan bottom-up, evaluate each step's predicates   │
│  - Record per-atom observations (TRUE/FALSE/NULL)         │
│  - Track EXISTS/IN/DISTINCT/GROUP outcomes                │
│  - Use column_meta for early pruning (IS NULL on NOT NULL)│
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Phase 3: Cover Gaps                                     │
│  Input:  BranchTree (uncovered targets), Plan, Instance  │
│  Output: Instance with additional rows                   │
│                                                          │
│  Responsibilities:                                        │
│  - For each uncovered target:                             │
│    1. Generate SolverConstraint (constraint_gen)          │
│    2. Solve via SMT solver                                │
│    3. Materialize new rows in Instance                    │
│    4. Re-evaluate to check coverage                       │
│  - Mark infeasible targets                                │
│  - Respect row budget                                     │
└─────────────────────────────────────────────────────────┘
```

## Component Responsibilities

### Speculative Layer (`speculate.py`)

**Goal:** Generate a comprehensive set of initial rows that exercise as many branches as possible, without using SMT solving.

**Propagator** walks the plan top-down and produces `BranchSpec` objects. Each spec describes what one branch needs:
- Which tables need rows (`TableRequirement`)
- How many rows per table (`min_rows`)
- What values columns need (`fixed_values`, `predicates`)
- Which columns must be NULL / NOT NULL (`must_null`, `not_null`)
- Which columns must be coordinated across tables (`equivalences` via Union-Find)
- Which columns need duplicate values (`duplicate_columns`)

**Resolver** turns `BranchSpec` into concrete row values:
- Resolves equivalence classes to shared values
- Generates values satisfying predicates
- Creates `min_rows` rows per table with coordinated group keys
- Orders table creation by FK dependencies

**What it should handle (but currently doesn't), ordered by impact:**
1. **FK-referenced tables** — Resolver creates rows for tables in `spec.requirements`, but FK-referenced tables that aren't directly scanned may be missing. This causes the most downstream failures because FK constraints block row insertion.
2. **HAVING COUNT** — Propagator extracts `min_group_size`, but applies it to ALL tables. Should apply to the counted table only. Wrong table targeting means the evaluator never sees GROUP_MULTI.
3. **OFFSET/LIMIT** — Propagator sets `min_rows = offset + limit`, but applies it to ALL tables. Should apply to the driving table only. Over-seeding wastes budget; under-seeding misses OFFSET branches.
4. **Aggregate NULLs** — Propagator doesn't track that COUNT/SUM/AVG columns need a NULL row to test NULL-handling semantics. The evaluator can't observe ATOM_NULL for these columns without a NULL row.
5. **Row validation** — Resolver generates values but doesn't verify they satisfy the predicates. If `_satisfy_all` produces a bad value, the row silently fails. Low impact because `_satisfy_all` is already correct for common predicates.

### Evaluator (`evaluator.py`)

**Goal:** Run concrete evaluation against the seeded Instance and record which branches are covered.

**What it handles:**
- Walks plan bottom-up, builds `Environment` per row
- Evaluates atoms via `concrete()`, classifies outcomes
- Records observations into `BranchTree`
- Handles SubPlan (EXISTS/IN) by evaluating inner plans
- Uses `column_meta` for early pruning

**What it doesn't need to change:** The evaluator is already correct. It reads from the Instance and records what it sees. If the Instance has the right rows, the evaluator discovers the right branches.

### Constraint Generator (`constraints.py`)

**Goal:** For a specific uncovered target, produce a `SolverConstraint` that the SMT solver can satisfy.

**What it handles:**
- Transforms atom for target outcome (TRUE/FALSE/NULL)
- Collects path predicates from upstream steps
- Collects NOT NULL/UNIQUE/FK constraints from `column_meta`
- Handles EXISTS/IN/DISTINCT/GROUP special cases

**What it doesn't need to change:** The constraint generator is already correct. It produces constraints for a specific target, and the solver finds values.

### Engine (`engine.py`)

**Goal:** Orchestrate the three components.

**Current flow:**
```
speculate()           → seed Instance
6 fixup methods       → patch gaps
evaluator.evaluate()  → build BranchTree
constraint_gen loop   → cover remaining gaps
```

**Desired flow:**
```
speculate()           → seed Instance (comprehensive)
evaluator.evaluate()  → build BranchTree
constraint_gen loop   → cover remaining gaps
```

The engine should be thin: just orchestration, no domain logic.

## Design Decisions

1. **Speculative layer handles as many cases as possible** — common and uncommon patterns, including HAVING COUNT, aggregate NULLs, FK ordering, OFFSET, self-joins, NOT IN, scalar subqueries.
2. **Keep `_smt_repair_where` in the engine** — the solver module already handles speculative constraints via Z3. This stays as a fallback for cases the speculative layer can't handle heuristically. **Contract:** after the other 5 fixups move, `_smt_repair_where` handles exactly these cases: (a) self-join predicates requiring per-alias Z3 variables with distinct-PK constraints, (b) NOT IN predicates requiring anti-value constraints, (c) complex OR predicates where the speculative layer's conjunctive heuristic fails. It is the Z3 fallback — if the speculative layer's rows already satisfy the WHERE clause, `_smt_repair_where` short-circuits and does nothing.
3. **Speculative layer handles runtime-dependent predicates** — scalar subqueries like `col > (SELECT AVG(x) FROM t)` need a multi-pass approach: seed inner tables first, evaluate the subquery, then adjust outer rows. **Why move this to speculate:** the evaluator skips atoms containing subqueries (`decompose_atoms` filters them), so they must be resolved before evaluation. Placing this in the speculative layer keeps the "seed → evaluate → cover" phases clean — the engine never needs to re-seed after evaluation starts.
4. **`_fill_fk_values` stays in the engine** — this is solver-materialization glue, not speculative seeding. When the solver produces row values with FK references, `_fill_fk_values` ensures parent rows exist (creating them if needed). This belongs in the engine's `_solve_and_materialize` method alongside the solver loop.

## Implementation Plan

**Phase A: Propagator enhancements**

1. **HAVING COUNT: table-specific `min_rows`** — add `_find_counted_table(condition)` that finds the column inside `COUNT(col)`, resolves its table, and sets `min_rows` only on that table instead of globally.
2. **Aggregate NULL columns** — in the `Aggregate` step handler, detect columns in COUNT/SUM/AVG (reuse logic from `enrichment.py:_collect_aggregate_targets`). Mark them as `must_null` with `min_rows >= 2` so the Resolver creates a NULL row.
3. **OFFSET: table-specific `min_rows`** — the Limit handler already calculates `offset + limit`. Fix: apply `min_rows` to the driving table (first table in alias_map values) instead of all tables.
4. **Scalar subquery handling** — in the Filter handler, detect atoms containing scalar subqueries. Mark them for deferred evaluation (store in a `BranchSpec.deferred` field). The Resolver seeds inner tables first, evaluates the subquery, then adjusts outer rows.

**Phase B: Resolver enhancements**

5. **FK-referenced table rows** (highest impact) — in `_creation_order`, after topological sort, ensure FK-referenced tables that aren't in `spec.requirements` get a `TableRequirement(min_rows=1)` so they're seeded.
6. **Row validation** — add `_validate_row(table, req, row)` that checks each predicate in `req.predicates` against the generated row. If a predicate fails, retry with `_satisfy(op, value)`. Called from `_build_row`.
7. **Deferred subquery resolution** — add `_resolve_deferred(spec, shared)` that evaluates scalar subqueries after initial rows are generated, then adjusts outer row values.

**Phase C: Engine cleanup**

8. **Remove `_ensure_base_rows`** — handled by Resolver FK creation (step 5) plus the existing Propagator's positive branch, which already requires every scanned table. The `alias_map.ensure_rows_exist` logic (self-join aware alias coverage) moves into the Resolver's `_creation_order` — if an alias has no `TableRequirement` after propagation, the Resolver adds one with `min_rows=1`.
9. **Remove `_seed_for_offset`** — handled by Propagator Limit handler (step 3)
10. **Remove `_create_having_count_rows`** — handled by Propagator HAVING handler (step 1)
11. **Remove `_enrich_for_semantics`** — handled by Propagator aggregate NULL detection (step 2)
12. **Remove `_resolve_subquery_predicates`** — handled by Propagator deferred evaluation (step 4)
13. **Keep `_smt_repair_where`** — Z3 fallback stays

Each step is independently testable. Remove engine fixups one at a time, verifying tests pass after each.

## Risk Analysis

If the speculative layer generates bad rows after refactoring, the evaluator sees fewer branches, the solver picks up the slack (slower), and BIRD benchmark coverage drops. This is a silent regression — no errors, just reduced coverage.

**Mitigation:** Run the BIRD benchmark after each implementation step. If coverage drops from baseline, the last step introduced a regression. The benchmark test (`tests/symbolic/test_symbolic_bird.py`) exercises ~1600 real queries and catches most seeding failures.

**Baseline:** Record current BIRD pass rate before starting. Each step must not regress below baseline.

## Files Modified

- `src/parseval/symbolic/speculate.py` — Propagator + Resolver enhancements
- `src/parseval/symbolic/engine.py` — remove fixup methods
- `src/parseval/symbolic/enrichment.py` — may be removable if Propagator handles aggregate detection

## Verification

```
.venv/bin/python3 -m unittest discover tests/ -q
```
Run after each step. Key tests: `tests/symbolic/test_symbolic_engine.py`, `tests/symbolic/test_distinct_eval.py`, `tests/symbolic/test_subplan_eval.py`.

**BIRD benchmark gate:** Before starting, record the current pass rate from `tests/symbolic/test_symbolic_bird.py`. After each step, re-run and verify no regression. This catches silent seeding failures that unit tests miss.
