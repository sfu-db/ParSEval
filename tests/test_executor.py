

import sys
import os
# Get the current directory (where your_script.py resides)
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from collections import defaultdict

import unittest
from sqlglot import parse_one, exp
schema = """CREATE TABLE IF NOT EXISTS `frpm` (`CDSCode` TEXT, `Academic Year` TEXT, `County Code` TEXT, `District Code` INT, `Free Meal Count (K-12)` FLOAT, FOREIGN KEY (`CDSCode`) REFERENCES `schools`(`CDSCode`));
CREATE TABLE IF NOT EXISTS `satscores` (`cds` TEXT, `rtype` TEXT, `sname` TEXT, `dname` TEXT, `cname` TEXT, `NumGE1500` INT, PRIMARY KEY (`cds`));
CREATE TABLE IF NOT EXISTS `schools` (`CDSCode` TEXT, `NCESDist` TEXT, `NCESSchool` TEXT, `StatusType` TEXT, PRIMARY KEY (`CDSCode`))"""

sql = """SELECT T2.NCESDist FROM frpm AS T1 INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode where T1.CDSCode = 'cds1'"""



class TestExecutor(unittest.TestCase):
    constraints = defaultdict(list)

    def add_constraint(self, constraint, identity, label):
        self.constraints[label].append(constraint)
        

    def test_executor(self):
        from src.instance.generators import ValueGeneratorRegistry, register_default_generators

        register_default_generators()
        from src.instance.instance import Instance
        from src.runtime.executor import Executor
        from src.runtime.uexpr_to_constraint import UExprToConstraint
        from src.expression.query import rel, parser

        path =UExprToConstraint(lambda constraint, identity, label: self.add_constraint(constraint, identity, label))

        executor = Executor(lambda constraint, identity, label: self.add_constraint(constraint, identity, label))

        parser = parser.QParser()
        plan = parser.explain(sql, schema)

        print(plan)
        # executor = Executor(path)

        instance = Instance.create(schema = schema, name = 'test', dialect = 'sqlite')

        for _ in range(3):
            instance.create_row('frpm', {})
        result = executor(plan, instance)

        print(result)
        
        # executor()

if __name__ == '__main__':
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
        