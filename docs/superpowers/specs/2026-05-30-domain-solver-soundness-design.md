# DomainSolver Soundness Redesign

Date: 2026-05-30
Status: Approved for planning

## Goal

Redesign `src/parseval/solver/domain.py` and the unified solver boundary so the domain tier is a sound fast path:

- `DomainSolver` may return `sat` only when it has handled the full input formula soundly.
- `DomainSolver` may return `unsat` when it derives a contradiction without SMT.
- `DomainSolver` must return `unknown` when it cannot reason about the full formula safely.
- `Solver.solve()` must short-circuit on domain `unsat`, trust domain `sat`, and rerun the original full constraint set in SMT only on domain `unknown`.

## Current Problems

The current domain tier is unsound in several ways:

- Unsupported atoms can be dropped while the solver still returns an assignment.
- `OR` is handled by taking one branch, which can produce assignments that do not satisfy the original expression.
- `NOT` may fall back to lowering the inner expression as-is, which is not logically equivalent.
- Boolean domains can become exhausted without being detected as contradictory.
- Alias-to-physical remapping can collapse distinct self-join aliases into one output row.

The current SMT orchestration also weakens soundness because unsupported expressions may be skipped silently instead of failing the fallback attempt.

## Recommended Approach

Use a tri-state `DomainSolver` with this unified flow:

1. Validate typed columns in `Solver.solve()`.
2. Run `DomainSolver.solve(constraint) -> DomainResult`.
3. If domain returns `unsat`, return `SolveResult(sat=False, reason=...)` immediately.
4. If domain returns `sat`, return its assignments immediately.
5. If domain returns `unknown`, rerun the original full `SolverConstraint` in SMT.
6. If SMT cannot translate the full formula, return a non-`sat` result rather than solving a subset.

This is the preferred approach because it preserves the value of a cheap domain fast path without allowing partial solving to masquerade as correctness.

## Public Contracts

### DomainResult

Introduce a dedicated domain result type:

- `status`: `sat | unsat | unknown`
- `assignments`: per-table assignment map, present only for `sat`
- `reason`: short machine-readable or human-readable diagnostic

Suggested reasons:

- `contradictory_bounds`
- `contradictory_nullability`
- `empty_boolean_domain`
- `unsupported_or`
- `unsupported_not`
- `unsupported_function`
- `unsupported_arithmetic`

### Unified Solver Contract

`Solver.solve()` keeps returning `SolveResult`, but its orchestration changes:

- Domain `unsat` maps directly to unified `sat=False`.
- Domain `sat` maps directly to unified `sat=True`.
- Domain `unknown` delegates the full original constraint set to SMT.
- SMT unsupported translation maps to unified failure, not partial success.

## Domain Semantics

### Conjunctions

`AND` is domain-solvable only when both sides are domain-solvable.

- If either side proves `unsat`, the whole conjunction is `unsat`.
- If both sides are fully supported and consistent, the conjunction can become `sat`.
- If either side is unsupported and no contradiction is proven, the conjunction is `unknown`.

The critical rule is that supported predicates from one branch may not justify `sat` if the other branch is unknown.

### Disjunctions

The domain solver must stop choosing one branch of `OR`.

Safe tri-state behavior:

- If both branches are `unsat`, the `OR` is `unsat`.
- If one branch is `sat` with a valid assignment and the other branch is `unsat`, the `OR` is `sat`.
- If both branches are `sat`, the `OR` may return either satisfying assignment.
- In any mixed case involving `unknown`, return `unknown` unless the other branch already provides a sound witness and the unsupported branch is not needed to justify satisfiability.

Implementation may begin conservatively by returning `unknown` for most `OR` cases except the obvious `unsat|unsat` and `sat|unsat` shapes.

### Negation

`NOT` is only domain-solvable when the inner expression can be negated exactly within the domain subset.

Allowed examples:

- `NOT(col IS NULL)` -> `col IS NOT NULL`
- `NOT(col IS NOT NULL)` -> `col IS NULL`
- `NOT(col = lit)` -> `col != lit`
- `NOT(col > lit)` -> `col <= lit`

Unsupported examples should produce `unknown`:

- `NOT(OR(...))`
- `NOT(arithmetic comparison)`
- `NOT(function call predicate)` when no exact rewrite exists

