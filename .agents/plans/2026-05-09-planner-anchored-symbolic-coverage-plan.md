# Planner-Anchored Symbolic Coverage and Solving Plan

## Objective
Redesign symbolic data generation around the planner tree so that symbolic generation is driven by:

1. structural branch sites extracted from each plan node
2. runtime branch observations collected by executing the current `Instance`
3. contribution analysis that determines which branches affect final outputs or required intermediate states
4. a scheduler that repeatedly selects plausible branches and tries to solve them under user-provided configuration limits

This design intentionally replaces the current tracer-first mental model with a planner-anchored execution-and-coverage model. The new source of truth should be:

- the plan tree for structure
- runtime execution for exact path/coverage observations
- explicit branch records for scheduling and solving

## Core Design Statement
The plan is a tree structure.

When an `Instance` is executed through that tree:

- we observe branch behavior at each relevant node and expression
- we record exact coverage data for those branches
- we determine whether each branch contributes to final outputs or to output-critical intermediate states
- we label uncovered-yet-possible branches as plausible
- we repeatedly try to solve plausible branches according to user-provided config

The system does **not** need to statically enumerate all possible global branch combinations in advance. Instead, it needs to:

- enumerate structural branch opportunities
- observe runtime branch realizations
- prioritize and solve plausible branch targets iteratively

That is the intended operational model.

## Why This Design

### Problem with planner-only coverage
The plan tree alone is not enough to infer exact covered paths.

Examples:
- `CASE WHEN ... THEN ... WHEN ... THEN ... ELSE ...`
- `a > 1 OR b < 2`
- joins with null-extension behavior
- grouped queries with aggregate-sensitive coverage
- correlated subqueries

Those require concrete instance-dependent execution information.

### Problem with the current tracer-first model
The current `UExprToConstraint` machinery reconstructs branch structure dynamically from runtime execution side effects. This makes branch identity and path attachment depend on:

- previous-step bookkeeping
- rowid indexes
- positive-node indexes
- fallback attachment heuristics

That is powerful, but too indirect and too brittle for long-term robustness.

### Better alternative
Anchor symbolic generation to the planner tree.

Use the planner to define:
- where branch sites exist
- what kind of branch each site represents
- what expressions and tables participate

Then use runtime execution over `Instance` to define:
- which branches were actually taken
- under what row/group/subquery context
- whether those branches contributed to output behavior

This preserves exact dynamic coverage while eliminating implicit branch identity.

## Design Principles

1. The planner tree is the structural backbone.
2. Branch identity must be stable and planner-anchored.
3. Exact coverage is runtime-dependent and must be recorded from execution.
4. Coverage should be tracked per branch site and per execution context.
5. Contribution must be analyzed explicitly, not guessed from branch existence alone.
6. The solver should target plausible branches iteratively under config limits, not exhaustively materialize every theoretical branch combination.
7. Legacy tracer/tree compatibility is not a constraint; the redesign may replace old symbolic infrastructure completely.

## Required Coverage Semantics
Per the design constraints clarified for this redesign:

### Disjunctions
- All subpredicates of `OR` must be covered individually.
- Coverage is not only the final truth value of the disjunction.
- We need explicit branch sites for each disjunct.

### CASE expressions
- We need one witness per arm.
- Coverage is per `WHEN` arm and `ELSE` arm, not only per final projected value.

### Group and aggregation logic
- Coverage must consider both:
  - group key behavior
  - aggregation-function behavior
- This includes group-size, group-count, null-sensitive aggregate behavior, duplicate-sensitive aggregate behavior, and HAVING predicates.

### Solver policy
- The system repeatedly tries to solve plausible branches.
- The number of attempts and thresholds come from user-provided config.
- Branch scheduling must therefore be explicit and configurable.

## Redesign Scope
This plan covers redesign of:

- planner-driven branch extraction
- runtime branch observation recording
- contribution analysis
- plausible branch scheduling
- branch-targeted solving
- eventual replacement of current `UExprToConstraint`-driven control flow

It does **not** require preserving the current uexpr tree as the primary abstraction.

## Current Workflow Summary

### Current planner role
`SymbolicScopeEncoder` executes a scope over the current instance and calls `UExprToConstraint.which_path(...)` to mutate a branch tree indirectly.

### Current uexpr role
`UExprToConstraint` stores:
- branch identity
- path tree topology
- branch coverage
- retry state
- path scheduling state

