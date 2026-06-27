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


if __name__ == "__main__":
    unittest.main()
