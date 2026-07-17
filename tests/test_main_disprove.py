from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import ANY, call, patch

from parseval.main import _final_projection_count, _normalize_sql, disprove
from parseval.states import ExecutionResult, Verdict


class TestMainDisprove(unittest.TestCase):
    def test_final_projection_count_only_counts_selects_without_star_projection(self):
        self.assertEqual(
            2,
            _final_projection_count("SELECT id, age FROM users", "sqlite"),
        )
        self.assertIsNone(
            _final_projection_count("SELECT * FROM users", "sqlite"),
        )
        self.assertIsNone(
            _final_projection_count("SELECT users.* FROM users", "sqlite"),
        )
        self.assertIsNone(
            _final_projection_count(
                "SELECT id FROM users UNION SELECT id FROM admins",
                "sqlite",
            ),
        )

    def test_disprove_returns_syntax_error_before_generation_for_invalid_query(self):
        sql1 = "SELECT FROM users"
        sql2 = "SELECT id FROM users"
        syntax_result = ExecutionResult(query=sql1, error_msg="near FROM: syntax error")

        with (
            patch("parseval.main.generate") as generate_mock,
            patch("parseval.main.to_db") as to_db_mock,
            patch("parseval.main.execute_query", return_value=syntax_result) as execute_mock,
        ):
            result = disprove(
                sql1,
                sql2,
                "CREATE TABLE users (id INT PRIMARY KEY);",
                "sqlite:///tmp/test-main-disprove.sqlite",
                "sqlite",
            )

        self.assertEqual(Verdict.SYNTAX_ERROR, result.verdict)
        self.assertEqual("near FROM: syntax error", result.error_msg)
        self.assertEqual(result.error_msg, result.generation.error_msg)
        to_db_mock.assert_called_once()
        execute_mock.assert_called_once_with(
            sql1,
            "sqlite:///tmp/test-main-disprove.sqlite",
            "sqlite",
            60,
        )
        generate_mock.assert_not_called()

    def test_disprove_returns_syntax_error_for_execution_error_after_generation(self):
        sql1 = "SELECT id FROM users"
        sql2 = "SELECT id FROM users JOIN orders ON users.id = orders.user_id"
        schema = "CREATE TABLE users (id INT PRIMARY KEY);"
        connection_string = "sqlite:///tmp/test-main-disprove.sqlite"
        instance = SimpleNamespace(
            generation=SimpleNamespace(
                status="sat",
                create_rows={"users": [{"id": 1}]},
                coverage_ratio=1.0,
            ),
            tables={"users": object()},
            get_rows=lambda table: [{"id": 1}],
        )

        def execute(query, *_args):
            if execute_mock.call_count <= 2:
                return ExecutionResult(query=query)
            if query == sql2:
                return ExecutionResult(query=query, error_msg="ambiguous column name: id")
            return ExecutionResult(query=query, rows=[(1,)])

        with (
            patch("parseval.main.generate", return_value=instance),
            patch("parseval.main.to_db"),
            patch("parseval.main.execute_query", side_effect=execute) as execute_mock,
        ):
            result = disprove(sql1, sql2, schema, connection_string, "sqlite")

        self.assertEqual(Verdict.SYNTAX_ERROR, result.verdict)
        self.assertEqual("ambiguous column name: id", result.error_msg)

    def test_disprove_returns_neq_before_generation_for_different_projection_counts(self):
        sql1 = "SELECT id FROM users"
        sql2 = "SELECT id, age FROM users"

        with patch("parseval.main.generate") as generate_mock:
            result = disprove(
                sql1,
                sql2,
                "CREATE TABLE users (id INT PRIMARY KEY, age INT);",
                "sqlite:///tmp/test-main-disprove.sqlite",
                "sqlite",
            )

        self.assertEqual(Verdict.NEQ, result.verdict)
        self.assertTrue(result.generation.success)
        generate_mock.assert_not_called()

    def test_disprove_does_not_return_neq_before_generation_for_star_projection_count(self):
        sql1 = "SELECT * FROM users"
        sql2 = "SELECT id, age FROM users"
        schema = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        connection_string = "sqlite:///tmp/test-main-disprove.sqlite"
        instance = SimpleNamespace(
            generation=SimpleNamespace(
                status="sat",
                create_rows={"users": [{"id": 1, "age": 21}]},
                coverage_ratio=1.0,
            ),
            tables={"users": object()},
            get_rows=lambda table: [{"id": 1, "age": 21}],
        )

        def execute(query, *_args):
            return ExecutionResult(query=query, rows=[(1, 21)])

        with (
            patch("parseval.main.generate", return_value=instance) as generate_mock,
            patch("parseval.main.to_db"),
            patch("parseval.main.execute_query", side_effect=execute),
        ):
            result = disprove(sql1, sql2, schema, connection_string, "sqlite")

        self.assertNotEqual(Verdict.NEQ, result.verdict)
        generate_mock.assert_called()

    def test_disprove_returns_eq_for_normalized_textual_identity_without_generation(self):
        sql1 = " SELECT id FROM users ; "
        sql2 = "select   id from users"

        with patch("parseval.main.generate") as generate_mock:
            result = disprove(
                sql1,
                sql2,
                "CREATE TABLE users (id INT PRIMARY KEY);",
                "sqlite:///tmp/test-main-disprove.sqlite",
                "sqlite",
            )

        self.assertEqual(Verdict.EQ, result.verdict)
        generate_mock.assert_not_called()
        self.assertTrue(result.generation.success)

    def test_normalize_sql_normalizes_identifier_case_but_preserves_string_literals(self):
        self.assertEqual(
            _normalize_sql(
                "SELECT ID FROM USERS WHERE NAME = 'Legal'",
                "sqlite",
            ),
            _normalize_sql(
                "select id from users where name = 'Legal'",
                "sqlite",
            ),
        )
        self.assertNotEqual(
            _normalize_sql(
                "SELECT id FROM users WHERE name = 'Legal'",
                "sqlite",
            ),
            _normalize_sql(
                "SELECT id FROM users WHERE name = 'legal'",
                "sqlite",
            ),
        )

    def test_disprove_does_not_treat_literal_case_change_as_textual_identity(self):
        sql1 = "SELECT id FROM users WHERE name = 'Legal'"
        sql2 = "select id from users where name = 'legal'"

        with patch("parseval.main.generate", side_effect=RuntimeError("generated")):
            result = disprove(
                sql1,
                sql2,
                "CREATE TABLE users (id INT PRIMARY KEY, name TEXT);",
                "sqlite:///tmp/test-main-disprove.sqlite",
                "sqlite",
            )

        self.assertEqual(Verdict.UNKNOWN, result.verdict)
        self.assertEqual("generated", result.error_msg)

    def test_disprove_strips_matching_order_by_and_limit_before_generation(self):
        sql1 = "SELECT id FROM users WHERE age > 21 ORDER BY id LIMIT 1"
        sql2 = "SELECT id FROM users WHERE age >= 22 ORDER BY id LIMIT 1"
        schema = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        connection_string = "sqlite:///tmp/test-main-disprove.sqlite"
        instance = SimpleNamespace(
            generation=SimpleNamespace(
                status="sat",
                create_rows={"users": [{"id": 1, "age": 21}]},
                coverage_ratio=1.0,
            ),
            tables={"users": object()},
            get_rows=lambda table: [{"id": 1, "age": 21}],
        )

        def execute(query, *_args):
            if "> 21" in query:
                return ExecutionResult(query=query, rows=[])
            return ExecutionResult(query=query, rows=[(1,)])

        with (
            patch("parseval.main.generate", return_value=instance) as generate_mock,
            patch("parseval.main.to_db"),
            patch("parseval.main.execute_query", side_effect=execute),
        ):
            result = disprove(sql1, sql2, schema, connection_string, "sqlite")

        self.assertEqual(Verdict.NEQ, result.verdict)
        generated_sql = generate_mock.call_args.args[1]
        self.assertNotIn("ORDER BY", generated_sql.upper())
        self.assertNotIn("LIMIT", generated_sql.upper())

    def test_disprove_returns_neq_when_sql1_generated_instance_separates_queries(self):
        sql1 = "SELECT id FROM users"
        sql2 = "SELECT id FROM users WHERE id > 10"
        schema = "CREATE TABLE users (id INT PRIMARY KEY);"
        connection_string = "sqlite:///tmp/test-main-disprove.sqlite"
        instance = SimpleNamespace(
            generation=SimpleNamespace(
                status="sat",
                create_rows={"users": [{"id": 1}]},
                coverage_ratio=1.0,
            ),
            tables={"users": object()},
            get_rows=lambda table: [{"id": 1}],
        )

        def execute(query, *_args):
            if execute_mock.call_count <= 2:
                return ExecutionResult(query=query)
            if query == sql1:
                return ExecutionResult(query=query, rows=[(1,)])
            return ExecutionResult(query=query, rows=[])

        with (
            patch("parseval.main.generate", return_value=instance) as generate_mock,
            patch("parseval.main.to_db") as to_db_mock,
            patch("parseval.main.execute_query", side_effect=execute) as execute_mock,
        ):
            result = disprove(
                sql1,
                sql2,
                schema,
                connection_string,
                "sqlite",
                table_rows=2,
                max_iterations=0,
                generate_negatives=False,
                timeout=7,
            )

        self.assertEqual(Verdict.NEQ, result.verdict)
        generate_mock.assert_called_once()
        self.assertEqual(sql1, generate_mock.call_args.args[1])
        self.assertEqual(2, generate_mock.call_args.kwargs["bounds"].table_rows)
        self.assertEqual(0, generate_mock.call_args.kwargs["bounds"].max_iterations)
        self.assertFalse(generate_mock.call_args.kwargs["generate_negatives"])
        self.assertEqual(2, to_db_mock.call_count)
        to_db_mock.assert_has_calls(
            [
                call(instance, connection_string, dialect="sqlite"),
            ],
            any_order=True,
        )
        execute_mock.assert_has_calls(
            [
                call(sql1, connection_string, "sqlite", 7),
                call(sql2, connection_string, "sqlite", 7),
                call(sql1, connection_string, "sqlite", 7),
                call(sql2, connection_string, "sqlite", 7),
            ]
        )
        self.assertEqual(1, result.generation.rows_generated)
        self.assertEqual(1.0, result.generation.coverage)
        self.assertEqual([(1,)], result.q1_result.rows)
        self.assertEqual([], result.q2_result.rows)

    def test_disprove_tries_sql2_generated_instance_when_sql1_instance_is_equivalent(self):
        sql1 = "SELECT id FROM users"
        sql2 = "SELECT id FROM users WHERE id > 10"
        schema = "CREATE TABLE users (id INT PRIMARY KEY);"
        connection_string = "sqlite:///tmp/test-main-disprove.sqlite"
        first_instance = SimpleNamespace(
            generation=SimpleNamespace(
                status="sat",
                create_rows={"users": [{"id": 11}]},
                coverage_ratio=0.5,
            ),
            tables={"users": object()},
            get_rows=lambda table: [{"id": 11}],
        )
        second_instance = SimpleNamespace(
            generation=SimpleNamespace(
                status="sat",
                create_rows={"users": [{"id": 1}]},
                coverage_ratio=0.75,
            ),
            tables={"users": object()},
            get_rows=lambda table: [{"id": 1}],
        )

        def execute(query, *_args):
            if execute_mock.call_count <= 2:
                return ExecutionResult(query=query)
            if execute_mock.call_count <= 4:
                return ExecutionResult(query=query, rows=[(11,)])
            if query == sql1:
                return ExecutionResult(query=query, rows=[(1,)])
            return ExecutionResult(query=query, rows=[])

        with (
            patch("parseval.main.generate", side_effect=[first_instance, second_instance]) as generate_mock,
            patch("parseval.main.to_db") as to_db_mock,
            patch("parseval.main.execute_query", side_effect=execute) as execute_mock,
        ):
            result = disprove(sql1, sql2, schema, connection_string, "sqlite")

        self.assertEqual(Verdict.NEQ, result.verdict)
        self.assertEqual([sql1, sql2], [args.args[1] for args in generate_mock.call_args_list])
        self.assertEqual(
            [call(ANY, connection_string, dialect="sqlite"),
             call(first_instance, connection_string, dialect="sqlite"),
             call(second_instance, connection_string, dialect="sqlite")],
            to_db_mock.call_args_list,
        )
        self.assertEqual(6, execute_mock.call_count)
        self.assertEqual(1, result.generation.rows_generated)
        self.assertEqual(0.75, result.generation.coverage)


if __name__ == "__main__":
    unittest.main()
