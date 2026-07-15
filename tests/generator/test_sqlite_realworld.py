from __future__ import annotations

import functools
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
CALIFORNIA_JOINED_TOPK_TEXT_NUMERIC_QID = 32
CALIFORNIA_JOINED_DUAL_FILTER_QID = 38
CALIFORNIA_TRANSPARENT_ALIAS_QIDS = (66, 68, 72)
FINANCIAL_REFERENCED_KEY_QIDS = (139, 140)
# 139 and 140 already get a dedicated, stronger assertion in
# test_financial_referenced_key_queries_do_not_duplicate_keys, so the broader
# slice below skips them to avoid solving the same query twice.
FINANCIAL_REFERENCED_KEY_SLICE = tuple(
    qid for qid in range(139, 155) if qid not in FINANCIAL_REFERENCED_KEY_QIDS
)
FINANCIAL_SCALAR_SUBQUERY_QIDS = (94, 138)
FINANCIAL_NESTED_JOIN_QID = 90
FINANCIAL_DISTINCT_AGGREGATE_CASE_QID = 91
TOXICOLOGY_TRIPLE_BOND_ELEMENTS_QID = 253
TOXICOLOGY_BOND_UNIQUE_QID = 215
CODEBASE_COMMUNITY_POST_OWNER_QID = 566
FORMULA_1_RENAULT_CONSTRUCTOR_QID = 851
# 27 and 28 get dedicated, stronger tests further down
# (test_california_strftime_cast_year_predicate_sat and
# test_california_scalar_subquery_join_does_not_duplicate_school_keys), so the
# generic empty-result slice only needs to cover the remaining ids.
CALIFORNIA_EMPTY_RESULT_SLICE_QIDS = (25, 26)


def schema_entry_to_ddl(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Sequence):
        parts = [str(stmt).strip().rstrip(";") for stmt in entry if str(stmt).strip()]
        return ";\n".join(parts) + (";" if parts else "")
    raise TypeError(f"unsupported_schema_entry:{type(entry)!r}")


@functools.lru_cache(maxsize=1)
def _load_dev_and_schema_raw() -> tuple[tuple[Mapping[str, Any], ...], Mapping[str, Any]]:
    """
    Reads and parses dev.json/schema.json exactly once for the whole test run
    instead of once per load_dev_case() call (previously ~40+ redundant reads
    and JSON parses across this file).
    """
    queries = json.loads((DATA_DIR / "dev.json").read_text(encoding="utf-8"))
    schemas = json.loads((DATA_DIR / "schema.json").read_text(encoding="utf-8"))
    return tuple(queries), schemas


@functools.lru_cache(maxsize=1)
def _query_by_question_id() -> Mapping[int, Mapping[str, Any]]:
    queries, _ = _load_dev_and_schema_raw()
    return {row["question_id"]: row for row in queries}


@functools.lru_cache(maxsize=1)
def _ddl_by_db_id() -> Mapping[str, str]:
    """
    Precomputes every schema's DDL string once. schema_entry_to_ddl() does
    string joins/strips that are cheap individually but pointless to redo
    every time the same db_id is requested across many tests.
    """
    _, schemas = _load_dev_and_schema_raw()
    return {db_id: schema_entry_to_ddl(entry) for db_id, entry in schemas.items()}


def load_dev_case(question_id: int) -> tuple[str, Mapping[str, Any]]:
    query = _query_by_question_id()[question_id]
    return _ddl_by_db_id()[str(query["db_id"])], query


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


