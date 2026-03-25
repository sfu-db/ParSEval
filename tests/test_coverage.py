import os
import sys
import unittest

from sqlglot import exp

from parseval.constants import BranchType, PBit, StepType
from parseval.uexpr.coverage import CoverageCalculator
from parseval.uexpr.uexprs import Constraint, PlausibleBranch, UExprToConstraint


class CoverageCalculatorTests(unittest.TestCase):
    def setUp(self):
        self.tree = UExprToConstraint()
        self.calculator = CoverageCalculator(positive_threshold=1, negative_threshold=1)

    def _make_filter_node(self, sql_condition):
        node = Constraint(
            tree=self.tree,
            parent=self.tree.root,
            scope_id=0,
            step_type=StepType.FILTER,
            step_name="filter",
            sql_condition=sql_condition,
        )
        node.depth = 1
        self.tree.root.children[PBit.TRUE] = node
        return node

    def test_predicate_hits_use_unique_rowids(self):
        predicate = exp.EQ(
            this=exp.column("x", table="t1"), expression=exp.Literal.number(1)
        )
        node = self._make_filter_node(predicate)
        leaf = PlausibleBranch(self.tree, node, BranchType.POSITIVE)
        node.children[PBit.TRUE] = leaf
        self.tree.leaves[leaf.pattern()] = leaf

        node.rowid_index[PBit.TRUE].extend(
            [
                ("row1",),
                ("row1",),
                ("row2",),
            ]
        )

        coverage = self.calculator.evaluate_leaf(leaf)

        self.assertEqual(2, coverage.hits)
        self.assertTrue(coverage.covered)

    def test_equivalent_nodes_share_coverage_by_label(self):
        predicate = exp.EQ(
            this=exp.column("x", table="t1"), expression=exp.Literal.number(1)
        )
        node1 = self._make_filter_node(predicate)
        leaf1 = PlausibleBranch(self.tree, node1, BranchType.POSITIVE)
        node1.children[PBit.TRUE] = leaf1
        self.tree.leaves[leaf1.pattern()] = leaf1
        node1.rowid_index[PBit.TRUE].append(("row1",))

        # A second equivalent node with the same label but attached elsewhere in the tree.
        parent2 = Constraint(
            tree=self.tree,
            parent=node1,
            scope_id=0,
            step_type=StepType.FILTER,
            step_name="filter",
            sql_condition=predicate.copy(),
        )
        parent2.depth = 2
        node1.children[PBit.FALSE] = parent2

        node2 = Constraint(
            tree=self.tree,
            parent=parent2,
            scope_id=0,
            step_type=StepType.FILTER,
            step_name="filter",
            sql_condition=predicate.copy(),
        )
        node2.depth = 3
        parent2.children[PBit.TRUE] = node2

        leaf2 = PlausibleBranch(self.tree, node2, BranchType.POSITIVE)
        node2.children[PBit.TRUE] = leaf2
        self.tree.leaves[leaf2.pattern()] = leaf2
        node2.rowid_index[PBit.TRUE].append(("row2",))

        coverage = self.calculator.evaluate_leaf(leaf2)

        self.assertEqual(2, coverage.hits)
        self.assertTrue(coverage.covered)

    def test_nullable_impossibility_is_reported_structurally(self):
        column = exp.column("pk", table="t1")
        column.set("nullable", False)
        node = self._make_filter_node(column)
        leaf = PlausibleBranch(self.tree, node, BranchType.POSITIVE)
        node.children[PBit.NULL] = leaf
        self.tree.leaves[leaf.pattern()] = leaf

        coverage = self.calculator.evaluate_leaf(leaf)

        self.assertTrue(coverage.infeasible)

    def test_duplicate_on_unique_column_forces_negative_branch(self):
        column = exp.column("pk", table="t1")
        column.set("is_unique", True)
        node = self._make_filter_node(column)
        leaf = PlausibleBranch(self.tree, node, BranchType.POSITIVE)
        node.children[PBit.DUPLICATE] = leaf
        self.tree.leaves[leaf.pattern()] = leaf

        coverage = self.calculator.evaluate_leaf(leaf)

        self.assertTrue(coverage.infeasible)
        self.assertEqual(BranchType.NEGATIVE, coverage.forced_branch)


if __name__ == "__main__":
    unittest.main(verbosity=2)
