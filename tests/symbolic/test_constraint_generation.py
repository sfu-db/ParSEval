"""Tests for constraint generation for new branch types."""

from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.identity import ColumnKind
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


class TestSemanticBranchConstraintGeneration(unittest.TestCase):
    def _compile_target(
        self,
        schema: str,
        sql: str,
        site: str,
        outcome: BranchType,
        metric: str | None = None,
    ) -> SolverConstraint:
        instance = Instance(ddls=schema, name="test", dialect="sqlite")
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            target
            for target in tree.uncovered_targets
            if target.node.site == site
            and target.target_outcome == outcome
            and (
                metric is None
                or (
                    target.obligation is not None
                    and target.obligation.metric == metric
                )
            )
        )
        return ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    def test_case_taken_compiles_to_arm_predicate(self):
        constraint = self._compile_target(
            "CREATE TABLE t (id INT PRIMARY KEY, a INT);",
            "SELECT CASE WHEN a > 5 THEN 'big' ELSE 'small' END FROM t",
            "case_arm",
            BranchType.CASE_ARM_TAKEN,
        )

        self.assertTrue(any(isinstance(item, exp.GT) for item in constraint.constraints))

    def test_case_skipped_compiles_to_negated_arm_predicate(self):
        constraint = self._compile_target(
            "CREATE TABLE t (id INT PRIMARY KEY, a INT);",
            "SELECT CASE WHEN a > 5 THEN 'big' ELSE 'small' END FROM t",
            "case_arm",
            BranchType.CASE_ARM_SKIPPED,
        )

        self.assertTrue(
            any(
                isinstance(item, exp.LTE)
                or (isinstance(item, exp.Not) and isinstance(item.this, exp.GT))
                for item in constraint.constraints
            )
        )

    def test_join_fanout_emits_equal_many_side_key_and_distinct_row_identity(self):
        constraint = self._compile_target(
            (
                "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);"
                "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);"
            ),
            "SELECT parent.name FROM parent JOIN child ON parent.id = child.parent_id",
            "join_on",
            BranchType.DUPLICATE,
        )

        key_equalities = []
        identity_differences = []
        for expression in constraint.constraints:
            if isinstance(expression, exp.EQ):
                columns = list(expression.find_all(exp.Column))
                vars_ = [solver_var(column) for column in columns]
                if (
                    len(vars_) == 2
                    and {var.row_scope for var in vars_} == {"r0", "r1"}
                    and all(var.column_id.name.normalized == "parent_id" for var in vars_)
                ):
                    key_equalities.append(expression)
            if isinstance(expression, exp.NEQ):
                columns = list(expression.find_all(exp.Column))
                vars_ = [solver_var(column) for column in columns]
                if (
                    len(vars_) == 2
                    and {var.row_scope for var in vars_} == {"r0", "r1"}
                    and all(var.column_id.name.normalized == "id" for var in vars_)
                ):
                    identity_differences.append(expression)

        self.assertTrue(key_equalities)
        self.assertTrue(identity_differences)

    def test_project_duplicate_emits_equal_project_values_and_distinct_row_identity(self):
        constraint = self._compile_target(
            "CREATE TABLE t (id INT PRIMARY KEY, code TEXT, name TEXT);",
            "SELECT code, name FROM t",
            "root_result",
            BranchType.DUPLICATE,
            metric="project_duplicate",
        )

        project_equalities = []
        identity_differences = []
        for expression in constraint.constraints:
            if isinstance(expression, exp.EQ):
                left = solver_var(expression.this)
                right = solver_var(expression.expression)
                if (
                    left is not None
                    and right is not None
                    and {left.row_scope, right.row_scope} == {"out0", "out1"}
                    and left.column_id.name.normalized in {"code", "name"}
                    and right.column_id.name.normalized == left.column_id.name.normalized
                ):
                    project_equalities.append(expression)
            if isinstance(expression, exp.NEQ):
                left = solver_var(expression.this)
                right = solver_var(expression.expression)
                if (
                    left is not None
                    and right is not None
                    and {left.row_scope, right.row_scope} == {"out0", "out1"}
                    and left.column_id.name.normalized == "id"
                    and right.column_id.name.normalized == "id"
                ):
                    identity_differences.append(expression)

        self.assertGreaterEqual(len(project_equalities), 2)
        self.assertTrue(identity_differences)

    def test_count_distinct_duplicate_emits_equal_counted_value_and_distinct_rows(self):
        constraint = self._compile_target(
            "CREATE TABLE t (pk INT PRIMARY KEY, id INT);",
            "SELECT COUNT(DISTINCT id) FROM t",
            "aggregate_output",
            BranchType.DUPLICATE,
            metric="count_distinct_duplicate",
        )

        counted_equalities = []
        identity_differences = []
        counted_not_null = []
        for expression in constraint.constraints:
            if isinstance(expression, exp.EQ):
                left = solver_var(expression.this)
                right = solver_var(expression.expression)
                if (
                    left is not None
                    and right is not None
                    and {left.row_scope, right.row_scope} == {"r0", "r1"}
                    and left.column_id.name.normalized == "id"
                    and right.column_id.name.normalized == "id"
                ):
                    counted_equalities.append(expression)
            if isinstance(expression, exp.NEQ):
                left = solver_var(expression.this)
                right = solver_var(expression.expression)
                if (
                    left is not None
                    and right is not None
                    and {left.row_scope, right.row_scope} == {"r0", "r1"}
                    and left.column_id.name.normalized == "pk"
                    and right.column_id.name.normalized == "pk"
                ):
                    identity_differences.append(expression)
            if isinstance(expression, exp.Is):
                value = solver_var(expression.this)
                if value is not None and value.column_id.name.normalized == "id":
                    counted_not_null.append(expression)

        self.assertTrue(counted_equalities)
        self.assertTrue(identity_differences)
        self.assertGreaterEqual(len(counted_not_null), 2)

    def test_case_positive_strategy_target_includes_when_predicate(self):
        constraint = self._compile_target(
            "CREATE TABLE t (id INT PRIMARY KEY, a INT);",
            "SELECT SUM(CASE WHEN a > 5 THEN 1 ELSE 0 END) FROM t",
            "case_arm",
            BranchType.CASE_ARM_TAKEN,
            metric="case_positive",
        )

        self.assertTrue(any(isinstance(item, exp.GT) for item in constraint.constraints))

    def test_rank_tie_emits_equal_non_null_order_values_and_distinct_rows(self):
        constraint = self._compile_target(
            "CREATE TABLE schools (id INT PRIMARY KEY, opened INT);",
            "SELECT id FROM schools ORDER BY opened DESC LIMIT 1",
            "root_result",
            BranchType.DUPLICATE,
            metric="rank_tie",
        )

        order_equalities = []
        identity_differences = []
        order_not_null = []
        for expression in constraint.constraints:
            if isinstance(expression, exp.EQ):
                left = solver_var(expression.this)
                right = solver_var(expression.expression)
                if (
                    left is not None
                    and right is not None
                    and {left.row_scope, right.row_scope} == {"out0", "out1"}
                    and left.column_id.name.normalized == "opened"
                    and right.column_id.name.normalized == "opened"
                ):
                    order_equalities.append(expression)
            if isinstance(expression, exp.NEQ):
                left = solver_var(expression.this)
                right = solver_var(expression.expression)
                if (
                    left is not None
                    and right is not None
                    and {left.row_scope, right.row_scope} == {"out0", "out1"}
                    and left.column_id.name.normalized == "id"
                    and right.column_id.name.normalized == "id"
                ):
                    identity_differences.append(expression)
            if isinstance(expression, exp.Is):
                value = solver_var(expression.this)
                if value is not None and value.column_id.name.normalized == "opened":
                    order_not_null.append(expression)

        self.assertTrue(order_equalities)
        self.assertTrue(identity_differences)
        self.assertGreaterEqual(len(order_not_null), 2)

    def test_rank_contrast_emits_non_null_order_values_and_winner_comparison(self):
        constraint = self._compile_target(
            "CREATE TABLE schools (id INT PRIMARY KEY, opened INT);",
            "SELECT id FROM schools ORDER BY opened DESC LIMIT 1 OFFSET 1",
            "root_result",
            BranchType.ATOM_TRUE,
        )

        scoped_order_columns = [
            solver_var(column)
            for expression in constraint.constraints
            for column in expression.find_all(exp.Column)
            if solver_var(column) is not None
            and solver_var(column).column_id.name.normalized == "opened"
        ]
        comparisons = [
            expression
            for expression in constraint.constraints
            if isinstance(expression, exp.GTE)
            and solver_var(expression.this) is not None
            and solver_var(expression.expression) is not None
            and solver_var(expression.this).row_scope == "out0"
            and solver_var(expression.expression).row_scope == "out1"
        ]

        self.assertTrue({"out0", "out1"} <= {var.row_scope for var in scoped_order_columns})
        self.assertTrue(comparisons)

    def test_aggregate_contrast_emits_grouped_count_rows(self):
        constraint = self._compile_target(
            "CREATE TABLE sales (id INT PRIMARY KEY, category TEXT);",
            "SELECT category, COUNT(id) FROM sales GROUP BY category",
            "aggregate_output",
            BranchType.DUPLICATE,
        )

        group_equalities = [
            expression
            for expression in constraint.constraints
            if isinstance(expression, exp.EQ)
            and solver_var(expression.this) is not None
            and solver_var(expression.expression) is not None
            and solver_var(expression.this).column_id.name.normalized == "category"
            and {solver_var(expression.this).row_scope, solver_var(expression.expression).row_scope}
            == {"r0", "r1"}
        ]
        counted_not_null = [
            expression
            for expression in constraint.constraints
            if isinstance(expression, exp.Is)
            and solver_var(expression.this) is not None
            and solver_var(expression.this).column_id.name.normalized == "id"
        ]

        self.assertTrue(group_equalities)
        self.assertGreaterEqual(len(counted_not_null), 2)

    def test_aggregate_contrast_emits_sum_value_contrast(self):
        constraint = self._compile_target(
            "CREATE TABLE sales (id INT PRIMARY KEY, category TEXT, amount INT);",
            "SELECT category, SUM(amount) FROM sales GROUP BY category",
            "aggregate_output",
            BranchType.DUPLICATE,
        )

        value_differences = [
            expression
            for expression in constraint.constraints
            if isinstance(expression, exp.NEQ)
            and solver_var(expression.this) is not None
            and solver_var(expression.expression) is not None
            and solver_var(expression.this).column_id.name.normalized == "amount"
            and {solver_var(expression.this).row_scope, solver_var(expression.expression).row_scope}
            == {"r0", "r1"}
        ]

        self.assertTrue(value_differences)

    def test_ranked_join_antimatch_emits_top_neq_match_eq_and_ordering(self):
        constraint = self._compile_target(
            (
                "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT, score INT);"
                "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);"
            ),
            (
                "SELECT parent.name FROM parent "
                "JOIN child ON parent.id = child.parent_id "
                "ORDER BY parent.score DESC LIMIT 1"
            ),
            "join_on",
            BranchType.JOIN_LEFT,
        )

        top_neq = []
        match_eq = []
        ordering = []
        for expression in constraint.constraints:
            if isinstance(expression, exp.NEQ):
                left = solver_var(expression.this)
                right = solver_var(expression.expression)
                if left is not None and right is not None and {left.row_scope, right.row_scope} == {"rank_top"}:
                    top_neq.append(expression)
            if isinstance(expression, exp.EQ):
                left = solver_var(expression.this)
                right = solver_var(expression.expression)
                if left is not None and right is not None and {left.row_scope, right.row_scope} == {"rank_match"}:
                    match_eq.append(expression)
            if isinstance(expression, exp.GTE):
                left = solver_var(expression.this)
                right = solver_var(expression.expression)
                if (
                    left is not None
                    and right is not None
                    and left.column_id.name.normalized == "score"
                    and right.column_id.name.normalized == "score"
                    and left.row_scope == "rank_top"
                    and right.row_scope == "rank_match"
                ):
                    ordering.append(expression)

        self.assertTrue(top_neq)
        self.assertTrue(match_eq)
        self.assertTrue(ordering)

    def test_group_count_multi_emits_group_key_inequality_without_same_group_equality(self):
        constraint = self._compile_target(
            "CREATE TABLE sales (id INT PRIMARY KEY, category TEXT, amount INT);",
            "SELECT category, COUNT(id) FROM sales GROUP BY category",
            "group",
            BranchType.GROUP_MULTI,
            metric="group_count",
        )

        group_neq = []
        group_eq = []
        for expression in constraint.constraints:
            if not isinstance(expression, (exp.EQ, exp.NEQ)):
                continue
            left = solver_var(expression.this)
            right = solver_var(expression.expression)
            if (
                left is None
                or right is None
                or left.column_id.name.normalized != "category"
                or right.column_id.name.normalized != "category"
                or {left.row_scope, right.row_scope} != {"r0", "r1"}
            ):
                continue
            if isinstance(expression, exp.NEQ):
                group_neq.append(expression)
            else:
                group_eq.append(expression)

        self.assertTrue(group_neq)
        self.assertEqual(group_eq, [])


