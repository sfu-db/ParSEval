from __future__ import annotations

import importlib
import unittest
import sqlite3
from unittest.mock import patch

from sqlglot import exp

from parseval.dtype import DataType
from parseval.generator import BmcBounds, generate_query_database
from parseval.generator.symbolic import operator as symbolic_operator
from parseval.generator.symbolic.generate import generate
from parseval.generator.symbolic.operator import (
    AggregateEncodeStep,
    EncodePipeline,
    _database_constraints_for_solver,
    _aggregate_expression_map,
    _aggregate_key,
    _schema_constraints_for_solver_rows,
    _solve_table_rows,
)
from parseval.instance import Instance
from parseval.instance.exporter import InstanceValueSerializer
from parseval.plan.explain import Aggregate, explain
from parseval.plan.context import DerivedSchema, Row
from parseval.solver.types import SolverVar

symbolic_generate_module = importlib.import_module("parseval.generator.symbolic.generate")


def snapshot_rows(instance: Instance) -> dict[str, list[dict[str, object]]]:
    serializer = InstanceValueSerializer()
    rows: dict[str, list[dict[str, object]]] = {}
    for table in instance.snapshot().tables:
        if table.rows:
            rows[table.table_name] = [
                serializer.serialize_row(table.table_name, row) for row in table.rows
            ]
    return rows


def projected_ids(rows) -> list[object]:
    values = []
    for row in rows:
        for _column, value in row.column_values.items():
            values.append(value)
            break
    return values


def projected_values(rows, column: str) -> list[object]:
    return [row[column] for row in rows]


def sqlite_rows(ddl: str, rows: dict[str, list[dict[str, object]]], query: str):
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ddl)
        for table, table_rows in rows.items():
            table_name = table.name if hasattr(table, "name") else str(table)
            for row in table_rows:
                columns = tuple(
                    column.name if hasattr(column, "name") else str(column)
                    for column in row
                )
                placeholders = ", ".join("?" for _ in columns)
                column_sql = ", ".join(columns)
                conn.execute(
                    f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})",
                    tuple(row[column] for column in row),
                )
        return conn.execute(query).fetchall()
    finally:
        conn.close()


