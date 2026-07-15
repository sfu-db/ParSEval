"""Tests for speculative data seeding (src/parseval/generator/speculate.py)."""

from __future__ import annotations

import unittest

from parseval.generator.speculate import speculate


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


if __name__ == "__main__":
    unittest.main()
