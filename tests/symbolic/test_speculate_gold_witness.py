import sqlite3

from sqlglot import exp

from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.solver.types import solver_var
from parseval.symbolic.speculate import (
    Propagator,
    Resolver,
    RowBinding,
    SpeculateConfig,
    TableConstraint,
    _bindings_for_solver_var,
    _bindings_for_requirement,
    _build_gold_row_bindings,
    _collect_existing_composite_values,
    _ensure_composite_unique_rows,
    speculate,
)


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


def test_datetime_equality_literal_survives_sqlite_materialization(tmp_path):
    ddl = """
    CREATE TABLE badges (
      Id INTEGER PRIMARY KEY,
      Name TEXT,
      Date DATETIME
    );
    """
    sql = "SELECT Name FROM badges WHERE Date = '2010-07-19 19:39:08.0'"

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    assert rows["badges"][0]["date"] == "2010-07-19 19:39:08.0"

    db_path = tmp_path / "datetime_literal.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output


def test_composite_unique_completion_uses_storage_equivalent_existing_values():
    ddl = """
    CREATE TABLE yearmonth (
        CustomerID INTEGER NOT NULL,
        Date TEXT NOT NULL,
        Consumption REAL,
        PRIMARY KEY (Date, CustomerID)
    );
    """
    instance = Instance(ddl, name="speculate_composite_storage", dialect="sqlite")
    relation = instance.table_id("yearmonth")
    customer_id = instance.column_id(relation, "CustomerID")
    date = instance.column_id(relation, "Date")
    instance.create_row(relation, {customer_id: 38508, date: 1})

    row = {"customerid": 38508, "date": 1, "consumption": 994.117864}
    binding = RowBinding(relation=relation, row=0)
    req = TableConstraint(relation=relation, group_key_columns=[customer_id])
    composite_values = _collect_existing_composite_values(instance)
    builder = type(instance.builder)(instance.schema_spec)

    _ensure_composite_unique_rows(
        row, binding, req, instance, builder, composite_values,
    )

    assert row["date"] != 1