def query_rows(ddl: str, create_rows, query: str, *, instance: Instance | None = None):
    """
    Writes an Instance to a temp sqlite file and executes `query` against it.

    If the caller already built an Instance (e.g. via materialize_rows) for
    its own assertions, pass it in via `instance=` so we don't construct and
    populate a second, throwaway Instance from the same create_rows.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "query_rows.sqlite"
        connection_string = f"sqlite:///{db_path}"
        if instance is None:
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
        instance, _ = materialize_rows(ddl, result.create_rows)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))
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
        instance, _ = materialize_rows(ddl, result.create_rows)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))
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
                instance, _ = materialize_rows(ddl, result.create_rows)
                rows = query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
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
                instance, _ = materialize_rows(ddl, result.create_rows)
                self.assertTrue(
                    query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
                )

    def test_california_empty_result_slice_validates_in_sqlite(self):
        for question_id in CALIFORNIA_EMPTY_RESULT_SLICE_QIDS:
            ddl, query = load_dev_case(question_id)
            result = generate_query_state(
                ddl,
                query["SQL"],
                bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=4),
            )

            with self.subTest(question_id=question_id):
                self.assertEqual("sat", result.status, result.reason)
                instance, _ = materialize_rows(ddl, result.create_rows)
                self.assertTrue(
                    query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
                )

    def test_symbolic_demand_regression_qids_generate_non_empty_sqlite_rows(self):
        for question_id in (25, 94, 95, 329, 349):
            ddl, query = load_dev_case(question_id)
            result = generate_query_state(
                ddl,
                query["SQL"],
                bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=4),
            )

            with self.subTest(question_id=question_id, db_id=query["db_id"]):
                self.assertEqual("sat", result.status, result.reason)
                self.assertGreater(len(result.root_schema.rows), 0)
                instance, _ = materialize_rows(ddl, result.create_rows)
                self.assertTrue(
                    query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
                )

    def test_financial_nested_join_coverage_preserves_numeric_key_types(self):
        ddl, query = load_dev_case(FINANCIAL_NESTED_JOIN_QID)

        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, max_iterations=4),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, _ = materialize_rows(ddl, result.create_rows)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))

    def test_financial_distinct_aggregate_case_validates_in_sqlite(self):
        ddl, query = load_dev_case(FINANCIAL_DISTINCT_AGGREGATE_CASE_QID)

        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, _ = materialize_rows(ddl, result.create_rows)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))

    def test_california_joined_filter_topk_validates_in_sqlite(self):
        for question_id in (
            CALIFORNIA_JOINED_TOPK_TEXT_NUMERIC_QID,
            CALIFORNIA_JOINED_DUAL_FILTER_QID,
        ):
            ddl, query = load_dev_case(question_id)
            result = generate_query_state(
                ddl,
                query["SQL"],
                bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
            )

            with self.subTest(question_id=question_id):
                self.assertEqual("sat", result.status, result.reason)
                instance, _ = materialize_rows(ddl, result.create_rows)
                self.assertTrue(
                    query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
                )

    def test_empty_aggregate_result_raises_bounds_for_cardinality(self):
        ddl = "CREATE TABLE t (x INTEGER);"
        query = "SELECT COUNT(*) FROM t HAVING COUNT(*) > 1"

        result = generate_query_state(
            ddl,
            query,
            bounds=BmcBounds(table_rows=1, max_iterations=4),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, _ = materialize_rows(ddl, result.create_rows)
        rows = query_rows(ddl, result.create_rows, query, instance=instance)
        self.assertTrue(rows)
        self.assertGreater(rows[0][0], 1)

    def test_california_strftime_cast_year_predicate_sat(self):
        ddl, query = load_dev_case(CALIFORNIA_STRFTIME_CAST_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, _ = materialize_rows(ddl, result.create_rows)
        rows = query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
        self.assertTrue(rows)
        self.assertTrue(all(int(row[3]) > 1991 for row in rows))
        self.assertTrue(all(int(row[4]) < 2000 for row in rows))

    def test_california_scalar_subquery_join_does_not_duplicate_school_keys(self):
        ddl, query = load_dev_case(CALIFORNIA_SCALAR_SUBQUERY_JOIN_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, rows = materialize_rows(ddl, result.create_rows)
        cdscodes = [row["cdscode"] for row in rows["schools"]]
        self.assertEqual(len(cdscodes), len(set(cdscodes)))
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))

    def test_california_conditional_ratio_uses_distinct_case_rows(self):
        ddl, query = load_dev_case(CALIFORNIA_CONDITIONAL_RATIO_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, rows = materialize_rows(ddl, result.create_rows)
        docs = {row["doc"] for row in rows["schools"]}
        self.assertIn("54", docs)
        self.assertIn("52", docs)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))

    def test_california_text_eq_numeric_with_min_text_sat(self):
        ddl, query = load_dev_case(CALIFORNIA_TEXT_EQ_NUMERIC_MIN_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, _ = materialize_rows(ddl, result.create_rows)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))

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
                instance, _ = materialize_rows(ddl, result.create_rows)
                self.assertTrue(
                    query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
                )

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
                instance, rows = materialize_rows(ddl, result.create_rows)
                table, column = expected_keys[question_id]
                values = [row[column] for row in rows[table]]
                self.assertEqual(len(values), len(set(values)))
                self.assertTrue(
                    query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
                )

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
                instance, _ = materialize_rows(ddl, result.create_rows)
                self.assertTrue(
                    query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
                )

    def test_toxicology_quoted_schema_qualifier_query_validates_in_sqlite(self):
        ddl, query = load_dev_case(TOXICOLOGY_TRIPLE_BOND_ELEMENTS_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=1),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, _ = materialize_rows(ddl, result.create_rows)
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))

    def test_toxicology_bond_query_does_not_duplicate_bond_key(self):
        ddl, query = load_dev_case(TOXICOLOGY_BOND_UNIQUE_QID)
        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, rows = materialize_rows(ddl, result.create_rows)
        bond_ids = [str(row["bond_id"]) for row in rows["bond"]]
        self.assertEqual(len(bond_ids), len(set(bond_ids)))
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))

    def test_codebase_community_post_owner_does_not_duplicate_post_key(self):
        ddl, query = load_dev_case(CODEBASE_COMMUNITY_POST_OWNER_QID)

        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, rows = materialize_rows(ddl, result.create_rows)
        post_ids = [row["id"] for row in rows["posts"]]
        self.assertEqual(len(post_ids), len(set(post_ids)))
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))

    def test_formula_1_renault_constructor_does_not_duplicate_unique_name(self):
        ddl, query = load_dev_case(FORMULA_1_RENAULT_CONSTRUCTOR_QID)

        result = generate_query_state(
            ddl,
            query["SQL"],
            bounds=BmcBounds(table_rows=1, order_competitors=1, max_iterations=0),
        )

        self.assertEqual("sat", result.status, result.reason)
        instance, rows = materialize_rows(ddl, result.create_rows)
        constructor_names = [row["name"] for row in rows["constructors"]]
        self.assertEqual(len(constructor_names), len(set(constructor_names)))
        self.assertTrue(query_rows(ddl, result.create_rows, query["SQL"], instance=instance))

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
                instance, _ = materialize_rows(ddl, result.create_rows)
                self.assertTrue(
                    query_rows(ddl, result.create_rows, query["SQL"], instance=instance)
                )


if __name__ == "__main__":
    unittest.main()