class TestSymbolicGenerationResult(unittest.TestCase):
    def test_symbolic_generate_seeds_from_speculate_before_planning(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        bounds = BmcBounds(table_rows=2, max_iterations=0)
        seeded = Instance(ddl, name="speculative", dialect="sqlite")

        with (
            patch.object(
                symbolic_generate_module,
                "speculate",
                return_value=seeded,
                create=True,
            ) as speculate_mock,
            patch.object(
                symbolic_generate_module,
                "explain",
                side_effect=RuntimeError("planner failed"),
                create=True,
            ),
            patch.object(symbolic_generate_module, "EncodePipeline") as pipeline_mock,
        ):
            result = generate(
                ddl,
                query,
                dialect="sqlite",
                bounds=bounds,
                generate_negatives=False,
            )

        self.assertIs(result, seeded)
        speculate_mock.assert_called_once_with(
            ddl,
            query,
            dialect="sqlite",
            bounds=bounds,
            generate_negatives=False,
        )
        pipeline_mock.assert_not_called()

    def test_symbolic_generate_continues_pipeline_from_speculative_instance(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"
        bounds = BmcBounds(max_iterations=0)
        seeded = Instance(ddl, name="speculative", dialect="sqlite")

        with patch.object(
            symbolic_generate_module,
            "speculate",
            return_value=seeded,
            create=True,
        ):
            result = generate(ddl, query, dialect="sqlite", bounds=bounds)

        self.assertIs(result, seeded)
        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(result.generation.create_rows)

    def test_distinct_forward_collapses_duplicate_projected_rows(self):
        ddl = "CREATE TABLE people (id INT PRIMARY KEY, name TEXT);"
        query = "SELECT DISTINCT name FROM people"
        instance = Instance(ddl, name="distinct_project_rows", dialect="sqlite")
        instance.create_rows(
            {
                "people": [
                    {"id": 1, "name": "Ada"},
                    {"id": 2, "name": "Ada"},
                ]
            }
        )
        plan = explain(ddl, query, "sqlite")

        schema = EncodePipeline(plan, instance).forward()

        self.assertEqual(["Ada"], projected_values(schema.rows, "name"))

    def test_symbolic_generate_returns_enriched_root_schema(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users WHERE age > 21"

        result = generate(ddl, query, dialect="sqlite", bounds=BmcBounds(max_iterations=0))
        schema = result.generation.root_schema

        self.assertIsInstance(schema, DerivedSchema)
        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(result.generation.create_rows)
        self.assertIsNotNone(result.generation.problem)
        self.assertTrue(result.generation.assignments)
        self.assertGreater(result.generation.coverage_ratio, 0.0)
        self.assertTrue(result.generation.obligations)
        self.assertTrue(schema.evidence)

    def test_symbolic_generation_plain_select_defaults_to_multiple_rows(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertGreaterEqual(len(result.generation.root_schema.rows), 3)

    def test_symbolic_generation_plain_select_uses_result_row_bound(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, result_rows=4, max_iterations=0),
        )

        self.assertGreaterEqual(len(result.generation.root_schema.rows), 4)

    def test_symbolic_generation_expands_existing_single_row_to_default_threshold(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users"
        instance = Instance(ddl, name="expand_existing", dialect="sqlite")
        instance.create_rows({"users": [{"id": 1, "age": 18}]})

        result = generate_query_database(
            instance,
            query,
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertGreaterEqual(len(result.root_schema.rows), 3)

    def test_symbolic_generation_limit_one_remains_single_result_row(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users LIMIT 1"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual(1, len(result.generation.root_schema.rows))

    def test_symbolic_generation_limit_two_targets_two_result_rows(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT id FROM users LIMIT 2"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual(2, len(result.generation.root_schema.rows))

    def test_symbolic_generation_group_by_defaults_to_multiple_groups(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = "SELECT region, COUNT(*) FROM scores GROUP BY region"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertGreaterEqual(len(result.generation.root_schema.rows), 3)

    def test_symbolic_generation_distinct_aggregate_uses_result_rows_not_group_bound(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT DISTINCT age FROM users"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(
                table_rows=1,
                result_rows=3,
                groups=1,
                rows_per_group=1,
                max_iterations=0,
            ),
        )

        values = [next(iter(row.column_values.values())) for row in result.generation.root_schema.rows]
        self.assertGreaterEqual(len(values), 3)
        self.assertEqual(len(values), len(set(values)))

    def test_symbolic_generation_distinct_case_generates_distinct_branches(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT);"
        query = "SELECT DISTINCT CASE WHEN age > 21 THEN 'adult' ELSE 'minor' END FROM users"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, result_rows=2, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["users"]
        self.assertTrue(any(row["age"] > 21 for row in rows))
        self.assertTrue(any(row["age"] <= 21 for row in rows))
        self.assertEqual(
            {("adult",), ("minor",)},
            set(sqlite_rows(ddl, result.generation.create_rows, query)),
        )

    def test_symbolic_generation_distinct_arithmetic_expression_generates_distinct_values(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT, score INT);"
        query = "SELECT DISTINCT age + score FROM users"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, result_rows=3, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        query_result = sqlite_rows(ddl, result.generation.create_rows, query)
        self.assertGreaterEqual(len(query_result), 3)
        self.assertEqual(len(query_result), len(set(query_result)))

    def test_symbolic_generation_aggregate_case_generates_requested_branch(self):
        ddl = "CREATE TABLE t (a INT, b INT);"
        query = "SELECT CASE WHEN AVG(a) > AVG(b) THEN 'a' ELSE 'b' END FROM t"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, rows_per_group=2, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["t"]
        a_values = [row["a"] for row in rows if row["a"] is not None]
        b_values = [row["b"] for row in rows if row["b"] is not None]
        self.assertGreater(sum(a_values) / len(a_values), sum(b_values) / len(b_values))
        self.assertEqual([("a",)], sqlite_rows(ddl, result.generation.create_rows, query))

    def test_symbolic_generation_grouped_aggregate_case_generates_group_branch(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = "SELECT region, CASE WHEN SUM(points) > 10 THEN 'hi' ELSE 'lo' END FROM scores GROUP BY region"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, groups=2, rows_per_group=2, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        query_result = sqlite_rows(ddl, result.generation.create_rows, query)
        labels = {row[1] for row in query_result}
        self.assertIn("hi", labels)

    def test_symbolic_generation_order_by_case_limit_selects_lowest_case_value(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT, score INT);"
        query = "SELECT id FROM users ORDER BY CASE WHEN age > 21 THEN score ELSE age END LIMIT 1"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["users"]
        expected = min(
            rows,
            key=lambda row: row["score"] if row["age"] > 21 else row["age"],
        )["id"]
        self.assertEqual([(expected,)], sqlite_rows(ddl, result.generation.create_rows, query))

    def test_symbolic_generation_group_by_uses_varied_input_group_sizes(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = "SELECT region, COUNT(*) FROM scores GROUP BY region"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        grouped: dict[object, list[dict[str, object]]] = {}
        for row in snapshot_rows(result)["scores"]:
            grouped.setdefault(row["region"], []).append(row)
        sizes = sorted(len(rows) for rows in grouped.values())
        self.assertGreaterEqual(len(sizes), 3)
        self.assertTrue({1, 2, 3}.issubset(set(sizes)))

    def test_symbolic_generation_global_aggregate_remains_single_result_row(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT);"
        query = "SELECT COUNT(*) FROM scores"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual(1, len(result.generation.root_schema.rows))

    def test_symbolic_generation_count_column_witnesses_null_argument(self):
        ddl = "CREATE TABLE t (id INT PRIMARY KEY, a INT);"

        result = generate_query_database(
            ddl,
            "SELECT COUNT(a), COUNT(*) FROM t",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, rows_per_group=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["t"]
        self.assertTrue(any(row["a"] is None for row in rows))
        self.assertNotEqual(
            sqlite_rows(ddl, result.generation.create_rows, "SELECT COUNT(a) FROM t"),
            sqlite_rows(ddl, result.generation.create_rows, "SELECT COUNT(*) FROM t"),
        )

    def test_symbolic_generation_count_distinct_witnesses_duplicate_argument(self):
        ddl = "CREATE TABLE t (id INT PRIMARY KEY, a INT);"

        result = generate_query_database(
            ddl,
            "SELECT COUNT(DISTINCT a), COUNT(a) FROM t",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, rows_per_group=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        values = [row["a"] for row in snapshot_rows(result)["t"] if row["a"] is not None]
        self.assertLess(len(set(values)), len(values))
        self.assertNotEqual(
            sqlite_rows(ddl, result.generation.create_rows, "SELECT COUNT(DISTINCT a) FROM t"),
            sqlite_rows(ddl, result.generation.create_rows, "SELECT COUNT(a) FROM t"),
        )

    def test_symbolic_generation_sum_witnesses_nullable_argument(self):
        ddl = "CREATE TABLE t (id INT PRIMARY KEY, a INT);"

        result = generate_query_database(
            ddl,
            "SELECT SUM(a) FROM t",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, rows_per_group=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        values = [row["a"] for row in snapshot_rows(result)["t"]]
        self.assertIn(None, values)
        self.assertTrue(any(value is not None for value in values))

    def test_symbolic_generation_having_count_preserves_row_with_null_argument(self):
        ddl = "CREATE TABLE t (id INT PRIMARY KEY, a INT);"
        query = "SELECT COUNT(a) FROM t HAVING COUNT(a) > 0"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, rows_per_group=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(sqlite_rows(ddl, result.generation.create_rows, query))
        self.assertTrue(any(row["a"] is None for row in snapshot_rows(result)["t"]))

    def test_symbolic_generation_having_count_distinct_preserves_duplicate_argument(self):
        ddl = "CREATE TABLE t (id INT PRIMARY KEY, a INT);"
        query = "SELECT COUNT(DISTINCT a) FROM t HAVING COUNT(DISTINCT a) > 0"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, rows_per_group=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(sqlite_rows(ddl, result.generation.create_rows, query))
        values = [row["a"] for row in snapshot_rows(result)["t"] if row["a"] is not None]
        self.assertLess(len(set(values)), len(values))

    def test_symbolic_generation_skips_null_stress_for_non_nullable_argument(self):
        ddl = "CREATE TABLE t (id INT PRIMARY KEY, a INT NOT NULL);"

        result = generate_query_database(
            ddl,
            "SELECT SUM(a) FROM t",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, rows_per_group=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertTrue(all(row["a"] is not None for row in snapshot_rows(result)["t"]))

    def test_symbolic_generation_skips_duplicate_stress_for_unique_argument(self):
        ddl = "CREATE TABLE t (id INT PRIMARY KEY, a INT UNIQUE);"

        result = generate_query_database(
            ddl,
            "SELECT COUNT(DISTINCT a), COUNT(a) FROM t",
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, rows_per_group=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        values = [row["a"] for row in snapshot_rows(result)["t"] if row["a"] is not None]
        self.assertEqual(len(values), len(set(values)))

    def test_aggregate_keys_distinguish_distinct_and_cast_inputs(self):
        plain = exp.Count(this=exp.column("x"))
        distinct = exp.Count(this=exp.Distinct(expressions=[exp.column("x")]))
        casted = exp.Count(
            this=exp.Cast(this=exp.column("x"), to=DataType.build("REAL"))
        )

        plain_key = _aggregate_key(plain, "sqlite")
        distinct_key = _aggregate_key(distinct, "sqlite")
        casted_key = _aggregate_key(casted, "sqlite")

        self.assertNotEqual(plain_key.name, distinct_key.name)
        self.assertNotEqual(plain_key.name, casted_key.name)

    def test_aggregate_expression_map_resolves_distinct_and_alias_independently(self):
        aggregate = Aggregate()
        plain = exp.Count(this=exp.column("x"))
        distinct = exp.Alias(
            this=exp.Count(this=exp.Distinct(expressions=[exp.column("x")])),
            alias=exp.to_identifier("distinct_x"),
        )
        aggregate.aggregations = [plain, distinct]

        mapping = _aggregate_expression_map(aggregate, "sqlite")

        self.assertIs(mapping[_aggregate_key(plain, "sqlite").name.casefold()], plain)
        self.assertIs(
            mapping[_aggregate_key(distinct, "sqlite").name.casefold()],
            distinct.this,
        )
        self.assertIs(mapping["distinct_x"], distinct.this)

    def test_symbolic_generation_keeps_cast_count_and_distinct_count_columns(self):
        ddl = """
        CREATE TABLE event (event_id TEXT PRIMARY KEY);
        CREATE TABLE attendance (
            link_to_event TEXT,
            link_to_member TEXT,
            FOREIGN KEY(link_to_event) REFERENCES event(event_id)
        );
        """
        query = """
        SELECT CAST(COUNT(T2.link_to_event) AS REAL) / COUNT(DISTINCT T2.link_to_event)
        FROM attendance AS T2
        """

        result = generate(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=2, rows_per_group=2, max_iterations=0),
        )

        self.assertTrue(result.generation.root_schema.rows)
        self.assertTrue(
            all(
                len(row.column_values) == len(result.generation.root_schema.columns)
                for row in result.generation.root_schema.rows
            )
        )

        source = exp.column("link_to_event", table="t2")
        plain = exp.Count(this=source.copy())
        distinct = exp.Count(this=source.copy(), distinct=True)
        aggregate = Aggregate()
        aggregate.aggregations = [plain, distinct]
        child = DerivedSchema(
            columns=(source,),
            rows=[
                Row(this=("r1",), columns={source: "e1"}),
                Row(this=("r2",), columns={source: "e1"}),
                Row(this=("r3",), columns={source: "e2"}),
            ],
        )

        aggregate_schema = AggregateEncodeStep(aggregate).forward(child)

        self.assertEqual(2, len(aggregate_schema.columns))
        self.assertEqual(2, len(aggregate_schema.rows[0].column_values))

    def test_symbolic_generation_passes_referenced_check_constraints_to_problem(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT CHECK (age > 0));"
        query = "SELECT id FROM users WHERE age > 21"

        result = generate(ddl, query, dialect="sqlite", bounds=BmcBounds(max_iterations=0))

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

        result = generate(ddl, query, dialect="sqlite", bounds=BmcBounds(max_iterations=0))

        self.assertFalse(result.generation.root_schema.rows)
        rows = snapshot_rows(result).get("users", [])
        self.assertTrue(all(row["age"] >= 0 for row in rows))

    def test_symbolic_generation_passes_referenced_unique_constraints_to_problem(self):
        ddl = "CREATE TABLE users (id INT PRIMARY KEY, age INT UNIQUE);"
        query = "SELECT id FROM users WHERE age > 30"

        result = generate(ddl, query, dialect="sqlite", bounds=BmcBounds(max_iterations=0))

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

    def test_solver_row_schema_constraints_include_batch_unique_keys(self):
        ddl = "CREATE TABLE bond (bond_id TEXT PRIMARY KEY, bond_type TEXT);"
        instance = Instance(ddl, name="unique_batch", dialect="sqlite")
        table = exp.to_table("bond")
        left = {
            "bond_id": SolverVar(key="left.bond_id"),
            "bond_type": SolverVar(key="left.bond_type"),
        }
        right = {
            "bond_id": SolverVar(key="right.bond_id"),
            "bond_type": SolverVar(key="right.bond_type"),
        }

        constraints = _schema_constraints_for_solver_rows(
            instance,
            table,
            (left, right),
            (set(), set()),
        )

        self.assertTrue(
            any(
                constraint.find(exp.NEQ) is not None
                and left["bond_id"] in set(constraint.find_all(SolverVar))
                and right["bond_id"] in set(constraint.find_all(SolverVar))
                for constraint in constraints
            )
        )

    def test_solver_rows_do_not_assign_null_to_non_nullable_composite_pk_columns(self):
        ddl = """
        CREATE TABLE laboratory (
            id INTEGER NOT NULL,
            date DATE NOT NULL,
            gpt INTEGER NULL,
            PRIMARY KEY (id, date)
        );
        """
        instance = Instance(ddl, name="composite_pk_non_null", dialect="sqlite")
        table = instance.resolve_table("laboratory")

        rows = _solve_table_rows(
            instance,
            table,
            ({"gpt": 61}, {"gpt": 62}),
            ((), ()),
            dialect="sqlite",
        )

        self.assertIsNotNone(rows)
        self.assertTrue(all(row.get("date") is not None for row in rows or ()))

    def test_solver_rows_use_distinct_dates_for_same_id_composite_primary_key(self):
        ddl = """
        CREATE TABLE laboratory (
            id INTEGER NOT NULL,
            date DATE NOT NULL,
            gpt INTEGER NULL,
            PRIMARY KEY (id, date)
        );
        """
        instance = Instance(ddl, name="composite_pk_same_id_dates", dialect="sqlite")
        table = instance.resolve_table("laboratory")

        rows = _solve_table_rows(
            instance,
            table,
            ({"id": 1, "gpt": 61}, {"id": 1, "gpt": 62}, {"id": 1, "gpt": 63}),
            ((), (), ()),
            dialect="sqlite",
        )

        self.assertIsNotNone(rows)
        dates = [row.get("date") for row in rows or ()]
        self.assertTrue(all(value is not None for value in dates))
        self.assertEqual(len(dates), len(set(dates)))

    def test_solver_database_constraints_skip_unrelated_non_nullable_columns(self):
        ddl = """
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY,
            required_label TEXT NOT NULL,
            optional_score INTEGER
        );
        """
        instance = Instance(ddl, name="skip_unrelated_non_null", dialect="sqlite")
        table = instance.resolve_table("tasks")
        sv_map = {
            "id": SolverVar(key="tasks.id"),
            "required_label": SolverVar(key="tasks.required_label"),
            "optional_score": SolverVar(key="tasks.optional_score"),
        }

        constraints = _database_constraints_for_solver(
            instance,
            table,
            sv_map,
            set(),
        )

        self.assertEqual([], constraints)

    def test_solver_database_constraints_include_existing_unique_keys_without_predicate_reference(self):
        ddl = "CREATE TABLE district (district_id INT PRIMARY KEY, a12 REAL, a13 REAL);"
        instance = Instance(ddl, name="unique_existing", dialect="sqlite")
        table = exp.to_table("district")
        instance.create_rows({table: [{"district_id": 2, "a12": 80.0, "a13": 38.0}]})
        sv_map = {
            "district_id": SolverVar(key="new.district_id"),
            "a12": SolverVar(key="new.a12"),
            "a13": SolverVar(key="new.a13"),
        }

        constraints = _database_constraints_for_solver(
            instance,
            table,
            sv_map,
            {"a12", "a13"},
        )

        self.assertTrue(
            any(
                constraint.find(exp.NEQ) is not None
                and sv_map["district_id"] in set(constraint.find_all(SolverVar))
                for constraint in constraints
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

        result = generate(ddl, query, dialect="sqlite", bounds=BmcBounds(max_iterations=0))

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
        query_result = sqlite_rows(ddl, result.generation.create_rows, query)
        self.assertGreaterEqual(len(query_result), 1)
        averages = [sum(points) / len(points) for points in grouped.values()]
        self.assertTrue(any(average > 10 for average in averages))
        self.assertTrue(any(average <= 10 for average in averages))
        averages = [sum(points) / len(points) for points in grouped.values()]
        self.assertTrue(any(avg > 10 for avg in averages))
        self.assertTrue(any(avg <= 10 for avg in averages))

    def test_symbolic_generation_having_count_demands_multi_row_group(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = """
        SELECT region, COUNT(*)
        FROM scores
        GROUP BY region
        HAVING COUNT(*) > 1
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["scores"]
        grouped: dict[object, list[dict[str, object]]] = {}
        for row in rows:
            grouped.setdefault(row["region"], []).append(row)
        self.assertTrue(any(len(group) >= 2 for group in grouped.values()))
        self.assertGreaterEqual(len(result.generation.root_schema.rows), 1)

    def test_symbolic_generation_group_bounds_materialize_group_sizes(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = """
        SELECT region, COUNT(*)
        FROM scores
        GROUP BY region
        HAVING COUNT(*) > 1
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(groups=3, rows_per_group=2, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["scores"]
        grouped: dict[object, list[dict[str, object]]] = {}
        for row in rows:
            grouped.setdefault(row["region"], []).append(row)
        self.assertGreaterEqual(len(grouped), 3)
        self.assertGreaterEqual(
            sum(1 for group in grouped.values() if len(group) >= 2),
            3,
        )

    def test_symbolic_generation_having_ratio_demands_passing_group(self):
        ddl = "CREATE TABLE scores (region TEXT, id INT, points INT);"
        query = """
        SELECT region, SUM(points) / COUNT(id)
        FROM scores
        GROUP BY region
        HAVING SUM(points) / COUNT(id) > 400
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["scores"]
        grouped: dict[object, list[dict[str, object]]] = {}
        for row in rows:
            grouped.setdefault(row["region"], []).append(row)
        ratios = [
            sum(row["points"] for row in group)
            / sum(1 for row in group if row["id"] is not None)
            for group in grouped.values()
            if any(row["id"] is not None for row in group)
        ]
        self.assertTrue(any(ratio > 400 for ratio in ratios))
        self.assertGreaterEqual(len(result.generation.root_schema.rows), 1)

    def test_symbolic_generation_join_having_uses_multiple_rows_per_group(self):
        ddl = """
        CREATE TABLE regions (id INT PRIMARY KEY, name TEXT);
        CREATE TABLE scores (id INT PRIMARY KEY, region_id INT, points INT);
        """
        query = """
        SELECT regions.name, COUNT(*)
        FROM regions
        JOIN scores ON regions.id = scores.region_id
        GROUP BY regions.name
        HAVING COUNT(*) > 1
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)
        self.assertGreaterEqual(len(rows["scores"]), 2)
        self.assertTrue(
            any(
                sum(1 for score in rows["scores"] if score["region_id"] == region["id"]) >= 2
                for region in rows["regions"]
            )
        )

    def test_symbolic_generation_seeds_scalar_avg_subquery_comparison(self):
        ddl = """
        CREATE TABLE outer_scores (id INT PRIMARY KEY, points INT);
        CREATE TABLE inner_scores (id INT PRIMARY KEY, points INT);
        """
        query = """
        SELECT id
        FROM outer_scores
        WHERE points > (SELECT AVG(points) FROM inner_scores)
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertGreaterEqual(len(result.generation.root_schema.rows), 1)
        rows = snapshot_rows(result)
        outer_points = [row["points"] for row in rows["outer_scores"]]
        inner_points = [row["points"] for row in rows["inner_scores"]]
        inner_avg = sum(inner_points) / len(inner_points)
        self.assertTrue(any(point > inner_avg for point in outer_points))

    def test_scalar_aggregate_subquery_comparison_does_not_lower_null_predicate(self):
        ddl = """
        CREATE TABLE outer_scores (id INT PRIMARY KEY, points INT);
        CREATE TABLE inner_scores (id INT PRIMARY KEY, points INT);
        """
        query = """
        SELECT id
        FROM outer_scores
        WHERE points > (SELECT AVG(points) FROM inner_scores)
        """
        observed_null_comparisons: list[exp.Expression] = []
        original_solve = symbolic_operator.Solver.solve

        def record_constraints(solver, problem):
            for constraint in problem.constraints:
                for comparison in constraint.find_all(exp.GT):
                    if comparison.expression.find(exp.Null):
                        observed_null_comparisons.append(comparison)
            return original_solve(solver, problem)

        with patch.object(symbolic_operator.Solver, "solve", record_constraints):
            result = generate_query_database(
                ddl,
                query,
                dialect="sqlite",
                bounds=BmcBounds(max_iterations=0),
            )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertFalse(
            observed_null_comparisons,
            observed_null_comparisons,
        )

    def test_symbolic_generation_seeds_joined_scalar_avg_subquery_comparison(self):
        ddl = """
        CREATE TABLE schools (cdscode INT PRIMARY KEY, school_name TEXT);
        CREATE TABLE frpm (
            cdscode INT PRIMARY KEY,
            funding_type TEXT,
            free_meal_count INT,
            enrollment INT
        );
        """
        query = """
        SELECT s.cdscode
        FROM schools AS s
        JOIN frpm AS f ON s.cdscode = f.cdscode
        WHERE f.funding_type = 'Locally funded'
          AND (f.free_meal_count - f.enrollment) > (
              SELECT AVG(f2.free_meal_count - f2.enrollment)
              FROM frpm AS f2
              JOIN schools AS s2 ON s2.cdscode = f2.cdscode
          )
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)
        sqlite_result = sqlite_rows(ddl, rows, query)
        self.assertGreater(len(sqlite_result), 0)
        diffs = [
            row["free_meal_count"] - row["enrollment"]
            for row in rows["frpm"]
        ]
        scalar_avg = sum(diffs) / len(diffs)
        self.assertIsNotNone(scalar_avg)
        self.assertTrue(
            any(
                row["funding_type"] == "Locally funded"
                and row["free_meal_count"] - row["enrollment"] > scalar_avg
                for row in rows["frpm"]
            )
        )

    def test_symbolic_generation_seeds_scalar_order_limit_comparison(self):
        ddl = """
        CREATE TABLE outer_scores (id INT PRIMARY KEY, points INT);
        CREATE TABLE inner_scores (id INT PRIMARY KEY, points INT);
        """
        query = """
        SELECT id
        FROM outer_scores
        WHERE points < (
            SELECT points FROM inner_scores ORDER BY points DESC LIMIT 1
        )
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertGreaterEqual(len(result.generation.root_schema.rows), 1)
        rows = snapshot_rows(result)
        top_inner = max(row["points"] for row in rows["inner_scores"])
        self.assertTrue(any(row["points"] < top_inner for row in rows["outer_scores"]))

    def test_symbolic_generation_covers_nested_having_under_outer_filter(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = """
        SELECT region, total
        FROM (
            SELECT region, SUM(points) AS total
            FROM scores
            GROUP BY region
            HAVING SUM(points) > 10
        ) AS grouped
        WHERE total < 100
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        self.assertGreaterEqual(len(result.generation.root_schema.rows), 1)
        rows = snapshot_rows(result)["scores"]
        grouped = {}
        for row in rows:
            grouped.setdefault(row["region"], 0)
            grouped[row["region"]] += row["points"]
        self.assertTrue(any(10 < total < 100 for total in grouped.values()))
        self.assertTrue(any(total <= 10 for total in grouped.values()))

    def test_symbolic_generation_ranks_descending_limit_selected_and_competitor(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT);"
        query = "SELECT id FROM scores ORDER BY points DESC LIMIT 1"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["scores"]
        self.assertGreaterEqual(len(rows), 2)
        ordered = sorted(rows, key=lambda row: row["points"], reverse=True)
        self.assertEqual(
            [row["id"] for row in ordered[:1]],
            projected_ids(result.generation.root_schema.rows),
        )
        obligations = {
            (obligation.kind, obligation.target): obligation.status
            for obligation in result.generation.obligations
        }
        self.assertEqual("covered", obligations[("ordering", "selected")])
        self.assertEqual("covered", obligations[("ordering", "excluded_competitor")])
        self.assertNotIn(("limit_window", "selected"), obligations)

    def test_symbolic_generation_ranks_offset_window(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT);"
        query = "SELECT id FROM scores ORDER BY points ASC LIMIT 2 OFFSET 1"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["scores"]
        self.assertGreaterEqual(len(rows), 3)
        ordered = sorted(rows, key=lambda row: row["points"])
        self.assertEqual(
            [row["id"] for row in ordered[1:3]],
            projected_ids(result.generation.root_schema.rows),
        )
        obligations = {
            (obligation.kind, obligation.target): obligation.status
            for obligation in result.generation.obligations
        }
        self.assertEqual("covered", obligations[("limit_window", "offset_skipped")])

    def test_symbolic_generation_records_order_rank_tie(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT);"
        query = "SELECT id FROM scores ORDER BY points DESC LIMIT 1"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        rows = snapshot_rows(result)["scores"]
        top_points = max(row["points"] for row in rows)
        self.assertGreaterEqual(
            sum(1 for row in rows if row["points"] == top_points),
            2,
        )
        obligations = {
            (obligation.kind, obligation.target): obligation.status
            for obligation in result.generation.obligations
        }
        self.assertEqual("covered", obligations[("ordering", "rank_tie")])

    def test_symbolic_generation_ranks_derived_physical_expression(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, points INT, total INT);"
        query = "SELECT id FROM scores ORDER BY points / total DESC LIMIT 1"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["scores"]
        self.assertGreaterEqual(len(rows), 2)
        ordered = sorted(
            rows,
            key=lambda row: row["points"] / row["total"],
            reverse=True,
        )
        self.assertEqual(
            [row["id"] for row in ordered[:1]],
            projected_ids(result.generation.root_schema.rows),
        )

    def test_symbolic_generation_materializes_limit_rows_for_filtered_derived_order(self):
        ddl = """
        CREATE TABLE frpm (
            id INT PRIMARY KEY,
            educational_option_type TEXT,
            free_meal_count REAL,
            enrollment REAL
        );
        """
        query = """
        SELECT free_meal_count / enrollment
        FROM frpm
        WHERE educational_option_type = 'Continuation School'
          AND free_meal_count / enrollment IS NOT NULL
        ORDER BY free_meal_count / enrollment ASC
        LIMIT 3
        """

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        query_result = sqlite_rows(ddl, result.generation.create_rows, query)
        self.assertEqual(3, len(query_result))

    def test_symbolic_generation_orders_mixed_python_values_for_text_keys(self):
        ddl = "CREATE TABLE scores (id INT PRIMARY KEY, label TEXT);"
        query = "SELECT id FROM scores ORDER BY label DESC LIMIT 1"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.generation.status, result.generation.reason)
        rows = snapshot_rows(result)["scores"]
        self.assertGreaterEqual(len(rows), 2)
        ordered = sorted(rows, key=lambda row: str(row["label"]), reverse=True)
        self.assertEqual(
            [row["id"] for row in ordered[:1]],
            projected_ids(result.generation.root_schema.rows),
        )

    def test_symbolic_generation_reports_aggregate_order_key_unsupported(self):
        ddl = "CREATE TABLE scores (region TEXT, points INT);"
        query = "SELECT region, SUM(points) FROM scores GROUP BY region ORDER BY SUM(points) DESC LIMIT 1"

        result = generate_query_database(
            ddl,
            query,
            dialect="sqlite",
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        obligations = {
            (obligation.kind, obligation.target): obligation.status
            for obligation in result.generation.obligations
        }
        self.assertEqual("unsupported", obligations[("ordering", "selected")])


if __name__ == "__main__":
    unittest.main()
