from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Mapping, Sequence

from parseval.db_manager import DBManager
from parseval.generator import BmcBounds, generate_query_database
from parseval.instance import Instance
from parseval.instance.exporter import InstanceValueSerializer


DATA_DIR = Path("data/sqlite")
REALWORLD_WORKLOAD = (
    0,
    90,
    195,
    340,
    531,
    717,
    847,
    1020,
    1153,
    1312,
    1470,
)
CALIFORNIA_BASE_CAPABILITY_QIDS = (0, 1, 5, 8, 12, 13, 14)
CALIFORNIA_STRFTIME_CAST_QID = 27
CALIFORNIA_SCALAR_SUBQUERY_JOIN_QID = 28
CALIFORNIA_CONDITIONAL_RATIO_QID = 48
CALIFORNIA_TEXT_EQ_NUMERIC_MIN_QID = 74
CALIFORNIA_TRANSPARENT_ALIAS_QIDS = (66, 68, 72)
FINANCIAL_REFERENCED_KEY_QIDS = (139, 140)
FINANCIAL_REFERENCED_KEY_SLICE = tuple(range(139, 155))
FINANCIAL_SCALAR_SUBQUERY_QIDS = (94, 138)
TOXICOLOGY_TRIPLE_BOND_ELEMENTS_QID = 253


def schema_entry_to_ddl(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Sequence):
        parts = [str(stmt).strip().rstrip(";") for stmt in entry if str(stmt).strip()]
        return ";\n".join(parts) + (";" if parts else "")
    raise TypeError(f"unsupported_schema_entry:{type(entry)!r}")


def load_dev_case(question_id: int) -> tuple[str, Mapping[str, Any]]:
    queries = json.loads((DATA_DIR / "dev.json").read_text(encoding="utf-8"))
    schemas = json.loads((DATA_DIR / "schema.json").read_text(encoding="utf-8"))
    query = next(row for row in queries if row.get("question_id") == question_id)
    return schema_entry_to_ddl(schemas[query["db_id"]]), query


def materialize_rows(ddl: str, create_rows, *, dialect: str = "sqlite"):
    instance = Instance(ddl, name="query_rows", dialect=dialect)
    instance.create_rows(create_rows)
    serializer = InstanceValueSerializer()
    rows = {}
    for table in instance.snapshot().tables:
        if table.rows:
            rows[table.table_name] = [
                serializer.serialize_row(table.table_name, row) for row in table.rows
            ]
    return instance, rows


def query_rows(ddl: str, create_rows, query: str):
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "query_rows.sqlite"
        connection_string = f"sqlite:///{db_path}"
        instance, _ = materialize_rows(ddl, create_rows)
        instance.to_db(connection_string, dialect="sqlite")
        with DBManager().get_connection(connection_string, "sqlite") as conn:
            return conn.execute(query, fetch="all")


def generate_query_state(ddl: str, query: str, **kwargs):
    return generate_query_database(ddl, query, **kwargs).generation


