# ParSEval: Plan-aware Test Database Generation for SQL Equivalence Evaluation

ParSEval considers the specific behaviors of each query operator and covers all possible execution branches of the logical query plan by adapting the notion of branch coverage to query plans.


## Current Status & Upcoming Release

We are actively refactoring ParSeval to a pure Python implementation to provide a cleaner, more user-friendly interface. The new version will include improved functions for query equivalence checking and database instance generation.

> The current codebase may be messy during this transition. The new version will be released soon. We recommend waiting for it to reduce deployment and maintenance effort.


The new version of ParSEval provides the following features:
1.	`check_eq(schema, gold, pred, dialect, verify_first=False)` – combines formal verification and test-case-based approaches for query equivalence evaluation. When verify_first=True, ParSEval prioritizes formal verification when checking query pairs, while still leveraging test-case-based evaluation when needed.

2.	`db_generate(schema, sql, dialect, **kwargs)` – generates test database instances based on the input SQL.

Users can set verify_first=False to to use testcase based evaluation only.


## Future Plans
- Pure Python Refactor – Completed soon: full transition from mixed implementations to a clean Python-only codebase.
- NULL-related constraints


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
pip install -r requirements.txt
```

### Usage

Normally, one invoke the tool as 
```bash
python main2.py --strategy complete  --dataset bird --dialect sqlite --start 0 --end 100
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

### Experiment Setup
- [Install Docker](https://docs.docker.com/engine/install/)
- Dataset
    - Download Leetcode/Literature/Bird/Spider datasets [here](https://drive.google.com/drive/folders/12y5tR2JeSf2cVpp_woHn6CiQ9YiY7J25?usp=drive_link).
    - Could also download official database instances for [bird](https://bird-bench.github.io/) and [spider](https://yale-lily.github.io/spider).

### Running Experiments
Commands needed can be found in the tests folder.





