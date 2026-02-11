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

## Get started 

### Install the Query Parser
Please download and set up the query parser from the [repository](https://github.com/sfu-db/qParser).
### Set Up the Python Environment
1. Please use conda or venv to create a virtual environment. Run following command to install requirements.

```bash
# Example with venv
python -m venv venv
source venv/bin/activate
# Or with conda
conda create -n parseval-dev python=3.8
conda activate parseval-dev
```
2. Install the required dependencies:
```bash
poetry install
```

### Usage

Normally, one invoke the tool as 
```bash
from parseval import instantiate_db
instantiate_db(workspace, schema, sql, dialect, **kwargs):
```
to generate test database instances for input query SQL1.
To test the equivalence of two queries:
```bash
from parseval import disprove_queries
disprove_queries(schema, gold, pred, dialect, **kwargs)
```

One can enhance the readability of generated data for common column types by customizing the data generation strategy in the `register_default_generators` function.

```python
# Integer generator
def int_generator(existing_values: Optional[Set[Any]] = None, is_unique: bool = False) -> int:
    """
    Generate a random integer value.        
    Args:
        existing_values: Set of existing values to avoid duplicates if is_unique is True
        is_unique: Whether the value should be unique
        
    Returns:
        int: A random integer value
    """
    value = random.randint(1, 100)
    if is_unique and existing_values:
        while value in existing_values:
            value = random.randint(1, 100)
    return value
from .registry import ValueGeneratorRegistry
ValueGeneratorRegistry.register('int', int_generator)
```

## TBD
1. adapting MySQL/SQLite dialect processing code
2. Handling special functions from SQLite/MySQL
3. 
### Experiment Setup
- [Install Docker](https://docs.docker.com/engine/install/)
- Dataset
    - Download Leetcode/Literature/Bird/Spider datasets [here](https://drive.google.com/drive/folders/12y5tR2JeSf2cVpp_woHn6CiQ9YiY7J25?usp=drive_link).
    - Could also download official database instances for [bird](https://bird-bench.github.io/) and [spider](https://yale-lily.github.io/spider).




