# Instances and concolic execution

An `Instance` stores a database whose every cell and row multiplicity is an
input of one `parseval.symbolic.Runtime`. It keeps the concrete value of each
input, so the same object is the saved data and the symbolic state that
execution and solving refer to. Instances are persistent: `insert` and
`assign` return new instances. A slot with multiplicity zero stores no row;
it remains a *candidate row* whose inputs the solver may choose.

## Modules

| Module | Responsibility |
| --- | --- |
| `model.py` | `Instance` and `Slot`: inputs, values, export of stored rows |
| `valuation.py` | `Valuation`: Term construction with folding of closed inputs, U-semiring operations over INTEGER multiplicities, concrete values |
| `machine.py` | `Machine`: execution of compiled U-expressions; reports every decision to an `Observer` |
| `relations.py` | Rows, weighted entries, bags, sequences, bindings, environments |
| `aggregates.py` | COUNT/SUM/AVG/MIN/MAX as Terms over weighted inputs |
| `ordering.py` | Concrete ORDER BY comparison and window functions |
| `constraints.py` | Keys, foreign keys, CHECK, generated columns and storage limits as predicates |
| `domain.py` | Value carriers, placeholders, `Domain` (constants and their neighbours, bounds, LIKE fillers; shared by the CSP and speculation) and `Provider`s of fresh values |

## Execution

The machine interprets the simplified U-expression directly. A `Sum` ranges
over the entries of the bag its variable is drawn from; products schedule
filters as soon as their rows are bound, use hash lookups for equi-joins on
stored values, and drop bindings whose weight is the constant zero. Every
value is a Term: constant for stored data, symbolic in the open inputs of
candidate rows. `‖m‖` is `CASE WHEN m > 0 THEN 1 ELSE 0`, `not(m)` is
`CASE WHEN m = 0 THEN 1 ELSE 0`, and products with a 0/1 factor become CASE,
which keeps solver arithmetic linear.

SQL runtime errors are values (`Failure`) until a stored row really
evaluates them; then `ExecutionError` is raised. ORDER BY keeps sort keys and
derives symbolic positions only when LIMIT or OFFSET needs them. Windows are
computed concretely over present rows.

`Machine(..., budget=k)` bounds the candidate rows in one binding. Stored
rows are always enumerated, so concrete results and coverage do not depend on
the budget; it only bounds symbolic work across joins.

Dialect semantics come from the catalog: SQLite and MySQL divide by zero to
NULL and convert text to numbers leniently (`symbolic.Semantics`).