class TestLogicalSolverVariables(unittest.TestCase):
    def test_physical_predicate_columns_use_logical_solver_variables_with_storage_lineage(self):
        instance = Instance(
            ddls="CREATE TABLE users (id INT PRIMARY KEY, LastAccessDate TEXT);",
            name="test",
            dialect="sqlite",
        )
        sql = "SELECT COUNT(id) FROM users WHERE date(LastAccessDate) > '2014-09-01'"
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
        tree = build_branch_tree(plan, instance)
        target = next(target for target in tree.root_witness_targets)

        constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

        variables = [
            solver_var(column)
            for expression in constraint.constraints
            for column in expression.find_all(exp.Column)
            if solver_var(column) is not None
            and solver_var(column).column_id.name.normalized == "lastaccessdate"
        ]
        self.assertTrue(variables)
        self.assertTrue(all(variable.column_id.kind is not ColumnKind.PHYSICAL for variable in variables))
        self.assertTrue(all(variable.column_id.source_column_id is not None for variable in variables))
        self.assertTrue(all(variable.column_id.source_column_id.kind is ColumnKind.PHYSICAL for variable in variables))


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

    def test_table_level_check_constraints_use_existing_path_variables(self):
        instance = Instance(
            ddls=(
                "CREATE TABLE follow ("
                "followee INT, follower INT, "
                "CONSTRAINT check_follow CHECK (followee <> follower)"
                ");"
            ),
            name="test",
            dialect="sqlite",
        )

        sql = "SELECT * FROM follow WHERE followee > 0 AND follower > 0"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            target
            for target in tree.uncovered_targets
            if target.node.site == "filter"
            and target.target_outcome == BranchType.TRUE
        )

        constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

        check_scopes = {
            (
                solver_var(item.this).row_scope,
                solver_var(item.expression).row_scope,
            )
            for item in constraint.constraints
            if isinstance(item, exp.NEQ)
            and solver_var(item.this) is not None
            and solver_var(item.expression) is not None
            and solver_var(item.this).column_id.name.normalized == "followee"
            and solver_var(item.expression).column_id.name.normalized == "follower"
        }
        self.assertEqual(check_scopes, {(None, None)})

    def test_table_level_check_constraints_skip_missing_path_variables(self):
        instance = Instance(
            ddls=(
                "CREATE TABLE follow ("
                "followee INT, follower INT, "
                "CONSTRAINT check_follow CHECK (followee <> follower)"
                ");"
            ),
            name="test",
            dialect="sqlite",
        )

        sql = "SELECT COUNT(followee) FROM follow"
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

        check_constraints = [
            item
            for item in constraint.constraints
            if isinstance(item, exp.NEQ)
            and isinstance(item.this, exp.Column)
            and isinstance(item.expression, exp.Column)
            and item.this.name == "followee"
            and item.expression.name == "follower"
        ]
        self.assertEqual(check_constraints, [])

    def test_mysql_check_constraint_uses_existing_path_variable(self):
        instance = Instance(
            ddls=(
                "CREATE TABLE activity ("
                "player_id INT, games_played INT, "
                "CHECK (games_played >= 0)"
                ");"
            ),
            name="test",
            dialect="mysql",
        )

        sql = "SELECT player_id FROM activity WHERE games_played = 1"
        expr = preprocess_sql(sql, instance, dialect="mysql")
        plan = Plan(expr, instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            target
            for target in tree.uncovered_targets
            if target.node.site == "filter"
            and target.target_outcome == BranchType.TRUE
        )

        constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

        check_constraints = [
            item
            for item in constraint.constraints
            if isinstance(item, exp.GTE)
            and solver_var(item.this) is not None
            and solver_var(item.this).column_id.name.normalized == "games_played"
        ]
        self.assertEqual(len(check_constraints), 1)

    def test_mysql_check_constraint_skips_missing_path_variable(self):
        instance = Instance(
            ddls=(
                "CREATE TABLE activity ("
                "player_id INT, games_played INT, "
                "CHECK (games_played >= 0)"
                ");"
            ),
            name="test",
            dialect="mysql",
        )

        sql = "SELECT player_id FROM activity WHERE player_id = 1"
        expr = preprocess_sql(sql, instance, dialect="mysql")
        plan = Plan(expr, instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            target
            for target in tree.uncovered_targets
            if target.node.site == "filter"
            and target.target_outcome == BranchType.TRUE
        )

        constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

        check_constraints = [
            item
            for item in constraint.constraints
            if isinstance(item, exp.GTE)
            and isinstance(item.this, exp.Column)
            and item.this.name == "games_played"
        ]
        self.assertEqual(check_constraints, [])

    def test_table_level_check_constraints_preserve_self_join_alias_scope(self):
        instance = Instance(
            ddls=(
                "CREATE TABLE follow ("
                "followee INT, follower INT, "
                "CONSTRAINT check_follow CHECK (followee <> follower)"
                ");"
            ),
            name="test",
            dialect="sqlite",
        )

        sql = (
            "SELECT * FROM follow AS t1 "
            "INNER JOIN follow AS t2 ON t1.follower = t2.followee "
            "WHERE t1.followee > 0 AND t1.follower > 0 "
            "AND t2.followee > 0 AND t2.follower > 0"
        )
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        tree = build_branch_tree(plan, instance)
        target = next(
            target
            for target in tree.uncovered_targets
            if target.node.site == "filter"
            and target.atom_outcomes
            and all(outcome == BranchType.TRUE for _atom, outcome in target.atom_outcomes)
        )

        constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

        check_bindings = {
            (
                solver_var(item.this).relation_id.alias.normalized,
                solver_var(item.this).row_scope,
                solver_var(item.expression).relation_id.alias.normalized,
                solver_var(item.expression).row_scope,
            )
            for item in constraint.constraints
            if isinstance(item, exp.NEQ)
            and solver_var(item.this) is not None
            and solver_var(item.expression) is not None
            and solver_var(item.this).column_id.name.normalized == "followee"
            and solver_var(item.expression).column_id.name.normalized == "follower"
        }
        self.assertEqual(check_bindings, {("t1", "r0", "t1", "r0"), ("t2", "r1", "t2", "r1")})

    def test_unsupported_table_level_check_raises_explicit_error(self):
        instance = Instance(
            ddls="CREATE TABLE t (x INT, CHECK ((SELECT 1) = 1));",
            name="test",
            dialect="sqlite",
        )

        sql = "SELECT * FROM t WHERE x > 0"
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)
        tree = build_branch_tree(plan, instance)
        target = next(target for target in tree.uncovered_targets if target.node.site == "filter")

        with self.assertRaisesRegex(ValueError, "unsupported_database_check:subquery"):
            ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

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

    def test_limit_offset_row_set_carries_ordering_obligation(self):
        schema = "CREATE TABLE schools (id INT PRIMARY KEY, zip TEXT, opened INT);"
        sql = "SELECT zip FROM schools ORDER BY opened DESC LIMIT 1 OFFSET 2"
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

        self.assertEqual(row_set.required_rows, 3)
        self.assertEqual(len(row_set.ordering), 1)
        self.assertEqual(row_set.ordering[0].this.name, "opened")

    def test_row_set_ordering_lowers_to_rank_constraints(self):
        schema = "CREATE TABLE schools (id INT PRIMARY KEY, zip TEXT, opened INT);"
        sql = "SELECT zip FROM schools ORDER BY opened DESC LIMIT 1 OFFSET 2"
        constraint = self._compile_root_constraint(schema, sql)

        rank_constraints = []
        for expression in constraint.constraints:
            if not isinstance(expression, exp.GTE):
                continue
            left = solver_var(expression.this)
            right = solver_var(expression.expression)
            if left is None or right is None:
                continue
            if (
                left.column_id.name.normalized == "opened"
                and right.column_id.name.normalized == "opened"
                and left.row_scope == "out0"
                and right.row_scope in {"out1", "out2"}
            ):
                rank_constraints.append(right.row_scope)

        self.assertEqual(set(rank_constraints), {"out1", "out2"})

    def test_capped_large_offset_row_set_does_not_emit_partial_rank_constraints(self):
        schema = "CREATE TABLE schools (id INT PRIMARY KEY, zip TEXT, opened INT);"
        sql = "SELECT zip FROM schools ORDER BY opened DESC LIMIT 1 OFFSET 332"
        constraint = self._compile_root_constraint(schema, sql)

        rank_constraints = []
        for expression in constraint.constraints:
            if not isinstance(expression, exp.GTE):
                continue
            left = solver_var(expression.this)
            right = solver_var(expression.expression)
            if left is None or right is None:
                continue
            if (
                left.column_id.name.normalized == "opened"
                and right.column_id.name.normalized == "opened"
                and left.row_scope == "out0"
                and right.row_scope
                and right.row_scope.startswith("out")
            ):
                rank_constraints.append(right.row_scope)

        self.assertEqual(rank_constraints, [])

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