### Current generator role
`DataGenerator` coordinates:
- scope ordering
- subquery rewrites
- row seeding
- branch selection
- variable declaration
- SMT constraint declaration
- solver execution
- row materialization
- re-encoding

### Problem
Those roles are too entangled. The redesign will separate them.

## Proposed Architecture

## Layer 1: Planner Tree
Use existing planner-core:

- `Graph`
- `ScopeNode`
- `ScopePlan`
- `Step`

This remains the structural tree.

### Responsibilities
- define scope structure
- define step structure
- provide stable scope and step identities
- expose step-local expressions to branch extraction

### Non-responsibilities
- no direct coverage decisions
- no branch scheduling
- no solver state

## Layer 2: Branch Extraction
For each `Step` in each `ScopePlan`, derive explicit branch sites.

### New concept: `BranchTemplate`
A `BranchTemplate` is a structural, planner-anchored branch site.

It identifies:
- where a branch exists
- what semantic kind it has
- what expression or operator drives it
- what dimensions of coverage matter

### Example branch-template kinds
- `predicate_atom`
- `predicate_disjunction_member`
- `predicate_disjunction_outcome`
- `case_when_arm`
- `case_else_arm`
- `join_true`
- `join_left_preserve`
- `join_right_preserve`
- `group_size`
- `group_count`
- `group_null`
- `group_duplicate`
- `aggregate_behavior`
- `having_atom`
- `sort_max`
- `sort_min`
- `subquery_exists`
- `subquery_in_membership`
- `subquery_scalar_result`

### Required `BranchTemplate` fields
Suggested shape:

```python
@dataclass(frozen=True)
class BranchTemplate:
    template_id: str
    scope_id: int
    step_id: str
    step_type: str
    kind: str
    bit: PBit | None
    expression_sql: str
    role: str
    source_tables: tuple[str, ...]
    referenced_columns: tuple[str, ...]
    metadata: dict[str, Any]
```

### Important note
`BranchTemplate` is **not** a runtime branch hit.
It is the structural definition of a branch opportunity.

## Layer 3: Runtime Branch Observation
When executing an `Instance` through the plan tree, record exact dynamic branch realizations.

### New concept: `BranchInstance`
A `BranchInstance` is one runtime observation of one `BranchTemplate`.

### Required properties
- which branch template it corresponds to
- what execution context it was evaluated under
- whether it was taken
- what symbolic/concrete evidence was observed
- whether it contributed to output behavior

### New concept: `ExecutionContextKey`
This is essential.

Coverage must be attached to a stable normalized context.

#### Required context types
- row context
  - tuple of rowids
- join context
  - tuple of participating rowids, normalized by side/table
- group context
  - scope id + normalized group-key values
- subquery context
  - scope id + outer rowids
- projection / CASE context
  - source rowids

### Suggested shape
```python
@dataclass(frozen=True)
class ExecutionContextKey:
    scope_id: int
    context_kind: str
    values: tuple[Any, ...]
```

```python
@dataclass
class BranchInstance:
    template_id: str
    context_key: ExecutionContextKey
    taken: bool | BranchType
    hit_count: int
    symbolic_values: tuple[Any, ...]
    concrete_values: tuple[Any, ...]
    rowids: tuple[Any, ...]
    contribution: str
    metadata: dict[str, Any]
```

## Layer 4: Contribution Analysis
The system must know whether a branch matters for final outputs.

### Important refinement
Contribution should not be only boolean.

A branch can be:
- `active_contributing`
  - directly affects current final outputs
- `active_noncontributing`
  - observed, but does not currently affect final outputs
- `potentially_contributing`
  - not currently contributing, but structurally relevant and could affect outputs under another instance
- `infeasible`
  - structurally impossible or contradicted

### Why this matters
This is the bridge between coverage and scheduling.
It lets the scheduler prioritize meaningful branches without losing potentially important ones.

### Suggested rules by operator family

#### Filter
- contributes if it affects row survival
- potentially contributes if not yet taken but could change row survival

#### CASE / projection
- contributes if it affects the projected output value
- each arm is a distinct coverage target

#### Join
- contributes if it affects:
  - row survival
  - null-extension behavior
  - downstream row multiplicity

#### Group / aggregate
- contributes if it affects:
  - group existence
  - group key formation
  - aggregate result value
  - HAVING outcome

#### Sort / top-k
- contributes if it affects:
  - which row can appear in final limited output
  - max/min witness selection

#### Subquery
- contributes if it affects:
  - outer predicate result
  - scalar replacement value
  - `EXISTS` / `IN` outcome

