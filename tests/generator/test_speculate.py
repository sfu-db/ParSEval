"""Tests for speculative data seeding (src/parseval/generator/speculate.py)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from parseval.generator.speculate import speculate
from parseval.instance import Instance


class TestSpeculateSelfJoin(unittest.TestCase):
    """Self-join resolution with different aliases of the same table."""

    def test_self_join_qualified(self):
        ddls = "CREATE TABLE t (id INT, x INT);"
        inst = speculate(
            ddls,
            "SELECT t1.x, t2.x FROM t t1 JOIN t t2 ON t1.id = t2.id",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)

    def test_self_join_with_where(self):
        ddls = "CREATE TABLE t (id INT, val INT);"
        inst = speculate(
            ddls,
            "SELECT t1.val FROM t t1 JOIN t t2 ON t1.id = t2.id WHERE t1.val > t2.val",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)


class TestSpeculateSemiJoin(unittest.TestCase):
    """Semi-join via EXISTS / IN subquery."""

    def test_exists_correlated(self):
        ddls = """CREATE TABLE t (id INT, x INT);
CREATE TABLE u (id INT, t_id INT);"""
        inst = speculate(
            ddls,
            "SELECT t.x FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.t_id = t.id)",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)

    def test_in_correlated(self):
        ddls = """CREATE TABLE t (id INT, x INT);
CREATE TABLE u (id INT, t_id INT);"""
        inst = speculate(
            ddls,
            "SELECT t.x FROM t WHERE t.id IN (SELECT u.t_id FROM u)",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)

    def test_exists_with_extra_where(self):
        ddls = """CREATE TABLE t (id INT, x INT);
CREATE TABLE u (id INT, t_id INT, category TEXT);"""
        inst = speculate(
            ddls,
            "SELECT t.x FROM t WHERE EXISTS "
            "(SELECT 1 FROM u WHERE u.t_id = t.id AND u.category = 'A')",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)

    def test_inner_join_regression(self):
        """Plain INNER JOIN still works."""
        ddls = """CREATE TABLE a (id INT, val INT);
CREATE TABLE b (id INT, a_id INT);"""
        inst = speculate(
            ddls,
            "SELECT a.val FROM a JOIN b ON a.id = b.a_id",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)


class TestSpeculateAntiJoin(unittest.TestCase):
    """Anti-join via NOT EXISTS / NOT IN subquery."""

    def test_not_exists_correlated(self):
        ddls = """CREATE TABLE t (id INT, x INT);
CREATE TABLE u (id INT, t_id INT);"""
        inst = speculate(
            ddls,
            "SELECT t.x FROM t WHERE NOT EXISTS "
            "(SELECT 1 FROM u WHERE u.t_id = t.id)",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)

    def test_not_in_correlated(self):
        ddls = """CREATE TABLE t (id INT, x INT);
CREATE TABLE u (id INT, t_id INT);"""
        inst = speculate(
            ddls,
            "SELECT t.x FROM t WHERE t.id NOT IN (SELECT u.t_id FROM u)",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)


class TestSpeculateEdgeCases(unittest.TestCase):
    """Edge cases around subqueries."""

    def test_no_subquery_fallback(self):
        """Query without subquery still works."""
        inst = speculate(
            "CREATE TABLE t (id INT, x INT);",
            "SELECT t.x FROM t WHERE t.x > 10",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)

    def test_uncorrelated_exists(self):
        """EXISTS on uncorrelated subquery."""
        ddls = """CREATE TABLE t (id INT, x INT);
CREATE TABLE u (id INT);"""
        inst = speculate(
            ddls,
            "SELECT t.x FROM t WHERE EXISTS (SELECT 1 FROM u)",
            "sqlite",
        )
        self.assertIsNotNone(inst)

    def test_multiple_joins(self):
        """Query with multiple regular joins."""
        ddls = """CREATE TABLE a (id INT);
