from __future__ import annotations

from sqlglot import exp

from parseval.constants import PlausibleBit
from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.solver import Solver
from parseval.solver.types import solver_var
from parseval.symbolic.constraints import ConstraintGenerator
from parseval.symbolic.engine import _materialized_rows
from parseval.symbolic.branch_tree import build_branch_tree
from parseval.symbolic.types import CoverageTarget


SCHEMA = "CREATE TABLE t (x INT, y INT);"
JOIN_SCHEMA = """
CREATE TABLE t (id INT, x INT);
CREATE TABLE u (id INT, x INT);
"""


def _filter_node(sql: str):
    instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    node = next(node for node in tree.nodes if node.site == "filter")
    return instance, plan, tree, node


def test_false_filter_target_compiles_to_satisfiable_constraint():
    instance, plan, tree, node = _filter_node("SELECT * FROM t WHERE x > 5")
    target = CoverageTarget(
        node=node,
        atom_id=0,
        target_outcome=PlausibleBit.FALSE,
    )

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
    result = Solver().solve(constraint)

    assert result.sat, result.reason
    value = result.assignments[
        next(var for var in result.assignments if var.column_id.name.normalized == "x")
    ]
    assert value <= 5


def test_null_filter_target_adds_null_requirement():
    instance, plan, tree, node = _filter_node("SELECT * FROM t WHERE x > 5")
    target = CoverageTarget(
        node=node,
        atom_id=0,
        target_outcome=PlausibleBit.NULL,
    )

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    assert any(isinstance(expr, exp.Is) and isinstance(expr.expression, exp.Null)
               for expr in constraint.constraints)


def test_coverage_target_exposes_relation_and_atom_identity():
    _instance, _plan, _tree, node = _filter_node("SELECT * FROM t WHERE x > 5")
    target = CoverageTarget(
        node=node,
        atom_id=0,
        target_outcome=PlausibleBit.TRUE,
    )

    assert tuple(rel.name.normalized for rel in target.node.tables) == ("t",)
    assert isinstance(target.atom, exp.GT)
    assert target.atom.this.name == "x"


def test_plausible_compiler_lowers_cached_join_equalities_to_solver_vars():
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    sql = "SELECT * FROM t JOIN u ON t.id = u.id WHERE t.x > 5"
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    node = next(node for node in tree.nodes if node.site == "filter")
    target = CoverageTarget(
        node=node,
        atom_id=0,
        target_outcome=PlausibleBit.FALSE,
    )

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    assert constraint.join_equalities
    assert all(len(pair) == 2 for pair in constraint.join_equalities)


def test_join_path_uses_relation_scoped_witness_rows():
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    sql = "SELECT * FROM t JOIN u ON t.id = u.id WHERE t.x > 5 AND u.x < 10"
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(t for t in tree.uncovered_targets if t.node.site == "filter")

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    join_scopes = {
        var.row_scope
        for pair in constraint.join_equalities
        for var in pair
    }
    predicate_scopes = {
        solver_var(col).row_scope
        for expr in constraint.constraints
        for col in expr.find_all(exp.Column)
        if solver_var(col) is not None
    }

    assert join_scopes == {"r0", "r1"}
    assert {"r0", "r1"} <= predicate_scopes


def test_distinct_branch_uses_relation_ids_and_row_scoped_solver_vars():
    instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
    sql = "SELECT DISTINCT x FROM t"
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    node = next(node for node in tree.nodes if node.site == "distinct")
    target = CoverageTarget(
        node=node,
        atom_id=0,
        target_outcome=PlausibleBit.DISTINCT_DUPLICATE,
    )

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    assert all(hasattr(rel, "name") for rel in constraint.target_relations)
    scopes = {
        solver_var(col).row_scope
        for expr in constraint.constraints
        for col in expr.find_all(exp.Column)
        if solver_var(col) is not None
    }
    assert {"r0", "r1"} <= scopes


def test_group_branch_uses_relation_ids_and_row_scoped_solver_vars():
    instance = Instance(ddls=SCHEMA, name="test", dialect="sqlite")
    sql = "SELECT x, COUNT(*) FROM t GROUP BY x"
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "group"
        and target.obligation is not None
        and target.obligation.metric == "group_size"
        and target.target_outcome == PlausibleBit.GROUP_MULTI
    )

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    assert all(hasattr(rel, "name") for rel in constraint.target_relations)
    scopes = {
        solver_var(col).row_scope
        for expr in constraint.constraints
        for col in expr.find_all(exp.Column)
        if solver_var(col) is not None
    }
    assert {"r0", "r1"} <= scopes


