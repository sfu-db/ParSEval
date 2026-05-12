# Symbolic Generator Redesign Plan

## Objective
Refine the symbolic data generation pipeline in `src/parseval/data_generator.py` so it is more robust, more predictable, and better aligned with the step-centered planner architecture.

The redesign target is not only code cleanup. It is to make symbolic generation:

- planner-driven rather than tracer-driven
- less stateful and mutation-heavy
- more explicit about scope/step/branch solving phases
- easier to extend for unsupported SQL shapes

## Research Summary

### Current symbolic workflow
- `build_graph_from_scopes` determines inter-scope order.
- `DataGenerator.generate()` iterates scopes in dependency order and performs:
  - subquery binding substitution
  - optional correlated row seeding
  - optional scalar/literal/domain seeding
  - `_solve_scope(...)`
- `_solve_scope(...)` performs:
  1. encode one scope with `SymbolicScopeEncoder`
  2. collect a `UExprToConstraint` trace
  3. if no leaves exist, bootstrap rows and re-encode
  4. repeatedly call `tracer.next_path(...)`
  5. for each plausible branch:
     - derive operator constraints
     - declare symbolic variables
     - declare DB / FK / PK / uniqueness constraints
     - solve with `SMTSolver`
     - materialize new rows
     - reset generator state
     - re-encode the scope
  6. post-repair null outputs if required
  7. materialize a `SubqueryBinding`

### Current tracer / uexpr workflow
- `SymbolicScopeEncoder` calls `tracer.which_path(...)` during actual data execution.
- `UExprToConstraint` incrementally builds a tree of:
  - `Constraint`
  - `PlausibleBranch`
- path attachment is inferred through:
  - previous step bookkeeping
  - positive-node index
  - rowid index fallback
- coverage and branch prioritization are computed in `next_path(...)` through `CoverageCalculator`

### Architectural strengths
- The current engine already distinguishes:
  - scope ordering
  - operator-level symbolic coverage
  - plausibility / coverage-driven search
  - SMT solving
- OperatorRuleRegistry is a good seam for per-operator branch semantics.
- The new planner core (`ScopePlan`, step annotations) creates a better source of truth than raw `sqlglot` expressions alone.

### Architectural weaknesses
- `DataGenerator` currently owns too many responsibilities:
  - planner orchestration
  - branch search strategy
  - variable declaration
  - database constraint declaration
  - row materialization
  - subquery binding
  - pre-seeding heuristics
  - null repair
- `_solve_scope(...)` repeatedly re-encodes the whole scope after every solve attempt.
- `UExprToConstraint.which_path(...)` relies on runtime attachment heuristics using previous-step state and rowid indexes rather than an explicit planner path model.
- Symbolic variable declaration is global mutable state on the generator, not scoped to one solve attempt.
- Scope solving mixes:
  - planner execution
  - branch enumeration
  - candidate synthesis
  - persistence into `Instance`
- The current branch model is coverage-oriented but not plan-oriented: it treats each plausible leaf as a solve target without an explicit intermediate branch request object.

## Current Workflow Model

### Phase 1: Encode
- Input:
  - `ScopeNode`
  - planner context
  - current database instance
- Output:
  - execution context
  - tracer tree

### Phase 2: Select branch
- Input:
  - tracer leaves
  - coverage stats
  - generator config thresholds
- Output:
  - one `PlausibleBranch`

### Phase 3: Lower branch to solve request
- Input:
  - plausible branch
  - operator path to root
  - DB schema/domain metadata
- Output:
  - symbolic variables
  - SMT constraints

### Phase 4: Solve
- Input:
  - variable declarations
  - branch constraints
  - DB integrity constraints
- Output:
  - concrete assignments or UNSAT

### Phase 5: Materialize / iterate
- Input:
  - solver result
- Output:
  - mutated `Instance`
  - re-encode and continue

The redesign should make these phases explicit in code instead of embedding them into one large generator object.

