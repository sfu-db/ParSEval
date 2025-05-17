

import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from src.corekit import get_ctx, rm_folder, reset_folder, rm_folder
from sqlglot import exp
import unittest
from sqlglot import parse
import logging
logger = logging.getLogger('src.test')
schema = """CREATE TABLE IF NOT EXISTS `frpm` (`CDSCode` TEXT, `Academic Year` TEXT NOT NULL, `County Code` TEXT, `District Code` INT, `Free Meal Count (K-12)` FLOAT);
CREATE TABLE IF NOT EXISTS `satscores` (`cds` TEXT, `rtype` TEXT, `sname` TEXT, `dname` TEXT, `cname` TEXT, `NumGE1500` INT, PRIMARY KEY (`cds`));
CREATE TABLE IF NOT EXISTS `schools` (`CDSCode` TEXT, `NCESDist` TEXT, `NCESSchool` TEXT, `StatusType` TEXT, PRIMARY KEY (`CDSCode`))"""

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T2.`NumGE1500` is NOT NULL and T1.`District Code` > 15 """ 

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15  """ 

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 INNER JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15 """ 

sql = """SELECT  T1.`CDSCode`  FROM frpm AS T1  where T1.`Academic Year` <> '2023' or T1.`District Code` > 15 GROUP BY T1.`CDSCode`""" 

# ORDER BY T2.`NumGE1500`
# sql = """SELECT T1.`Academic Year`, T1.CDSCode FROM frpm AS T1 where T1.`District Code` > 15 or T1.`Academic Year` <> '2023'  """ 
# sql = """SELECT T1.`District Code` FROM frpm AS T1 where T1.`CDSCode` is NULL  """ 
# where T1.`District Code` > 15 and T1.`Academic Year` <> '2023'
#   where T1.`District Code` > 15 and T1.`Academic Year` <> '2023'
# sql = """SELECT T1.`Academic Year`, T1.CDSCode FROM frpm AS T1 where T1.`District Code` > 15 or T1.`Academic Year` <> '2023'  """ 
# sql = """SELECT T1.`Academic Year` FROM frpm AS T1 where T1.`District Code` > 15 and T1.`Academic Year` = '2023'   """ 
#  or (T1.`District Code` < 5  and T1.`Academic Year` = '2023') 
# or (T1.`District Code` < 5  and T1.`Academic Year` = '2023') 

#   T1.`District Code` + T1.`Free Meal Count (K-12)` > 1000 
#  and T1.`Academic Year` > '2023' or T1.CDSCode = '123456'
# def test_single_query(query, schema, workspace):
#     generator = UExprGenerator(workspace= workspace, schema= schema, query= query, initial_values= {})
#     print(str(generator.plan))
#     generator._one_execution()
# import z3

# a = z3.SeqSort(z3.IntSort())

logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format='[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s',
)


# print(a > z3.CharVal('2023'))

class TestGenerator(unittest.TestCase):
    def test_executor(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators

        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.generator import Generator

        generator = Generator('tests/db', schema, sql, name = 'test')
        print(generator.plan)
        
        result =generator.get_coverage(None)

        # if result:
        #     generator.instance.to_

        

        print(result)
if __name__ == '__main__':
    reset_folder('tests/db')
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)


      