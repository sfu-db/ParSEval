
import pandas as pd

from src.runtime.naive.generator import UExprGenerator
from src.corekit import get_ctx, rm_folder, reset_folder
from func_timeout import func_timeout, FunctionTimedOut, func_set_timeout

import logging, os, time, json
    # dataset_fp = "/home/chunyu/Projects/Dockers/autotest/.datasets/benchmarks2/bird_dail2.jsonlines"

logger = logging.getLogger('src.run')

import gc

def data_generate(dataset_fp, workspace, dialect = 'sqlite'):
    # df = load_data(dataset_fp, '')
    df = pd.read_json(dataset_fp, lines= True)
    for idx, row in df.iterrows():
        qidx = idx + 1
        # if qidx < 875:
        #     continue
        gold = row['pair'][0]
        ddl = row['ddl']
        name = row.get('name') if 'name' in row else row.get('benchmark', f'q{qidx}')
        tpath = os.path.join(workspace, f'{name}_{qidx}')
        reset_folder(tpath)
        state = 'UNSUPPORT'
        start_time, err = time.perf_counter(), ''
        try:
            generator = UExprGenerator(workspace= tpath, schema= ddl, query= gold, initial_values= {}, dialect= dialect, question_id = qidx, db_id = name)
            # generator.generate(4)
            func_timeout(360, generator.generate, kwargs={'max_iterations' : 5})
            state = 'SUCCESS'
        except FunctionTimedOut:
            err = 'timeout'
            state = 'TIMEOUT'
        except Exception as e:
            err = str(e)
            state = 'ERROR'
        finally:
            logger.info({ "qidx": qidx, 'db_id': name, 'state': state, 'error': err, 'data_gen': time.perf_counter() - start_time}, extra = {'to': 'metric'})

            gc.collect()  # Explicitly runs garbage collection
