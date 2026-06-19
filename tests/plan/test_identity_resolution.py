import pytest
import sqlglot
from sqlglot import exp

from parseval.identity import PARSEVAL_COLUMN_ID, ColumnId, column_identity
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


def test_nested_join_columns_keep_alias_scope_and_physical_lineage():
    ddl = """
    CREATE TABLE atom (
      atom_id TEXT PRIMARY KEY,
      element TEXT
    );
    CREATE TABLE connected (
      atom_id TEXT NOT NULL,
      atom_id2 TEXT NOT NULL,
      PRIMARY KEY (atom_id, atom_id2),
      FOREIGN KEY (atom_id) REFERENCES atom(atom_id)
    );
    """
    plan = _plan(
        """
        SELECT DISTINCT T.element
        FROM atom AS T
        WHERE T.element NOT IN (
          SELECT DISTINCT T1.element
          FROM atom AS T1
          INNER JOIN connected AS T2 ON T1.atom_id = T2.atom_id
        )
        """,
        ddl,
    )

    for step in plan.ordered_steps:
        plan.annotation_for(step)

    columns = {
        (column.table.lower(), column.name.lower()): column_identity(column)
        for column in plan.expression.find_all(exp.Column)
    }
    outer = columns[("t", "element")]
    inner_atom = columns[("t1", "atom_id")]
    inner_connected = columns[("t2", "atom_id")]

    assert outer is not None
    assert inner_atom is not None
    assert inner_connected is not None
    assert len({outer.relation, inner_atom.relation, inner_connected.relation}) == 3
    assert (outer.source_column_id or outer).relation == plan._instance.table_id("atom")
    assert (inner_atom.source_column_id or inner_atom).relation == plan._instance.table_id("atom")
    assert (inner_connected.source_column_id or inner_connected).relation == plan._instance.table_id("connected")


def test_reused_alias_text_is_distinguished_by_query_scope():
    ddl = """
    CREATE TABLE atom (atom_id TEXT PRIMARY KEY, element TEXT);
    CREATE TABLE connected (atom_id TEXT NOT NULL, atom_id2 TEXT NOT NULL);
    """
    plan = _plan(
        """
        SELECT X.element
        FROM atom AS X
        WHERE EXISTS (
          SELECT 1 FROM connected AS X WHERE X.atom_id IS NOT NULL
        )
        """,
        ddl,
    )
    for step in plan.ordered_steps:
        plan.annotation_for(step)

    outer = next(
        column_identity(column)
        for column in plan.expression.find_all(exp.Column)
        if column.name.lower() == "element"
    )
    inner = next(
        column_identity(column)
        for column in plan.expression.find_all(exp.Column)
        if column.name.lower() == "atom_id"
    )

    assert outer is not None
    assert inner is not None
    assert outer.relation != inner.relation
    assert outer.relation.scope_id != inner.relation.scope_id
    assert (outer.source_column_id or outer).relation.name.normalized == "atom"
    assert (inner.source_column_id or inner).relation.name.normalized == "connected"


def test_plan_exposes_aliases_through_relation_identity_not_alias_map():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
    plan = _plan("SELECT u.id FROM users AS u", ddl)
    ann = plan.annotation_for(plan.root)

    assert not hasattr(plan, "alias_map")
    assert not hasattr(ann, "source_tables")
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


def test_projected_alias_does_not_expose_source_name():
    ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
    plan = _plan("SELECT dt.id FROM (SELECT id AS x FROM users) AS dt", ddl)

    with pytest.raises(ValueError, match="Unresolved column"):
        plan.annotation_for(plan.root)
