from __future__ import annotations

from decimal import Decimal
import sqlite3

import pytest

from parseval.identity import ColumnKind, RelationKind, column_identity, identifier_name, relation_id
from parseval.instance import Instance
from parseval.plan import Filter, Plan
from parseval.query import preprocess_sql
from parseval.solver import Solver, SolverConstraint
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


def _instance_table_rows(instance: Instance, table_name: str):
    return [
        {column.name.normalized: symbol.concrete for column, symbol in row.items()}
        for row in instance.get_rows(table_name)
    ]


def _execute_generated(schema: str, instance: Instance, sql: str):
    def sqlite_value(value):
        if isinstance(value, Decimal):
            return float(value)
        return value

    connection = sqlite3.connect(":memory:")
    try:
        for ddl in schema.split(";"):
            ddl = ddl.strip()
            if ddl:
                connection.execute(ddl)
        for table_name in instance.tables:
            rows = _instance_table_rows(instance, table_name)
            if not rows:
                continue
            columns = list(rows[0])
            quoted = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _column in columns)
            statement = (
                f'INSERT INTO "{table_name}" ({quoted}) VALUES ({placeholders})'
            )
            for row in rows:
                connection.execute(
                    statement,
                    [sqlite_value(row[column]) for column in columns],
                )
        connection.commit()
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _has_customer_product_covering_group(customer_rows, product_rows):
    product_keys = {row["product_key"] for row in product_rows}
    if len(product_keys) < 2 or any(value is None for value in product_keys):
        return False

    keys_by_customer = {}
    for row in customer_rows:
        customer_id = row["customer_id"]
        product_key = row["product_key"]
        if customer_id is None or product_key is None:
            continue
        keys_by_customer.setdefault(customer_id, set()).add(product_key)
    return any(keys == product_keys for keys in keys_by_customer.values())


def _compile_root_witness(sql: str, schema: str):
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance, CoverageThresholds(atom_null=0))
    target = next(
        target
        for target in tree.root_witness_targets
        if target.node.site == "root_result"
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)
    return constraint


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


def test_case_arm_row_observations_satisfy_case_coverage_targets():
    schema = """
    CREATE TABLE t (
      id INT PRIMARY KEY,
      a INT
    );
    """
    sql = "SELECT CASE WHEN a > 5 THEN 'big' ELSE 'small' END FROM t"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    instance.create_row("t", values={"id": 1, "a": 10})
    instance.create_row("t", values={"id": 2, "a": 1})
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = BranchTreeBuilder(plan, instance).build()

    evaluated = PlanEvaluator(plan, instance).evaluate(tree)
    uncovered_case_outcomes = {
        target.target_outcome
        for target in evaluated.uncovered_targets
        if target.node.site == "case_arm"
    }

    assert BranchType.CASE_ARM_TAKEN not in uncovered_case_outcomes
    assert BranchType.CASE_ARM_SKIPPED not in uncovered_case_outcomes


def test_case_target_path_carries_case_arm_predicate():
    schema = """
    CREATE TABLE t (
      id INT PRIMARY KEY,
      a INT
    );
    """
    sql = "SELECT CASE WHEN a > 5 THEN 'big' ELSE 'small' END FROM t"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "case_arm"
        and target.target_outcome == BranchType.CASE_ARM_TAKEN
    )

    path = BranchPathBuilder().path_for_target(target)

    assert [(predicate.node.site, predicate.expression.sql(), predicate.outcome) for predicate in path.predicates] == [
        ("case_arm", '"t"."a" > 5', BranchType.CASE_ARM_TAKEN)
    ]


def test_join_fanout_target_path_includes_two_row_obligation():
    schema = """
    CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);
    CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
    """
    sql = "SELECT parent.name FROM parent JOIN child ON parent.id = child.parent_id"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "join_on"
        and target.obligation is not None
        and target.obligation.metric == "join_fanout"
    )

    path = BranchPathBuilder().path_for_target(target)
    row_set = next(
        obligation.row_set
        for obligation in path.obligations
        if obligation.kind == "row_set" and obligation.row_set is not None
    )

    assert target.target_outcome == BranchType.DUPLICATE
    assert row_set.required_rows == 2
    assert row_set.row_scopes == ("r0", "r1")
    assert [
        column_identity(expression).name.normalized
        for expression in row_set.duplicate_expressions
    ] == ["parent_id"]


