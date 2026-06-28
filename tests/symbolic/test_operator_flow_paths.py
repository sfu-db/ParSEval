from __future__ import annotations

import pytest

from parseval.identity import ColumnKind, RelationKind, identifier_name, relation_id
from parseval.instance import Instance
from parseval.plan import Filter, Plan
from parseval.query import preprocess_sql
from parseval.solver import Solver
from parseval.solver.types import solver_var
from parseval.symbolic.branch_tree import (
    BranchCoverageRecorder,
    BranchPathBuilder,
    BranchTreeBuilder,
    CoverageAnalyzer,
    build_branch_tree,
    decompose_atoms,
)
from parseval.symbolic.constraints import ConstraintGenerator
from parseval.symbolic.evaluator import PlanEvaluator
from parseval.symbolic.engine import SymbolicEngine
from parseval.symbolic.types import AtomObservation, BranchTree, BranchType, CoverageThresholds
from sqlglot import exp, parse_one


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
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
    return instance, plan, tree, target, constraint


def _normalized_identifier(value: str, dialect: str = "sqlite") -> str:
    return identifier_name(value, dialect=dialect).normalized


def _constraint_column_names(constraint):
    names = set()
    for expression in constraint.constraints:
        for column in expression.find_all(exp.Column):
            identifier = column.args.get("this")
            if not isinstance(identifier, exp.Identifier):
                identifier = exp.to_identifier(column.name)
            names.add(identifier_name(identifier, dialect="sqlite").normalized)
    return names


def test_branchless_order_limit_query_has_root_witness_target():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)

    targets = tree.root_witness_targets

    assert targets
    assert targets[0].node.site == "root_result"


def test_branch_tree_builder_is_topology_owner_behind_convenience_function():
    sql = "SELECT Zip FROM schools WHERE Zip = '94110' ORDER BY OpenDate DESC LIMIT 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    built = BranchTreeBuilder(plan, instance).build()
    from_function = build_branch_tree(plan, instance)

    assert [(node.step_id, node.site, node.predicate_sql) for node in built.nodes] == [
        (node.step_id, node.site, node.predicate_sql) for node in from_function.nodes
    ]
    assert built.nodes


def test_branch_tree_builder_marks_planned_nodes():
    sql = "SELECT Zip FROM schools WHERE Zip = '94110'"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    tree = BranchTreeBuilder(plan, instance).build()

    assert tree.nodes
    assert {node.discovery for node in tree.nodes} == {"planned"}
    assert all(node.origin.startswith("planner:") for node in tree.nodes)


def test_branch_coverage_recorder_marks_runtime_nodes():
    tree = BranchTree()
    recorder = BranchCoverageRecorder(tree)
    predicate = parse_one("a = 1")

    node = recorder.runtime_node(
        step_id="runtime_1",
        step_type="Runtime",
        site="scalar_subquery",
        predicate=predicate,
        atoms=(predicate,),
        origin="evaluator:scalar_subquery",
    )

    assert node.discovery == "runtime"
    assert node.origin == "evaluator:scalar_subquery"


def test_evaluator_marks_nodes_runtime_when_tree_was_not_planned():
    sql = "SELECT Zip FROM schools WHERE Zip = '94110'"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    tree = PlanEvaluator(plan, instance).evaluate(BranchTree())

    filter_node = next(node for node in tree.nodes if node.site == "filter")
    assert filter_node.discovery == "runtime"


def test_coverage_analyzer_and_path_builder_wrap_branch_tree_policy():
    sql = "SELECT Zip FROM schools WHERE Zip = '94110'"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    analyzer = CoverageAnalyzer(tree)

    target = next(target for target in analyzer.uncovered_targets if target.node.site == "filter")
    path = BranchPathBuilder().path_for_target(target)

    assert analyzer.uncovered_targets == tree.uncovered_targets
    assert analyzer.root_witness_targets == tree.root_witness_targets
    assert analyzer.fully_covered == tree.fully_covered
    wrapped_path = tree.path_for_target(target)
    assert [(p.expression.sql(), p.outcome) for p in path.predicates] == [
        (p.expression.sql(), p.outcome) for p in wrapped_path.predicates
    ]
    assert path.join_facts == wrapped_path.join_facts
    assert path.obligations == wrapped_path.obligations


def test_evaluator_without_tree_starts_from_planner_topology():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    tree = PlanEvaluator(plan, instance).evaluate()

    assert any(node.site == "root_result" for node in tree.nodes)


def test_evaluator_records_case_coverage_on_planned_branch_node():
    schema = """
    CREATE TABLE t (
      id INT PRIMARY KEY,
      a INT
    );
    """
    sql = "SELECT CASE WHEN a > 5 THEN 'big' ELSE 'small' END FROM t"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    instance.create_row("t", values={"id": 1, "a": 10})
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = BranchTreeBuilder(plan, instance).build()
    case_nodes = [node for node in tree.nodes if node.site == "case_arm"]

    evaluated = PlanEvaluator(plan, instance).evaluate(tree)
    evaluated_case_nodes = [node for node in evaluated.nodes if node.site == "case_arm"]

    assert len(case_nodes) == 1
    assert evaluated_case_nodes == case_nodes
    assert case_nodes[0].observation_count(-1, BranchType.CASE_ARM_TAKEN) == 1