def test_group_count_multi_conjoins_upstream_filter_and_avoids_existing_group():
    instance = Instance(
        ddls="CREATE TABLE t (id INT PRIMARY KEY, x INT, y INT);",
        name="test",
        dialect="sqlite",
    )
    instance.create_row("t", values={"id": 1, "x": 7, "y": 1})
    sql = "SELECT x, COUNT(*) FROM t WHERE y = 1 GROUP BY x"
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "group"
        and target.obligation is not None
        and target.obligation.metric == "group_count"
        and target.target_outcome == PlausibleBit.GROUP_MULTI
    )

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
    sql_text = " AND ".join(expr.sql(dialect="sqlite") for expr in constraint.constraints)

    assert "y" in sql_text
    assert "= 1" in sql_text
    assert "x <> 7" in sql_text


def test_group_size_multi_uses_existing_group_key_when_available():
    instance = Instance(
        ddls="CREATE TABLE t (id INT PRIMARY KEY, x INT, y INT);",
        name="test",
        dialect="sqlite",
    )
    instance.create_row("t", values={"id": 1, "x": 7, "y": 1})
    sql = "SELECT x, COUNT(*) FROM t WHERE y = 1 GROUP BY x"
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "group"
        and target.obligation is not None
        and target.obligation.metric == "group_size"
        and target.target_outcome == PlausibleBit.GROUP_MULTI
    )

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
    sql_text = " AND ".join(expr.sql(dialect="sqlite") for expr in constraint.constraints)

    assert "y" in sql_text
    assert "= 1" in sql_text
    assert "x = 7" in sql_text


def test_compile_target_uses_atom_combination_constraints_for_positive_filter():
    instance, plan, tree, _node = _filter_node("SELECT * FROM t WHERE x > 5 AND y = 1")
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "filter"
        and target.atom_outcomes
        == ((0, PlausibleBit.TRUE), (1, PlausibleBit.TRUE))
    )

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
    sql = " AND ".join(expr.sql() for expr in constraint.constraints)

    assert "x" in sql
    assert "y" in sql


def test_scalar_subquery_same_table_primary_key_materializes_without_conflict():
    instance = Instance(
        ddls=(
            "CREATE TABLE account (account_id INT PRIMARY KEY);"
            "CREATE TABLE trans ("
            "trans_id INT PRIMARY KEY, account_id INT, date DATE, "
            "operation TEXT, amount INT"
            ");"
        ),
        name="test",
        dialect="sqlite",
    )
    sql = (
        "SELECT T1.account_id "
        "FROM trans AS T1 "
        "INNER JOIN account AS T2 ON T1.account_id = T2.account_id "
        "WHERE STRFTIME('%Y', T1.date) = '1998' "
        "AND T1.operation = 'VYBER KARTOU' "
        "AND T1.amount < ("
        "SELECT AVG(amount) FROM trans WHERE STRFTIME('%Y', date) = '1998'"
        ")"
    )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "filter"
        and target.atom_outcomes
        and all(outcome is PlausibleBit.TRUE for _atom_id, outcome in target.atom_outcomes)
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    result = Solver(dialect="sqlite").solve(constraint)

    assert result.sat, result.reason
    _materialized_rows(constraint, result.assignments)


def test_having_sum_positive_branch_uses_existing_same_table_group_as_reference():
    instance = Instance(
        ddls=(
            "CREATE TABLE trans ("
            "trans_id INT PRIMARY KEY, customer_id INT, date DATE, amount INT"
            ");"
        ),
        name="test",
        dialect="sqlite",
    )
    instance.create_row(
        "trans",
        values={
            "trans_id": 1,
            "customer_id": 7,
            "date": "1997-01-01",
            "amount": 8000,
        },
    )
    sql = (
        "SELECT customer_id "
        "FROM trans "
        "WHERE STRFTIME('%Y', date) = '1997' "
        "GROUP BY customer_id "
        "HAVING SUM(amount) > 10000"
    )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "having" and target.target_outcome == PlausibleBit.TRUE
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    result = Solver(dialect="sqlite").solve(constraint)

    assert result.sat, result.reason
    amount_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "amount"
    }
    customer_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "customer_id"
    }
    assert any(2000 < value <= 10000 for value in amount_values)
    assert 7 in customer_values