def test_engine_generates_strategy_only_join_fanout_without_speculation():
    schema = """
    CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);
    CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
    """
    sql = "SELECT parent.name FROM parent JOIN child ON parent.id = child.parent_id"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=12,
        max_rows_per_table=12,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    uncovered_metrics = {
        target.obligation.metric
        for target in result.tree.uncovered_targets
        if target.obligation is not None
    }

    assert "join_fanout" not in uncovered_metrics


def test_strategy_generation_budget_counts_from_engine_start():
    schema = """
    CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);
    CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
    """
    sql = "SELECT parent.name FROM parent JOIN child ON parent.id = child.parent_id"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=12,
        max_rows_per_table=12,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert result.rows_generated > 0
    assert result.tree.fully_covered


def test_engine_without_speculation_generates_count_distinct_duplicate_witness():
    schema = "CREATE TABLE t (pk INT PRIMARY KEY, id INT);"
    gold_sql = "SELECT COUNT(DISTINCT id) FROM t"
    pred_sql = "SELECT COUNT(id) FROM t"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        gold_sql,
        dialect="sqlite",
        max_iterations=8,
        max_rows_per_table=8,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert _execute_generated(schema, instance, gold_sql) != _execute_generated(
        schema,
        instance,
        pred_sql,
    )


def test_engine_without_speculation_generates_project_duplicate_witness():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, code TEXT, name TEXT);"
    gold_sql = "SELECT code, name FROM t"
    pred_sql = "SELECT DISTINCT code, name FROM t"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        gold_sql,
        dialect="sqlite",
        max_iterations=8,
        max_rows_per_table=8,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert _execute_generated(schema, instance, gold_sql) != _execute_generated(
        schema,
        instance,
        pred_sql,
    )


def test_engine_without_speculation_generates_case_positive_witness():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, a INT);"
    sql = "SELECT SUM(CASE WHEN a > 5 THEN 1 ELSE 0 END) FROM t"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=8,
        max_rows_per_table=8,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert _execute_generated(schema, instance, sql)[0][0] > 0


def test_engine_without_speculation_generates_rank_tie_witness():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, value INT);"
    pred_sql = "SELECT id FROM t ORDER BY value DESC LIMIT 1"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        pred_sql,
        dialect="sqlite",
        max_iterations=8,
        max_rows_per_table=8,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    rows = instance.get_rows("t")
    value_col = instance.column_id("t", exp.to_identifier("value"))
    id_col = instance.column_id("t", exp.to_identifier("id"))
    top_value = max(row[value_col].concrete for row in rows)
    top_ids = {
        row[id_col].concrete
        for row in rows
        if row[value_col].concrete == top_value
    }

    assert _execute_generated(schema, instance, pred_sql)
    assert len(top_ids) >= 2


def test_aggregate_contrast_target_path_includes_grouped_row_scopes():
    schema = "CREATE TABLE sales (id INT PRIMARY KEY, category TEXT, amount INT);"
    sql = "SELECT category, SUM(amount) FROM sales GROUP BY category"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "aggregate_output"
        and target.obligation is not None
        and target.obligation.metric == "aggregate_contrast"
    )

    path = BranchPathBuilder().path_for_target(target)
    row_set = next(
        obligation.row_set
        for obligation in path.obligations
        if obligation.kind == "row_set" and obligation.row_set is not None
    )

    assert target.target_outcome == BranchType.DUPLICATE
    assert row_set.group_keys
    assert row_set.row_scopes == ("r0", "r1")
    assert row_set.distinct_expression is not None
    assert row_set.distinct_expression.sql() == '"sales"."amount"'


def test_rank_contrast_ordering_only_when_sort_feeds_limit():
    schema = "CREATE TABLE schools (id INT PRIMARY KEY, opened INT);"
    limited = Instance(ddls=schema, name="limited", dialect="sqlite")
    limited_plan = Plan(
        preprocess_sql(
            "SELECT id FROM schools ORDER BY opened DESC LIMIT 1",
            limited,
            dialect="sqlite",
        ),
        limited,
    )
    unlimited = Instance(ddls=schema, name="unlimited", dialect="sqlite")
    unlimited_plan = Plan(
        preprocess_sql(
            "SELECT id FROM schools ORDER BY opened DESC",
            unlimited,
            dialect="sqlite",
        ),
        unlimited,
    )

    limited_row_set = next(
        obligation.row_set
        for target in build_branch_tree(limited_plan, limited).root_witness_targets
        for obligation in target.node.obligations
        if obligation.kind == "row_set"
    )
    unlimited_row_set = next(
        obligation.row_set
        for target in build_branch_tree(unlimited_plan, unlimited).root_witness_targets
        for obligation in target.node.obligations
        if obligation.kind == "row_set"
    )

    assert limited_row_set.ordering
    assert unlimited_row_set.ordering == ()


