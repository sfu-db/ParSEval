"""Targeted enrichment -- analyze plans for DISTINCT/GROUP BY/aggregate patterns.

Identifies columns that need duplicate rows (DISTINCT, GROUP BY) or NULL
values (COUNT/SUM/AVG) to expose semantic differences between queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Tuple

from sqlglot import exp

from parseval.plan import Plan
from parseval.plan.planner import Aggregate, Project, StepAnnotations


@dataclass
class EnrichmentTargets:
    """Columns that need enrichment to expose semantic differences."""

    # Columns that need duplicate values (DISTINCT, GROUP BY).
    duplicate_columns: List[Tuple[str, str]] = field(default_factory=list)
    # Columns that need NULL values (COUNT/SUM/AVG operands).
    null_columns: List[Tuple[str, str]] = field(default_factory=list)


def analyze_plan_for_enrichment(plan: Plan) -> EnrichmentTargets:
    """Walk the plan and identify columns needing enrichment.

    Returns:
        EnrichmentTargets with duplicate_columns and null_columns populated.
    """
    targets = EnrichmentTargets()

    for step in plan.ordered_steps:
        if isinstance(step, Project) and step.distinct:
            _collect_distinct_targets(plan, step, targets)
        elif isinstance(step, Aggregate):
            _collect_aggregate_targets(plan, step, targets)

    # Deduplicate
    targets.duplicate_columns = list(set(targets.duplicate_columns))
    targets.null_columns = list(set(targets.null_columns))
    return targets


def _collect_distinct_targets(
    plan: Plan, step: Project, targets: EnrichmentTargets
) -> None:
    """Collect projected columns as duplicate targets for DISTINCT."""
    annotation = plan.annotation_for(step)
    # Build a map of column name -> table from referenced columns
    col_table_map: dict[str, str] = {}
    for col in annotation.referenced_columns:
        if col.table:
            col_table_map[col.name] = col.table

    fallback_table = annotation.source_tables[0] if annotation.source_tables else ""
    for col in annotation.projected_columns:
        table = col_table_map.get(col, fallback_table)
        targets.duplicate_columns.append((table, col))


def _collect_aggregate_targets(
    plan: Plan, step: Aggregate, targets: EnrichmentTargets
) -> None:
    """Collect GROUP BY columns (duplicates) and aggregate operands (NULLs)."""
    # GROUP BY columns need duplicates
    for col_name, col_expr in step.group.items():
        if isinstance(col_expr, exp.Column):
            table = col_expr.table or ""
            targets.duplicate_columns.append((table, col_expr.name))

    # Detect COUNT(*) synthetic aliases: the planner rewrites COUNT(*)
    # into COUNT("_a_N") and records the Star->alias mapping in operands.
    star_aliases: set[str] = set()
    # Detect COUNT(DISTINCT col) aliases: the planner rewrites
    # COUNT(DISTINCT a) into COUNT("_a_N") with operand DISTINCT a AS _a_N.
    distinct_aliases: set[str] = set()
    for operand in step.operands:
        if isinstance(operand, exp.Alias):
            if isinstance(operand.this, exp.Star):
                star_aliases.add(operand.alias)
            elif isinstance(operand.this, exp.Distinct):
                distinct_aliases.add(operand.alias)

    # Aggregate operands need NULLs (except COUNT(*) and COUNT(DISTINCT col))
    for agg_expr in step.aggregations:
        for agg_func in agg_expr.find_all(exp.Func):
            func_name = agg_func.sql_name().upper()
            if func_name in ("COUNT", "SUM", "AVG"):
                # Find the column operand
                for operand in agg_func.unnest_operands():
                    if isinstance(operand, exp.Column):
                        # Skip COUNT(*) -- the column is a synthetic alias
                        if func_name == "COUNT" and operand.name in star_aliases:
                            continue
                        # Skip COUNT(DISTINCT col) -- DISTINCT ignores NULLs
                        if func_name == "COUNT" and operand.name in distinct_aliases:
                            continue
                        table = operand.table or ""
                        targets.null_columns.append((table, operand.name))
