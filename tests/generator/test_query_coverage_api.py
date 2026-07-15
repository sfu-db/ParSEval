from __future__ import annotations

import unittest
import json
import tempfile
from pathlib import Path
from typing import Any, Sequence

import parseval.generator as generator_api
from parseval.db_manager import DBManager
from parseval.instance import Instance
from parseval.generator import (
    BmcBounds,
    CoverageTreeNode,
    CoverageObligation,
    generate,
    generate_query_database,
)


def schema_entry_to_ddl(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Sequence):
        parts = [str(stmt).strip().rstrip(";") for stmt in entry if str(stmt).strip()]
        return ";\n".join(parts) + (";" if parts else "")
    raise TypeError(f"unsupported_schema_entry:{type(entry)!r}")


def load_sqlite_dev_case(index: int):
    queries = json.loads(Path("data/sqlite/dev.json").read_text(encoding="utf-8"))
    schemas = json.loads(Path("data/sqlite/schema.json").read_text(encoding="utf-8"))
    item = queries[index]
    return schema_entry_to_ddl(schemas[item["db_id"]]), item


def flatten_tree(node):
    nodes = [node]
    for child in node.children:
        nodes.extend(flatten_tree(child))
    return nodes


def query_rows_from_generation(ddl: str, create_rows, query: str):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "query_rows.sqlite"
        connection_string = f"sqlite:///{db_path}"
        instance = Instance(ddl, name="query_rows", dialect="sqlite")
        instance.create_rows(create_rows)
        instance.to_db(connection_string, dialect="sqlite")
        with DBManager().get_connection(connection_string, "sqlite") as conn:
            return conn.execute(query, fetch="all")