def test_having_avg_positive_branch_uses_existing_group_as_reference():
    instance = Instance(
        ddls=(
            "CREATE TABLE trans ("
            "trans_id INT PRIMARY KEY, customer_id INT, amount INT"
            ");"
        ),
        name="test",
        dialect="sqlite",
    )
    instance.create_row(
        "trans",
        values={"trans_id": 1, "customer_id": 7, "amount": 8},
    )
    sql = (
        "SELECT customer_id "
        "FROM trans "
        "GROUP BY customer_id "
        "HAVING AVG(amount) > 10"
    )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "having" and target.target_outcome == PlausibleBit.TRUE
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    result = Solver(dialect="sqlite").solve(constraint)

    assert result.sat, result.reason
    amount_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "amount"
    }
    customer_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "customer_id"
    }
    assert any(value > 12 for value in amount_values)
    assert 7 in customer_values


def test_having_min_positive_branch_uses_existing_group_as_reference():
    instance = Instance(
        ddls=(
            "CREATE TABLE trans ("
            "trans_id INT PRIMARY KEY, customer_id INT, amount INT"
            ");"
        ),
        name="test",
        dialect="sqlite",
    )
    instance.create_row(
        "trans",
        values={"trans_id": 1, "customer_id": 7, "amount": 20},
    )
    sql = (
        "SELECT customer_id "
        "FROM trans "
        "GROUP BY customer_id "
        "HAVING MIN(amount) > 10"
    )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "having" and target.target_outcome == PlausibleBit.TRUE
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    result = Solver(dialect="sqlite").solve(constraint)

    assert result.sat, result.reason
    amount_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "amount"
    }
    customer_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "customer_id"
    }
    assert any(value > 10 for value in amount_values)
    assert 7 in customer_values


def test_having_max_positive_branch_uses_existing_group_as_reference():
    instance = Instance(
        ddls=(
            "CREATE TABLE trans ("
            "trans_id INT PRIMARY KEY, customer_id INT, amount INT"
            ");"
        ),
        name="test",
        dialect="sqlite",
    )
    instance.create_row(
        "trans",
        values={"trans_id": 1, "customer_id": 7, "amount": 8},
    )
    sql = (
        "SELECT customer_id "
        "FROM trans "
        "GROUP BY customer_id "
        "HAVING MAX(amount) > 10"
    )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "having" and target.target_outcome == PlausibleBit.TRUE
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    result = Solver(dialect="sqlite").solve(constraint)

    assert result.sat, result.reason
    amount_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "amount"
    }
    customer_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "customer_id"
    }
    assert any(value > 10 for value in amount_values)
    assert 7 in customer_values


def test_having_count_positive_branch_uses_existing_group_as_reference():
    instance = Instance(
        ddls=(
            "CREATE TABLE trans ("
            "trans_id INT PRIMARY KEY, customer_id INT, amount INT"
            ");"
        ),
        name="test",
        dialect="sqlite",
    )
    instance.create_row(
        "trans",
        values={"trans_id": 1, "customer_id": 7, "amount": 8},
    )
    instance.create_row(
        "trans",
        values={"trans_id": 2, "customer_id": 7, "amount": 9},
    )
    sql = (
        "SELECT customer_id "
        "FROM trans "
        "GROUP BY customer_id "
        "HAVING COUNT(amount) > 2"
    )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "having" and target.target_outcome == PlausibleBit.TRUE
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    result = Solver(dialect="sqlite").solve(constraint)

    assert result.sat, result.reason
    amount_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "amount"
    }
    customer_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "customer_id"
    }
    assert amount_values
    assert None not in amount_values
    assert 7 in customer_values


def test_having_sum_positive_branch_falls_back_when_existing_group_is_unsafe():
    instance = Instance(
        ddls=(
            "CREATE TABLE account (account_id INT PRIMARY KEY, district_id INT);"
            "CREATE TABLE district (district_id INT PRIMARY KEY);"
            "CREATE TABLE trans (trans_id INT PRIMARY KEY, account_id INT, date DATE, amount INT);"
        ),
        name="test",
        dialect="sqlite",
    )
    instance.create_row("district", values={"district_id": 7})
    instance.create_row("account", values={"account_id": 1, "district_id": 7})
    instance.create_row(
        "trans",
        values={
            "trans_id": 1,
            "account_id": 1,
            "date": "1997-01-01",
            "amount": 8000,
        },
    )
    sql = (
        "SELECT T1.district_id "
        "FROM account AS T1 "
        "INNER JOIN district AS T2 ON T1.district_id = T2.district_id "
        "INNER JOIN trans AS T3 ON T1.account_id = T3.account_id "
        "WHERE STRFTIME('%Y', T3.date) = '1997' "
        "GROUP BY T1.district_id "
        "HAVING SUM(T3.amount) > 10000"
    )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "having" and target.target_outcome == PlausibleBit.TRUE
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    result = Solver(dialect="sqlite").solve(constraint)

    assert result.sat, result.reason
    amount_values = {
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "amount"
    }
    assert any(value > 10000 for value in amount_values)
