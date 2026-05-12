# DB Manager Review And Redesign Plan

## Objective

Review `src/parseval/db_manager.py` critically in the context of the current
ParSEval persistence flow, then define a concrete redesign path to support
multiple database backends cleanly and let callers pass a connection string
directly instead of the current `host_or_path`-style parameter set.

## Research Summary

- `db_manager.py` currently exposes a legacy connection API based on
  `host_or_path`, `database`, `port`, `username`, `password`, and `dialect`.
- Recent refactor work already introduced `DatabaseTarget.connection_string`
  into the instance loader, but the loader has to reverse-parse that string
  back into legacy `DBManager.get_connection(...)` arguments.
- Main call sites in `main.py`, `disprover.py`, tests, and result-state models
  still encode the old connection shape directly.
- The existing instance refactor plan explicitly says `DBManager` should remain
  a connection/SQL execution utility, which is useful guardrail for the redesign.

## Proposed Architecture

- Keep `DBManager` focused on:
  - building or reusing SQLAlchemy engines
  - optionally provisioning missing databases
  - returning a small execution wrapper (`Connect`)
- Move the public API to a connection-string-first interface:
  - primary input: SQLAlchemy URL / connection string
  - optional provisioning policy for backends that can create missing databases
- Preserve a compatibility wrapper for legacy callers during migration.

## Implementation Approach

1. Finish code-path review of `db_manager.py` and its consumers.
2. Produce a sharp assessment of current issues:
   - API shape
   - backend abstraction quality
   - correctness and safety issues
   - pooling / lifecycle behavior
   - layering violations
3. Propose a concrete replacement design:
   - normalized target object
   - URL parsing and dialect handling
   - backend-specific provisioning hooks
   - migration strategy for existing callers

## Validation Strategy

- Analytical task only for this turn.
- Validate by grounding conclusions in current source files and existing plans.

## Success Criteria

- [ ] Review identifies concrete weaknesses with file-level evidence
- [ ] Redesign supports multiple backends more cleanly than current API
- [ ] Proposal centers on direct connection string input
- [ ] Migration path is realistic for current call sites

## Potential Issues

- Some rule files referenced by `AGENTS.md` are absent from `.agents/rules/`;
  use the files that actually exist and note the mismatch.
- The codebase is mid-refactor, so part of the review must distinguish between
  inherited legacy constraints and fresh design mistakes.