## Layer 5: Coverage Store
Store branch templates and branch instances explicitly.

### New concept: `CoverageStore`
Responsibilities:
- index branch templates by scope and step
- index branch instances by template and execution context
- aggregate hit counts
- expose uncovered / partially covered / infeasible branches

### Suggested indexes
- `templates_by_scope`
- `templates_by_step`
- `instances_by_template`
- `instances_by_context`
- `coverage_stats_by_template`

### Important distinction
Coverage should be tracked at two levels:

#### Template-level coverage
Questions:
- Has this `CASE` arm ever been covered?
- Has this `OR` subpredicate ever been individually covered?
- Has this join-left branch ever been realized?

#### Context-level coverage
Questions:
- Has this branch been covered for a distinct row/group/subquery context?
- Are we only seeing one repeated witness?

This distinction should be explicit in the design.

## Layer 6: Plausible Branch Scheduling
The old concept of “plausible branch” remains useful, but should be redefined around branch records.

### New concept: `BranchGoal`
This is the solve target selected by the scheduler.

It is not necessarily one exact runtime branch instance.

It can represent:
- one uncovered branch template
- one under-covered branch template for a relevant context family
- one operator-specific branch target

### Suggested shape
```python
@dataclass(frozen=True)
class BranchGoal:
    goal_id: str
    template_id: str
    scope_id: int
    step_id: str
    kind: str
    target_bit: PBit | None
    contribution_priority: str
    context_selector: dict[str, Any]
    retry_budget: int
    metadata: dict[str, Any]
```

### Scheduling policy
The scheduler should rank goals using:
- uncovered before covered
- active/potentially contributing before noncontributing
- low-hit before saturated
- operator-specific thresholds from config
- branch-kind-specific policies

### Important implication
We do **not** need to enumerate all global branch combinations.
We need to schedule branch goals intelligently.

## Layer 7: Solve Session
Each branch-solving attempt should use isolated mutable state.

### New concept: `SolveSession`
Responsibilities:
- declare variables
- declare constraints
- manage FK/PK/uniqueness constraints
- manage bound-table constraints
- materialize solver input

### Why
This removes the current generator-global mutable state problem.

### Suggested shape
```python
class SolveSession:
    def __init__(self, instance, scope_goal, planner_context, config): ...

    def declare_variable(...): ...
    def declare_constraint(...): ...
    def build_solver(...): ...
    def solve(...): ...
```

## Layer 8: Branch Policies
The old `Check` / `Declare` split in `src/parseval/uexpr/checks.py` is a strong signal.

We should preserve that conceptual pairing in the new design.

### New concept: `BranchPolicy`
Each branch kind should have a policy with two responsibilities:

1. coverage evaluation
2. solve lowering

### Suggested interface
```python
class BranchPolicy(Protocol):
    def evaluate_coverage(
        self,
        template: BranchTemplate,
        instances: Sequence[BranchInstance],
        config,
    ) -> CoverageDecision: ...

    def lower_goal(
        self,
        goal: BranchGoal,
        runtime_snapshot,
        session: SolveSession,
    ) -> LoweringResult: ...
```

### Why this is better than the current design
- branch semantics become planner-anchored
- coverage logic becomes explicit
- solver lowering logic becomes explicit
- operator-specific behavior is extensible

## Concrete Branch Extraction Requirements

### Filter / WHERE
Extract:
- atomic predicates
- disjunction members
- optional full disjunction outcome

Requirement:
- every subpredicate of `OR` must be coverable

### Projection / CASE
Extract:
- each `WHEN` arm
- `ELSE` arm
- null-sensitive output targets if needed
- duplicate-sensitive output targets if needed

Requirement:
- one witness per arm

### Join
Extract:
- match branch
- left-preserve branch
- right-preserve branch
- residual predicate branches if present

### Group / aggregate / HAVING
Extract:
- group-size branch
- group-count branch
- group-null branch
- group-duplicate branch
- each HAVING predicate atom
- aggregate-sensitive branches as needed

Requirement:
- consider both group key behavior and aggregation behavior

### Sort / top-k
Extract:
- max witness
- min witness
- limit-sensitive selection branches where relevant

### Subquery
Extract:
- exists / not exists
- `IN` membership
- scalar result witness
- correlated dependency-sensitive branches

## Concrete Runtime Recording Requirements

### For each branch instance, record:
- template id
- execution context key
- taken bit / arm id
- relevant rowids
- symbolic values or symbolic expressions
- concrete values if available
- contribution label

