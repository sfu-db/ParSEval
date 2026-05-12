# Symbolic BIRD: FK-Aware Heuristic Realization and Capacity Fallback

**Date:** 2026-05-10
**Context:** Extending `src/parseval/symbolic` and `src/parseval/solver` so
the current symbolic campaign can pass more of
`tests/symbolic/test_symbolic_bird.py` without query-specific patches.

## The Problems
Several later BIRD failures were not SQL-shape-specific bugs. They came from
general gaps in concrete realization and capacity control:

- batched heuristic row creation inserted child tables before referenced parent
  tables, so FK bootstrapping picked the wrong values
- heuristic equality handling processed `col = col` before `col = literal`,
  locking related columns onto a placeholder instead of the constrained value
- recursive FK parent creation used `_create_row(...)` directly, so parent rows
  skipped bootstrapping of their own FK dependencies
- `LIKE '1996-01%'` predicates were ignored by heuristic realization, which made
  large `LIMIT` join queries keep generating irrelevant rows
- some join-heavy scopes produced valid SQLite results while symbolic execution
  failed to materialize binding rows, causing repeated unnecessary capacity
  growth

## The Solutions
- order batch row creation by FK dependency before calling `create_row(...)`
- flatten and prioritize heuristic constraints so literal-bearing equalities run
  before column-to-column equalities
- when auto-creating FK parents, call `create_row(...)` recursively instead of
  `_create_row(...)`
- add heuristic `LIKE` prefix support, including temporal prefixes such as
  `'1996-01%' -> datetime(1996, 1, 1)`
- in `SymbolicCampaign`, when symbolic binding row counts are unreliable but the
  scope has concrete branch activity, fall back to executing the scope SQL on
  the current SQLite instance to measure actual cardinality
- for scopes that require multiple rows because of `LIMIT/OFFSET`, stop the
  branch campaign once the required concrete row count is already satisfied

## Why This Works
These fixes preserve the intended architecture:

- symbolic still decides what scope or branch to pursue
- solver and instance layers still own concrete row realization
- campaign-level fallback is only used to measure whether the current concrete
  instance already satisfies scope cardinality

Together, these changes prevent the campaign from wasting time growing an
already-sufficient instance or mutating rows in a way that violates the intended
join/FK witness.

## How to Apply
When later BIRD failures involve joins, limits, or chained foreign keys:

- inspect row creation order before adding new predicate-specific logic
- inspect heuristic constraint ordering before assuming the solver missed the
  equality
- treat `LIKE` and other pattern predicates as first-class heuristic inputs for
  witness generation
- if symbolic execution records branch activity but binding rows are empty, do
  not assume the concrete instance is empty; verify cardinality separately

## Tags
#symbolic #bird #solver #foreign-key #heuristic #capacity
