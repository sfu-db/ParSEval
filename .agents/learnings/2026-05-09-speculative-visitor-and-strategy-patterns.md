# Speculative Generator: Visitor Collection and Function Strategies

**Date:** 2026-05-09
**Context:** Second-pass refactor of the speculative generator to remove hardcoded extraction orchestration and function dispatch chains.

## The Problem
Even after splitting the monolith into modules, two architectural smells remained:

- speculative spec collection still orchestrated raw scope handling inside speculative code rather than through a reusable planner traversal abstraction
- function support still depended on large `if/elif` chains, so each new SQL function required editing core value-generation logic

## The Solution
Introduce two reusable abstractions:

- a planner-side scope visitor entry point built on `build_graph_from_scopes`
- speculative function strategy registries for single-function handling and multi-function composition

The speculative package now consumes these abstractions instead of embedding traversal and dispatch logic directly in the generator.

## Why This Works
The visitor pattern localizes traversal policy in `plan/` and lets speculative generation focus on turning planner scopes into `GenerationSpec`. The strategy registry localizes function behavior by function name, which keeps the core generator closed to modification and open to extension.

## How to Apply
When extending speculative support:

- add new scope-driven extraction behavior in a concrete visitor, not in the generator constructor
- add new function support by registering a strategy, not by editing a central dispatch chain
- if a regression appears in witness generation, check boundary-value helpers such as temporal comparison offsets before blaming the visitor wiring

## Tags
#architecture #visitor-pattern #strategy-pattern #speculative
