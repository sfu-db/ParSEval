"""Generated databases make real-world dataset queries return rows.

A few representative BIRD-dev questions are generated in-process, loaded into
a SQLite file and replayed by SQLite itself.
"""

import json
from pathlib import Path

import pytest

from parseval import DBManager, GenerationConfig, instantiate_db

BIRD = Path(__file__).resolve().parents[2] / "data" / "sqlite"
# Question 563 compares a SQLite DATETIME column with the text '...:39.0';
# SQLite compares text there, while the frontend types the column TIMESTAMP.
BIRD_QUESTIONS = ("0", "1", "66", "201", "942")


def _bird_cases():
    schemas = {db: ";\n".join(ddl) for db, ddl in json.loads((BIRD / "schema.json").read_text()).items()}
    for item in json.loads((BIRD / "dev.json").read_text()):
        if str(item["question_id"]) in BIRD_QUESTIONS:
            yield pytest.param(schemas[item["db_id"]], item["SQL"], id=f"bird-{item['question_id']}")


@pytest.mark.parametrize("ddl,sql", list(_bird_cases()))
def test_generated_database_returns_rows(ddl, sql, tmp_path):
    url = f"sqlite:///{tmp_path / 'instance.sqlite'}"
    result = instantiate_db(sql, ddl, url, "sqlite", config=GenerationConfig(timeout_ms=3000))
    assert result.nonempty
    with DBManager(url, "sqlite").connect() as connection:
        assert connection.execute(sql, timeout=10)
