"""Tests for :class:`parseval.plan.StepAnnotations` + :meth:`Plan.annotation_for`."""

import unittest

import sqlglot

from parseval.plan import (
    Aggregate,
    Filter,
    Having,
    Join,
    Plan,
    Project,
    Scan,
)


def _plan(sql: str, dialect: str = "sqlite") -> Plan:
    return Plan(sqlglot.parse_one(sql, read=dialect))


def _first_step_of_type(plan: Plan, step_type):
    for step in plan.ordered_steps:
        if isinstance(step, step_type):
            return step
    raise AssertionError(f"no {step_type.__name__} step in plan")


class TestStepAnnotations(unittest.TestCase):
    def test_annotation_for_is_cached_and_reflects_step(self):
        plan = _plan("SELECT a FROM t WHERE b > 1")
        step = plan.root

        first = plan.annotation_for(step)
        second = plan.annotation_for(step)
        self.assertIs(first, second)

        self.assertEqual(first.step_type, type(step).__name__)
        self.assertEqual(first.step_id, f"step_{plan.ordered_steps.index(step)}")

    def test_scan_filter_project_split(self):
        plan = _plan("SELECT a FROM t WHERE b > 1")

        project = _first_step_of_type(plan, Project)
        project_ann = plan.annotation_for(project)
        self.assertEqual(project_ann.step_type, "Project")
        self.assertEqual(project_ann.projected_columns, ("a",))
        self.assertEqual(project_ann.source_tables, ("t",))

        filter_step = _first_step_of_type(plan, Filter)
        filter_ann = plan.annotation_for(filter_step)
        self.assertEqual(filter_ann.step_type, "Filter")
        self.assertIsNotNone(filter_ann.condition)
        self.assertEqual(filter_ann.source_tables, ("t",))

        scan = _first_step_of_type(plan, Scan)
        scan_ann = plan.annotation_for(scan)
        self.assertEqual(scan_ann.step_type, "Scan")
        self.assertEqual(scan_ann.source_tables, ("t",))
        self.assertIsNone(scan_ann.condition)

    def test_join_source_tables_include_both_sides(self):
        plan = _plan("SELECT t.a, u.c FROM t JOIN u ON t.a = u.a")
        project_ann = plan.annotation_for(plan.root)
        self.assertEqual(project_ann.source_tables, ("t", "u"))

        join = _first_step_of_type(plan, Join)
        join_ann = plan.annotation_for(join)
        self.assertEqual(join_ann.step_type, "Join")
        self.assertEqual(join_ann.source_tables, ("t", "u"))

    def test_having_lives_on_its_own_step_and_aggregate_has_no_condition(self):
        plan = _plan("SELECT a FROM t GROUP BY a HAVING COUNT(b) > 1")

        aggregate = _first_step_of_type(plan, Aggregate)
        agg_ann = plan.annotation_for(aggregate)
        self.assertIsNone(agg_ann.condition)
        self.assertEqual(agg_ann.source_tables, ("t",))

        having = _first_step_of_type(plan, Having)
        having_ann = plan.annotation_for(having)
        self.assertEqual(having_ann.step_type, "Having")
        self.assertIsNotNone(having_ann.condition)

    def test_source_tables_resolves_through_from_subquery(self):
        plan = _plan("SELECT dt.a FROM (SELECT a FROM t) AS dt")
        project_ann = plan.annotation_for(plan.root)
        self.assertEqual(project_ann.source_tables, ("t",))


if __name__ == "__main__":
    unittest.main()
