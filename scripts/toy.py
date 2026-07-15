#!/usr/bin/env python3
"""Minimal scalar-subquery example for the DataFusion explain planner.

Scalar subqueries are ``exp.Subquery`` nodes whose ``this`` is a lowered inner
Step plan (not a sqlglot SELECT AST or SQL text).

Run::

    uv run python scripts/toy.py
"""

from __future__ import annotations

from sqlglot import exp

from parseval.plan.explain import Filter, Projection, Step, explain, repr_expr
from datafusion import substrait

DDL = """
CREATE TABLE t(a INT);
CREATE TABLE u(b INT);
"""

QUERY = """
SELECT (SELECT MAX(b) FROM u) AS hi, a
FROM t
WHERE a = (SELECT MIN(b) FROM u)
"""

student_club = [
    "CREATE TABLE event\n(\n    event_id   TEXT\n        constraint event_pk\n            primary key,\n    event_name TEXT,\n    event_date TEXT,\n    type       TEXT,\n    notes      TEXT,\n    location   TEXT,\n    status     TEXT\n)",
    "CREATE TABLE major\n(\n    major_id   TEXT\n        constraint major_pk\n            primary key,\n    major_name TEXT,\n    department TEXT,\n    college    TEXT\n)",
    "CREATE TABLE zip_code\n(\n    zip_code    INTEGER\n        constraint zip_code_pk\n            primary key,\n    type        TEXT,\n    city        TEXT,\n    county      TEXT,\n    state       TEXT,\n    short_state TEXT\n)",
    "CREATE TABLE \"attendance\"\n(\n    link_to_event  TEXT,\n    link_to_member TEXT,\n    primary key (link_to_event, link_to_member)\n)",
    "CREATE TABLE \"budget\"\n(\n    budget_id     TEXT\n            primary key,\n    category      TEXT,\n    spent         REAL,\n    remaining     REAL,\n    amount        INTEGER,\n    event_status  TEXT,\n    link_to_event TEXT\n)",
    "CREATE TABLE \"expense\"\n(\n    expense_id          TEXT\n            primary key,\n    expense_description TEXT,\n    expense_date        TEXT,\n    cost                REAL,\n    approved            TEXT,\n    link_to_member      TEXT,\n    link_to_budget      TEXT\n)",
    "CREATE TABLE \"income\"\n(\n    income_id      TEXT\n        constraint income_pk\n            primary key,\n    date_received  TEXT,\n    amount         INTEGER,\n    source         TEXT,\n    notes          TEXT,\n    link_to_member TEXT\n)",
    "CREATE TABLE \"member\"\n(\n    member_id     TEXT\n        constraint member_pk\n            primary key,\n    first_name    TEXT,\n    last_name     TEXT,\n    email         TEXT,\n    position      TEXT,\n    t_shirt_size  TEXT,\n    phone         TEXT,\n    zip           INTEGER,\n    link_to_major TEXT\n)"
  ]
def plan_to_dict(node) -> dict:
    # .to_variant() unwraps the generic LogicalPlan into its specific type 
    # (e.g., Projection, Filter, TableScan)
    variant = node.to_variant()
    node_type = type(variant).__name__
    
    # Initialize the base dictionary for this node
    result = {
        "node_type": node_type
    }
    
    # 1. Extract specific information based on the node type
    if node_type == "Projection":
        # Get the list of columns/expressions being selected
        result["expressions"] = [str(expr) for expr in variant.projections()]
        
    elif node_type == "Filter":
        # Get the WHERE condition
        result["predicate"] = str(variant.predicate())
        
    elif node_type == "TableScan":
        # Get the underlying table name
        result["table_name"] = variant.table_name()
        
    elif node_type == "SubqueryAlias":
        # Get the 'AS T1' or 'AS T2' alias
        result["alias"] = variant.alias()
        
    # Add more 'elif' blocks here if you need to handle Joins, Aggregates, etc.

    # 2. Recursively process child nodes (relational inputs)
    children = node.inputs()
    if children:
        result["children"] = [plan_to_dict(child) for child in children]
        
    return result