def test_root_witness_target_conjoins_upstream_filter_predicate():
    sql = "SELECT Zip FROM schools WHERE Zip = '94110' ORDER BY OpenDate DESC LIMIT 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(target for target in tree.root_witness_targets if target.node.site == "root_result")

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
    constraint_sql = " AND ".join(expr.sql() for expr in constraint.constraints)

    assert "94110" in constraint_sql


def test_root_witness_keeps_filter_parent_when_filter_has_scalar_subquery():
    schema = """
    CREATE TABLE frpm (
      CDSCode TEXT PRIMARY KEY,
      Enrollment INT
    );
    CREATE TABLE schools (
      CDSCode TEXT PRIMARY KEY,
      FundingType TEXT,
      School TEXT
    );
    """
    sql = """
    SELECT T2.School
    FROM frpm AS T1
    INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
    WHERE T2.FundingType = 'Locally funded'
      AND T1.Enrollment > (SELECT AVG(T3.Enrollment) FROM frpm AS T3)
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(target for target in tree.root_witness_targets if target.node.site == "root_result")

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
    constraint_sql = " AND ".join(expr.sql(dialect="sqlite") for expr in constraint.constraints)

    assert "Locally funded" in constraint_sql


def test_quoted_cased_root_table_produces_scan_obligation():
    schema = """
    CREATE TABLE "Player_Attributes" (
      "id" INT PRIMARY KEY,
      player_api_id INT,
      overall_rating INT
    );
    """
    sql = "SELECT player_api_id FROM Player_Attributes ORDER BY overall_rating DESC LIMIT 1"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(target for target in tree.root_witness_targets if target.node.site == "root_result")

    row_sets = [
        obligation.row_set
        for obligation in target.node.obligations
        if obligation.kind == "row_set"
    ]

    assert row_sets
    assert row_sets[0].relations


def test_infeasible_root_witness_does_not_block_other_targets():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    root_target = next(target for target in tree.root_witness_targets if target.node.site == "root_result")

    tree.mark_infeasible(root_target.node, root_target.atom_id, root_target.target_outcome)

    assert root_target not in tree.root_witness_targets


def test_branchless_order_limit_engine_generates_non_empty_result():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    engine = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=5)

    result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
    rows = PlanEvaluator(Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance), instance).evaluate_context().tables

    assert result.rows_generated > 0
    assert len(next(iter(rows.values())).rows) == 1


def test_project_order_limit_root_obligation_uses_output_lineage():
    schema = """
    CREATE TABLE frpm (
      CDSCode TEXT PRIMARY KEY,
      `School Name` TEXT,
      Enrollment INT
    );
    """
    sql = """
    SELECT `School Name`
    FROM frpm
    ORDER BY Enrollment DESC
    LIMIT 1 OFFSET 1
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    engine = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=5)

    result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 2
    assert len(instance.get_rows("frpm")) >= 2
    assert len(next(iter(output.tables.values())).rows) >= 1


def test_having_count_threshold_generates_enough_rows_for_group():
    schema = """
    CREATE TABLE sales (
      id INT PRIMARY KEY,
      category TEXT
    );
    """
    sql = "SELECT category FROM sales GROUP BY category HAVING COUNT(id) > 3"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    engine = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=20)

    result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 4
    assert len(instance.get_rows("sales")) >= 4
    assert len(next(iter(output.tables.values())).rows) == 1


def test_having_count_threshold_over_join_generates_same_group_rows():
    schema = """
    CREATE TABLE events (
      event_id INT PRIMARY KEY,
      category TEXT
    );
    CREATE TABLE attendees (
      id INT PRIMARY KEY,
      link_to_event INT,
      FOREIGN KEY (link_to_event) REFERENCES events(event_id)
    );
    """
    sql = """
    SELECT T1.category
    FROM events AS T1
    INNER JOIN attendees AS T2 ON T1.event_id = T2.link_to_event
    GROUP BY T1.category
    HAVING COUNT(T2.link_to_event) > 20
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=30,
        max_rows_per_table=30,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 22
    assert len(instance.get_rows("attendees")) >= 21
    assert len(next(iter(output.tables.values())).rows) == 1


def test_having_count_threshold_over_join_allows_counted_fk_as_first_child_column():
    schema = """
    CREATE TABLE events (
      event_id INT PRIMARY KEY,
      event_name TEXT
    );
    CREATE TABLE attendance (
      link_to_event INT,
      link_to_member INT,
      FOREIGN KEY (link_to_event) REFERENCES events(event_id)
    );
    """
    sql = """
    SELECT T1.event_name
    FROM events AS T1
    INNER JOIN attendance AS T2 ON T1.event_id = T2.link_to_event
    GROUP BY T1.event_id
    HAVING COUNT(T2.link_to_event) > 20
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=30,
        max_rows_per_table=30,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 22
    assert len(instance.get_rows("attendance")) >= 21
    assert len(next(iter(output.tables.values())).rows) == 1


