# Planner-Anchored Symbolic Coverage Implementation Breakdown

## Objective
Break down `2026-05-09-planner-anchored-symbolic-coverage-plan.md` into small, dependency-aware implementation tasks that can be executed incrementally.

This breakdown assumes:

- the planner tree (`Graph`, `ScopePlan`, `Step`) is the structural backbone
- symbolic execution will record runtime branch observations against planner-anchored branch templates
- branch scheduling and solving will be rebuilt around explicit branch records rather than the legacy leaf-centric tracer workflow

## Delivery Strategy
The redesign is too large to land safely as one refactor. The implementation should proceed in layers:

1. define the new symbolic coverage data model
2. extract branch templates for a narrow subset of branch kinds
3. record runtime branch instances for those branch kinds
4. build a coverage store and branch-goal scheduler
5. move solving to session-scoped state
6. progressively migrate operator families
7. replace the legacy tracer-centric control loop

This sequence is designed to:

- preserve testability at every slice
- keep old and new paths comparable during migration
- avoid rewriting joins/groups/subqueries before the model is proven on filter / `OR` / `CASE`

## Work Packages

### WP1: Define Core Symbolic Coverage Types
Goal:
Establish the data model for the new architecture without changing runtime behavior yet.

Tasks:

1. Add a module for new symbolic coverage types.
2. Define:
   - `BranchTemplate`
   - `ExecutionContextKey`
   - `BranchInstance`
   - `BranchGoal`
3. Keep the initial fields minimal but sufficient for filter / `OR` / `CASE`.
4. Add clear docstrings for semantics and intended invariants.

Key files:

- new symbolic coverage module(s)
- maybe `src/parseval/symbolic/` or equivalent final location

Acceptance criteria:

- new types are importable without touching current symbolic generation
- field names and semantics are documented clearly
- no generator behavior changes yet

Dependencies:

- none

### WP2: Add Planner-Side Branch Extraction Skeleton
Goal:
Create a planner-anchored extraction layer that can derive branch templates from `ScopePlan`.

Tasks:

1. Add a branch extraction entry point, for example:
   - `extract_branch_templates(scope_plan)`
2. Define a stable template-id scheme:
   - scope id
   - step id
   - branch kind
   - branch-local ordinal
3. Add an extraction registry or dispatcher by step/branch kind.
4. Do not connect it to the generator yet.

Key files:

- new branch extraction module(s)
- planner-core imports from `ScopePlan`

Acceptance criteria:

- branch extraction runs on a `ScopePlan`
- output is a list of `BranchTemplate`s
- extraction is deterministic

Dependencies:

- WP1

### WP3: Implement Branch Extraction For Filter Predicates
Goal:
Prove the model on the simplest branch kind first.

Tasks:

1. For filter-like steps, extract templates for:
   - atomic predicates
   - composite predicate outcome when useful
2. Normalize template metadata for:
   - SQL expression
   - referenced columns
   - source tables
3. Add tests for:
   - simple predicate
   - conjunction
   - nested parentheses

Key files:

- branch extraction module(s)
- new symbolic coverage tests

Acceptance criteria:

- one branch template is created per atomic predicate
- extraction is stable across repeated runs
- tests assert template ids and branch kinds

Dependencies:

- WP2

### WP4: Implement Branch Extraction For `OR` Subpredicates
Goal:
Satisfy the explicit requirement that all subpredicates of disjunctions are tracked individually.

Tasks:

1. Extend extraction to emit templates for each `OR` subpredicate.
2. Optionally emit a template for whole-disjunction outcome if needed.
3. Decide and document whether nested `OR` trees are flattened or preserved structurally.
4. Add tests for:
   - `a OR b`
   - `a OR b OR c`
   - nested mixed `AND`/`OR`

Key files:

- branch extraction module(s)
- new tests

Acceptance criteria:

- each disjunct is independently represented as a `BranchTemplate`
- tests explicitly assert full coverage of all subpredicates

Dependencies:

- WP3

### WP5: Implement Branch Extraction For `CASE` Arms
Goal:
Satisfy the explicit requirement that every `CASE` arm is a distinct branch target.