def test_ranked_join_antimatch_uses_existing_join_unmatched_target():
    schema = """
    CREATE TABLE parent (id INT PRIMARY KEY, name TEXT, score INT);
    CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
    """
    sql = (
        "SELECT parent.name FROM parent "
        "JOIN child ON parent.id = child.parent_id "
        "ORDER BY parent.score DESC LIMIT 1"
    )
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "join_on"
        and target.obligation is not None
        and target.obligation.metric == "join_left_unmatched"
    )

    path = BranchPathBuilder().path_for_target(target)
    row_set = next(
        obligation.row_set
        for obligation in path.obligations
        if obligation.kind == "row_set"
        and obligation.site == "ranked_join_antimatch"
        and obligation.row_set is not None
    )

    assert target.target_outcome == BranchType.JOIN_LEFT
    assert target.obligation.metric == "join_left_unmatched"
    assert row_set.row_scopes == ("rank_top", "rank_match")
    assert row_set.ordering


def test_ranked_join_antimatch_uses_scoped_join_outcomes():
    schema = """
    CREATE TABLE parent (id INT PRIMARY KEY, name TEXT, score INT);
    CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
    """
    sql = (
        "SELECT parent.name FROM parent "
        "JOIN child ON parent.id = child.parent_id "
        "ORDER BY parent.score DESC LIMIT 1"
    )
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "join_on"
        and target.obligation is not None
        and target.obligation.metric == "join_left_unmatched"
    )

    path = BranchPathBuilder().path_for_target(target)
    row_set = next(
        obligation.row_set
        for obligation in path.obligations
        if obligation.kind == "row_set"
        and obligation.site == "ranked_join_antimatch"
        and obligation.row_set is not None
    )

    assert row_set.join_facts
    assert row_set.join_scope_outcomes == (
        ("rank_top", BranchType.JOIN_LEFT),
        ("rank_match", BranchType.JOIN_MATCH),
    )


def test_ranked_join_antimatch_reads_ordering_from_join_node_metadata():
    schema = """
    CREATE TABLE parent (id INT PRIMARY KEY, name TEXT, score INT);
    CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
    """
    sql = (
        "SELECT parent.name FROM parent "
        "JOIN child ON parent.id = child.parent_id "
        "ORDER BY parent.score DESC LIMIT 1"
    )
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    join_node = next(node for node in tree.nodes if node.site == "join_on")

    assert join_node.annotation_metadata.get("root_ordering")


def test_plain_join_antimatch_does_not_use_ranked_strategy_without_limit():
    schema = """
    CREATE TABLE parent (id INT PRIMARY KEY, name TEXT, score INT);
    CREATE TABLE child (id INT PRIMARY KEY, parent_id INT);
    """
    sql = (
        "SELECT parent.name FROM parent "
        "JOIN child ON parent.id = child.parent_id "
        "ORDER BY parent.score DESC"
    )
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "join_on"
        and target.obligation is not None
        and target.obligation.metric == "join_left_unmatched"
    )

    path = BranchPathBuilder().path_for_target(target)

    assert not any(
        obligation.kind == "row_set"
        and obligation.site == "ranked_join_antimatch"
        for obligation in path.obligations
    )


def test_group_count_multi_target_path_uses_group_metric_predicate():
    schema = "CREATE TABLE sales (id INT PRIMARY KEY, category TEXT, amount INT);"
    sql = "SELECT category, COUNT(id) FROM sales GROUP BY category"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "group"
        and target.obligation is not None
        and target.obligation.metric == "group_count"
        and target.target_outcome == BranchType.GROUP_MULTI
    )

    path = BranchPathBuilder().path_for_target(target)

    assert any(
        predicate.node.site == "group"
        and predicate.obligation is not None
        and predicate.obligation.metric == "group_count"
        and predicate.outcome == BranchType.GROUP_MULTI
        for predicate in path.predicates
    )


