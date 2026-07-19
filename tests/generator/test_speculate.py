"""Tests for speculative data seeding (src/parseval/generator/speculate.py)."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from parseval.generator.speculate import speculate
from parseval.instance import Instance


def _replay_sqlite_query(ddls: str, inst: Instance, query: str) -> list[tuple]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ddls)
        for table in inst.schema.fk_safe_table_order():
            rows = inst.get_rows(table)
            if not rows:
                continue
            columns = list(inst.schema.tables[table].columns)
            placeholders = ", ".join("?" for _ in columns)
            sql = (
                f"INSERT INTO {table.name} "
                f"({', '.join(column.name for column in columns)}) "
                f"VALUES ({placeholders})"
            )
            for row in rows:
                values = Instance._row_value_dict(row)
                conn.execute(sql, [values.get(column) for column in columns])
        return list(conn.execute(query))
    finally:
        conn.close()


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

    def test_not_in_aliased_projection(self):
        ddls = """CREATE TABLE orders (id INT);
CREATE TABLE customers (id INT);"""
        inst = speculate(
            ddls,
            "SELECT id FROM orders WHERE id NOT IN "
            "(SELECT c.id AS customers FROM customers AS c)",
            "sqlite",
        )

        self.assertIsNotNone(inst)
        self.assertGreater(len(inst.get_rows("orders")), 0)

    def test_not_in_parenthesized_distinct_projection(self):
        ddls = """CREATE TABLE orders (id INT);
CREATE TABLE customers (id INT);"""
        inst = speculate(
            ddls,
            "SELECT id FROM orders WHERE id NOT IN "
            "(SELECT DISTINCT (customers.id) FROM customers)",
            "sqlite",
        )

        self.assertIsNotNone(inst)
        self.assertGreater(len(inst.get_rows("orders")), 0)


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

    def test_high_offset_fk_join_creates_same_batch_parent_child_rows(self):
        ddls = """
CREATE TABLE schools (
    CDSCode TEXT PRIMARY KEY,
    Phone TEXT,
    Ext TEXT
);
CREATE TABLE satscores (
    cds TEXT PRIMARY KEY,
    AvgScrWrite INT,
    FOREIGN KEY (cds) REFERENCES schools(CDSCode)
);
"""
        query = """
SELECT T1.Phone, T1.Ext
FROM schools AS T1
INNER JOIN satscores AS T2 ON T1.CDSCode = T2.cds
ORDER BY T2.AvgScrWrite DESC
LIMIT 1 OFFSET 222
"""
        inst = speculate(ddls, query, "sqlite")

        self.assertIsNotNone(inst)
        self.assertGreaterEqual(len(inst.get_rows("schools")), 223)
        self.assertGreaterEqual(len(inst.get_rows("satscores")), 223)
        self.assertEqual(len(_replay_sqlite_query(ddls, inst, query)), 1)


class TestSpeculateGroupBy(unittest.TestCase):
    def test_simple_group_by_gets_multiple_distinct_group_sizes(self):
        ddls = "CREATE TABLE t (x INT, y INT);"
        query = "SELECT x, COUNT(*) FROM t GROUP BY x"

        inst = speculate(ddls, query, "sqlite", generate_negatives=False)

        self.assertIsNotNone(inst)
        grouped = dict(_replay_sqlite_query(ddls, inst, query))
        self.assertGreaterEqual(len(grouped), 3)
        self.assertEqual(len(set(grouped.values())), len(grouped))

    def test_simple_group_by_in_values_get_distinct_group_sizes(self):
        ddls = "CREATE TABLE t (x INT, y INT);"
        query = "SELECT x, COUNT(*) FROM t WHERE x IN (1, 2, 3) GROUP BY x"

        inst = speculate(ddls, query, "sqlite", generate_negatives=False)

        self.assertIsNotNone(inst)
        grouped = dict(_replay_sqlite_query(ddls, inst, query))
        self.assertEqual(set(grouped), {1, 2, 3})
        self.assertEqual(len(set(grouped.values())), len(grouped))

    def test_group_by_having_count_star_gt_one(self):
        ddls = "CREATE TABLE t (x INT, y INT);"
        query = "SELECT x, COUNT(*) FROM t GROUP BY x HAVING COUNT(*) > 1"

        inst = speculate(ddls, query, "sqlite", generate_negatives=False)

        self.assertIsNotNone(inst)
        grouped = dict(_replay_sqlite_query(ddls, inst, query))
        self.assertGreater(len(grouped), 0)
        self.assertTrue(all(count > 1 for count in grouped.values()))

    def test_group_by_having_count_column_makes_counted_values_non_null(self):
        ddls = "CREATE TABLE t (x INT, y INT);"
        query = "SELECT x, COUNT(y) FROM t GROUP BY x HAVING COUNT(y) >= 2"

        inst = speculate(ddls, query, "sqlite", generate_negatives=False)

        self.assertIsNotNone(inst)
        grouped = dict(_replay_sqlite_query(ddls, inst, query))
        self.assertGreater(len(grouped), 0)
        self.assertTrue(all(count >= 2 for count in grouped.values()))
        for _x, y in _replay_sqlite_query(ddls, inst, "SELECT x, y FROM t"):
            self.assertIsNotNone(y)

    def test_group_by_having_count_distinct_with_distinct_projection_and_order(self):
        ddls = """
