import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from parseval.instance import Instance
from parseval.domain.exceptions import UniqueConflictError


SCHEMA = """
CREATE TABLE users (
    id INT PRIMARY KEY,
    name TEXT NOT NULL
);
"""


class InstanceLoaderTests(unittest.TestCase):
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

    def test_to_db_passes_connection_string_and_dialect_directly_to_loader(self):
        instance = Instance(ddls=SCHEMA, name="target_case", dialect="sqlite")

        with patch("parseval.instance.core.InstanceLoader.load") as load:
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

    def test_create_row_retries_unique_conflict_after_bootstrapping_reference_rows(self):
        schema = """
        CREATE TABLE parents (
            id INT PRIMARY KEY
        );
        CREATE TABLE children (
            id INT PRIMARY KEY,
            parent_id INT UNIQUE,
            FOREIGN KEY (parent_id) REFERENCES parents(id)
        );
        """
        instance = Instance(ddls=schema, name="retry_case", dialect="sqlite")
        original_create_row = instance._create_row
        state = {"raised": False}

        def flaky_create_row(table_name, concretes, alias=None):
            if table_name == "children" and not state["raised"]:
                state["raised"] = True
                raise UniqueConflictError("retry after parent bootstrap")
            return original_create_row(table_name, concretes, alias=alias)

        def bootstrap_reference_rows(table_name, values, prefer_new_for_unique=False, locked_columns=None):
            if table_name != "children" or not prefer_new_for_unique:
                return {}
            parent_position = original_create_row("parents", {}, alias=None)
            parent_id = instance.get_column_data("parents", "id")[parent_position].concrete
            values["parent_id"] = parent_id
            return {"parents": [instance.get_row("parents", parent_position)]}

        with patch.object(instance, "_create_row", side_effect=flaky_create_row):
            with patch.object(
                instance,
                "_bootstrap_reference_rows",
                side_effect=bootstrap_reference_rows,
            ):
                result = instance.create_row("children", {"id": 2})

        self.assertIn("parents", result.created)
        self.assertIn("children", result.created)
        parent_id = result.created["parents"][0]["id"].concrete
        child_parent_id = result.created["children"][0]["parent_id"].concrete
        self.assertEqual(parent_id, child_parent_id)

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


if __name__ == "__main__":
    unittest.main()
