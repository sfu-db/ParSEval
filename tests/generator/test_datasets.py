"""Generated databases make real-world dataset queries return rows.

A few representative statements from BIRD-dev and postgres.csv are generated
in-process, exported to SQLite and replayed by SQLite itself. Full runs use
scripts/benchmark_bird_dev.py and scripts/audit_postgres_dataset.py.
"""

import csv
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from audit_postgres_dataset import replay_sqlite  # noqa: E402
from experiments.sqlite import write_sqlite  # noqa: E402

from parseval import Catalog, GenerationConfig, generate  # noqa: E402

BIRD = ROOT / "data" / "sqlite"
# Question 563 compares a SQLite DATETIME column with the text '...:39.0';
# SQLite compares text there, while the frontend types the column TIMESTAMP.
BIRD_QUESTIONS = ("0", "1", "66", "201", "942")
POSTGRES_STATEMENTS = (("dsb", "9", "q2"),)


def _bird_cases():
    schemas = {db: ";\n".join(ddl) for db, ddl in json.loads((BIRD / "schema.json").read_text()).items()}
    for item in json.loads((BIRD / "dev.json").read_text()):
        if str(item["question_id"]) in BIRD_QUESTIONS:
            yield pytest.param(schemas[item["db_id"]], item["SQL"], "sqlite", id=f"bird-{item['question_id']}")


def _postgres_cases():
    with (ROOT / "data" / "postgres.csv").open() as stream:
        for row in csv.DictReader(stream):
            for query in ("q1", "q2"):
                if (row["dbid"], row["index"], query) in POSTGRES_STATEMENTS:
                    yield pytest.param(
                        row["schema_ddl"], row[query], row["dialect"], id=f"{row['dbid']}-{row['index']}-{query}"
                    )


@pytest.mark.parametrize("ddl,sql,dialect", [*_bird_cases(), *_postgres_cases()])
def test_generated_database_returns_rows(ddl, sql, dialect, tmp_path):
    catalog = Catalog.from_ddl(ddl, dialect=dialect)
    result = generate(sql, catalog, config=GenerationConfig(timeout_ms=3000))
    assert result.nonempty
    path = write_sqlite(result.instance, tmp_path / "instance.sqlite")
    replay = replay_sqlite(path, sql, dialect, 10)
    assert replay["status"] == "ok", replay.get("error")
    assert replay["nonempty"]