class TestSqliteRealWorldGenerator(unittest.TestCase):
    def test_single_table_bird_query_generates_under_budget(self):
        ddl, query = load_dev_case(22)

        started = time.perf_counter()
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )
        elapsed = time.perf_counter() - started

        self.assertEqual("sat", result.status, result.reason)
        self.assertLess(elapsed, 2.0)
        rows = materialize_rows(ddl, result.create_rows)[1]
        self.assertIn("satscores", rows)

    def test_join_bird_query_generates_under_budget(self):
        ddl, query = load_dev_case(2)

        started = time.perf_counter()
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )
        elapsed = time.perf_counter() - started

        self.assertEqual("sat", result.status, result.reason)
        self.assertLess(elapsed, 2.0)
        rows = materialize_rows(ddl, result.create_rows)[1]
        self.assertIn("frpm", rows)
        self.assertIn("schools", rows)

    def test_quoted_identifier_bird_query_auto_raises_limit_budget(self):
        ddl, query = load_dev_case(1)

        started = time.perf_counter()
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )
        elapsed = time.perf_counter() - started

        self.assertEqual("sat", result.status, result.reason)
        self.assertEqual(3, result.bounds.table_rows)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))
        self.assertLess(elapsed, 2.0)

    def test_realworld_scalar_subquery_generates_under_budget(self):
        ddl, query = load_dev_case(8)

        started = time.perf_counter()
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )
        elapsed = time.perf_counter() - started

        self.assertEqual("sat", result.status, result.reason)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))
        self.assertLess(elapsed, 2.0)

    def test_offset_limit_queries_generate_non_empty(self):
        cases = (
            (31, 11, 2.0),
            (50, 6, 2.0),
            (57, 333, 90.0),
        )
        for question_id, expected_rows, budget in cases:
            with self.subTest(question_id=question_id):
                ddl, query = load_dev_case(question_id)
                started = time.perf_counter()
                result = generate_query_state(
                    ddl,
                    query["SQL"],
                    bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
                )
                elapsed = time.perf_counter() - started

                self.assertEqual("sat", result.status, result.reason)
                self.assertEqual(expected_rows, result.bounds.table_rows)
                rows = query_rows(ddl, result.create_rows, query["SQL"])
                self.assertTrue(rows)
                self.assertLess(elapsed, budget)

    def test_realworld_workload_completes_under_budget(self):
        total_started = time.perf_counter()
        seen_dbs: set[str] = set()

        for question_id in REALWORLD_WORKLOAD:
            ddl, query = load_dev_case(question_id)
            started = time.perf_counter()
            result = generate_query_state(
                ddl,
                query["SQL"],
                bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
            )
            elapsed = time.perf_counter() - started

            with self.subTest(question_id=question_id, db_id=query["db_id"]):
                self.assertIn(result.status, {"sat", "bounded_unknown", "unknown"})
                self.assertLess(elapsed, 2.0)
                seen_dbs.add(str(query["db_id"]))

        self.assertEqual(11, len(seen_dbs))
        self.assertLess(time.perf_counter() - total_started, 8.0)

    def test_california_base_capability_queries_validate_in_sqlite(self):

        for question_id in CALIFORNIA_BASE_CAPABILITY_QIDS:
            ddl, query = load_dev_case(question_id)
            result = generate_query_state(
                ddl,
                query["SQL"],
                bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
            )

            with self.subTest(question_id=question_id):
                self.assertEqual("sat", result.status, result.reason)
                instance = Instance(ddl, name="query_rows", dialect="sqlite")
                instance.create_rows(result.create_rows)
                self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))

    def test_california_strftime_cast_year_predicate_sat(self):
        ddl, query = load_dev_case(CALIFORNIA_STRFTIME_CAST_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
        )

        self.assertEqual("sat", result.status, result.reason)
        self.assertTrue(result.assignments)
        # OpenDate year > 1991 and ClosedDate year < 2000 must hold on assigned values.
        open_vals = [
            value
            for var, value in result.assignments.items()
            if "opendate" in var.var_key.lower()
        ]
        closed_vals = [
            value
            for var, value in result.assignments.items()
            if "closeddate" in var.var_key.lower()
        ]
        self.assertTrue(open_vals)
        self.assertTrue(closed_vals)
        self.assertTrue(all(getattr(v, "year", 0) > 1991 for v in open_vals))
        self.assertTrue(all(getattr(v, "year", 9999) < 2000 for v in closed_vals))

    def test_california_scalar_subquery_join_does_not_duplicate_school_keys(self):
        ddl, query = load_dev_case(CALIFORNIA_SCALAR_SUBQUERY_JOIN_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
        )

        self.assertEqual("sat", result.status, result.reason)
        rows = materialize_rows(ddl, result.create_rows)[1]
        cdscodes = [row["cdscode"] for row in rows["schools"]]
        self.assertEqual(len(cdscodes), len(set(cdscodes)))
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))

    def test_california_conditional_ratio_uses_distinct_case_rows(self):
        ddl, query = load_dev_case(CALIFORNIA_CONDITIONAL_RATIO_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
        )

        self.assertEqual("sat", result.status, result.reason)
        rows = materialize_rows(ddl, result.create_rows)[1]
        docs = {row["doc"] for row in rows["schools"]}
        self.assertIn("54", docs)
        self.assertIn("52", docs)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))

    def test_california_text_eq_numeric_with_min_text_sat(self):
        ddl, query = load_dev_case(CALIFORNIA_TEXT_EQ_NUMERIC_MIN_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))

    def test_california_transparent_alias_queries_validate_in_sqlite(self):

        for question_id in CALIFORNIA_TRANSPARENT_ALIAS_QIDS:
            ddl, query = load_dev_case(question_id)
            result = generate_query_state(
                ddl,
                query["SQL"],
                bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
            )

            with self.subTest(question_id=question_id):
                self.assertNotEqual(
                    ("unknown", "unsupported_smt_expression"),
                    (result.status, result.reason),
                )
                self.assertEqual("sat", result.status, result.reason)
                self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))

    def test_financial_referenced_key_queries_do_not_duplicate_keys(self):
        expected_keys = {
            139: ("disp", "disp_id"),
            140: ("district", "district_id"),
        }

        for question_id in FINANCIAL_REFERENCED_KEY_QIDS:
            ddl, query = load_dev_case(question_id)
            result = generate_query_state(
                ddl,
                query["SQL"],
                bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
            )

            with self.subTest(question_id=question_id):
                self.assertEqual("sat", result.status, result.reason)
                rows = materialize_rows(ddl, result.create_rows)[1]
                table, column = expected_keys[question_id]
                values = [row[column] for row in rows[table]]
                self.assertEqual(len(values), len(set(values)))
                self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))

    def test_financial_scalar_subquery_queries_validate_in_sqlite(self):

        for question_id in FINANCIAL_SCALAR_SUBQUERY_QIDS:
            ddl, query = load_dev_case(question_id)
            result = generate_query_state(
                ddl,
                query["SQL"],
                bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
            )

            with self.subTest(question_id=question_id):
                self.assertEqual("sat", result.status, result.reason)
                self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))

    def test_toxicology_quoted_schema_qualifier_query_validates_in_sqlite(self):
        ddl, query = load_dev_case(TOXICOLOGY_TRIPLE_BOND_ELEMENTS_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
        )

        self.assertEqual("sat", result.status, result.reason)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))

    def test_financial_referenced_key_slice_validates_in_sqlite(self):

        for question_id in FINANCIAL_REFERENCED_KEY_SLICE:
            ddl, query = load_dev_case(question_id)
            result = generate_query_state(
                ddl,
                query["SQL"],
                bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
            )

            with self.subTest(question_id=question_id):
                self.assertEqual("sat", result.status, result.reason)
                materialize_rows(ddl, result.create_rows)
                self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"]))


if __name__ == "__main__":
    unittest.main()
