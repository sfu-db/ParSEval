# ParSEval

ParSEval generates minimal test database instances that exercise all execution branches of a SQL query's logical plan. It uses branch-coverage-driven symbolic reasoning, speculative data generation, and SMT solving (Z3) to produce databases that make queries return non-empty, distinguishing results.

## Quick start

```bash
uv sync
uv run pytest
uv pip install -e .
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
    config=GenerationConfig(timeout_ms=1_000, time_limit_s=60),
)

print(len(result.covered), "of", len(result.reached), "outcomes covered")
print(result.instance)
```

`GenerationConfig` controls the solver and generation time limits,
speculation, and the provider of unconstrained values. Generation returns one
database, `result.instance`, that covers both productive and rejected query
paths (`None` if no database was found); `result.nonempty` tells whether the
query output is productive, and outcomes that could not be covered remain
visible in `result.failed`.

`instantiate_db` generates a database for a query and loads it into a backend
given by an SQLAlchemy URL. `disprove` checks two queries: it generates a
database for each, runs both queries on each database, and compares the
results.

```python
from parseval import Verdict, disprove

result = disprove(
    "SELECT name FROM users WHERE age > 25",
    "SELECT name FROM users WHERE age >= 25",
    "CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, age INTEGER)",
    "sqlite:///check.sqlite",
    "sqlite",
)
assert result.verdict is Verdict.NEQ
print(result.counterexample, result.results)
```

The verdict is `NEQ` with a counterexample database, `EQ` when the queries
agree on every generated database (evidence, not a proof), `SYNTAX_ERROR`,
`TIMEOUT`, or `UNKNOWN` when the generator does not support a query or its
database gives neither query rows.

## Schema input

The schema is a string of DDL statements in the given dialect, separated by
semicolons. Only two statement forms are accepted:

- `CREATE TABLE` (optionally `IF NOT EXISTS`) with explicit columns: types,
  `NOT NULL`, `DEFAULT`, `CHECK`, and inline or table-level `PRIMARY KEY`,
  `UNIQUE`, and `FOREIGN KEY` constraints.
- `ALTER TABLE ... ADD CONSTRAINT` for keys, foreign keys, and `CHECK`
  constraints on tables created in the same string.

Tables may reference tables declared later in the string. Any other statement
(views, `CREATE TABLE ... AS SELECT`, `ALTER TABLE ... ADD COLUMN`, `INSERT`)
raises `DDLImportError`.

```python
from parseval import Catalog

schema = """
CREATE TABLE users (
    id INT PRIMARY KEY,
    name VARCHAR(50) NOT NULL,
    age INT CHECK (age >= 0)
);
CREATE TABLE orders (
    id INT PRIMARY KEY,
    user_id INT,
    total DECIMAL(10, 2)
);
ALTER TABLE orders ADD CONSTRAINT fk_orders_user
    FOREIGN KEY (user_id) REFERENCES users (id);
"""
catalog = Catalog.from_ddl(schema, dialect="postgres")
```

`instantiate_db` and `disprove` also run the schema on the backend, so it must
be valid there too. SQLite, for example, has no `ALTER TABLE ... ADD
CONSTRAINT`; declare its keys inside `CREATE TABLE` instead.

## What is new

- **Richer U-semiring model**: `UAgg` models aggregation and `UOrder` models
  ordering.
- **CSP solver**: simple constraints are solved by a CSP model, leaving Z3 for
  the rest.
- **Extensive query parser**: broader SQL coverage when lowering queries into
  typed relational terms.
- **Multiple dialects**: SQLite, MySQL, and PostgreSQL are supported.

## Architecture

- `parser/` lowers DDL and SQL into typed relational terms.
- `terms/` holds the typed term arena shared by every stage.
- `uexpr/` compiles and normalizes U-expressions.
- `symbolic/` executes scalar operations concolically: a concrete value and a
  term per operation, with one set of SQL semantics.
- `instance/` stores databases as symbolic inputs and executes U-expressions
  over them (the concolic machine).
- `smt/` solves for candidate rows: a CSP for simple constraints, Z3 for the rest.
- `generator/` records the U-semiring branch outcomes an execution reaches
  and runs the concolic loop.

Generation first targets productive output, then appends rows that cover
further outcomes while preserving the covered ones. Coverage always describes
the final database.

## Disprove benchmarks

`scripts/disprove_bird.py` checks DAIL-SQL's BIRD-dev predictions against the
gold queries on SQLite; `scripts/disprove_leetcode.py` checks LeetCode query
pairs on a MySQL server, recovering each problem's DDL from the dataset's JSON
schema and constraints and skipping pairs with a non-SELECT query. Each writes
one JSON record per pair and a `<output stem>.summary.json` with verdict counts.

```bash
uv run python scripts/disprove_bird.py --limit 0 --workers 8

uv run python scripts/disprove_leetcode.py --limit 0 --workers 8 \
  --connection-string mysql+pymysql://root:rootpass@127.0.0.1:3306/mydb
```

The GitHub workflows `run-sqlite-test.yml` and `run-mysql-test.yml` run them.

## Experimental results

Experiment outputs are available on GitHub Actions. Open the repository's
Actions tab, choose the relevant workflow (Disprove BIRD-dev (SQLite) or
Disprove LeetCode (MySQL)), and select the latest successful run. You can
download the generated result and metric files from the run's Artifacts
section. The current false positives in the results come from aggregates with
DISTINCT (such as `COUNT(DISTINCT x)`) and will be fixed soon.
