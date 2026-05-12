> # Implementation Plan v2.1 — Bite-Sized Task Breakdown

Each top-level task from the approved plan is broken into bite-sized subtasks (target: 1–3 hours each, each independently verifiable, strict additive/
compat discipline through subtask 12.1).

Legend: files touched | verification | time

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 1: Additive 3VL + decision-tree data model

1.1 Add BranchOutcome + DecisionSite + RequirementStatus enums
- src/parseval/symbolic/types.py | append new enums, keep all legacy types | new tests/symbolic/test_types_3vl.py::test_outcome_enum + test_classify 
covering None/True/False/0/1/"" | ~1h

1.2 Add DecisionNode + AtomicCondition dataclasses
- types.py | frozen dataclass with kind: Literal["and","or","not","atom"], atom_id: str | None, children: tuple[...] | 
test_types_3vl::test_decision_node_tree_construction — build A AND (B OR C) tree by hand, assert structure | ~1h

1.3 Add Decision dataclass + deterministic id helpers
- types.py | Decision(decision_id, scope_id, step_id, site, expression_sql, tree, atoms, source_tables, group_scoped, metadata) + 
_make_decision_id(...) + _make_atom_id(...) | test_types_3vl::test_id_stability — same inputs twice → same ids | ~1h

1.4 Add requirement types + DecisionObservation + CoverageState
- types.py | OutcomeRequirement, MCDCPairRequirement (carries atom_id, value_transition: tuple[BranchOutcome, BranchOutcome]), DecisionObservation, 
CoverageState (dict requirement_id → status + evidence + diagnostics) | test_types_3vl::test_coverage_state_api — set_status, get_status, counts | ~1h

1.5 Formalize MC/DC semantics in docstring + export
- types.py module docstring with the 3VL Unique-Cause MC/DC definition from Design Decisions; update src/parseval/symbolic/__init__.py __all__ | grep 
for new names via test_types_3vl::test_public_exports | ~0.5h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 2: Decision extractor + atom 3VL classifier

2.1 atom_analysis.atom_can_be_null(atom, schema)
- new src/parseval/symbolic/atom_analysis.py | keyed on type(atom) + operand column nullability; returns False for Is_Null/Is_Not_Null/exp.Exists/
IS DISTINCT FROM | new tests/symbolic/test_atom_analysis.py covering each operator kind + a nullable-column case | ~1.5h

2.2 Boolean-tree walker → DecisionNode
- new src/parseval/symbolic/decision_extractor.py::_build_decision_tree(expr) | recursive on exp.And/exp.Or/exp.Not/exp.Paren; leaves become atom 
nodes | new tests/symbolic/test_decision_extractor.py::test_tree_shape — parametrized on simple AND/OR/NOT/nested/paren | ~1.5h

2.3 Extract top-level filter + join_on decisions
- decision_extractor.py::extract_decisions(scope_plan) | iterate scope_plan.ordered_steps; for each step with a condition, build tree + atoms + 
decision | test_decision_extractor::test_filter_and_join_on on a SELECT ... FROM a JOIN b ON ... WHERE ... | ~1.5h

2.4 Extract CASE-WHEN-arm decisions
- extend extract_decisions to walk projections via existing _iter_case_predicates and emit one decision per arm | 
test_decision_extractor::test_case_when_arms — nested CASE, assert arm indices in metadata | ~1h

2.5 Extract HAVING decisions (with group_scoped=True)
- detect sqlglot.planner.Having step or Aggregate.having annotation; emit decision with site=having, group_scoped=True | 
test_decision_extractor::test_having_group_scoped | ~1.5h

2.6 Extract EXISTS decisions
- scan each step's condition + projections for exp.Exists; emit site=exists decision with single atom EXISTS(...). (Subquery internals out of scope.) 
| test_decision_extractor::test_exists_in_where + test_exists_in_projection | ~1h

2.7 Sub-decisions per OR disjunct
- for each top-level filter/join/having decision whose tree has a root-level OR, emit one extra site=or_sub_decision decision per disjunct (parent 
linkage in metadata["parent_decision_id"]) | test_decision_extractor::test_or_sub_decisions_emit_once — nested OR not double-counted | ~1h

2.8 Deprecation warning + green legacy tests
- add DeprecationWarning to extractor.extract_branch_templates | confirm tests/symbolic/test_branch_extraction.py still green (old extractor untouched
) | ~0.5h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 3: 3VL-aware MC/DC static analysis module

3.1 3VL AND/OR/NOT truth tables as lookup dicts
- new src/parseval/symbolic/mcdc.py::AND_TABLE, OR_TABLE, NOT_TABLE keyed on BranchOutcome tuples | new tests/symbolic/test_mcdc.py::test_truth_tables
— golden assertions per SQL 3VL | ~1h