def test_having_count_threshold_over_composite_key_generates_distinct_key_tuples():
    schema = """
    CREATE TABLE members (
      member_id TEXT PRIMARY KEY,
      first_name TEXT
    );
    CREATE TABLE attendance (
      link_to_event TEXT,
      link_to_member TEXT,
      PRIMARY KEY (link_to_event, link_to_member),
      FOREIGN KEY (link_to_member) REFERENCES members(member_id)
    );
    """
    sql = """
    SELECT T1.first_name
    FROM members AS T1
    INNER JOIN attendance AS T2 ON T1.member_id = T2.link_to_member
    GROUP BY T2.link_to_member
    HAVING COUNT(T2.link_to_event) > 7
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=30,
        max_rows_per_table=30,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 9
    assert len(instance.get_rows("attendance")) >= 8
    assert len(next(iter(output.tables.values())).rows) >= 1


def test_having_count_distinct_threshold_generates_distinct_group_values():
    schema = """
    CREATE TABLE venues (
      venue_id INT PRIMARY KEY,
      city TEXT
    );
    CREATE TABLE events (
      event_id INT PRIMARY KEY,
      venue_id INT,
      FOREIGN KEY (venue_id) REFERENCES venues(venue_id)
    );
    """
    sql = """
    SELECT T1.city
    FROM venues AS T1
    INNER JOIN events AS T4 ON T1.venue_id = T4.venue_id
    GROUP BY T1.city
    HAVING COUNT(DISTINCT T4.event_id) > 1
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=20,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()
    event_ids = {
        row["event_id"].concrete
        for row in instance.get_rows("events")
        if row["event_id"].concrete is not None
    }

    assert result.rows_generated >= 3
    assert len(event_ids) >= 2
    assert len(next(iter(output.tables.values())).rows) >= 1


def test_query_required_rows_override_zero_coverage_thresholds():
    schema = """
    CREATE TABLE sales (
      id INT PRIMARY KEY,
      category TEXT
    );
    """
    sql = "SELECT category FROM sales GROUP BY category HAVING COUNT(id) > 3"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    engine = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=20)

    result = engine.generate(
        thresholds=CoverageThresholds(
            atom_true=0,
            atom_false=0,
            atom_null=0,
            having_pass=0,
            having_fail=0,
            group_single=0,
            group_multi=0,
            project_null=0,
            project_non_null=0,
            aggregate_null=0,
            aggregate_non_null=0,
            aggregate_duplicate=0,
        ),
        speculate_first=False,
    )
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 4
    assert len(instance.get_rows("sales")) >= 4
    assert len(next(iter(output.tables.values())).rows) == 1


def test_scalar_subquery_aggregate_join_generates_inner_witness_group():
    schema = """
    CREATE TABLE frpm (
      CDSCode TEXT PRIMARY KEY,
      EnrollmentK12 INT,
      Enrollment517 INT
    );
    CREATE TABLE schools (
      CDSCode TEXT PRIMARY KEY,
      FundingType TEXT,
      School TEXT
    );
    """
    sql = """
    SELECT T2.School
    FROM frpm AS T1
    INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
    WHERE T2.FundingType = 'Locally funded'
      AND (T1.EnrollmentK12 - T1.Enrollment517) > (
        SELECT AVG(T3.EnrollmentK12 - T3.Enrollment517)
        FROM frpm AS T3
        INNER JOIN schools AS T4 ON T3.CDSCode = T4.CDSCode
        WHERE T4.FundingType = 'Locally funded'
      )
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    engine = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=20)

    result = engine.generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 4
    assert len(next(iter(output.tables.values())).rows) >= 1


def test_scalar_subquery_sum_count_ratio_uses_inner_source_witness():
    schema = """
    CREATE TABLE Team (
      id INT PRIMARY KEY,
      team_api_id INT,
      team_long_name TEXT
    );
    CREATE TABLE Team_Attributes (
      id INT PRIMARY KEY,
      team_api_id INT,
      date TEXT,
      buildUpPlayPassing INT
    );
    """
    sql = """
    SELECT DISTINCT T4.team_long_name
    FROM Team_Attributes AS T3
    INNER JOIN Team AS T4 ON T3.team_api_id = T4.team_api_id
    WHERE SUBSTR(T3.date, 1, 4) = '2012'
      AND T3.buildUpPlayPassing > (
        SELECT CAST(SUM(T2.buildUpPlayPassing) AS REAL) / COUNT(T1.id)
        FROM Team AS T1
        INNER JOIN Team_Attributes AS T2 ON T1.team_api_id = T2.team_api_id
        WHERE SUBSTR(T2.date, 1, 4) = '2012'
      )
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(target for target in tree.root_witness_targets if target.node.site == "root_result")

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    assert not any("_col_0" in expr.sql() for expr in constraint.constraints)
    assert _normalized_identifier("buildUpPlayPassing") in _constraint_column_names(constraint)

    result = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=6).generate(
        thresholds=CoverageThresholds(atom_null=0)
    )
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated > 0
    assert len(next(iter(output.tables.values())).rows) >= 1


