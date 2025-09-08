

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Generator, Tuple, List, Dict, Callable
from datetime import datetime
from sqlglot import exp
from collections import defaultdict
from parseval.query import rex, uexpr
from parseval.symbol import NULL
from itertools import chain, product, tee, combinations
from functools import reduce
from copy import deepcopy
from dataclasses import dataclass, asdict, field
from parseval.exceptions import UnSupportError

import traceback, os, pathlib, logging, itertools, time, json, z3
from parseval.exceptions import SchemaError, UserDefineFunctionError, QuerySyntaxError
from parseval.query.qparser import qparse_one
from parseval.query.tracer import UExprGenerator
from parseval.data_generator.generator import Generator
from parseval.query.display import display_plan, display_uexpr
from parseval.query import rex
from func_timeout import func_timeout, FunctionTimedOut, func_set_timeout
from parseval.db.schema import Instance
# from core.db.connection import compare_sql, Connection
from parseval.db.connection import compare_sql

from parseval.runner.q import submit_work, close_workers

from concurrent.futures import as_completed, TimeoutError, wait

logger = logging.getLogger('app')
metrics_logger = logging.getLogger('app.metric')

exec_logger = logging.getLogger('app.qplan')
instance_logger = logging.getLogger('app.instance')
intermediate_logger = logging.getLogger('app.execution')

TIMEOUT = 360
UPPER_BOUND = 300

def clean_workspace(folder_path):
    try:
        files = os.listdir(folder_path)
        for f in files:
            file_path = os.path.join(folder_path, f)
            if os.path.isfile(file_path):
                os.remove(file_path)
    except Exception as e:
        print(f"An error occurred: {e}")

def create_workspace(folder_path):
    try:
        if not os.path.exists(folder_path):
            pathlib.Path(folder_path).mkdir(parents=True, exist_ok=True)
    except FileExistsError:
        ...
import pandas as pd

def load_data(fp, dataset_name) -> pd.DataFrame:
    df = pd.read_json(fp, lines = True)
    return df


def get_all_paths(values, strategy = 'complete'):
    all_paths = {'positive_only' : []}
    for r in range(1, len(values) + 1):
        permutations = itertools.combinations(values, r)
        for paths in permutations:
            coverage_id = '#'.join(path._id for path in paths)
            all_paths[coverage_id] = [path.expr for path in paths]
    return all_paths


@func_set_timeout(timeout= int(TIMEOUT))
def data_generation(query, ddl, name, workspace, dialect = 'sqlite', ctx = None):
    print(f'start to execute q{name} with ctx {ctx}')
    instance = Instance.from_ddl(sql = ddl, name = name, dialect= dialect, ctx= ctx )
    root = qparse_one(schema = instance.to_ddl('mysql'), query = query)
    size = 3
    logs = {'query': query}
    flag = True
    covered = {}
    while size < UPPER_BOUND and flag:
        instance.seeds(size = size)
        ugenerator = UExprGenerator(plan = root, instance = instance)
        negative_paths = get_all_paths(ugenerator.negative)
        for tidx, branch in enumerate(ugenerator.positive):
            logs['positive_branch'] = [display_uexpr(branch.expr)]
            generator = Generator(ctx = instance.ctx)
            smt_expr = generator.from_uexpr(branch.expr)
            paths = [smt_expr >= 2]
            for ctype, constraints in branch.constraints.items():
                for constraint in constraints:            
                    paths.append(generator.from_uexpr(constraint))
                    logs['positive_branch'].append(display_uexpr(constraint))
            state = len(paths)
            for negative_path_id, negative_paths in negative_paths.items():
                tpath = os.path.join(workspace, f'{name}_p{tidx}_{negative_path_id}.db')
                connection_str= f"sqlite:///{tpath}"
                if connection_str in covered:
                    flag = False
                    continue
                flag = True
                start_time = time.perf_counter()
                for p in negative_paths:
                    paths.append(generator.from_uexpr(p))
                    logs['negative_branch'] = [display_uexpr(p)]
                assignments = generator.solve(paths)
                if assignments:
                    instance.reset()
                    instance.initialize_instance(assignments)
                    # instance.to_instance(connection_string= connection_str)
                    # covered.add(connection_str)
                    covered[connection_str] = instance.to_insert(dialect = dialect)
                    end_time = time.perf_counter()
                    logs['data_gen'] = end_time - start_time
                    logs['covered'] = f"p{tidx}_{negative_path_id}"
                    logs['tpath'] = connection_str
                    logs['stmt'] = covered[connection_str]
                    intermediate_logger.info(json.dumps(logs))
                paths = paths[:state]
        size += 5
    return covered

