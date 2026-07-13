"""Loader / to_db tests for the identity-free Instance."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from parseval.instance import Instance
from parseval.instance.core import ConstraintViolationError
from parseval.instance.exporter import InstanceSnapshot, TableBatch
from parseval.instance.loader import DatabaseTarget, InstanceLoader


SCHEMA = """
CREATE TABLE users (
    id INT PRIMARY KEY,
    name TEXT NOT NULL
);
"""


class InstanceLoaderTests(unittest.TestCase):
    def test_loader_creates_and_inserts_parents_before_children_and_drops_children_first(self):
        events = []

        class FakeConnection:
            metadata = None

            def drop_table(self, table_name):
                events.append(("drop", table_name))

            def create_tables(self, *ddls):
                for ddl in ddls:
                    events.append(("create", ddl))

            def insert(self, statement, payload):
                events.append(("insert", statement))

        class FakeConnectionContext:
            def __enter__(self):
                return FakeConnection()

            def __exit__(self, exc_type, exc, tb):
                return False

        class FakeDBManager:
            def get_connection(self, *args, **kwargs):
                return FakeConnectionContext()

        class FakeSerializer:
            def serialize_row(self, table_name, row):
                return row

        snapshot = InstanceSnapshot(
            schema_ddl=(
                "CREATE TABLE parent (id INT PRIMARY KEY); "
                "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT)"
            ),
            dialect="sqlite",
            tables=(
                TableBatch("parent", ("id",), ({"id": 1},)),
                TableBatch("child", ("id", "parent_id"), ({"id": 2, "parent_id": 1},)),
            ),
        )

        with patch("parseval.instance.loader.DBManager", return_value=FakeDBManager()):
            InstanceLoader().load(
                snapshot,
                DatabaseTarget("sqlite:///:memory:", "sqlite"),
                FakeSerializer(),
            )

        self.assertEqual(
            [event for event in events if event[0] == "drop"],
            [("drop", "child"), ("drop", "parent")],
        )
        self.assertEqual(
            [event[0] for event in events],
            ["drop", "drop", "create", "create", "insert", "insert"],
        )
        self.assertIn("parent", events[2][1])
        self.assertIn("child", events[3][1])
        self.assertIn('"parent"', events[4][1])
        self.assertIn('"child"', events[5][1])

    def test_create_row_rejects_explicit_null_for_non_nullable_column(self):
        instance = Instance(ddls=SCHEMA, name="nonnull_case", dialect="sqlite")

        with self.assertRaisesRegex(
            ConstraintViolationError,
            "explicit_null_for_non_nullable_column:users.id",
        ):
            instance.create_row("users", {"id": None, "name": "Alice"})

        self.assertEqual(instance.get_rows("users"), [])

    def test_to_db_materializes_snapshot_into_sqlite(self):
        instance = Instance(ddls=SCHEMA, name="loader_case", dialect="sqlite")
        instance.create_row("users", {"id": 1, "name": "Alice"})
        instance.create_row("users", {"id": 2, "name": "Bob"})

        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "loader_case.sqlite"
            connection_string = f"sqlite:///{database_path}"
            result = instance.to_db(
                connection_string=connection_string,
                dialect="sqlite",
            )

            self.assertEqual(result.inserted_tables, ("users",))
            self.assertEqual(result.inserted_rows, 2)

            with sqlite3.connect(database_path) as conn:
                rows = conn.execute("SELECT id, name FROM users ORDER BY id").fetchall()
            self.assertEqual(rows, [(1, "Alice"), (2, "Bob")])

    def test_to_db_can_return_sql_fixture_output(self):
        instance = Instance(ddls=SCHEMA, name="fixture_case", dialect="sqlite")
        instance.create_row("users", {"id": 1, "name": "O'Brien"})

        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "fixture_case.sqlite"
            connection_string = f"sqlite:///{database_path}"
            sql_fixture = instance.to_db(
                connection_string=connection_string,
                dialect="sqlite",
                return_inserted=True,
            )

        self.assertIn("-- Inserting into table: users --", sql_fixture)
        self.assertIn(
            'INSERT INTO "users" ("id", "name") VALUES (1, \'O\'\'Brien\');',
            sql_fixture,
        )

    def test_to_db_quotes_reserved_identifiers(self):
        schema = """
        CREATE TABLE "order" (
            order_id INT PRIMARY KEY,
            account_id INT NOT NULL
        );
        """
        instance = Instance(ddls=schema, name="keyword_case", dialect="sqlite")
        instance.create_row("order", {"order_id": 1, "account_id": 2})

        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "keyword_case.sqlite"
            connection_string = f"sqlite:///{database_path}"
            result = instance.to_db(
                connection_string=connection_string,
                dialect="sqlite",
            )

            self.assertEqual(result.inserted_tables, ("order",))
            with sqlite3.connect(database_path) as conn:
                rows = conn.execute(
                    'SELECT "order_id", "account_id" FROM "order"'
                ).fetchall()
            self.assertEqual(rows, [(1, 2)])

    def test_to_db_coerces_decimal_values_for_sqlite_binding(self):
        schema = """
        CREATE TABLE measurements (
            id INT PRIMARY KEY,
            reading DECIMAL(10, 1) NOT NULL
        );
        """
        instance = Instance(ddls=schema, name="decimal_case", dialect="sqlite")
        instance.create_row("measurements", {"id": 1, "reading": Decimal("501.0")})

        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "decimal_case.sqlite"
            connection_string = f"sqlite:///{database_path}"
            result = instance.to_db(
                connection_string=connection_string,
                dialect="sqlite",
            )

            self.assertEqual(result.inserted_tables, ("measurements",))
            with sqlite3.connect(database_path) as conn:
                rows = conn.execute(
                    "SELECT id, reading FROM measurements"
                ).fetchall()
            self.assertEqual(rows, [(1, 501.0)])

    def test_default_temporal_values_are_valid_temporals(self):
        from datetime import date, datetime, time

        instance = Instance(
            ddls="""
            CREATE TABLE events (
                id INT PRIMARY KEY,
                occurred_on DATE NULL,
                happened_at DATETIME NULL,
                happened_time TIME NULL
            );
            """,
            name="temporal_case",
            dialect="sqlite",
        )
        row = instance.create_row("events", {"id": 1}).created["events"][0]
        self.assertIsInstance(row["occurred_on"].concrete, date)
        self.assertIsInstance(row["happened_at"].concrete, (datetime, date))
        self.assertIsInstance(row["happened_time"].concrete, (time, date, str))

    def test_to_db_passes_connection_string_and_dialect_directly_to_loader(self):
        instance = Instance(ddls=SCHEMA, name="target_case", dialect="sqlite")

        with patch("parseval.instance.io.InstanceLoader.load") as load:
            load.return_value.inserted_tables = ()
            load.return_value.inserted_rows = 0

            instance.to_db(
                connection_string="sqlite:////tmp/target_case.sqlite",
                dialect="sqlite",
            )

        _, kwargs = load.call_args
        self.assertEqual(
            kwargs["target"].connection_string,
            "sqlite:////tmp/target_case.sqlite",
        )
        self.assertEqual(kwargs["target"].dialect, "sqlite")

    def test_composite_primary_key_columns_are_not_treated_as_individually_unique(self):
        schema = """
        CREATE TABLE parents_a (
            id INT PRIMARY KEY
        );
        CREATE TABLE parents_b (
            id INT PRIMARY KEY
        );
        CREATE TABLE child (
            a_id INT NOT NULL,
            b_id INT NOT NULL,
            seq INT NOT NULL,
            PRIMARY KEY (a_id, b_id, seq),
            FOREIGN KEY (a_id) REFERENCES parents_a(id),
            FOREIGN KEY (b_id) REFERENCES parents_b(id)
        );
        """
        instance = Instance(ddls=schema, name="composite_pk_case", dialect="sqlite")
        instance.create_row("child", {"seq": 1})
        instance.create_row("child", {"seq": 2})

        rows = instance.snapshot().tables[-1].rows
        self.assertEqual(len(rows), 2)

    def test_composite_foreign_key_join_rows_do_not_repeat_pairs(self):
        schema = """
        CREATE TABLE event (
            event_id TEXT PRIMARY KEY
        );
        CREATE TABLE member (
            member_id TEXT PRIMARY KEY
        );
        CREATE TABLE attendance (
            link_to_event TEXT,
            link_to_member TEXT,
            PRIMARY KEY (link_to_event, link_to_member),
            FOREIGN KEY (link_to_event) REFERENCES event(event_id),
            FOREIGN KEY (link_to_member) REFERENCES member(member_id)
        );
        """
        instance = Instance(ddls=schema, name="attendance_case", dialect="sqlite")
        for _ in range(5):
            instance.create_row("attendance")

        attendance_rows = next(
            table.rows for table in instance.snapshot().tables if table.table_name == "attendance"
        )
        pairs = [(row["link_to_event"], row["link_to_member"]) for row in attendance_rows]
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_unique_string_primary_key_respects_length_without_collapsing(self):
        schema = """
        CREATE TABLE molecule (
            molecule_id TEXT NOT NULL PRIMARY KEY,
            label TEXT
        );
        """
        instance = Instance(ddls=schema, name="molecule_case", dialect="sqlite")
        for _ in range(5):
            instance.create_row("molecule")

        rows = next(
            table.rows for table in instance.snapshot().tables if table.table_name == "molecule"
        )
        values = [row["molecule_id"] for row in rows]
        self.assertEqual(len(values), len(set(values)))

    def test_quoted_inline_primary_key_is_preserved_after_normalization(self):
        schema = """
        CREATE TABLE IF NOT EXISTS `League` (
            `id` int PRIMARY KEY,
            `country_id` int,
            `name` varchar
        );
        """
        instance = Instance(ddls=schema, name="quoted_pk_case", dialect="sqlite")
        pk = instance.get_primary_key("league")
        self.assertEqual(len(pk), 1)
        self.assertEqual(pk[0].name, "id")
        self.assertTrue(instance.is_unique("league", "id"))

    def test_composite_pk_members_are_not_individually_unique(self):
        schema = """
        CREATE TABLE child (
            a_id INT NOT NULL,
            b_id INT NOT NULL,
            seq INT NOT NULL,
            PRIMARY KEY (a_id, b_id, seq)
        );
        """
        instance = Instance(ddls=schema, name="composite_unique", dialect="sqlite")
        self.assertEqual(len(instance.get_primary_key("child")), 3)
        self.assertFalse(instance.is_unique("child", "a_id"))
        self.assertFalse(instance.is_unique("child", "b_id"))
        self.assertFalse(instance.is_unique("child", "seq"))
        groups = instance.schema.get_table("child").uniqueness_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)


if __name__ == "__main__":
    unittest.main()
