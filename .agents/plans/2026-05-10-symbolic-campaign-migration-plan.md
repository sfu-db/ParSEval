# Symbolic Campaign Migration Plan

## Objective
Refactor the current symbolic subsystem into an instance-driven branch coverage
campaign that:

1. executes the query on the current live `Instance`
2. records structural branch outcomes and execution contexts
3. identifies uncovered positive and negative branch targets
4. builds new branch-targeted constraints
5. delegates concrete row realization to `src/parseval/solver`
6. mutates the same live instance
7. repeats until coverage goals or iteration limits are reached

This plan assumes the correct architectural split is:

- `src/parseval/symbolic/`: coverage engine and branch-targeted constraint builder
- `src/parseval/solver/`: concrete realization engine

## Design Summary

### Symbolic owns
- branch extraction
- structural branch identity
- execution observation
- branch/context coverage aggregation
- branch-goal scheduling
- branch-targeted constraint building
- campaign orchestration

### Solver owns
- low-level SMT solving
- heuristic realization when SMT is insufficient
- live-instance row mutation
- row seeding / bootstrapping
- future minimal-mutation policy

### Main Loop
The desired end-state loop is:

1. run current query/scopes on the current instance
2. collect `ExecutionTrace`
3. update `CoverageStore`
4. ask scheduler for next uncovered or weakly-covered goal
5. build a branch-targeted constraint set
6. ask solver to realize it on the live instance
7. repeat

## Why This Refactor Is Needed
- `SymbolicDataGenerator` is still too generator-shaped and too witness-oriented
- branch lowering is too close to "make this predicate satisfiable once"
- the current symbolic flow does not yet treat positive and negative outcomes as
  the primary progress metric
- the solver boundary is now cleaner, but symbolic still needs a proper
  campaign-oriented top layer and a stronger constraint builder

## Non-Goals
- rewriting all operator families in one pass
- immediate full support for correlated subqueries and grouped branches
- removing all compatibility wrappers during the first migration
- optimizing performance before the architectural split is stable

## Target Public API

### New API
Introduce:

- `SymbolicCampaign`
- `SymbolicCampaignResult`

Suggested usage:

```python
campaign = SymbolicCampaign(
    expr=expr,
    instance=instance,
    dialect="sqlite",
)
result = campaign.run(max_iterations=20)
```

### Compatibility API
Keep temporarily:

- `SymbolicDataGenerator`
- `generate_symbolic_data`

Compatibility direction:
- `SymbolicDataGenerator.generate()` should delegate to `SymbolicCampaign.run()`
- keep return shapes stable during migration

## Planned Module Layout

### New / Expanded Symbolic Modules
- `src/parseval/symbolic/campaign.py`
  - top-level iterative orchestration
- `src/parseval/symbolic/engine.py`
  - one execution pass over the current instance
- `src/parseval/symbolic/builder.py`
  - branch-targeted constraint builder
- `src/parseval/symbolic/coverage.py`
  - richer outcome/context coverage metrics
- `src/parseval/symbolic/scheduler.py`
  - goal prioritization for both outcomes
- `src/parseval/symbolic/runtime.py`
  - dependency/scope bindings and campaign artifacts

### Existing Modules To Shrink
- `src/parseval/symbolic/generator.py`
  - compatibility wrapper only
- `src/parseval/symbolic/lowerer.py`
  - either slim adapter or eventually replaced by `builder.py`
- `src/parseval/symbolic/session.py`
  - compatibility alias only
- `src/parseval/symbolic/mutator.py`
  - compatibility alias only

### Solver Modules
- `src/parseval/solver/instance.py`
  - continue expanding as the concrete synthesis boundary

## Execution Stages

## Stage 1: Freeze Architectural Boundaries

### Goal
Prevent further symbolic/solver mixing before deeper redesign.

### Tasks
- keep all concrete-data realization inside `src/parseval/solver/instance.py`
- stop adding new realization logic to `src/parseval/symbolic/generator.py`
- keep `src/parseval/symbolic/session.py` and `mutator.py` as compatibility wrappers only
- add comments/docstrings where a module is temporary or compatibility-only

