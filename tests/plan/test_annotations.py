"""Tests for :class:`parseval.plan.StepAnnotations` + :meth:`Plan.annotation_for`."""

import unittest

import sqlglot

from parseval.dtype import DataType
from parseval.instance import Instance
from parseval.plan import (
    Aggregate,
    Filter,
    Having,
    Join,
    Plan,
    Project,
    Scan,
    SubPlan,
)


def _plan(sql: str, ddl: str | None = None, dialect: str = "sqlite") -> Plan:
    instance = Instance(ddl, name="db", dialect=dialect) if ddl is not None else None
    return Plan(sqlglot.parse_one(sql, read=dialect), instance=instance)


def _first_step_of_type(plan: Plan, step_type):
    for step in plan.ordered_steps:
        if isinstance(step, step_type):
            return step
    raise AssertionError(f"no {step_type.__name__} step in plan")


def _relation_names(annotation):
    return tuple(
        relation.name.normalized
        for relation in annotation.source_relations
        if relation.name is not None
    )


def _relation_aliases(annotation):
    return tuple(
        relation.alias.normalized
        for relation in annotation.source_relations
        if relation.alias is not None
    )


def _column_names(annotation):
    return tuple(column.name.normalized for column in annotation.projected_columns)


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
        plan = _plan(
            "SELECT a FROM t WHERE b > 1",
            "CREATE TABLE t (a INT, b INT);",
        )

        project = _first_step_of_type(plan, Project)
        project_ann = plan.annotation_for(project)
        self.assertEqual(project_ann.step_type, "Project")
        self.assertEqual(_column_names(project_ann), ("a",))
        self.assertEqual(_relation_names(project_ann), ("t",))

        filter_step = _first_step_of_type(plan, Filter)
        filter_ann = plan.annotation_for(filter_step)
        self.assertEqual(filter_ann.step_type, "Filter")
        self.assertIsNotNone(filter_ann.condition)
        self.assertEqual(_relation_names(filter_ann), ("t",))

        scan = _first_step_of_type(plan, Scan)
        scan_ann = plan.annotation_for(scan)
        self.assertEqual(scan_ann.step_type, "Scan")
        self.assertEqual(_relation_names(scan_ann), ("t",))
        self.assertIsNone(scan_ann.condition)

    def test_join_source_relations_include_both_sides(self):
        plan = _plan(
            "SELECT t.a, u.c FROM t JOIN u ON t.a = u.a",
            "CREATE TABLE t (a INT); CREATE TABLE u (a INT, c INT);",
        )
        project_ann = plan.annotation_for(plan.root)
        self.assertCountEqual(_relation_names(project_ann), ("t", "u"))

        join = _first_step_of_type(plan, Join)
        join_ann = plan.annotation_for(join)
        self.assertEqual(join_ann.step_type, "Join")
        self.assertCountEqual(_relation_names(join_ann), ("t", "u"))

    def test_having_lives_on_its_own_step_and_aggregate_has_no_condition(self):
        plan = _plan(
            "SELECT a FROM t GROUP BY a HAVING COUNT(b) > 1",
            "CREATE TABLE t (a INT, b INT);",
        )

        aggregate = _first_step_of_type(plan, Aggregate)
        agg_ann = plan.annotation_for(aggregate)
        self.assertIsNone(agg_ann.condition)
        self.assertEqual(_relation_names(agg_ann), ("t",))

        having = _first_step_of_type(plan, Having)
        having_ann = plan.annotation_for(having)
        self.assertEqual(having_ann.step_type, "Having")
        self.assertIsNotNone(having_ann.condition)

    def test_from_subquery_exposes_subquery_relation_identity(self):
        plan = _plan(
            "SELECT dt.a FROM (SELECT a FROM t) AS dt",
            "CREATE TABLE t (a INT);",
        )
        project_ann = plan.annotation_for(plan.root)
        self.assertEqual(_relation_names(project_ann), ("dt",))
        self.assertEqual(_relation_aliases(project_ann), ("dt",))


