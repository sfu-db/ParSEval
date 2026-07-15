from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from parseval.generator import BmcBounds
from parseval.instance import Instance
from parseval.main import instantiate_db
from parseval.states import ExecutionResult


class TestInstantiateDb(unittest.TestCase):
    def test_instantiate_db_uses_generator_api_and_persists_instance(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        connection_string = "sqlite:///tmp/test-main-instantiate.sqlite"
        expected_bounds = BmcBounds(table_rows=2, max_iterations=0)
        instance = Instance(ddl, name="generated", dialect="sqlite")
        instance.generation = SimpleNamespace(
            create_rows={
                "users": [
                    {"id": 1, "age": 22},
                    {"id": 2, "age": 18},
                ]
            },
            coverage_ratio=0.75,
        )
        query_result = ExecutionResult(query=query, rows=[(1,)])

        with (
            patch("parseval.main.generate", return_value=instance) as generate_mock,
            patch("parseval.main.to_db") as to_db_mock,
            patch("parseval.main.execute_query", return_value=query_result) as execute_mock,
        ):
            result = instantiate_db(
                query,
                ddl,
                connection_string,
                "sqlite",
                table_rows=2,
                max_iterations=0,
                generate_negatives=False,
                timeout=7,
            )

        generate_mock.assert_called_once_with(
            ddl,
            query,
            dialect="sqlite",
            bounds=expected_bounds,
            generate_negatives=False,
        )
        to_db_mock.assert_called_once_with(instance, connection_string, dialect="sqlite")
        execute_mock.assert_called_once_with(query, connection_string, "sqlite", 7)
        self.assertTrue(result.success, result.error_msg)
        self.assertIs(result.q_result, query_result)
        self.assertEqual(2, result.generation.rows_generated)
        self.assertEqual(0.75, result.generation.coverage)


if __name__ == "__main__":
    unittest.main()
