# Symbolic Recording: Use Deterministic ScopePlan Step IDs

**Date:** 2026-05-09
**Context:** Implementing planner-anchored symbolic branch extraction, runtime recording, and early scheduling.

## The Problem
`sqlglot` planner step ids include object identity details, so rebuilding a
`Plan` for extraction and then again for execution produces different step ids.
That breaks any symbolic index keyed directly by `step.id`, even when the query
shape is unchanged.

## The Solution
Derive symbolic step ids from `ScopePlan.ordered_steps` instead of raw
`sqlglot` ids. The current slice uses `step_{index}` as a scope-local stable id.

Runtime branch recording should receive that deterministic `step_id` from the
active `ScopePlan` annotation rather than reconstructing identity from the live
`Step` object.

## Why This Works
`ScopePlan.ordered_steps` is already the project’s deterministic structural
ordering over a scope. Using that ordering keeps extraction, runtime recording,
coverage, and scheduling aligned even when separate `Plan(...)` objects are
built for the same query.

## How to Apply
When adding later symbolic layers:

- key branch templates, runtime instances, and lowering rules by
  `ScopePlan.annotation_for(step).step_id`
- do not key symbolic state directly by raw `step.id`
- if a slice needs richer step identity, extend `StepAnnotations` rather than
  reintroducing object-identity-based keys

## Tags
#symbolic #planner #step-id #determinism #coverage
