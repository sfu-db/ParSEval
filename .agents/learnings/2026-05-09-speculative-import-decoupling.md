# Speculative Generator: Keep Imports Lightweight

**Date:** 2026-05-09
**Context:** Refactoring the speculative database-instance generator from `speculative_backup.py` into a package.

## The Problem
`parseval.speculative` originally depended on `parseval.data_generator.BaseGenerator` for a small amount of shared generator behavior. That import pulled in the SMT and UExpr stack, which in turn required optional packages such as `ordered_set`. As a result, simple speculative imports failed even when the speculative path itself did not need those dependencies.

## The Solution
Keep the speculative generator self-contained for lightweight behavior:

- implement the small shared generator helpers locally (`table_alias`, `randomdb`, pool lookup)
- preserve the public `SpeculativeGenerator` API
- keep speculative extraction and value-generation modules importable without loading SMT-specific modules

## Why This Works
The speculative path is a fallback witness generator. It needs schema access, SQL analysis, and row synthesis, but not the SMT pipeline. Decoupling at the import boundary prevents optional solver dependencies from becoming mandatory for speculative-only workflows.

## How to Apply
When adding generator or planner code:

- only import solver/planner subsystems when the feature actually needs them
- prefer copying a small stable abstraction over importing a large module that drags unrelated dependencies
- treat package importability as part of the public API

## Tags
#architecture #imports #speculative #dependency-management
