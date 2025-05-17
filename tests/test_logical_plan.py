


import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)
import random, logging, json
from src.corekit import reset_folder

import pandas as pd
from sqlglot import parse_one, exp, parse
from src.uexpr.query_parser import get_logical_plan
from src.exceptions import *

FP = "./datasets/bird/bird_dail2.jsonlines"

def download_plan(dataset_fp):
    df = pd.read_json(dataset_fp, lines = True)
    errors = []
    for idx, row in df.iterrows():
        try:
            db_id = row['benchmark']
            question_id = row['index']
            ddls = [d.sql(dialect = 'mysql') for d in parse(row['ddl'], dialect = 'sqlite')]
            gold = parse_one(row['pair'][0], dialect = 'sqlite').sql()
            pred = parse_one(row['pair'][1], dialect = 'sqlite').sql()

            raw = get_logical_plan(ddl= ddls, queries= [gold], dialect= 'sqlite')
            src = json.loads(raw)[0]
            
            if src['state'] == 'SUCCESS':
                with open(f'datasets/bird/plan/{db_id}_{question_id}_gold.sql', 'w') as fp:
                    json.dump(json.loads(src.get('plan')), fp, indent= 2)
                    # fp.write(';\n\n'.join([*schemas, gold, pred]))
            elif src['state'] == 'SYNTAX_ERROR':
                raise QuerySyntaxError(src['error'])
            elif src['state'] == 'SCHEMA_ERROR':
                raise SchemaError(src['error'])
        except Exception as e:
            errors.append({'question_id': question_id, 'db_id': db_id, 'error': str(e)})
            
    with open('datasets/bird/error.txt', 'w') as ffp:
        json.dump(errors, ffp, indent= 2)
reset_folder('datasets/bird/plan/')
download_plan(FP)