def test_scalar_subquery_count_ratio_evaluates_inner_aggregate_scope():
    schema = """
    CREATE TABLE posts (
      PostId INT PRIMARY KEY
    );
    CREATE TABLE users (
      UserId INT PRIMARY KEY
    );
    """
    sql = """
    SELECT COUNT(T1.PostId) * 1.0 / (SELECT COUNT(UserId) FROM users) AS ratio
    FROM posts AS T1
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    instance.create_row("posts", values={"PostId": 1})
    instance.create_row("users", values={"UserId": 10})
    instance.create_row("users", values={"UserId": 11})
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)

    output = PlanEvaluator(plan, instance).evaluate_context()

    assert len(next(iter(output.tables.values())).rows) == 1


def test_alias_scoped_solver_vars_materialize_to_physical_table():
    sql = "SELECT T1.Zip FROM schools AS T1 ORDER BY T1.OpenDate DESC LIMIT 1"
    instance, _plan, _tree, _target, constraint = _compile_uncovered(sql, JOIN_SCHEMA, site="root_result")

    result = Solver().solve(constraint)

    assert result.sat, result.reason
    assert result.assignments
    assert all(var.relation_id.alias is not None for var in result.assignments)
    assert constraint.storage_relations
    assert {
        storage.name.normalized
        for storage in constraint.storage_relations.values()
        if storage.name is not None
    } == {"schools"}


def test_limit_offset_materializes_distinct_row_scopes():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 1 OFFSET 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    engine = SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=5)

    result = engine.generate(thresholds=CoverageThresholds(atom_null=0))
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 2
    assert len(instance.get_rows("schools")) >= 2
    assert len(next(iter(output.tables.values())).rows) == 1


def test_limit_comma_offset_root_result_requires_all_surviving_rows():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 5, 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    for index in range(5):
        instance.create_row(
            "schools",
            values={
                "CDSCode": f"school-{index}",
                "Zip": f"old-{index}",
                "OpenDate": f"2020-01-0{index + 1}",
            },
        )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = PlanEvaluator(plan, instance).evaluate()
    root_node = next(node for node in tree.nodes if node.site == "root_result")

    assert root_node.observation_count(0, BranchType.ATOM_TRUE) == 0
    assert any(
        target.node is root_node
        and target.target_outcome == BranchType.ATOM_TRUE
        for target in tree.root_witness_targets
    )


def test_limit_comma_offset_join_engine_generates_required_final_rows():
    sql = """
    SELECT T2.MailStreet, T2.Zip
    FROM frpm AS T1
    INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
    ORDER BY T1.`FRPM Count (K-12)` DESC
    LIMIT 5, 1
    """
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=20,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert len(next(iter(output.tables.values())).rows) == 1


def test_root_result_records_one_observation_per_final_output_row():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 2"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    for index in range(2):
        instance.create_row(
            "schools",
            values={
                "CDSCode": f"school-{index}",
                "Zip": f"zip-{index}",
                "OpenDate": f"2020-01-0{index + 1}",
            },
        )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    tree = PlanEvaluator(plan, instance).evaluate()
    root_node = next(node for node in tree.nodes if node.site == "root_result")

    assert root_node.observation_count(0, BranchType.ATOM_TRUE) == 2


def test_large_limit_offset_root_obligation_keeps_true_requirement_uncovered():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 1 OFFSET 332"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(target for target in tree.root_witness_targets if target.node.site == "root_result")

    root_row_counts = {
        obligation.row_count
        for obligation in target.node.obligations
        if obligation.kind == "root_result"
    }
    row_set = next(
        obligation.row_set
        for obligation in target.node.obligations
        if obligation.kind == "row_set"
    )

    assert root_row_counts == {333}
    assert row_set.required_rows == 333
    assert row_set.generation_rows == 20


def test_root_scan_obligation_avoids_existing_identity_values():
    sql = "SELECT Zip FROM schools ORDER BY OpenDate DESC LIMIT 1"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    instance.create_row("schools", values={"CDSCode": "value", "Zip": "old"})
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(target for target in tree.root_witness_targets if target.node.site == "root_result")

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
    result = Solver().solve(constraint)

    assert result.sat, result.reason
    cdscode_value = next(
        value
        for var, value in result.assignments.items()
        if var.column_id.name.normalized == "cdscode"
    )
    assert cdscode_value != "value"


def test_filter_branch_scan_obligations_keep_primary_keys_unique():
    schema = """
    CREATE TABLE people (
      id TEXT PRIMARY KEY,
      first_name TEXT,
      last_name TEXT,
      website TEXT
    );
    """
    sql = """
    SELECT website
    FROM people
    WHERE (first_name = 'Mike' AND last_name = 'Larson')
       OR (first_name = 'Dante' AND last_name = 'Alvarez')
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(instance, sql, dialect="sqlite", max_iterations=6).generate(
        thresholds=CoverageThresholds(atom_null=0)
    )

    ids = [row["id"].concrete for row in instance.get_rows("people")]
    assert len(ids) == len(set(ids))


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
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    assert constraint.constraints or constraint.join_equalities
    result = Solver().solve(constraint)
    assert result.sat, result.reason
    names = {var.column_id.name.normalized for var in result.assignments}
    assert {"cds", "cdscode"} <= names