3.2 evaluate_decision(tree, atom_values)
- mcdc.py | given a concrete mapping atom_id → BranchOutcome, walk the DecisionNode tree and return the decision's BranchOutcome using the truth 
tables | test_mcdc::test_evaluate_decision — parametrized across small trees | ~1h

3.3 reachable_outcomes(tree, atom_feasibility)
- enumerate all legal atom assignments (given per-atom feasible value sets from atom_can_be_null) and collect the resulting outcomes | 
test_mcdc::test_reachable_outcomes — simple atom with nullable column → {TRUE, FALSE, NULL}; non-nullable → {TRUE, FALSE}; A OR NOT A with non-
nullable → {TRUE} | ~1.5h

3.4 masking_assignments(tree, target_atom_id, atom_feasibility)
- enumerate assignments of non-target atoms where flipping the target atom through its feasible-value pairs changes the decision outcome; return list 
of MaskingAssignment or None if empty | test_mcdc::test_masking_and_or_not — asserts A AND B returns {B=TRUE} for target A; A OR B returns 
{B=FALSE, B=NULL} | ~2h

3.5 Statically-masked infeasibility case
- test_mcdc::test_static_infeasible — for A AND NOT A, masking_assignments returns None | ~0.5h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 4: Requirement generator

4.1 Outcome requirement generation
- new src/parseval/symbolic/requirements.py::generate_outcome_requirements(decisions, schema) | for each decision, emit one OutcomeRequirement per 
o ∈ reachable_outcomes(...) | new tests/symbolic/test_requirements.py::test_outcome_reqs_count — NULL requirement omitted for non-nullable atoms | ~1h

4.2 MC/DC pair requirement generation (top-level only)
- requirements.py::generate_mcdc_requirements(decisions, schema) | skip or_sub_decision; for each top-level atom, for each feasible value-transition 
pair (v1, v2), call masking_assignments; emit MCDCPairRequirement (live or pre-marked infeasible) | test_requirements::test_mcdc_req_count + 
test_mcdc_static_infeasible | ~1.5h

4.3 Public entrypoint + determinism
- generate_requirements(decisions, schema) -> (tuple[OutcomeRequirement,...], tuple[MCDCPairRequirement,...]); assert stable ordering + deterministic 
ids | test_requirements::test_stable_output (run twice, equal tuples) | ~0.5h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 5: 3VL decision recorder + single-site encoder wiring + end-to-end smoke

5.1 DecisionRecorder class
- rename/rewrite src/parseval/symbolic/recorder.py to DecisionRecorder(decisions) with record(decision, resolved_expr, context) -> DecisionObservation
using BranchOutcome.classify(expr.concrete) | new tests/symbolic/test_decision_recorder.py::test_record_outcome_and_atoms with synthesized 
expressions | ~1.5h

5.2 Per-atom outcome classification
- extend recorder to also classify each atom sub-expression's .concrete; attach as atom_outcomes: tuple[(atom_id, outcome), ...] in the observation | 
test_decision_recorder::test_atom_outcomes_null on an expression with a NULL-evaluating atom | ~1h

5.3 Wire recorder into encoder — filter site only
- src/parseval/symbolic/encoder.py | locate filter evaluation hook; replace (or add alongside) current BranchRecorder call with a 
DecisionRecorder.record(...) call using the new Decision lookup | test_decision_recorder::test_encoder_filter_observation — encode a toy query, assert
one observation | ~2h

5.4 Smoke campaign (outcome requirements only)
- new src/parseval/symbolic/smoke.py::run_outcome_only_campaign(expr, instance, dialect) — minimal loop: extract → generate_outcome_requirements → 
engine.execute_scope → analyze → solve each uncovered outcome using existing legacy ConstraintBuilder.build + InstanceDrivenSolver.realize | new 
tests/symbolic/test_smoke_outcome.py — toy query whose FALSE branch starts uncovered, asserts convergence | ~2h

5.5 Migrate/delete test_branch_recording.py
- rewrite it to target the new recorder or delete it if fully superseded | full test suite green | ~1h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 6: Encoder extension to all decision sites + HAVING group context

6.1 Recorder hook at join_on site
- encoder.py | at join-evaluation point, record DecisionObservation for the join-on decision | extend 
test_decision_recorder::test_encoder_join_observation | ~1.5h

6.2 Recorder hook at case_when_arm site
- encoder.py | one observation per row per arm with prior-arm path implied by metadata | test_decision_recorder::test_encoder_case_observations | ~
1.5h

