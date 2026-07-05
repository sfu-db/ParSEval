import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from sqlalchemy.engine import make_url

from parseval import db_manager
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

    def test_mysql_ensure_database_uses_server_url(self):
        engine = MagicMock()
        engine.begin.return_value.__enter__.return_value = Mock()
        url = make_url("mysql+pymysql://root:pw@localhost:3306/missing_db")

        with patch("parseval.db_manager.create_engine", return_value=engine) as create_engine:
            db_manager._providers["mysql"].ensure_database(url)

        admin_url = create_engine.call_args.args[0]
        self.assertEqual(admin_url.database, "")

    def test_get_connection_does_not_cache_engine_or_database_initialization(self):
        class FakeProvider:
            def __init__(self):
                self.ensure_calls = 0
                self.engines = []

            def ensure_database(self, url):
                self.ensure_calls += 1

            def create_engine(
                self,
                url,
                *,
                pool_size,
                max_overflow,
                pool_timeout,
                pool_recycle,
                connect_timeout,
            ):
                engine = Mock()
                engine.url = url
                self.engines.append(engine)
                return engine

        provider = FakeProvider()

        with patch.dict(db_manager._providers, {"sqlite": provider}):
            with DBManager().get_connection("sqlite:///:memory:", "sqlite") as first:
                first_engine = first.engine
            with DBManager().get_connection("sqlite:///:memory:", "sqlite") as second:
                second_engine = second.engine

        self.assertEqual(provider.ensure_calls, 2)
        self.assertEqual(len(provider.engines), 2)
        self.assertIsNot(first_engine, second_engine)
        first_engine.dispose.assert_called_once_with()
        second_engine.dispose.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