def test_scalar_subquery_join_path_keeps_join_key_datatypes():
    schema = """
    CREATE TABLE client (
      client_id INTEGER PRIMARY KEY,
      gender TEXT NOT NULL,
      district_id INTEGER NOT NULL,
      FOREIGN KEY (district_id) REFERENCES district (district_id)
    );
    CREATE TABLE district (
      district_id INTEGER PRIMARY KEY,
      A15 INTEGER
    );
    """
    sql = """
    SELECT COUNT(T1.client_id)
    FROM client AS T1
    INNER JOIN district AS T2 ON T1.district_id = T2.district_id
    WHERE T1.gender = 'M'
      AND T2.A15 = (
        SELECT T3.A15
        FROM district AS T3
        ORDER BY T3.A15 DESC
        LIMIT 1 OFFSET 1
      )
    """

    instance, plan, tree, target, constraint = _compile_uncovered(
        sql,
        schema,
        site="scalar_subquery",
    )

    assert constraint.join_equalities
    for left_var, right_var in constraint.join_equalities:
        if left_var.column_id.name.normalized == "district_id":
            assert constraint.variables[left_var].is_type("INT")
            assert constraint.variables[right_var].is_type("INT")


def test_scalar_subquery_query_has_uncovered_target_before_generation():
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

    assert not tree.fully_covered
    assert any(target.node.site == "scalar_subquery" for target in tree.uncovered_targets)


def test_in_match_constraint_generates_coordinated_outer_and_inner_rows():
    schema = """
    CREATE TABLE atom (
      atom_id TEXT PRIMARY KEY,
      element TEXT
    );
    CREATE TABLE connected (
      atom_id TEXT,
      bond_id TEXT
    );
    """
    sql = """
    SELECT T2.bond_id
    FROM atom AS T1
    INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
    WHERE T2.bond_id IN (
      SELECT T3.bond_id
      FROM connected AS T3
      INNER JOIN atom AS T4 ON T3.atom_id = T4.atom_id
      WHERE T4.element = 'p'
    )
      AND T1.element = 'n'
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "in" and target.target_outcome == BranchType.IN_MATCH
    )

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    assert constraint.constraints
    assert not any(expression is exp.false() for expression in constraint.constraints)
    constraint_sql = " AND ".join(expression.sql() for expression in constraint.constraints)
    assert "element" in constraint_sql
    assert "'p'" in constraint_sql
    names = {
        solver_var(column).column_id.name.normalized
        for expression in constraint.constraints
        for column in expression.find_all(exp.Column)
        if solver_var(column) is not None
    }
    assert {"bond_id", "atom_id", "element"} <= names
    assert constraint.join_equalities
    assert {
        var.column_id.name.normalized
        for equality in constraint.join_equalities
        for var in equality
    } >= {"atom_id"}
    assert any(
        isinstance(expression, exp.NEQ)
        and solver_var(expression.this) is not None
        and solver_var(expression.expression) is not None
        and solver_var(expression.this).column_id.name.normalized == "atom_id"
        and solver_var(expression.expression).column_id.name.normalized == "atom_id"
        and {
            solver_var(expression.this).row_scope,
            solver_var(expression.expression).row_scope,
        }
        == {"in_outer", "in_inner"}
        for expression in constraint.constraints
    )
    result = Solver().solve(constraint)
    assert result.sat, result.reason


def test_in_match_engine_generation_makes_outer_filter_non_empty():
    schema = """
    CREATE TABLE atom (
      atom_id TEXT PRIMARY KEY,
      element TEXT
    );
    CREATE TABLE connected (
      atom_id TEXT,
      bond_id TEXT
    );
    """
    sql = """
    SELECT T2.bond_id
    FROM atom AS T1
    INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
    WHERE T2.bond_id IN (
      SELECT T3.bond_id
      FROM connected AS T3
      INNER JOIN atom AS T4 ON T3.atom_id = T4.atom_id
      WHERE T4.element = 'p'
    )
      AND T1.element = 'n'
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=8,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated > 0
    assert len(next(iter(output.tables.values())).rows) >= 1


def test_in_trace_binds_outer_and_inner_rows_for_match():
    schema = """
    CREATE TABLE atom (
      atom_id TEXT PRIMARY KEY,
      element TEXT
    );
    CREATE TABLE connected (
      atom_id TEXT,
      bond_id TEXT
    );
    """
    sql = """
    SELECT T2.bond_id
    FROM atom AS T1
    INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
    WHERE T2.bond_id IN (
      SELECT T3.bond_id
      FROM connected AS T3
      INNER JOIN atom AS T4 ON T3.atom_id = T4.atom_id
      WHERE T4.element = 'p'
    )
      AND T1.element = 'n'
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    instance.create_row("atom", values={"atom_id": "outer-atom", "element": "n"})
    instance.create_row("connected", values={"atom_id": "outer-atom", "bond_id": "bond-1"})
    instance.create_row("atom", values={"atom_id": "inner-atom", "element": "p"})
    instance.create_row("connected", values={"atom_id": "inner-atom", "bond_id": "bond-1"})
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)

    tree = PlanEvaluator(plan, instance).evaluate()
    in_node = next(node for node in tree.nodes if node.site == "in")
    traces = [
        trace
        for trace in tree.traces_for_node(in_node)
        if trace.outcome == BranchType.IN_MATCH
    ]

    assert traces
    assert any(len(trace.input_row_ids) >= 2 for trace in traces)
    assert tree.root_output_lineages()


def test_disconnected_in_observations_do_not_create_root_lineage():
    schema = """
    CREATE TABLE atom (
      atom_id TEXT PRIMARY KEY,
      element TEXT
    );
    CREATE TABLE connected (
      atom_id TEXT,
      bond_id TEXT
    );
    """
    sql = """
    SELECT T2.bond_id
    FROM atom AS T1
    INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
    WHERE T2.bond_id IN (
      SELECT T3.bond_id
      FROM connected AS T3
      INNER JOIN atom AS T4 ON T3.atom_id = T4.atom_id
      WHERE T4.element = 'p'
    )
      AND T1.element = 'n'
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    instance.create_row("atom", values={"atom_id": "outer-atom", "element": "n"})
    instance.create_row("connected", values={"atom_id": "outer-atom", "bond_id": "bond-outer"})
    instance.create_row("atom", values={"atom_id": "inner-atom", "element": "p"})
    instance.create_row("connected", values={"atom_id": "inner-atom", "bond_id": "bond-inner"})
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)

    tree = PlanEvaluator(plan, instance).evaluate()
    in_node = next(node for node in tree.nodes if node.site == "in")

    assert in_node.observation_count(0, BranchType.IN_NO_MATCH) >= 1
    assert not tree.root_output_lineages()