def test_group_count_multi_uses_group_metric_constraints_not_extra_row_set():
    schema = "CREATE TABLE sales (id INT PRIMARY KEY, category TEXT, amount INT);"
    sql = "SELECT category, COUNT(id) FROM sales GROUP BY category"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    tree = build_branch_tree(plan, instance)
    target = next(
        target
        for target in tree.uncovered_targets
        if target.node.site == "group"
        and target.obligation is not None
        and target.obligation.metric == "group_count"
        and target.target_outcome == BranchType.GROUP_MULTI
    )

    path = BranchPathBuilder().path_for_target(target)

    assert not any(
        obligation.kind == "row_set" and obligation.site == "group_count"
        for obligation in path.obligations
    )


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


def test_engine_does_not_treat_empty_materialization_as_generation_success():
    instance = Instance(
        ddls="CREATE TABLE t (id INT PRIMARY KEY);",
        name="test",
        dialect="sqlite",
    )
    engine = SymbolicEngine(instance, "SELECT id FROM t", dialect="sqlite")

    assert not engine._solve_and_materialize(
        SolverConstraint(target_relations=(instance.table_id("t"),))
    )


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
    assert len(next(iter(output.tables.values())).rows) >= 1


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
    assert len(next(iter(output.tables.values())).rows) >= 1


def test_engine_non_speculate_solves_table_level_check_before_materializing():
    schema = """
    CREATE TABLE follow (
      followee INT NOT NULL,
      follower INT NOT NULL,
      CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    sql = "SELECT * FROM follow WHERE followee > 0 AND follower > 0"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=5,
        max_rows_per_table=5,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    rows = [
        {column.name.normalized: symbol.concrete for column, symbol in row.items()}
        for row in instance.get_rows("follow")
    ]
    assert result.rows_generated > 0
    assert rows
    assert all(row["followee"] != row["follower"] for row in rows)
    assert len(next(iter(output.tables.values())).rows) > 0


def test_engine_non_speculate_applies_table_check_per_self_join_binding():
    schema = """
    CREATE TABLE FOLLOW (
      FOLLOWEE VARCHAR(30) NOT NULL,
      FOLLOWER VARCHAR(30) NOT NULL,
      CONSTRAINT PK_FOLLOW PRIMARY KEY (FOLLOWEE, FOLLOWER),
      CONSTRAINT CHECK_FOLLOW CHECK (FOLLOWEE <> FOLLOWER)
    );
    """
    sql = """
    SELECT DISTINCT T1.FOLLOWER, COUNT(DISTINCT T2.FOLLOWER) AS NUM
    FROM FOLLOW AS T1
    INNER JOIN FOLLOW AS T2 ON T1.FOLLOWER = T2.FOLLOWEE
    WHERE T2.FOLLOWER IS NOT NULL
    GROUP BY T1.FOLLOWER
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=20,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    rows = [
        {column.name.normalized: symbol.concrete for column, symbol in row.items()}
        for row in instance.get_rows("follow")
    ]
    conn = sqlite3.connect(":memory:")
    conn.execute(schema)
    for row in rows:
        conn.execute(
            "INSERT INTO FOLLOW(FOLLOWEE, FOLLOWER) VALUES (?, ?)",
            (row["followee"], row["follower"]),
        )
    output_rows = conn.execute(sql).fetchall()

    assert result.rows_generated > 0
    assert rows
    assert all(row["followee"] != row["follower"] for row in rows)
    assert output_rows


