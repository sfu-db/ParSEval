
# Implementation Plan — 3VL + Unique-Cause MC/DC Symbolic Coverage

## Problem Statement

The current symbolic module:
- Classifies branch outcomes as 2-valued (taken: bool), collapsing FALSE and NULL together and losing the NULL-bug signal that matters most for SQL 
equivalence testing.
- Extracts branches as flat leaves (OR disjuncts, CASE arms, predicate atoms) and discards the compound decision structure needed for MC/DC reasoning.
- Alternates positive/negative goals in the scheduler rather than modeling positive/negative coverage as first-class requirements.
- Does not cover HAVING (reserved as future), treats EXISTS as an unconditional TRUE shortcut, and does not expose a structured coverage artifact.
- Has no mechanism to distinguish "uncovered but achievable" from "infeasible" (solver UNSAT).

We need the module to, given a SQL query and an instance:
1. Parse → tree plan (ScopePlan) — already works.
2. Extract every decision site with its full boolean tree and atomic conditions, preserving compound structure.
3. Observe each decision's 3-valued outcome and each atom's 3-valued outcome per execution context using the existing rex.OPS/.concrete 3VL machinery.
4. Dynamically analyze the instance to compute which outcomes and which Unique-Cause MC/DC pairs are covered / uncovered, with diagnostics explaining 
why.
5. Build targeted constraints for each unmet requirement (outcome or MC/DC pair), invoke the solver, mutate the instance, and re-observe.
6. Surface a structured CoverageReport (with JSON export) as a first-class result.

## Requirements (from Q&A)

- **3-valued outcomes + Unique-Cause MC/DC** (Q1=c, Q4=a). Infeasible MC/DC pairs/outcomes are marked infeasible when solver returns UNSAT.
- **In-place refactor, legacy code can be rewritten freely** (Q2=a).
- **Structured CoverageReport object + JSON export** (Q3=b).
- **Mixed decision boundary**: top-level boolean per branch site as the primary decision; additionally a sub-decision per OR disjunct (Q5=c).
- **Full scope**: filter / join-on / CASE arm / HAVING / EXISTS, plus diagnostic report explaining uncovered requirements + row-level requirements (Q6
=d).
- Out of scope for this iteration: correlated subqueries, set operations, window functions.

## Background / Findings

- parseval/plan/rex.py already evaluates SQL 3VL correctly: OPS propagates None through comparisons/LIKE/BETWEEN/arithmetic, and .concrete returns 
None/True/False. The __bool__ shortcut coerces NULL→False, but .concrete is None is a clean NULL signal.
- BranchRecorder._is_taken currently does concrete is True — the only change needed to lift to 3VL at the observation layer is to record the raw 
concrete as an outcome enum.
- negate_predicate(expr) in rex.py returns simplify(expr.not_()). For a "FALSE" outcome constraint we need (NOT P) AND (P IS NOT NULL) in 3VL to 
exclude the NULL branch; for "NULL" we need P IS NULL.
- extract_branch_templates flattens AND/OR — we must replace it with a decision-tree extractor. The existing _iter_condition_predicates and 
_iter_case_predicates helpers are reusable as leaf finders.
- ScopePlan.step_annotations already surfaces condition per step. HAVING surfaces as a Having step in sqlglot's planner (and should be wired into the 
annotator if it isn't already — verify in Task 2). EXISTS is nested inside expressions; we'll traverse projections/conditions to locate EXISTS sites.
- InstanceDrivenSolver.solve_constraints returns a sat flag — we'll interpret UNSAT as "requirement infeasible" and persist that verdict.

## Proposed Solution Architecture

                 ┌────────────────────────┐
                 │  SymbolicCampaign      │
                 └───────────┬────────────┘
                             │
      ┌──────────────────────┼───────────────────────┐
      ▼                      ▼                       ▼
┌────────────┐      ┌────────────────┐       ┌──────────────────┐
│ Decision   │      │ Requirement    │       │ Instance         │
│ Extractor  │─────▶│ Generator      │◀──────│ Analyzer (3VL    │
│ (tree +    │      │ (outcome +     │       │  classify +      │
│  atoms)    │      │  MC/DC pairs)  │       │  diagnostics)    │
└────────────┘      └───────┬────────┘       └──────────────────┘
                            │                           ▲
                            ▼                           │
                    ┌───────────────┐           ┌───────┴────────┐
                    │ Constraint    │           │ Decision       │
                    │ Builder       │──────────▶│ Recorder (3VL  │
                    │ (per req)     │           │  per decision, │
                    └───────┬───────┘           │  per atom)     │
                            ▼                   └────────────────┘
                    ┌───────────────┐                   ▲
                    │ Solver → SAT? │                   │
                    │ mutate inst   │───────────────────┘
                    │ UNSAT→infeas  │
                    └───────┬───────┘
                            ▼
                  ┌──────────────────┐
                  │ CoverageReport   │
                  │ (+ JSON export)  │
                  └──────────────────┘


