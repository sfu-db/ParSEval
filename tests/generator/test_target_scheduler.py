from __future__ import annotations

from collections import Counter
from importlib import import_module
from unittest.mock import patch

import pytest

from parseval.generator import GenerationConfig
from parseval.generator.symbolic.generate import generate
from parseval.generator.symbolic.operator import EncodePipeline
from parseval.plan.explain import PlanError


generate_module = import_module("parseval.generator.symbolic.generate")


def test_scheduler_attempts_each_uncovered_target_at_most_once() -> None:
    ddl = "CREATE TABLE items (id INT PRIMARY KEY, value INT);"
    query = "SELECT id FROM items WHERE value > 10 ORDER BY value DESC LIMIT 1"
    attempts: Counter[str] = Counter()
    original = EncodePipeline._attempt_target

    def recording_attempt(self, target, cache):
        attempts[target.id] += 1
        return original(self, target, cache)

    with patch.object(EncodePipeline, "_attempt_target", recording_attempt):
        generated = generate(
            ddl,
            query,
            config=GenerationConfig(bootstrap_rows=1),
        )

    assert sum(len(generated.get_rows(table)) for table in generated.tables) > 0
    assert attempts
    assert max(attempts.values()) == 1


def test_scheduler_does_not_return_generation_state() -> None:
    ddl = """
    CREATE TABLE parent (id INT PRIMARY KEY);
    CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
    """
    query = """
    SELECT child.id
    FROM child
    JOIN parent ON child.parent_id = parent.id
    WHERE child.id > 10
    """

    generated = generate(
        ddl,
        query,
        config=GenerationConfig(bootstrap_rows=1),
    )
    assert not hasattr(generated, "generation")


def test_large_scalar_subquery_plan_is_target_bounded() -> None:
    ddl = """
    CREATE TABLE account (account_id INT PRIMARY KEY, district_id INT);
    CREATE TABLE district (district_id INT PRIMARY KEY, a11 INT);
    CREATE TABLE client (
        client_id INT PRIMARY KEY,
        district_id INT,
        gender TEXT,
        birth_date TEXT
    );
    """
    query = """
    SELECT a.account_id,
           (SELECT MAX(a11) - MIN(a11) FROM district)
    FROM account AS a
    JOIN district AS d ON a.district_id = d.district_id
    WHERE d.district_id = (
        SELECT district_id
        FROM client
        WHERE gender = 'F'
        ORDER BY birth_date ASC
        LIMIT 1
    )
    ORDER BY d.a11 DESC
    LIMIT 1
    """

    attempts = 0
    original = EncodePipeline._attempt_target

    def recording_attempt(self, target, cache):
        nonlocal attempts
        attempts += 1
        return original(self, target, cache)

    config = GenerationConfig(bootstrap_rows=1)
    with patch.object(EncodePipeline, "_attempt_target", recording_attempt):
        generated = generate(ddl, query, config=config)

    assert sum(len(generated.get_rows(table)) for table in generated.tables) > 0
    assert attempts < 100


def test_expected_planning_failure_returns_bootstrap_instance() -> None:
    ddl = "CREATE TABLE items (id INT PRIMARY KEY);"

    with patch.object(
        generate_module,
        "explain",
        side_effect=PlanError("unsupported scalar lowering"),
    ):
        generated = generate(ddl, "SELECT id FROM items", dialect="sqlite")

    assert not hasattr(generated, "generation")
    assert sum(len(generated.get_rows(table)) for table in generated.tables) > 0


def test_internal_generator_errors_are_not_hidden_as_planning_failures() -> None:
    ddl = "CREATE TABLE items (id INT PRIMARY KEY);"

    with patch.object(
        generate_module,
        "explain",
        side_effect=IndexError("internal bug"),
    ):
        with pytest.raises(IndexError, match="internal bug"):
            generate(ddl, "SELECT id FROM items", dialect="sqlite")
