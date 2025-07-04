from __future__ import annotations
from typing import List, Tuple
from src.corekit import DBManager
import pandas as pd
import numpy as np
import re
from src.exceptions import SchemaError
def compare_df(result1: List[Tuple], result2: List[Tuple], order_matters: bool) -> bool:

    df_gold = pd.DataFrame.from_records(result1)
    df_pred = pd.DataFrame.from_records(result2)
    df_pred.fillna(-99999, inplace=True)
    df_gold.fillna(-99999, inplace=True)

    df_gold_sorted = df_gold.sort_index(axis=1)
    df_pred_sorted = df_pred.sort_index(axis=1)

    if df_gold.empty and df_pred.empty:
        return -1
    
    if df_gold.shape != df_pred.shape:
        return 0
    # Compare the values ignoring column names using numpy
    are_values_equal = np.array_equal(df_gold_sorted.values, df_pred_sorted.values)

    if are_values_equal:
        return 1
    
    return 0

limit_pattern = re.compile(r'LIMIT\s+\d+\b(?:\s+OFFSET\s+\d+)?$', re.IGNORECASE)  #LIMIT\s+(\d+)(?:\s*,\s*(\d+))?\s*$
orderby_pattern = re.compile(r'ORDER\s+BY\s+.*[^\)]$', re.IGNORECASE)  ##ORDER\s+BY\s+([^,\s]+)\s*(ASC|DESC)?\s*$

def remove_limit(gold, pred):
    gold_limit_match = limit_pattern.search(gold)
    pred_limit_match = limit_pattern.search(pred)
    gold_limit = gold_limit_match.group(0) if gold_limit_match else None
    pred_limit = pred_limit_match.group(0) if pred_limit_match else None
    if gold_limit == pred_limit:
        query1 = re.sub(limit_pattern, '', gold)
        query2 = re.sub(limit_pattern, '', pred)
        return query1, query2
    return gold , pred

def compare_sql(host_or_path, predicted_sql, ground_truth, relax_eq = False):
    with DBManager().get_connection(host_or_path) as conn:    
        message = {}
        predicted_res = []
        ground_truth_res = []    
        ground_truth, predicted_sql = remove_limit(ground_truth, predicted_sql)
        predicted_res = conn.execute(predicted_sql)
        ground_truth_res = conn.execute(ground_truth)
        if not ground_truth_res and predicted_res:
            message['msg'] = 'Gold NULL VS Pred NOT NULL'

        eq = compare_df(list(ground_truth_res), list(predicted_res), order_matters = not relax_eq)
        if eq == 1:
            message['state'] = 'EQ'
        elif eq == 0:
            message['state'] = 'NEQ'
        else:
            message['state'] = 'UNKNOWN'
        return message