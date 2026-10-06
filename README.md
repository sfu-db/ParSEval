# ParSEval

ParSEval compiles SQL into a typed U-expression and generates small concrete
databases by concolic execution of that U-expression. Every stored cell and
row multiplicity is a symbolic input; executing the query on the data covers
branch outcomes of the U-semiring (predicate truth, NULLs, empty and nonempty
subqueries, join contributions, groups, duplicates), and a solver appends rows
that cover the outcomes the data does not cover yet.

## Quick start

```bash
uv sync
uv run pytest
```

```python
from parseval import Catalog, GenerationConfig, generate

catalog = Catalog.from_ddl(
    "CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, age INTEGER)",
    dialect="sqlite",
)
result = generate(
    "SELECT name FROM users WHERE age > 25",
    catalog,
    config=GenerationConfig(timeout_ms=1_000, max_fail_retry=3),
)

print(result.coverage.ratio)
print(result.instance)
```

`GenerationConfig` controls solver time, candidate rows and the symbolic
budget. Generation returns one database containing productive and rejected
query paths as `result.instance` (`None` if none was found);
`result.nonempty` tells whether the query output is productive, and outcomes
that could not be covered stay visible in `result.coverage.failed`.

## Architecture

- `parser/` lowers DDL and SQL into typed relational terms.
- `terms/` holds the typed term arena shared by every stage.
- `uexpr/` compiles and normalizes U-expressions.
- `symbolic/` executes scalar operations concolically: a concrete value and a
  term per operation, with one set of SQL semantics.
- `instance/` stores databases as symbolic inputs and executes U-expressions
  over them (the concolic machine).
- `coverage/` records the U-semiring branch outcomes an execution reaches.
- `smt/` solves for candidate rows: a CSP for simple constraints, Z3 for the rest.
- `generator/` runs the concolic loop.

Generation first targets productive output, then appends rows that cover
further outcomes while preserving the covered ones. Coverage always describes
the final database.

## PostgreSQL corpus experiment

Experimental replay and outcome reporting live in `scripts/experiments/`,
outside the installed `parseval` package.

`data/postgres.csv` contains 709 query pairs (1,418 statements). The benchmark
uses the original SQL and DDL and exports one SQLite file per generated database.
SQLite exports are data artifacts; PostgreSQL replay uses the original schema.

```bash
uv run python scripts/benchmark_postgres_coverage.py \
  --limit 10 --max-fail-retry 3 \
  --sqlite-dir results/postgres-corpus --output results/postgres-data.jsonl

uv run python scripts/audit_postgres_dataset.py \
  --output results/postgres-audit.jsonl --workers 4 \
  --postgres-dsn 'dbname=parseval_test'

PARSEVAL_POSTGRES_DSN='dbname=parseval_test' \
  uv run pytest tests/generator/test_postgres_dataset.py
```

Set `PARSEVAL_FULL_POSTGRES_DATASET=1` to test every original statement.
The PostgreSQL connection must permit temporary schema creation; replay rolls
back each case. Dataset errors and timeouts are reported explicitly. See
[coverage](src/parseval/coverage/README.md) and
[generation](src/parseval/generator/README.md) for the design.
