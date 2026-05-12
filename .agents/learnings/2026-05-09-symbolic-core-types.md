# Symbolic Core Types: Keep Early Coverage Models Fully Hashable

**Date:** 2026-05-09
**Context:** Implementing WP1 of the planner-anchored symbolic coverage redesign.

## The Problem
The first symbolic coverage slice needs importable dataclasses that can act as
stable keys for coverage, scheduling, and tests. Using mutable dictionaries for
metadata would make the types less predictable for equality and hashing.

## The Solution
Model optional metadata and row-context details as tuples of normalized entries:

- metadata as `tuple[(name, value), ...]`
- row identities as `tuple[(table, row_id), ...]`
- group keys as `tuple[(column, value), ...]`

Keep the new `parseval.symbolic` package stdlib-only and independent from the
legacy generator stack.

## Why This Works
Frozen dataclasses with immutable tuple fields provide stable equality semantics
immediately. That makes the initial TDD slice useful for later coverage-store
and scheduler layers without revisiting the basic type contracts.

## How to Apply
When adding new symbolic coverage records:

- prefer immutable, normalized tuple fields for keys and lightweight metadata
- keep the package import boundary free from planner, solver, or generator
  dependencies unless a slice truly needs them
- add any richer helper APIs around these types rather than weakening their core
  equality semantics

## Tags
#symbolic #dataclasses #hashability #imports
