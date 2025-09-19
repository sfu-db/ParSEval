

import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from src.corekit import get_ctx, rm_folder, reset_folder, rm_folder, DBManager
from sqlglot import exp
import unittest

import logging
logger = logging.getLogger('src.test')
schema = """CREATE TABLE IF NOT EXISTS `frpm` (`CDSCode` TEXT, `Academic Year` TEXT NOT NULL, `County Code` TEXT, `District Code` INT, `Free Meal Count (K-12)` FLOAT);
CREATE TABLE IF NOT EXISTS `satscores` (`cds` TEXT, `rtype` TEXT, `sname` TEXT, `dname` TEXT, `cname` TEXT, `NumGE1500` INT, PRIMARY KEY (`cds`));
CREATE TABLE IF NOT EXISTS `schools` (`CDSCode` TEXT, `NCESDist` TEXT, `NCESSchool` TEXT, `StatusType` TEXT, PRIMARY KEY (`CDSCode`))"""

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T2.`NumGE1500` is NOT NULL and T1.`District Code` > 15 """ 

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 left JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15  """ 

# sql = """SELECT T1.`District Code`, T2.`NumGE1500`  FROM frpm AS T1 INNER JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`Academic Year` <> '2023' or T1.`District Code` > 15 """ 




logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format='[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s',
)
from src.exprs.plan import Planner
class TestGenerator(unittest.TestCase):
    @unittest.skip("skip covaerage")
    def test_parse_spj(self):
        sql = """SELECT  T1.`CDSCode`  FROM frpm AS T1  where T1.`Academic Year` <> '2023' or T1.`District Code` > 15""" 
        ...


if __name__ == '__main__':
    
    reset_folder('tests/db')
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)


      