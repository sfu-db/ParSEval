"""Tests for SubPlan evaluation in PlanEvaluator."""

from __future__ import annotations

import unittest

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic import BranchTree, BranchType, CoverageThresholds, PlanEvaluator


SCHEMA = """
CREATE TABLE t1 (id INT, x INT);
CREATE TABLE t2 (id INT, x INT, y INT);
"""


class TestExistsEvaluation(unittest.TestCase):
    def test_exists_records_true_when_inner_has_rows(self):
        """EXISTS should record EXISTS_TRUE when inner query returns rows."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t2", values={"x": 1, "y": 10})
        instance.create_row("t1", values={"x": 1})

        sql = "SELECT * FROM t1 WHERE EXISTS (SELECT * FROM t2 WHERE t2.x = 1)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(exists_true=1, exists_false=1))

        tree = evaluator.evaluate(tree)

        exists_nodes = [n for n in tree.nodes if n.site == "exists"]
        self.assertTrue(len(exists_nodes) > 0, "No EXISTS branch node found")

        exists_node = exists_nodes[0]
        outcomes = exists_node.observed_outcomes(0)
        self.assertIn(BranchType.EXISTS_TRUE, outcomes)

    def test_exists_records_false_when_inner_empty(self):
        """EXISTS should record EXISTS_FALSE when inner query returns no rows."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        # Don't insert any rows into t2, so EXISTS returns false

        sql = "SELECT * FROM t1 WHERE EXISTS (SELECT * FROM t2 WHERE t2.x = 999)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(exists_true=1, exists_false=1))

        instance.create_row("t1", values={"x": 1})

        tree = evaluator.evaluate(tree)

        exists_nodes = [n for n in tree.nodes if n.site == "exists"]
        self.assertTrue(len(exists_nodes) > 0, "No EXISTS branch node found")

        exists_node = exists_nodes[0]
        outcomes = exists_node.observed_outcomes(0)
        self.assertIn(BranchType.EXISTS_FALSE, outcomes)


class TestInEvaluation(unittest.TestCase):
    def test_in_records_match_when_value_in_set(self):
        """IN should record IN_MATCH when outer value is in inner result set."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t2", values={"x": 1, "y": 10})
        instance.create_row("t2", values={"x": 2, "y": 20})
        instance.create_row("t1", values={"x": 1})  # x=1 is in t2.x

        sql = "SELECT * FROM t1 WHERE t1.x IN (SELECT t2.x FROM t2)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(in_match=1, in_no_match=1))

        tree = evaluator.evaluate(tree)

        in_nodes = [n for n in tree.nodes if n.site == "in"]
        self.assertTrue(len(in_nodes) > 0, "No IN branch node found")

        in_node = in_nodes[0]
        outcomes = in_node.observed_outcomes(0)
        self.assertIn(BranchType.IN_MATCH, outcomes)

    def test_in_records_no_match_when_value_not_in_set(self):
        """IN should record IN_NO_MATCH when outer value is not in inner result set."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t2", values={"x": 1, "y": 10})
        instance.create_row("t1", values={"x": 999})  # x=999 not in t2.x

        sql = "SELECT * FROM t1 WHERE t1.x IN (SELECT t2.x FROM t2)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(in_match=1, in_no_match=1))

        tree = evaluator.evaluate(tree)

        in_nodes = [n for n in tree.nodes if n.site == "in"]
        self.assertTrue(len(in_nodes) > 0, "No IN branch node found")

        in_node = in_nodes[0]
        outcomes = in_node.observed_outcomes(0)
        self.assertIn(BranchType.IN_NO_MATCH, outcomes)


if __name__ == "__main__":
    unittest.main()
