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


def _output_values(output):
    table = next(iter(output.tables.values()))
    return [
        tuple(symbol.concrete for _column, symbol in row.items())
        for row in table.rows
    ]


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

    def test_tuple_in_filter_uses_full_projected_inner_tuple(self):
        """Tuple IN should compare the full projected tuple from the subquery."""
        schema = """
        CREATE TABLE points (id INT, lat INT, lon INT);
        CREATE TABLE allowed (id INT, lat INT, lon INT);
        """
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        instance.create_row("points", values={"id": 1, "lat": 10, "lon": 20})
        instance.create_row("points", values={"id": 2, "lat": 10, "lon": 99})
        instance.create_row("allowed", values={"id": 1, "lat": 10, "lon": 20})
        instance.create_row("allowed", values={"id": 2, "lat": 10, "lon": 30})

        sql = """
        SELECT id
        FROM points
        WHERE (lat, lon) IN (SELECT lat, lon FROM allowed)
        """
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        output = PlanEvaluator(plan, instance, "sqlite").evaluate_context()

        self.assertEqual(_output_values(output), [(1,)])

    def test_not_in_with_inner_null_filters_outer_row(self):
        """NOT IN should follow SQL three-valued logic when inner rows contain NULL."""
        schema = """
        CREATE TABLE outer_values (id INT, value INT);
        CREATE TABLE inner_values (id INT, value INT);
        """
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        instance.create_row("outer_values", values={"id": 1, "value": 7})
        instance.create_row("inner_values", values={"id": 1, "value": None})

        sql = """
        SELECT id
        FROM outer_values
        WHERE value NOT IN (SELECT value FROM inner_values)
        """
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        output = PlanEvaluator(plan, instance, "sqlite").evaluate_context()

        self.assertEqual(_output_values(output), [])


class TestSetOperationEvaluation(unittest.TestCase):
    def test_union_distinct_materializes_single_output_schema(self):
        schema = """
        CREATE TABLE left_values (id INT, value INT);
        CREATE TABLE right_values (id INT, value INT);
        """
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        instance.create_row("left_values", values={"id": 1, "value": 1})
        instance.create_row("left_values", values={"id": 2, "value": 2})
        instance.create_row("right_values", values={"id": 1, "value": 2})
        instance.create_row("right_values", values={"id": 2, "value": 3})

        sql = """
        SELECT value FROM left_values
        UNION
        SELECT value FROM right_values
        """
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        output = PlanEvaluator(plan, instance, "sqlite").evaluate_context()

        self.assertEqual(len(output.tables), 1)
        self.assertEqual(_output_values(output), [(1,), (2,), (3,)])

    def test_union_all_preserves_duplicates(self):
        schema = """
        CREATE TABLE left_values (id INT, value INT);
        CREATE TABLE right_values (id INT, value INT);
        """
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        instance.create_row("left_values", values={"id": 1, "value": 2})
        instance.create_row("right_values", values={"id": 1, "value": 2})

        sql = """
        SELECT value FROM left_values
        UNION ALL
        SELECT value FROM right_values
        """
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        output = PlanEvaluator(plan, instance, "sqlite").evaluate_context()

        self.assertEqual(_output_values(output), [(2,), (2,)])

    def test_intersect_keeps_values_present_on_both_sides(self):
        schema = """
        CREATE TABLE left_values (id INT, value INT);
        CREATE TABLE right_values (id INT, value INT);
        """
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        instance.create_row("left_values", values={"id": 1, "value": 1})
        instance.create_row("left_values", values={"id": 2, "value": 2})
        instance.create_row("right_values", values={"id": 1, "value": 2})
        instance.create_row("right_values", values={"id": 2, "value": 3})

        sql = """
        SELECT value FROM left_values
        INTERSECT
        SELECT value FROM right_values
        """
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        output = PlanEvaluator(plan, instance, "sqlite").evaluate_context()

        self.assertEqual(_output_values(output), [(2,)])

    def test_except_removes_values_present_on_right_side(self):
        schema = """
        CREATE TABLE left_values (id INT, value INT);
        CREATE TABLE right_values (id INT, value INT);
        """
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        instance.create_row("left_values", values={"id": 1, "value": 1})
        instance.create_row("left_values", values={"id": 2, "value": 2})
        instance.create_row("right_values", values={"id": 1, "value": 2})

        sql = """
        SELECT value FROM left_values
        EXCEPT
        SELECT value FROM right_values
        """
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        output = PlanEvaluator(plan, instance, "sqlite").evaluate_context()

        self.assertEqual(_output_values(output), [(1,)])


class TestCaseProjectionEvaluation(unittest.TestCase):
    def test_case_projection_can_feed_join_key(self):
        schema = """
        CREATE TABLE friends (id INT, user1 INT, user2 INT);
        CREATE TABLE likes (id INT, user_id INT, page_id INT);
        """
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        instance.create_row("friends", values={"id": 1, "user1": 1, "user2": 2})
        instance.create_row("likes", values={"id": 1, "user_id": 2, "page_id": 99})

        sql = """
        SELECT liked.page_id
        FROM (
          SELECT CASE WHEN user1 = 1 THEN user2 ELSE user1 END AS friend_id
          FROM friends
        ) AS friend_edges
        JOIN likes AS liked ON friend_edges.friend_id = liked.user_id
        """
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        output = PlanEvaluator(plan, instance, "sqlite").evaluate_context()

        self.assertEqual(_output_values(output), [(99,)])


class TestCTEEvaluation(unittest.TestCase):
    def test_cte_reference_feeds_derived_aggregate_join(self):
        schema = """
        CREATE TABLE lapTimes (
          raceId INT,
          driverId INT,
          lap INT,
          time_in_seconds REAL
        );
        CREATE TABLE drivers (
          driverId INT PRIMARY KEY,
          forename TEXT,
          surname TEXT
        );
        """
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        instance.create_row(
            "lapTimes",
            values={
                "raceId": 1,
                "driverId": 7,
                "lap": 1,
                "time_in_seconds": 91.5,
            },
        )
        instance.create_row(
            "drivers",
            values={"driverId": 7, "forename": "Ada", "surname": "Lovelace"},
        )
        sql = """
        WITH lap_times_in_seconds AS (
          SELECT driverId, time_in_seconds
          FROM lapTimes
        )
        SELECT T2.forename, T2.surname
        FROM (
          SELECT driverId, MIN(time_in_seconds) AS min_time_in_seconds
          FROM lap_times_in_seconds
          GROUP BY driverId
        ) AS T1
        INNER JOIN drivers AS T2 ON T1.driverId = T2.driverId
        ORDER BY T1.min_time_in_seconds ASC
        LIMIT 1
        """
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        evaluator = PlanEvaluator(plan, instance, "sqlite")

        output = evaluator.evaluate_context()
        table = next(iter(output.tables.values()))

        self.assertEqual(len(table.rows), 1)


if __name__ == "__main__":
    unittest.main()
