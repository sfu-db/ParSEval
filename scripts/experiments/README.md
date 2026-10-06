# Dataset experiments

This directory contains benchmark-only SQLite export, outcome reporting, and
optional PostgreSQL replay. These helpers are imported by `scripts/benchmark_postgres_coverage.py`
and dataset integration tests. They are not installed as part of `parseval`.

`sqlite.py` saves each retained database as a separate SQLite file, including
empty tables, NULLs, and duplicate rows. Numeric cells use REAL and dates/times
use ISO text. No PostgreSQL installation is needed for generation, export, or
the default dataset tests. Export is experimental and is not a library API.

```bash
uv run python scripts/benchmark_postgres_coverage.py \
  --limit 10 --max-fail-retry 3 \
  --sqlite-dir results/postgres-corpus --output results/postgres-data.jsonl
```

`postgres.py` loads each generated database into an isolated schema, checks
schema constraints, executes the original SQL, compares its result against the
U-expression evaluator, and rolls the transaction back. Numeric result cells
use `rel_tol=1e-9`, `abs_tol=1e-12` to match the core's float approximation;
row counts, multiplicities, NULLs, and nonnumeric cells are compared exactly. Supply a disposable
PostgreSQL connection through `--postgres-dsn` or `PARSEVAL_POSTGRES_DSN`.

`outcomes.py` reports input and result cardinalities and whether the result
changes from executing on an empty database. This prevents a populated but
irrelevant database, or a vacuous aggregate result, from counting as productive.

The exhaustive runner `scripts/audit_postgres_dataset.py` records every original
statement in `data/postgres.csv`, with explicit reuse of identical statements.
Its summary records resource budgets and source/dataset hashes. A run with
`source_unchanged: false` is not a reproducible audit of one code version.