New/renamed data types:
- BranchOutcome enum: TRUE, FALSE, NULL
- Decision (replaces flat BranchTemplate) — holds a DecisionNode tree and AtomicCondition leaves
- DecisionNode — compound boolean tree (kinds: and, or, not, atom)
- AtomicCondition — boolean leaf expression with stable id
- OutcomeRequirement — "Decision D must be observed with outcome O"
- MCDCPairRequirement — "For atom A in decision D, observe the unique-cause pair flipping A"
- DecisionObservation — runtime observation: (decision_id, context, outcome, atom_outcomes, evidence_rows)
- RequirementStatus — COVERED / UNCOVERED / INFEASIBLE / UNATTEMPTED
- CoverageReport — structured per-decision report with summary, diagnostics, and to_dict()/to_json()

## Task Breakdown

Each task is a working, test-driven, demoable increment. Tasks build on one another with no orphan code.

### Task 1: Introduce the 3VL + decision-tree data model

- **Objective**: Replace the 2-valued BranchTemplate/BranchInstance/BranchGoal with the new 3VL decision model in src/parseval/symbolic/types.py. Add 
BranchOutcome, DecisionNode, AtomicCondition, Decision, DecisionObservation, OutcomeRequirement, MCDCPairRequirement, RequirementStatus.
- **Guidance**: Keep dataclasses frozen. Provide BranchOutcome.classify(concrete) that maps None → NULL, True → TRUE, falsy-non-None → FALSE. Ensure 
decision_id and atom_id are deterministic strings derived from (scope_id, step_id, site, ordinal, arm_index?).
- **Tests**: Unit tests for BranchOutcome.classify (covering None, True, False, 0, 1); round-trip equality for frozen dataclasses; id stability across
reconstruction.
- **Demo**: 
python -c "from parseval.symbolic.types import BranchOutcome; print(BranchOutcome.classify(None), BranchOutcome.classify(True), BranchOutcome.classify(False))"
prints NULL TRUE FALSE.

### Task 2: Decision extractor with compound tree + all branch sites