def test_root_row_set_uses_existing_in_subquery_values_for_outer_row():
    schema = """
    CREATE TABLE atom (
      atom_id TEXT PRIMARY KEY,
      element TEXT
    );
    CREATE TABLE connected (
      atom_id TEXT,
      bond_id TEXT
    );
    """
    sql = """
    SELECT T2.bond_id
    FROM atom AS T1
    INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
    WHERE T2.bond_id IN (
      SELECT T3.bond_id
      FROM connected AS T3
      INNER JOIN atom AS T4 ON T3.atom_id = T4.atom_id
      WHERE T4.element = 'p'
    )
      AND T1.element = 'n'
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    instance.create_row("atom", values={"atom_id": "inner-atom", "element": "p"})
    instance.create_row("connected", values={"atom_id": "inner-atom", "bond_id": "bond-1"})

    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=4,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated > 0
    assert len(next(iter(output.tables.values())).rows) >= 1


def test_root_path_unwraps_scalar_aggregate_subquery_expression():
    schema = """
    CREATE TABLE frpm (
      CDSCode TEXT PRIMARY KEY,
      EnrollmentK12 REAL,
      EnrollmentAges REAL
    );
    CREATE TABLE schools (
      CDSCode TEXT PRIMARY KEY,
      FundingType TEXT,
      School TEXT
    );
    """
    sql = """
    SELECT T2.School
    FROM frpm AS T1
    INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
    WHERE T2.FundingType = 'Locally funded'
      AND (T1.EnrollmentK12 - T1.EnrollmentAges) > (
        SELECT AVG(T3.EnrollmentK12 - T3.EnrollmentAges)
        FROM frpm AS T3
        INNER JOIN schools AS T4 ON T3.CDSCode = T4.CDSCode
        WHERE T4.FundingType = 'Locally funded'
      )
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(plan, instance)
    target = next(target for target in tree.root_witness_targets if target.node.site == "root_result")

    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    assert not any(expr.find(exp.Subquery) for expr in constraint.constraints)
    result = Solver().solve(constraint)
    assert result.sat, result.reason
    names = {var.column_id.name.normalized for var in result.assignments}
    assert {"enrollmentk12", "enrollmentages"} <= names


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


def test_build_branch_tree_propagates_annotation_errors(monkeypatch):
    sql = "SELECT * FROM frpm WHERE CDSCode = '1'"
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    original_annotation_for = plan.annotation_for

    def broken_annotation_for(step):
        if isinstance(step, Filter):
            raise KeyError("broken annotation")
        return original_annotation_for(step)

    monkeypatch.setattr(plan, "annotation_for", broken_annotation_for)

    import pytest

    with pytest.raises(KeyError, match="broken annotation"):
        build_branch_tree(plan, instance)


def test_filter_predicate_outcomes_are_atom_combination_targets():
    sql = """
    SELECT *
    FROM frpm
    WHERE CDSCode = '1' OR `District Name` = 'x'
    """
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(
        plan,
        instance,
        CoverageThresholds(
            atom_true=1,
            atom_false=1,
            atom_null=0,
            filter_true=1,
            filter_false=1,
            filter_null=1,
        ),
    )

    targets = [
        target
        for target in tree.uncovered_targets
        if target.node.site == "filter"
    ]

    assert all(target.atom_outcomes for target in targets)
    assert {target.atom_outcomes for target in targets} == {
        ((0, BranchType.ATOM_TRUE), (1, BranchType.ATOM_TRUE)),
        ((0, BranchType.ATOM_TRUE), (1, BranchType.ATOM_FALSE)),
        ((0, BranchType.ATOM_FALSE), (1, BranchType.ATOM_TRUE)),
        ((0, BranchType.ATOM_FALSE), (1, BranchType.ATOM_FALSE)),
    }


