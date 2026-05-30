"""Tests for constraint generation for new branch types."""

from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.solver.unified import SolverConstraint
from parseval.symbolic.constraints import ConstraintGenerator
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
        plan = Plan(expr)

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

        gen = ConstraintGenerator(plan, instance, "sqlite")
        constraint = gen.generate(target)

        self.assertIsNotNone(constraint, "Constraint should not be None")
        self.assertGreater(len(constraint.constraints), 0, "Should have constraints")


class TestDistinctConstraintGeneration(unittest.TestCase):
    def test_generates_constraint_for_distinct_duplicate(self):
        """ConstraintGenerator should produce constraints for DISTINCT_DUPLICATE."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t1", values={"x": 1})

        sql = "SELECT DISTINCT x FROM t1"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)

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

        gen = ConstraintGenerator(plan, instance, "sqlite")
        constraint = gen.generate(target)

        self.assertIsNotNone(constraint, "Constraint should not be None")


class TestGroupConstraintGeneration(unittest.TestCase):
    def test_generates_constraint_for_group_multi(self):
        """ConstraintGenerator should produce constraints for GROUP_MULTI."""
        instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
        instance.create_row("t1", values={"x": 1})

        sql = "SELECT x, COUNT(*) FROM t1 GROUP BY x"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)

        node = BranchNode(
            step_id="test_step",
            step_type="Aggregate",
            site="group",
            predicate=exp.Literal.number(1),
            atoms=(exp.Literal.number(1),),
            tables=("t1",),
        )

        target = CoverageTarget(
            node=node,
            atom_id=0,
            target_outcome=BranchType.GROUP_MULTI,
        )

        gen = ConstraintGenerator(plan, instance, "sqlite")
        constraint = gen.generate(target)

        self.assertIsNotNone(constraint, "Constraint should not be None")


if __name__ == "__main__":
    unittest.main()
