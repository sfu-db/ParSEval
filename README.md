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
from parseval import disprove, Semantics

result = disprove(
    sql1="SELECT name FROM users WHERE age > 25",
    sql2="SELECT name FROM users WHERE age >= 26",
    schema="CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)",
    connection_string="sqlite:////tmp/test.db",
    dialect="sqlite",
    semantics=Semantics.BAG,  # or Semantics.SET
)
print(result.verdict)  # Verdict.EQ or Verdict.NEQ
```

### Supported Dialects

- `sqlite` — full support
- `mysql` — supported via DBManager
- `postgres` — supported via DBManager

## File Structure

```
src/parseval/
├── main.py              # Public API: instantiate_db, disprove
├── symbolic/
│   ├── engine.py        # SymbolicEngine — orchestrates generation
│   ├── speculate.py     # Speculative data generation (Propagator + Resolver)
│   ├── evaluator.py     # Branch coverage evaluation
│   ├── uexpr.py         # UExprToConstraint — Z3-based constraint solver
│   ├── constraints.py   # Coverage gap → solver constraint translation
│   └── types.py         # BranchType, CoverageTarget, BranchTree
├── solver/
│   ├── unified.py       # Tiered solver (Trivial → Heuristic → CSP → SMT)
│   ├── smt.py           # Z3 SMT solver with SQL-to-Z3 translation
│   ├── lowering.py      # Predicate lowering (SQL → column constraints)
│   └── value_space.py   # CSP domain narrowing
├── plan/
│   ├── planner.py       # Query plan builder (Scan, Join, Filter, etc.)
│   ├── rex.py           # Concrete expression evaluation + Symbol types
│   └── context.py       # Row/Environment for plan evaluation
├── instance/
│   ├── core.py          # Instance — in-memory row management
│   ├── io.py            # Persistence (to_db)
│   └── loader.py        # SQLAlchemy-based DB writer
├── domain/              # Type-aware value generation
├── db_manager.py        # Multi-backend connection management
├── logger.py            # Configurable logging
└── states.py            # Result types (Verdict, DisproveResult, etc.)
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