def test_engine_handles_comma_self_join_order_by_projection_alias():
    schema = """
    CREATE TABLE FOLLOW (
      FOLLOWEE VARCHAR(30) NOT NULL,
      FOLLOWER VARCHAR(30) NOT NULL,
      CONSTRAINT PK_FOLLOW PRIMARY KEY (FOLLOWEE, FOLLOWER),
      CONSTRAINT CHECK_FOLLOW CHECK (FOLLOWEE <> FOLLOWER)
    );
    """
    sql = """
    SELECT DISTINCT T1.FOLLOWER, COUNT(DISTINCT T2.FOLLOWER) AS NUM
    FROM FOLLOW T1, FOLLOW T2
    WHERE T1.FOLLOWER = T2.FOLLOWEE
    GROUP BY T1.FOLLOWER
    ORDER BY T1.FOLLOWER
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=20,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    rows = [
        {column.name.normalized: symbol.concrete for column, symbol in row.items()}
        for row in instance.get_rows("follow")
    ]
    conn = sqlite3.connect(":memory:")
    conn.execute(schema)
    for row in rows:
        conn.execute(
            "INSERT INTO FOLLOW(FOLLOWEE, FOLLOWER) VALUES (?, ?)",
            (row["followee"], row["follower"]),
        )
    output_rows = conn.execute(sql).fetchall()

    assert result.rows_generated > 0
    assert rows
    assert all(row["followee"] != row["follower"] for row in rows)
    assert output_rows


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


def test_relational_division_count_distinct_equals_domain_count_generates_covering_group():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT T1.customer_id
    FROM CUSTOMER AS T1
    GROUP BY T1.customer_id
    HAVING COUNT(DISTINCT T1.product_key) = (
      SELECT COUNT(DISTINCT T2.product_key)
      FROM PRODUCT AS T2
    )
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    customer_rows = _instance_table_rows(instance, "customer")
    product_rows = _instance_table_rows(instance, "product")
    output_rows = _execute_generated(schema, instance, sql)

    assert result.rows_generated >= 4
    assert output_rows
    assert _has_customer_product_covering_group(customer_rows, product_rows)


def test_relational_division_count_distinct_constraint_pairs_two_domain_keys():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT T1.customer_id
    FROM CUSTOMER AS T1
    GROUP BY T1.customer_id
    HAVING COUNT(DISTINCT T1.product_key) = (
      SELECT COUNT(DISTINCT T2.product_key)
      FROM PRODUCT AS T2
    )
    """
    constraint = _compile_root_witness(sql, schema)

    equal_pairs = set()
    distinct_pairs = set()
    not_null_scopes = set()
    for expression in constraint.constraints:
        if isinstance(expression, exp.Is):
            column = next(expression.find_all(exp.Column), None)
            var = solver_var(column) if column is not None else None
            if var is not None:
                not_null_scopes.add((var.relation_id.name.normalized, var.row_scope))
        if isinstance(expression, exp.EQ):
            vars_ = [solver_var(column) for column in expression.find_all(exp.Column)]
            scopes = {var.row_scope for var in vars_ if var is not None}
            if {"division_outer0", "division_domain0"} <= scopes:
                equal_pairs.add(("outer0", "domain0"))
            if {"division_outer1", "division_domain1"} <= scopes:
                equal_pairs.add(("outer1", "domain1"))
            if {"division_outer0", "division_outer1"} <= scopes:
                equal_pairs.add(("same_customer", "group"))
        if isinstance(expression, exp.NEQ):
            vars_ = [solver_var(column) for column in expression.find_all(exp.Column)]
            scopes = frozenset(var.row_scope for var in vars_ if var is not None)
            distinct_pairs.add(scopes)

    assert {
        ("outer0", "domain0"),
        ("outer1", "domain1"),
        ("same_customer", "group"),
    } <= equal_pairs
    assert frozenset({"division_domain0", "division_domain1"}) in distinct_pairs
    assert frozenset({"division_outer0", "division_outer1"}) in distinct_pairs
    assert ("product", "division_domain0") in not_null_scopes
    assert ("product", "division_domain1") in not_null_scopes


def test_relational_division_full_threshold_generation_keeps_survivor_group():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT CUSTOMER_ID
    FROM CUSTOMER
    GROUP BY 1
    HAVING COUNT(DISTINCT PRODUCT_KEY) = (
      SELECT COUNT(DISTINCT PRODUCT_KEY)
      FROM PRODUCT
    )
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=25,
    ).generate(
        thresholds=CoverageThresholds(
            atom_null=1,
            atom_false=1,
            atom_dup=1,
            project_null=1,
            distinct_duplicate=1,
            distinct_unique=1,
        ),
    )

    output_rows = _execute_generated(schema, instance, sql)

    assert result.rows_generated >= 4
    assert output_rows


def test_relational_division_count_distinct_equals_domain_count_star_generates_covering_group():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT T1.customer_id
    FROM CUSTOMER AS T1
    GROUP BY T1.customer_id
    HAVING COUNT(DISTINCT T1.product_key) = (
      SELECT COUNT(*)
      FROM PRODUCT AS T2
    )
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    customer_rows = _instance_table_rows(instance, "customer")
    product_rows = _instance_table_rows(instance, "product")
    output_rows = _execute_generated(schema, instance, sql)

    assert result.rows_generated >= 4
    assert output_rows
    assert _has_customer_product_covering_group(customer_rows, product_rows)


def test_relational_division_count_distinct_equals_domain_count_column_generates_covering_group():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT CUSTOMER_ID
    FROM CUSTOMER
    GROUP BY CUSTOMER_ID
    HAVING COUNT(DISTINCT PRODUCT_KEY) = (
      SELECT COUNT(PRODUCT_KEY)
      FROM PRODUCT
    )
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    customer_rows = _instance_table_rows(instance, "customer")
    product_rows = _instance_table_rows(instance, "product")
    output_rows = _execute_generated(schema, instance, sql)

    assert result.rows_generated >= 4
    assert output_rows
    assert _has_customer_product_covering_group(customer_rows, product_rows)


