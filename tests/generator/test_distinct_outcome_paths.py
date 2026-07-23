from __future__ import annotations

import json
from pathlib import Path

import pytest

from parseval.generator import GenerationConfig, generate
from parseval.main import disprove


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("index", [5, 199, 556, 593, 1324])
def test_bird_distinct_join_outcome_paths_disprove(
    index: int,
    tmp_path: Path,
) -> None:
    schemas = json.loads((REPO_ROOT / "data/sqlite/schema.json").read_text())
    examples = json.loads((REPO_ROOT / "data/sqlite/dev.json").read_text())
    predictions = (REPO_ROOT / "data/sqlite/dail.txt").read_text().splitlines()
    example = examples[index]
    ddl = ";".join(schemas[example["db_id"]])

    result = disprove(
        example["SQL"],
        predictions[index],
        ddl,
        f"sqlite:///{tmp_path / f'{index}.db'}",
        "sqlite",
        semantics="bag",
        timeout=60,
    )

    assert result.verdict.value == "neq"


def test_parallel_non_key_distinct_path_is_reported() -> None:
    generated = generate(
        """
        CREATE TABLE parent (
            id INT PRIMARY KEY,
            label TEXT,
            active INT
        );
        CREATE TABLE child (
            id INT PRIMARY KEY,
            parent_id INT REFERENCES parent(id),
            kind TEXT
        );
        """,
        """
        SELECT COUNT(DISTINCT p.label)
        FROM child AS c
        JOIN parent AS p ON c.parent_id = p.id
        WHERE c.kind = 'x' AND p.active = 1
        """,
        dialect="sqlite",
    )

    parallel = [
        obligation
        for obligation in generated.generation.obligations
        if obligation.id.endswith(":parallel")
    ]
    assert parallel
    assert all(obligation.status == "covered" for obligation in parallel)


def test_nullable_count_argument_gets_its_own_group_outcome_path() -> None:
    generated = generate(
        """
        CREATE TABLE measurements (
            id INT PRIMARY KEY,
            required_value INT NOT NULL,
            optional_value INT
        );
        """,
        """
        SELECT COUNT(required_value), COUNT(optional_value)
        FROM measurements
        WHERE required_value > 0
        """,
        dialect="sqlite",
        config=GenerationConfig(bootstrap_negatives=False),
    )

    obligations = [
        obligation
        for obligation in generated.generation.obligations
        if obligation.target_id.endswith(".agg1.null_sensitive")
    ]
    assert obligations
    assert all(obligation.status == "covered" for obligation in obligations)
    rows = generated.get_rows("measurements")
    qualifying = [row for row in rows if row["required_value"].concrete > 0]
    assert any(row["optional_value"].concrete is None for row in qualifying)
    assert any(row["optional_value"].concrete is not None for row in qualifying)
    assert all(row["required_value"].concrete is not None for row in qualifying)
