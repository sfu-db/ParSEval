"""Tests for constraint generation for new branch types."""

from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.solver.unified import SolverConstraint
from parseval.solver.types import solver_var
from parseval.symbolic.constraints import ConstraintGenerator
from parseval.symbolic.branch_tree import build_branch_tree
from parseval.symbolic.types import BranchTree, BranchType, CoverageTarget, BranchNode


SCHEMA = """
CREATE TABLE t1 (id INT, x INT);
CREATE TABLE t2 (id INT, x INT, y INT);
"""


class TestExistsConstraintGeneration(unittest.TestCase):
    def test_generates_constraint_for_exists_false(self):
        """ConstraintGenerator should produce constraints for EXISTS_FALSE."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t2", values={"x": 1, "y": 10})
        instance.create_row("t1", values={"x": 1})

        sql = "SELECT * FROM t1 WHERE EXISTS (SELECT * FROM t2 WHERE t2.x = t1.x)"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)

        # Create a CoverageTarget for EXISTS_FALSE
        exists_expr = exp.Exists(this=exp.Subquery(
            this=exp.select("*").from_("t2").where(exp.column("x", "t2").eq(exp.column("x", "t1")))
        ))

        node = BranchNode(
            step_id="test_step",
            step_type="SubPlan",
            site="exists",
            predicate=exists_expr,
            atoms=(exists_expr,),
            tables=("t1",),
        )

        target = CoverageTarget(
            node=node,
            atom_id=0,
            target_outcome=BranchType.EXISTS_FALSE,
        )

        gen = ConstraintGenerator(plan, instance, instance.dialect)
        constraint = gen.compile_target(target)

        self.assertIsNotNone(constraint, "Constraint should not be None")
        self.assertGreater(len(constraint.constraints), 0, "Should have constraints")


class TestDistinctConstraintGeneration(unittest.TestCase):
    def test_generates_constraint_for_distinct_duplicate(self):
        """ConstraintGenerator should produce constraints for DISTINCT_DUPLICATE."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t1", values={"x": 1})

        sql = "SELECT DISTINCT x FROM t1"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)

        node = BranchNode(
            step_id="test_step",
            step_type="Project",
            site="distinct",
            predicate=exp.Literal.string("DISTINCT"),
            atoms=(exp.Literal.string("DISTINCT"),),
            tables=("t1",),
        )

        target = CoverageTarget(
            node=node,
            atom_id=0,
            target_outcome=BranchType.DISTINCT_DUPLICATE,
        )

        gen = ConstraintGenerator(plan, instance, instance.dialect)
        constraint = gen.compile_target(target)

        self.assertIsNotNone(constraint, "Constraint should not be None")