import gc
def do_check(gold, pred, ddl, name, qidx, workspace, dialect = 'sqlite', ctx = None):
    ctx = z3.Context()
    
    try:
        tpath = os.path.join(workspace, f'q{qidx}')
        create_workspace(tpath)
        db_paths = data_generation(query = gold, ddl = ddl, name = name, workspace= tpath, dialect = dialect, ctx = ctx)
        for connection_str, statements in db_paths.items():
            start_time = time.perf_counter()     
            msg = compare_sql(connection_str, ddl.split(';'), statements, predicted_sql= pred, ground_truth= gold, relax_eq= False)
            end_time = time.perf_counter()
            metrics_logger.info(json.dumps({"connection_str": connection_str, **msg, "qidx": qidx, "compare_time": end_time - start_time}))
    except QuerySyntaxError as e:
        metrics_logger.info(json.dumps({ "connection_str": '', 'state': 'SYNERR', 'error': str(e), "qidx": qidx, "compare_time": TIMEOUT}))
    except UserDefineFunctionError as func_e:
        metrics_logger.info(json.dumps({ "connection_str": '', 'state': 'UDFERR','error': str(func_e), "qidx": qidx, "compare_time": TIMEOUT}))
        # print(f'UDF ERROR : {idx} -> {func_e}')
    except SchemaError as scm_e:
        metrics_logger.info(json.dumps({ "connection_str": '', 'state': 'SCMERR','error': str(scm_e), "qidx": qidx, "compare_time": TIMEOUT}))
    except UnSupportError as u:
        metrics_logger.info(json.dumps({ "connection_str": '', 'state': 'UNSUPPORT','error': str(u), "qidx": qidx, "compare_time": TIMEOUT}))
    except FunctionTimedOut  as tmo:
        metrics_logger.info(json.dumps({ "connection_str": '', 'state': 'TIMEOUT', 'error': str(tmo), "qidx": qidx, "compare_time": TIMEOUT}))
    except Exception as e:
        metrics_logger.info(json.dumps({ "connection_str": '', 'state': 'UNKNOWN', 'error': str(e), "qidx": qidx, "compare_time": TIMEOUT}))
    finally:
        del ctx
        gc.collect()
    
from concurrent.futures import Future, ThreadPoolExecutor, ProcessPoolExecutor
import z3

def checker(fp, dataset_name, workspace, dialect = 'sqlite'):
    df = load_data(fp, dataset_name = dataset_name)
    futures = []

    with ProcessPoolExecutor(max_workers= 32) as pool:
        for idx, row in df.iterrows():
            qidx = idx + 1
            gold = row['pair'][0]
            pred = row['pair'][1]
            ddl = row['ddl']
            name = row.get('name') if 'name' in row else row.get('benchmark', f'q{qidx}')
            # ctx = z3.Context()
            ctx = None

            future = pool.submit(do_check, gold, pred, ddl, name, qidx, workspace, dialect, ctx)
            # future = submit_work(do_check, gold, pred, ddl, name, qidx, workspace, dialect)
            futures.append(future)

        wait(futures)
        # close_workers()
    # for future in as_completed(futures):
    #     label = futures[future]
    #     statements = []
    #     try:
    #         result = future.result(timeout= int(TIMEOUT))
    #         statements = result.pop('statements')
    #         results.update(result)
    #     except (TimeoutError, FunctionTimedOut)  as tmo:
    #         results[f'{label}_data_gen'] = TIMEOUT
    #     except Exception as e:
    #         results['error'].append(f'{label} : {traceback.format_exc()}')
    #     finally:
    #         schemas[label] =  statements if statements else [instances[label].to_insert(dialect)]


        
        # try:
        #     tpath = os.path.join(workspace, f'q{qidx}')
        #     create_workspace(tpath)
        #     db_paths = data_generation(query = gold, ddl = ddl, name = name, workspace= tpath, dialect = dialect)
        #     for connection_str, statements in db_paths.items():
        #         start_time = time.perf_counter()     
        #         msg = compare_sql(connection_str, ddl.split(';'), statements, predicted_sql= pred, ground_truth= gold, relax_eq= False)
        #         end_time = time.perf_counter()
        #         metrics_logger.info(json.dumps({"pair": row['pair'], "connection_str": connection_str, **msg, "qidx": qidx, "compare_time": end_time - start_time}))
        # except QuerySyntaxError as e:
        #     metrics_logger.info(json.dumps({"pair": row['pair'], "connection_str": '', 'state': 'SYNERR', 'error': str(e), "qidx": qidx, "compare_time": TIMEOUT}))
        # except UserDefineFunctionError as func_e:
        #     metrics_logger.info(json.dumps({"pair": row['pair'], "connection_str": '', 'state': 'UDFERR','error': str(func_e), "qidx": qidx, "compare_time": TIMEOUT}))
        #     # print(f'UDF ERROR : {idx} -> {func_e}')
        # except SchemaError as scm_e:
        #     metrics_logger.info(json.dumps({"pair": row['pair'], "connection_str": '', 'state': 'SCMERR','error': str(scm_e), "qidx": qidx, "compare_time": TIMEOUT}))
        #     # print(f'Schema ERROR : {idx} -> {func_e}')
        # except UnSupportError as u:
        #     metrics_logger.info(json.dumps({"pair": row['pair'], "connection_str": '', 'state': 'UNSUPPORT','error': str(u), "qidx": qidx, "compare_time": TIMEOUT}))
        # except FunctionTimedOut  as tmo:
        #     metrics_logger.info(json.dumps({"pair": row['pair'], "connection_str": '', 'state': 'TIMEOUT', 'error': str(tmo), "qidx": qidx, "compare_time": TIMEOUT}))
        # except Exception as e:
        #     metrics_logger.info(json.dumps({"pair": row['pair'], "connection_str": '', 'state': 'UNKNOWN', 'error': str(e), "qidx": qidx, "compare_time": TIMEOUT}))
        # if idx % 100 == 0:
        #     print(f'has processed {idx}')
        # break
        
