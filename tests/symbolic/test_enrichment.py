"""Tests for targeted enrichment plan analysis."""

import pytest
from sqlglot import parse_one

from parseval.plan import Plan
from parseval.symbolic.enrichment import (
    EnrichmentTargets,
    analyze_plan_for_enrichment,
)


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
        assert len(targets.duplicate_columns) > 0

    def test_group_by_columns(self):
        """GROUP BY columns should be duplicate targets."""
        plan = Plan(parse_one("SELECT a, COUNT(b) FROM t GROUP BY a"))
        targets = analyze_plan_for_enrichment(plan)
        assert len(targets.duplicate_columns) > 0

    def test_count_column_null(self):
        """COUNT(col) should identify col as NULL target."""
        plan = Plan(parse_one("SELECT COUNT(a) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert len(targets.null_columns) > 0

    def test_count_star_no_null_target(self):
        """COUNT(*) should not create NULL targets."""
        plan = Plan(parse_one("SELECT COUNT(*) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert targets.null_columns == []

    def test_sum_avg_null_targets(self):
        """SUM(col) and AVG(col) should identify col as NULL target."""
        plan = Plan(parse_one("SELECT SUM(a), AVG(b) FROM t"))
        targets = analyze_plan_for_enrichment(plan)
        assert len(targets.null_columns) >= 2
