import sqlite3

from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.speculate import SpeculateConfig, speculate


def _speculate_rows(sql: str, ddl: str):
    instance = Instance(ddls=ddl, name="speculate_gold_witness", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    return instance, speculate(
        plan,
        instance,
        dialect="sqlite",
        config=SpeculateConfig.gold_non_empty(),
    )


def test_gold_witness_keeps_literal_filtered_join_key_on_materialized_row(tmp_path):
    ddl = """
    CREATE TABLE client (
      client_id INTEGER PRIMARY KEY,
      gender TEXT
    );
    CREATE TABLE disp (
      disp_id INTEGER PRIMARY KEY,
      client_id INTEGER NOT NULL,
      account_id INTEGER NOT NULL,
      FOREIGN KEY (client_id) REFERENCES client(client_id)
    );
    CREATE TABLE account (
      account_id INTEGER PRIMARY KEY,
      frequency TEXT
    );
    CREATE TABLE trans (
      trans_id INTEGER PRIMARY KEY,
      account_id INTEGER NOT NULL,
      operation TEXT,
      FOREIGN KEY (account_id) REFERENCES account(account_id)
    );
    """
    sql = """
    SELECT T4.trans_id
    FROM client AS T1
    INNER JOIN disp AS T2 ON T1.client_id = T2.client_id
    INNER JOIN account AS T3 ON T2.account_id = T3.account_id
    INNER JOIN trans AS T4 ON T3.account_id = T4.account_id
    WHERE T1.client_id = 3356 AND T4.operation = 'VYBER'
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    assert rows["client"][0]["client_id"] == 3356
    assert rows["disp"][0]["client_id"] == 3356

    db_path = tmp_path / "witness.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output


def test_gold_witness_expands_count_group_without_changing_group_key(tmp_path):
    ddl = """
    CREATE TABLE member (
      member_id TEXT PRIMARY KEY,
      first_name TEXT,
      last_name TEXT
    );
    CREATE TABLE attendance (
      link_to_event TEXT,
      link_to_member TEXT,
      PRIMARY KEY (link_to_event, link_to_member),
      FOREIGN KEY (link_to_member) REFERENCES member(member_id)
    );
    """
    sql = """
    SELECT T1.first_name, T1.last_name
    FROM member AS T1
    INNER JOIN attendance AS T2 ON T1.member_id = T2.link_to_member
    GROUP BY T2.link_to_member
    HAVING COUNT(T2.link_to_event) > 2
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    member_id = rows["member"][0]["member_id"]
    assert len(rows["member"]) == 1
    assert len(rows["attendance"]) >= 3
    assert {row["link_to_member"] for row in rows["attendance"]} == {member_id}
    assert len({row["link_to_event"] for row in rows["attendance"]}) >= 3

    db_path = tmp_path / "aggregate_witness.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output


def test_speculate_materializes_constraint_rows_into_instance():
    ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT);"
    sql = "SELECT id FROM t WHERE value = 'target'"

    instance, results = _speculate_rows(sql, ddl)

    assert results
    # Find the positive branch
    positive = next((r for r in results if r[0] == "positive"), None)
    assert positive is not None
    assert positive[1]["t"][0]["value"] == "target"
    stored_rows = instance.get_rows("t")
    assert len(stored_rows) >= 1
    target_rows = [r for r in stored_rows if r["value"].concrete == "target"]
    assert len(target_rows) >= 1


def test_cte_outer_filter_pushes_through_projected_column(tmp_path):
    ddl = "CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, amount INTEGER);"
    sql = """
    WITH x AS (
      SELECT id, status FROM orders WHERE status = 'open'
    )
    SELECT id FROM x WHERE id = 7
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    assert rows["orders"][0]["id"] == 7
    assert rows["orders"][0]["status"] == "open"

    db_path = tmp_path / "cte_witness.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output == [(7,)]


def test_chained_cte_outer_filter_pushes_to_physical_source(tmp_path):
    ddl = "CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, amount INTEGER);"
    sql = """
    WITH x AS (
      SELECT id, status FROM orders WHERE status = 'open'
    ), y AS (
      SELECT id FROM x WHERE id > 3
    )
    SELECT id FROM y WHERE id = 7
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    assert rows["orders"][0]["id"] == 7
    assert rows["orders"][0]["status"] == "open"

    db_path = tmp_path / "chained_cte_witness.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output == [(7,)]


def test_derived_cte_not_null_does_not_drop_physical_filter(tmp_path):
    ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY, time TEXT);"
    sql = """
    WITH x AS (
      SELECT id, CAST(SUBSTR(time, 1, 1) AS REAL) AS seconds
      FROM t
      WHERE time IS NOT NULL
    )
    SELECT id FROM x WHERE seconds IS NOT NULL AND id = 7
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    assert rows["t"][0]["id"] == 7
    assert rows["t"][0]["time"] is not None

    db_path = tmp_path / "derived_cte_witness.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output == [(7,)]


def test_expression_join_key_generates_positive_witness(tmp_path):
    ddl = "CREATE TABLE t (x INTEGER PRIMARY KEY);"
    sql = """
    WITH v AS (
      SELECT x, x + 1 AS y FROM t
    )
    SELECT t.x
    FROM t
    INNER JOIN (
      SELECT MIN(v.y) AS min_y FROM v
    ) AS m ON t.x + 1 = m.min_y
    """

    instance = Instance(ddls=ddl, name="speculate_expression_join", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    results = speculate(
        plan,
        instance,
        dialect="sqlite",
        config=SpeculateConfig(
            positive=1,
            negative=0,
            null=0,
            left_unmatched=0,
            right_unmatched=0,
            having_fail=0,
            case_else=0,
            boundary=0,
        ),
    )

    assert results
    rows = next(rows for branch, rows in results if branch == "positive")
    assert rows["t"]

    db_path = tmp_path / "expression_join_witness.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output
