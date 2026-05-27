# Targeted Enrichment for Minimizing False EQ Verdicts

**Date:** 2026-05-27
**Status:** Approved

## Problem

Running the SQLite experiment on 1534 Bird dataset pairs produces:
- EQ: 746 (48.6%) — many are false positives
- NEQ: 683 (44.5%)
- Syntax errors: 74 (4.8%) — from predicted SQL, not our engine
- Runtime errors: 23 (1.5%)
- Unknown: 8 (0.5%)

The false EQs occur because the generated data doesn't contain the patterns that expose semantic differences between queries. Specifically:

| Pattern | Cases | Root cause |
|---------|-------|------------|
| DISTINCT mismatch | ~15 | Engine doesn't generate duplicate rows |
| COUNT(*) vs COUNT(col) | ~10 | Engine doesn't generate NULL values |
| Aggregate semantics | ~10 | SUM/COUNT equivalences not recognized |
| GROUP BY behavior | ~5 | GROUP BY vs DISTINCT not distinguished |
| Subquery rewriting | ~10 | Different execution paths with duplicates |

## Solution: Targeted Enrichment via Constraint Augmentation

### Strategy

Use **enriched first round** with **targeted enrichment**: always generate data that includes duplicates and NULLs in the first round, targeting specific columns identified from the query plan.

### Architecture

```
SQL Query
    │
    ▼
Plan Builder (existing)
    │
    ▼
Plan Analysis (new) ──► Identify enrichment targets
    │                    - DISTINCT columns → need duplicates
    │                    - GROUP BY columns → need duplicates
    │                    - COUNT/SUM/AVG columns → need NULLs
    ▼
Constraint Generation (extended)
    │
    ▼
Solver (existing) ──► Generates rows satisfying both
    │                  query constraints AND enrichment requirements
    ▼
Instance (existing)
    │
    ▼
Execute both queries → Compare → Verdict
```

### Phase 1: Plan Analysis

After building the plan, traverse `plan.ordered_steps` to collect enrichment targets. No new data classes needed — derive targets from existing plan structure:

**From `Project` steps:**
- If `distinct=True` → collect `projected_columns` as duplicate targets
- These columns need at least two rows with identical values

**From `Aggregate` steps:**
- `group` columns → duplicate targets (same group key in multiple rows)
- `aggregations` referencing `COUNT(col)`, `SUM(col)`, `AVG(col)` → NULL targets
- Resolve column references to table/column pairs using `StepAnnotations`

**Resolution:**
- Use `StepAnnotations.referenced_columns` to map expressions to concrete table.column
- Use `StepAnnotations.source_tables` to identify which table owns each column

### Phase 2: Constraint Generation

In `SymbolicEngine.generate()`, after normal speculation (Phase 0), add enrichment pass:

**For NULL targets:**
- Add soft constraint: column CAN be NULL
- Skip if schema has NOT NULL constraint
- Solver generates at least one row with NULL in that column

**For duplicate targets:**
- After generating first row satisfying WHERE clause
- Generate second row with same values in DISTINCT/GROUP BY columns
- Other columns can differ (solver chooses)
- Ensures query sees duplicate keys

**Key property:** Enrichment constraints are additive. The solver finds values satisfying both the query's WHERE clause AND the enrichment requirements.

### Phase 3: Bug Fixes (Integrated)

Fix these bugs as part of the enrichment implementation:

**P0 — Correctness:**
- `compiler.py:275` — Move `_intersect_preserving_order` inside `ConstraintCompiler` class. Currently defined at module level but called as `self._intersect_preserving_order()`, causing `AttributeError` for ENUM/ChoicesConstraint columns.

**P1 — Performance:**
- `rex.py:966` — Cache `like_to_pattern()` result. Currently recompiles LIKE-to-regex on every row evaluation. The pattern is fixed per AST node.
- `compiler.py:347` — Pre-compile regex patterns in `PatternConstraint`. Currently calls `re.search(string_pattern, ...)` on every validation.

**P2 — Performance:**
- `planner.py:1273` — Use `heapq` for topological sort. Currently O(n² log n) due to re-sorting on every iteration.
- `unified.py:346` — Single-pass evaluation in IN-list heuristic. Currently calls `concrete(e)` twice per expression.

**P3 — Code quality:**
- Factor FK boilerplate (5 identical lines) into `ValueProvider` base class across 9 providers.
- Consolidate 3 independent DFS traversals in `speculate.py` into shared helper.

### Phase 4: Experiment

1. Skip pairs where either query has syntax error (~74 pairs)
2. Run enriched generation on ~1452 valid pairs
3. Compare against baseline: 683 NEQ, 746 EQ
4. Target: reduce false EQ rate from 51.4% to ~49% or lower

## Expected Impact

- **NEQ recovery:** ~25+ pairs from DISTINCT and COUNT mismatches
- **Performance:** Regex/LIKE caching reduces redundant computation
- **Code quality:** Bug fixes and deduplication improve maintainability

## Implementation Order

1. Fix P0 bug (compiler.py method) — unblocks ENUM columns
2. Add plan analysis for enrichment targets
3. Add enrichment constraint generation
4. Fix P1 bugs (regex/LIKE caching) — performance improvement
5. Fix P2/P3 bugs — code quality
6. Run experiment and measure improvement
