from __future__ import annotations

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.solver import Solver
from parseval.symbolic.constraints import ConstraintGenerator
from parseval.symbolic.evaluator import build_branch_tree
from parseval.symbolic.types import BranchTree, BranchType
from sqlglot import parse_one


JOIN_SCHEMA = """
CREATE TABLE frpm (
  CDSCode TEXT PRIMARY KEY,
  `District Name` TEXT,
  `Charter School (Y/N)` INT,
  `FRPM Count (K-12)` REAL
);
CREATE TABLE schools (
  CDSCode TEXT PRIMARY KEY,
  Zip TEXT,
  MailStreet TEXT,
  OpenDate DATE
);
"""


SUBQUERY_SCHEMA = """
CREATE TABLE frpm (
  CDSCode TEXT PRIMARY KEY,
  `FRPM Count (K-12)` REAL
);
CREATE TABLE satscores (
  cds TEXT PRIMARY KEY,
  NumTstTakr INT
);
"""


def _compile_uncovered(sql: str, schema: str, site: str | None = None):
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    if site is None:
        target = tree.uncovered_targets[0]
    else:
        target = next(t for t in tree.uncovered_targets if t.node.site == site)
    constraint = ConstraintGenerator(plan, instance).generate(target)
    return instance, plan, tree, target, constraint


def test_join_only_query_compiles_non_vacuous_join_path():
    sql = """
    SELECT T2.MailStreet
    FROM frpm AS T1
    INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
    ORDER BY T1.`FRPM Count (K-12)` DESC
    LIMIT 1
    """

    instance, plan, tree, target, constraint = _compile_uncovered(sql, JOIN_SCHEMA)

    assert target.node.site == "join_on"
    assert target.target_outcome in {BranchType.JOIN_MATCH, BranchType.ATOM_TRUE}
    assert constraint.join_equalities
    result = Solver().solve(constraint)
    assert result.sat, result.reason
    assert result.assignments


def test_positive_filter_path_conjoins_sibling_atoms_on_same_row():
    sql = """
    SELECT T2.Zip
    FROM frpm AS T1
    INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
    WHERE T1.`District Name` = 'Fresno County Office of Education'
      AND T1.`Charter School (Y/N)` = 1
    """

    instance, plan, tree, target, constraint = _compile_uncovered(
        sql,
        JOIN_SCHEMA,
        site="filter",
    )
    constraint_sql = " AND ".join(expr.sql() for expr in constraint.constraints)

    assert "District Name" in constraint_sql or "district name" in constraint_sql
    assert "Charter School (Y/N)" in constraint_sql or "charter school (y/n)" in constraint_sql
    assert constraint.join_equalities
    result = Solver().solve(constraint)
    assert result.sat, result.reason
    assignments = {var.column_id.name.normalized: value for var, value in result.assignments.items()}
    assert assignments["district name"] == "Fresno County Office of Education"
    assert assignments["charter school (y/n)"] == 1


def test_scalar_subquery_filter_builds_outer_inner_path():
    sql = """
    SELECT NumTstTakr
    FROM satscores
    WHERE cds = (
      SELECT CDSCode
      FROM frpm
      ORDER BY `FRPM Count (K-12)` DESC
      LIMIT 1
    )
    """

    instance = Instance(ddls=SUBQUERY_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)

    assert any(node.site == "scalar_subquery" for node in tree.nodes)
    target = next(t for t in tree.uncovered_targets if t.node.site == "scalar_subquery")
    constraint = ConstraintGenerator(plan, instance).generate(target)

    assert constraint.constraints or constraint.join_equalities
    result = Solver().solve(constraint)
    assert result.sat, result.reason
    names = {var.column_id.name.normalized for var in result.assignments}
    assert {"cds", "cdscode"} <= names


def test_row_path_coverage_dedupes_same_branch_for_same_row_path():
    sql = "SELECT * FROM frpm WHERE `District Name` = 'X'"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    instance.create_row("frpm", values={"CDSCode": "1", "District Name": "X"})
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)

    from parseval.symbolic.evaluator import PlanEvaluator

    evaluator = PlanEvaluator(plan, instance, "sqlite")
    tree = evaluator.evaluate(tree)
    tree = evaluator.evaluate(tree)

    node = next(node for node in tree.nodes if node.site == "filter")
    assert node.observation_count(0, BranchType.ATOM_TRUE) == 1


def test_branch_tree_keeps_distinct_sites_for_same_step_predicate():
    predicate = parse_one("a = 1")
    tree = BranchTree()

    first = tree.get_or_create_node(
        step_id="project_1",
        step_type="Project",
        site="case_arm",
        predicate=predicate,
        atoms=(predicate,),
    )
    second = tree.get_or_create_node(
        step_id="project_1",
        step_type="Project",
        site="distinct",
        predicate=predicate,
        atoms=(predicate,),
    )

    assert first is not second
    assert len(tree.nodes) == 2
