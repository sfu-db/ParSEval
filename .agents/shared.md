# Shared Agent Instructions

Use this file for repository-wide guidance that should stay aligned across
different coding agents.

## Defaults

- Make the smallest change that solves the task cleanly.
- Preserve existing project structure unless there is a documented reason to
  change it.
- Prefer project-local commands and scripts over ad hoc one-off workflows.
- Update documentation when behavior, setup, or structure changes.
- For non-trivial work, read relevant files in `.agents/learnings/` before planning.
- Write and maintain the active implementation plan in `.agents/plans/`.
- Follow the active plan while implementing; if the plan becomes wrong, update it before continuing.
- When a relevant plan or learning exists, agents are expected to follow it rather than reinventing the approach ad hoc.

## Validation

- Python changes: run the relevant `pytest` target from the project root.



- If no automated checks exist yet, explain what was validated manually.
