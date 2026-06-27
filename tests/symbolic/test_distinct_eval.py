"""Tests for DISTINCT evaluation in PlanEvaluator."""

from __future__ import annotations

import unittest

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic import BranchTree, BranchType, CoverageThresholds, PlanEvaluator


SCHEMA = "CREATE TABLE t (id INT, name TEXT);"


class TestDistinctEvaluation(unittest.TestCase):
    def test_distinct_records_unique_when_all_rows_unique(self):
        """DISTINCT should record DISTINCT_UNIQUE when all projected values are unique."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t", values={"name": "Alice"})
        instance.create_row("t", values={"name": "Bob"})

        sql = "SELECT DISTINCT name FROM t"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(distinct_unique=1, distinct_duplicate=1))

        tree = evaluator.evaluate(tree)

        distinct_nodes = [n for n in tree.nodes if n.site == "distinct"]
        self.assertTrue(len(distinct_nodes) > 0, "No DISTINCT branch node found")

        outcomes = distinct_nodes[0].observed_outcomes(0)
        self.assertIn(BranchType.DISTINCT_UNIQUE, outcomes)

    def test_distinct_records_duplicate_when_duplicates_exist(self):
        """DISTINCT should record DISTINCT_DUPLICATE when projected values have duplicates."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t", values={"name": "Alice"})
        instance.create_row("t", values={"name": "Alice"})  # duplicate

        sql = "SELECT DISTINCT name FROM t"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        evaluator = PlanEvaluator(plan, instance, "sqlite")
        tree = BranchTree(thresholds=CoverageThresholds(distinct_unique=1, distinct_duplicate=1))

        tree = evaluator.evaluate(tree)

        distinct_nodes = [n for n in tree.nodes if n.site == "distinct"]
        self.assertTrue(len(distinct_nodes) > 0, "No DISTINCT branch node found")

        outcomes = distinct_nodes[0].observed_outcomes(0)
        self.assertIn(BranchType.DISTINCT_DUPLICATE, outcomes)


if __name__ == "__main__":
    unittest.main()