Tasks:

1. Extract templates for:
   - each `WHEN` arm
   - `ELSE` arm
2. Scope the extraction initially to projection expressions.
3. Add tests for:
   - one `WHEN` + `ELSE`
   - multiple `WHEN`s
   - nested `CASE`

Key files:

- branch extraction module(s)
- new tests

Acceptance criteria:

- every arm is represented by a unique `BranchTemplate`
- template metadata identifies arm order and source expression

Dependencies:

- WP2

### WP6: Add Runtime Branch Recording Skeleton
Goal:
Introduce the mechanism that records branch instances during execution without replacing legacy control flow yet.

Tasks:

1. Add a `BranchRecorder` or equivalent runtime sink.
2. Define APIs for recording:
   - branch template id
   - execution context key
   - taken bit / arm
   - rowids / group key / outer-row context
3. Keep this recording sidecar-only at first.
4. Do not remove `UExprToConstraint` yet.

Key files:

- new runtime recording module(s)
- symbolic encoder integration points

Acceptance criteria:

- runtime recording works independently of branch scheduling
- branch instances can be accumulated during one scope execution

Dependencies:

- WP1

### WP7: Add Runtime Recording For Filter Predicates
Goal:
Prove planner-template to runtime-instance mapping on simple predicates.

Tasks:

1. During symbolic execution of filter steps, emit branch instances for extracted filter templates.
2. Define `ExecutionContextKey` for row-scoped predicates.
3. Add tests asserting:
   - correct template id
   - correct row-context key
   - correct taken state

Key files:

- symbolic execution recorder integration
- tests

Acceptance criteria:

- filter branch instances are recorded correctly for concrete rows
- template ids and runtime contexts match expected rows

Dependencies:

- WP3
- WP6

### WP8: Add Runtime Recording For `OR` Subpredicates
Goal:
Verify exact coverage tracking for disjunction members.

Tasks:

1. Emit one branch instance per evaluated disjunct.
2. Ensure multiple disjuncts on the same row can each be recorded.
3. Add tests where:
   - only first disjunct is true
   - only second disjunct is true
   - both are true
   - none are true

Acceptance criteria:

- runtime recording distinguishes all subpredicate hits correctly
- coverage no longer depends only on final disjunction outcome

Dependencies:

- WP4
- WP6

### WP9: Add Runtime Recording For `CASE` Arms
Goal:
Track exact arm selection for projection-time branching.

Tasks:

1. Emit one branch instance per selected `CASE` arm.
2. Define row-scoped execution contexts for projection/case evaluation.
3. Add tests for:
   - first arm selected
   - later arm selected
   - else arm selected

Acceptance criteria:

- case-arm branch instances are recorded correctly
- one witness per arm can be verified from branch instances

Dependencies:

- WP5
- WP6

### WP10: Add Contribution Labels
Goal:
Introduce explicit contribution semantics into branch instances.

Tasks:

1. Define contribution labels:
   - `active_contributing`
   - `active_noncontributing`
   - `potentially_contributing`
   - `infeasible`
2. Implement first-pass contribution rules for:
   - filter predicates
   - `OR` subpredicates
   - `CASE` arms
3. Add tests that assert contribution labeling for representative queries.

Acceptance criteria:

- branch instances carry contribution labels
- labels are stable and explainable for initial branch kinds

Dependencies:

- WP7
- WP8
- WP9

### WP11: Build Coverage Store
Goal:
Store branch templates and branch instances in a structured index.

Tasks:

1. Add `CoverageStore`.
2. Index:
   - templates by scope and step
   - instances by template
   - instances by context
3. Support:
   - template-level coverage queries
   - context-level coverage queries
4. Add tests for aggregation of branch hits.

Acceptance criteria:

- coverage store answers “covered/uncovered” by template
- coverage store can distinguish repeated hits from distinct contexts

Dependencies:

- WP1
- WP7
- WP8
- WP9

### WP12: Add Branch-Goal Scheduling
Goal:
Replace direct plausible-leaf selection with explicit branch-goal selection.

Tasks:

1. Add a scheduler that reads `CoverageStore` and config.
2. Produce `BranchGoal`s.
3. Implement ranking policy for initial branch kinds:
   - uncovered before covered
   - contributing before noncontributing
   - lower hit count before saturated
4. Add tests for scheduler priority behavior.

Acceptance criteria:

- scheduler produces stable prioritized goals
- scheduling is config-sensitive

Dependencies:

- WP10
- WP11

### WP13: Introduce `SolveSession`
Goal:
Move mutable solve-attempt state out of `DataGenerator`.

Tasks:

1. Extract:
   - variable registry
   - constraint registry
   - bound-table variable state
   - variable declaration helpers
2. Encapsulate them in `SolveSession`.
3. Keep current operator-lowering logic temporarily, but route declarations through the session.
4. Add focused tests for session behavior.

Acceptance criteria:

- a solve attempt can run with isolated mutable state
- repeated attempts do not leak declarations across sessions

Dependencies:

- none strictly, but safest after WP12 groundwork

### WP14: Add Branch Lowering For Filter / `OR` / `CASE`
Goal:
Turn scheduled branch goals into solve constraints.

Tasks:

1. Introduce first branch policies / lowerers for:
   - filter predicates
   - `OR` subpredicates
   - `CASE` arms
2. Lower one `BranchGoal` into:
   - referenced columns
   - symbolic constraints
   - optional post-solve expectations
3. Add tests from goal to emitted constraints.

Acceptance criteria:

- each initial branch kind can produce a solve request through policy-driven lowering

Dependencies:

- WP12
- WP13

### WP15: Wire New Branch Flow Into One Narrow Solve Loop
Goal:
Prove the end-to-end redesign on a small subset of symbolic behavior before touching joins/groups/subqueries.

Tasks:

1. Add a narrow experimental solve path for:
   - filter-only queries
   - `OR` queries
   - projection `CASE` queries
2. Execute:
   - extract templates
   - run instance through scope
   - record instances
   - build coverage
   - select branch goal
   - solve via `SolveSession`
3. Keep old symbolic path available for all other query shapes.

Acceptance criteria:

- new flow can cover initial branch kinds end-to-end
- behavior is testable and isolated from legacy path

Dependencies:

- WP14

### WP16: Add Group / Aggregate Branch Templates
Goal:
Start modeling the next major operator family after filter / case logic stabilizes.

Tasks:

1. Extract templates for:
   - group size
   - group count
   - group null
   - group duplicate
   - aggregate-sensitive behavior
   - HAVING predicate atoms
2. Carry both group-key and aggregate metadata.
3. Add extraction tests.

Acceptance criteria:

- branch templates reflect both group-key and aggregation behavior

Dependencies:

- WP2

### WP17: Add Group Runtime Recording And Coverage
Goal:
Track exact group-scoped branch instances.

Tasks:

1. Define group `ExecutionContextKey`.
2. Record:
   - group key
   - aggregate-relevant observed values
   - HAVING outcomes
3. Add coverage/contribution rules for group branch kinds.

Acceptance criteria:

- group-scoped branch instances are distinct from row-scoped ones
- coverage captures group-key and aggregate behavior together

Dependencies:

- WP16
- WP11

### WP18: Add Group Branch Lowering
Goal:
Move group / aggregate / HAVING branch solving onto branch-goal policies.

Tasks:

1. Add branch policies for:
   - group size
   - group count
   - null
   - duplicate
   - HAVING
2. Add tests based on semantics currently encoded in `uexpr/checks.py`.

Acceptance criteria:

- group solving behavior is expressible without legacy plausible-branch logic

Dependencies:

- WP17
- WP13

### WP19: Add Join Branch Templates And Recording
Goal:
Model join success and preservation behavior explicitly.

Tasks:

1. Extract join templates:
   - true match
   - left preserve
   - right preserve
2. Define join execution context keys.
3. Add runtime recording and coverage tests.

Acceptance criteria:

- joins are planner-anchored and runtime-context-aware

Dependencies:

- WP2
- WP11

### WP20: Add Subquery Branch Templates And Recording
Goal:
Model existence, scalar, and membership behavior explicitly.

