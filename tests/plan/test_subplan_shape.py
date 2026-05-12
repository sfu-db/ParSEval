"""Tree-shape tests for :class:`parseval.plan.planner.SubPlan`.

These tests pin down how the planner lowers every subquery shape into a
dedicated :class:`SubPlan` node attached as an extra dependency of the
consuming outer step, with correct :attr:`SubPlan.kind`, correlation
columns, output columns, and inner-plan shape.
"""

from __future__ import annotations

import unittest

import sqlglot

from parseval.plan.planner import (
    Aggregate,
    Filter,
    Having,
    Join,
    Plan,
    Project,
    Scan,
    SetOperation,
    Step,
    SubPlan,
    SubPlanKind,
)


def _plan(sql: str, dialect: str = "sqlite") -> Plan:
    return Plan(sqlglot.parse_one(sql, read=dialect))


def _only(deps):
    deps = list(deps)
    assert len(deps) == 1, f"expected exactly one dependency, got {len(deps)}: {deps}"
    return deps[0]


def _first_subplan(step: Step) -> SubPlan:
    subs = step.subplan_dependencies
    assert subs, f"{step.id} has no SubPlan dependencies"
    return subs[0]


class TestSubPlanAccessors(unittest.TestCase):
    def test_chain_vs_subplan_separation(self):
        """chain_dependencies and subplan_dependencies partition dependencies cleanly."""
        plan = _plan(
            "SELECT a FROM t AS t WHERE EXISTS (SELECT 1 FROM u AS u)"
        )
        project = plan.root
        self.assertIsInstance(project, Project)
        filter_step = _only(project.chain_dependencies)
        self.assertIsInstance(filter_step, Filter)
        # Filter has exactly one chain dependency (the Scan) and one SubPlan.
        self.assertEqual(len(filter_step.chain_dependencies), 1)
        self.assertIsInstance(filter_step.chain_dependencies[0], Scan)
        self.assertEqual(len(filter_step.subplan_dependencies), 1)
        sub = filter_step.subplan_dependencies[0]
        self.assertEqual(sub.kind, SubPlanKind.EXISTS)

    def test_subplan_is_leaf_in_outer_dag(self):
        """SubPlan has no dependencies of its own in the outer plan's DAG."""
        plan = _plan(
            "SELECT a FROM t AS t WHERE EXISTS (SELECT 1 FROM u AS u)"
        )
        sub = _first_subplan(_only(plan.root.chain_dependencies))
        # Inner plan is carried via SubPlan.inner, not via dependencies.
        self.assertIsNotNone(sub.inner)
        self.assertEqual(len(sub.dependencies), 0)
        self.assertEqual(sub.chain_dependencies, ())


class TestFromSubquery(unittest.TestCase):
    def test_from_subquery_emits_subplan_table(self):
        plan = _plan("SELECT dt.a FROM (SELECT a FROM t AS t) AS dt")

        project = plan.root
        self.assertIsInstance(project, Project)

        scan = _only(project.chain_dependencies)
        self.assertIsInstance(scan, Scan)
        sub = _first_subplan(scan)

        self.assertEqual(sub.kind, SubPlanKind.TABLE)
        self.assertEqual(sub.alias, "dt")
        self.assertEqual(sub.output_columns, ("a",))
        self.assertEqual(sub.correlation, ())

        # The inner plan is a full Project over a Scan of t.
        self.assertIsInstance(sub.inner, Project)
        self.assertIsInstance(_only(sub.inner.chain_dependencies), Scan)

    def test_join_subquery_emits_subplan_on_the_joined_scan(self):
        plan = _plan(
            "SELECT t.a FROM t AS t "
            "LEFT JOIN (SELECT x FROM u AS u) AS sub ON sub.x = t.a"
        )
        project = plan.root
        self.assertIsInstance(project, Project)
        join = _only(project.chain_dependencies)
        self.assertIsInstance(join, Join)

        scan_sub = next(
            dep for dep in join.chain_dependencies if dep.name == "sub"
        )
        self.assertIsInstance(scan_sub, Scan)
        sub = _first_subplan(scan_sub)
        self.assertEqual(sub.kind, SubPlanKind.TABLE)
        self.assertEqual(sub.alias, "sub")


class TestExists(unittest.TestCase):
    def test_uncorrelated_exists_in_where(self):
        plan = _plan(
            "SELECT a FROM t AS t WHERE EXISTS (SELECT 1 FROM u AS u)"
        )
        filter_step = _only(plan.root.chain_dependencies)
        sub = _first_subplan(filter_step)
        self.assertEqual(sub.kind, SubPlanKind.EXISTS)
        self.assertEqual(sub.correlation, ())

    def test_correlated_exists_captures_correlation_columns(self):
        plan = _plan(
            "SELECT t.a FROM t AS t "
            "WHERE EXISTS (SELECT 1 FROM u AS u WHERE u.x = t.a)"
        )
        filter_step = _only(plan.root.chain_dependencies)
        sub = _first_subplan(filter_step)
        self.assertEqual(sub.kind, SubPlanKind.EXISTS)
        correlated = [column.sql() for column in sub.correlation]
        self.assertIn("t.a", correlated)

    def test_not_exists_anchors_on_same_subplan(self):
        plan = _plan(
            "SELECT a FROM t AS t WHERE NOT EXISTS (SELECT 1 FROM u AS u)"
        )
        filter_step = _only(plan.root.chain_dependencies)
        subs = filter_step.subplan_dependencies
        # NOT wraps EXISTS but we still emit one EXISTS SubPlan.
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0].kind, SubPlanKind.EXISTS)

    def test_exists_in_having(self):
        plan = _plan(
            "SELECT a, COUNT(*) FROM t AS t GROUP BY a "
            "HAVING EXISTS (SELECT 1 FROM u AS u WHERE u.x = a)"
        )
        having = _only(plan.root.chain_dependencies)
        self.assertIsInstance(having, Having)
        sub = _first_subplan(having)
        self.assertEqual(sub.kind, SubPlanKind.EXISTS)