def plan():
    import datafusion
    ctx = datafusion.SessionContext()
    # ctx.from_pydict({"id": [1, 2], "val": [10, 20]}, name="t1")
    
    # df = ctx.sql("SELECT * FROM t1 WHERE val > 15")
    # DDLs =[ """CREATE TABLE t(a INT);""", """CREATE TABLE u(b INT);"""]
    for ddl in student_club:
        ctx.sql(ddl)
    # df = ctx.sql("SELECT * FROM t, u WHERE CAST(a AS CHAR) LIKE CAST(b AS CHAR)")
    df = ctx.sql("SELECT T2.member_id FROM expense AS T1 INNER JOIN member AS T2 ON T1.link_to_member = T2.member_id INNER JOIN budget AS T3 ON T1.link_to_budget = T3.budget_id INNER JOIN event AS T4 ON T3.link_to_event = T4.event_id GROUP BY T2.member_id HAVING COUNT(DISTINCT T4.event_id) > 1 ORDER BY SUM(T1.cost) DESC LIMIT 1")

    df = ctx.sql("SELECT T2.member_id FROM member AS T2 where T2.member_id in (select T1.link_to_member from expense AS T1 )")

    # plan = df.logical_plan()

    plan = df.explain(verbose=True)

    # # print(plan)
    # python_tree = plan_to_dict(plan)

    # # Print it beautifully as JSON
    # import json
    # print(json.dumps(python_tree, indent=2))

    # # You can print the formatted tree directly:
    # print("--- Text Tree ---")
    # print(plan.display_indent())

    # # 2. Serialize to Substrait to get a traversable tree structure in Python
    # # (Requires the `substrait` module in datafusion-python)
    # substrait_bytes = substrait.Producer().to_substrait_plan(plan, ctx)

    # print("=="*10)
    # print(substrait_bytes.to_json())

    # from substrait.gen.proto.plan_pb2 import Plan

    # from google.protobuf.json_format import MessageToDict

    # # 2. Parse the bytes into a Python Protobuf Object
    # plan_proto = Plan()
    # plan_proto.ParseFromString(substrait_bytes)

    # # At this point, 'plan_proto' is a Python object. 
    # # You can traverse it using dot notation, for example:
    # # print(plan_proto.relations[0].root.input.project)

    # # 3. Convert to a standard Python Dictionary (Highly Recommended)
    # # This makes it much easier to print, inspect, and traverse dynamically
    # plan_dict = MessageToDict(plan_proto)

    # # Print the resulting dictionary beautifully
    # import json
    # print(json.dumps(plan_dict, indent=2))

    # # Prints both logical and physical plans to the console
    # # df.explain()


def main() -> None:
    plan = explain(DDL, QUERY, dialect="sqlite")

    print("=== SQL ===")
    print(QUERY.strip())
    print()

    print("=== Operator DAG (outer Step tree) ===")
    print(repr(plan))
    print()

    filt = next(step for step in plan.dag if isinstance(step, Filter))
    proj = next(step for step in plan.dag if isinstance(step, Projection))

    print("=== Scalar subqueries: exp.Subquery(this=Step) ===")
    where_subq = filt.condition.find(exp.Subquery)
    assert where_subq is not None and isinstance(where_subq.this, Step)
    print("WHERE subquery inner plan:")
    print(repr(where_subq.this))
    print()

    for i, expr in enumerate(proj.projections):
        subqs = list(expr.find_all(exp.Subquery))
        print(f"Projection[{i}]:")
        if subqs:
            for j, subq in enumerate(subqs):
                assert isinstance(subq.this, Step)
                print(f"  Subquery[{j}] inner plan:")
                print(repr(subq.this))
        else:
            print(f"  {repr_expr(expr)}")


if __name__ == "__main__":
    main()
    # plan()