- **Objective**: Write src/parseval/symbolic/extractor.py::extract_decisions(scope_plan) -> list[Decision] that produces top-level decisions for five 
sites — filter, join_on, case_when_arm, having, exists — plus one sub-decision per OR disjunct (per Q5=c). Each decision carries a full DecisionNode 
tree with atom leaves.
- **Guidance**: For each step annotation with a condition, emit one top-level Decision. For each CASE WHEN arm in each projection, emit a decision. 
For HAVING, recognize sqlglot.planner.Having steps (or the Aggregate step's having annotation — verify before implementing). For EXISTS, traverse each
step's condition and projection expressions and emit a site="exists" decision for each exp.Exists sub-expression, whose decision expression is the 
existence predicate itself (atom: EXISTS(...)). Sub-decisions for OR: for every top-level decision whose tree contains an OR node at depth 1, emit one
additional decision per disjunct (parent linkage preserved via metadata). Reuse _iter_condition_predicates / _iter_case_predicates to identify atoms.
Determine atom nullability by walking referenced columns against the Catalog2 schema.
- **Tests**: Extraction tests in tests/symbolic/test_extractor.py covering: simple filter, AND/OR compound, nested CASE, HAVING clause, EXISTS in 
WHERE, NOT wrapper, mixed sites.
- **Demo**: Given SELECT * FROM t WHERE (a > 10 OR b IS NULL) AND EXISTS (SELECT 1 FROM u WHERE u.id = t.id), print all decisions with their trees; 
verify filter-top, two OR sub-decisions, and one exists decision are emitted.

### Task 3: Requirement generator (outcomes + Unique-Cause MC/DC pairs)

- **Objective**: Add src/parseval/symbolic/requirements.py with 
generate_requirements(decisions) -> tuple[OutcomeRequirement, ...], tuple[MCDCPairRequirement, ...].
- **Guidance**: For each Decision, emit OutcomeRequirement for TRUE, FALSE, and (only if any atom references a nullable column or uses a NULL-
propagating operator) NULL. For each atom in each decision, generate MCDCPairRequirement representing the Unique-Cause pair: the atom toggles, all 
other atoms hold constant, and the decision outcome changes. Compute the required "other atoms" truth-assignment at extraction time by short-circuit 
analysis of the decision tree (e.g., for A AND B, flipping A requires B=TRUE; for A OR B, flipping A requires B=FALSE). If the decision tree admits no
such assignment (e.g., atom is masked), mark the requirement infeasible statically before it ever reaches the solver.
- **Tests**: Unit tests in tests/symbolic/test_requirements.py: A, A AND B, A OR B, NOT A, (A AND B) OR C, and a statically-masked atom that yields 
infeasible without solver input.
- **Demo**: For WHERE a > 10 AND b < 5, print 3 outcome requirements + 2 MC/DC pairs (one per atom), each pair including the required fixed-value of 
the other atom.

### Task 4: 3VL decision recorder

- **Objective**: Rewrite src/parseval/symbolic/recorder.py as DecisionRecorder that records DecisionObservation from the encoder, including per-atom 
3VL outcomes and the evidence rowids driving the observation.
- **Guidance**: The recorder receives, per step, the resolved decision expression with concrete values substituted. Call 
BranchOutcome.classify(decision_expression.concrete) for the decision, and classify each atom subexpression the same way. Produce an 
ExecutionContextKey with row/group context. Wire the recorder into SymbolicScopeEncoder at each decision site (the encoder already has hooks at filter
and case evaluation — extend to join-on, having, exists sites).
- **Tests**: Unit tests that feed a fake expression tree with precomputed .concrete values (including None) and assert the recorder emits the expected
DecisionObservations.
- **Demo**: On a toy instance where a=NULL, b=3, running the encoder on WHERE a > 10 OR b < 5 yields one observation with outcome=TRUE, atom outcomes 
{a>10: NULL, b<5: TRUE}.

### Task 5: Instance analyzer — coverage state + diagnostics

- **Objective**: Add src/parseval/symbolic/analyzer.py::analyze_coverage(decisions, outcome_reqs, mcdc_reqs, observations) -> CoverageState and a 
diagnostics module producing human-readable explanations for each uncovered requirement.
- **Guidance**: CoverageState maps each requirement to RequirementStatus plus optional evidence. For uncovered outcome requirements, the diagnostic 
inspects the instance: "no row in table T has a > 10" (counterexample stats from observations), "column x has no NULL rows", etc. For uncovered MC/DC 
pairs, diagnose whether the partial observation shows one leg but not the other. Use already-observed rows to derive counts; do not re-run the plan. 
Diagnostic text should be stable, compact, and programmatically parseable (tuple of (code, message, details)).
- **Tests**: Analyzer tests fed synthesized observation lists that partially cover a small decision set; assert RequirementStatus and diagnostic 
payloads.
- **Demo**: On a query/instance where the FALSE branch is uncovered because all rows satisfy a > 10, the analyzer returns a diagnostic like 
("uncovered.outcome", "decision D_filter_0 FALSE never observed", {"rows_with_true": 5, "rows_with_false": 0, "rows_with_null": 0}).

### Task 6: Outcome-targeted constraint builder

- **Objective**: Extend src/parseval/symbolic/builder.py with build_for_outcome(requirement: OutcomeRequirement) producing proper 3VL-aware SMT 
constraints.
- **Guidance**: For TRUE: emit P. For FALSE: emit AND(NOT P, P IS NOT NULL) — because NOT NULL = NULL in 3VL and we want a strictly-FALSE evaluation. 
For NULL: emit P IS NULL and mark referenced columns as nullable in the solve session. Include the structural constraints (joins, prior step 
conditions) as before. For CASE arms, also emit the prior arms' negations to select the targeted arm. Normalize temporal literals as the current 
builder does.
- **Tests**: Builder tests asserting the generated constraint SQL matches expected shapes for each outcome on simple predicates (a > 10, a = b, 
a IS NULL, a IN (...), EXISTS (...)).
- **Demo**: Build constraints for each of TRUE/FALSE/NULL on a > 10 and print them; verify the FALSE branch includes the IS NOT NULL conjunct.

### Task 7: MC/DC-targeted constraint builder (two-row pairs)

- **Objective**: Extend the builder with build_for_mcdc_pair(requirement: MCDCPairRequirement) producing constraint sets for the pair of executions in
a Unique-Cause MC/DC witness.
- **Guidance**: For a MCDCPairRequirement, output two ConstraintBuildResult instances (or one grouped container) — one asserting atom=TRUE with the 
frozen assignment of the other atoms, the other asserting atom=FALSE with the same frozen assignment. The campaign solves each leg independently 
against the instance. Short-circuit to infeasible at build time when the decision tree forces the other atoms into contradictory concrete assignments 
(use the static analysis from Task 3).
- **Tests**: Builder tests for MC/DC pairs on A AND B, A OR B, NOT (A AND B), and a case where static analysis detects infeasibility (e.g., 
A AND NOT A).
- **Demo**: Print the two constraint legs for flipping a > 10 inside a > 10 AND b < 5: leg 1 = a > 10 AND b < 5 AND b IS NOT NULL, leg 2 = 
NOT(a > 10) AND (a > 10) IS NOT NULL AND b < 5 AND b IS NOT NULL.

### Task 8: Campaign loop update — drive requirements, handle UNSAT as infeasible

- **Objective**: Update src/parseval/symbolic/campaign.py to work with the new requirement/observation model.
- **Guidance**: Replace _scheduled_goals with a requirement-driven scheduler that prioritizes uncovered outcomes, then uncovered MC/DC pairs. For each
requirement: build constraints → solve → if SAT, mutate instance and re-observe → if UNSAT, mark INFEASIBLE in the coverage state and do not retry 
it. Re-run observation after each successful mutation (only for affected scopes). Break when no requirement changes status. Remove the legacy positive
/negative alternation.
- **Tests**: Campaign tests in tests/symbolic/test_campaign.py on a hand-built query + instance that exercises all outcome types and at least one MC/
DC pair; assert progression from UNCOVERED → COVERED and at least one INFEASIBLE verdict.
- **Demo**: Run the updated SymbolicCampaign on a query whose FALSE branch starts uncovered; show iteration-by-iteration that coverage improves and 
ends with a structured final state.

### Task 9: CoverageReport + JSON export

- **Objective**: Add src/parseval/symbolic/report.py with DecisionCoverageReport, CoverageSummary, CoverageReport, plus to_dict() and to_json() 
methods. Expose it from SymbolicCampaignResult.coverage_report.
- **Guidance**: Each DecisionCoverageReport includes decision_id, expression_sql, site, per-outcome status (covered/uncovered/infeasible), per-MC/DC-
pair status, evidence row-ids per observed outcome, and the diagnostics list from Task 5. CoverageSummary carries totals and coverage percentages. 
to_json() produces stable, versioned JSON ({"schema_version": "1.0", ...}). Include a small pretty-printing helper for human inspection. Do not 
include raw objects that can't be serialized — stringify exp.Expression via .sql().
- **Tests**: Snapshot tests verifying the JSON structure for a small query. Round-trip test: from_dict(to_dict()) returns an equal report (for a 
frozen-dataclass-based re-loader).
- **Demo**: After a campaign run, print(result.coverage_report.to_json(indent=2)) yields a readable JSON report with every decision, outcome, MC/DC 
pair, status, and diagnostic.

### Task 10: End-to-end integration + documentation + cleanup

- **Objective**: Wire all pieces into a single public API, add an end-to-end integration test, update docs, and remove now-unused legacy symbols (
BranchTemplate, BranchInstance, BranchGoal, BranchScheduler in its old form, BranchRecorder's 2-valued recording) that were superseded during Tasks 1–
9.
- **Guidance**: Public entry point: SymbolicCampaign(expr, instance, dialect).run() -> SymbolicCampaignResult, where the result exposes 
coverage_report: CoverageReport. Integration test query should span all five sites (filter, join-on, CASE, HAVING, EXISTS) against a small synthetic 
instance; assert the full report includes every expected decision with non-trivial coverage. Update src/parseval/symbolic/__init__.py __all__ to the 
new public API and prune lazy __getattr__ entries that no longer exist. Update docs/ with a brief "Symbolic Coverage" page outlining the 3VL + MC/DC 
model and JSON schema. Delete src/parseval/plan copy/ if still present; remove solver/smt copy.py if it is now dead (confirm first). Run 
python -m unittest discover -s tests -p 'test_*.py' and ensure everything passes.
- **Tests**: tests/symbolic/test_end_to_end.py asserting a known JSON report for the canonical query (use golden-file comparison with a stable schema 
version).
- **Demo**: A single command — e.g., python -m tests.symbolic.test_end_to_end or a small example script under docs/examples/coverage_demo.py — runs 
the campaign on the canonical query and prints the full JSON coverage report, showing covered / uncovered / infeasible verdicts across every decision 
and every MC/DC pair.


> # Implementation Plan v2 — 3VL + Unique-Cause MC/DC Symbolic Coverage

## Problem Statement

The current symbolic module uses 2-valued branch coverage (taken: bool), collapsing FALSE and NULL and losing the NULL-bug signal that matters most 
for SQL equivalence testing. It extracts branches as flat leaves (OR disjuncts, CASE arms, predicate atoms) and discards the compound decision 
structure needed for MC/DC reasoning. It does not cover HAVING, treats EXISTS as unconditional TRUE, and exposes no structured coverage artifact. It 
cannot distinguish "uncovered but achievable" from "infeasible" (solver UNSAT).

We need the module to:
1. Parse the query into a tree plan (ScopePlan) — already exists.
2. Extract every decision site with its full boolean tree and atomic conditions.
3. Observe each decision's 3-valued outcome and each atom's 3-valued outcome per execution context.
4. Dynamically analyze the instance to compute which outcomes and which Unique-Cause MC/DC pairs are covered / uncovered / infeasible, with 
diagnostics.
5. Build targeted constraints for each unmet requirement, invoke the solver, mutate the instance, re-observe, with solver timeouts and witness-reuse.
6. Emit a structured CoverageReport (with JSON export) as a first-class result.

## Requirements (from Q&A)

- 3-valued outcomes + Unique-Cause MC/DC. Infeasible = static mask OR solver UNSAT OR budget exhausted.
- In-place refactor; legacy code may be rewritten freely. Each task updates/deletes its corresponding legacy tests in the same change.
- Structured CoverageReport object + JSON export (versioned schema).
- Mixed decision boundary: top-level boolean per branch site + one sub-decision per OR disjunct (sub-decisions track per-disjunct outcome coverage, not
MC/DC — MC/DC is anchored at top-level decisions only).
- Full scope: filter / join-on / CASE arm / HAVING / EXISTS + diagnostic report.
- Out of scope: correlated subqueries, set operations, window functions.

## Key Design Decisions (formalized from review)

1. 3VL Unique-Cause MC/DC definition (design-anchor, used everywhere).

An MC/DC witness for atom A with respect to decision D is a pair of execution contexts (c1, c2) such that:
- (i) A evaluates to two distinct 3VL values in c1 vs. c2 (TRUE↔FALSE, TRUE↔NULL, or FALSE↔NULL),
- (ii) every other atom of D is masked in both c1 and c2 (its value does not affect D's outcome), and
- (iii) D's 3VL outcome differs between c1 and c2.

MC/DC coverage for A requires at least one witness pair. When the decision tree + atom nullability admit no such pair (verified by 3VL static analysis
in mcdc.py), the requirement is marked INFEASIBLE statically. Otherwise the solver is asked; UNSAT → INFEASIBLE.

2. FALSE-outcome constraint. We use (NOT P) AND (P IS NOT NULL) because the newer InstanceDrivenSolver (solver/instance.py) uses Z3 Option types where
NOT NULL = NULL. The IS NOT NULL conjunct is therefore necessary, not redundant.

3. Atom 3VL classification. A helper atom_can_be_null(atom, schema) is used wherever NULL-outcome feasibility is decided. IS NULL, IS NOT NULL, EXISTS
, and IS DISTINCT FROM are inherently 2VL and never yield NULL outcomes.

4. HAVING operates per group. HAVING decisions use ExecutionContextKey.group_key as context; each observed group is a distinct execution context.

5. Monotonic-instance invariant. INFEASIBLE verdicts hold only within a single campaign run, where the instance grows monotonically (existing rows are
never removed, only added).

## Background / Findings

- parseval/plan/rex.py already evaluates SQL 3VL correctly: OPS propagates None; .concrete returns None/True/False. .concrete is None is the clean 
NULL signal.
- negate_predicate(expr) returns simplify(expr.not_()). For strict 3VL FALSE we need (NOT P) AND (P IS NOT NULL); for NULL we need P IS NULL.
- _iter_condition_predicates and _iter_case_predicates are reusable atom-leaf finders.
- ScopePlan.step_annotations surfaces condition per step; HAVING surfaces as a Having step in sqlglot's planner.
- Catalog2.nullable(table, column) and is_unique(...) provide schema nullability lookups.
- InstanceDrivenSolver.solve_constraints returns a sat flag — UNSAT → INFEASIBLE. It currently has no timeout configured.

## Proposed Architecture

                    ┌─────────────────────────────┐
                    │   SymbolicCampaign (Task 10)│
                    │   + budget, timeout, reuse, │
                    │   + structured logging      │
                    └───────────────┬─────────────┘
                                    │
     ┌──────────────┬───────────────┼─────────────┬──────────────┐
     ▼              ▼               ▼             ▼              ▼
┌──────────┐  ┌────────────┐  ┌──────────┐ ┌────────────┐ ┌────────────┐
│ extract_ │  │ mcdc.py    │  │ require- │ │ analyzer   │ │ recorder   │
│ decisions│  │ (3VL mask  │  │ ments    │ │ (+diagnost)│ │ (3VL per   │
│ (Task 2) │  │ analysis)  │  │ (Task 4) │ │ (Task 7)   │ │  site)     │
└────┬─────┘  │ (Task 3)   │  └────┬─────┘ └─────┬──────┘ │ (Task 5/6) │
     │        └─────┬──────┘       │             │        └─────┬──────┘
     │              │              │             │              │
     ▼              ▼              ▼             ▼              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                     types.py (Task 1, additive)                      │
│  BranchOutcome, Decision, DecisionNode, AtomicCondition,             │
│  OutcomeRequirement, MCDCPairRequirement, DecisionObservation,       │
│  RequirementStatus, CoverageState                                    │
└──────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
               ┌────────────────────────────────────┐
               │  builder.py (Tasks 8 + 9)          │
               │  build_for_outcome, build_for_mcdc │
               └────────────────────────────────────┘
                                    │
                                    ▼
             ┌─────────────────────────────────────────┐
             │ CoverageReport + JSON export (Task 11)  │
             └─────────────────────────────────────────┘
                                    │
                                    ▼
             ┌─────────────────────────────────────────┐
             │ Task 12: cleanup, migrate tests, docs,  │
             │ delete legacy symbols, end-to-end       │
             └─────────────────────────────────────────┘


## Task Breakdown

Each task:
- is a working, demoable increment;
- updates or deletes corresponding legacy test files in the same change (**test migration is per-task**, not deferred);
- is strictly additive through Task 11 — legacy types BranchTemplate/BranchInstance/BranchGoal remain importable until Task 12 so the build is green 
at every checkpoint.

### Task 1: Additive 3VL + decision-tree data model

- **Objective**: Add new 3VL data types to src/parseval/symbolic/types.py alongside the existing BranchTemplate/BranchInstance/BranchGoal. New types: 
BranchOutcome enum (TRUE/FALSE/NULL), DecisionSite enum (filter/join_on/case_when_arm/having/exists/or_sub_decision), DecisionNode (compound tree: and
/or/not/atom), AtomicCondition, Decision (holds tree + atoms + site + group-keyed flag for HAVING), DecisionObservation (outcome + atom_outcomes + 
evidence_rows + group_key), OutcomeRequirement, MCDCPairRequirement (atom_id + value_transition pair), RequirementStatus (COVERED/UNCOVERED/INFEASIBLE
/UNATTEMPTED/BUDGET_EXHAUSTED), CoverageState (keyed requirement→status+evidence).
- **Guidance**: All frozen dataclasses. BranchOutcome.classify(concrete) maps None→NULL, True→TRUE, falsy-non-None→FALSE. decision_id / atom_id are 
deterministic strings derived from (scope_id, step_id, site, ordinal, arm_index?). Include a short docstring in the module formalizing the 3VL Unique-
Cause MC/DC definition from the Design Decisions section.
- **Tests**: New tests/symbolic/test_types_3vl.py covering BranchOutcome.classify (None/True/False/0/1/""), id stability for reconstructed objects, 
RequirementStatus transitions. Existing test_branch_types.py remains green (legacy types untouched).
- **Demo**: Build a Decision for a > 10 AND b IS NULL from hand-crafted sqlglot expressions and pretty-print its tree, atom ids, and site.

### Task 2: Decision extractor + atom 3VL classifier

- **Objective**: Add src/parseval/symbolic/decision_extractor.py with extract_decisions(scope_plan) -> list[Decision]. Sites: filter, join_on, 
case_when_arm, having, exists. Add one sub-decision per OR disjunct of a top-level filter/join/having decision with site=or_sub_decision. Add 
src/parseval/symbolic/atom_analysis.py with atom_can_be_null(atom, schema) -> bool keyed on operator type + operand column nullability. Leave existing
extractor.extract_branch_templates in place (marked DeprecationWarning at import time).
- **Guidance**: For each step annotation with a condition → top-level decision. For each CASE WHEN arm inside projections → one decision. For HAVING, 
detect sqlglot.planner.Having steps and mark the resulting Decision.group_scoped = True. For EXISTS, walk each step's condition/projection for 
exp.Exists and emit a site=exists decision with a single atom EXISTS(...) (subquery internals out of scope for this iteration). Build DecisionNode 
tree via a recursive walker on exp.And / exp.Or / exp.Not; leaves are boolean-valued comparison / LIKE / BETWEEN / IS NULL / IN / EXISTS expressions. 
Sub-decisions per OR disjunct exist only to provide per-disjunct outcome coverage (is disjunct A ever TRUE alone? ever FALSE? ever NULL?); they 
intentionally do not generate MC/DC pairs (which are anchored at the top-level decision).
- **Tests**: New tests/symbolic/test_decision_extractor.py covering: simple filter, AND/OR nesting, CASE arms, HAVING group-scoped flag, EXISTS in 
WHERE, NOT wrapper, a query with all five sites. Plus tests/symbolic/test_atom_analysis.py for atom_can_be_null across comparisons, IS-NULL, EXISTS, 
LIKE, IN-with-nullable-operand. Legacy test_branch_extraction.py remains green (old extractor untouched).
- **Demo**: On SELECT a FROM t JOIN u ON t.id=u.id WHERE (a>10 OR b IS NULL) GROUP BY a HAVING count(*)>5 AND EXISTS (SELECT 1 FROM v), print all 
extracted decisions + sites + tree structure + can_be_null for each atom.

### Task 3: 3VL-aware MC/DC static analysis module

- **Objective**: Add src/parseval/symbolic/mcdc.py encoding the 3VL truth tables for AND/OR/NOT and providing:
  - masking_assignments(decision_node, target_atom_id) -> list[MaskingAssignment] | None — returns all 3VL value assignments of the non-target atoms 
under which the target atom can independently affect the decision's outcome. Returns None (static infeasibility) when no such assignment exists.
  - reachable_outcomes(decision_node, schema) -> frozenset[BranchOutcome] — the set of outcomes the decision tree can yield at all, given atom 
nullability (used to suppress NULL-outcome requirements where impossible).
- **Guidance**: Treat AND/OR under 3VL using the full 9-entry truth table (not 2VL short-circuit). For A OR B flipping A: the masking assignments for 
B are {B=FALSE} (classical) and {B=NULL} (because A=TRUE→TRUE, A=FALSE→NULL differ). Use the per-atom 3VL classifier from Task 2 to skip impossible 
atom values. Provide a pure-Python implementation with no sqlglot dependencies in the core function (except for reading atom metadata).
- **Tests**: New tests/symbolic/test_mcdc.py with parameterized cases: bare atom, A AND B, A OR B, NOT A, (A AND B) OR C, A AND NOT A (infeasible 
masking), an atom with can_be_null=False restricting its possible values. Golden truth-table test asserting the 3VL AND/OR tables match SQL semantics.
- **Demo**: Print masking assignments for atom A in A AND B (expect {B=TRUE}), atom A in A OR B (expect {B=FALSE, B=NULL}), and masking_assignments 
for masked atom (returns None).

### Task 4: Requirement generator

- **Objective**: Add src/parseval/symbolic/requirements.py with generate_requirements(decisions, schema) -> (outcome_reqs, mcdc_reqs). Uses mcdc.py + 
atom_can_be_null.
- **Guidance**: For each Decision, emit OutcomeRequirement for each o ∈ reachable_outcomes(D.tree, schema) (skips NULL if unreachable). For each atom 
in top-level decisions (sites filter/join_on/case_when_arm/having), for each feasible 3VL value-transition pair (v1, v2), compute 
masking_assignments(tree, atom). If empty, emit a pre-marked INFEASIBLE MCDCPairRequirement (static infeasibility). Otherwise emit a live 
MCDCPairRequirement. Sub-decisions (or_sub_decision) get outcome requirements only, no MC/DC pairs. Requirements are stable (deterministic ids).
- **Tests**: New tests/symbolic/test_requirements.py: count of reqs for small queries; static-infeasibility for A AND NOT A; NULL outcome omitted when
can_be_null=False for every atom; sub-decisions yield only outcome reqs.
- **Demo**: Print all requirements for WHERE a > 10 AND b IS NULL: 3 outcome reqs for top-level, 2 atom MC/DC pair reqs, plus NULL outcome omitted 
when b IS NULL is the only NULL-producing atom (because a > 10 can produce NULL only if a is nullable).

### Task 5: 3VL decision recorder + single-site encoder wiring + end-to-end smoke

- **Objective**: Replace recorder.py with a DecisionRecorder that emits DecisionObservation per evaluated decision. Wire into SymbolicScopeEncoder at 
the filter site only (keep existing case-recording code in place, unused). Add a thin end-to-end smoke path 
run_outcome_only_campaign(expr, instance, dialect) -> CoverageState that uses outcome requirements only (no MC/DC, no diagnostics yet) — proves the 
full pipeline end-to-end before Task 6 expands the encoder surface.
- **Guidance**: DecisionRecorder.record(decision, resolved_expression, context) uses BranchOutcome.classify(resolved_expression.concrete) for the 
decision and per-atom outcomes. Evidence rows come from the execution context. The smoke path reuses Task 4's requirements + existing 
ConstraintBuilder.build (legacy-style bootstrap + filter predicate) + InstanceDrivenSolver — do not refactor the builder yet.
- **Tests**: New tests/symbolic/test_decision_recorder.py with synthesized expressions + precomputed .concrete values (including None). New 
tests/symbolic/test_smoke_outcome.py: small toy query + instance where FALSE branch starts uncovered; assert the smoke campaign flips it to COVERED. 
Delete or rewrite tests/symbolic/test_branch_recording.py (legacy 2VL recording is no longer the primary API).
- **Demo**: On a toy instance where a=NULL, b=3, encoding WHERE a > 10 OR b < 5 yields one observation with outcome=TRUE and atom outcomes 
{a>10: NULL, b<5: TRUE}. Run run_outcome_only_campaign on a query that initially covers only TRUE; show it converges to {TRUE, FALSE}.

### Task 6: Encoder extension to all decision sites + HAVING group context

- **Objective**: Extend SymbolicScopeEncoder to emit DecisionRecorder events at join-on, case_when_arm, having, and exists sites in addition to filter. 
HAVING observations use ExecutionContextKey.group_key.
- **Guidance**: For each site, identify the encoder's existing evaluation point and insert a recorder call. For having: the encoder iterates over 
groups; for each group, evaluate the HAVING condition and record one observation per group with context_type="group" and the group key. For exists: 
where the encoder currently substitutes exp.Boolean(this=True), compute the 3VL outcome by checking the bound subquery result (from the dependency 
binding when available; otherwise mark observation unknown and emit a structured log warning). For case_when_arm: one observation per row per arm, 
with prior-arms-negated path constraint implicit in the recorded context. Do not decompose encoder.py in this task — leave as monolith; mark a follow-
up.
- **Tests**: Extend tests/symbolic/test_decision_recorder.py with fixtures per site. New tests/symbolic/test_having_groups.py asserting per-group 
observations carry distinct group_key.
- **Demo**: On a query touching all five sites, print the observation stream annotated by site; show HAVING produces N observations for N groups.

### Task 7: Instance analyzer + diagnostics

- **Objective**: Add src/parseval/symbolic/analyzer.py::analyze_coverage(decisions, outcome_reqs, mcdc_reqs, observations) -> CoverageState. Each 
requirement is classified COVERED / UNCOVERED / INFEASIBLE (from static analysis only at this point). Produce a diagnostic list 
tuple[(code, message, details), ...] per uncovered requirement.
- **Guidance**: For each outcome requirement, scan observations for a matching (decision_id, outcome). For each MC/DC pair requirement, scan for two 
observations matching the masking assignment and outcome pair. Diagnostics use observation statistics: counts of TRUE/FALSE/NULL rows; columns with 
zero NULL rows; masked-atom evidence. Keep details structured (dict), message short. No re-execution — observations only.
- **Tests**: New tests/symbolic/test_analyzer.py with synthesized observation lists that partially cover small decision sets; assert RequirementStatus
and diagnostic payloads. Delete legacy tests/symbolic/test_coverage.py or migrate its relevant assertions into test_analyzer.py.
- **Demo**: On a query/instance where the FALSE branch is uncovered (all rows satisfy a > 10), print diagnostic 
("uncovered.outcome.false", "decision filter_0 FALSE never observed", {"rows_with_true": 5, "rows_with_false": 0, "rows_with_null": 0}).

### Task 8: Outcome-targeted constraint builder

- **Objective**: Extend src/parseval/symbolic/builder.py with build_for_outcome(req: OutcomeRequirement) -> ConstraintBuildResult.
- **Guidance**: TRUE → P. FALSE → (NOT P) AND (P IS NOT NULL) (justified by Z3 Option-type solver). NULL → P IS NULL. For CASE arms, add negations of 
prior arms' predicates to select the targeted arm. For HAVING, wrap the decision tree in the group-by structural constraints so the solver must create
rows forming a group satisfying the HAVING outcome. Keep the existing build_scope_bootstrap for scope-level seeding; the new method coexists. 
Normalize temporal literals as before.
- **Tests**: New tests/symbolic/test_builder_outcome.py asserting generated constraint shapes for each of TRUE/FALSE/NULL on a > 10, a = b, a IS NULL,
a IN (...), EXISTS (...), plus a CASE-arm target. Update existing test_builder.py if it tests only legacy paths.
- **Demo**: Print constraints for each of TRUE/FALSE/NULL on a > 10; verify FALSE branch includes the IS NOT NULL conjunct.

### Task 9: MC/DC-targeted constraint builder

- **Objective**: Extend builder with build_for_mcdc_pair(req: MCDCPairRequirement) -> (ConstraintBuildResult, ConstraintBuildResult) producing the two
legs of the Unique-Cause witness. Use mcdc.py to select a concrete masking assignment (prefer the simplest). Short-circuit to INFEASIBLE when Task 3'
s static analysis returned none (should never reach here post-Task-4, but defensive).
- **Guidance**: Leg 1: assert atom = v1 plus the masking assignment for the other atoms plus the structural + prior-arm constraints. Leg 2: assert 
atom = v2 plus the same masking assignment. Value assertions use the outcome-constraint translations from Task 8 (e.g., atom = NULL → atom IS NULL). 
The campaign solves each leg independently and considers the pair COVERED only when both rows are realized and observed.
- **Tests**: New tests/symbolic/test_builder_mcdc.py for A AND B, A OR B, NOT (A AND B), and a statically-masked case (should return legs marked 
infeasible). Assert the generated SQL matches expected leg shapes.
- **Demo**: For atom a > 10 in decision a > 10 AND b < 5, print both legs: leg 1 asserts a > 10 = TRUE, b < 5 = TRUE; leg 2 asserts 
a > 10 = FALSE (with IS NOT NULL), b < 5 = TRUE.

### Task 10: Campaign loop — budget, timeout, witness reuse, structured logging

- **Objective**: Rewrite SymbolicCampaign.run() to drive OutcomeRequirement + MCDCPairRequirement via a requirement-priority scheduler. Introduce:
  - **Solver timeout**: set z3.set_param("timeout", N_ms) before each solve; timeout → INFEASIBLE with reason solver_timeout.
  - **Iteration and time budget**: SymbolicCampaign(..., max_iterations=..., max_wall_seconds=...). Exhaustion → remaining UNATTEMPTED reqs become 
BUDGET_EXHAUSTED.
  - **Witness reuse**: after every successful mutation + re-observation, re-classify all pending requirements against the new observation set before 
picking the next requirement. Eliminates redundant solves.
  - **Structured logging**: a CampaignLogger (JSON-line sink; writes to a list by default, optionally to a file) emits one record per iteration: 
{iteration, scope_id, requirement_id, kind, status_before, status_after, solver_status, solver_time_ms, reason}.
  - **Monotonic-instance invariant**: document and assert (in debug) that INFEASIBLE verdicts are not revisited within a run.
- **Guidance**: Scheduler priority: (i) outcome requirements before MC/DC pairs, (ii) top-level decisions before sub-decisions, (iii) UNCOVERED before
BUDGET_EXHAUSTED retry (no retry in this iteration), (iv) stable secondary ordering by requirement id. Break when no requirement changes status in a 
full pass.
- **Tests**: New tests/symbolic/test_campaign_v2.py: a query where (a) some outcomes cover from initial observation; (b) some solve successfully and 
become COVERED; (c) at least one requirement becomes INFEASIBLE via UNSAT; (d) budget-exhaustion stops the loop cleanly. Update or delete legacy 
tests/symbolic/test_campaign.py and test_scheduler.py.
- **Demo**: Run the updated campaign on a 3VL-heavy query; print the per-iteration JSON log and final CoverageState showing progressive coverage and 
at least one INFEASIBLE verdict.

### Task 11: CoverageReport + JSON export

- **Objective**: Add src/parseval/symbolic/report.py with DecisionCoverageReport, CoverageSummary, CoverageReport, plus to_dict(), 
to_json(indent=None), and CoverageReport.from_dict() (round-trip). Expose as SymbolicCampaignResult.coverage_report.
- **Guidance**: Each DecisionCoverageReport carries decision_id, expression_sql, site, per-outcome status, per-MC/DC-pair status, evidence row ids per
observed outcome, and the analyzer's diagnostics. CoverageSummary carries totals and coverage percentages (covered / uncovered / infeasible / budget_
exhausted). JSON shape: {"schema_version": "1.0", "summary": {...}, "decisions": [...]}. Stringify sqlglot expressions via .sql(). Add a short 
"Versioning" section to the module docstring: schema_version bumps for breaking JSON changes only.
- **Tests**: New tests/symbolic/test_report.py: snapshot assertions for a small query; from_dict(to_dict()) round-trip equality; stability of 
schema_version.
- **Demo**: After a campaign run, print result.coverage_report.to_json(indent=2) showing every decision, outcome, MC/DC pair, status, and diagnostic.

### Task 12: Cleanup, public API, end-to-end integration, docs

- **Objective**: Remove legacy types and modules that are now unused; finalize the public API; add an end-to-end integration test; update docs.
- **Guidance**: Delete BranchTemplate, BranchInstance, BranchGoal from types.py; delete extract_branch_templates from extractor.py (or delete the 
module); delete BranchRecorder (now replaced by DecisionRecorder); prune BranchScheduler legacy interface. Update src/parseval/symbolic/__init__.py 
__all__ to expose the new public API: SymbolicCampaign, SymbolicCampaignResult, CoverageReport, BranchOutcome, RequirementStatus, Decision, 
extract_decisions, generate_requirements. Remove src/parseval/plan copy/ and src/parseval/solver/smt copy.py (confirm unused first with grep). Add 
docs/symbolic-coverage.md covering: the 3VL + MC/DC model, decision sites, JSON schema, a worked example. Add one end-to-end integration test 
tests/symbolic/test_end_to_end_v2.py on a canonical query spanning all five sites, using golden-file comparison with schema_version=1.0. Run full 
suite: python -m unittest discover -s tests -p 'test_*.py' — must be green.
- **Tests**: End-to-end golden test; full suite pass.
- **Demo**: python tests/symbolic/test_end_to_end_v2.py (or a small docs/examples/coverage_demo.py) runs the campaign on the canonical query and 
prints the complete JSON coverage report.