def test_infeasible_atom_combination_target_is_excluded():
    sql = """
    SELECT *
    FROM frpm
    WHERE CDSCode = '1' AND `District Name` = 'x'
    """
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(
        plan,
        instance,
        CoverageThresholds(atom_true=1, atom_false=1, atom_null=0),
    )
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "filter"
        and target.atom_outcomes
        == ((0, BranchType.ATOM_TRUE), (1, BranchType.ATOM_FALSE))
    )

    tree.mark_target_infeasible(target)

    assert target.atom_outcomes not in {
        candidate.atom_outcomes
        for candidate in tree.uncovered_targets
        if candidate.node is target.node
    }


def test_atom_combination_coverage_ratio_uses_combination_target_count():
    predicate = parse_one("a = 1 AND b = 2 AND c = 3")
    tree = BranchTree(thresholds=CoverageThresholds(atom_true=1, atom_false=1, atom_null=0))
    node = tree.get_or_create_node(
        step_id="step_0",
        step_type="Filter",
        site="filter",
        predicate=predicate,
        atoms=decompose_atoms(predicate),
    )

    assert len(tree.uncovered_targets) == 8
    assert tree.total_targets == 8
    assert tree.covered_count == 0
    assert tree.coverage_ratio == 0.0

    row_ids = ("r0",)
    for atom_id in range(3):
        tree.record_observation(
            node,
            AtomObservation(
                atom_id=atom_id,
                outcome=BranchType.ATOM_TRUE,
                row_ids=row_ids,
            ),
        )

    assert tree.covered_count == 1
    assert tree.coverage_ratio == 0.125


