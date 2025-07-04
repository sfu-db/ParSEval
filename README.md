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
pip install -r requirements.txt
```
