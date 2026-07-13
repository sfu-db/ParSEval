"""Snapshot tests for the identity-free Instance."""

from __future__ import annotations

import datetime as dt
import unittest

from parseval.instance import Instance


SCHEMA = """
CREATE TABLE users (
    id INT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at DATE NULL
);
"""


class InstanceSnapshotTests(unittest.TestCase):
    def test_snapshot_orders_foreign_key_parents_before_children(self):
        schema = """
        CREATE TABLE child (
            id INT PRIMARY KEY,
            parent_id INT,
            FOREIGN KEY (parent_id) REFERENCES parent(id)
        );
        CREATE TABLE parent (
            id INT PRIMARY KEY
        );
        """
        instance = Instance(ddls=schema, name="fk_order", dialect="sqlite")

        snapshot = instance.snapshot()

        self.assertEqual([table.table_name for table in snapshot.tables], ["parent", "child"])
        self.assertLess(
            snapshot.schema_ddl.index("CREATE TABLE parent"),
            snapshot.schema_ddl.index("CREATE TABLE child"),
        )

    def test_snapshot_preserves_cyclic_foreign_key_relative_order(self):
        schema = """
        CREATE TABLE left_node (
            id INT PRIMARY KEY,
            right_id INT,
            FOREIGN KEY (right_id) REFERENCES right_node(id)
        );
        CREATE TABLE right_node (
            id INT PRIMARY KEY,
            left_id INT,
            FOREIGN KEY (left_id) REFERENCES left_node(id)
        );
        """
        instance = Instance(ddls=schema, name="fk_cycle", dialect="sqlite")

        snapshot = instance.snapshot()

        self.assertEqual(
            [table.table_name for table in snapshot.tables],
            ["left_node", "right_node"],
        )
        self.assertLess(
            snapshot.schema_ddl.index("CREATE TABLE left_node"),
            snapshot.schema_ddl.index("CREATE TABLE right_node"),
        )

    def test_snapshot_keeps_in_memory_rows_unchanged(self):
        instance = Instance(ddls=SCHEMA, name="snapshot", dialect="sqlite")
        instance.place_row(
            "users",
            {"id": 1, "name": "alpha", "created_at": dt.date(2024, 1, 1)},
        )
        instance.place_row(
            "users",
            {"id": 1, "name": "beta", "created_at": dt.date(2024, 1, 2)},
        )
        original_count = len(instance.get_rows("users"))

        snapshot = instance.snapshot()

        self.assertEqual(len(instance.get_rows("users")), original_count)
        self.assertEqual(
            snapshot.tables[0].rows,
            (
                {"id": 1, "name": "alpha", "created_at": dt.date(2024, 1, 1)},
                {"id": 1, "name": "beta", "created_at": dt.date(2024, 1, 2)},
            ),
        )

    def test_snapshot_preserves_null_rows(self):
        instance = Instance(ddls=SCHEMA, name="snapshot", dialect="sqlite")
        instance.place_row(
            "users",
            {"id": 1, "name": "alpha", "created_at": None},
        )
        instance.place_row(
            "users",
            {"id": 1, "name": "beta", "created_at": None},
        )

        snapshot = instance.snapshot()

        self.assertEqual(
            snapshot.tables[0].rows,
            (
                {"id": 1, "name": "alpha", "created_at": None},
                {"id": 1, "name": "beta", "created_at": None},
            ),
        )


if __name__ == "__main__":
    unittest.main()
