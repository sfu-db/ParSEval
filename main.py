import argparse, os
from src.corekit import get_ctx
from sqlglot import parse
from src.instance.generators import ValueGeneratorRegistry, register_default_generators
from src.runtime.verifier import compare_sql
import logging
logger = logging.getLogger('src.test')

PROJECT_DIR = os.getcwd()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ParSEval: Plan-aware test database generation for query equivalence")
    parser.add_argument('--dialect', type=str,  help="Query Dialect", default='sqlite')
    parser.add_argument('--schema', type=str, help="database schema")
    parser.add_argument('--gold', type=str,  help="the gold query")
    parser.add_argument('--pred', type=str,  help="the pred query", default= None)
    parser.add_argument('--offline', type=bool,  default= False)
    parser.add_argument('--name', type=str,  help="the name of generated instance", default= "test")
    parser.add_argument('--maxiter', type=int,  default= 8)


    
    args = parser.parse_args()
    dialect = args.dialect
    maxiter = args.maxiter
    
    ctx = get_ctx(log_level = 'INFO', result_path = 'results/dail')
    register_default_generators()
    from src.runtime.generator import Generator
    schema = args.schema
    if args.offline:
        generator = Generator('tests/db', schema, args.gold, name = 'test')
        result =generator.generate(max_iter= maxiter)
    else:
        generator = Generator('tests/db', schema, args.gold, name = 'test_gold')
        result1 =generator.generate(max_iter= maxiter)
        result = compare_sql(result1, args.gold, args.pred)
        if result['state'] == 'EQ':
            generator = Generator('tests/db', schema, args.pred, name = 'test_pred')
            result2 =generator.generate(max_iter= maxiter)
            result = compare_sql(result2, args.gold, args.pred)
        






