from __future__ import annotations

from parseval.identity import ColumnKind
from parseval.instance import Instance
from parseval.plan import Aggregate, Having, Plan
from parseval.plan.context import build_context_from_instance
from parseval.query import preprocess_sql
from parseval.symbolic.evaluator import PlanEvaluator
from parseval.symbolic.types import BranchTree, BranchType


def _first_step_of_type(plan: Plan, step_type):
    for step in plan.ordered_steps:
        if isinstance(step, step_type):
            return step
    raise AssertionError(f"no {step_type.__name__} step in plan")


def _value(value):
    return getattr(value, "concrete", value)


def test_complex_group_key_runtime_uses_planner_identity_and_provenance():
    instance = Instance(
        ddls="CREATE TABLE sales (date TEXT, amount INT);",
        name="test",
        dialect="sqlite",
    )
    instance.create_row("sales", values={"date": "2024-01-01", "amount": 10})
    instance.create_row("sales", values={"date": "2024-02-01", "amount": 20})
    instance.create_row("sales", values={"date": "2025-01-01", "amount": 30})
    sql = (
        "SELECT SUBSTR(t1.date, 1, 4) AS yr, COUNT(*) AS n "
        "FROM sales AS t1 GROUP BY SUBSTR(t1.date, 1, 4)"
    )
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    aggregate = _first_step_of_type(plan, Aggregate)
    metadata = plan.annotation_for(aggregate).metadata["aggregation"]
    group_key = metadata["group_keys"][0]

    evaluator = PlanEvaluator(plan, instance, "sqlite")
    ctx = evaluator._walk(
        aggregate,
        build_context_from_instance(instance),
        BranchTree(),
        observe=False,
    )
    table = ctx.tables[aggregate.name]
    row_2024 = next(row for row in table.rows if _value(row[group_key]) == "2024")
    group = table.aggregate_groups[row_2024.rowid]

    assert group_key.kind is ColumnKind.DERIVED
    assert group_key in table.columns
    assert group_key in row_2024.columns
    assert group.group_key_values[group_key] == "2024"
    assert group.group_sources[group_key][0].name.normalized == "date"
    expression_sql = group.group_expressions[group_key].sql(dialect="sqlite")
    assert expression_sql.replace('"', "") in {
        "SUBSTR(t1.date, 1, 4)",
        "SUBSTRING(t1.date, 1, 4)",
    }
    assert len(group.source_row_ids) == 2


def test_branch_tree_records_group_lineage_from_evaluator():
    instance = Instance(
        ddls="CREATE TABLE sales (dept TEXT, amount INT);",
        name="test",
        dialect="sqlite",
    )
    instance.create_row("sales", values={"dept": "A", "amount": 10})
    instance.create_row("sales", values={"dept": "A", "amount": 20})
    instance.create_row("sales", values={"dept": "B", "amount": 30})
    sql = "SELECT dept, COUNT(*) AS n FROM sales GROUP BY dept"
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    tree = PlanEvaluator(plan, instance, "sqlite").evaluate(BranchTree())
    group_node = next(node for node in tree.nodes if node.site == "group")
    multi_traces = [
        trace
        for trace in tree.traces_for_node(group_node)
        if trace.outcome == BranchType.GROUP_MULTI
    ]

    assert multi_traces
    assert any(len(trace.input_row_ids) == 2 for trace in multi_traces)
    assert any(len(group.source_row_ids) == 2 for group in tree.group_lineage.values())


def test_having_without_select_alias_evaluates_without_synthetic_placeholder():
    instance = Instance(
        ddls="CREATE TABLE sales (dept TEXT, amount INT);",
        name="test",
        dialect="sqlite",
    )
    for amount in (10, 20, 30, 40):
        instance.create_row("sales", values={"dept": "A", "amount": amount})

    sql = "SELECT dept FROM sales GROUP BY dept HAVING COUNT(*) > 3"
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    having = next(step for step in plan.ordered_steps if isinstance(step, Having))
    assert "_h" not in having.condition.sql()

    evaluator = PlanEvaluator(plan, instance, "sqlite")
    ctx = evaluator.evaluate_context(BranchTree())
    table = ctx.tables[plan.root.name]

    assert len(table.rows) == 1
    assert all(col.name.normalized != "_h" for col in table.columns)