### Files
- `src/parseval/symbolic/generator.py`
- `src/parseval/symbolic/session.py`
- `src/parseval/symbolic/mutator.py`
- `src/parseval/solver/instance.py`

### Exit Criteria
- no new heuristic or mutation policy exists in symbolic modules
- solver remains the only owner of concrete row synthesis

## Stage 2: Introduce Symbolic Campaign

### Goal
Replace the generator-centric top-level loop with a campaign-oriented loop.

### Tasks
- create `src/parseval/symbolic/campaign.py`
- introduce:
  - `SymbolicCampaign`
  - `SymbolicCampaignResult`
  - `CampaignIterationRecord`
  - `GoalAttemptRecord`
- move iteration state out of `SymbolicDataGenerator`
- make `SymbolicDataGenerator` a thin compatibility wrapper over campaign

### Responsibilities In `SymbolicCampaign`
- build graph from scopes
- manage dependency execution order
- run execution engine for each iteration
- update coverage
- schedule goals
- build constraints
- delegate realization to solver
- stop on convergence / iteration limit / failure budget

### Files
- add `src/parseval/symbolic/campaign.py`
- trim `src/parseval/symbolic/generator.py`
- update `src/parseval/symbolic/__init__.py`

### Tests
- add `tests/symbolic/test_campaign.py`

### Exit Criteria
- orchestration lives in `campaign.py`
- `generator.py` contains compatibility surface only

## Stage 3: Replace Goal Lowering With Constraint Builder

### Goal
Evolve from template-to-predicate lowering into branch-targeted constraint
building.

### Tasks
- create `src/parseval/symbolic/builder.py`
- add builder-facing types:
  - `ConstraintBuildRequest`
  - `ConstraintBuildResult`
  - `ConstraintBuildHint`
- support four constraint layers:
  - structural constraints
  - path feasibility constraints
  - target outcome constraints
  - optional stability/preservation constraints

### First Supported Branch Families
- filter predicates
- `OR` disjuncts
- `CASE WHEN` predicates
- join key equalities
- dependency scope scalar / `IN` / `EXISTS` bindings
- temporal and `strftime('%Y', ...)` predicate normalization

### Migration Strategy
- keep `BranchGoalLowerer` temporarily
- reimplement it as a thin adapter around `ConstraintBuilder`
- later deprecate `lowerer.py` once tests use builder semantics directly

### Files
- add `src/parseval/symbolic/builder.py`
- update `src/parseval/symbolic/lowerer.py`
- possibly update `src/parseval/symbolic/runtime.py`

### Tests
- add `tests/symbolic/test_constraint_builder.py`
- extend `tests/symbolic/test_goal_lowering.py`

### Exit Criteria
- symbolic goal solving is driven by builder output, not direct predicate-only lowering

## Stage 4: Make Coverage Outcome-First

### Goal
Treat `taken=True` and `taken=False` as first-class coverage targets.

### Tasks
- extend `CoverageStore` with outcome-aware queries:
  - `is_fully_outcome_covered`
  - `outcome_hit_count`
  - `distinct_context_count(..., desired_taken=...)`
  - `coverage_gap_summary`
- track and expose weak coverage separately for true and false outcomes
- update scheduler to explicitly rank both outcomes

### Scheduler Policy
Suggested default priority:
1. uncovered true
2. uncovered false
3. weakly-covered true
4. weakly-covered false
5. contributing new contexts
6. low estimated mutation-cost goals

### Files
- `src/parseval/symbolic/coverage.py`
- `src/parseval/symbolic/scheduler.py`
- `src/parseval/symbolic/types.py`

### Tests
- extend:
  - `tests/symbolic/test_coverage.py`
  - `tests/symbolic/test_scheduler.py`
  - `tests/symbolic/test_generator.py`

### Exit Criteria
- campaign progress is measured by branch outcomes, not just witness existence

