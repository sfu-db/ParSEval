from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from parseval.datasets import get_schema_ddl, load_dev_examples, load_schema_map
from parseval.db_manager import DBManager
from parseval.generation_policy import analyze_smt_generation_support
from parseval.main import instantiate_db
from parseval.instance import Instance
from parseval.query import preprocess_sql
from parseval.data_generator import DataGenerator
from parseval.plan import build_graph_from_scopes


class TestDatasetHelpers(unittest.TestCase):
    def test_repo_dataset_files_load(self):
        examples = load_dev_examples()
        schema_map = load_schema_map()

        self.assertGreater(len(examples), 1000)
        self.assertIn("california_schools", schema_map)
        self.assertIn("SELECT", examples[0].sql.upper())
        self.assertTrue(get_schema_ddl(examples[0].db_id))

    def test_first_dataset_query_is_smt_supported(self):
        example = load_dev_examples()[0]
        ddl = get_schema_ddl(example.db_id)
        expr = preprocess_sql(
            example.sql,
            Instance(ddls=ddl, name="dataset_policy", dialect="sqlite"),
            dialect="sqlite",
        )

        capability = analyze_smt_generation_support(expr)

        self.assertTrue(capability.can_use_smt, capability.reasons)


class TestGenerationFallbackPolicy(unittest.TestCase):
    def test_not_in_subquery_generates_rows_without_speculative_fallback(self):
        target = next(
            example
            for example in load_dev_examples()
            if example.db_id == "toxicology" and example.question_id == 247
        )
        instance = Instance(
            ddls=get_schema_ddl(target.db_id), name="not_in_subquery", dialect="sqlite"
        )
        expr = preprocess_sql(target.sql, instance, dialect="sqlite")
        generator = DataGenerator(expr=expr, instance=instance, verbose=False)
        scope_graph = build_graph_from_scopes(expr)

        subquery_scope = scope_graph.get_node(0)
        outer_scope = scope_graph.get_node(1)
        required_non_null = generator._required_non_null_output_columns(
            scope_graph, subquery_scope
        )
        subquery_result = generator._solve_scope(
            subquery_scope, required_non_null_columns=required_non_null
        )

        self.assertTrue(subquery_result.binding.values)
        self.assertNotIn(None, subquery_result.binding.values)

        generator._apply_subquery_binding(outer_scope, subquery_result.binding)
        outer_result = generator._solve_scope(outer_scope)

        self.assertTrue(outer_result.binding.rows)

    def test_grouped_subquery_join_generates_rows_without_speculative_fallback(self):
        target = next(
            example
            for example in load_dev_examples()
            if example.db_id == "california_schools" and example.question_id == 84
        )
        schema = get_schema_ddl(target.db_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            name = f"{target.db_id}_{target.question_id}"
            instantiate_db(
                query=target.sql,
                schema=schema,
                host_or_path=tmpdir,
                db_id=name,
                dialect="sqlite",
                global_timeout=15,
                query_timeout=5,
                allow_speculative_fallback=False,
            )
            with DBManager().get_connection(
                host_or_path=tmpdir,
                database=f"{name}.sqlite",
                dialect="sqlite",
            ) as conn:
                rows = conn.execute(target.sql, fetch="all", timeout=5)

        self.assertTrue(rows)

    def test_scalar_subquery_binding_propagates_join_seed_values(self):
        target = next(
            example
            for example in load_dev_examples()
            if example.db_id == "financial" and example.question_id == 94
        )
        instance = Instance(
            ddls=get_schema_ddl(target.db_id), name="scalar_subquery_seed", dialect="sqlite"
        )
        expr = preprocess_sql(target.sql, instance, dialect="sqlite")
        generator = DataGenerator(expr=expr, instance=instance, verbose=False)
        scope_graph = build_graph_from_scopes(expr)

        scalar_scope = scope_graph.get_node(1)
        outer_scope = scope_graph.get_node(2)

        scalar_result = generator._solve_scope(scalar_scope)
        self.assertIsNotNone(scalar_result.binding.scalar_value)

        generator._apply_subquery_binding(outer_scope, scalar_result.binding)
        generator._seed_structured_scalar_rows(outer_scope.scope.expression)

        account_rows = instance.get_rows("account")
        district_rows = instance.get_rows("district")
        account_ids = {row["district_id"].concrete for row in account_rows}
        district_ids = {row["district_id"].concrete for row in district_rows}

        self.assertIn(scalar_result.binding.scalar_value, account_ids)
        self.assertIn(scalar_result.binding.scalar_value, district_ids)

    def test_dataset_strftime_query_generates_rows_without_speculative_fallback(self):
        target = next(
            example
            for example in load_dev_examples()
            if example.db_id == "california_schools" and example.question_id == 27
        )
        schema = get_schema_ddl(target.db_id)

        with tempfile.TemporaryDirectory() as tmpdir:
            name = f"{target.db_id}_{target.question_id}"
            instantiate_db(
                query=target.sql,
                schema=schema,
                host_or_path=tmpdir,
                db_id=name,
                dialect="sqlite",
                global_timeout=10,
                query_timeout=5,
                allow_speculative_fallback=False,
            )
            with DBManager().get_connection(
                host_or_path=tmpdir,
                database=f"{name}.sqlite",
                dialect="sqlite",
            ) as conn:
                rows = conn.execute(target.sql, fetch="all", timeout=5)

        self.assertTrue(rows)

    def test_supported_query_uses_data_generator_without_speculative_fallback(self):
        schema = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        sql = "SELECT age FROM users WHERE age > 0"

        class FakeDataGenerator:
            called = 0

            def __init__(self, expr, instance, verbose=False, config=None):
                self.instance = instance

            def generate(self, early_stop, stop_event, timeout=None):
                type(self).called += 1
                self.instance.create_row("users", {"id": 1, "age": 7})

        class FakeSpeculativeGenerator:
            called = 0

            def __init__(self, expr, instance, generator_config=None):
                pass

            def generate(self, **kwargs):
                type(self).called += 1

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("parseval.data_generator.DataGenerator", FakeDataGenerator):
                with patch(
                    "parseval.speculative.SpeculativeGenerator",
                    FakeSpeculativeGenerator,
                ):
                    instance = instantiate_db(
                        query=sql,
                        schema=schema,
                        host_or_path=tmpdir,
                        db_id="supported_case",
                        dialect="sqlite",
                        global_timeout=5,
                    )

        self.assertEqual(1, FakeDataGenerator.called)
        self.assertEqual(0, FakeSpeculativeGenerator.called)
        self.assertEqual(1, len(instance.get_rows("users")))

    def test_unsupported_query_allows_speculative_fallback(self):
        schema = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        sql = "SELECT MYSTERY(age) FROM users"

        class FakeDataGenerator:
            called = 0

            def __init__(self, expr, instance, verbose=False, config=None):
                type(self).called += 1

            def generate(self, early_stop, stop_event, timeout=None):
                raise AssertionError("SMT generator should not run for unsupported SQL")

        class FakeSpeculativeGenerator:
            called = 0

            def __init__(self, expr, instance, generator_config=None):
                self.instance = instance

            def generate(self, **kwargs):
                type(self).called += 1
                self.instance.create_row("users", {"id": 1, "age": 5})

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("parseval.data_generator.DataGenerator", FakeDataGenerator):
                with patch(
                    "parseval.speculative.SpeculativeGenerator",
                    FakeSpeculativeGenerator,
                ):
                    instance = instantiate_db(
                        query=sql,
                        schema=schema,
                        host_or_path=tmpdir,
                        db_id="unsupported_case",
                        dialect="sqlite",
                        global_timeout=5,
                    )

        self.assertEqual(0, FakeDataGenerator.called)
        self.assertEqual(1, FakeSpeculativeGenerator.called)
        self.assertEqual(1, len(instance.get_rows("users")))


if __name__ == "__main__":
    unittest.main()
