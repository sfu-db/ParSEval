from __future__ import annotations

import unittest
import json
from pathlib import Path

import parseval.generator as generator_api
from parseval.instance import Instance
from parseval.generator import (
    BmcBounds,
    CoverageObligation,
    generate_query_database,
)
from scripts.generate_query_fixture import schema_entry_to_ddl


def load_sqlite_dev_case(index: int):
    queries = json.loads(Path("data/sqlite/dev.json").read_text(encoding="utf-8"))
    schemas = json.loads(Path("data/sqlite/schema.json").read_text(encoding="utf-8"))
    item = queries[index]
    return schema_entry_to_ddl(schemas[item["db_id"]]), item


class TestQueryCoverageApi(unittest.TestCase):
    def test_generator_package_exposes_only_current_generation_api(self):
        self.assertFalse(hasattr(generator_api, "BranchTreeGenerator"))
        self.assertFalse(hasattr(generator_api, "generate_query_database_from_ddl"))
        self.assertTrue(hasattr(generator_api, "generate_query_database"))

    def test_generate_query_database_returns_coverage_result(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertIsInstance(result, Instance)
        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(result.generation.create_rows)
        self.assertTrue(result.generation.assignments)
        self.assertIsNotNone(result.generation.problem)
        self.assertGreaterEqual(result.generation.coverage_ratio, 0.0)
        self.assertLessEqual(result.generation.coverage_ratio, 1.0)
        self.assertTrue(result.generation.obligations)
        self.assertTrue(all(isinstance(item, CoverageObligation) for item in result.generation.obligations))
        self.assertTrue(
            any(
                obligation.kind == "filter"
                and obligation.target == "true"
                and obligation.status == "covered"
                for obligation in result.generation.obligations
            )
        )
        self.assertEqual(
            1.0,
            result.generation.coverage_ratio,
            [obligation for obligation in result.generation.obligations if obligation.status != "covered"],
        )

    def test_generate_query_database_reports_sort_limit_semantics(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT);"
        query = "SELECT id FROM scores ORDER BY points DESC LIMIT 2"
        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertEqual(2, result.generation.bounds.table_rows)
        kinds = {obligation.kind for obligation in result.generation.obligations}
        self.assertIn("ordering", kinds)

    def test_realworld_alias_join_resolves_physical_tables(self):
        ddl, item = load_sqlite_dev_case(2)

        result = generate_query_database(
            ddl,
            item["SQL"],
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(result.generation.create_rows)

    def test_realworld_derived_filter_without_physical_table_uses_child_rows(self):
        ddl, item = load_sqlite_dev_case(84)

        result = generate_query_database(
            ddl,
            item["SQL"],
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)

    def test_realworld_projection_preserves_qualified_duplicate_names(self):
        ddl, item = load_sqlite_dev_case(108)

        result = generate_query_database(
            ddl,
            item["SQL"],
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)

    def test_scalar_subquery_obligation_description_does_not_render_step_body(self):
        ddl = """
        CREATE TABLE schools (cdscode INT PRIMARY KEY);
        CREATE TABLE frpm (cdscode INT PRIMARY KEY, free_count INT);
        """
        query = """
        SELECT cdscode
        FROM schools
        WHERE cdscode = (
            SELECT cdscode FROM frpm ORDER BY free_count DESC LIMIT 1
        )
        """
        instance = Instance(ddl, name="coverage", dialect="sqlite")

        result = generate_query_database(
            instance,
            query,
            bounds=BmcBounds(order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        self.assertTrue(
            any(
                obligation.kind == "filter" and obligation.target == "true"
                for obligation in result.obligations
            )
        )

    def test_generate_query_database_surfaces_invalid_query_errors(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT missing_column FROM users"
        instance = Instance(ddl, name="coverage", dialect="sqlite")

        with self.assertRaises(Exception):
            generate_query_database(instance, query)

    def test_existing_row_satisfies_filter_without_delta_rows(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        instance = Instance(ddl, name="coverage", dialect="sqlite")
        instance.create_rows({"users": [{"id": 1, "age": 30}]})

        result = generate_query_database(
            instance,
            query,
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        filter_targets = {
            obligation.target: obligation.status
            for obligation in result.obligations
            if obligation.kind == "filter"
        }
        self.assertEqual("covered", filter_targets["true"])

    def test_existing_rows_cover_filter_true_false_null_semantics_without_delta(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        instance = Instance(ddl, name="coverage", dialect="sqlite")
        instance.create_rows(
            {
                "users": [
                    {"id": 1, "age": 30},
                    {"id": 2, "age": 18},
                    {"id": 3, "age": None},
                ]
            }
        )

        result = generate_query_database(
            instance,
            query,
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        filter_targets = {
            obligation.target: obligation.status
            for obligation in result.obligations
            if obligation.kind == "filter"
        }
        self.assertEqual(
            {"true": "covered", "false": "covered", "null": "covered"},
            filter_targets,
        )
        self.assertEqual({}, result.create_rows)

    def test_missing_filter_branches_generate_full_path_delta_rows(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        instance = Instance(ddl, name="coverage", dialect="sqlite")

        result = generate_query_database(
            instance,
            query,
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        rows = next(iter(result.create_rows.values()))
        ages = [row[next(column for column in row if column.name == "age")] for row in rows]
        filter_targets = {
            obligation.target: obligation.status
            for obligation in result.obligations
            if obligation.kind == "filter"
        }
        self.assertGreaterEqual(len(rows), 3)
        self.assertTrue(any(age is None for age in ages))
        self.assertTrue(any(age <= 21 for age in ages if age is not None))
        self.assertTrue(any(age > 21 for age in ages if age is not None))
        self.assertEqual(
            {"true": "covered", "false": "covered", "null": "covered"},
            filter_targets,
        )

    def test_and_filter_null_branch_uses_compatible_nullable_atom(self):
        ddl = """
        CREATE TABLE frpm (
            cdscode INT PRIMARY KEY,
            county_name TEXT,
            free_meal_count INT
        );
        """
        query = """
        SELECT COUNT(cdscode)
        FROM frpm
        WHERE county_name = 'Los Angeles'
          AND free_meal_count > 500
          AND free_meal_count < 700
        """

        result = generate_query_database(
            ddl,
            query,
            bounds=BmcBounds(
                table_rows=1,
                order_competitors=1,
                max_iterations=0,
            ),
        ).generation

        self.assertEqual("sat", result.status, result.reason)
        rows = next(iter(result.create_rows.values()))
        county_values = [
            row[next(column for column in row if column.name == "county_name")]
            for row in rows
            if any(column.name == "county_name" for column in row)
        ]
        self.assertIn(None, county_values)

    def test_existing_failing_filter_emits_only_generated_delta(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        instance = Instance(ddl, name="coverage", dialect="sqlite")
        instance.create_rows({"users": [{"id": 1, "age": 18}]})

        result = generate_query_database(
            instance,
            query,
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        self.assertEqual(["users"], [table.name for table in result.create_rows])
        rows = next(iter(result.create_rows.values()))
        self.assertTrue(
            any(
                row[next(column for column in row if column.name == "age")] > 21
                for row in rows
            )
        )

    def test_existing_join_rows_satisfy_coverage_without_delta_rows(self):
        ddl = """
        CREATE TABLE parent (id INT PRIMARY KEY);
        CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
        """
        query = """
        SELECT parent.id
        FROM parent JOIN child ON parent.id = child.parent_id
        """
        instance = Instance(ddl, name="coverage", dialect="sqlite")
        instance.create_rows(
            {
                "parent": [{"id": 10}],
                "child": [{"id": 20, "parent_id": 10}],
            }
        )

        result = generate_query_database(
            instance,
            query,
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        self.assertEqual({}, result.create_rows)

    def test_existing_parent_join_emits_only_missing_child_delta(self):
        ddl = """
        CREATE TABLE parent (id INT PRIMARY KEY);
        CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
        """
        query = """
        SELECT parent.id
        FROM parent JOIN child ON parent.id = child.parent_id
        """
        instance = Instance(ddl, name="coverage", dialect="sqlite")
        instance.create_rows({"parent": [{"id": 10}]})

        result = generate_query_database(
            instance,
            query,
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        self.assertEqual(["child"], [table.name for table in result.create_rows])
        [row] = next(iter(result.create_rows.values()))
        self.assertEqual(10, row[next(column for column in row if column.name == "parent_id")])

    def test_existing_scalar_subquery_order_limit_does_not_emit_competitor(self):
        ddl = """
        CREATE TABLE schools (cdscode INT PRIMARY KEY);
        CREATE TABLE frpm (cdscode INT PRIMARY KEY, free_count INT);
        """
        query = """
        SELECT cdscode
        FROM schools
        WHERE cdscode = (
            SELECT cdscode FROM frpm ORDER BY free_count DESC LIMIT 1
        )
        """
        instance = Instance(ddl, name="coverage", dialect="sqlite")
        instance.create_rows(
            {
                "schools": [{"cdscode": 100}],
                "frpm": [{"cdscode": 100, "free_count": 50}],
            }
        )

        result = generate_query_database(
            instance,
            query,
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        self.assertEqual({}, result.create_rows)

    def test_ddl_input_attaches_generation_to_instance(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"

        instance = generate_query_database(
            ddl,
            query,
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )
        result = instance.generation

        self.assertEqual("sat", result.status, result.reason)
        self.assertTrue(result.create_rows)

    def test_coverage_obligations_do_not_carry_descriptions_or_evidence(self):
        fields = set(CoverageObligation.__dataclass_fields__)

        self.assertEqual({"id", "step_type", "kind", "target", "status"}, fields)


if __name__ == "__main__":
    unittest.main()
