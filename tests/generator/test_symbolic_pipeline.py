from __future__ import annotations

import unittest
from collections import Counter

from parseval.generator import GenerationConfig
from parseval.generator.symbolic.generate import generate
from parseval.generator.symbolic.operator import EncodePipeline
from parseval.instance import Instance
from parseval.plan.explain import explain


def generated_rows(instance, ddl, query):
    plan = explain(ddl, query, dialect="sqlite")
    return EncodePipeline(plan, instance).forward().rows


def cell_value(row, column):
    value = row[column]
    return value.concrete if hasattr(value, "concrete") else value


class TestSymbolicPipeline(unittest.TestCase):
    def test_final_columns_repeat_non_null_and_null_values(self):
        ddl = "CREATE TABLE t (a INT, b INT)"
        query = "SELECT a, b FROM t"

        result = generate(
            ddl,
            query,
            dialect="sqlite",
            config=GenerationConfig(bootstrap_negatives=False),
        )
        rows = generated_rows(result, ddl, query)

        for column in ("a", "b"):
            values = [cell_value(row, column) for row in rows]
            non_null = [value for value in values if value is not None]
            self.assertLess(len(set(non_null)), len(non_null))
            self.assertGreaterEqual(values.count(None), 2)

    def test_computed_final_column_repeats_non_null_and_null_values(self):
        ddl = "CREATE TABLE t (a INT, b INT)"
        query = "SELECT a + b AS z FROM t"

        result = generate(
            ddl,
            query,
            dialect="sqlite",
            config=GenerationConfig(bootstrap_negatives=False),
        )
        values = [cell_value(row, "z") for row in generated_rows(result, ddl, query)]
        non_null = [value for value in values if value is not None]

        self.assertLess(len(set(non_null)), len(non_null))
        self.assertGreaterEqual(values.count(None), 2)

    def test_distinct_keeps_tuple_unique_after_duplicate_and_null_candidates(self):
        ddl = "CREATE TABLE t (a INT, b INT)"
        query = "SELECT DISTINCT a, b FROM t"

        result = generate(
            ddl,
            query,
            dialect="sqlite",
            config=GenerationConfig(bootstrap_negatives=False),
        )
        output = [
            tuple(cell_value(row, column) for column in ("a", "b"))
            for row in generated_rows(result, ddl, query)
        ]
        base = [
            tuple(Instance._row_value_dict(row).values())
            for row in result.get_rows("t")
        ]

        self.assertEqual(len(output), len(set(output)))
        self.assertTrue(any(count >= 2 for count in Counter(base).values()))
        for index in (0, 1):
            values = [row[index] for row in base]
            self.assertGreaterEqual(values.count(None), 2)

    def test_limit_keeps_duplicate_candidates_and_selects_null_result(self):
        ddl = "CREATE TABLE t (a INT)"
        query = "SELECT a FROM t ORDER BY a LIMIT 1"

        result = generate(
            ddl,
            query,
            dialect="sqlite",
            config=GenerationConfig(bootstrap_negatives=False),
        )
        output = generated_rows(result, ddl, query)
        base = [
            next(iter(Instance._row_value_dict(row).values()))
            for row in result.get_rows("t")
        ]
        non_null = [value for value in base if value is not None]

        self.assertEqual([None], [cell_value(row, "a") for row in output])
        self.assertLess(len(set(non_null)), len(non_null))
        self.assertGreaterEqual(base.count(None), 2)

    def test_grouped_aggregate_outputs_repeat_and_sum_can_be_null(self):
        ddl = "CREATE TABLE t (g INT, a INT)"
        query = "SELECT g, SUM(a), COUNT(a) FROM t GROUP BY g"

        result = generate(
            ddl,
            query,
            dialect="sqlite",
            config=GenerationConfig(bootstrap_negatives=False),
        )
        rows = generated_rows(result, ddl, query)
        values = [
            tuple(
                value.concrete if hasattr(value, "concrete") else value
                for value in row.column_values.values()
            )
            for row in rows
        ]
        sums = [row[1] for row in values]
        counts = [row[2] for row in values]
        non_null_sums = [value for value in sums if value is not None]

        self.assertLess(len(set(non_null_sums)), len(non_null_sums))
        self.assertGreaterEqual(sums.count(None), 2)
        self.assertNotIn(None, counts)
        self.assertLess(len(set(counts)), len(counts))

    """End-to-end tests for the symbolic execution pipeline."""

    def test_simple_filter_selects_matching_concrete_rows(self):
        ddl = "CREATE TABLE t (a INT, b TEXT)"
        query = "SELECT a, b FROM t WHERE a > 1"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result, ddl, query)

        self.assertGreater(len(rows), 0)
        self.assertTrue(any(cell_value(row, "a") > 1 for row in rows))

    def test_simple_filter_on_empty_instance_generates_new_row(self):
        ddl = "CREATE TABLE t (a INT)"
        query = "SELECT a FROM t WHERE a > 5"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result, ddl, query)

        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(any(cell_value(row, "a") > 5 for row in rows))

    def test_no_filter_returns_concrete_and_null_generated_rows(self):
        ddl = "CREATE TABLE t (a INT)"
        query = "SELECT a FROM t"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result, ddl, query)

        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertTrue(
                cell_value(row, "a") is None
                or isinstance(cell_value(row, "a"), int)
            )

    def test_and_filter(self):
        ddl = "CREATE TABLE t (a INT, b TEXT)"
        query = "SELECT a, b FROM t WHERE a > 0 AND b = 'x'"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result, ddl, query)

        self.assertGreater(len(rows), 0)
        self.assertTrue(
            any(cell_value(row, "a") > 0 and cell_value(row, "b") == "x" for row in rows)
        )

    def test_inner_join(self):
        ddl = "CREATE TABLE t (a INT, b TEXT); CREATE TABLE u (c INT, d TEXT)"
        query = "SELECT t.a, u.c FROM t JOIN u ON t.a = u.c"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result, ddl, query)

        self.assertGreater(len(rows), 0)
        self.assertTrue(any(cell_value(row, "a") == cell_value(row, "c") for row in rows))

    def test_inner_join_with_filter(self):
        ddl = "CREATE TABLE t (a INT, b TEXT); CREATE TABLE u (c INT, d TEXT)"
        query = "SELECT t.a, u.c FROM t JOIN u ON t.a = u.c WHERE t.a > 1"

        result = generate(ddl, query, dialect="sqlite")
        rows = generated_rows(result, ddl, query)

        self.assertGreater(len(rows), 0)
        self.assertTrue(
            any(
                cell_value(row, "a") > 1
                and cell_value(row, "a") == cell_value(row, "c")
                for row in rows
            )
        )

    def test_mysql_enum_filter_generates_declared_values_only(self):
        ddl = """
CREATE TABLE employee (
    employee_id INT,
    primary_flag ENUM('Y', 'N') NOT NULL
);
"""
        result = generate(
            ddl,
            "SELECT employee_id FROM employee WHERE primary_flag = 'Y'",
            dialect="mysql",
            config=GenerationConfig(bootstrap_negatives=False),
        )

        flag_col = result.resolve_column("employee", "primary_flag")
        values = [
            Instance._row_value_dict(row)[flag_col]
            for row in result.get_rows("employee")
        ]
        self.assertGreater(len(values), 0)
        self.assertIn("Y", values)
        self.assertTrue(all(value in {"Y", "N"} for value in values))


if __name__ == "__main__":
    unittest.main()