## Stage 5: Introduce Execution Engine Boundary

### Goal
Split one-pass execution from multi-iteration orchestration.

### Tasks
- add `src/parseval/symbolic/engine.py`
- move one-pass execution into:
  - `SymbolicExecutionEngine`
- keep `SymbolicScopeEncoder` as the internal execution primitive
- return a structured execution result:
  - trace
  - coverage delta
  - materialized binding
  - dependency artifacts

### Files
- add `src/parseval/symbolic/engine.py`
- trim `src/parseval/symbolic/generator.py`
- possibly trim `src/parseval/symbolic/encoder.py`

### Tests
- add `tests/symbolic/test_engine.py`

### Exit Criteria
- one execution pass can be invoked independently of the campaign loop

## Stage 6: Model Subquery / Dependency Bindings Explicitly

### Goal
Support multi-scope solving intentionally rather than opportunistically.

### Tasks
- expand `src/parseval/symbolic/runtime.py`
- add first-class binding types:
  - scalar binding
  - `IN` binding set
  - `EXISTS` binding
  - future correlated binding
- make builder consume these bindings directly
- allow scheduler to prioritize dependency scopes when parent goals depend on them

### Files
- `src/parseval/symbolic/runtime.py`
- `src/parseval/symbolic/campaign.py`
- `src/parseval/symbolic/builder.py`

### Tests
- add subquery-oriented tests:
  - scalar subquery
  - `IN (subquery)`
  - `EXISTS`
  - `NOT EXISTS`

### Exit Criteria
- dependent scope outputs are explicit runtime artifacts, not informal SQL rewrites

## Stage 7: Add Join and Outer Join Branch Semantics

### Goal
Model join behavior as coverage.

### Tasks
- extend extractor/recorder/model to support:
  - inner join match
  - left join matched branch
  - left join unmatched branch
  - join filter true/false
- decide whether these become new branch kinds or new context metadata first

### Files
- `src/parseval/symbolic/extractor.py`
- `src/parseval/symbolic/recorder.py`
- `src/parseval/symbolic/types.py`
- `src/parseval/symbolic/builder.py`

### Tests
- add `tests/symbolic/test_join_branch_coverage.py`

### Exit Criteria
- join-heavy workloads are covered as branch work, not only row-feasibility work

## Stage 8: Add Group / Having Coverage

### Goal
Cover grouped branch logic, not only row-level predicates.

### Tasks
- extend `ExecutionContextKey` real use of `group_key`
- add branch kinds for:
  - `HAVING`
  - aggregate threshold predicates
  - grouped `CASE`
- add grouped execution contexts to coverage store and recorder

### Files
- `src/parseval/symbolic/types.py`
- `src/parseval/symbolic/coverage.py`
- `src/parseval/symbolic/recorder.py`
- `src/parseval/symbolic/builder.py`

### Tests
- add `tests/symbolic/test_group_branch_coverage.py`

### Exit Criteria
- grouped queries can be targeted by the symbolic campaign directly

## Stage 9: Solver-Side Mutation Cost Policy

### Goal
Make solver realization more principled than "any satisfying mutation".

### Tasks
- add policy knobs to `InstanceDrivenSolver`:
  - prefer new rows
  - prefer row edits
  - minimize touched tables
  - minimize number of inserted rows
  - preserve already-covered rows if possible
- record mutation result details:
  - inserted rows
  - edited rows
  - strategy used
  - estimated mutation cost

### Files
- `src/parseval/solver/instance.py`
- future `src/parseval/solver/policy.py` if needed

### Tests
- add `tests/solver/test_mutation_policy.py`

### Exit Criteria
- solver can make coverage-oriented but controlled mutation decisions

## Stage 10: Public API Cleanup

### Goal
Align names with actual architecture.

### Tasks
- make `SymbolicCampaign` the preferred public API
- keep `SymbolicDataGenerator` as deprecated adapter
- keep package-level import compatibility for one migration cycle

