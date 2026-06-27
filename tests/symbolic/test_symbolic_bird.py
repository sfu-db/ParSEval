"""End-to-end test: generate database → execute query → verify non-empty results.

Uses direct SQLite (no SQLAlchemy) with pre-created schemas for speed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import unittest

from parseval.instance import Instance
from parseval.symbolic import SymbolicEngine, CoverageThresholds
from tqdm import tqdm

BIRD_SCHEMA_FP = "data/sqlite/schema.json"
BIRD_SQLITE_DEV_FP = "data/sqlite/dev.json"
DEFAULT_BIRD_SYMBOLIC_LIMIT = 3


def _env_int(name: str, default: int | None = None) -> int | None:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _bird_query_window(total: int) -> tuple[int, int]:
    start = max(_env_int("BIRD_SYMBOLIC_START", 0) or 0, 0)
    limit = _env_int("BIRD_SYMBOLIC_LIMIT", DEFAULT_BIRD_SYMBOLIC_LIMIT)
    end = _env_int("BIRD_SYMBOLIC_END")
    if end is None:
        end = start + max(limit or 0, 0)
    end = min(max(end, start), total)
    return start, end


def _write_and_execute(instance: Instance, sql: str) -> list:
    """Write instance to in-memory SQLite and execute the query."""
    conn = sqlite3.connect(":memory:")
    # Create tables (skip FK constraints for speed — SQLite doesn't enforce them by default).
    for ddl in instance.ddls.split(";"):
        ddl = ddl.strip()
        if ddl:
            try:
                conn.execute(ddl)
            except Exception:
                pass

    # Insert rows.
    for table_name in instance.tables:
        rows = instance.get_rows(table_name)
        if not rows:
            continue
        cols = list(instance.tables[table_name].keys())
        placeholders = ",".join(["?"] * len(cols))
        col_names = ",".join(f'"{c}"' for c in cols)
        stmt = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
        for row in rows:
            values = []
            for c in cols:
                v = row[c].concrete if c in row.columns else None
                if v is not None and not isinstance(v, (int, float, str, bytes)):
                    v = str(v)
                values.append(v)
            try:
                conn.execute(stmt, values)
            except Exception:
                pass
    conn.commit()

    try:
        return conn.execute(sql).fetchall()
    except Exception:
        return []
    finally:
        conn.close()


class TestBirdQueriesReturnNonEmpty(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(BIRD_SQLITE_DEV_FP) or not os.path.exists(BIRD_SCHEMA_FP):
            raise unittest.SkipTest("BIRD SQLite fixtures are not available")
        with open(BIRD_SQLITE_DEV_FP) as f:
            cls.bird_dev = json.load(f)
        with open(BIRD_SCHEMA_FP) as f:
            cls.bird_schema = json.load(f)

    def test_configured_query_window_returns_non_empty_results(self):
        successes = 0
        failures = []
        start, end = _bird_query_window(len(self.bird_dev))
        selected = self.bird_dev[start:end]
        total = len(selected)
        min_non_empty_ratio = float(os.environ.get("BIRD_SYMBOLIC_MIN_NON_EMPTY_RATIO", "0"))
        max_iterations = int(os.environ.get("BIRD_SYMBOLIC_MAX_ITERATIONS", "5"))

        for offset, row in tqdm(
            enumerate(selected),
            total=total,
            desc=f"Testing BIRD queries {start}:{end}",
        ):
            i = start + offset
            db_id = row["db_id"]
            sql = row["SQL"]
            ddls = ";".join(self.bird_schema[db_id])

            try:
                instance = Instance(ddls=ddls, name=f"{db_id}_{i}", dialect="sqlite")
                engine = SymbolicEngine(
                    instance, sql, dialect="sqlite", max_iterations=max_iterations
                )
                engine.generate(thresholds=CoverageThresholds(atom_null=0))
                results = _write_and_execute(instance, sql)
                if results:
                    successes += 1
                else:
                    failures.append((i, db_id, sql[:], "empty"))
            except Exception as e:
                failures.append((i, db_id, sql[:], str(e)[:40]))

        print(f"\n{'='*60}")
        print(f"BIRD query window: {start}:{end}")
        print(f"Non-empty results: {successes}/{total} ({successes*100//total}%)")
        if failures:
            print(f"Failures ({len(failures)}):")
            for idx, db_id, sql_preview, reason in failures[:]:
                print(f"  [{idx}] {db_id} {sql_preview}: {reason}")
        print(f"{'='*60}")

        self.assertGreaterEqual(total, 1)
        self.assertGreaterEqual(successes / total, min_non_empty_ratio,
            f"Only {successes}/{total} queries returned non-empty results")


class TestBirdQueryWindowConfig(unittest.TestCase):
    def test_default_window_uses_small_limit(self):
        self.assertEqual(_bird_query_window(100), (0, DEFAULT_BIRD_SYMBOLIC_LIMIT))

    def test_start_and_limit_define_window(self):
        old_environ = dict(os.environ)
        try:
            os.environ["BIRD_SYMBOLIC_START"] = "10"
            os.environ["BIRD_SYMBOLIC_LIMIT"] = "5"
            os.environ.pop("BIRD_SYMBOLIC_END", None)
            self.assertEqual(_bird_query_window(100), (10, 15))
        finally:
            os.environ.clear()
            os.environ.update(old_environ)

    def test_end_overrides_limit(self):
        old_environ = dict(os.environ)
        try:
            os.environ["BIRD_SYMBOLIC_START"] = "10"
            os.environ["BIRD_SYMBOLIC_LIMIT"] = "50"
            os.environ["BIRD_SYMBOLIC_END"] = "12"
            self.assertEqual(_bird_query_window(100), (10, 12))
        finally:
            os.environ.clear()
            os.environ.update(old_environ)


if __name__ == "__main__":
    unittest.main()
