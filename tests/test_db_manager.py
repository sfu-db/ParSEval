import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from parseval.db_manager import DBManager
from parseval.db_manager import Connect


class DBManagerTests(unittest.TestCase):
    def test_get_connection_uses_exact_sqlite_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            database_path = Path(tmpdir) / "exact-path.db"
            connection_string = f"sqlite:///{database_path}"

            with DBManager().get_connection(
                connection_string=connection_string,
                dialect="sqlite",
            ) as conn:
                conn.create_tables("CREATE TABLE users (id INT PRIMARY KEY, name TEXT)")
                conn.insert(
                    'INSERT INTO "users" ("id", "name") VALUES (:id, :name)',
                    [{"id": 1, "name": "Alice"}],
                )

            self.assertTrue(database_path.exists())
            with sqlite3.connect(database_path) as raw_conn:
                rows = raw_conn.execute(
                    "SELECT id, name FROM users ORDER BY id"
                ).fetchall()
            self.assertEqual(rows, [(1, "Alice")])

    def test_get_connection_rejects_backend_dialect_mismatch(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            with DBManager().get_connection(
                connection_string="sqlite:////tmp/mismatch.db",
                dialect="postgres",
            ):
                pass

    def test_clear_tables_quotes_reserved_identifier_via_sqlglot(self):
        conn = Connect(engine=Mock())
        conn.engine.url.get_backend_name.return_value = "sqlite"
        stmt = conn._render_delete_table("order")
        self.assertEqual(stmt, 'DELETE FROM "order"')

    def test_drop_table_uses_postgres_cascade_via_sqlglot(self):
        conn = Connect(engine=Mock())
        conn.engine.url.get_backend_name.return_value = "postgresql"
        stmt = conn._render_drop_table("users")
        self.assertEqual(stmt, 'DROP TABLE IF EXISTS "users" CASCADE')


if __name__ == "__main__":
    unittest.main()
