from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass(frozen=True)
class DatasetExample:
    question_id: int
    db_id: str
    question: str
    evidence: str
    sql: str
    difficulty: Optional[str] = None


def _default_data_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data"


@lru_cache(maxsize=1)
def load_dev_examples(data_dir: Optional[str | Path] = None) -> List[DatasetExample]:
    root = Path(data_dir) if data_dir is not None else _default_data_dir()
    payload = json.loads((root / "dev.json").read_text())
    return [
        DatasetExample(
            question_id=row["question_id"],
            db_id=row["db_id"],
            question=row.get("question", ""),
            evidence=row.get("evidence", ""),
            sql=row["SQL"],
            difficulty=row.get("difficulty"),
        )
        for row in payload
    ]


@lru_cache(maxsize=1)
def load_schema_map(data_dir: Optional[str | Path] = None) -> Dict[str, List[str]]:
    root = Path(data_dir) if data_dir is not None else _default_data_dir()
    payload = json.loads((root / "schema.json").read_text())
    return {db_id: list(ddls) for db_id, ddls in payload.items()}


def get_schema_ddl(db_id: str, data_dir: Optional[str | Path] = None) -> str:
    schema_map = load_schema_map(data_dir)
    return ";".join(schema_map[db_id])


def iter_examples(
    db_id: Optional[str] = None,
    limit: Optional[int] = None,
    data_dir: Optional[str | Path] = None,
) -> Iterable[DatasetExample]:
    count = 0
    for example in load_dev_examples(data_dir):
        if db_id is not None and example.db_id != db_id:
            continue
        yield example
        count += 1
        if limit is not None and count >= limit:
            break