CREATE TABLE Views (
    article_id INT,
    author_id INT,
    viewer_id INT,
    view_date TEXT
);
"""
        query = """
SELECT DISTINCT viewer_id AS id
FROM Views
GROUP BY view_date, viewer_id
HAVING COUNT(DISTINCT article_id) > 1
ORDER BY id
"""

        inst = speculate(ddls, query, "sqlite", generate_negatives=False)

        self.assertIsNotNone(inst)
        self.assertGreater(len(_replay_sqlite_query(ddls, inst, query)), 0)
        grouped = _replay_sqlite_query(
            ddls,
            inst,
            """
SELECT view_date, viewer_id, COUNT(DISTINCT article_id)
FROM Views
GROUP BY view_date, viewer_id
HAVING COUNT(DISTINCT article_id) > 1
""",
        )
        self.assertGreater(len(grouped), 0)
        self.assertTrue(all(count > 1 for _date, _viewer, count in grouped))

    def test_group_by_respects_check_constraint(self):
        ddls = "CREATE TABLE t (x INT, y INT CHECK (y > 0));"
        query = "SELECT x, COUNT(*) FROM t GROUP BY x"

        inst = speculate(ddls, query, "sqlite", generate_negatives=False)

        self.assertIsNotNone(inst)
        grouped = dict(_replay_sqlite_query(ddls, inst, query))
        self.assertGreaterEqual(len(grouped), 3)
        values = _replay_sqlite_query(ddls, inst, "SELECT x, y FROM t")
        self.assertTrue(all(y > 0 for _x, y in values))

    def test_group_by_join_preserves_join_predicate(self):
        ddls = """
CREATE TABLE a (id INT, x INT);
CREATE TABLE b (a_id INT, y INT);
"""
        query = """
SELECT a.x, COUNT(*)
FROM a JOIN b ON a.id = b.a_id
GROUP BY a.x
"""

        inst = speculate(ddls, query, "sqlite", generate_negatives=False)

        self.assertIsNotNone(inst)
        grouped = dict(_replay_sqlite_query(ddls, inst, query))
        self.assertGreaterEqual(len(grouped), 2)
        self.assertEqual(len(set(grouped.values())), len(grouped))


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

    def test_computed_cte_output_does_not_resolve_as_base_column(self):
        ddls = """
CREATE TABLE contacts (
    first_name TEXT,
    last_name TEXT
);
"""
        inst = speculate(
            ddls,
            "WITH locations AS ("
            "SELECT CONCAT(first_name, last_name) AS city FROM contacts"
            ") SELECT city FROM locations WHERE city = 'Boston'",
            "mysql",
        )

        self.assertIsNotNone(inst)
        self.assertGreater(len(inst.get_rows("contacts")), 0)

    def test_star_cte_output_resolves_unambiguous_base_column(self):
        ddls = """