### Files
- `src/parseval/symbolic/__init__.py`
- `src/parseval/symbolic/generator.py`

### Exit Criteria
- public API no longer implies "simple data generation" when the module is really a campaign engine

## Exact File Creation / Migration Checklist

### Create
- `src/parseval/symbolic/campaign.py`
- `src/parseval/symbolic/engine.py`
- `src/parseval/symbolic/builder.py`
- `tests/symbolic/test_campaign.py`
- `tests/symbolic/test_engine.py`
- `tests/symbolic/test_constraint_builder.py`
- `tests/symbolic/test_join_branch_coverage.py`
- `tests/symbolic/test_group_branch_coverage.py`
- `tests/solver/test_mutation_policy.py`

### Shrink / Re-scope
- `src/parseval/symbolic/generator.py`
- `src/parseval/symbolic/lowerer.py`
- `src/parseval/symbolic/session.py`
- `src/parseval/symbolic/mutator.py`

### Extend
- `src/parseval/symbolic/coverage.py`
- `src/parseval/symbolic/scheduler.py`
- `src/parseval/symbolic/runtime.py`
- `src/parseval/symbolic/types.py`
- `src/parseval/symbolic/extractor.py`
- `src/parseval/symbolic/recorder.py`
- `src/parseval/solver/instance.py`

## Validation Plan

### Core Symbolic Tests
- `python -m unittest tests.symbolic.test_generator`
- `python -m unittest tests.symbolic.test_solve_session`
- `python -m unittest tests.symbolic.test_scheduler`
- `python -m unittest tests.symbolic.test_coverage`
- `python -m unittest tests.symbolic.test_branch_recording`
- `python -m unittest tests.symbolic.test_goal_lowering`

### New Tests To Add As Refactor Proceeds
- `python -m unittest tests.solver.test_instance_driven`
- `python -m unittest tests.solver.test_mutation_policy`
- `python -m unittest tests.symbolic.test_campaign`
- `python -m unittest tests.symbolic.test_engine`
- `python -m unittest tests.symbolic.test_constraint_builder`

### Bounded Integration Probes
Maintain bounded BIRD probes by category:
- early join-heavy prefix
- scalar subquery prefix
- temporal / `strftime` prefix
- later grouped-query prefix

### Full Integration
- `python -m unittest tests.symbolic.test_symbolic_bird`

## Success Criteria
- [ ] `src/parseval/symbolic` no longer owns concrete realization policy
- [ ] symbolic campaign is instance-driven and iterative
- [ ] positive and negative outcomes are first-class coverage targets
- [ ] branch-targeted constraints are built by a dedicated builder
- [ ] dependency scopes are first-class runtime artifacts
- [ ] join and grouped branches are explicit symbolic concepts
- [ ] compatibility wrappers exist only at the API edge

## Risks

### Risk 1: Over-merging campaign and engine
Mitigation:
- keep one-pass execution in `engine.py`
- keep iteration orchestration in `campaign.py`

### Risk 2: Over-merging builder and solver
Mitigation:
- builder emits constraints and hints only
- solver owns realization strategy

### Risk 3: Regressing simple cases while targeting richer coverage
Mitigation:
- preserve current filter/case/join tests
- add outcome-coverage tests before broadening operator families

### Risk 4: Compatibility drag
Mitigation:
- keep wrappers thin and documented
- do not add new logic to compatibility modules

## Recommended Immediate Execution Order
The lowest-risk next implementation order is:

1. create `campaign.py`
2. create `builder.py`
3. move `generator.py` to campaign wrapper role
4. expand coverage/scheduler to outcome-first behavior
5. add campaign and builder tests
6. formalize dependency bindings
7. add join-specific branch semantics
8. add grouped branch semantics

## Sources
- Current symbolic modules under `src/parseval/symbolic/`
- Current solver realization module `src/parseval/solver/instance.py`
- Existing symbolic plans:
  - `2026-05-10-symbolic-instance-driven-refined-design-plan.md`
  - `2026-05-10-symbolic-solver-boundary-plan.md`