### For grouped contexts, record:
- normalized group key
- aggregate-relevant observed values
- whether group survived HAVING

### For projection CASE contexts, record:
- rowids
- selected arm
- produced concrete result if available

## Solver Loop Design

### New symbolic workflow
For each scope:

1. Build `ScopePlan`
2. Extract `BranchTemplate`s for all steps
3. Execute current instance through the scope
4. Record `BranchInstance`s
5. Analyze contribution and coverage
6. Build a prioritized queue of `BranchGoal`s
7. For each goal, under config limits:
   - create `SolveSession`
   - lower the branch goal to solver constraints
   - solve
   - materialize rows
   - rerun scope execution
   - update coverage
8. Stop when:
   - goals are exhausted
   - thresholds are satisfied
   - timeout/config limits are reached

### Config integration
User-provided config should control:
- max tries per branch goal
- positive/negative thresholds
- null threshold
- duplicate threshold
- group size/count thresholds
- branch-priority preferences if desired

## Proposed File / Module Direction

This is an architectural target, not a mandatory final file layout:

### Planner side
- existing `plan/graph.py`
- existing `plan/scope_plan.py`
- richer step/branch extraction helpers inside planner core or nearby

### Symbolic side
- `symbolic/branch_templates.py`
- `symbolic/runtime_trace.py`
- `symbolic/coverage_store.py`
- `symbolic/branch_policies.py`
- `symbolic/branch_scheduler.py`
- `symbolic/solve_session.py`
- `symbolic/materializer.py`
- `symbolic/generator.py` or an equivalent refactor target

The exact file layout can be refined later, but the responsibility split should follow this structure.

## Migration Strategy

### Phase 1: Planner-anchored branch templates
- add branch extraction on top of `ScopePlan`
- do not change solving yet

### Phase 2: Runtime branch recording
- introduce branch-instance recording during symbolic execution
- keep old tracer available temporarily if useful

### Phase 3: Coverage store
- replace direct plausible-leaf bookkeeping with explicit template + instance coverage records

### Phase 4: Branch goals and scheduler
- replace `next_path(...)`-style direct leaf selection with `BranchGoal` scheduling

### Phase 5: Solve session extraction
- move mutable solve-attempt state out of `DataGenerator`

### Phase 6: Branch policy lowerers
- replace ad hoc declaration logic with branch-policy-driven lowering

### Phase 7: Remove legacy symbolic path model
- once planner-anchored branch scheduling is stable, remove legacy leaf/tree control flow

## Risks and Challenges

### Risk 1: Overly generic branch abstractions
Mitigation:
- keep branch kinds explicit and operator-aware

### Risk 2: Context-key instability
Mitigation:
- define normalized execution context keys early
- distinguish row/join/group/subquery contexts explicitly

### Risk 3: Overcommitting to full branch-combination enumeration
Mitigation:
- schedule branch goals iteratively instead of computing all global combinations

### Risk 4: Contribution analysis becoming too expensive
Mitigation:
- start with conservative operator-local contribution rules
- delay full provenance unless necessary

### Risk 5: Group and HAVING behavior becoming underspecified
Mitigation:
- preserve semantics from `uexpr/checks.py` as the first source of truth for branch-policy design

## Success Criteria
- [ ] every plan node can expose branch templates extracted from its expressions
- [ ] runtime execution records exact branch instances using normalized context keys
- [ ] disjunction subpredicates are tracked individually
- [ ] each CASE arm is a distinct coverable branch goal
- [ ] group key and aggregation behavior are both represented in coverage and solving
- [ ] branch contribution is analyzed explicitly
- [ ] plausible branch scheduling is planner-anchored and config-driven
- [ ] solve attempts use isolated session state
- [ ] legacy tracer-centric control flow is no longer the architectural center

## Immediate Next Design Slice
The first implementation slice should likely be:

1. define `BranchTemplate`
2. define `ExecutionContextKey`
3. add branch extraction for:
   - filter predicates
   - OR subpredicates
   - CASE arms
4. add a runtime record format for branch instances
5. build one small coverage store for those branch kinds only

This gives a narrow but meaningful first slice before tackling joins/groups/subqueries.

## Sources
- Local code: `src/parseval/data_generator.py`
- Local code: `src/parseval/uexpr/uexprs.py`
- Local code: `src/parseval/uexpr/checks.py`
- Local code: `src/parseval/uexpr/coverage.py`
- Local code: `src/parseval/plan/scope_plan.py`
- Local plan: `.agents/plans/2026-05-09-symbolic-generator-redesign-plan.md`
