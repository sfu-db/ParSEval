
# import os, sys
# PROJECT_ROOT_PATH = os.path.join(os.path.dirname(__file__), "../")
# sys.path.append(PROJECT_ROOT_PATH)

import argparse, os
import pandas as pd
import datetime
from core.log import initialize_logger
from parseval.runner import runner

PROJECT_DIR = os.getcwd()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ParSEval: Plan-aware test database generation for query equivalence")
    parser.add_argument('--dataset', type=str, help="dataset name", default= 'bird_dail2')
    parser.add_argument('--manner', type=str, help="online / offline", default= 'online')
    parser.add_argument('--strategy', type=str, help="strategy to generate databases", default= '1+0', choices=['1+0', '1+1', '1+n', 'complete'])
    parser.add_argument('--dialect', type=str,  help="Query Dialect", default='sqlite')

    parser.add_argument('--start', type=int,  help="Query Dialect", default=1)
    parser.add_argument('--end', type=int,  help="Query Dialect", default=1000)
    parser.add_argument('--debug', type= bool,  help="Print debug ", default= False)
    args = parser.parse_args()
    dataset = args.dataset
    dialect = args.dialect

    start = args.start
    end = args.end
   
    prefix = f'{dataset}_{args.strategy}_{start}_{end}'
    initialize_logger(prefix= prefix)
    dataset_fp = f"datasets/{dataset}.jsonlines"

    runner.checker(dataset_fp, dataset_name= 'literature', workspace=".results/20241016")

