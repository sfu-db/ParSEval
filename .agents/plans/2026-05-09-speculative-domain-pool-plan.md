# Speculative Domain Pool Refactor Plan

## Objective
Replace the temporary `_PoolAdapter` in the speculative generator with a domain-aligned pool abstraction that reuses `Instance.schema_spec`, `DatabaseBuilder`, and the domain type/profile system.

## Research Summary
- `src/parseval/speculative/generator.py` currently defines `_PoolAdapter` as a minimal compatibility shim for the removed legacy `column_domains` API.
- The shim only exposes `generate`, `_generate`, `add_generated_value`, `unique`, and `datatype`, but it does not model domain metadata explicitly.
- `Instance` already owns `schema_spec` and `builder`, so speculative code can reuse the current domain module directly instead of reconstructing pool semantics ad hoc.
- Recent learnings establish two constraints:
  - speculative imports should stay lightweight
  - traversal/dispatch should be abstracted rather than embedded in the generator

## Proposed Architecture
- Add `src/parseval/speculative/pool.py` with:
  - `DomainValuePool`: wraps one schema column, its type profile, and builder/runtime access
  - `DomainPoolRegistry`: caches pools by normalized `(table, column)`
- The pool should expose the small interface speculative planning already expects:
  - `generate()`
  - `_generate(count, skips=None)`
  - `add_generated_value(value)`
  - `unique`
  - `datatype`
- The implementation should rely on:
  - `instance.schema_spec`
  - `instance.builder`
  - `TypeService`

## Implementation Approach
- Keep support for legacy `instance.column_domains` if present.
- For the new instance path, use the domain-backed registry instead of `_PoolAdapter`.
- Make fallback generation type-aware using the domain profile rather than hardcoded datatype strings where possible.

## Validation Strategy
- Add focused tests around speculative pool behavior:
  - correct uniqueness metadata
  - sensible typed values for dates/numerics/text
  - `_generate(..., skips=...)` respects uniqueness/skips
- Re-run:
  - `python -m unittest tests.speculative.test_architecture`
  - `python -m unittest tests.test_instance_loader`

## Success Criteria
- [ ] `_PoolAdapter` is removed or reduced to legacy compatibility only.
- [ ] Speculative generation uses a domain-backed pool registry for current `Instance`.
- [ ] Focused tests pass.
- [ ] Existing targeted speculative and instance validations remain green.

## Potential Issues
- The domain builder does not persist values for `generate_value(...)`, so speculative code still needs an explicit `add_generated_value(...)` step when it wants the runtime to remember a candidate.
- Some fallback logic may remain necessary if builder/provider generation fails under incomplete constraints.

## Sources
- Local repository code only:
  - `src/parseval/speculative/generator.py`
  - `src/parseval/speculative/planner.py`
  - `src/parseval/domain/builder.py`
  - `src/parseval/domain/spec.py`