def test_relational_division_sum_equals_domain_sum_generates_covering_group():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT T1.customer_id
    FROM CUSTOMER AS T1
    GROUP BY T1.customer_id
    HAVING SUM(DISTINCT T1.product_key) = (
      SELECT SUM(DISTINCT T2.product_key)
      FROM PRODUCT AS T2
    )
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    customer_rows = _instance_table_rows(instance, "customer")
    product_rows = _instance_table_rows(instance, "product")
    output_rows = _execute_generated(schema, instance, sql)

    assert result.rows_generated >= 4
    assert output_rows
    assert _has_customer_product_covering_group(customer_rows, product_rows)


def test_derived_relational_division_sum_generates_non_empty_result():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT CUSTOMER_ID
    FROM (
      SELECT CUSTOMER_ID, SUM(PRODUCT_KEY) AS TOT
      FROM (
        SELECT DISTINCT CUSTOMER_ID, PRODUCT_KEY
        FROM CUSTOMER
        ORDER BY PRODUCT_KEY
      ) A
      GROUP BY CUSTOMER_ID
      HAVING SUM(PRODUCT_KEY) = (SELECT SUM(PRODUCT_KEY) FROM PRODUCT)
    ) A
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    output_rows = _execute_generated(schema, instance, sql)

    assert result.rows_generated >= 4
    assert output_rows


def test_derived_relational_division_count_alias_generates_non_empty_result():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT SUB.CUSTOMER_ID
    FROM (
      SELECT CUSTOMER_ID, COUNT(DISTINCT PRODUCT_KEY) AS NUM_BOUGHT_PRODUCT
      FROM CUSTOMER
      GROUP BY CUSTOMER_ID
    ) SUB
    WHERE SUB.NUM_BOUGHT_PRODUCT = (SELECT COUNT(PRODUCT_KEY) FROM PRODUCT)
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    customer_rows = _instance_table_rows(instance, "customer")
    product_rows = _instance_table_rows(instance, "product")
    output_rows = _execute_generated(schema, instance, sql)

    assert result.rows_generated >= 4
    assert output_rows
    assert _has_customer_product_covering_group(customer_rows, product_rows)


def test_derived_join_relational_division_count_alias_generates_non_empty_result():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT C.CUSTOMER_ID
    FROM (
      SELECT A.CUSTOMER_ID, A.PRODUCT_KEY, COUNT(DISTINCT A.PRODUCT_KEY) AS CNT
      FROM CUSTOMER A
      INNER JOIN PRODUCT B ON A.PRODUCT_KEY = B.PRODUCT_KEY
      GROUP BY A.CUSTOMER_ID
    ) C
    WHERE C.CNT = (SELECT COUNT(D.PRODUCT_KEY) AS CNT FROM PRODUCT D)
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    customer_rows = _instance_table_rows(instance, "customer")
    product_rows = _instance_table_rows(instance, "product")
    output_rows = _execute_generated(schema, instance, sql)

    assert result.rows_generated >= 4
    assert output_rows
    assert _has_customer_product_covering_group(customer_rows, product_rows)


