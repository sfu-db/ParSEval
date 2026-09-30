"""End-to-end checks for the CSV coverage benchmark's result interpretation."""

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_postgres_coverage.py"
spec = importlib.util.spec_from_file_location("benchmark_postgres_coverage", SCRIPT)
assert spec is not None and spec.loader is not None
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)
run_case = benchmark.run_case


def _row(q2: str, ground_truth: str) -> dict[str, str]:
    return {
        "index": "fixture",
        "dbid": "fixture",
        "ground_truth": ground_truth,
        "dialect": "postgres",
        "schema_ddl": "CREATE TABLE t(a INT)",
        "q1": "SELECT a FROM t WHERE a > 0",
        "q2": q2,
    }


def test_generated_instances_distinguish_a_neq_pair():
    result = run_case(_row("SELECT a FROM t WHERE a < 0", "NEQ"), 500)

    assert result["queries"]["q1"]["covered"] > 0
    assert result["queries"]["q2"]["covered"] > 0
    assert "not_attempted" in result["queries"]["q1"]
    assert result["comparison"]["status"] == "difference_found"
    assert result["comparison"]["bag_difference_found"]
    assert result["comparison"]["witness_rows"]
    assert result["sqlite_replay"]["status"] == "completed"
    assert result["sqlite_replay"]["query_executions"] > 0
    assert result["sqlite_replay"]["semantic_mismatches"] == 0
    assert result["sqlite_replay"]["bag_difference_found"]


def test_query_failure_is_recorded_without_losing_other_query_coverage():
    result = run_case(_row("SELECT missing FROM t", "EQ"), 500)

    assert "error" not in result["queries"]["q1"]
    assert "error" in result["queries"]["q2"]
    assert result["comparison"]["status"] == "not_run"
    assert result["comparison"]["tested_instances"] == 0


def test_inventory_reports_initial_frontier_without_solving():
    result = run_case(_row("SELECT a FROM t WHERE a < 0", "NEQ"), 500, mode="inventory")

    assert result["queries"]["q1"]["initial_neighbors"] > 0
    assert result["queries"]["q2"]["status"] == "inventory_only"
    assert result["comparison"]["status"] == "not_run"
    assert result["comparison"]["tested_instances"] == 0


def test_progress_uses_compact_target_keys_and_precise_phases():
    updates = []

    def record(stage, details):
        if stage.startswith("q1.") and "target" in details:
            updates.append((stage, details.copy()))

    run_case(_row("SELECT a FROM t WHERE a < 0", "NEQ"), 500, progress=record)

    assert updates
    assert updates[0][0] == "q1.solve"
    assert updates[0][1]["attempt"] == 1
    assert len(updates[0][1]["target"]) == 12
    assert {stage for stage, _ in updates} >= {
        "q1.solve", "q1.validate", "q1.rediscover"
    }


def test_sqlite_transpilation_rewrites_postgres_intervals():
    sql = (
        "SELECT * FROM public.t WHERE d < "
        "DATE '2020-01-01' + INTERVAL '3' MONTH"
    )

    translated = benchmark._sqlite_query(sql, "postgres")

    assert "public" not in translated
    assert "INTERVAL" not in translated
    assert "'+3 month'" in translated