6.3 Recorder hook at having site — per-group observations
- encoder.py | iterate groups; each group produces one observation with ExecutionContextKey(context_type="group", group_key=...) | new 
tests/symbolic/test_having_groups.py::test_distinct_group_keys | ~2h

6.4 Recorder hook at exists site
- encoder.py | replace/augment the current exp.Boolean(this=True) shortcut; compute 3VL outcome from dependency binding if available, else log warning
+ record UNKNOWN | test_decision_recorder::test_encoder_exists_observation | ~1.5h

6.5 End-to-end smoke on all five sites
- extend test_smoke_outcome.py with a canonical multi-site query | asserts each site produces ≥1 observation | ~1h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 7: Instance analyzer + diagnostics

7.1 Outcome-requirement classification
- new src/parseval/symbolic/analyzer.py::_classify_outcome_req(req, observations) returns RequirementStatus + evidence row ids | new 
tests/symbolic/test_analyzer.py::test_outcome_covered_uncovered | ~1h

7.2 MC/DC-pair classification
- analyzer.py::_classify_mcdc_req(req, observations) — find two observations matching the value transition + masking assignment → COVERED; else 
UNCOVERED; carry over pre-marked INFEASIBLE | test_analyzer::test_mcdc_covered_needs_two_observations | ~1.5h

7.3 Diagnostics builder
- analyzer.py::_diagnostic_for(req, observations) returning (code, message, details) with counts (TRUE/FALSE/NULL rows, columns with zero NULL rows, 
masked-atom notes) | test_analyzer::test_diagnostic_text_stable with golden assertions | ~1.5h

7.4 Top-level analyze_coverage orchestrator
- ties 7.1–7.3 together; returns CoverageState | test_analyzer::test_full_classification on a mixed small scenario | ~1h

7.5 Migrate/delete test_coverage.py
- move applicable assertions to test_analyzer.py; delete legacy file if fully superseded | suite green | ~1h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 8: Outcome-targeted constraint builder

8.1 build_for_outcome skeleton + TRUE case
- extend src/parseval/symbolic/builder.py with build_for_outcome(req); TRUE → P + structural constraints | new 
tests/symbolic/test_builder_outcome.py::test_true_outcome | ~1h

8.2 FALSE case with (NOT P) AND (P IS NOT NULL)
- handle FALSE branch with explicit 3VL exclusion | test_builder_outcome::test_false_includes_not_null — assert shape of emitted expression | ~1h

8.3 NULL case
- NULL → P IS NULL + mark referenced columns nullable in the solve session | test_builder_outcome::test_null_outcome | ~1h

8.4 CASE-arm path constraints
- when targeting a CASE arm, add negations of prior arms' predicates | test_builder_outcome::test_case_arm_path_constraints | ~1h

8.5 HAVING structural wrapping
- when targeting a group_scoped decision, include GROUP BY columns in the referenced set and ensure the solver can create rows forming a qualifying 
group | test_builder_outcome::test_having_outcome_structural | ~1.5h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 9: MC/DC-targeted constraint builder

9.1 build_for_mcdc_pair skeleton
- extend builder with build_for_mcdc_pair(req) -> (ConstraintBuildResult, ConstraintBuildResult) | new 
tests/symbolic/test_builder_mcdc.py::test_two_legs_emitted | ~1h

9.2 Leg construction using outcome helpers
- each leg reuses build_for_outcome-style atom-value assertions (atom = v → atom IS NULL / atom / (NOT atom) AND atom IS NOT NULL) plus structural + 
masking assertions | test_builder_mcdc::test_and_leg_shapes / test_or_leg_shapes | ~1.5h

9.3 Masking-assignment selection policy
- when multiple masking assignments exist (from Task 3.4), pick the simplest (fewest NULL atoms, fewest unique columns); stable tiebreak | 
test_builder_mcdc::test_prefers_simpler_masking | ~1h

9.4 Defensive static-infeasibility check
- if MCDCPairRequirement.pre_status == INFEASIBLE passed in, return legs marked infeasible (no solver dispatch later) | 
test_builder_mcdc::test_static_infeasible_passthrough | ~0.5h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 10: Campaign loop with budget, timeout, witness reuse, structured logging

10.1 CampaignLogger JSON-line sink
- new src/parseval/symbolic/logger.py::CampaignLogger with .log(record: dict) — in-memory list + optional file sink | new 
tests/symbolic/test_logger.py::test_record_shape | ~1h

10.2 Solver timeout wiring
- thread solver_timeout_ms kwarg through SymbolicCampaign → InstanceDrivenSolver; set Z3 set_param("timeout", ...) in solver/smt.py; timeout → solver 
returns SAT=False with reason "timeout" | new tests/symbolic/test_campaign_v2.py::test_solver_timeout_marks_infeasible with a small time limit | ~1.5h

