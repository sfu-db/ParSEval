from __future__ import annotations

from collections import Counter
from importlib import import_module
from unittest.mock import patch

import pytest

from parseval.generator import GenerationConfig
from parseval.generator.symbolic.generate import generate
from parseval.generator.symbolic.operator import EncodePipeline
from parseval.generator.speculate import speculate
from parseval.plan.explain import PlanError, TableScan, explain


generate_module = import_module("parseval.generator.symbolic.generate")


def test_scheduler_compiles_each_outcome_path_at_most_once() -> None:
    ddl = "CREATE TABLE items (id INT PRIMARY KEY, value INT);"
    query = "SELECT id FROM items WHERE value > 10 ORDER BY value DESC LIMIT 1"
    attempts: Counter[str] = Counter()
    original = EncodePipeline._compile_outcome_path

    def recording_compile(self, target, cache, variant):
        attempts[f"{target.id}:{variant}"] += 1
        return original(self, target, cache, variant)

    with patch.object(EncodePipeline, "_compile_outcome_path", recording_compile):
        generated = generate(
            ddl,
            query,
            config=GenerationConfig(bootstrap_rows=1),
        )

    assert sum(len(generated.get_rows(table)) for table in generated.tables) > 0
    assert attempts
    assert max(attempts.values()) == 1


def test_scheduler_returns_generation_state() -> None:
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
    assert generated.generation.status in {"sat", "unknown"}
    assert generated.generation.obligations
    assert 0.0 <= generated.generation.coverage_ratio <= 1.0


def test_scalar_subquery_plan_is_target_bounded() -> None:
    ddl = """
    CREATE TABLE account (account_id INT PRIMARY KEY, district_id INT);
    CREATE TABLE district (district_id INT PRIMARY KEY, a11 INT);
    """
    query = """
    SELECT a.account_id,
           (SELECT MAX(a11) - MIN(a11) FROM district)
    FROM account AS a
    JOIN district AS d ON a.district_id = d.district_id
    WHERE d.a11 > 0
    ORDER BY d.a11 DESC
    LIMIT 1
    """

    attempts = 0
    original = EncodePipeline._compile_outcome_path

    def recording_compile(self, target, cache, variant):
        nonlocal attempts
        attempts += 1
        return original(self, target, cache, variant)

    config = GenerationConfig(bootstrap_rows=1)
    with patch.object(EncodePipeline, "_compile_outcome_path", recording_compile):
        generated = generate(ddl, query, config=config)

    assert sum(len(generated.get_rows(table)) for table in generated.tables) > 0
    assert attempts < 100


def test_planning_failure_is_not_replaced_with_bootstrap_data() -> None:
    ddl = "CREATE TABLE items (id INT PRIMARY KEY);"

    with patch.object(
        generate_module,
        "explain",
        side_effect=PlanError("unsupported scalar lowering"),
    ):
        with pytest.raises(PlanError, match="unsupported scalar lowering"):
            generate(ddl, "SELECT id FROM items", dialect="sqlite")


def test_internal_generator_errors_are_not_hidden_as_planning_failures() -> None:
    ddl = "CREATE TABLE items (id INT PRIMARY KEY);"

    with patch.object(
        generate_module,
        "explain",
        side_effect=IndexError("internal bug"),
    ):
        with pytest.raises(IndexError, match="internal bug"):
            generate(ddl, "SELECT id FROM items", dialect="sqlite")


def test_left_preserved_path_traverses_only_preserved_child() -> None:
    ddl = """
    CREATE TABLE parent (id INT PRIMARY KEY);
    CREATE TABLE child (
        id INT PRIMARY KEY,
        parent_id INT REFERENCES parent(id)
    );
    """
    query = """
    SELECT parent.id, child.id
    FROM parent
    LEFT JOIN child ON parent.id = child.parent_id
    """
    plan = explain(ddl, query, dialect="sqlite")
    instance = speculate(ddl, query, dialect="sqlite")
    pipeline = EncodePipeline(plan, instance)
    cache = {}
    pipeline._cache_processor(cache)(plan.root, "root")
    target = next(
        target
        for target in pipeline._semantic_targets(cache)
        if target.target == "preserved_left"
    )

    path, reason = pipeline._compile_outcome_path(target, cache, "default")

    assert reason == ""
    assert path is not None
    scans = [
        demand.step.table.name
        for demand in path.demands
        if isinstance(demand.step, TableScan)
    ]
    assert scans == ["parent"]
    assert {request.table.name for request in path.row_requests} == {"parent"}


def test_plan_depth_bound_marks_deeper_paths_exhausted() -> None:
    generated = generate(
        "CREATE TABLE items (id INT PRIMARY KEY, value INT);",
        "SELECT id FROM items WHERE value > 10",
        config=GenerationConfig(bootstrap_rows=1, max_plan_depth=0),
    )

    exhausted = [
        obligation
        for obligation in generated.generation.obligations
        if obligation.status == "exhausted"
    ]
    assert exhausted
    assert any(
        obligation.reason.startswith("depth_bound_exhausted:")
        for obligation in exhausted
    )
