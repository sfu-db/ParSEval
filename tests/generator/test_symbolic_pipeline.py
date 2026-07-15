from __future__ import annotations

import unittest

from parseval.generator.symbolic.generate import generate


def generated_rows(instance):
    return instance.generation.root_schema.rows


def cell_value(row, column):
    value = row[column]
    return value.concrete if hasattr(value, "concrete") else value


class TestSymbolicPipeline(unittest.TestCase):
    """End-to-end tests for the symbolic execution pipeline."""

    def test_simple_filter_selects_matching_concrete_rows(self):
        ddl = "CREATE TABLE t (a INT, b TEXT)"
        query = "SELECT a, b FROM t WHERE a > 1"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result)

        self.assertGreater(len(rows), 0)
        self.assertTrue(any(cell_value(row, "a") > 1 for row in rows))

    def test_simple_filter_on_empty_instance_generates_new_row(self):
        ddl = "CREATE TABLE t (a INT)"
        query = "SELECT a FROM t WHERE a > 5"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result)

        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(any(cell_value(row, "a") > 5 for row in rows))

    def test_no_filter_returns_concrete_and_generated_rows(self):
        ddl = "CREATE TABLE t (a INT)"
        query = "SELECT a FROM t"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result)

        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertIsInstance(cell_value(row, "a"), int)

    def test_and_filter(self):
        ddl = "CREATE TABLE t (a INT, b TEXT)"
        query = "SELECT a, b FROM t WHERE a > 0 AND b = 'x'"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result)

        self.assertGreater(len(rows), 0)
        self.assertTrue(
            any(cell_value(row, "a") > 0 and cell_value(row, "b") == "x" for row in rows)
        )

    def test_inner_join(self):
        ddl = "CREATE TABLE t (a INT, b TEXT); CREATE TABLE u (c INT, d TEXT)"
        query = "SELECT t.a, u.c FROM t JOIN u ON t.a = u.c"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result)

        self.assertGreater(len(rows), 0)
        self.assertTrue(any(cell_value(row, "a") == cell_value(row, "c") for row in rows))

    def test_inner_join_with_filter(self):
        ddl = "CREATE TABLE t (a INT, b TEXT); CREATE TABLE u (c INT, d TEXT)"
        query = "SELECT t.a, u.c FROM t JOIN u ON t.a = u.c WHERE t.a > 1"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result)

        self.assertGreater(len(rows), 0)
        self.assertTrue(
            any(
                cell_value(row, "a") > 1
                and cell_value(row, "a") == cell_value(row, "c")
                for row in rows
            )
        )


if __name__ == "__main__":
    unittest.main()