def test_multi_atom_filter_targets_atom_outcome_combinations():
    sql = """
    SELECT *
    FROM frpm
    WHERE CDSCode = '1'
      AND `District Name` = 'x'
      AND `Charter School (Y/N)` = 1
    """
    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    tree = build_branch_tree(
        plan,
        instance,
        CoverageThresholds(atom_true=1, atom_false=1, atom_null=0),
    )

    combo_targets = [
        target
        for target in tree.uncovered_targets
        if target.node.site == "filter" and target.atom_outcomes
    ]
    target = next(
        target
        for target in combo_targets
        if target.atom_outcomes
        == (
            (0, BranchType.ATOM_TRUE),
            (1, BranchType.ATOM_FALSE),
            (2, BranchType.ATOM_FALSE),
        )
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    assert len(combo_targets) == 8
    assert {
        _normalized_identifier("CDSCode"),
        _normalized_identifier("District Name"),
        _normalized_identifier("Charter School (Y/N)"),
    } <= _constraint_column_names(constraint)


def test_group_and_distinct_operator_outcomes_are_coverage_targets():
    group_sql = "SELECT `District Name`, COUNT(*) FROM frpm GROUP BY `District Name`"
    group_instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    group_expr = preprocess_sql(group_sql, group_instance, dialect="sqlite")
    group_plan = Plan(group_expr, group_instance)
    group_tree = build_branch_tree(
        group_plan,
        group_instance,
        CoverageThresholds(atom_true=0, atom_false=0, atom_null=0),
    )
    group_targets = [
        target
        for target in group_tree.uncovered_targets
        if target.node.site == "group"
    ]

    assert {
        (target.obligation.metric, target.target_outcome)
        for target in group_targets
        if target.obligation is not None
    } == {
        ("group_size", BranchType.GROUP_SINGLE),
        ("group_size", BranchType.GROUP_MULTI),
        ("group_count", BranchType.GROUP_SINGLE),
        ("group_count", BranchType.GROUP_MULTI),
    }

    distinct_sql = "SELECT DISTINCT `District Name` FROM frpm"
    distinct_instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    distinct_expr = preprocess_sql(distinct_sql, distinct_instance, dialect="sqlite")
    distinct_plan = Plan(distinct_expr, distinct_instance)
    distinct_tree = build_branch_tree(
        distinct_plan,
        distinct_instance,
        CoverageThresholds(
            atom_true=0,
            atom_false=0,
            atom_null=0,
            distinct_unique=1,
            distinct_duplicate=1,
        ),
    )
    distinct_targets = [
        target
        for target in distinct_tree.uncovered_targets
        if target.node.site == "distinct"
    ]

    assert {(target.atom_id, target.target_outcome) for target in distinct_targets} == {
        (0, BranchType.DISTINCT_UNIQUE),
        (0, BranchType.DISTINCT_DUPLICATE),
    }


def test_engine_generates_rows_for_join_only_query():
    sql = """
    SELECT T2.MailStreet
    FROM frpm AS T1
    INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
    ORDER BY T1.`FRPM Count (K-12)` DESC
    LIMIT 1
    """
    from parseval.symbolic.engine import SymbolicEngine
    from parseval.symbolic.types import CoverageThresholds

    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    result = SymbolicEngine(instance, sql, "sqlite", max_iterations=3).generate(
        CoverageThresholds(atom_null=0)
    )
    assert result.rows_generated >= 2


def test_engine_generates_single_row_satisfying_conjunctive_filter_path():
    sql = """
    SELECT T2.Zip
    FROM frpm AS T1
    INNER JOIN schools AS T2 ON T1.CDSCode = T2.CDSCode
    WHERE T1.`District Name` = 'Fresno County Office of Education'
      AND T1.`Charter School (Y/N)` = 1
    """
    from parseval.symbolic.engine import SymbolicEngine
    from parseval.symbolic.types import CoverageThresholds

    instance = Instance(ddls=JOIN_SCHEMA, name="test", dialect="sqlite")
    result = SymbolicEngine(instance, sql, "sqlite", max_iterations=3).generate(
        CoverageThresholds(atom_null=0)
    )
    assert result.rows_generated >= 2
    frpm_rows = instance.get_rows(instance.table_id("frpm"))
    assert any(
        row["district name"].concrete == "Fresno County Office of Education"
        and row["charter school (y/n)"].concrete == 1
        for row in frpm_rows
    )


def test_nested_alias_solver_vars_keep_distinct_bindings_and_storage():
    schema = """
    CREATE TABLE atom (atom_id TEXT PRIMARY KEY, element TEXT);
    CREATE TABLE connected (
      atom_id TEXT NOT NULL,
      atom_id2 TEXT NOT NULL,
      PRIMARY KEY (atom_id, atom_id2),
      FOREIGN KEY (atom_id) REFERENCES atom(atom_id)
    );
    """
    sql = """
    SELECT DISTINCT T.element FROM atom AS T
    WHERE T.element NOT IN (
      SELECT DISTINCT T1.element FROM atom AS T1
      INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
    )
    """
    instance, _plan, _tree, _target, constraint = _compile_uncovered(
        sql, schema, site="scalar_subquery"
    )
    vars_by_qualifier = {}
    for expression in constraint.constraints:
        for column in expression.find_all(exp.Column):
            variable = solver_var(column)
            assert variable is not None
            table_key = identifier_name(column.table, dialect="sqlite").normalized
            vars_by_qualifier.setdefault(table_key, set()).add(variable)
    t1_key = _normalized_identifier("T1")
    t2_key = _normalized_identifier("T2")
    assert {
        identifier_name(var.relation_id.display, dialect="sqlite").normalized
        for var in vars_by_qualifier[t1_key]
    } == {t1_key}
    assert {
        identifier_name(var.relation_id.display, dialect="sqlite").normalized
        for var in vars_by_qualifier[t2_key]
    } == {t2_key}
    assert all(var.column_id.kind is not ColumnKind.SYNTHETIC for var in vars_by_qualifier[t1_key])
    assert all(var.column_id.kind is not ColumnKind.SYNTHETIC for var in vars_by_qualifier[t2_key])
    assert {constraint.storage_relations[var].name.normalized for var in vars_by_qualifier["t1"]} == {"atom"}
    assert {constraint.storage_relations[var].name.normalized for var in vars_by_qualifier["t2"]} == {"connected"}


def test_unresolved_qualified_constraint_column_fails_closed():
    instance = Instance(
        ddls="CREATE TABLE atom (atom_id TEXT PRIMARY KEY);",
        name="unresolved_constraint",
        dialect="sqlite",
    )
    plan = Plan(
        preprocess_sql("SELECT atom_id FROM atom", instance, dialect="sqlite"),
        instance,
    )
    column = exp.column("atom_id", table="missing_alias")
    predicate = exp.EQ(this=column, expression=exp.Literal.string("x"))
    with pytest.raises(ValueError, match="unresolved_scoped_column"):
        ConstraintGenerator(plan, instance, instance.dialect)._annotate_solver_vars(
            [predicate],
            (instance.table_id("atom"),),
        )


def test_planner_relation_resolution_requires_column_identity():
    instance = Instance(
        ddls="CREATE TABLE races (raceId INT PRIMARY KEY, year INT);",
        name="strict_relation_resolution",
        dialect="sqlite",
    )
    plan = Plan(
        preprocess_sql("SELECT raceId FROM races", instance, dialect="sqlite"),
        instance,
    )
    outer = relation_id(
        RelationKind.TABLE,
        identifier_name("races"),
        scope_id="outer",
    )
    inner = relation_id(
        RelationKind.TABLE,
        identifier_name("races"),
        scope_id="inner",
    )

    generator = ConstraintGenerator(plan, instance, instance.dialect)

    assert generator._planner_relation_for_column(
        exp.column("year"),
        (outer, inner),
    ) is None
    with pytest.raises(ValueError, match="unresolved_scoped_column"):
        generator._planner_relation_for_column(
            exp.column("year", table="races"),
            (outer, inner),
        )


def test_foreign_key_table_resolution_returns_physical_relation():
    instance = Instance(
        ddls="""
        CREATE TABLE parent (id INT PRIMARY KEY);
        CREATE TABLE child (
          id INT PRIMARY KEY,
          parent_id INT,
          FOREIGN KEY (parent_id) REFERENCES parent(id)
        );
        """,
        name="physical_fk_resolution",
        dialect="sqlite",
    )
    plan = Plan(
        preprocess_sql("SELECT id FROM child", instance, dialect="sqlite"),
        instance,
    )
    scoped_parent = relation_id(
        RelationKind.TABLE,
        identifier_name("parent"),
        alias=identifier_name("p"),
        scope_id="inner",
    )

    resolved = ConstraintGenerator(plan, instance, instance.dialect)._storage_relation_for_table_reference(
        exp.to_table("parent"),
    )

    assert resolved == instance.table_id("parent")
    assert resolved != scoped_parent