def test_resolver_global_constraint_includes_table_level_check_per_row():
    ddl = """
    CREATE TABLE follow (
      followee INTEGER,
      follower INTEGER,
      CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    sql = "SELECT followee FROM follow WHERE follower = 1"

    instance = Instance(ddls=ddl, name="speculate_check", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    config = SpeculateConfig(
        positive=1,
        negative=0,
        null=0,
        left_unmatched=0,
        right_unmatched=0,
        having_fail=0,
        case_else=0,
        boundary=0,
    )
    spec = Propagator(plan, instance, "sqlite", config=config).propagate()[0]

    constraint, _row_bindings = Resolver(plan, instance)._build_global_constraint(spec)

    check_scopes = {
        (
            solver_var(item.this).row_scope,
            solver_var(item.expression).row_scope,
        )
        for item in constraint.constraints
        if isinstance(item, exp.NEQ)
        and solver_var(item.this) is not None
        and solver_var(item.expression) is not None
        and solver_var(item.this).column_id.name.normalized == "followee"
        and solver_var(item.expression).column_id.name.normalized == "follower"
    }
    assert check_scopes == {("r0", "r0")}


def test_speculate_solves_table_level_check_before_materializing(tmp_path):
    ddl = """
    CREATE TABLE follow (
      followee INTEGER,
      follower INTEGER,
      CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    sql = "SELECT followee FROM follow WHERE follower = 1"

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    assert rows["follow"][0]["follower"] == 1
    assert rows["follow"][0]["followee"] != rows["follow"][0]["follower"]

    db_path = tmp_path / "check_follow.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output


def test_self_join_requirement_matches_only_its_alias_bindings():
    ddl = """
    CREATE TABLE follow (
      followee INTEGER,
      follower INTEGER,
      CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    sql = """
    SELECT T1.follower
    FROM follow AS T1
    INNER JOIN follow AS T2 ON T1.follower = T2.followee
    WHERE T2.follower IS NOT NULL
    GROUP BY T1.follower
    """

    instance = Instance(ddls=ddl, name="speculate_self_join_bindings", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    spec = Propagator(
        plan,
        instance,
        "sqlite",
        config=SpeculateConfig.gold_non_empty(),
    ).propagate()[0]
    row_bindings = _build_gold_row_bindings(spec)

    for relation, req in spec.requirements.items():
        matches = _bindings_for_requirement(relation, req, row_bindings)
        assert matches
        assert {binding.alias for binding in matches} == {req.alias}


def test_repeated_unaliased_table_requirements_keep_separate_scope_bindings():
    ddl = """
    CREATE TABLE follow (
      followee INTEGER,
      follower INTEGER,
      CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    sql = """
    SELECT followee
    FROM follow
    WHERE followee IN (SELECT follower FROM follow)
    GROUP BY followee
    """

    instance = Instance(ddls=ddl, name="speculate_repeated_unaliased", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    spec = Propagator(
        plan,
        instance,
        "sqlite",
        config=SpeculateConfig.gold_non_empty(),
    ).propagate()[0]
    row_bindings = _build_gold_row_bindings(spec)

    scoped_requirements = [
        (relation, req)
        for relation, req in spec.requirements.items()
        if req.table == "follow" and req.relation.scope_id is not None
    ]
    assert len(scoped_requirements) >= 2

    for relation, req in scoped_requirements:
        matches = _bindings_for_requirement(relation, req, row_bindings)
        assert matches
        assert {binding.relation.scope_id for binding in matches} == {
            req.relation.scope_id
        }


def test_repeated_unaliased_solver_vars_match_only_their_scope_bindings():
    ddl = """
    CREATE TABLE follow (
      followee INTEGER,
      follower INTEGER,
      CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    sql = """
    SELECT followee
    FROM follow
    WHERE followee IN (SELECT follower FROM follow)
    GROUP BY followee
    """

    instance = Instance(ddls=ddl, name="speculate_repeated_unaliased_vars", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    spec = Propagator(
        plan,
        instance,
        "sqlite",
        config=SpeculateConfig.gold_non_empty(),
    ).propagate()[0]
    row_bindings = _build_gold_row_bindings(spec)

    scoped_vars = []
    for req in spec.requirements.values():
        for constraint in req.constraints:
            for col in constraint.find_all(exp.Column):
                var = solver_var(col)
                if var is not None and var.relation_id.scope_id is not None:
                    scoped_vars.append(var)
    assert scoped_vars

    for var in scoped_vars:
        matches = _bindings_for_solver_var(var, row_bindings)
        assert matches
        assert {binding.relation.scope_id for binding in matches} == {
            var.relation_id.scope_id
        }


def test_global_constraint_does_not_lower_aggregate_alias_as_storage_column():
    ddl = """
    CREATE TABLE follow (
      followee INTEGER,
      follower INTEGER,
      CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    sql = """
    SELECT T1.follower, COUNT(DISTINCT T2.follower) AS num
    FROM follow AS T1
    INNER JOIN follow AS T2 ON T1.follower = T2.followee
    WHERE T2.follower IS NOT NULL
    GROUP BY T1.follower
    """

    instance = Instance(ddls=ddl, name="speculate_aggregate_alias", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    spec = Propagator(
        plan,
        instance,
        "sqlite",
        config=SpeculateConfig.gold_non_empty(),
    ).propagate()[0]

    constraint, _row_bindings = Resolver(plan, instance)._build_global_constraint(spec)

    lowered_columns = {
        solver_var(col).column_id.name.normalized
        for expression in constraint.constraints
        for col in expression.find_all(exp.Column)
        if solver_var(col) is not None
    }
    assert "num" not in lowered_columns


def test_table_check_lowering_uses_one_relation_scope_per_row():
    ddl = """
    CREATE TABLE FOLLOW (
      FOLLOWEE VARCHAR(30) NOT NULL,
      FOLLOWER VARCHAR(30) NOT NULL,
      CONSTRAINT PK_FOLLOW PRIMARY KEY (FOLLOWEE, FOLLOWER),
      CONSTRAINT CHECK_FOLLOW CHECK (FOLLOWEE <> FOLLOWER)
    );
    """
    sql = """
    SELECT FOLLOWEE
    FROM FOLLOW
    WHERE FOLLOWEE IN (SELECT FOLLOWER FROM FOLLOW)
    GROUP BY FOLLOWEE
    """

    instance = Instance(ddls=ddl, name="speculate_check_scope", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    spec = Propagator(
        plan,
        instance,
        "sqlite",
        config=SpeculateConfig.gold_non_empty(),
    ).propagate()[0]

    constraint, _row_bindings = Resolver(plan, instance)._build_global_constraint(spec)

    for expression in constraint.constraints:
        if not isinstance(expression, exp.NEQ):
            continue
        left = solver_var(expression.this)
        right = solver_var(expression.expression)
        if left is None or right is None:
            continue
        if {
            left.column_id.name.normalized,
            right.column_id.name.normalized,
        } == {"followee", "follower"}:
            assert left.relation_id.scope_id == right.relation_id.scope_id


def test_follow_self_join_check_witness_writes_and_returns_rows(tmp_path):
    ddl = """
    CREATE TABLE FOLLOW (
      FOLLOWEE VARCHAR(30) NOT NULL,
      FOLLOWER VARCHAR(30) NOT NULL,
      CONSTRAINT PK_FOLLOW PRIMARY KEY (FOLLOWEE, FOLLOWER),
      CONSTRAINT CHECK_FOLLOW CHECK (FOLLOWEE <> FOLLOWER)
    );
    """
    sql = """
    SELECT DISTINCT T1.FOLLOWER,
           COUNT(DISTINCT T2.FOLLOWER) AS NUM
    FROM FOLLOW AS T1
    INNER JOIN FOLLOW AS T2 ON T1.FOLLOWER = T2.FOLLOWEE
    WHERE T2.FOLLOWER IS NOT NULL
    GROUP BY T1.FOLLOWER
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = [
        (
            row[instance.column_id("follow", "followee")].concrete,
            row[instance.column_id("follow", "follower")].concrete,
        )
        for row in instance.get_rows("follow")
    ]
    assert rows
    assert len(rows) == len(set(rows))
    assert all(followee != follower for followee, follower in rows)

    db_path = tmp_path / "follow_self_join.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output


def test_follow_in_subquery_check_witness_writes_and_returns_rows(tmp_path):
    ddl = """
    CREATE TABLE FOLLOW (
      FOLLOWEE VARCHAR(30) NOT NULL,
      FOLLOWER VARCHAR(30) NOT NULL,
      CONSTRAINT PK_FOLLOW PRIMARY KEY (FOLLOWEE, FOLLOWER),
      CONSTRAINT CHECK_FOLLOW CHECK (FOLLOWEE <> FOLLOWER)
    );
    """
    sql = """
    SELECT FOLLOWEE AS FOLLOWER,
           COUNT(FOLLOWEE) AS NUM
    FROM FOLLOW
    WHERE FOLLOWEE IN (SELECT FOLLOWER FROM FOLLOW)
    GROUP BY 1
    ORDER BY 1
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = [
        (
            row[instance.column_id("follow", "followee")].concrete,
            row[instance.column_id("follow", "follower")].concrete,
        )
        for row in instance.get_rows("follow")
    ]
    assert rows
    assert len(rows) == len(set(rows))
    assert all(followee != follower for followee, follower in rows)

    db_path = tmp_path / "follow_in_subquery.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output


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
    matching_rows = [row for row in rows["t"] if row["id"] == 7]
    assert matching_rows
    assert matching_rows[0]["time"] is not None

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
