# Speculation

`speculate` samples rows for a query before any solving. Concolic generation
(`parseval.generator`) starts from the result and solves only for the branch
outcomes speculation missed. `GenerationConfig(speculate=False)` starts from
an empty database instead.

## Hints from the compact IR

Column lineage maps every field of a compact relational term (join, filter,
group_fold, topk, ...) to the columns it copies. A column belongs to an
*occurrence*: each reference to a table in the query, so a self-join or a
subquery over the outer table reads different rows, and each occurrence has
occurrences of its foreign-key parents. Comparisons then give:

| Hint | Source | Use |
| --- | --- | --- |
| value classes | equi-joins, IN, correlated equalities, foreign keys | columns of a class share one value per tuple, so rows meet |
| constants | literals compared with a class or a value depending on it, LIKE patterns, NULL tests | `Domain.around` gives each constant and its neighbours |
| formulas | predicates of filters and joins, over atoms `class op constant`, tests on one class and joint tests on several (`x > g`, `x + g = 9`); an equality of two columns of a class, IN and `= (SELECT ...)` are `NOT class IS NULL` | goals (below) |
| checks | CHECK constraints, ENUM lists among them | tests or joint tests holding in every goal |
| keys | GROUP BY, window PARTITION BY | shared by the tuples of a group, drawn again per group |
| group size | lower bounds on COUNT | tuples per group |
| group count | LIMIT/OFFSET | groups in the first batch |

A LIMIT matters only when the result exceeds it, so `LIMIT c OFFSET o` asks
for `o + c + 1` result rows (LIMIT 1 gets two), whatever order the database
executes. Result rows are groups when the query groups, tuples otherwise. A
lower bound on COUNT sets the group size: `COUNT(*) > n` asks for `n + 1`
rows per group, `COUNT(*) >= n` and `COUNT(*) = n` for `n`; upper bounds hold
for one row. Neither is capped: a query that needs many rows gets them.

Lineage separates copies, bounds and dependence. A field copies a column
through casts, unary functions, MIN and MAX; copies join classes and form
atoms, so `x = (SELECT MAX(y) ...)` joins the classes of `x` and `y`. AVG is
bounded by its argument: when every value satisfies `x > 10`, so does
`AVG(x)`, so it forms atoms without joining classes. Arithmetic, CASE and SUM
only depend on their columns and spread constants, so
`HAVING SUM(x) / COUNT(*) > 400` gives `x` values around 400.

## Goals

Each batch follows a goal and assigns only what the goal's outcome needs (an
OR is TRUE when one item is). A goal narrows one
`parseval.instance.domain.Space` per class, the value spaces the CSP solves
with; a class takes its usual sample when the space admits it.

| Goal | Batch |
| --- | --- |
| positive | every formula TRUE, without negative occurrences |
| pinned hint | positive, with a class that only receives hints (`HAVING SUM(x) / COUNT(*) > 400`) set to one hint |
| inner | positive, without the NULL-padded sides of outer joins |
| all | positive, with the negative occurrences (`NOT EXISTS` finds a row) |
| repeat | a group of two tuples equal in the output's columns and aggregate arguments |
| negative atom | one atom FALSE or UNKNOWN, the other atoms as the formula allows |
| NULL | an output column or aggregate argument NULL |
| reuse | a new group equal to an output row in one output column and different in the others |
| distinct | a new group with a non-NULL value different from an output row in one output column |

Until the output is productive only the positive and pinned-hint goals are
tried. The batch that makes it productive is remembered: reuse goals repeat
its values, and classes that only receive hints keep theirs in later goals.
Predicates over one column through functions (`strftime('%Y', d) > '1991'`)
are test atoms, checked by substituting a candidate value and folding. A
predicate over several columns (`x > g`, `x + g = 9`, `a.id < b.id`) is a joint
test: its classes are chosen together, from their candidates (current value,
hints, the productive value, one fresh value) or, for a predicate candidates
cannot meet such as an equation, by solving the test alone with the solver
(`parseval.smt`). CHECK constraints, ENUM lists among them, are tests that hold
in every goal, and every value fits its column's storage limit
(`parseval.instance.domain.limit`). What speculation cannot reach, the
concolic generator solves for.

## Coverage-greedy batches

Occurrences met only under negation (NOT, NOT EXISTS, NOT IN, anti-joins) are
negative: their rows can only block output. The positive goal leaves them out;
another goal includes them, and so do the negative goals of atoms over them.
A batch adds groups of tuples, one row per occurrence of the goal, closed under
foreign-key parents. Constant classes keep one value through the batch, key
classes (GROUP BY, PARTITION BY and the output's columns) one value per group.
A value is NULL, a constant of its class, a value already stored, or a fresh
value from the configured `Provider`, which also fills the columns the query
never mentions. A row whose key is already stored is left out; the stored row
takes its place.

A batch is kept when its rows satisfy the catalog's integrity constraints and
concrete execution of the query makes the output productive and covers a new
outcome without losing one. Stored rows are permanent, so no row is kept
before the output is productive: a row of `s` would block
`NOT EXISTS (SELECT 1 FROM s)` forever. Like the concolic generator, each
goal is tried once per kept instance; speculation ends when every goal fails
on the current instance.

## Value providers

`GenerationConfig(provider=...)` takes any callable
`(table: TableDecl, column: ColumnBinding, existing, unique: bool) -> value`
returning a value of the column's kind. `existing` holds the values of that
kind already in use. `unique` is set for a column that alone forms a key and
is not joined with another column: its value must lie outside `existing`.
Otherwise `existing` only informs the choice; speculation creates the repeats
it needs by reusing stored values itself. `parseval.instance.domain.sequential`
(the first unused of 1, 2, ...; "a", "b", ...) is the default; a Faker-backed
provider or samples of real data plug in the same way.
