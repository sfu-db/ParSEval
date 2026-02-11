import os, re
from typing import List, Tuple, Set, Union
from .data_generator import DataGenerator, dbgenerate
from .db_manager import DBManager

# from .generator import Generator

# from .db_manager import DBManager


def instantiate_db(workspace, schema, sql, dialect, **kwargs):
    """generates test database instances based on the input SQL."""
    dbgenerate(ddls=schema, query=sql, workspace=workspace, dialect=dialect, **kwargs)
    return workspace


def compare_df(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:
    if not result1 and not result2:
        return -1
    sentinel = -99999
    result1_filled = [[sentinel if v is None else v for v in row] for row in result1]
    result2_filled = [[sentinel if v is None else v for v in row] for row in result2]

    # Check shape (number of rows and columns)
    if len(result1_filled) != len(result2_filled):
        return 0
    if len(result1_filled) > 0 and len(result1_filled[0]) != len(result2_filled[0]):
        return 0
    if not order_matters:
        result1_filled = sorted(result1_filled)
        result2_filled = sorted(result2_filled)

    # Compare element-wise
    for r1, r2 in zip(result1_filled, result2_filled):
        if r1 != r2:
            return 0

    return 1


limit_pattern = re.compile(
    r"LIMIT\s+\d+\b(?:\s+OFFSET\s+\d+)?$", re.IGNORECASE
)  # LIMIT\s+(\d+)(?:\s*,\s*(\d+))?\s*$
orderby_pattern = re.compile(
    r"ORDER\s+BY\s+.*[^\)]$", re.IGNORECASE
)  ##ORDER\s+BY\s+([^,\s]+)\s*(ASC|DESC)?\s*$


def compare_queries(
    host_or_path, database, gold, pred, dialect, order_matters=False, **kwargs
):
    with DBManager().get_connection(host_or_path, database=database) as conn:
        message = {}
        predicted_res = []
        ground_truth_res = []
        predicted_res = conn.execute(pred)
        ground_truth_res = conn.execute(gold)
        if not ground_truth_res and predicted_res:
            message["msg"] = "Gold NULL VS Pred NOT NULL"

        print(predicted_res)
        print(ground_truth_res)

        eq = compare_df(
            list(ground_truth_res), list(predicted_res), order_matters=order_matters
        )
        if eq == 1:
            message["state"] = "EQ"
        elif eq == 0:
            message["state"] = "NEQ"
        else:
            message["state"] = "UNKNOWN"
        return message


def remove_limit(gold, pred):
    gold_limit_match = limit_pattern.search(gold)
    pred_limit_match = limit_pattern.search(pred)
    gold_limit = gold_limit_match.group(0) if gold_limit_match else None
    pred_limit = pred_limit_match.group(0) if pred_limit_match else None
    if gold_limit == pred_limit:
        query1 = re.sub(limit_pattern, "", gold)
        query2 = re.sub(limit_pattern, "", pred)
        return query1, query2
    return gold, pred


def disprove_queries(schema, gold, pred, dialect, **kwargs):
    """combines formal verification and test-case-based approaches for query equivalence evaluation. When verify_first=True, ParSEval prioritizes formal verification when checking query pairs, while
    still leveraging test-case-based evaluation when needed."""
    workspace = kwargs.get("workspace", os.getcwd())
    max_iter = kwargs.pop("max_iter", 30)
    threshold = kwargs.pop("threshold", 1)
    gold, pred = remove_limit(gold, pred)

    for label, query in zip(["gold", "pred"], [gold, pred]):
        dbgenerate(ddls=schema, query=query, workspace=workspace, dialect=dialect, **kwargs)
        result = compare_queries(
            host_or_path=workspace,
            database=f"{label}.sqlite",
            gold=gold,
            pred=pred,
            dialect=dialect,
            **kwargs,
        )
        if result["state"] == "NEQ":
            return result

    return {"state": "UNKNOWN"}
