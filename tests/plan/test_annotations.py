"""Tests for :class:`parseval.plan.StepAnnotations` + :meth:`Plan.annotation_for`."""

import unittest

import sqlglot

from parseval.dtype import DataType
from parseval.identity import ColumnKind
from parseval.instance import Instance
from parseval.plan import (
    Aggregate,
    Filter,
    Having,
    Join,
    Limit,
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


def _first_inner_step_of_type(step, step_type):
    seen = set()
    stack = [step]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, step_type):
            return current
        for subplan in getattr(current, "subplan_dependencies", ()) or ():
            stack.append(subplan)
            if subplan.inner is not None:
                stack.append(subplan.inner)
        stack.extend(getattr(current, "chain_dependencies", ()) or ())
    raise AssertionError(f"no inner {step_type.__name__} step")


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
        plan = _plan("SELECT a FROM t WHERE b > 1", "CREATE TABLE t (a INT, b INT);")
        step = plan.root

        first = plan.annotation_for(step)
        second = plan.annotation_for(step)
        self.assertIs(first, second)
        self.assertEqual(first.step_type, type(step).__name__)
        self.assertEqual(first.step_id, f"step_{plan.ordered_steps.index(step)}")

    def test_limit_annotation_preserves_physical_source_relation(self):
        plan = _plan(
            """
            SELECT CAST(`Free Meal Count (K-12)` AS REAL) / `Enrollment (K-12)`
            FROM frpm
            ORDER BY `Enrollment (K-12)` DESC
            LIMIT 2 OFFSET 9
            """,
            """
            CREATE TABLE frpm (
              CDSCode TEXT PRIMARY KEY,
              `Free Meal Count (K-12)` REAL,
              `Enrollment (K-12)` REAL
            );
            """,
        )

        limit = _first_step_of_type(plan, Limit)
        annotation = plan.annotation_for(limit)

        self.assertEqual(_relation_names(annotation), ("frpm",))

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

    def test_cte_inner_operators_are_annotated_without_changing_outer_ids(self):
        plan = _plan(
            "WITH x AS (SELECT id, status FROM orders WHERE status = 'open') "
            "SELECT id FROM x WHERE id = 7",
            "CREATE TABLE orders (id INT, status TEXT);",
        )

        outer_ids = {
            id(step): f"step_{index}"
            for index, step in enumerate(plan.ordered_steps)
        }

        cte = _first_step_of_type(plan, SubPlan)
        inner_project = _first_inner_step_of_type(cte.inner, Project)
        inner_filter = _first_inner_step_of_type(cte.inner, Filter)
        inner_scan = _first_inner_step_of_type(cte.inner, Scan)

        self.assertEqual(plan.annotation_for(inner_project).step_type, "Project")
        self.assertEqual(plan.annotation_for(inner_filter).step_type, "Filter")
        self.assertEqual(plan.annotation_for(inner_scan).step_type, "Scan")
        self.assertEqual(_relation_names(plan.annotation_for(inner_scan)), ("orders",))

        for step in plan.ordered_steps:
            self.assertEqual(plan.annotation_for(step).step_id, outer_ids[id(step)])

    def test_nested_cte_subplan_and_inner_operators_are_annotated(self):
        plan = _plan(
            "WITH x AS (SELECT id, status FROM orders), "
            "y AS (SELECT id FROM x WHERE id > 3) "
            "SELECT id FROM y WHERE id = 7",
            "CREATE TABLE orders (id INT, status TEXT);",
        )

        outer_ctes = [step for step in plan.ordered_steps if isinstance(step, SubPlan)]
        self.assertTrue(outer_ctes)
        nested_cte = _first_inner_step_of_type(outer_ctes[0].inner, SubPlan)
        nested_project = _first_inner_step_of_type(nested_cte.inner, Project)
        nested_scan = _first_inner_step_of_type(nested_cte.inner, Scan)

        self.assertEqual(plan.annotation_for(nested_cte).step_type, "SubPlan")
        self.assertEqual(plan.annotation_for(nested_project).step_type, "Project")
        self.assertEqual(plan.annotation_for(nested_scan).step_type, "Scan")
        self.assertEqual(
            plan.annotation_for(nested_cte).metadata["subquery"]["kind"],
            "cte",
        )

    def test_correlated_inner_filter_annotation_resolves_inner_and_outer_columns(self):
        plan = _plan(
            "SELECT t.a FROM t WHERE EXISTS (SELECT 1 FROM u WHERE u.x = t.a)",
            "CREATE TABLE t (a INT); CREATE TABLE u (x INT);",
        )

        subplan = _first_step_of_type(plan, SubPlan)
        inner_filter = _first_inner_step_of_type(subplan.inner, Filter)
        referenced = plan.annotation_for(inner_filter).referenced_columns

        self.assertEqual(
            {(column.relation.name.normalized, column.name.normalized) for column in referenced},
            {("u", "x"), ("t", "a")},
        )


