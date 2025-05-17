import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from src.expression.query.parser import QParser
import unittest

class TestQuery(unittest.TestCase):
    def test_planner(self):
        schema = """CREATE TABLE IF NOT EXISTS `frpm` (`CDSCode` TEXT, `Academic Year` TEXT, `County Code` TEXT, `District Code` INT, `Free Meal Count (K-12)` FLOAT);
CREATE TABLE IF NOT EXISTS `satscores` (`cds` TEXT, `rtype` TEXT, `sname` TEXT, `dname` TEXT, `cname` TEXT, `NumGE1500` INT, PRIMARY KEY (`cds`));
CREATE TABLE IF NOT EXISTS `schools` (`CDSCode` TEXT, `NCESDist` TEXT, `NCESSchool` TEXT, `StatusType` TEXT, PRIMARY KEY (`CDSCode`))"""

        sql = """SELECT T1.`Academic Year` FROM frpm AS T1 INNER  JOIN satscores AS T2 on T1.CDSCode = T2.cds where T1.`District Code` > 15 and T1.`Academic Year` = '2023'""" 
        
        qparser = QParser()

        plan = qparser.explain(sql, schema)
        print(plan)



        
    
if __name__ == '__main__':
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)