## Key Design Problems

### 1. Tracer structure is inferred from execution side effects
`UExprToConstraint.which_path(...)` reconstructs branch attachment using:
- previous step tracking
- rowid indexing
- positive-node indexes
- fallbacks to positive branches

This is clever but brittle. It means the symbolic branch graph is reconstructed from dynamic execution traces rather than anchored directly to the planner structure.

### 2. Scope solving is too stateful
`DataGenerator` mutates:
- `variables`
- `constraints`
- variable registries
- active bound tables
- scope results
- instance rows

This makes it hard to reason about one solve attempt independently from the rest of generation.

### 3. Variable declaration is too implicit
Variables are declared by walking branch conditions and foreign-key closures after branch selection. This is late and highly procedural. The planner already knows step inputs and referenced columns; variable planning should move earlier and be more structured.

### 4. Re-encoding is expensive and semantically overloaded
The generator re-encodes the entire scope after each solve attempt because the tracer is both:
- the branch search structure
- the runtime execution result

A more robust design would separate:
- static branch opportunities derived from the planner
- dynamic branch coverage / witness status derived from execution

### 5. Subquery solving and root-scope solving share too much machinery
Subqueries, scalar bindings, correlated scopes, and top-level scopes all go through nearly the same solve loop even though their solve goals differ significantly.

## Redesign Direction

## Layer 1: Planner-derived symbolic plan
Add a symbolic-facing plan layer that consumes `ScopePlan` and produces explicit symbolic step descriptors.

Suggested concept:
- `SymbolicScopePlan`

Responsibilities:
- hold `ScopePlan`
- expose symbolic-relevant step metadata
- precompute branchable step conditions
- record correlation/binding needs
- precompute referenced columns per branchable step

This should be derived from planner-core, not from runtime execution.

## Layer 2: Explicit branch requests
Replace “solve a plausible leaf directly” with an explicit intermediate object.

Suggested concept:
- `BranchSolveRequest`

Fields:
- scope id
- target step id
- target bit
- branch pattern
- referenced columns
- operator constraints
- required bound tables
- solve intent

This object should be produced after branch selection and before variable declaration.

Why:
- easier logging
- easier testing
- easier retry policy
- easier specialization by operator type

## Layer 3: Scoped solve state
Move mutable solve-attempt state off `DataGenerator`.

Suggested concept:
- `SolveSession`

Responsibilities:
- own:
  - variables
  - declared constraints
  - alias maps
  - bound variable registries
- expose:
  - `declare_variable(...)`
  - `declare_constraint(...)`
  - `build_solver_input(...)`

This makes each attempt isolated and testable.

## Layer 4: Separate planner execution from branch exploration
Right now the tracer tree is discovered by executing current rows through the symbolic encoder.

A stronger design would split symbolic scope work into:

1. `SymbolicExecutionRecorder`
   - runs the current instance through `SymbolicScopeEncoder`
   - records branch hits / rowids / coverage

2. `BranchExplorer`
   - reads the recorder state
   - chooses next unexplored or weakly-covered branch
   - produces `BranchSolveRequest`

This keeps “what happened” separate from “what should we try next”.

## Layer 5: Operator lowerers
`OperatorRuleRegistry` is already pointing in the right direction. Push further.

Suggested concepts:
- `PredicateBranchLowerer`
- `JoinBranchLowerer`
- `AggregateBranchLowerer`
- `SortBranchLowerer`
- `NullBranchLowerer`
- `DuplicateBranchLowerer`

Each lowerer should:
- validate whether it can lower the target branch
- return:
  - referenced columns
  - symbolic constraints
  - post-solve obligations if needed

This is a more scalable replacement for growing branch logic directly inside `DataGenerator`.

## Layer 6: Scope goal policies
Not all scopes need the same solve strategy.

