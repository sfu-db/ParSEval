"""End-to-end test: generate database → execute query → verify non-empty results.

Uses direct SQLite (no SQLAlchemy) with pre-created schemas for speed.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest

from parseval.instance import Instance
from parseval.symbolic import SymbolicEngine, CoverageThresholds
from tqdm import tqdm

BIRD_SCHEMA_FP = "data/sqlite/schema.json"
BIRD_SQLITE_DEV_FP = "data/sqlite/dev.json"


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
        with open(BIRD_SQLITE_DEV_FP) as f:
            cls.bird_dev = json.load(f)
        with open(BIRD_SCHEMA_FP) as f:
            cls.bird_schema = json.load(f)

    def test_first_100_queries(self):
        successes = 0
        failures = []
        total = min(len(self.bird_dev), 1600)

        for i, row in tqdm(enumerate(self.bird_dev[:total]), total=total, desc="Testing BIRD queries"):
            db_id = row["db_id"]
            sql = row["SQL"]
            ddls = ";".join(self.bird_schema[db_id])

            try:
                instance = Instance(ddls=ddls, name=f"{db_id}_{i}", dialect="sqlite")
                engine = SymbolicEngine(
                    instance, sql, dialect="sqlite", max_iterations=5
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
        print(f"Non-empty results: {successes}/{total} ({successes*100//total}%)")
        if failures:
            print(f"Failures ({len(failures)}):")
            for idx, db_id, sql_preview, reason in failures[:]:
                print(f"  [{idx}] {db_id} {sql_preview}: {reason}")
        print(f"{'='*60}")

        self.assertGreater(successes, total * 0.8,
            f"Only {successes}/{total} queries returned non-empty results")


if __name__ == "__main__":
    unittest.main()
