# SMT model

`Solver.solve` encodes coverage obligations over weighted, finite relation support.
A SAT result supplies a concrete instance. `bounded_unsat` applies only within the
configured support, physical-row bounds, and the modeled scalar domains below;
it is not a proof of unrestricted SQL infeasibility. Budget exhaustion or an
incomplete Z3 check returns UNKNOWN.

## Text and dates

- LIKE accepts literal and symbolic patterns, with `%`, `_`, and backslash
  escaping. Both operands propagate NULL to UNKNOWN. Wildcards include newlines.
  A trailing unescaped backslash is excluded as an invalid pattern.
- ILIKE and LOWER use **ASCII case semantics**. Non-NULL inputs participating in
  these operations are constrained to ASCII. This is a deliberate restricted
  search domain, not full Unicode or locale-aware case conversion. In particular,
  bounded-UNSAT does not rule out non-ASCII witnesses.
- Literal LIKE/ILIKE patterns use Z3 regular expressions. Dynamic patterns and
  LOWER use recursive string functions with no fixed length cap. Difficult string
  synthesis can return UNKNOWN within the solver deadline.
- `cast_string_to_date` accepts canonical `YYYY-MM-DD`, years 0001–9999, using
  proleptic Gregorian leap-year and month-length rules. It produces the ordinal
  used by the existing DATE representation. NULL stays NULL. Invalid dates and
  alternate formats are excluded from non-NULL inputs; SQL error execution,
  DateStyle-dependent formats, BC years and infinity are not modeled.

## Modeled standard deviation

`stddev`/`stddev_samp` and `stddev_pop` use exact real arithmetic, not a database's
floating-point implementation. With non-NULL weighted moments

```
n = sum(weight)
s = sum(weight * value)
q = sum(weight * value * value)
```

the nonnegative result `d` satisfies:

```
sample:     d*d*n*(n-1) = n*q - s*s     (n > 1)
population: d*d*n*n     = n*q - s*s     (n > 0)
```

Otherwise the result is NULL. DISTINCT and aggregate FILTER are applied before
these moments. Zero-weight entries do not contribute. Irrational results remain
algebraic reals inside Z3. Nonlinear solving can return UNKNOWN. The concrete
validation evaluator uses numerical square roots; exact equality around irrational
results is therefore not a database-equivalence guarantee.

These operators are available to direct IR callers. SQL frontend recognition is
a separate concern; adding SMT encodings does not imply every SQL spelling or
dialect is lowered by the parser.

Run `python -m pytest tests/smt/test_text_and_deviation.py -q` for direct encoding
checks, and `scripts/benchmark_smt_postgres_patterns.py` for witness workloads.
