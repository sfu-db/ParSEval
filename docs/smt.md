# Solving for candidate rows

`solve(valuation, requirements, ...)` finds values of open inputs making one
predicate of every requirement TRUE. Requirements are folded Terms over the
inputs of candidate rows (see `parseval.instance.Valuation`).

1. `closure` adds the integrity constraints of every row whose inputs a
   requirement mentions, until no new input appears.
2. Requirements are partitioned into components that share no input.
3. Each component goes to the CSP (`csp.py`) and, if that is inconclusive or
   outside its fragment, to Z3 (`translate.py`).

## CSP

Predicates are normalized into positive formulas over atoms: an input
compared with a constant or another input, NULL tests, LIKE patterns, and
*tests*, predicates over a single input checked by execution. TRUE, FALSE and
UNKNOWN are pushed to the atoms exactly; weights over 0/1 multiplicities
become formulas stating which weights are positive; CASE operands are lifted
(`P(CASE c a b)` is `(c AND P(a)) OR (NOT c AND P(b))`). The search applies
unit propagation over disjunctions, branches on the one with fewest options,
narrows a value space per input and verifies the picked assignment by
execution. A test reads one input, so its outcome on each candidate is kept
for the whole search; later branches meet the same tests again. The search is
bounded by its branch count, not by a clock. When every branch is
contradictory the requirements are UNSAT, which is a proof because
normalization is exact. Relations between several inputs other than
(dis)equality, arithmetic over several inputs and symbolic positions go to Z3.

## Z3

Scalars are a value and a NULL flag; predicates are TRUE and UNKNOWN flags;
every Term has a definedness condition, and CASE requires only the selected
arm's. DATE is a day ordinal, TIMESTAMP and TIME are microseconds; calendar
fields use the civil-from-days algorithm with constant divisions, and
`strftime` compared with a constant of the same format is compared field by
field. Strings use the sequence solver; order against a constant and LIKE
with leading/trailing `%` have direct encodings.

Text read as a temporal is the ISO rendering of a fresh value. String inputs
that are only read that way, tested for NULL, or compared with each other or
with constants in one ISO pattern (directly or through CASE arms) are solved
as timestamps and decoded as ISO text, as fixed-width ISO text orders like
its moment; equality-only strings are likewise solved as integer codes.
Constant text chosen by a CASE is parsed concretely. With SQLite's text
temporals, text that is no moment reads as NULL (timed inputs have one value
above every moment for it; other text may start with a non-digit), a
temporal read as a number is its year (the hour of a time), and `strftime`
text read as a number is the digits of its leading fields.

Each check runs a few short restarts with different seeds and prefers
candidate multiplicities of zero through an unsat-core loop. Generated
non-NULL strings have at least `min_string_length` characters.
