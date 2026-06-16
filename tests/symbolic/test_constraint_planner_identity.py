from __future__ import annotations

import pytest
from sqlglot import exp

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.solver.types import solver_var
from parseval.symbolic.constraints import ConstraintGenerator, UnsupportedConstraintTarget
from parseval.symbolic.evaluator import build_branch_tree
from parseval.symbolic.types import BranchType, CoverageThresholds


def _plan(sql: str, ddl: str):
    instance = Instance(ddls=ddl, name='test', dialect='sqlite')
    expr = preprocess_sql(sql, instance, dialect='sqlite')
    plan = Plan(expr, instance)
    return instance, plan


def test_constraint_columns_keep_planner_relation_identity_for_alias_self_join():
    ddl = """
    CREATE TABLE employee (id INT PRIMARY KEY, manager_id INT, name TEXT);
    """
    sql = """
    SELECT e.name
    FROM employee AS e
    JOIN employee AS m ON e.manager_id = m.id
    WHERE m.name = 'Ada'
    """
    instance, plan = _plan(sql, ddl)
    tree = build_branch_tree(plan, instance, CoverageThresholds(atom_null=0))
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == 'filter'
        and target.target_outcome == BranchType.ATOM_TRUE
    )

    constraint = ConstraintGenerator(plan, instance).generate(target)

    solver_vars = [
        solver_var(col)
        for expr in constraint.constraints
        for col in expr.find_all(exp.Column)
        if solver_var(col) is not None
    ]
    aliases = {
        sv.relation_id.alias.normalized
        for sv in solver_vars
        if sv.relation_id.alias
    }
    assert 'm' in aliases
    assert 'e' not in aliases


def test_unresolved_constraint_column_raises_instead_of_guessing_first_table(monkeypatch):
    ddl = "CREATE TABLE t (a INT);"
    sql = "SELECT * FROM t WHERE a = 1"
    instance, plan = _plan(sql, ddl)
    tree = build_branch_tree(plan, instance)
    target = next(target for target in tree.uncovered_targets if target.node.site == 'filter')
    compiler = ConstraintGenerator(plan, instance)

    monkeypatch.setattr(
        'parseval.symbolic.constraints.column_identity',
        lambda col: None,
    )

    with pytest.raises(UnsupportedConstraintTarget, match='missing planner identity'):
        compiler.generate(target)


def test_distinct_complex_projection_is_explicitly_unsupported():
    ddl = "CREATE TABLE t (name TEXT);"
    sql = "SELECT DISTINCT SUBSTR(name, 1, 1) FROM t"
    instance, plan = _plan(sql, ddl)
    tree = build_branch_tree(
        plan,
        instance,
        CoverageThresholds(atom_true=0, atom_false=0, atom_null=0, distinct_duplicate=1),
    )
    target = next(target for target in tree.uncovered_targets if target.node.site == 'distinct')

    with pytest.raises(UnsupportedConstraintTarget, match='complex DISTINCT expression'):
        ConstraintGenerator(plan, instance).generate(target)

def test_missing_catalog_type_for_constraint_column_surfaces_error(monkeypatch):
    ddl = "CREATE TABLE t (a INT);"
    sql = "SELECT * FROM t WHERE a = 1"
    instance, plan = _plan(sql, ddl)
    tree = build_branch_tree(plan, instance)
    target = next(target for target in tree.uncovered_targets if target.node.site == 'filter')

    def broken_catalog_column(*args, **kwargs):
        raise KeyError("missing catalog")

    monkeypatch.setattr(instance, "catalog_column", broken_catalog_column)

    with pytest.raises(KeyError, match="missing catalog"):
        ConstraintGenerator(plan, instance).generate(target)
