from __future__ import annotations
from sqlalchemy import create_engine, text
from threading import Lock

import sys
import pandas as pd
from func_timeout import func_timeout, FunctionTimedOut
from core.db.eq import result_eq
from typing import List, Tuple
import numpy as np
import re
from collections import OrderedDict


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

class SingletonMeta(type):
    _instances = OrderedDict()
    _lock: Lock = Lock()
    def __call__(cls, *args, **kwargs):
        if args:
            connection_string = args[0]
        else:
            connection_string = kwargs.get('connection_string')
        
        with cls._lock:   
            if connection_string not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[connection_string] = instance

                if len(cls._instances) > 10:
                    rmd = list(cls._instances.keys())[4]
                    del cls._instances[rmd]

        return cls._instances[connection_string]

class DDLError(Exception):
    def __init__(self, message = 'Init DB Exception ') -> None:
        self.message = message
        super().__init__(self.message)

# metaclass = SingletonMeta
class Connection():
    def __init__(self, connection_string, pool_size=2, max_overflow=10, pool_timeout=30, pool_recycle=1800):
        self.engine = create_engine(
            connection_string,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle
        )
    def get_conn(self):
        return self.engine.connect()
    def close_conn(self, conn):
        conn.close()
    def execute_raw_sql(self, sql, params=None):
        with self.engine.connect() as connection:
            result = connection.execute(text(sql), params or {})
            return result.fetchall()

    def get_query_results(self, ddls, statements, sql):
        result = []
        with self.engine.connect() as connection:
            trans = connection.begin()
            try:            
                for ddl in ddls:
                    connection.execute(text(ddl), None)
                for stmt in statements:
                    connection.execute(text(stmt), None)
            except Exception as e:
                trans.rollback()
                raise DDLError(str(e))
            try:
                r = connection.execute(text(sql), None)
                result = r.fetchall()
            except Exception as e:
                raise
            finally:
                trans.rollback()
                return result

    def get_execution_results(self, ddls, gold, pred):
        connection = self.get_conn()
        gold_result = []
        pred_result = []
        try:
            trans = connection.begin()
            for ddl in ddls:
                ddl = ddl.replace('TEXT', 'VARCHAR(255)')
                connection.execute(text(ddl), None)
            try:
                r = connection.execute(text(gold), None)
                gold_result = r.fetchall()
            except Exception as e:
                ...
            try:
                r = connection.execute(text(pred), None)
                pred_result = r.fetchall()
            except Exception as e:
                ...
        except Exception as e:
            raise 
        finally:
            trans.rollback()
            connection.close()
            return gold_result, pred_result

    def execute_ddl(self, sql, params = None):
        with self.engine.connect() as conn:
            q = text(sql)
            res = conn.execute(q, params)
            conn.commit()
            return res
            

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

def compare_sql(connection_string, ddl, statements, predicted_sql, ground_truth, relax_eq = False):
    conn = Connection(connection_string= connection_string)
    message = {}
    predicted_res = []
    ground_truth_res = []    
    ground_truth, predicted_sql = remove_limit(ground_truth, predicted_sql)
    try:
        predicted_res = conn.get_query_results(ddl, statements, sql = predicted_sql)
    except DDLError as e:
        message['state'] = 'UNKNOWN'
        message['error'] = [str(e)]
    
    try:
        ground_truth_res = conn.get_query_results(ddl, statements, sql = ground_truth)
    except DDLError as e:
        message['state'] = 'UNKNOWN'
        message['error'] = [str(e)]

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


def execute_sql(connection_string, predicted_sql, ground_truth, relax_eq = False):
    conn = Connection(connection_string= connection_string)
    message = {}
    predicted_res = None
    ground_truth_res = None    
    ground_truth, predicted_sql = remove_limit(ground_truth, predicted_sql)

    try:
        predicted_res = conn.execute_raw_sql(predicted_sql)
    except Exception as e:
        message['pred'] = f"Pred execution Error, {e}"
        message['state'] = 'NEQ'
        return message
    try:
        ground_truth_res = conn.execute_raw_sql(ground_truth)
    except Exception as e:
        message['gold'] = f"Gold execution Error, {e}"
        message['state'] = 'UNKNOWN'
        return  message
    if not ground_truth_res and predicted_res:
        message['msg'] = 'Gold NULL VS Pred NOT NULL'
    eq = result_eq(list(ground_truth_res), list(predicted_res), order_matters = not relax_eq)
    if eq == True:
        message['state'] = 'EQ'
    elif eq == False:
        message['state'] = 'NEQ'
    else:
        message['state'] = 'UNKNOWN'
    return message

def execute_model(connection_string, predicted_sql, ground_truth, idx, relax_eq, meta_time_out):
    message = {'state': None, 'qidx': idx}
    res = 0
    try:
        msg = func_timeout(meta_time_out, execute_sql,
                                  args=(connection_string, predicted_sql, ground_truth, relax_eq))
        message.update(msg)
    except KeyboardInterrupt:
        sys.exit(0)
    except FunctionTimedOut:
        message['state'] = 'TIMEOUT'
    except Exception as e:
        msg = f'error: {e}'
        message['error'] = f"execution error: {e}"
    finally:
        return message

def run_sqls(connection_string, sql_pair, idx, meta_time_out = 30.0, relax_eq = False):
    ground_truth, predicted_sql = sql_pair
    return execute_model(connection_string = connection_string, predicted_sql= predicted_sql, ground_truth= ground_truth,  idx= idx, relax_eq= relax_eq, meta_time_out= meta_time_out)
