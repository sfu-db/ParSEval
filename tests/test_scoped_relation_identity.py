import pytest
import sqlglot
from sqlglot import exp

from parseval.identity import (
    RelationKind,
    column_identity,
    identifier_name,
    relation_id,
)
from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.constraints import ConstraintGenerator


DDL = "CREATE TABLE races (raceId INT PRIMARY KEY, year INT, name TEXT);"


def _plan(sql: str) -> Plan:
    instance = Instance(DDL, name="db", dialect="sqlite")
    return Plan(sqlglot.parse_one(sql, read="sqlite"), instance=instance)


def test_in_subquery_uses_distinct_internal_bindings():
    plan = _plan(
        "SELECT name FROM races WHERE name NOT IN "
        "(SELECT name FROM races WHERE year = 2000)"
    )
    for step in plan.ordered_steps:
        plan.annotation_for(step)

    in_expression = plan.expression.find(exp.In)
    outer = column_identity(in_expression.this)
    query = in_expression.args["query"]
    inner = column_identity(
        next(
            column
            for column in query.find_all(exp.Column)
            if column.name.lower() == "name"
        )
    )

    assert outer is not None
    assert inner is not None
    assert outer.relation.binding_display != inner.relation.binding_display
    assert (outer.source_column_id or outer).relation.name.normalized == "races"
    assert (inner.source_column_id or inner).relation.name.normalized == "races"


def test_scalar_subquery_uses_distinct_internal_bindings():
    plan = _plan(
        "SELECT r.name FROM races AS r WHERE r.year = "
        "(SELECT MAX(i.year) FROM races AS i)"
    )
    for step in plan.ordered_steps:
        plan.annotation_for(step)

    identities = {
        column.sql(): column_identity(column)
        for column in plan.expression.find_all(exp.Column)
    }

    assert identities["r.year"].relation.binding_display != (
        identities["i.year"].relation.binding_display
    )
    assert (
        (identities["r.year"].source_column_id or identities["r.year"])
        .relation.name.normalized
        == "races"
    )
    assert (
        (identities["i.year"].source_column_id or identities["i.year"])
        .relation.name.normalized
        == "races"
    )


def test_correlated_column_keeps_outer_binding_identity():
    plan = _plan(
        "SELECT r.name FROM races AS r WHERE EXISTS "
        "(SELECT 1 FROM races AS i WHERE i.year < r.year)"
    )
    for step in plan.ordered_steps:
        plan.annotation_for(step)

    identities = {
        column.sql(): column_identity(column)
        for column in plan.expression.find_all(exp.Column)
    }

    assert identities["r.name"].relation == identities["r.year"].relation
    assert (
        identities["r.year"].relation.binding_display
        != identities["i.year"].relation.binding_display
    )


def test_qualified_relation_resolution_does_not_guess_from_name():
    instance = Instance(DDL, name="strict", dialect="sqlite")
    plan = Plan(
        preprocess_sql("SELECT raceId FROM races", instance, dialect="sqlite"),
        instance,
    )
    outer = relation_id(
        RelationKind.TABLE, identifier_name("races"), scope_id="outer"
    )
    inner = relation_id(
        RelationKind.TABLE, identifier_name("races"), scope_id="inner"
    )

    with pytest.raises(ValueError, match="unresolved_scoped_column"):
        ConstraintGenerator(plan, instance)._resolve_relation(
            exp.column("year", table="races"),
            (outer, inner),
        )


def test_foreign_key_resolution_returns_physical_relation():
    instance = Instance(
        """
        CREATE TABLE parent (id INT PRIMARY KEY);
        CREATE TABLE child (
          id INT PRIMARY KEY,
          parent_id INT,
          FOREIGN KEY (parent_id) REFERENCES parent(id)
        );
        """,
        name="fk",
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

    resolved = ConstraintGenerator(plan, instance)._resolve_table_relation(
        exp.to_table("parent"),
        (scoped_parent,),
    )

    assert resolved == instance.table_id("parent")
    assert resolved != scoped_parent


def test_inner_alias_cannot_resolve_from_shadowed_outer_alias():
    instance = Instance(
        """
        CREATE TABLE users (id INT, user_name TEXT);
        CREATE TABLE orders (id INT);
        """,
        name="shadowed_alias",
        dialect="sqlite",
    )
    plan = Plan(
        sqlglot.parse_one(
            """
            SELECT X.id FROM users AS X
            WHERE EXISTS (
              SELECT 1 FROM orders AS X WHERE X.user_name = 'invalid'
            )
            """,
            read="sqlite",
        ),
        instance,
    )

    with pytest.raises(ValueError, match="Unresolved column qualifier: X.user_name"):
        plan.annotation_for(plan.root)
