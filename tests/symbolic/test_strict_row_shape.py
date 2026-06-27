from __future__ import annotations

from parseval.identity import ColumnKind, column_id, identifier_name, relation_id, RelationKind
from parseval.instance import Instance
from parseval.plan import Aggregate, Join, Plan, Project, Scan
from parseval.plan.context import Row, build_context_from_instance
from parseval.plan.rex import Variable
from parseval.query import preprocess_sql
from parseval.symbolic.evaluator import PlanEvaluator, _materialize_column_from_row
from parseval.symbolic.types import BranchTree


def _first_step_of_type(plan: Plan, step_type):
    for step in plan.ordered_steps:
        if isinstance(step, step_type):
            return step
    raise AssertionError(f"no {step_type.__name__} step in plan")


def _eval_step(plan: Plan, instance: Instance, step):
    return PlanEvaluator(plan, instance, "sqlite")._walk(
        step,
        build_context_from_instance(instance),
        BranchTree(),
        observe=False,
    )


def _assert_strict_schema(table):
    for row in table.rows:
        assert tuple(row.columns) == tuple(table.columns)
        for column in table.columns:
            assert column in row


def test_scan_outputs_query_column_ids_not_stored_instance_ids():
    instance = Instance(
        ddls="CREATE TABLE sales (dept TEXT, amount INT);",
        name="test",
        dialect="sqlite",
    )
    instance.create_row("sales", values={"dept": "ops", "amount": 10})
    expr = preprocess_sql(
        "SELECT t.dept FROM sales AS t",
        instance,
        dialect="sqlite",
    )
    plan = Plan(expr, instance)
    scan = _first_step_of_type(plan, Scan)
    plan.annotation_for(scan)

    ctx = _eval_step(plan, instance, scan)
    table = ctx.tables[scan.relation_id]

    assert tuple(table.columns) == tuple(scan.output_column_ids)
    _assert_strict_schema(table)
    assert table.columns[0].relation.alias.normalized == "t"


def test_aggregate_outputs_only_planned_columns():
    instance = Instance(
        ddls="CREATE TABLE sales (dept TEXT, amount INT);",
        name="test",
        dialect="sqlite",
    )
    instance.create_row("sales", values={"dept": "ops", "amount": 10})
    instance.create_row("sales", values={"dept": "eng", "amount": 20})
    expr = preprocess_sql(
        "SELECT dept, COUNT(*) AS n FROM sales GROUP BY dept",
        instance,
        dialect="sqlite",
    )
    plan = Plan(expr, instance)
    aggregate = _first_step_of_type(plan, Aggregate)
    plan.annotation_for(aggregate)

    ctx = _eval_step(plan, instance, aggregate)
    table = ctx.tables[aggregate.name]

    assert tuple(table.columns) == tuple(aggregate.output_column_ids)
    _assert_strict_schema(table)


def test_project_outputs_only_select_list_columns_after_aggregate():
    instance = Instance(
        ddls="CREATE TABLE sales (dept TEXT, amount INT);",
        name="test",
        dialect="sqlite",
    )
    instance.create_row("sales", values={"dept": "ops", "amount": 10})
    instance.create_row("sales", values={"dept": "eng", "amount": 20})
    expr = preprocess_sql(
        "SELECT dept AS d, COUNT(*) AS n FROM sales GROUP BY dept",
        instance,
        dialect="sqlite",
    )
    plan = Plan(expr, instance)
    project = _first_step_of_type(plan, Project)
    plan.annotation_for(project)

    ctx = _eval_step(plan, instance, project)
    table = ctx.tables[project.name]

    assert tuple(table.columns) == tuple(project.output_column_ids)
    _assert_strict_schema(table)
    assert [column.name.normalized for column in table.columns] == ["d", "n"]


def test_join_resolves_relation_id_join_keys_to_scan_context_tables():
    instance = Instance(
        ddls=(
            "CREATE TABLE satscores (cds TEXT, sname TEXT, AvgScrMath INT);"
            "CREATE TABLE frpm (CDSCode TEXT, `District Name` TEXT, `Charter Funding Type` TEXT);"
        ),
        name="test",
        dialect="sqlite",
    )
    instance.create_row(
        "satscores",
        values={"cds": "1", "sname": "A", "AvgScrMath": 500},
    )
    instance.create_row(
        "frpm",
        values={
            "CDSCode": "1",
            "District Name": "Riverside Unified",
            "Charter Funding Type": "Direct",
        },
    )
    expr = preprocess_sql(
        (
            "SELECT T1.sname, T2.`Charter Funding Type` "
            "FROM satscores AS T1 "
            "INNER JOIN frpm AS T2 ON T1.cds = T2.CDSCode "
            "WHERE T2.`District Name` LIKE 'Riverside%' "
            "GROUP BY T1.sname, T2.`Charter Funding Type` "
            "HAVING CAST(SUM(T1.AvgScrMath) AS REAL) / COUNT(T1.cds) > 400"
        ),
        instance,
        dialect="sqlite",
    )
    plan = Plan(expr, instance)
    join = _first_step_of_type(plan, Join)
    plan.annotation_for(join)

    ctx = _eval_step(plan, instance, join)
    table = next(iter(ctx.tables.values()))

    assert len(ctx.tables) == 1
    assert len(table.rows) == 1
    assert {column.relation.alias.normalized for column in table.columns} == {"t1", "t2"}
    _assert_strict_schema(table)


