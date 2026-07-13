from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from parseval.db_manager import DBManager


class TestSqlitePipelineScript(unittest.TestCase):
    def test_generates_instance_and_writes_sqlite_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            db_path = out_dir / "fixture.sqlite"
            summary_path = out_dir / "summary.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/generate_query_fixture.py",
                    "--start",
                    "22",
                    "--limit",
                    "1",
                    "--out-dir",
                    str(out_dir),
                    "--summary-json",
                    str(summary_path),
                    "--write-db",
                    "--workers",
                    "2",
                ],
                cwd=Path(__file__).resolve().parents[2],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertIn("[sat]", completed.stderr)
            self.assertRegex(completed.stderr, r"rows=[1-9]")
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(1, summary["total"])
            self.assertEqual(1, summary["sat"])
            self.assertEqual(0, summary["empty_result"])
            self.assertEqual(0, summary["validation_error"])
            row = summary["results"][0]
            self.assertEqual("sat", row["status"])
            self.assertGreater(row["validation_rows"], 0)
            self.assertEqual(1.0, row["coverage_ratio"])
            self.assertGreater(row["obligations_total"], 0)
            self.assertEqual(row["obligations_total"], row["obligations_covered"])
            self.assertEqual(0, row["obligations_unsupported"])
            self.assertEqual(0, row["obligations_infeasible"])

            db_path = Path(row["db_path"])
            self.assertTrue(db_path.is_file())
            with DBManager().get_connection(row["connection_string"], "sqlite") as conn:
                rows = conn.execute(row["sql"], fetch="all")
            self.assertGreater(len(rows), 0)

    def test_start_and_limit_select_dataset_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            summary_path = out_dir / "summary.json"

            completed = subprocess.run(
                [
                    sys.executable,
                    "scripts/generate_query_fixture.py",
                    "--start",
                    "22",
                    "--limit",
                    "2",
                    "--out-dir",
                    str(out_dir),
                    "--summary-json",
                    str(summary_path),
                    "--workers",
                    "2",
                ],
                cwd=Path(__file__).resolve().parents[2],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(0, completed.returncode, completed.stderr)
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(2, summary["total"])
            self.assertIn("coverage_ratio_avg", summary)
            self.assertIn("obligations_total", summary)
            self.assertEqual([22, 23], [row["dataset_index"] for row in summary["results"]])


if __name__ == "__main__":
    unittest.main()