CREATE TABLE a (id INT, name TEXT);
CREATE TABLE b (id INT, name TEXT);
"""
        inst = speculate(
            ddls,
            "WITH expanded AS ("
            "SELECT a.*, b.name FROM a JOIN b ON a.id = b.id"
            ") SELECT id FROM expanded WHERE id > 0",
            "sqlite",
        )

        self.assertIsNotNone(inst)
        self.assertGreater(len(inst.get_rows("a")), 0)

    def test_cte_aggregate_keeps_simple_group_output_lineage(self):
        ddls = "CREATE TABLE contacts (user_id INT, email TEXT);"
        inst = speculate(
            ddls,
            "WITH allcontacts AS ("
            "SELECT user_id, COUNT(*) AS cnt FROM contacts GROUP BY user_id"
            ") SELECT allcontacts.user_id FROM allcontacts "
            "WHERE allcontacts.user_id > 0",
            "sqlite",
        )

        self.assertIsNotNone(inst)
        self.assertGreater(len(inst.get_rows("contacts")), 0)

    def test_two_derived_aliases_of_same_table_keep_occurrences_separate(self):
        ddls = "CREATE TABLE t (id INT);"
        inst = speculate(
            ddls,
            "WITH a AS (SELECT id FROM t), b AS (SELECT id FROM t) "
            "SELECT a.id FROM a JOIN b ON a.id <> b.id",
            "sqlite",
        )

        self.assertIsNotNone(inst)
        self.assertGreaterEqual(len(inst.get_rows("t")), 2)

    def test_parenthesized_union_branch_is_seeded(self):
        ddls = "CREATE TABLE t (id INT);"
        inst = speculate(
            ddls,
            "SELECT id FROM t WHERE id > 0 UNION "
            "(SELECT id FROM t WHERE id < 0)",
            "sqlite",
        )

        self.assertIsNotNone(inst)
        self.assertGreater(len(inst.get_rows("t")), 0)


class TestSpeculateConstraintCompletion(unittest.TestCase):
    def test_base_row_seeding_satisfies_date_check_constraint(self):
        ddls = """
CREATE TABLE orders (
    order_date DATE,
    customer_pref_delivery_date DATE,
    CHECK (order_date <= customer_pref_delivery_date)
);
"""
        query = "SELECT order_date FROM orders"

        inst = speculate(ddls, query, "sqlite", generate_negatives=False)

        self.assertIsNotNone(inst)
        self.assertGreater(len(inst.get_rows("orders")), 0)
        self.assertGreater(len(_replay_sqlite_query(ddls, inst, query)), 0)

    def test_base_row_seeding_satisfies_numeric_check_constraint(self):
        ddls = """
CREATE TABLE ranges (
    min_value INT,
    max_value INT,
    CHECK (min_value <= max_value)
);
"""
        query = "SELECT min_value FROM ranges"

        inst = speculate(ddls, query, "sqlite", generate_negatives=False)

        self.assertIsNotNone(inst)
        self.assertGreater(len(inst.get_rows("ranges")), 0)
        self.assertGreater(len(_replay_sqlite_query(ddls, inst, query)), 0)

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
        self.assertGreater(len(inst.get_rows("customer")), 0)
        id_col = inst.resolve_column("customer", "id")
        referee_col = inst.resolve_column("customer", "referee_id")
        for row in inst.get_rows("customer"):
            values = Instance._row_value_dict(row)
            self.assertNotEqual(values[referee_col], values[id_col])
            self.assertIn(id_col, values)

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


class TestSpeculateEnum(unittest.TestCase):
    def test_mysql_enum_filter_generates_declared_values_only(self):
        ddls = """
CREATE TABLE employee (
    employee_id INT,
    primary_flag ENUM('Y', 'N') NOT NULL
);
"""
        inst = speculate(
            ddls,
            "SELECT employee_id FROM employee WHERE primary_flag = 'Y'",
            "mysql",
            generate_negatives=False,
        )

        self.assertIsNotNone(inst)
        flag_col = inst.resolve_column("employee", "primary_flag")
        values = [
            Instance._row_value_dict(row)[flag_col]
            for row in inst.get_rows("employee")
        ]
        self.assertGreater(len(values), 0)
        self.assertIn("Y", values)
        self.assertTrue(all(value in {"Y", "N"} for value in values))


if __name__ == "__main__":
    unittest.main()