class TestAggregationMetadata(unittest.TestCase):
    def test_simple_group_key_keeps_projected_identity(self):
        plan = _plan(
            "SELECT dept FROM sales GROUP BY dept",
            "CREATE TABLE sales (dept TEXT);",
        )

        aggregate = _first_step_of_type(plan, Aggregate)
        metadata = plan.annotation_for(aggregate).metadata["aggregation"]

        group_key = metadata["group_keys"][0]
        self.assertIs(group_key.kind, ColumnKind.PROJECTED)
        self.assertEqual(group_key.name.normalized, "_g0")
        self.assertEqual(group_key.source_column_id.name.normalized, "dept")

    def test_complex_group_key_uses_derived_identity_with_single_lineage(self):
        plan = _plan(
            "SELECT SUBSTR(t1.date, 1, 4) AS yr FROM sales AS t1 "
            "GROUP BY SUBSTR(t1.date, 1, 4)",
            "CREATE TABLE sales (date TEXT);",
        )

        aggregate = _first_step_of_type(plan, Aggregate)
        metadata = plan.annotation_for(aggregate).metadata["aggregation"]

        group_key = metadata["group_keys"][0]
        self.assertIs(group_key.kind, ColumnKind.DERIVED)
        self.assertEqual(group_key.name.normalized, "_g0")
        self.assertEqual(group_key.source_column_id.name.normalized, "date")
        self.assertIn(
            metadata["group_expressions"][group_key].sql(dialect="sqlite"),
            {"SUBSTR(t1.date, 1, 4)", "SUBSTRING(t1.date, 1, 4)"},
        )
        self.assertEqual(
            tuple(
                source.name.normalized
                for source in metadata["group_sources"][group_key]
            ),
            ("date",),
        )

    def test_multi_column_group_key_uses_derived_identity_without_single_lineage(self):
        plan = _plan(
            "SELECT t1.a || '-' || t1.b AS label FROM sales AS t1 "
            "GROUP BY t1.a || '-' || t1.b",
            "CREATE TABLE sales (a TEXT, b TEXT);",
        )

        aggregate = _first_step_of_type(plan, Aggregate)
        metadata = plan.annotation_for(aggregate).metadata["aggregation"]

        group_key = metadata["group_keys"][0]
        self.assertIs(group_key.kind, ColumnKind.DERIVED)
        self.assertEqual(group_key.name.normalized, "_g0")
        self.assertIsNone(group_key.source_column_id)
        self.assertEqual(
            tuple(
                source.name.normalized
                for source in metadata["group_sources"][group_key]
            ),
            ("a", "b"),
        )

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

    def test_having_aggregate_without_select_alias_uses_real_output(self):
        plan = _plan(
            "SELECT dept FROM sales GROUP BY dept HAVING COUNT(*) > 3",
            "CREATE TABLE sales (dept TEXT, amount REAL);",
        )

        having = _first_step_of_type(plan, Having)
        self.assertNotIn("_h", having.condition.sql())

        aggregate = _first_step_of_type(plan, Aggregate)
        outputs = plan.annotation_for(aggregate).metadata["aggregation"]["aggregate_outputs"]
        count_output = next(
            item for item in outputs.values() if item["function"] == "count"
        )
        self.assertEqual(count_output["alias"], "count")

        constraints = plan.annotation_for(having).metadata["having_constraints"]
        self.assertEqual(constraints[0]["function"], "count")
        self.assertEqual(constraints[0]["operator"], "gt")

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

    def test_join_group_order_projection_keeps_input_column_visibility(self):
        plan = _plan(
            "SELECT T1.id "
            "FROM sets AS T1 "
            "INNER JOIN set_translations AS T2 ON T1.code = T2.setCode "
            "WHERE T2.language = 'Russian' "
            "GROUP BY T1.baseSetSize "
            "ORDER BY COUNT(T1.id) DESC "
            "LIMIT 1",
            "CREATE TABLE sets (id INTEGER, baseSetSize INTEGER, code TEXT, name TEXT); "
            "CREATE TABLE set_translations (id INTEGER, language TEXT, setCode TEXT, translation TEXT);",
        )

        project = _first_step_of_type(plan, Project)
        project_columns = plan.annotation_for(project).projected_columns
        self.assertEqual(project_columns[0].name.normalized, "id")
        self.assertEqual(project_columns[0].source_column_id.name.normalized, "id")

        aggregate = _first_step_of_type(plan, Aggregate)
        metadata = plan.annotation_for(aggregate).metadata["aggregation"]
        group_key = metadata["group_keys"][0]
        self.assertEqual(group_key.source_column_id.name.normalized, "basesetsize")


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