Define explicit goal policies:
- `TopLevelWitnessPolicy`
- `ScalarSubqueryPolicy`
- `ExistsSubqueryPolicy`
- `CorrelatedSubqueryPolicy`
- `SetOperationPolicy`

These policies decide:
- whether to bootstrap rows
- whether to seed literals
- whether to require non-null projected outputs
- whether to stop after first satisfying witness
- how to materialize bindings

This will reduce the current special-case branching inside `generate()` and `_solve_scope(...)`.

## Concrete Recommendations

### Recommendation 1: Introduce `SymbolicScopePlan`
Use `ScopePlan` plus planner annotations to precompute:
- branchable steps
- branch conditions
- required columns
- output binding metadata

This should replace ad hoc introspection from the tracer path where possible.

### Recommendation 2: Replace generator-global solve state with `SolveSession`
Every attempt to solve one plausible branch should create a fresh session.

Benefits:
- avoids leakage between attempts
- easier debugging and testing
- makes timeout / retry / backtracking cleaner

### Recommendation 3: Turn `_solve_scope(...)` into a coordinator, not an engine
Refactor `_solve_scope(...)` into high-level orchestration only:
- encode
- explore
- solve
- materialize

Push detailed work into helpers/classes.

### Recommendation 4: Make branch attachment planner-aware
Instead of relying primarily on rowid and previous-step heuristics in `which_path(...)`, use the explicit step ordering and step identity from `ScopePlan` to anchor tracer nodes.

Even if rowids remain necessary for joins and group semantics, attachment should be constrained by planner structure first.

### Recommendation 5: Separate coverage bookkeeping from branch candidate identity
The current leaf is doing too much:
- branch identity
- branch feasibility
- branch coverage
- branch retry state

Introduce a clearer distinction between:
- branch identity
- branch runtime observations
- branch scheduling state

### Recommendation 6: Planner-derived variable planning
Variable declaration should become more deterministic:
- derive step-level referenced columns from `ScopePlan`
- derive FK closure from planner-visible table set
- distinguish:
  - branch-driving variables
  - integrity-only variables
  - bound-table variables

### Recommendation 7: Keep seeding as a separate pre-solver component
Literal/scalar/correlated seeding is useful, but it should become an explicit pre-solve phase with its own contract.

Suggested component:
- `ScopeSeeder`

Responsibilities:
- literal row seeding
- scalar alignment seeding
- correlated `EXISTS` seed rows
- domain hint propagation

Then the solver can assume seeding is already done.

## Proposed Target Structure

### Planner side
- existing `ScopePlan`
- richer `StepAnnotations`
- add symbolic annotations where useful

### Symbolic side
- `symbolic_plan.py`
  - `SymbolicScopePlan`
- `symbolic_trace.py`
  - execution recorder / tracer adapter
- `symbolic_explorer.py`
  - branch scheduling and coverage policy
- `symbolic_lowering.py`
  - branch lowerers
- `symbolic_session.py`
  - scoped variable/constraint state
- `symbolic_materializer.py`
  - create/update rows from solver result

`DataGenerator` would then become a coordinator over these components.

## Lowest-Risk Refactor Sequence

1. Extract symbolic solve state from `DataGenerator` into `SolveSession`
2. Extract branch-selection logic from `UExprToConstraint.next_path(...)` caller into a `BranchExplorer`
3. Add planner-derived symbolic branch descriptors from `ScopePlan`
4. Make `OperatorRuleRegistry` return richer branch-lowering results
5. Separate scope seeding from scope solving
6. Rework tracer attachment to use planner step identity more explicitly

## Success Criteria
- [ ] symbolic generation has explicit per-attempt solve state
- [ ] scope solving is decomposed into planner, explorer, solver, materializer phases
- [ ] planner structure, not runtime heuristics alone, anchors symbolic branch identity
- [ ] operator branch logic is extensible through lowerers
- [ ] subquery / top-level / correlated scope policies are explicit
- [ ] re-encoding and mutation loops are easier to reason about and debug