def test_join_relation_keys_are_scan_relation_ids_after_annotation():
    instance = Instance(
        ddls=(
            "CREATE TABLE satscores (cds TEXT, sname TEXT);"
            "CREATE TABLE frpm (CDSCode TEXT, `District Name` TEXT);"
        ),
        name="test",
        dialect="sqlite",
    )
    expr = preprocess_sql(
        (
            "SELECT T1.sname "
            "FROM satscores AS T1 "
            "INNER JOIN frpm AS T2 ON T1.cds = T2.CDSCode"
        ),
        instance,
        dialect="sqlite",
    )
    plan = Plan(expr, instance)
    join = _first_step_of_type(plan, Join)
    plan.annotation_for(join)

    scan_relations = {
        dep.relation_id
        for dep in join.chain_dependencies
        if isinstance(dep, Scan)
    }

    assert join.source_relation in scan_relations
    assert set(join.joins) <= scan_relations


def test_multi_join_keeps_later_scan_tables_available():
    instance = Instance(
        ddls=(
            "CREATE TABLE loan (loan_id INT, account_id INT, date TEXT);"
            "CREATE TABLE account (account_id INT, district_id INT);"
            "CREATE TABLE trans (trans_id INT, account_id INT, date TEXT, balance INT);"
        ),
        name="test",
        dialect="sqlite",
    )
    instance.create_row(
        "loan",
        values={"loan_id": 1, "account_id": 7, "date": "1993-07-05"},
    )
    instance.create_row(
        "account",
        values={"account_id": 7, "district_id": 3},
    )
    instance.create_row(
        "trans",
        values={
            "trans_id": 10,
            "account_id": 7,
            "date": "1998-12-27",
            "balance": 100,
        },
    )
    expr = preprocess_sql(
        (
            "SELECT SUM(T3.balance) "
            "FROM loan AS T1 "
            "INNER JOIN account AS T2 ON T1.account_id = T2.account_id "
            "INNER JOIN trans AS T3 ON T3.account_id = T2.account_id "
            "WHERE T1.date = '1993-07-05'"
        ),
        instance,
        dialect="sqlite",
    )
    plan = Plan(expr, instance)
    aggregate = _first_step_of_type(plan, Aggregate)
    plan.annotation_for(aggregate)

    ctx = _eval_step(plan, instance, aggregate)
    table = ctx.tables[aggregate.name]

    assert len(table.rows) == 1
    assert any(
        column.name.normalized == "trans_id"
        and column.relation.alias.normalized == "t3"
        for column in table.columns
    )
    _assert_strict_schema(table)


def test_materializer_resolves_lineage_equivalent_mapped_column_names():
    relation = relation_id(
        RelationKind.TABLE,
        identifier_name("frpm", dialect="sqlite"),
        alias=identifier_name("t1", dialect="sqlite"),
        scope_id="scan",
    )
    stored_relation = relation_id(
        RelationKind.TABLE,
        identifier_name("frpm", dialect="sqlite"),
    )
    stored_source = column_id(
        ColumnKind.PHYSICAL,
        identifier_name("AcademicYear", dialect="sqlite"),
        stored_relation,
    )
    scan_column = column_id(
        ColumnKind.PHYSICAL,
        identifier_name("AcademicYear", dialect="sqlite"),
        relation,
        scope_id="scan",
        ordinal=1,
        source_column_id=stored_source,
    )
    aggregate_passthrough = column_id(
        ColumnKind.PROJECTED,
        identifier_name("Academic Year"),
        relation,
        scope_id="aggregate",
        ordinal=51,
        source_column_id=column_id(
            ColumnKind.PHYSICAL,
            identifier_name("Academic Year"),
            relation,
            scope_id="scan",
            ordinal=1,
            source_column_id=column_id(
                ColumnKind.PHYSICAL,
                identifier_name("AcademicYear", dialect="sqlite"),
                stored_relation,
            ),
        ),
    )
    row = Row(
        this=("frpm", 0),
        columns={
            scan_column: Variable(
                this="frpm_0_academic_year",
                column_id=scan_column,
                rowid=("frpm", 0),
                concrete="2020",
                is_bound=True,
                is_null=False,
            )
        },
    )

    materialized = _materialize_column_from_row(
        aggregate_passthrough,
        row,
        ("agg", "frpm", 0),
    )

    assert materialized.column_id == aggregate_passthrough
    assert materialized.concrete == "2020"
