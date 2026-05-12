# Symbolic BIRD Witnessing: Seed Scope Witnesses Before Branch Coverage

**Date:** 2026-05-10
**Context:** Expanding `src/parseval/symbolic` so the symbolic generator can
handle more real BIRD queries without falling back to speculative generation.

## The Problem
The first symbolic generator slice only worked for narrow single-table branch
coverage cases:

- it rejected join and aggregate scopes up front
- it required extracted branch templates, so template-free scopes stayed empty
- branch-goal lowering ignored join key equalities, so multi-table witnesses
  never materialized
- some real query predicates, especially temporal and `strftime('%Y', ...)`
  filters, were too awkward for the SMT path alone

## The Solution
Keep the existing coverage-oriented branch loop, but add a scope-witness phase
inside `src/parseval/symbolic`:

- lower join key equalities as structural background constraints
- lower a scope-wide witness goal before branch scheduling
- iterate graph dependency order so simple subquery-backed scopes can feed later
  scopes
- when SMT solving is insufficient, synthesize one witness row set directly from
  simple symbolic constraints such as:
  - column/literal equality
  - column/column equality
  - numeric/date inequalities
  - year-extractor predicates like `strftime('%Y', col)`

Also normalize symbolic-created `DATE` row values to comparison-safe
`datetime.datetime` instances inside the symbolic mutator to avoid runtime type
  mismatches during symbolic execution.

## Why This Works
The BIRD test only needs a non-empty witness, not exhaustive branch coverage.
Scope witness synthesis gives the symbolic runtime a concrete starting point
that satisfies the query shape itself, while the existing branch loop still
improves coverage when the SMT path can keep pushing.

## How to Apply
When broadening symbolic support:

- do not rely on branch templates alone for witness generation
- treat join equalities as structural constraints, not optional branch details
- add bounded symbolic heuristics only after the SMT path fails, and keep them
  local to `src/parseval/symbolic`
- for planner-rewritten temporal predicates, inspect the lowered SQL form before
  assuming the original function name is still present

## Tags
#symbolic #bird #joins #subqueries #temporal #witness-generation