### Unsupported Atoms

Any atom outside the supported subset must be surfaced explicitly.

Examples:

- arithmetic over columns
- function calls with no domain model
- non-literal comparisons outside equality propagation rules
- nested expressions requiring symbolic reasoning

These atoms must never be dropped. They are the basis for `unknown`.

## Internal Architecture

Restructure `DomainSolver` into three conceptual stages.

### 1. Constraint Analysis

Responsibility:

- Walk the AST.
- Lower only the safe subset.
- Detect unsupported constructs precisely.
- Evaluate `AND` / `OR` / `NOT` using tri-state composition rules.

Proposed internal output:

- lowered predicates
- column-column equalities
- support completeness flag
- contradiction flag or reason

This stage decides whether the formula is domain-complete, domain-contradictory, or domain-incomplete.

### 2. Domain Propagation

Responsibility:

- Apply lowered predicates into `ValueSpace`.
- Build equality constraints from joins and column equalities.
- Propagate ranges, equalities, nullability, and allowed sets.
- Detect contradictions.

This stage never decides `unknown`. It only works on already-accepted domain facts.

### 3. Assignment Building

Responsibility:

- Materialize values only after analysis has proved the formula is fully supported and propagation has proved it consistent.
- Preserve alias-qualified identity in the assignment map.

Assignments must not be built for `unknown`.

## ValueSpace Fixes

The value-space layer needs explicit contradiction handling for finite domains, especially booleans.

Required fixes:

- Detect boolean exhaustion when both `True` and `False` are excluded.
- Keep `must_null` and `not_null` contradictory.
- Reject `equals` values outside `allowed`, inside `not_equals`, or outside bounds.
- Preserve consistency between `allowed`, `not_equals`, and numeric bounds.

The invariant is simple: `pick()` may be called only on a non-empty space, and `is_empty()` must recognize all representable contradictions.

## Alias and Output Identity

To avoid self-join collisions:

- Keep domain assignments keyed by solver identity first, typically alias-qualified table names.
- Do not collapse aliases to physical table names inside `DomainSolver`.
- If the unified public API still wants physical grouping, that remapping must be a deliberate final step with a defined representation for multiple aliases that resolve to the same physical table.

Open design choice for later planning:

- either preserve alias keys in the public result
- or change the result shape to support multiple alias instances under one physical table

The redesign should not silently overwrite one alias with another.

## SMT Boundary

The SMT fallback must follow the same soundness rule as the domain tier.

Required changes:

- Retry the original full `SolverConstraint`, not a partially lowered one.
- Remove silent dropping of unsupported SMT expressions.
- If SMT translation is incomplete, return a non-`sat` outcome such as `unknown` or a failure reason like `unsupported_smt_expression`.

The overall invariant for the whole module becomes:

> No solver tier may report `sat` unless it has satisfied the full formula it received.

## Testing Strategy

Add or revise tests around the proof boundary rather than only happy-path solving.

### Domain `sat`

- simple conjunctions
- join equalities
- `IN`, `BETWEEN`, `IS NULL`, `IS NOT NULL`
- exact `NOT` rewrites

### Domain `unsat`

- conflicting equalities
- impossible ranges
- empty boolean domain
- null/not-null conflict
- incompatible propagated equality bounds

### Domain `unknown`

- arithmetic predicates
- unsupported functions
- mixed supported and unsupported conjunctions
- unsafe `OR`
- unsafe `NOT`

### Unified Orchestration

- domain `unsat` short-circuits without SMT
- domain `sat` returns directly
- domain `unknown` triggers SMT on the original full constraint set

### SMT Soundness

- unsupported SMT translation must not produce `sat` on a subset
- mixed supported/unsupported formulas must return non-`sat` unless full translation succeeds

## Non-Goals

This redesign does not attempt to:

- make the domain tier handle every SQL expression
- feed partial domain hints into SMT
- optimize SMT with residual predicates
- redesign the entire public solver result shape beyond what is needed to preserve correctness

## Recommendation

Proceed with the tri-state domain redesign and strict SMT fallback boundary. Favor conservative `unknown` results over speculative domain satisfiability. A smaller sound domain subset is preferable to a broader unsound one.
