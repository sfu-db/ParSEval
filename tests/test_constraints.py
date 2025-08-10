

import sys
import os
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.abspath(os.path.join(current_dir, os.pardir))
sys.path.append(parent_dir)

from src.corekit import get_ctx, rm_folder, rm_folder
import unittest, logging
from sqlglot import parse, parse_one

from collections import deque
from src.runtime.branch import Branch
from src.runtime.constraint import Constraint
from src.expression.symbol import to_variable
logger = logging.getLogger('src.test')



class TestConstraints(unittest.TestCase):
    def setUp(self):
        get_ctx()
        self.leaves = {}
        logger.info(f'Setting up test environment for constraints')
    def tearDown(self):
        logger.info(f'Remove the test logs and results folder')
        rm_folder(get_ctx().result_path)

    def test_branch(self):
        root_branch = Branch.from_value('root', None, None)
        plausible_branch = Branch.from_value('plausible', None, None)
        positive_branch = Branch.from_value('positive', None, None)
        negative_branch = Branch.from_value('negative', None, None)
        nullable_branch = Branch.from_value('nullable', None, None)
        multiplicity_branch = Branch.from_value('multiplicity', None, None)
        binary_branch = Branch.from_value('binary', None, None)
        grouping_branch = Branch.from_value('grouping', None, None)

        self.assertIsInstance(root_branch, Branch)
        self.assertIsInstance(plausible_branch, Branch)
        self.assertIsInstance(positive_branch, Branch)
        self.assertIsInstance(negative_branch, Branch)
        self.assertIsInstance(nullable_branch, Branch)
        self.assertIsInstance(multiplicity_branch, Branch)
        self.assertIsInstance(binary_branch, Branch)
        self.assertIsInstance(grouping_branch, Branch)


    def test_constraint_tree(self):
        
        root_constraint = Constraint(self, None, 'ROOT', None)
        a0 = to_variable("int", "a0", 10)
        smt_expr = a0 > 10
        node = root_constraint.add_child("filter", "1", parse_one("a > 10"), smt_expr, branch="positive", taken= True, tuples= [], tbl_exprs= [])

        node = node.add_child("join", "2", parse_one("a == b"), smt_expr, branch="positive", taken= True, tuples= [], tbl_exprs=[])

        node.add_child("project", "3", parse_one("a"), smt_expr, branch="positive", taken= True, tuples= [], tbl_exprs=[])


        from src.runtime.to_dot import display_constraints
        
        dot = display_constraints(root_constraint)
        print(dot)

        # lines = []
        # q = deque([root_constraint])
        # while  q:
        #     node = q.popleft()
        #     lines.append(str(node))
        #     logger.info(node)
        #     if hasattr(node, 'children'):
        #         for bit, child in node.children.items():
        #             q.append(child)
        # print('\n'.join(lines))


if __name__ == '__main__':
    runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
    unittest.main(testRunner=runner, exit=False)


      