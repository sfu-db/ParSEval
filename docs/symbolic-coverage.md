# Symbolic Coverage — 3VL + Unique-Cause MC/DC

ParSEval's symbolic module generates structured coverage information for a
SQL query evaluated against a concrete database instance. Coverage is
measured in **SQL three-valued logic** (TRUE / FALSE / NULL) and uses
**Unique-Cause Modified Condition / Decision Coverage (MC/DC)** to track
whether each atomic condition of each decision *independently* affects
outcomes.

## Conceptual model

- A **decision** is any boolean expression at a branch site:
  `WHERE` (filter), `JOIN ... ON`, `CASE WHEN` arm, `HAVING`, or
  `EXISTS`. In addition, each disjunct of a top-level OR in a decision
  becomes its own `or_sub_decision` (outcome-only).
- A decision has a compound boolean **tree** with AND / OR / NOT internal
  nodes and atomic **leaves** (comparisons, LIKE, BETWEEN, IS NULL,
  EXISTS, etc.). Atomic leaves are called **atoms**.
- Each atom evaluation, and each decision evaluation, yields a 3VL
  **outcome**: `TRUE`, `FALSE`, or `NULL`.

## Coverage targets

Two kinds of requirements are generated per decision:

- **Outcome requirements** — one per reachable outcome of the decision.
  `NULL` is only required when the tree can actually produce `NULL`
  (at least one atom can be `NULL`).
- **MC/DC pair requirements** — for every atom of a top-level decision,
  for every pair of 3VL values the atom can take, require an MC/DC
  witness pair of observations under a single **masking assignment** of
  the non-target atoms. When no such witness exists statically (the atom
  is masked for every non-target assignment), the requirement is
  pre-marked `INFEASIBLE`.

## Pipeline

```
sqlglot AST ─► ScopePlan ─► extract_decisions ─► generate_requirements
                                                       │
                                             ┌─────────┴─────────┐
                                             ▼                   ▼
                                      OutcomeRequirements  MCDCPairRequirements
                                             │                   │
                                             ▼                   ▼
                                     ┌───────────────────────────────┐
                                     │ encoder + DecisionRecorder     │
                                     │ (3VL observations per site)   │
                                     └────────────────┬──────────────┘
                                                      ▼
                                             analyzer + diagnostics
                                                      │
                                                      ▼
                                            RequirementScheduler
                                                      │
                                                      ▼
                                              build_for_outcome /
                                              build_for_mcdc_pair
                                                      │
                                                      ▼
                                            InstanceDrivenSolver
                                                      │
                                                      ▼
                                                  CoverageReport
```

The loop terminates when no requirement changes status, when
`max_iterations` is exhausted, or when `max_wall_seconds` elapses.
Exhaustion marks remaining pending requirements as `BUDGET_EXHAUSTED`.

## JSON schema (`schema_version = "1.0"`)

Top-level:

```json
{
  "schema_version": "1.0",
  "summary": {
    "total_decisions": 1,
    "total_outcome_requirements": 3,
    "total_mcdc_requirements": 0,
    "covered": 1,
    "uncovered": 2,
    "infeasible": 0,
    "budget_exhausted": 0,
    "unattempted": 0,
    "coverage_percent": 33.33
  },
  "decisions": [
    {
      "decision_id": "scope0:step_0:filter:0",
      "site": "filter",
      "expression_sql": "a > 10",
      "group_scoped": false,
      "outcomes": [
        {"outcome": "true", "status": "covered"},
        {"outcome": "false", "status": "uncovered"},
        {"outcome": "null", "status": "uncovered"}
      ],
      "mcdc_pairs": [],
      "evidence": [{"outcome": "true", "rows": [["t", 1]]}],
      "diagnostics": [
        {
          "code": "uncovered.outcome.false",
          "message": "decision scope0:step_0:filter:0 outcome false not observed",
          "details": {
            "rows_with_true": 1,
            "rows_with_false": 0,
            "rows_with_null": 0,
            "target_outcome": "false"
          }
        }
      ]
    }
  ]
}
```

### Versioning

Schema version bumps follow these rules:

- **Backward-compatible additions** (new optional keys, new enum members
  introduced only when present) do **not** bump the version.
- **Breaking changes** — removing a key, renaming a key, changing the
  semantics or allowed values of a field — require bumping the version
  and are called out in release notes.

## Worked example

```python
from parseval.instance import Instance
from parseval.symbolic import run_campaign

SCHEMA = "CREATE TABLE t (a INT, b INT);"
instance = Instance(ddls=SCHEMA, name="demo", dialect="sqlite")
instance.create_row("t", {"a": 20, "b": 1})

result = run_campaign(
    "SELECT a FROM t WHERE a > 10 OR b IS NULL",
    instance,
    max_iterations=20,
    solver_timeout_ms=5000,
)

print(result.coverage_report.to_json(indent=2))
```

`result.coverage_report` is a :class:`CoverageReport`. Use `to_dict()`
for programmatic access or `to_json(indent=2)` for human inspection.
The `result.logger.records` list holds a per-iteration JSON-line audit
log showing which requirements were attempted and why their status
changed.

## Out of scope (for this iteration)

- Correlated subqueries.
- Set operations (`UNION`, `INTERSECT`, `EXCEPT`).
- Window functions.
- Exists decisions are coarse: the encoder currently reports EXISTS as
  unconditionally TRUE. Future work will propagate 3VL outcomes from
  dependency bindings.
