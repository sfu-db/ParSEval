# Branch coverage of U-semiring decisions

Coverage is about data, not rows: an outcome of a decision is covered when
some binding of stored rows reaches the decision with that outcome.

A *site* is a path of child positions from the query root (hash-consing
shares equal subterms, so a TermId cannot identify an occurrence). A
`Target` is a site and an outcome. The machine reports:

| Decision | Outcomes |
| --- | --- |
| every predicate, including parts of AND/OR/NOT | `true`, `false`, `unknown` (when NULL is possible) |
| `‖m‖`, `not(m)` | `positive`, `zero` (only when correlated with outer rows) |
| each summand of `+`, each `Sum` | `positive` |
| DISTINCT over rows | `duplicate` |
| scalar subqueries | `nonempty`, `empty` (correlated only) |
| GROUP BY | `group`, `multiple`; per aggregate argument `null`, `duplicate` |
| global aggregates | `nonempty`, `empty` (correlated only) |
| final projection | `duplicate` rows; per column `null`, `duplicate`, `distinct` |
| query | `output`: rows exist and every uncorrelated aggregate has input |

`Recorder` is the observer. The machine passes whether stored rows cover an
outcome, decided concretely, and a callback that builds the outcome's
condition `presence > 0 AND outcome` over open inputs. The recorder calls it
only to keep a bounded number of witnesses: conditions of uncovered outcomes
(`candidates`) for solving, and of covered outcomes that depend on candidate
rows (`witnesses`) for preserving them. An outcome whose condition folds to
TRUE is `stable`: no candidate row can change it.