def test_derived_relational_division_count_alias_constraint_pairs_two_domain_keys():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT SUB.CUSTOMER_ID
    FROM (
      SELECT CUSTOMER_ID, COUNT(DISTINCT PRODUCT_KEY) AS NUM_BOUGHT_PRODUCT
      FROM CUSTOMER
      GROUP BY CUSTOMER_ID
    ) SUB
    WHERE SUB.NUM_BOUGHT_PRODUCT = (SELECT COUNT(PRODUCT_KEY) FROM PRODUCT)
    """
    constraint = _compile_root_witness(sql, schema)

    equal_pairs = set()
    for expression in constraint.constraints:
        if not isinstance(expression, exp.EQ):
            continue
        vars_ = [solver_var(column) for column in expression.find_all(exp.Column)]
        scopes = {var.row_scope for var in vars_ if var is not None}
        if {"division_outer0", "division_domain0"} <= scopes:
            equal_pairs.add(("outer0", "domain0"))
        if {"division_outer1", "division_domain1"} <= scopes:
            equal_pairs.add(("outer1", "domain1"))
        if {"division_outer0", "division_outer1"} <= scopes:
            equal_pairs.add(("same_customer", "group"))

    assert {
        ("outer0", "domain0"),
        ("outer1", "domain1"),
        ("same_customer", "group"),
    } <= equal_pairs


def test_relational_division_count_star_constraint_pairs_two_domain_keys():
    schema = """
    CREATE TABLE CUSTOMER (
      customer_id INT,
      product_key INT
    );
    CREATE TABLE PRODUCT (
      product_key INT
    );
    """
    sql = """
    SELECT T1.customer_id
    FROM CUSTOMER AS T1
    GROUP BY T1.customer_id
    HAVING COUNT(DISTINCT T1.product_key) = (
      SELECT COUNT(*)
      FROM PRODUCT AS T2
    )
    """
    constraint = _compile_root_witness(sql, schema)
    domain_scopes = set()
    for expression in constraint.constraints:
        for column in expression.find_all(exp.Column):
            var = solver_var(column)
            if var is None or var.relation_id.name.normalized != "product":
                continue
            domain_scopes.add(var.row_scope)

    assert {"division_domain0", "division_domain1"} <= domain_scopes
    assert "out0" not in domain_scopes


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


def test_engine_only_filter_on_derived_count_alias_generates_group_rows():
    schema = """
    CREATE TABLE badges (
      Id INT PRIMARY KEY,
      UserId INT,
      Name TEXT
    );
    """
    sql = """
    SELECT UserId
    FROM (
      SELECT UserId, COUNT(Name) AS num
      FROM badges
      GROUP BY UserId
    ) T
    WHERE T.num > 2
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 3
    assert len(instance.get_rows("badges")) >= 3
    assert len(next(iter(output.tables.values())).rows) == 1


def test_qualified_derived_count_alias_uses_matching_subquery_output():
    schema = """
    CREATE TABLE badges (
      Id INT PRIMARY KEY,
      UserId INT,
      Name TEXT
    );
    CREATE TABLE posts (
      Id INT PRIMARY KEY,
      UserId INT,
      Title TEXT
    );
    """
    sql = """
    SELECT B.UserId
    FROM (
      SELECT UserId, COUNT(Name) AS num
      FROM badges
      GROUP BY UserId
    ) B
    JOIN (
      SELECT UserId, COUNT(Title) AS num
      FROM posts
      GROUP BY UserId
    ) P ON B.UserId = P.UserId
    WHERE P.num > 2
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=15,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert result.rows_generated >= 3
    assert len(instance.get_rows("posts")) >= 3
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()
    assert len(next(iter(output.tables.values())).rows) >= 1


def test_engine_only_date_function_predicate_generates_physical_row():
    schema = """
    CREATE TABLE users (
      Id INT PRIMARY KEY,
      LastAccessDate TEXT
    );
    """
    sql = "SELECT COUNT(Id) FROM users WHERE date(LastAccessDate) > '2014-09-01'"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 1
    assert len(instance.get_rows("users")) >= 1
    assert next(iter(output.tables.values())).rows


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


def test_topk_derived_subquery_engine_generates_source_rows():
    schema = """
    CREATE TABLE drivers (
      driverId INT PRIMARY KEY,
      nationality TEXT,
      dob DATE
    );
    """
    sql = """
    SELECT COUNT(*)
    FROM (
      SELECT nationality
      FROM drivers
      ORDER BY JULIANDAY(dob) DESC
      LIMIT 3
    ) AS T3
    WHERE T3.nationality = 'Dutch'
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=20,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert result.rows_generated >= 3
    assert len(instance.get_rows("drivers")) >= 3


