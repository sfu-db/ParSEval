"""Tests for targeted enrichment plan analysis."""

import pytest
from sqlglot import parse_one

from parseval.plan import Plan
from parseval.symbolic.enrichment import (
    EnrichmentTargets,
    analyze_plan_for_enrichment,
)


# ---------------------------------------------------------------------------
# Integration tests: SymbolicEngine enrichment
# ---------------------------------------------------------------------------


def test_engine_generates_null_for_count_column():
    """Engine should generate NULL values for COUNT(col) columns."""
    from parseval.instance import Instance
    from parseval.symbolic.engine import SymbolicEngine

    schema = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT)"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    engine = SymbolicEngine(instance, "SELECT COUNT(name) FROM t", dialect="sqlite")
    result = engine.generate()

    rows = instance.get_rows("t")
    assert len(rows) > 0
    has_null = any(r["name"].concrete is None for r in rows)
    assert has_null, "Expected at least one NULL in name column for COUNT(name)"


def test_engine_generates_duplicates_for_distinct():
    """Engine should generate duplicate rows for DISTINCT queries."""
    from parseval.instance import Instance
    from parseval.symbolic.engine import SymbolicEngine

    schema = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT)"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    engine = SymbolicEngine(instance, "SELECT DISTINCT name FROM t", dialect="sqlite")
    result = engine.generate()

    rows = instance.get_rows("t")
    assert len(rows) >= 2, "Expected at least 2 rows for DISTINCT testing"


class TestEnrichmentTargets:
    def test_empty_plan(self):
        """Plan with no DISTINCT/GROUP BY/aggregates has no targets."""
        plan = Plan(parse_one("SELECT a FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert targets.duplicate_columns == []
        assert targets.null_columns == []

    def test_distinct_project(self):
        """DISTINCT project should identify projected columns as duplicate targets."""
        plan = Plan(parse_one("SELECT DISTINCT a, b FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        col_names = {col for _, col in targets.duplicate_columns}
        assert col_names == {"a", "b"}

    def test_distinct_project_with_tables(self):
        """DISTINCT on single-table query should resolve columns to the source table."""
        plan = Plan(parse_one("SELECT DISTINCT a, b FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert all(table == "t" for table, _ in targets.duplicate_columns)

    def test_group_by_columns(self):
        """GROUP BY columns should be duplicate targets."""
        plan = Plan(parse_one("SELECT a, COUNT(b) FROM t GROUP BY a"))
        targets = analyze_plan_for_enrichment(plan)
        group_cols = {col for _, col in targets.duplicate_columns}
        assert "a" in group_cols

    def test_count_column_null(self):
        """COUNT(col) should identify col as NULL target."""
        plan = Plan(parse_one("SELECT COUNT(a) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        null_col_names = {col for _, col in targets.null_columns}
        assert "a" in null_col_names

    def test_count_star_no_null_target(self):
        """COUNT(*) should not create NULL targets."""
        plan = Plan(parse_one("SELECT COUNT(*) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert targets.null_columns == []

    def test_count_distinct_no_null_target(self):
        """COUNT(DISTINCT col) should not create NULL targets -- DISTINCT ignores NULLs."""
        plan = Plan(parse_one("SELECT COUNT(DISTINCT a) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert targets.null_columns == []

    def test_sum_avg_null_targets(self):
        """SUM(col) and AVG(col) should identify col as NULL target."""
        plan = Plan(parse_one("SELECT SUM(a), AVG(b) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        null_col_names = {col for _, col in targets.null_columns}
        assert null_col_names == {"a", "b"}

    def test_multi_table_distinct_resolves_tables(self):
        """Multi-table DISTINCT should resolve columns to their respective source tables."""
        plan = Plan(parse_one("SELECT DISTINCT t1.a, t2.b FROM t1, t2"))
        targets = analyze_plan_for_enrichment(plan)
        table_col_map = {col: table for table, col in targets.duplicate_columns}
        assert table_col_map["a"] == "t1"
        assert table_col_map["b"] == "t2"
