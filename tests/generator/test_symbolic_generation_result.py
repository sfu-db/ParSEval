from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.generator import BmcBounds, generate_query_database
from parseval.generator.symbolic.generate import generate
from parseval.generator.symbolic.operator import EncodePipeline
from parseval.instance import Instance
from parseval.instance.exporter import InstanceValueSerializer
from parseval.plan.explain import explain
from parseval.plan.context import DerivedSchema
from parseval.solver.types import SolverVar


def snapshot_rows(instance: Instance) -> dict[str, list[dict[str, object]]]:
    serializer = InstanceValueSerializer()
    rows: dict[str, list[dict[str, object]]] = {}
    for table in instance.snapshot().tables:
        if table.rows:
            rows[table.table_name] = [
                serializer.serialize_row(table.table_name, row) for row in table.rows
            ]
    return rows


class TestSymbolicGenerationResult(unittest.TestCase):
    def test_symbolic_generate_returns_enriched_root_schema(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        instance = Instance(ddl, name="symbolic", dialect="sqlite")
        plan = explain(ddl, query, "sqlite")

        result = generate(plan, instance, query=query, bounds=BmcBounds(max_iterations=0))
        schema = result.generation.root_schema

        self.assertIs(result, instance)
        self.assertIsInstance(schema, DerivedSchema)
        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(result.generation.create_rows)
        self.assertIsNotNone(result.generation.problem)
        self.assertTrue(result.generation.assignments)
        self.assertGreater(result.generation.coverage_ratio, 0.0)
        self.assertTrue(result.generation.obligations)
        self.assertTrue(schema.evidence)

    def test_symbolic_generation_passes_referenced_check_constraints_to_problem(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT CHECK (age > 0));"
        query = "SELECT id FROM users WHERE age > 21"
        instance = Instance(ddl, name="checks", dialect="sqlite")
        plan = explain(ddl, query, "sqlite")

        result = generate(plan, instance, query=query, bounds=BmcBounds(max_iterations=0))

        self.assertTrue(
            any(
                any(
                    var.meta.get("column") == "age"
                    for var in constraint.find_all(SolverVar)
                )
                for constraint in result.generation.problem.constraints
            )
        )

    def test_symbolic_generation_does_not_solve_rows_that_violate_check_constraints(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT CHECK (age >= 0));"
        query = "SELECT id FROM users WHERE age < 0"
        instance = Instance(ddl, name="checks", dialect="sqlite")
        plan = explain(ddl, query, "sqlite")

        result = generate(plan, instance, query=query, bounds=BmcBounds(max_iterations=0))

        self.assertFalse(result.generation.root_schema.rows)
        rows = snapshot_rows(result).get("users", [])
        self.assertTrue(all(row["age"] >= 0 for row in rows))

    def test_symbolic_generation_passes_referenced_unique_constraints_to_problem(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT UNIQUE);"
        query = "SELECT id FROM users WHERE age > 30"
        instance = Instance(ddl, name="unique", dialect="sqlite")
        instance.create_rows({"users": [{"id": 1, "age": 31}]})
        plan = explain(ddl, query, "sqlite")

        result = generate(plan, instance, query=query, bounds=BmcBounds(max_iterations=0))

        self.assertTrue(
            any(
                constraint.find(exp.NEQ) is not None
                and any(
                    var.meta.get("column") == "age"
                    for var in constraint.find_all(SolverVar)
                )
                for constraint in result.generation.problem.constraints
            )
        )

    def test_symbolic_generation_passes_referenced_foreign_key_constraints_to_problem(self):
        ddl = """
        CREATE TABLE parent (id INT PRIMARY KEY);
        CREATE TABLE child (
            id INT PRIMARY KEY,
            parent_id INT,
            FOREIGN KEY(parent_id) REFERENCES parent(id)
        );
        """
        query = "SELECT id FROM child WHERE parent_id = 7"
        instance = Instance(ddl, name="fk", dialect="sqlite")
        instance.create_rows({"parent": [{"id": 7}]})
        plan = explain(ddl, query, "sqlite")

        result = generate(plan, instance, query=query, bounds=BmcBounds(max_iterations=0))

        self.assertTrue(
            any(
                constraint.find(exp.In) is not None
                and any(
                    var.meta.get("column") == "parent_id"
                    for var in constraint.find_all(SolverVar)
                )
                for constraint in result.generation.problem.constraints
            )
        )

    def test_coverage_generator_uses_symbolic_pipeline_result(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(result.generation.create_rows)
        self.assertIsNotNone(result.generation.problem)
        self.assertTrue(result.generation.assignments)
        self.assertTrue(
            any(
                obligation.kind == "filter"
                and obligation.target == "true"
                and obligation.status == "covered"
                for obligation in result.generation.obligations
            )
        )

    def test_subquery_alias_handles_string_table_qualifiers(self):
        ddl = "CREATE TABLE schools (id INT PRIMARY KEY, score INT);"
        query = "SELECT s.id FROM (SELECT id, score FROM schools) AS s WHERE s.score > 10"
        instance = Instance(ddl, name="alias", dialect="sqlite")
        instance.create_rows({"schools": [{"id": 1, "score": 20}]})
        plan = explain(ddl, query, "sqlite")

        schema = EncodePipeline(plan, instance).forward()

        self.assertGreater(len(schema.rows), 0)
        self.assertTrue(all(column.table == "s" for column in schema.columns))

    def test_sort_limit_distinct_forward_shape(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT);"
        query = "SELECT DISTINCT id, points FROM scores ORDER BY points DESC LIMIT 1"
        instance = Instance(ddl, name="shape", dialect="sqlite")
        instance.create_rows(
            {
                "scores": [
                    {"id": 1, "points": 10},
                    {"id": 2, "points": 30},
                    {"id": 3, "points": 20},
                ]
            }
        )
        plan = explain(ddl, query, "sqlite")

        schema = EncodePipeline(plan, instance).forward()

        self.assertEqual(1, len(schema.rows))
        self.assertEqual(30, schema.rows[0]["points"])

    def test_aggregate_forward_computes_group_and_functions(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = "SELECT region, COUNT(*), SUM(points), AVG(points), MAX(points) FROM scores GROUP BY region"
        instance = Instance(ddl, name="aggregate", dialect="sqlite")
        instance.create_rows(
            {
                "scores": [
                    {"region": "north", "points": 10},
                    {"region": "north", "points": 30},
                    {"region": "south", "points": 5},
                ]
            }
        )
        plan = explain(ddl, query, "sqlite")

        schema = EncodePipeline(plan, instance).forward()

        rows = {row["region"]: row for row in schema.rows}
        self.assertEqual(2, len(rows))
        self.assertEqual(2, rows["north"]["count(*)"])
        self.assertEqual(40, rows["north"]["sum(scores.points)"])
        self.assertEqual(20, rows["north"]["avg(scores.points)"])
        self.assertEqual(30, rows["north"]["max(scores.points)"])

    def test_symbolic_generation_adds_distinct_having_sum_groups(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = """
        SELECT region, SUM(points)
        FROM scores
        GROUP BY region
        HAVING SUM(points) > 10
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["scores"]
        grouped = {}
        for row in rows:
            grouped.setdefault(row["region"], 0)
            grouped[row["region"]] += row["points"]
        self.assertGreaterEqual(len(grouped), 2)
        self.assertTrue(any(total > 10 for total in grouped.values()))
        self.assertTrue(any(total <= 10 for total in grouped.values()))

    def test_symbolic_generation_adds_distinct_having_avg_input_rows(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = """
        SELECT region, AVG(points)
        FROM scores
        GROUP BY region
        HAVING AVG(points) > 10
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["scores"]
        grouped = {}
        for row in rows:
            grouped.setdefault(row["region"], []).append(row["points"])
        self.assertGreaterEqual(len(grouped), 2)
        self.assertTrue(all(len(points) >= 2 for points in grouped.values()))
        averages = [sum(points) / len(points) for points in grouped.values()]
        self.assertTrue(any(avg > 10 for avg in averages))
        self.assertTrue(any(avg <= 10 for avg in averages))


if __name__ == "__main__":
    unittest.main()
