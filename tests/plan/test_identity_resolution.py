import pytest
import sqlglot
from sqlglot import exp

from parseval.identity import PARSEVAL_COLUMN_ID, ColumnId
from parseval.instance import Instance
from parseval.plan import Plan


def _plan(sql: str, ddl: str):
    instance = Instance(ddl, name="db", dialect="sqlite")
    return Plan(sqlglot.parse_one(sql, read="sqlite"), instance=instance)


def test_self_join_columns_resolve_to_distinct_query_scope_ids():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY, parent_id INT);"
    plan = _plan(
        "SELECT a.id, b.id FROM users AS a JOIN users AS b ON a.id = b.parent_id",
        ddl,
    )
    ann = plan.annotation_for(plan.root)
    projected = ann.projected_columns

    assert len(projected) == 2
    assert projected[0] != projected[1]
    assert projected[0].source_column_id == projected[1].source_column_id
    assert projected[0].relation != projected[1].relation


def test_column_ast_is_stamped_with_resolved_identity():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
    plan = _plan("SELECT u.id FROM users AS u", ddl)
    col = next(plan.root.projections[0].find_all(exp.Column))

    plan.annotation_for(plan.root)

    assert isinstance(col.meta[PARSEVAL_COLUMN_ID], ColumnId)
    assert col.meta[PARSEVAL_COLUMN_ID].relation.alias.normalized == "u"


def test_plan_exposes_aliases_through_relation_identity_not_alias_map():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
    plan = _plan("SELECT u.id FROM users AS u", ddl)
    ann = plan.annotation_for(plan.root)

    assert not hasattr(plan, "alias_map")
    assert ann.source_relations[0].name.normalized == "users"
    assert ann.source_relations[0].alias.normalized == "u"


def test_bare_ambiguous_column_fails_during_annotation():
    ddl = "CREATE TABLE users (id INT); CREATE TABLE orders (id INT, user_id INT);"
    plan = _plan(
        "SELECT id FROM users JOIN orders ON users.id = orders.user_id",
        ddl,
    )

    with pytest.raises(ValueError, match="Ambiguous column"):
        plan.annotation_for(plan.root)


def test_cte_output_resolves_to_cte_column_not_physical_column():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
    plan = _plan("WITH x AS (SELECT id FROM users) SELECT id FROM x", ddl)

    ann = plan.annotation_for(plan.root)

    assert ann.projected_columns[0].relation.kind.value == "cte"
    assert ann.projected_columns[0].source_column_id.name.normalized == "id"


def test_subquery_output_resolves_to_subquery_column():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
    plan = _plan("SELECT dt.id FROM (SELECT id FROM users) AS dt", ddl)

    ann = plan.annotation_for(plan.root)

    assert ann.projected_columns[0].relation.kind.value == "subquery"
    assert ann.projected_columns[0].relation.alias.normalized == "dt"