class TestQueryCoverageApi(unittest.TestCase):
    def test_generator_package_exposes_only_current_generation_api(self):
        self.assertFalse(hasattr(generator_api, "BranchTreeGenerator"))
        self.assertFalse(hasattr(generator_api, "generate_query_database_from_ddl"))
        self.assertTrue(hasattr(generator_api, "generate_query_database"))
        self.assertTrue(hasattr(generator_api, "generate"))

    def test_generate_returns_instance_directly(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"

        instance = generate(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertIsInstance(instance, Instance)
        self.assertEqual("sat", instance.generation.status, instance.generation.reason)
        self.assertTrue(instance.generation.create_rows)

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
        self.assertIsInstance(result.generation.coverage_tree, CoverageTreeNode)
        self.assertEqual(
            result.generation.coverage_ratio,
            result.generation.coverage_tree.coverage_ratio(),
        )
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

    def test_join_filters_on_both_sides_generate_joined_result(self):
        ddl = """
        CREATE TABLE authors (id INT PRIMARY KEY, region TEXT);
        CREATE TABLE books (id INT PRIMARY KEY, author_id INT, status TEXT);
        """
        query = """
        SELECT books.id
        FROM authors
        JOIN books ON authors.id = books.author_id
        WHERE authors.region = 'west' AND books.status = 'published'
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = query_rows_from_generation(ddl, result.generation.create_rows, query)
        self.assertTrue(rows)

    def test_filter_under_subquery_alias_uses_child_schema(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT u.id FROM (SELECT id, age FROM users) AS u WHERE u.age > 21"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = query_rows_from_generation(ddl, result.generation.create_rows, query)
        self.assertTrue(rows)

    def test_projection_derived_expression_evaluates_child_schema_rows(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT, bonus INT);"
        query = "SELECT points + bonus AS total FROM scores WHERE points = 7 AND bonus = 5"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(result.generation.root_schema.rows)
        self.assertEqual(
            {12},
            {next(iter(row.column_values.values())) for row in result.generation.root_schema.rows},
        )

    def test_aggregate_case_inputs_materialize_branch_values(self):
        ddl = "CREATE TABLE schools (cdscode INT PRIMARY KEY, status TEXT, county TEXT, doc TEXT);"
        query = """
        SELECT
            CAST(SUM(CASE WHEN doc = 54 THEN 1 ELSE 0 END) AS REAL)
            / SUM(CASE WHEN doc = 52 THEN 1 ELSE 0 END)
        FROM schools
        WHERE status = 'Merged' AND county = 'Orange'
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        generated = next(iter(result.generation.create_rows.values()))
        docs = {row[next(column for column in row if column.name == "doc")] for row in generated}
        self.assertIn("54", docs)
        self.assertIn("52", docs)
        rows = query_rows_from_generation(ddl, result.generation.create_rows, query)
        self.assertTrue(rows)

    def test_aggregate_group_columns_are_unique_in_derived_schema(self):
        ddl = "CREATE TABLE scores (group_id INT, points INT);"
        query = "SELECT group_id, AVG(points) FROM scores GROUP BY group_id"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        ).generation

        group_column = result.root_schema.columns[0]
        self.assertTrue(result.root_schema.is_unique(group_column))

    def test_order_by_limit_generates_requested_root_cardinality(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT);"
        query = "SELECT id FROM scores ORDER BY points DESC LIMIT 3"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = query_rows_from_generation(ddl, result.generation.create_rows, query)
        self.assertGreaterEqual(len(rows), 3)

    def test_sort_operator_orders_child_schema_rows(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT);"
        query = "SELECT id FROM scores ORDER BY points DESC LIMIT 3"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        rows = query_rows_from_generation(ddl, result.generation.create_rows, query)
        self.assertEqual(3, len(rows))
        generated = next(iter(result.generation.create_rows.values()))
        points_by_id = {
            row[next(column for column in row if column.name == "id")]:
            row[next(column for column in row if column.name == "points")]
            for row in generated
        }
        ordered_points = [points_by_id[row[0]] for row in rows]
        self.assertEqual(ordered_points, sorted(ordered_points, reverse=True))

    def test_limit_operator_applies_offset_to_child_schema_rows(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT);"
        query = "SELECT id FROM scores ORDER BY points DESC LIMIT 2 OFFSET 1"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual(2, len(result.generation.root_schema.rows))
        rows = query_rows_from_generation(ddl, result.generation.create_rows, query)
        self.assertEqual(2, len(rows))

    def test_distinct_limit_generates_distinct_group_values(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT DISTINCT age FROM users LIMIT 3"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        values = [next(iter(row.column_values.values())) for row in result.generation.root_schema.rows]
        self.assertEqual(3, len(values))
        self.assertEqual(len(values), len(set(values)))

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
        self.assertTrue(result.create_rows)
        self.assertGreaterEqual(len(result.root_schema.rows), result.bounds.result_rows)

    def test_missing_supported_filter_branches_generate_delta_rows_after_rebuild(self):
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

    def test_coverage_tree_mirrors_filter_plan_shape(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        ).generation

        tree = result.coverage_tree
        self.assertIsInstance(tree, CoverageTreeNode)
        nodes = flatten_tree(tree)
        self.assertIn("TableScan", {node.step_type for node in nodes})
        filter_nodes = [node for node in nodes if node.step_type == "Filter"]
        self.assertEqual(1, len(filter_nodes))
        self.assertEqual(
            {"true", "false", "null"},
            {target.target for target in filter_nodes[0].targets if target.kind == "filter"},
        )

    def test_scalar_subquery_plan_is_child_of_owning_filter_node(self):
        ddl = """
        CREATE TABLE schools (id INT PRIMARY KEY);
        CREATE TABLE other (id INT PRIMARY KEY, score INT);
        """
        query = """
        SELECT id FROM schools
        WHERE id = (SELECT id FROM other ORDER BY score DESC LIMIT 1)
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        ).generation

        filter_node = next(
            node for node in flatten_tree(result.coverage_tree) if node.step_type == "Filter"
        )
        descendant_types = {
            node.step_type
            for child in filter_node.children
            for node in flatten_tree(child)
        }
        self.assertIn("Sort", descendant_types)
        self.assertIn("TableScan", descendant_types)

    def test_coverage_tree_contains_richer_step_local_targets(self):
        ddl = """
        CREATE TABLE users (id INT PRIMARY KEY, group_id INT, age INT);
        CREATE TABLE groups (id INT PRIMARY KEY);
        """

        join_result = generate_query_database(
            ddl,
            "SELECT users.id FROM users JOIN groups ON users.group_id = groups.id",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        ).generation
        join_targets = {
            target.target
            for node in flatten_tree(join_result.coverage_tree)
            for target in node.targets
            if target.kind == "join"
        }
        self.assertGreaterEqual(join_targets, {"match", "no_match"})

        outer_result = generate_query_database(
            ddl,
            "SELECT users.id FROM users LEFT JOIN groups ON users.group_id = groups.id",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        ).generation
        outer_targets = {
            target.target
            for node in flatten_tree(outer_result.coverage_tree)
            for target in node.targets
            if target.kind == "join"
        }
        self.assertIn("preserved_unmatched", outer_targets)

        case_result = generate_query_database(
            "CREATE TABLE users (id INT PRIMARY KEY, age INT);",
            "SELECT CASE WHEN age > 21 THEN 1 ELSE 0 END FROM users",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        ).generation
        self.assertTrue(
            any(
                target.kind == "case" and target.target == "when_0_true"
                for node in flatten_tree(case_result.coverage_tree)
                for target in node.targets
            )
        )

        aggregate_result = generate_query_database(
            "CREATE TABLE scores (group_id INT, points INT);",
            "SELECT group_id, AVG(points) FROM scores GROUP BY group_id",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        ).generation
        self.assertTrue(
            any(
                target.kind == "multi_row_aggregate_witness"
                for node in flatten_tree(aggregate_result.coverage_tree)
                for target in node.targets
            )
        )

        topk_result = generate_query_database(
            "CREATE TABLE scores (id INT PRIMARY KEY, points INT);",
            "SELECT id FROM scores ORDER BY points DESC LIMIT 1 OFFSET 1",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        ).generation
        topk_targets = {
            (target.kind, target.target)
            for node in flatten_tree(topk_result.coverage_tree)
            for target in node.targets
        }
        self.assertIn(("ordering", "selected"), topk_targets)
        self.assertIn(("ordering", "excluded_competitor"), topk_targets)
        self.assertIn(("ordering", "rank_tie"), topk_targets)
        self.assertIn(("limit_window", "offset_skipped"), topk_targets)

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

    def test_filter_literal_on_foreign_key_can_create_matching_parent(self):
        ddl = """
        CREATE TABLE parent (id TEXT PRIMARY KEY);
        CREATE TABLE child (
            id INT PRIMARY KEY,
            parent_id TEXT,
            FOREIGN KEY(parent_id) REFERENCES parent(id)
        );
        """
        query = "SELECT id FROM child WHERE parent_id = 'target_parent'"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertGreaterEqual(len(result.generation.root_schema.rows), 1)
        parent_rows = [
            {
                column.name: value
                for column, value in Instance._row_value_dict(row).items()
            }
            for row in result.get_rows("parent")
        ]
        child_rows = [
            {
                column.name: value
                for column, value in Instance._row_value_dict(row).items()
            }
            for row in result.get_rows("child")
        ]
        self.assertTrue(any(row["id"] == "target_parent" for row in parent_rows))
        self.assertTrue(
            any(row["parent_id"] == "target_parent" for row in child_rows)
        )

    def test_existing_join_rows_still_report_match_and_no_match_targets(self):
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
        join_obligations = {
            obligation.target: obligation.status
            for obligation in result.obligations
            if obligation.kind == "join"
        }
        self.assertEqual("covered", join_obligations["match"])
        self.assertEqual("covered", join_obligations["no_match"])

    def test_existing_parent_join_generates_rows_for_full_join_coverage(self):
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
        self.assertIn("child", [table.name for table in result.create_rows])
        join_obligations = {
            obligation.target: obligation.status
            for obligation in result.obligations
            if obligation.kind == "join"
        }
        self.assertEqual("covered", join_obligations["match"])
        self.assertEqual("covered", join_obligations["no_match"])

    def test_join_matches_row_from_filtered_side(self):
        ddl = """
        CREATE TABLE bond (bond_id TEXT PRIMARY KEY, bond_type TEXT);
        CREATE TABLE connected (
            atom_id TEXT,
            atom_id2 TEXT,
            bond_id TEXT,
            FOREIGN KEY(bond_id) REFERENCES bond(bond_id)
        );
        """
        query = """
        SELECT connected.atom_id, connected.atom_id2
        FROM bond
        JOIN connected ON bond.bond_id = connected.bond_id
        WHERE bond.bond_type = '-'
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertGreaterEqual(len(result.generation.root_schema.rows), 1)

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
