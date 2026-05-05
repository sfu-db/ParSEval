# ParSEval: Plan-aware Test Database Generation for SQL Equivalence Evaluation

ParSEval considers the specific behaviors of each query operator and covers all possible execution branches of the logical query plan by adapting the notion of branch coverage to query plans.


## File Structure

The repo contains following supplemental materials:
- source code of ParSEval
- Source code of query parser
```
├── src # Source code of ParSEval
├── requirements.txt # pip requirements
└── README.md
```


## Getting Started

### Environment Setup with uv
To set up your environment:

```bash
# Create a virtual environment (if you don't have one)
uv venv

# Install all dependencies from requirements.txt or pyproject.toml
uv sync

# Install ParSEval in editable mode
uv pip install -e .
```

If you prefer, you can use `python -m venv` or conda.


### Usage

#### As a Library
To generate test database instances for an input query:

```python
from parseval import instantiate_db
instantiate_db(sql, schema, host_or_path, db_id, dialect, **kwargs)
```

To test the equivalence of two queries:

```python
from parseval import disprove
disprove(sql1, sql2, schema, host_or_path="/tmp", dialect="sqlite", **kwargs)
```


## Updates
- Replaced Calcite with SQLGlot.
- Integrated symbolic expressions with SQLGlot classes.
- Handled special functions from SQLite and MySQL.
- For simple queries (e.g., `SELECT count(*) FROM singers`), randomly generate a database instance.

## To Be Done (TBD)
- Refactor the database manager to support more database backends.
- Load existing data first when using non-SQLite databases (e.g., Postgres, MySQL) to avoid wiping all existing data.
- Model more dialect-specific functions and keywords with symbolic expressions.


### Experiment Setup
- [Install Docker](https://docs.docker.com/engine/install/)
- Datasets:
    - Download Leetcode/Literature/Bird/Spider datasets [here](https://drive.google.com/drive/folders/12y5tR2JeSf2cVpp_woHn6CiQ9YiY7J25?usp=drive_link).
    - You can also download official database instances for [Bird](https://bird-bench.github.io/) and [Spider](https://yale-lily.github.io/spider).




