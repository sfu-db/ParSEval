import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)


import unittest

import logging
from src.parseval.symbol import *

logger = logging.getLogger("src.test")

logging.basicConfig(
    level=logging.INFO,  # or DEBUG, WARNING, ERROR, etc.
    format="[%(asctime)s] %(levelname)s [%(filename)s:%(lineno)d]: %(message)s",
)


class TestSymbol(unittest.TestCase):

    def test_variable(self):
        name = Variable("name", "string")
        age = Variable("age", "int")
        salary = Variable("salary", "float")
        is_active = Variable("is_active", "bool")
        join_date = Variable("join_date", "date")
        logging.info(str(name))
        self.assertEqual(str(name), "Variable(name, string)")
        self.assertEqual(str(age), "Variable(age, int)")
        self.assertEqual(str(salary), "Variable(salary, float)")
        self.assertEqual(str(is_active), "Variable(is_active, bool)")
        self.assertEqual(str(join_date), "Variable(join_date, date)")

    def test_condition(self):
        name = Variable("name", "string")
        age = Variable("age", dtype="int", concrete=25)
        salary = Variable("salary", "float")
        is_active = Variable("is_active", "bool")
        join_date = Variable("join_date", "date")
        cond1 = age > 35

        self.assertEqual(cond1.concrete, False)
        cond2 = age + 10
        self.assertEqual(cond2.concrete, 35)

        floordiv = age / cond2

        logger.info(f"age / cond2 : {age / cond2}, {floordiv.concrete}")

        gt_cond = floordiv > 0.3

        logger.info(f"{floordiv > 0.3}, {gt_cond.concrete}")

        group = Group("g1", name, age, salary)

        logger.info(group.name)

        logger.info(group[2])

        for item in group:
            logger.info(f"Group item: {item}, concrete: {item.concrete}")


if __name__ == "__main__":
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)
