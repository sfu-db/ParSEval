# Speculative Generator: Prefer Domain-Backed Pools Over Compatibility Shims

**Date:** 2026-05-09
**Context:** Refining the speculative generator after the instance/domain refactor.

## The Problem
The temporary `_PoolAdapter` in speculative generation only mimicked the old `column_domains` API. It exposed the methods the planner needed, but it did not explicitly model the current domain-layer concepts:

- `schema_spec`
- type profiles
- builder runtime state
- uniqueness semantics for provisional batch generation

That made the speculative path fragile and forced fallback logic to guess at types from strings.

## The Solution
Replace the shim with a domain-backed pool registry:

- `DomainPoolRegistry` caches pools by normalized `(table, column)`
- `DomainValuePool` wraps the actual schema column, type profile, and builder runtime
- fallback generation is driven by `TypeFamily` rather than ad hoc datatype-string checks

## Why This Works
The new pool abstraction keeps speculative generation aligned with the same schema and type system used by `Instance` and `DatabaseBuilder`. That makes typed fallback values, uniqueness metadata, and runtime bookkeeping consistent across the system.

## How to Apply
When speculative code needs a column-oriented helper:

- prefer wrapping `schema_spec` and builder/runtime directly
- avoid recreating legacy APIs with thin shims unless the shim is truly temporary
- when generating provisional unique batches, account for values that are not yet persisted into runtime state

## Tags
#architecture #speculative #domain #type-system