Tasks:

1. Extract branch templates for:
   - `EXISTS`
   - `IN`
   - scalar subquery result behavior
2. Define subquery execution context keys using outer-row context.
3. Add runtime recording and coverage tests.

Acceptance criteria:

- subquery branch coverage is explicit and outer-context-aware

Dependencies:

- WP2
- WP11

### WP21: Migrate Scheduling And Solving For Join / Group / Subquery Families
Goal:
Expand the new branch-goal architecture to the harder operator families.

Tasks:

1. Add branch-goal generation for join/group/subquery templates.
2. Add policy-driven lowering for those branch kinds.
3. Validate against current semantics and Bird-like query shapes.

Acceptance criteria:

- major operator families are covered by the new architecture

Dependencies:

- WP18
- WP19
- WP20

### WP22: Replace Legacy `UExprToConstraint` Control Role
Goal:
Make the new planner-anchored model the main symbolic workflow.

Tasks:

1. Reduce `UExprToConstraint` from architectural center to compatibility shim or remove it.
2. Replace `next_path(...)`-driven scheduling with `CoverageStore` + `BranchGoal` scheduler.
3. Replace old plausible-leaf selection in `DataGenerator`.

Acceptance criteria:

- symbolic generation is no longer architecturally leaf-tree-centric
- branch scheduling is planner-anchored

Dependencies:

- WP15
- WP21

### WP23: Simplify `DataGenerator` Into A Coordinator
Goal:
Shrink `DataGenerator` so it orchestrates components instead of owning all symbolic logic.

Tasks:

1. Move remaining responsibilities into:
   - planner/branch extraction
   - execution recording
   - coverage store
   - scheduler
   - solve session
   - branch lowerers
   - materializer
2. Keep `DataGenerator` as a coordinator over those pieces.

Acceptance criteria:

- `DataGenerator` no longer owns global symbolic state and branch semantics directly

Dependencies:

- WP22

## Recommended PR / Milestone Sequence

### Milestone 1: Core Types And Narrow Branch Extraction
- WP1
- WP2
- WP3
- WP4
- WP5

### Milestone 2: Runtime Recording And Coverage For Filter / `OR` / `CASE`
- WP6
- WP7
- WP8
- WP9
- WP10
- WP11

### Milestone 3: Scheduling And Solve Sessions
- WP12
- WP13
- WP14
- WP15

### Milestone 4: Group / Aggregate Migration
- WP16
- WP17
- WP18

### Milestone 5: Join And Subquery Migration
- WP19
- WP20
- WP21

### Milestone 6: Full Symbolic Workflow Replacement
- WP22
- WP23

## Testing Strategy

### New test families to add
- branch-template extraction tests
- runtime branch-recording tests
- contribution-label tests
- coverage-store tests
- scheduler-priority tests
- solve-session tests
- branch-policy lowering tests
- end-to-end narrow symbolic branch tests

### Existing semantic source of truth
While redesigning group/join/having behavior, use the semantics in:
- `src/parseval/uexpr/checks.py`

not as code to preserve, but as behavior to replicate or improve.

## Guardrails

### Guardrail 1
Do not migrate joins/groups/subqueries before filter / `OR` / `CASE` prove the model.

### Guardrail 2
Do not let the new system fall back into implicit branch identity based on previous-step heuristics.

### Guardrail 3
Do not conflate:
- branch template
- branch instance
- branch goal

Those must remain separate types.

### Guardrail 4
Do not aim for global exhaustive branch-combination enumeration.
Use iterative branch-goal scheduling instead.

## Success Criteria
- [ ] branch templates are planner-anchored and deterministic
- [ ] runtime branch instances capture exact coverage for row/group/subquery contexts
- [ ] `OR` subpredicates are individually coverable
- [ ] each `CASE` arm is a distinct coverable target
- [ ] group-key and aggregate behavior are both modeled explicitly
- [ ] branch scheduling is config-driven and explicit
- [ ] solve attempts use isolated mutable state
- [ ] legacy leaf-centric control flow is no longer the architectural center