CREATE TABLE b (id INT, a_id INT);
CREATE TABLE c (id INT, b_id INT);"""
        inst = speculate(
            ddls,
            "SELECT a.id FROM a JOIN b ON a.id = b.a_id JOIN c ON b.id = c.b_id",
            "sqlite",
        )
        self.assertIsNotNone(inst)
        total = sum(len(inst.get_rows(t)) for t in inst.schema.fk_safe_table_order())
        self.assertGreater(total, 0)


class TestSpeculateRelationScope(unittest.TestCase):
    def test_cte_alias_is_not_resolved_as_physical_table(self):
        ddls = """
CREATE TABLE activity (
    player_id INT,
    event_date TEXT
);
"""
        query = """
WITH first_login AS (
    SELECT player_id, MIN(event_date) AS first_date
    FROM activity
    GROUP BY player_id
)
SELECT a.player_id
FROM first_login AS a
LEFT JOIN activity AS b
    ON a.player_id = b.player_id
"""
        calls: list[str] = []
        original = Instance.resolve_table

        def recording_resolve_table(self, table):
            name = table.name if hasattr(table, "name") else str(table)
            calls.append(name)
            return original(self, table)

        with patch.object(Instance, "resolve_table", recording_resolve_table):
            inst = speculate(ddls, query, "sqlite")

        self.assertIsNotNone(inst)
        self.assertNotIn("first_login", calls)
        self.assertGreater(len(inst.get_rows("activity")), 0)


class TestSpeculateConstraintCompletion(unittest.TestCase):
    def test_check_constraint_with_non_query_column_is_satisfied(self):
        ddls = """
CREATE TABLE customer (
    id INT,
    referee_id INT,
    CHECK (referee_id <> id)
);
"""
        inst = speculate(
            ddls,
            "SELECT referee_id FROM customer WHERE referee_id = 1",
            "sqlite",
        )

        self.assertIsNotNone(inst)
        id_col = inst.resolve_column("customer", "id")
        referee_col = inst.resolve_column("customer", "referee_id")
        for row in inst.get_rows("customer"):
            values = Instance._row_value_dict(row)
            self.assertNotEqual(values[referee_col], values[id_col])

    def test_self_fk_check_constraint_can_seed_customer_filter(self):
        ddls = """
CREATE TABLE customer (
    id INT PRIMARY KEY,
    name VARCHAR(255),
    referee_id INT,
    FOREIGN KEY (referee_id) REFERENCES customer(id),
    CHECK (referee_id <> id)
);
"""
        inst = speculate(
            ddls,
            "SELECT name FROM customer "
            "WHERE referee_id <> 2 OR referee_id IS NULL",
            "mysql",
            generate_negatives=False,
        )

        self.assertIsNotNone(inst)
        self.assertGreater(len(inst.get_rows("customer")), 0)
        id_col = inst.resolve_column("customer", "id")
        referee_col = inst.resolve_column("customer", "referee_id")
        ids = {
            Instance._row_value_dict(row)[id_col]
            for row in inst.get_rows("customer")
        }
        for row in inst.get_rows("customer"):
            values = Instance._row_value_dict(row)
            self.assertNotEqual(values[referee_col], values[id_col])
            if values[referee_col] is not None:
                self.assertIn(values[referee_col], ids)

    def test_unique_values_are_reserved_across_speculative_batch(self):
        ddls = "CREATE TABLE insurance (pid INT PRIMARY KEY);"
        inst = speculate(
            ddls,
            "SELECT pid FROM insurance WHERE pid > 0",
            "sqlite",
        )

        self.assertIsNotNone(inst)
        pid_col = inst.resolve_column("insurance", "pid")
        pids = [
            Instance._row_value_dict(row)[pid_col]
            for row in inst.get_rows("insurance")
        ]
        self.assertEqual(len(pids), len(set(pids)))
        self.assertGreaterEqual(len(pids), 3)


if __name__ == "__main__":
    unittest.main()