def test_self_join_aliases_keep_distinct_join_edges():
    schema = """
    CREATE TABLE superhero (
      id INT PRIMARY KEY,
      superhero_name TEXT,
      eye_colour_id INT,
      hair_colour_id INT
    );
    CREATE TABLE colour (
      id INT PRIMARY KEY,
      colour TEXT
    );
    """
    sql = """
    SELECT T1.superhero_name
    FROM superhero AS T1
    INNER JOIN colour AS T2 ON T1.eye_colour_id = T2.id
    INNER JOIN colour AS T3 ON T1.hair_colour_id = T3.id
    WHERE T2.colour = 'Blue' AND T3.colour = 'Brown'
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance)
    join_step = next(step for step in plan.ordered_steps if type(step).__name__ == "Join")
    target = next(
        target
        for target in build_branch_tree(plan, instance).root_witness_targets
        if target.node.site == "root_result"
    )
    constraint = ConstraintGenerator(plan, instance, instance.dialect).compile_target(target)

    join_pairs = {
        (
            join_data["source_key"][0].sql(dialect="sqlite"),
            join_data["join_key"][0].sql(dialect="sqlite"),
        )
        for join_data in join_step.joins.values()
    }

    assert join_pairs == {
        ('"t1"."eye_colour_id"', '"t2"."id"'),
        ('"t1"."hair_colour_id"', '"t3"."id"'),
    }
    lowered_pairs = {
        (
            left.column_id.name.normalized,
            left.relation_id.display,
            right.column_id.name.normalized,
            right.relation_id.display,
        )
        for left, right in constraint.join_equalities
    }
    assert lowered_pairs == {
        ("eye_colour_id", "t1", "id", "t2"),
        ("hair_colour_id", "t1", "id", "t3"),
    }


def test_cte_derived_join_engine_materializes_source_rows():
    schema = """
    CREATE TABLE lapTimes (
      raceId INT,
      driverId INT,
      lap INT,
      time_in_seconds REAL
    );
    CREATE TABLE drivers (
      driverId INT PRIMARY KEY,
      forename TEXT,
      surname TEXT
    );
    """
    sql = """
    WITH lap_times_in_seconds AS (
      SELECT driverId, time_in_seconds
      FROM lapTimes
    )
    SELECT T2.forename, T2.surname
    FROM (
      SELECT driverId, MIN(time_in_seconds) AS min_time_in_seconds
      FROM lap_times_in_seconds
      GROUP BY driverId
    ) AS T1
    INNER JOIN drivers AS T2 ON T1.driverId = T2.driverId
    ORDER BY T1.min_time_in_seconds ASC
    LIMIT 1
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

    assert result.rows_generated > 0
    assert len(instance.get_rows("lapTimes")) >= 1
    assert len(instance.get_rows("drivers")) >= 1
    assert len(next(iter(output.tables.values())).rows) == 1


def test_aggregate_ordered_derived_table_engine_materializes_source_rows():
    schema = """
    CREATE TABLE atom (
      atom_id INT PRIMARY KEY,
      molecule_id INT,
      element TEXT
    );
    CREATE TABLE molecule (
      molecule_id INT PRIMARY KEY,
      label TEXT
    );
    """
    sql = """
    SELECT T.element
    FROM (
      SELECT T1.element, COUNT(DISTINCT T1.molecule_id)
      FROM atom AS T1
      INNER JOIN molecule AS T2 ON T1.molecule_id = T2.molecule_id
      WHERE T2.label = '-'
      GROUP BY T1.element
      ORDER BY COUNT(DISTINCT T1.molecule_id) ASC
      LIMIT 4
    ) T
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")
    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=20,
        max_rows_per_table=20,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    output = PlanEvaluator(
        Plan(preprocess_sql(sql, instance, dialect="sqlite"), instance),
        instance,
    ).evaluate_context()

    assert result.rows_generated >= 8
    assert len(instance.get_rows("atom")) >= 4
    assert len(instance.get_rows("molecule")) >= 4
    assert len(next(iter(output.tables.values())).rows) >= 1


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


def test_scalar_subquery_order_by_limit_generation_returns_outer_match():
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
    instance.create_row("frpm", {"CDSCode": "top", "FRPM Count (K-12)": 100})
    instance.create_row("frpm", {"CDSCode": "lower", "FRPM Count (K-12)": 1})

    SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=8,
        max_rows_per_table=8,
    ).generate(thresholds=CoverageThresholds(atom_null=0))

    assert _execute_generated(SUBQUERY_SCHEMA, instance, sql)
    frpm_rows = instance.get_rows("frpm")
    top_row = max(
        frpm_rows,
        key=lambda row: row[
            instance.column_id("frpm", exp.to_identifier("FRPM Count (K-12)"))
        ].concrete,
    )
    top_cdscode = top_row[
        instance.column_id("frpm", exp.to_identifier("CDSCode"))
    ].concrete
    assert any(
        row["cds"].concrete == top_cdscode
        for row in instance.get_rows("satscores")
    )


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
    for qualifier in (t1_key, t2_key):
        assert all(var.column_id.kind is ColumnKind.SYNTHETIC for var in vars_by_qualifier[qualifier])
        assert all(var.column_id.source_column_id is not None for var in vars_by_qualifier[qualifier])
        assert all(
            var.column_id.source_column_id.kind is ColumnKind.PHYSICAL
            for var in vars_by_qualifier[qualifier]
        )
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