10.3 Requirement-priority scheduler
- new src/parseval/symbolic/scheduler_v2.py::RequirementScheduler implementing the priority rules (outcome > MC/DC; top-level > or_sub_decision; 
UNCOVERED first; stable id ordering) | new tests/symbolic/test_scheduler_v2.py::test_priority_order | ~1.5h

10.4 Campaign main loop — requirement-driven
- rewrite SymbolicCampaign.run() to use extract_decisions → generate_requirements → initial observation pass → scheduler loop: pick highest-priority 
pending req → build → solve → mutate → re-observe | test_campaign_v2::test_loop_covers_outcome_reqs | ~2h

10.5 Witness reuse after each mutation
- after mutate + re-observe, call analyze_coverage over all pending reqs and mark newly-covered ones without further solving | 
test_campaign_v2::test_one_mutation_covers_two_reqs | ~1.5h

10.6 Budget exhaustion + BUDGET_EXHAUSTED status
- track wall time + iteration count; when exhausted, mark remaining UNCOVERED/UNATTEMPTED as BUDGET_EXHAUSTED and exit | 
test_campaign_v2::test_budget_exhausts_gracefully | ~1h

10.7 Structured log emission at key transitions
- emit CampaignLogger record per iteration: 
{iteration, scope_id, requirement_id, kind, status_before, status_after, solver_status, solver_time_ms, reason} | 
test_campaign_v2::test_log_records_present | ~1h

10.8 Migrate/delete legacy test_campaign.py + test_scheduler.py
- migrate any reusable assertions; delete legacy files | full suite green | ~1h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 11: CoverageReport + JSON export

11.1 DecisionCoverageReport + CoverageSummary dataclasses
- new src/parseval/symbolic/report.py with frozen dataclasses | new tests/symbolic/test_report.py::test_dataclass_construction | ~1h

11.2 Build report from CoverageState + decisions
- report.py::build_coverage_report(decisions, outcome_reqs, mcdc_reqs, state) -> CoverageReport | test_report::test_build_from_state on a small 
scenario | ~1.5h

11.3 to_dict / to_json with schema_version="1.0"
- JSON: {"schema_version": "1.0", "summary": {...}, "decisions": [...]}; stringify exp.Expression via .sql() | test_report::test_json_shape_stable (
golden-file) + test_schema_version_present | ~1.5h

11.4 CoverageReport.from_dict round-trip
- reverse of to_dict, returns a reconstructed CoverageReport | test_report::test_round_trip_equality | ~1h

11.5 Expose from SymbolicCampaignResult.coverage_report
- update campaign.py + result dataclass | test_campaign_v2::test_result_exposes_coverage_report | ~0.5h

11.6 Versioning docstring
- add a "Versioning" section to report.py module docstring: schema_version bumps only for breaking JSON changes | no test | ~0.25h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Task 12: Cleanup, public API, end-to-end, docs

12.1 Remove legacy symbols from types.py
- delete BranchTemplate, BranchInstance, BranchGoal | grep shows no remaining imports; suite green | ~1h

12.2 Remove legacy modules
- delete src/parseval/symbolic/extractor.py (old extractor) if fully superseded; delete legacy BranchRecorder symbol; prune old BranchScheduler | 
suite green | ~1h

12.3 Finalize public API in __init__.py
- update __all__ to: SymbolicCampaign, SymbolicCampaignResult, CoverageReport, BranchOutcome, RequirementStatus, Decision, extract_decisions, 
generate_requirements | tests/symbolic/test_public_api.py::test_exports | ~0.5h

12.4 Remove dead copy directories (confirm first)
- grep src/parseval/plan copy/ and src/parseval/solver/smt copy.py for references; if none, delete | suite green | ~0.5h

12.5 Write docs/symbolic-coverage.md
- cover: 3VL + MC/DC model, decision sites, JSON schema, worked example | mkdocs build clean | ~2h

12.6 End-to-end golden test
- new tests/symbolic/test_end_to_end_v2.py on a canonical query touching all five sites; golden-file comparison against 
tests/symbolic/fixtures/canonical_report.json (schema_version=1.0) | test passes | ~2h

12.7 Full suite run + CI-quality check
- python -m unittest discover -s tests -p 'test_*.py' — all green | ~0.5h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


## Summary

- **12 tasks → 60 bite-sized subtasks**, each 0.25–2h.
- Each subtask touches a named file + named test, verifiable on its own.
- Strict additive discipline: the build and all non-superseded tests stay green through subtask 12.1, at which point legacy deletion begins.
- Test migration is per-task, not deferred to a final cleanup.