class TestAggregationMetadata(unittest.TestCase):
    def test_count_having_metadata_describes_group_and_required_rows(self):
        plan = _plan(
            "SELECT dept, COUNT(*) AS n FROM sales GROUP BY dept HAVING COUNT(*) > 3",
            "CREATE TABLE sales (dept TEXT, amount REAL);",
        )

        aggregate = _first_step_of_type(plan, Aggregate)
        metadata = plan.annotation_for(aggregate).metadata["aggregation"]

        group_key = metadata["group_keys"][0]
        self.assertEqual(group_key.source_column_id.name.normalized, "dept")

        count_output = next(
            item
            for item in metadata["aggregate_outputs"].values()
            if item["function"] == "count"
        )
        self.assertEqual(count_output["alias"], "n")
        self.assertIsNone(count_output["argument"])
        self.assertTrue(
            count_output["semantic_datatype"].is_type(*DataType.INTEGER_TYPES)
        )

        having = _first_step_of_type(plan, Having)
        constraints = plan.annotation_for(having).metadata["having_constraints"]
        self.assertEqual(constraints[0]["function"], "count")
        self.assertEqual(constraints[0]["operator"], "gt")
        self.assertEqual(constraints[0]["value"], 3)
        self.assertEqual(constraints[0]["required_rows"], 4)

    def test_sum_metadata_describes_argument_and_output_datatype(self):
        plan = _plan(
            "SELECT dept, SUM(amount) AS total FROM sales GROUP BY dept",
            "CREATE TABLE sales (dept TEXT, amount REAL);",
        )

        aggregate = _first_step_of_type(plan, Aggregate)
        outputs = plan.annotation_for(aggregate).metadata["aggregation"]["aggregate_outputs"]
        sum_output = next(item for item in outputs.values() if item["function"] == "sum")

        self.assertEqual(sum_output["alias"], "total")
        self.assertEqual(sum_output["argument"].source_column_id.name.normalized, "amount")
        self.assertTrue(sum_output["semantic_datatype"].is_type(*DataType.REAL_TYPES))

    def test_min_metadata_preserves_argument_datatype(self):
        plan = _plan(
            "SELECT dept, MIN(created_at) AS first_seen FROM sales GROUP BY dept",
            "CREATE TABLE sales (dept TEXT, created_at DATE);",
        )

        aggregate = _first_step_of_type(plan, Aggregate)
        outputs = plan.annotation_for(aggregate).metadata["aggregation"]["aggregate_outputs"]
        min_output = next(item for item in outputs.values() if item["function"] == "min")

        self.assertEqual(min_output["argument"].source_column_id.name.normalized, "created_at")
        self.assertTrue(min_output["semantic_datatype"].is_type(DataType.Type.DATE))


class TestSubqueryMetadata(unittest.TestCase):
    def test_exists_metadata_describes_polarity_and_correlation_link(self):
        plan = _plan(
            "SELECT t.a FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.x = t.a)",
            "CREATE TABLE t (a INT); CREATE TABLE u (x INT);",
        )

        subplan = _first_step_of_type(plan, SubPlan)
        metadata = plan.annotation_for(subplan).metadata["subquery"]

        self.assertEqual(metadata["kind"], "exists")
        self.assertEqual(metadata["polarity"], "positive")
        self.assertEqual(metadata["cardinality"], "one_or_more")
        self.assertEqual(metadata["correlations"][0]["operator"], "eq")
        self.assertEqual(
            metadata["correlations"][0]["inner"].source_column_id.name.normalized,
            "x",
        )
        self.assertEqual(
            metadata["correlations"][0]["outer"].source_column_id.name.normalized,
            "a",
        )

    def test_not_exists_metadata_describes_negative_cardinality(self):
        plan = _plan(
            "SELECT t.a FROM t WHERE NOT EXISTS (SELECT 1 FROM u WHERE u.x = t.a)",
            "CREATE TABLE t (a INT); CREATE TABLE u (x INT);",
        )

        subplan = _first_step_of_type(plan, SubPlan)
        metadata = plan.annotation_for(subplan).metadata["subquery"]

        self.assertEqual(metadata["polarity"], "negative")
        self.assertEqual(metadata["cardinality"], "zero")

    def test_in_metadata_describes_predicate_and_subquery_output_columns(self):
        plan = _plan(
            "SELECT a FROM t WHERE a IN (SELECT x FROM u)",
            "CREATE TABLE t (a INT); CREATE TABLE u (x INT);",
        )

        subplan = _first_step_of_type(plan, SubPlan)
        metadata = plan.annotation_for(subplan).metadata["subquery"]

        self.assertEqual(metadata["kind"], "in")
        self.assertEqual(metadata["polarity"], "positive")
        self.assertEqual(
            metadata["predicate_column"].source_column_id.name.normalized,
            "a",
        )
        self.assertEqual(
            metadata["output_columns"][0].source_column_id.name.normalized,
            "x",
        )

    def test_not_in_metadata_describes_negative_matching_cardinality(self):
        plan = _plan(
            "SELECT a FROM t WHERE a NOT IN (SELECT x FROM u)",
            "CREATE TABLE t (a INT); CREATE TABLE u (x INT);",
        )

        subplan = _first_step_of_type(plan, SubPlan)
        metadata = plan.annotation_for(subplan).metadata["subquery"]

        self.assertEqual(metadata["kind"], "in")
        self.assertEqual(metadata["polarity"], "negative")
        self.assertEqual(metadata["cardinality"], "zero_matching")


if __name__ == "__main__":
    unittest.main()
