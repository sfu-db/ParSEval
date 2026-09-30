# ParSEval

ParSEval compiles SQL into a typed U-expression and generates small concrete
database instances that exercise semantic outcomes such as predicate truth,
NULL behavior, multiplicity, join contributions, and aggregate input sizes.
Generated SMT models are replayed with an independent concrete evaluator
before they count as coverage.

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
    config=GenerationConfig(timeout_ms=1_000, max_attempts=20),
)

print(result.coverage.ratio)
for case in result.counterexamples:
    print(case.instance)
```

`GenerationConfig` controls the semantic group-size boundary, SMT timeout,
symbolic support, encoding steps, materialized rows, number of target attempts,
and model minimization. Solver outcomes remain explicit: `bounded_unsat` means
only that the configured finite search was exhausted.

## Architecture

- `parser/` lowers DDL and SQL into typed relational terms.
- `uexpr/` compiles, normalizes, and concretely evaluates U-expressions.
- `coverage/` discovers witnessed semantic obligations and reports evidence.
- `smt/` encodes bounded weighted database instances in Z3.
- `generator/` schedules targets, invokes SMT, validates models, and expands
  the frontier.
- `instance/` stores concrete bag-valued database states.

The legacy plan/CSP solver remains isolated from the term-native pipeline and
is not used by `generate`.

## PostgreSQL corpus experiment

`data/postgres.csv` contains paired real-world queries. The benchmark generates
instances for both queries, validates them with the U-expression evaluator,
and independently materializes each instance in a temporary SQLite database.
PostgreSQL queries are transpiled for SQLite; portability failures are reported
separately from generation and semantic replay failures.

```bash
uv run python scripts/benchmark_postgres_coverage.py \
  --limit 10 \
  --solver-timeout-ms 1000 \
  --max-attempts 12 \
  --case-timeout-s 60 \
  --output results.jsonl
```

Use `--index`, `--dbid`, or `--mode inventory` to select smaller experiments.
