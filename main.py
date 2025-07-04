import argparse, os
from src.corekit import get_ctx
from sqlglot import parse
from src.instance.generators import ValueGeneratorRegistry, register_default_generators
import logging
logger = logging.getLogger('src.test')

PROJECT_DIR = os.getcwd()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ParSEval: Plan-aware test database generation for query equivalence")
    parser.add_argument('--dialect', type=str,  help="Query Dialect", default='sqlite')
    parser.add_argument('--schema', type=str, help="database schema")
    parser.add_argument('--gold', type=str,  help="the gold query")
    parser.add_argument('--pred', type=str,  help="the pred query", default= None)
    parser.add_argument('--offline', type=bool,  default= True)
    parser.add_argument('--maxiter', type=int,  default= 8)


    
    args = parser.parse_args()
    dialect = args.dialect
    maxiter = args.maxiter
    
    ctx = get_ctx(log_level = 'INFO', result_path = 'results/dail')
    register_default_generators()
    from src.runtime.generator import Generator
    schema = args.schema
    if args.offline:
        generator = Generator('tests/db', schema, args.gold, name = 'test_spj')
        result =generator.generate(max_iter= maxiter)