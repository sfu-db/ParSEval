"""Stable Instance API tests (sqlglot keys, no identity.py)."""

from __future__ import annotations

import inspect
import unittest

from sqlglot import exp

from parseval.instance import Instance, InstanceSchema


SCHEMA = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT, score REAL);"


class TestNoIdentityDependency(unittest.TestCase):
    def test_instance_modules_do_not_import_identity(self):
        import parseval.instance.core as core
        import parseval.instance.exporter as exporter
        import parseval.instance.loader as loader
        import parseval.instance.schema as schema
        import parseval.instance.symbols as symbols

        for mod in (core, exporter, loader, schema, symbols):
            for name, val in vars(mod).items():
                if inspect.ismodule(val) and val.__name__.startswith("parseval.identity"):
                    self.fail(f"{mod.__name__} imports {val.__name__}")
            self.assertFalse(
                any(
                    getattr(obj, "__module__", "").startswith("parseval.identity")
                    for obj in vars(mod).values()
                    if isinstance(obj, type)
                ),
                mod.__name__,
            )


class TestPlaceAndCreate(unittest.TestCase):
    def test_place_row(self):
        inst = Instance(ddls=SCHEMA, name="place", dialect="sqlite")
        row = inst.place_row("t", {"id": 1, "name": "alice", "score": 9.5})
        self.assertEqual(row["id"].concrete, 1)
        self.assertIsInstance(next(iter(row.columns)), exp.Identifier)
        self.assertEqual(row["id"].table_name, "t")
        self.assertEqual(row["id"].column_name, "id")

    def test_create_row_and_checkpoint(self):
        inst = Instance(ddls=SCHEMA, name="cp", dialect="sqlite")
        inst.create_row("t", {"id": 1, "name": "a"})
        cp = inst.checkpoint()
        inst.create_row("t", {"id": 2, "name": "b"})
        self.assertEqual(len(inst.get_rows("t")), 2)
        inst.rollback(cp)
        self.assertEqual(len(inst.get_rows("t")), 1)

    def test_fk_bootstrap(self):
        schema = """
        CREATE TABLE parent (id INT PRIMARY KEY);
        CREATE TABLE child (
            id INT PRIMARY KEY,
            parent_id INT,
            FOREIGN KEY (parent_id) REFERENCES parent(id)
        );
        """
        inst = Instance(ddls=schema, name="fk", dialect="sqlite")
        result = inst.create_row("child", {"id": 10})
        self.assertIn(inst.resolve_table("parent"), result.created)
        self.assertEqual(len(inst.get_rows("parent")), 1)

    def test_cycle_bootstrap_uses_domain_picked_text_keys(self):
        """Circular FKs must share Domain-picked keys, not a hardcoded 1."""
        schema = """
        CREATE TABLE left_node (
            id TEXT PRIMARY KEY,
            right_id TEXT,
            FOREIGN KEY (right_id) REFERENCES right_node(id)
        );
        CREATE TABLE right_node (
            id TEXT PRIMARY KEY,
            left_id TEXT,
            FOREIGN KEY (left_id) REFERENCES left_node(id)
        );
        """
        inst = Instance(ddls=schema, name="cycle", dialect="sqlite")
        inst.create_rows({"left_node": {}, "right_node": {}})
        left = inst.get_rows("left_node")[0]
        right = inst.get_rows("right_node")[0]
        self.assertIsInstance(left["id"].concrete, str)
        self.assertIsInstance(right["id"].concrete, str)
        self.assertEqual(left["right_id"].concrete, right["id"].concrete)
        self.assertEqual(right["left_id"].concrete, left["id"].concrete)
        # Hardcoded integer bootstrap would have put 1 here.
        self.assertNotEqual(left["id"].concrete, 1)
        self.assertNotEqual(right["id"].concrete, 1)

    def test_snapshot(self):
        inst = Instance(ddls=SCHEMA, name="snap", dialect="sqlite")
        inst.create_row("t", {"id": 1, "name": "a"})
        snap = inst.snapshot()
        self.assertEqual(snap.tables[0].table_name, "t")
        self.assertEqual(snap.tables[0].rows[0]["id"], 1)

    def test_check_rejection(self):
        ddl = """
        CREATE TABLE follow (
            followee TEXT NOT NULL,
            follower TEXT NOT NULL,
            CONSTRAINT check_follow CHECK (followee <> follower)
        );
        """
        inst = Instance(ddl, name="chk", dialect="sqlite")
        with self.assertRaises(Exception):
            inst.create_row("follow", {"followee": "A", "follower": "A"})


class TestSchemaIdentifiers(unittest.TestCase):
    def test_sqlite_case_fold(self):
        schema = InstanceSchema.from_ddl(
            "CREATE TABLE Users (ID INT PRIMARY KEY);", dialect="sqlite"
        )
        self.assertEqual(
            schema.resolve_table("users"),
            schema.resolve_table("Users"),
        )
        self.assertIsInstance(schema.resolve_table("users"), exp.Table)
        self.assertIsInstance(schema.resolve_column("users", "id"), exp.Identifier)

    def test_constraints_use_sqlglot(self):
        schema = InstanceSchema.from_ddl(
            """
            CREATE TABLE parent (id INT PRIMARY KEY);
            CREATE TABLE child (
                id INT PRIMARY KEY,
                parent_id INT,
                FOREIGN KEY (parent_id) REFERENCES parent(id)
            );
            """,
            dialect="sqlite",
        )
        cons = schema.database_constraints("child")
        self.assertIsInstance(cons.table, exp.Table)
        self.assertEqual(len(cons.foreign_keys), 1)
        self.assertIsInstance(cons.foreign_keys[0].source_columns[0], exp.Identifier)
        self.assertEqual(len(cons.primary_key), 1)


if __name__ == "__main__":
    unittest.main()
