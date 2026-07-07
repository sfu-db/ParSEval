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

### Coverage Thresholds

Control how many rows are generated per branch type. Set a threshold to `0` to skip that branch type entirely. Higher values generate more rows but improve coverage.

```python
result = instantiate_db(
    sql="SELECT name FROM users WHERE age > 25",
    schema="CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)",
    connection_string="sqlite:////tmp/test.db",
    dialect="sqlite",
    atom_null=2,           # Generate 2 rows where WHERE evaluates to NULL
    atom_false=1,          # Generate 1 row where WHERE is FALSE
    project_null=1,        # Generate 1 row where SELECT output is NULL
    distinct_duplicate=1,  # Generate 1 duplicate row for DISTINCT elimination
    distinct_unique=1,     # Generate 1 unique row for DISTINCT
)
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `atom_null` | Rows where a WHERE/ON predicate evaluates to NULL | 1 |
| `atom_false` | Rows where a WHERE/ON predicate is FALSE | 1 |
| `atom_dup` | Rows that trigger duplicate detection | 1 |
| `project_null` | Rows where a projected column is NULL | 1 |
| `distinct_duplicate` | Duplicate rows eliminated by DISTINCT | 1 |
| `distinct_unique` | Unique rows retained by DISTINCT | 1 |
| `max_iterations` | Max iterations for the symbolic engine | 10 |

### Connection Strings

```python
# SQLite
connection_string="sqlite:////tmp/test.db"

# MySQL
connection_string="mysql+pymysql://user:password@localhost:3306/mydb"

# PostgreSQL
connection_string="postgresql://user:password@localhost:5432/mydb"
```

## File Structure

```
src/parseval/
├── main.py              # Public API: instantiate_db, disprove
├── disprover.py         # Query equivalence disproval
├── states.py            # Result types (Verdict, DisproveResult, etc.)
├── symbolic/            # Coverage-driven data generation engine
├── solver/              # Constraint satisfaction (CSP + SMT/Z3)
├── plan/                # Query plan analysis
├── instance/            # In-memory row management and persistence
└── domain/              # Type-aware value generation
```

## Running Experiments

```bash
python tests/experiment/test_sqlite.py \
    --schema_fp data/sqlite/schema.json \
    --gold_fp data/sqlite/dev.json \
    --preds_fp data/sqlite/dail.txt \
    --output_dir results
```

## Updates

- See the `dev` branch for the latest features and ongoing development.
- See the `webui` branch for the frontend web interface of ParSEval.

## Experimental Results

Experiment outputs are available from GitHub Actions. Open the repository’s **Actions** tab, choose the corresponding workflow, such as **Run SQLite Experiment** or **Run MySQL Experiment**, and select the latest successful run. The generated result and metric files can be downloaded from the run’s **Artifacts** section.