class TestIn(unittest.TestCase):
    def test_in_subquery_emits_subplan_in(self):
        plan = _plan(
            "SELECT a FROM t AS t WHERE a IN (SELECT x FROM u AS u)"
        )
        filter_step = _only(plan.root.chain_dependencies)
        sub = _first_subplan(filter_step)
        self.assertEqual(sub.kind, SubPlanKind.IN)
        self.assertEqual(sub.output_columns, ("x",))

    def test_in_with_literal_list_does_not_emit_subplan(self):
        plan = _plan("SELECT a FROM t AS t WHERE a IN (1, 2, 3)")
        filter_step = _only(plan.root.chain_dependencies)
        self.assertEqual(filter_step.subplan_dependencies, ())


class TestScalarSubquery(unittest.TestCase):
    def test_scalar_subquery_in_projection(self):
        plan = _plan(
            "SELECT (SELECT MAX(x) FROM u AS u) AS m FROM t AS t"
        )
        project = plan.root
        self.assertIsInstance(project, Project)
        # Outer aggregation detection must NOT descend into the subquery, so
        # there should be no outer Aggregate step here.
        for dep in project.chain_dependencies:
            self.assertNotIsInstance(dep, Aggregate)
        sub = _first_subplan(project)
        self.assertEqual(sub.kind, SubPlanKind.SCALAR)

    def test_scalar_subquery_in_filter(self):
        plan = _plan(
            "SELECT a FROM t AS t "
            "WHERE a = (SELECT MAX(x) FROM u AS u)"
        )
        filter_step = _only(plan.root.chain_dependencies)
        sub = _first_subplan(filter_step)
        self.assertEqual(sub.kind, SubPlanKind.SCALAR)

    def test_correlated_scalar_subquery_in_projection(self):
        plan = _plan(
            "SELECT t.a, (SELECT MAX(u.x) FROM u AS u WHERE u.k = t.a) AS m "
            "FROM t AS t"
        )
        project = plan.root
        sub = _first_subplan(project)
        self.assertEqual(sub.kind, SubPlanKind.SCALAR)
        correlated = [column.sql() for column in sub.correlation]
        self.assertIn("t.a", correlated)


class TestCTE(unittest.TestCase):
    def test_cte_reference_emits_subplan_cte(self):
        plan = _plan(
            "WITH c AS (SELECT a FROM t AS t) SELECT c.a FROM c"
        )
        project = plan.root
        scan = _only(project.chain_dependencies)
        self.assertIsInstance(scan, Scan)
        sub = _first_subplan(scan)
        self.assertEqual(sub.kind, SubPlanKind.CTE)
        self.assertEqual(sub.alias, "c")
        self.assertEqual(sub.output_columns, ("a",))

    def test_cte_shared_across_multiple_references_uses_same_subplan(self):
        plan = _plan(
            "WITH c AS (SELECT a FROM t AS t) "
            "SELECT c1.a FROM c AS c1 JOIN c AS c2 ON c1.a = c2.a"
        )
        project = plan.root
        join = _only(project.chain_dependencies)
        self.assertIsInstance(join, Join)

        subs_by_scan = []
        for scan in join.chain_dependencies:
            subs_by_scan.append(_first_subplan(scan))

        # Both scans reference the same CTE → same SubPlan instance.
        self.assertIs(subs_by_scan[0], subs_by_scan[1])
        self.assertEqual(subs_by_scan[0].kind, SubPlanKind.CTE)


class TestDAGLeavesAreRealScans(unittest.TestCase):
    """The outer plan's DAG stops at SubPlans; inner-plan Scans are not leaves
    of the outer DAG."""

    def test_outer_leaves_exclude_subplan_inner_scans(self):
        plan = _plan(
            "SELECT a FROM t AS t WHERE EXISTS (SELECT 1 FROM u AS u)"
        )
        leaves = list(plan.leaves)
        # Exactly one leaf expected: the SubPlan for EXISTS (u's Scan lives
        # inside the SubPlan.inner subtree, not in the outer DAG).
        # However Scan t is also a leaf. So we expect {Scan(t), SubPlan(exists)}.
        leaf_types = {type(leaf).__name__ for leaf in leaves}
        self.assertIn("Scan", leaf_types)
        self.assertIn("SubPlan", leaf_types)

        # No leaf should be an inner-plan step (Filter / Scan u).
        outer_step_ids = {id(step) for step in plan.dag}
        for sub in (leaf for leaf in leaves if isinstance(leaf, SubPlan)):
            inner_ids = set()
            stack = [sub.inner]
            while stack:
                current = stack.pop()
                inner_ids.add(id(current))
                stack.extend(current.dependencies)
            self.assertTrue(inner_ids.isdisjoint(outer_step_ids - {id(sub)}))


if __name__ == "__main__":
    unittest.main()
