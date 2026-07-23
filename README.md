# ParSEval: Plan-aware Test Database Generation for SQL Equivalence Evaluation

ParSEval generates minimal test database instances that exercise all execution branches of a SQL query's logical plan. It uses branch-coverage-driven symbolic reasoning, speculative data generation, and SMT solving (Z3) to produce databases that make queries return non-empty, distinguishing results.

## Quick Start

```bash
uv venv
uv sync
uv pip install -e .
```

### Generate a Test Database

```python
from parseval import instantiate_db

result = instantiate_db(
    sql="SELECT name FROM users WHERE age > 25",
    schema="CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)",
    connection_string="sqlite:////tmp/test.db",
    dialect="sqlite",
)
print(result.success, result.generation.rows_generated)
```

### Disprove Query Equivalence

```python
from parseval import disprove

result = disprove(
    sql1="SELECT name FROM users WHERE age > 25",
    sql2="SELECT name FROM users WHERE age >= 26",
    schema="CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)",
    connection_string="sqlite:////tmp/test.db",
    dialect="sqlite",
    semantics="bag",  # or Semantics.SET
)
print(result.verdict)  # Verdict.EQ or Verdict.NEQ
```

### Generation Configuration

Use `GenerationConfig` to bound path enumeration, solving, and generated rows.

```python
from parseval import GenerationConfig, instantiate_db

result = instantiate_db(
    sql="SELECT department, COUNT(*) FROM employees GROUP BY department",
    schema="CREATE TABLE employees (id INT, department TEXT, salary INT)",
    connection_string="sqlite:////tmp/test.db",
    dialect="sqlite",
    generation_config=GenerationConfig(
        groups=6,
        rows_per_group=3,
        max_paths=256,
        max_solver_calls=48,
    ),
)
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `bootstrap_rows` | Speculative rows per table | 3 |
| `bootstrap_negatives` | Include speculative predicate counterexamples | `True` |
| `root_rows` | Rows requested at the root | 3 |
| `groups` | Number of aggregate groups | 3 |
| `rows_per_group` | Rows per aggregate group | 3 |
| `subquery_rows` | Rows per scalar subquery | 1 |
| `order_competitors` | Competing rows for ordering witnesses | 1 |
| `max_path_depth` | Maximum backward path depth (`None` means full acyclic depth) | `None` |
| `max_paths` | Maximum enumerated execution paths | 256 |
| `max_rows_per_table` | Generated-row cap per table | 128 |
| `max_total_rows` | Generated-row cap across all tables | 512 |
| `max_solver_calls` | Solver-call budget shared by bootstrap and path solving | 48 |
| `solver_timeout_ms` | Timeout for each solver call | 1000 |
| `seed` | Deterministic generation seed | 142 |

### Connection Strings

```python
# SQLite
connection_string="sqlite:////tmp/test.db"

# MySQL
connection_string="mysql+pymysql://user:password@localhost:3306/mydb"

# PostgreSQL
connection_string="postgresql://user:password@localhost:5432/mydb"
```

## Solver Backend

To speed up the constraint solving, the solver (`solver/`) follows a cascade strategy: partition the constraint problem by variable independence, then try the CSP backend first, falling back to the SMT (Z3) backend for each component. Supports type constraints (INT, TEXT, DATE, TIME, TIMESTAMP, BOOLEAN), NULL semantics, string domains, and temporal bounds.

## File Structure

```
src/parseval/
├── main.py              # Public API: instantiate_db, disprove
├── states.py            # Result types (Verdict, DisproveResult, etc.)
│
├── generator/           # Plan-aware data generation│
├── solver/              # Solver orchestration (CSP → SMT cascade)
│
├── plan/
│   ├── explain.py       # DataFusion-based query plan extraction
│   ├── context.py       # DerivedSchema, Row — intermediate representations
│   ├── rex.py           # Symbol, Variable, Environment — row expression eval
│   ├── session.py       # Session-level plan analysis
│   └── helper.py        # Plan AST helpers
│
├── instance/            # Schema parsing and management
└── domain/              # Type-aware value spaces and domain constraints
```

## Running Experiments

```bash
python scripts/exp_sqlite_disprover.py \
    --schema_fp data/sqlite/schema.json \
    --gold_fp data/sqlite/dev.json \
    --preds_fp data/sqlite/dail.txt \
    --output_dir results
```

## Updates
- Update the query parser to Datafusion. 
- See the `dev` branch for the latest features and ongoing development.
- See the `webui` branch for the frontend web interface of ParSEval.

## Experimental Results

Experiment outputs are available on GitHub Actions. Open the repository's **Actions** tab, choose the relevant workflow (for example, **Run SQLite Experiment** or **Run MySQL Experiment**), and select the latest successful run. You can download the generated result and metric files from the run's **Artifacts** section. Current false positives in the experimental results are caused by the aggregation `DISTINCT` pattern and will be fixed soon.