class TestGroupConstraintGeneration(unittest.TestCase):
    def test_generates_constraint_for_group_multi(self):
        """ConstraintGenerator should produce constraints for GROUP_MULTI."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t1", values={"x": 1})

        sql = "SELECT x, COUNT(*) FROM t1 GROUP BY x"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)

        tree = build_branch_tree(plan, instance)
        target = next(
            target
            for target in tree.uncovered_targets
            if target.node.site == "group"
            and target.obligation is not None
            and target.obligation.metric == "group_size"
            and target.target_outcome == BranchType.GROUP_MULTI
        )

        gen = ConstraintGenerator(plan, instance, instance.dialect)
        constraint = gen.compile_target(target)

        self.assertIsNotNone(constraint, "Constraint should not be None")


class TestDatabaseConstraintGeneration(unittest.TestCase):
    def test_scan_obligation_avoids_all_existing_primary_keys(self):
        instance = Instance(
            ddls="CREATE TABLE t (id INT PRIMARY KEY NOT NULL, x INT);",
            name="test",
            dialect="sqlite",
        )
        for value in range(10):
            instance.create_row("t", values={"id": value, "x": value})

        sql = "SELECT x FROM t"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        tree = build_branch_tree(plan, instance)
        target = next(target for target in tree.root_witness_targets)

        constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

        avoid_constraints = [
            item
            for item in constraint.constraints
            if isinstance(item, exp.Not) and isinstance(item.this, exp.In)
        ]
        self.assertTrue(avoid_constraints)
        self.assertEqual(len(avoid_constraints[0].this.expressions), 10)

    def test_fk_constraints_preserve_each_required_row_scope(self):
        instance = Instance(
            ddls=(
                "CREATE TABLE parent (id INT PRIMARY KEY);"
                "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT, "
                "FOREIGN KEY (parent_id) REFERENCES parent(id));"
            ),
            name="test",
            dialect="sqlite",
        )
        instance.create_row("parent", values={"id": 1})

        sql = "SELECT COUNT(parent_id) FROM child"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            target
            for target in tree.uncovered_targets
            if target.node.site == "aggregate_input"
            and target.target_outcome == BranchType.DUPLICATE
        )

        constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

        fk_scopes = {
            solver_var(item.this).row_scope
            for item in constraint.constraints
            if isinstance(item, exp.In)
            and solver_var(item.this) is not None
            and solver_var(item.this).column_id.name.normalized == "parent_id"
        }
        self.assertEqual(fk_scopes, {"r0", "r1"})

    def test_unique_lookup_uses_stored_source_for_aliased_join_column(self):
        instance = Instance(
            ddls=(
                "CREATE TABLE account (account_id INT PRIMARY KEY, district_id INT);"
                "CREATE TABLE district (district_id INT PRIMARY KEY, A2 TEXT, A3 TEXT);"
                "CREATE TABLE loan (loan_id INT PRIMARY KEY, account_id INT);"
            ),
            name="test",
            dialect="sqlite",
        )
        instance.create_row("loan", values={"loan_id": 1, "account_id": 7})
        sql = (
            "SELECT T2.A2, T2.A3 "
            "FROM account AS T1 "
            "INNER JOIN district AS T2 ON T1.district_id = T2.district_id "
            "INNER JOIN loan AS T3 ON T1.account_id = T3.account_id "
            "WHERE T3.loan_id = 4990"
        )
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        tree = build_branch_tree(plan, instance)
        target = next(target for target in tree.uncovered_targets if target.node.site == "filter")

        constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

        self.assertIsNotNone(constraint)


class TestRowSetObligations(unittest.TestCase):
    def _compile_root_constraint(self, schema: str, sql: str) -> SolverConstraint:
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            t for t in tree.root_witness_targets if t.node.site == "root_result"
        )
        return ConstraintGenerator(plan, instance, instance.dialect).compile_target(
            target
        )

    def test_limit_offset_join_root_target_has_row_set_obligation(self):
        schema = (
            "CREATE TABLE a (id INT PRIMARY KEY, score INT);"
            "CREATE TABLE b (id INT PRIMARY KEY, label TEXT);"
        )
        sql = (
            "SELECT b.label FROM a JOIN b ON a.id = b.id "
            "ORDER BY a.score DESC LIMIT 5, 1"
        )
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            t for t in tree.root_witness_targets if t.node.site == "root_result"
        )
        row_sets = [
            obligation.row_set
            for obligation in target.node.obligations
            if obligation.kind == "row_set"
        ]

        self.assertEqual(len(row_sets), 1)
        self.assertEqual(row_sets[0].required_rows, 6)
        self.assertEqual(row_sets[0].generation_rows, 6)
        self.assertEqual(len(row_sets[0].row_scopes), 6)
        self.assertTrue(row_sets[0].join_facts)

    def test_large_offset_row_set_keeps_true_requirement_and_cap(self):
        schema = "CREATE TABLE schools (id INT PRIMARY KEY, zip TEXT, opened INT);"
        sql = "SELECT zip FROM schools ORDER BY opened DESC LIMIT 1 OFFSET 332"
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            t for t in tree.root_witness_targets if t.node.site == "root_result"
        )
        row_set = next(
            obligation.row_set
            for obligation in target.node.obligations
            if obligation.kind == "row_set"
        )

        self.assertEqual(row_set.required_rows, 333)
        self.assertEqual(row_set.generation_rows, 20)

    def test_having_count_join_target_has_grouped_row_set_obligation(self):
        schema = (
            "CREATE TABLE events (event_id INT PRIMARY KEY, category TEXT);"
            "CREATE TABLE attendees (id INT PRIMARY KEY, link_to_event INT);"
        )
        sql = (
            "SELECT T1.category FROM events AS T1 "
            "JOIN attendees AS T2 ON T1.event_id = T2.link_to_event "
            "GROUP BY T1.category HAVING COUNT(T2.link_to_event) > 20"
        )
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            t for t in tree.root_witness_targets if t.node.site == "root_result"
        )
        row_sets = [
            obligation.row_set
            for obligation in target.node.obligations
            if obligation.kind == "row_set"
        ]

        self.assertTrue(
            any(
                row_set.required_rows == 21 and row_set.group_keys
                for row_set in row_sets
            )
        )

    def test_row_set_lowering_scopes_each_joined_output_row(self):
        schema = (
            "CREATE TABLE a (id INT PRIMARY KEY, score INT);"
            "CREATE TABLE b (id INT PRIMARY KEY, label TEXT);"
        )
        sql = (
            "SELECT b.label FROM a JOIN b ON a.id = b.id "
            "ORDER BY a.score DESC LIMIT 5, 1"
        )
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            t for t in tree.root_witness_targets if t.node.site == "root_result"
        )

        constraint = ConstraintGenerator(
            plan, instance, instance.dialect
        ).compile_target(target)
        scopes = {
            solver_var(column).row_scope
            for expression in constraint.constraints
            for column in expression.find_all(exp.Column)
            if solver_var(column) is not None
        }

        self.assertTrue({"out0", "out1", "out2", "out3", "out4", "out5"} <= scopes)

    def test_having_row_set_does_not_make_one_group_key_unique_per_counted_row(self):
        schema = (
            "CREATE TABLE events (event_id INT PRIMARY KEY, event_name TEXT);"
            "CREATE TABLE attendance ("
            "link_to_event INT, link_to_member INT, "
            "FOREIGN KEY (link_to_event) REFERENCES events(event_id));"
        )
        sql = (
            "SELECT T1.event_name FROM events AS T1 "
            "JOIN attendance AS T2 ON T1.event_id = T2.link_to_event "
            "GROUP BY T1.event_id HAVING COUNT(T2.link_to_event) > 20"
        )

        constraint = self._compile_root_constraint(schema, sql)

        forbidden = []
        for expression in constraint.constraints:
            if not isinstance(expression, exp.NEQ):
                continue
            left = solver_var(expression.this)
            right = solver_var(expression.expression)
            if left is None or right is None:
                continue
            if (
                left.column_id.name.normalized == "event_id"
                and right.column_id.name.normalized == "event_id"
                and left.row_scope
                and right.row_scope
                and left.row_scope.startswith("having0_")
                and right.row_scope.startswith("having0_")
            ):
                forbidden.append((left.row_scope, right.row_scope))

        self.assertEqual(forbidden, [])

    def test_having_row_set_keeps_joined_unique_parent_on_one_logical_row(self):
        schema = (
            "CREATE TABLE members (member_id TEXT PRIMARY KEY, first_name TEXT);"
            "CREATE TABLE attendance ("
            "link_to_event TEXT, link_to_member TEXT, "
            "PRIMARY KEY (link_to_event, link_to_member), "
            "FOREIGN KEY (link_to_member) REFERENCES members(member_id));"
        )
        sql = (
            "SELECT T1.first_name FROM members AS T1 "
            "JOIN attendance AS T2 ON T1.member_id = T2.link_to_member "
            "GROUP BY T2.link_to_member HAVING COUNT(T2.link_to_event) > 7"
        )

        constraint = self._compile_root_constraint(schema, sql)

        member_scopes = {
            solver_var(column).row_scope
            for expression in constraint.constraints
            for column in expression.find_all(exp.Column)
            if solver_var(column) is not None
            and solver_var(column).column_id.name.normalized == "member_id"
            and solver_var(column).row_scope
            and solver_var(column).row_scope.startswith("having0_")
        }
        event_scopes = {
            solver_var(column).row_scope
            for expression in constraint.constraints
            for column in expression.find_all(exp.Column)
            if solver_var(column) is not None
            and solver_var(column).column_id.name.normalized == "link_to_event"
            and solver_var(column).row_scope
            and solver_var(column).row_scope.startswith("having0_")
        }

        self.assertEqual(member_scopes, {"having0_0"})
        self.assertGreaterEqual(len(event_scopes), 8)


if __name__ == "__main__":
    unittest.main()
