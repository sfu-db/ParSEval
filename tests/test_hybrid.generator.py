
import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)
from src.corekit import get_ctx, rm_folder, reset_folder

from src.runtime.hybrid.executeor import HybridExecutor
from src.runtime.hybrid.generator import UExprGenerator
from sqlglot import exp, parse_one
import logging, time
import pandas as pd

logger = logging.getLogger('src.test.naive')


def test_generator(dataset_fp, index, workspace):
    df = pd.read_json(dataset_fp, lines= True)
    for idx, row in df.iterrows():
        qidx = idx + 1
        if qidx != index:
            continue
        gold = row['pair'][0]
        print(gold)
        # gold = "SELECT T1.CDSCode FROM frpm AS T1 WHERE T1.`District Name` = 'Fresno County Office of Education' OR T1.`Charter School (Y/N)` != 1"
        # gold = "SELECT max(T1.`District Code`) FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode WHERE T1.`District Name` = 'Fresno County Office of Education' AND T1.`Charter School (Y/N)` = 1 GROUP BY T1.`District Name`"
        
        ddl = row['ddl']
        name = row.get('name') if 'name' in row else row.get('benchmark', f'q{qidx}')
        tpath = os.path.join(workspace, f'{name}_{qidx}')
        reset_folder(tpath)
        generator = UExprGenerator(workspace= tpath, schema= ddl, query= gold, initial_values= {})
        print(generator.plan.root)
        generator._one_execution()
        
        # r = generator._one_execution( max_tries= 2)

if __name__ == '__main__':
    get_ctx(log_level = 'INFO')
    workspace = 'tests/db'
    reset_folder(workspace)
    dataset_fp = "datasets/bird/bird_dail2.jsonlines"
    # for i in range(92, 220):
    #     test_generator(dataset_fp= dataset_fp, index= i, workspace= workspace)
    test_generator(dataset_fp= dataset_fp, index= 3, workspace= workspace)
    # test_instance(workspace)
    rm_folder(get_ctx().result_path)
