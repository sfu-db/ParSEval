"""Tree-shape tests for :mod:`parseval.plan.planner`.

These tests verify the ParSEval-specific restructuring on top of sqlglot's
planner: ``Filter``/``Having``/``Project``/``Limit`` appear as dedicated
steps, and ``Project`` is always the top step (or the direct child of
``Limit``) for a SELECT.
"""

from __future__ import annotations

import json
import unittest

import sqlglot

from parseval.plan.planner import (
    Aggregate,
    Filter,
    Having,
    Join,
    Limit,
    Plan,
    Project,
    Scan,
    SetOperation,
    Sort,
    Step,
)
SCHEMA = "CREATE TABLE t (a INT, b INT);"

BIRD_SCHEMA_FP = "data/sqlite/schema.json"
BIRD_SQLITE_DEV_FP = "data/sqlite/dev.json"

def _plan(sql: str, dialect: str = "sqlite") -> Plan:
    return Plan(sqlglot.parse_one(sql, read=dialect))


def _only(deps):
    deps = list(deps)
    assert len(deps) == 1, f"expected exactly one dependency, got {len(deps)}: {deps}"
    return deps[0]


class TestTreeShape(unittest.TestCase):
    def test_plain_select_has_project_over_scan(self):
        plan = _plan("SELECT a FROM t AS t")

        self.assertIsInstance(plan.root, Project)
        self.assertFalse(plan.root.distinct)
        self.assertEqual(len(plan.root.projections), 1)
        self.assertIsInstance(_only(plan.root.dependencies), Scan)

    def test_where_becomes_filter_between_project_and_scan(self):
        plan = _plan("SELECT a FROM t AS t WHERE a > 1")

        project = plan.root
        self.assertIsInstance(project, Project)

        filter_step = _only(project.dependencies)
        self.assertIsInstance(filter_step, Filter)
        self.assertIsNotNone(filter_step.condition)
        self.assertEqual(filter_step.condition.sql(), "a > 1")

        self.assertIsInstance(_only(filter_step.dependencies), Scan)

    def test_scan_does_not_carry_where_condition(self):
        plan = _plan("SELECT a FROM t AS t WHERE a > 1")
        scan = next(plan.leaves)
        self.assertIsInstance(scan, Scan)
        self.assertIsNone(scan.condition)
        self.assertEqual(list(scan.projections), [])

    def test_group_by_without_having_has_no_having_step(self):
        plan = _plan(
            "SELECT a, COUNT(*) FROM t AS t WHERE a > 1 GROUP BY a"
        )

        project = plan.root
        self.assertIsInstance(project, Project)

        aggregate = _only(project.dependencies)
        self.assertIsInstance(aggregate, Aggregate)
        self.assertIsNone(aggregate.condition)

        filter_step = _only(aggregate.dependencies)
        self.assertIsInstance(filter_step, Filter)
        self.assertIsInstance(_only(filter_step.dependencies), Scan)

    def test_having_becomes_its_own_step_above_aggregate(self):
        plan = _plan(
            "SELECT a, COUNT(*) FROM t AS t GROUP BY a HAVING COUNT(*) > 3"
        )

        project = plan.root
        self.assertIsInstance(project, Project)

        having = _only(project.dependencies)
        self.assertIsInstance(having, Having)
        self.assertIsNotNone(having.condition)

        aggregate = _only(having.dependencies)
        self.assertIsInstance(aggregate, Aggregate)
        # Aggregate itself must not hold the HAVING condition anymore.
        self.assertIsNone(aggregate.condition)

        self.assertIsInstance(_only(aggregate.dependencies), Scan)

    def test_order_by_produces_sort_under_project(self):
        plan = _plan("SELECT a FROM t AS t ORDER BY a")

        project = plan.root
        self.assertIsInstance(project, Project)

        sort = _only(project.dependencies)
        self.assertIsInstance(sort, Sort)
        self.assertIsInstance(_only(sort.dependencies), Scan)

    def test_distinct_is_on_project_not_a_wrapping_aggregate(self):
        plan = _plan("SELECT DISTINCT a FROM t AS t")

        project = plan.root
        self.assertIsInstance(project, Project)
        self.assertTrue(project.distinct)

        # No extra Aggregate should be wrapping the chain just for DISTINCT.
        self.assertNotIsInstance(_only(project.dependencies), Aggregate)
        self.assertIsInstance(_only(project.dependencies), Scan)

    def test_limit_is_top_level_step_above_project(self):
        plan = _plan("SELECT a FROM t AS t LIMIT 5")

        self.assertIsInstance(plan.root, Limit)
        self.assertEqual(plan.root.limit, 5)
        self.assertEqual(plan.root.offset, 0)

        project = _only(plan.root.dependencies)
        self.assertIsInstance(project, Project)
        self.assertIsInstance(_only(project.dependencies), Scan)

    def test_full_stack_ordering(self):
        """Everything at once: WHERE, GROUP BY, HAVING, ORDER BY, LIMIT, DISTINCT."""
        sql = (
            "SELECT DISTINCT t.a, SUM(t.b) AS s "
            "FROM t AS t "
            "WHERE t.a > 0 "
            "GROUP BY t.a "
            "HAVING SUM(t.b) > 10 "
            "ORDER BY t.a "
            "LIMIT 5"
        )
        plan = _plan(sql)

        limit = plan.root
        self.assertIsInstance(limit, Limit)
        self.assertEqual(limit.limit, 5)

        project = _only(limit.dependencies)
        self.assertIsInstance(project, Project)
        self.assertTrue(project.distinct)

        sort = _only(project.dependencies)
        self.assertIsInstance(sort, Sort)

        having = _only(sort.dependencies)
        self.assertIsInstance(having, Having)

        aggregate = _only(having.dependencies)
        self.assertIsInstance(aggregate, Aggregate)
        self.assertIsNone(aggregate.condition)  # HAVING must be lifted out

        filter_step = _only(aggregate.dependencies)
        self.assertIsInstance(filter_step, Filter)
        self.assertEqual(filter_step.condition.sql(), "t.a > 0")

        scan = _only(filter_step.dependencies)
        self.assertIsInstance(scan, Scan)

    def test_join_plan_shape(self):
        sql = (
            "SELECT x.a, y.b "
            "FROM x AS x JOIN y AS y ON x.id = y.id "
            "WHERE x.a > 0"
        )
        plan = _plan(sql)

        project = plan.root
        self.assertIsInstance(project, Project)

        filter_step = _only(project.dependencies)
        self.assertIsInstance(filter_step, Filter)

        join = _only(filter_step.dependencies)
        self.assertIsInstance(join, Join)

        scans = [dep for dep in join.dependencies if isinstance(dep, Scan)]
        self.assertEqual(len(scans), 2)

    def test_union_branches_each_get_their_own_project(self):
        plan = _plan("SELECT a FROM t AS t UNION SELECT b FROM u AS u LIMIT 10")

        # Outer LIMIT wraps the union uniformly.
        self.assertIsInstance(plan.root, Limit)
        self.assertEqual(plan.root.limit, 10)

        set_op = _only(plan.root.dependencies)
        self.assertIsInstance(set_op, SetOperation)
        self.assertTrue(set_op.distinct)  # UNION implies distinct

        # Each branch of the set op is itself a SELECT, so each branch must
        # be rooted in a Project step.
        for branch in set_op.dependencies:
            self.assertIsInstance(branch, Project)

    def test_leaves_are_scans(self):
        plan = _plan(
            "SELECT x.a, y.b FROM x AS x JOIN y AS y ON x.id = y.id WHERE x.a > 0"
        )
        leaves = list(plan.leaves)
        self.assertTrue(leaves)
        for leaf in leaves:
            self.assertIsInstance(leaf, Scan)

    def test_offset_is_captured(self):
        plan = _plan("SELECT a FROM t AS t LIMIT 5 OFFSET 10")
        self.assertIsInstance(plan.root, Limit)
        self.assertEqual(plan.root.limit, 5)
        self.assertEqual(plan.root.offset, 10)

    @classmethod
    def setUpClass(cls):
        with open(BIRD_SQLITE_DEV_FP) as f:
            cls.bird_dev = json.load(f)
        with open(BIRD_SCHEMA_FP) as f:
            cls.bird_schema = json.load(f)
        cls.workspace = "tmp"   
        
        
    def test_build_graph_for_bird(self):
        for index, row in enumerate(self.bird_dev):
            db_id = row["db_id"]
            sql = row["SQL"]
            ddls = ';'.join(self.bird_schema[db_id])
            # instance = Instance(ddls, name = f"{db_id}_{index}", dialect="sqlite")
            # expr = preprocess_sql(sql, instance, dialect="sqlite")
            plan = _plan(sql, dialect="sqlite")  # Just make sure it doesn't error out
            
            # with open(f"{self.workspace}/plan_{db_id}_{index}.dot", "w") as f:
                
            #     f.write(f"query: {sql}\n")
            #     f.write(f"plan: {plan}\n")
            # print(f"Plan for query {index}:\n{plan}\n")
            # break

if __name__ == "__main__":
    unittest.main()
