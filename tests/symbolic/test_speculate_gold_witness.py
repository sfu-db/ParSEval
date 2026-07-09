import json
import sqlite3
from pathlib import Path

import pytest
from sqlglot import exp, parse_one

from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.identity import (
    ColumnKind,
    PARSEVAL_COLUMN_ID,
    RelationKind,
    column_id,
    identifier_name,
    physical_column,
    relation_id,
    table_relation,
)
from parseval.main import disprove
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.solver import Solver, SolverVar, set_solver_var
from parseval.solver.types import solver_var
from parseval.symbolic.speculate import (
    BranchSpec,
    Propagator,
    Resolver,
    RowBinding,
    SpeculateConfig,
    TableConstraint,
    _apply_literal_constraints_to_row,
    _bindings_for_solver_var,
    _bindings_for_requirement,
    _build_gold_row_bindings,
    _clone_rows_for_high_limit,
    _normalize_pending_rows_for_materialization,
    _pre_fill_group_key_values,
    _pre_fill_plan_distinct_values,
    _rewrite_constraint_for_binding,
    _row_binding_sort_key,
    _storage_equivalent_value_key,
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


def _bird_plan(case_index: int):
    schema_path = Path("data/sqlite/schema.json")
    dev_path = Path("data/sqlite/dev.json")
    if not schema_path.exists() or not dev_path.exists():
        pytest.skip("BIRD SQLite fixtures are not available")
    row = json.loads(dev_path.read_text())[case_index]
    schemas = json.loads(schema_path.read_text())
    raw_ddls = schemas[row["db_id"]]
    ddls = raw_ddls if isinstance(raw_ddls, str) else ";".join(raw_ddls)
    instance = Instance(
        ddls=ddls,
        name=f"speculate_bird_{case_index}",
        dialect="sqlite",
    )
    expr = preprocess_sql(row["SQL"], instance, dialect="sqlite")
    return Plan(expr, instance), instance


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


def test_resolver_rejects_unlowered_deferred_scalar_subquery():
    ddl = """
    CREATE TABLE scores (
      id INTEGER PRIMARY KEY,
      score INTEGER
    );
    """
    sql = """
    SELECT s.id
    FROM scores AS s
    WHERE s.score > (SELECT AVG(score) FROM scores)
    """
    instance = Instance(ddls=ddl, name="speculate_scalar_skip", dialect="sqlite")
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
    spec = next(
        spec for spec in Propagator(plan, instance, "sqlite", config=config).propagate()
        if spec.branch == "positive_seed_deferred"
    )
    assert spec.deferred

    rows = Resolver(
        plan, instance, "sqlite", solver=Solver(dialect="sqlite")
    ).resolve(spec)

    assert spec.unsupported_reason == "unsupported_lowering"
    assert rows == {}


def test_global_constraint_does_not_include_raw_subquery_even_when_scoped():
    ddl = "CREATE TABLE t (id INTEGER PRIMARY KEY, value INTEGER);"
    instance = Instance(ddls=ddl, name="speculate_no_raw_subquery", dialect="sqlite")
    expr = preprocess_sql("SELECT id FROM t", instance, dialect="sqlite")
    plan = Plan(expr, instance)
    relation = instance.table_id("t")
    id_column = instance.column_id(relation, "id")
    constraint = parse_one(
        "id IN (SELECT id FROM t)",
        into=exp.Condition,
        dialect="sqlite",
    )
    set_solver_var(
        next(constraint.find_all(exp.Column)),
        SolverVar(id_column, relation, "r0"),
    )
    spec = BranchSpec(
        branch="positive",
        requirements={
            relation: TableConstraint(
                relation=relation,
                constraints=[constraint],
            )
        },
    )

    solver_constraint, _row_bindings = Resolver(
        plan, instance
    )._build_global_constraint(spec)

    assert not any(
        item.find(exp.Subquery) or item.find(exp.Exists)
        for item in solver_constraint.constraints
    )


def _execute_rows(ddl: str, rows_per_table: dict, sql: str):
    with sqlite3.connect(":memory:") as connection:
        for ddl_part in ddl.split(";"):
            ddl_part = ddl_part.strip()
            if ddl_part:
                connection.execute(ddl_part)
        for table_name, rows in rows_per_table.items():
            if not rows:
                continue
            columns = list(rows[0])
            quoted = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _column in columns)
            statement = f'INSERT INTO "{table_name}" ({quoted}) VALUES ({placeholders})'
            for row in rows:
                connection.execute(statement, [row[column] for column in columns])
        connection.commit()
        return connection.execute(sql).fetchall()


def test_speculate_not_in_emits_semantic_outer_antimatch_witness():
    ddl = """
    CREATE TABLE customers (id INT PRIMARY KEY, name TEXT);
    CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT);
    """
    sql = """
    SELECT name
    FROM customers
    WHERE id NOT IN (SELECT customer_id FROM orders)
    """

    _instance, results = _speculate_rows(sql, ddl)

    by_branch = dict(results)
    assert "positive_semantic_not_in" in by_branch
    rows = by_branch["positive_semantic_not_in"]
    assert rows["customers"]
    customer_ids = {row["id"] for row in rows["customers"]}
    order_customer_ids = {
        row["customer_id"]
        for row in rows.get("orders", [])
        if row.get("customer_id") is not None
    }
    assert customer_ids - order_customer_ids
    assert _execute_rows(ddl, rows, sql)


def test_speculate_not_exists_emits_semantic_outer_without_matching_inner():
    ddl = """
    CREATE TABLE customers (id INT PRIMARY KEY, name TEXT);
    CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT);
    """
    sql = """
    SELECT name
    FROM customers AS c
    WHERE NOT EXISTS (
      SELECT 1 FROM orders AS o WHERE o.customer_id = c.id
    )
    """

    _instance, results = _speculate_rows(sql, ddl)

    by_branch = dict(results)
    assert "positive_semantic_not_exists" in by_branch
    rows = by_branch["positive_semantic_not_exists"]
    assert rows["customers"]
    customer_ids = {row["id"] for row in rows["customers"]}
    order_customer_ids = {
        row["customer_id"]
        for row in rows.get("orders", [])
        if row.get("customer_id") is not None
    }
    assert not (customer_ids & order_customer_ids)
    assert _execute_rows(ddl, rows, sql)


def test_speculate_count_zero_emits_semantic_outer_antimatch_witness():
    ddl = """
    CREATE TABLE salesperson (sales_id INT PRIMARY KEY, name TEXT);
    CREATE TABLE orders (order_id INT PRIMARY KEY, sales_id INT, com_id INT);
    CREATE TABLE company (com_id INT PRIMARY KEY, name TEXT);
    """
    sql = """
    SELECT name
    FROM salesperson AS s
    WHERE 0 = (
      SELECT COUNT(*)
      FROM orders AS o
      JOIN company AS c ON o.com_id = c.com_id
      WHERE o.sales_id = s.sales_id AND c.name = 'RED'
    )
    """

    _instance, results = _speculate_rows(sql, ddl)

    by_branch = dict(results)
    assert "positive_semantic_count_zero" in by_branch
    rows = by_branch["positive_semantic_count_zero"]
    assert rows["salesperson"]
    assert _execute_rows(ddl, rows, sql)


def test_count_zero_subplan_detection_skips_only_count_zero_subquery():
    ddl = """
    CREATE TABLE customers (id INT PRIMARY KEY, name TEXT);
    CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT, total INT);
    CREATE TABLE refunds (id INT PRIMARY KEY, customer_id INT);
    """
    sql = """
    SELECT name
    FROM customers AS c
    WHERE 0 = (
      SELECT COUNT(*)
      FROM refunds AS r
      WHERE r.customer_id = c.id
    )
    AND c.id IN (
      SELECT o.customer_id
      FROM orders AS o
      WHERE o.total = 9
    )
    """

    _instance, results = _speculate_rows(sql, ddl)

    rows = dict(results)["positive_semantic_count_zero"]
    assert rows["customers"]
    assert rows["orders"]
    assert rows["orders"][0]["total"] == 9
    assert _execute_rows(ddl, rows, sql)


def test_speculate_scalar_subquery_or_emits_adjacent_free_seat_witness():
    ddl = "CREATE TABLE cinema (seat_id INT PRIMARY KEY, free INT);"
    sql = """
    SELECT O1.seat_id
    FROM cinema AS O1
    WHERE O1.free = 1
      AND (
        (SELECT O2.free FROM cinema AS O2 WHERE O2.seat_id = O1.seat_id - 1) = 1
        OR
        (SELECT O2.free FROM cinema AS O2 WHERE O2.seat_id = O1.seat_id + 1) = 1
      )
    """

    _instance, results = _speculate_rows(sql, ddl)

    by_branch = dict(results)
    assert "positive_semantic_scalar_subquery_0" in by_branch
    rows = by_branch["positive_semantic_scalar_subquery_0"]
    assert len(rows["cinema"]) >= 2
    free_seats = {row["seat_id"] for row in rows["cinema"] if row["free"] == 1}
    assert len(free_seats) >= 2
    assert any(seat + 1 in free_seats for seat in free_seats)
    assert _execute_rows(ddl, rows, sql)


def test_speculate_tuple_in_emits_first_login_witness():
    ddl = """
    CREATE TABLE activity (
      player_id INT,
      device_id INT,
      event_date DATE,
      games_played INT
    );
    """
    sql = """
    SELECT player_id, device_id
    FROM activity
    WHERE (player_id, event_date) IN (
      SELECT player_id, MIN(event_date)
      FROM activity
      GROUP BY player_id
    )
    """

    _instance, results = _speculate_rows(sql, ddl)

    rows = dict(results).get("positive_semantic_in")
    assert rows is not None
    assert rows["activity"]
    assert _execute_rows(ddl, rows, sql)


def test_speculate_tuple_in_and_antimatch_emit_insurance_witness():
    ddl = """
    CREATE TABLE insurance (
      pid INT PRIMARY KEY,
      tiv_2015 FLOAT,
      tiv_2016 FLOAT,
      lat FLOAT,
      lon FLOAT
    );
    """
    sql = """
    SELECT SUM(tiv_2016) AS tiv_2016
    FROM insurance
    WHERE tiv_2015 IN (
      SELECT tiv_2015
      FROM insurance
      GROUP BY tiv_2015
      HAVING COUNT(*) > 1
    )
    AND (lat, lon) IN (
      SELECT lat, lon
      FROM insurance
      GROUP BY lat, lon
      HAVING COUNT(*) = 1
    )
    """

    _instance, results = _speculate_rows(sql, ddl)

    rows = dict(results).get("positive_semantic_in")
    assert rows is not None
    assert len(rows["insurance"]) >= 2
    assert _execute_rows(ddl, rows, sql)


def test_speculate_grouped_in_emits_followee_witness():
    ddl = """
    CREATE TABLE follow (
      followee INT,
      follower INT
    );
    """
    sql = """
    SELECT followee, COUNT(*) AS num
    FROM follow
    WHERE followee IN (
      SELECT follower
      FROM follow
    )
    GROUP BY followee
    """

    _instance, results = _speculate_rows(sql, ddl)

    rows = dict(results).get("positive_semantic_in")
    assert rows is not None
    assert rows["follow"]
    assert _execute_rows(ddl, rows, sql)


def test_scalar_subquery_semantic_specs_match_atoms_by_subplan_identity():
    ddl = """
    CREATE TABLE outer_t (id INT PRIMARY KEY, marker INT);
    CREATE TABLE inner_t (outer_id INT, a INT, b INT);
    """
    sql = """
    SELECT o.id
    FROM outer_t AS o
    WHERE 22 = (
      SELECT i.b FROM inner_t AS i WHERE i.outer_id = o.id
    )
    AND (
      SELECT i.a FROM inner_t AS i WHERE i.outer_id = o.id
    ) = 11
    """

    _instance, results = _speculate_rows(sql, ddl)

    scalar_rows = {
        branch: rows
        for branch, rows in results
        if branch.startswith("positive_semantic_scalar_subquery_")
    }
    assert len(scalar_rows) == 2
    assert any(
        rows["inner_t"][0].get("a") == 11
        and rows["inner_t"][0].get("b") != 11
        for rows in scalar_rows.values()
    )
    assert any(
        rows["inner_t"][0].get("b") == 22
        and rows["inner_t"][0].get("a") != 22
        for rows in scalar_rows.values()
    )


def test_speculate_unsupported_deferred_predicate_is_not_reported_as_witness():
    ddl = "CREATE TABLE t (id INT PRIMARY KEY, value INT);"
    sql = """
    SELECT id
    FROM t
    WHERE value > (
      SELECT AVG(value)
      FROM t
      GROUP BY value
      HAVING COUNT(*) > 1
    )
    """

    _instance, results = _speculate_rows(sql, ddl)

    by_branch = dict(results)
    assert "positive_seed_deferred" not in by_branch
    assert "positive_semantic_scalar_subquery_0" not in by_branch


def test_speculate_drops_value_branch_when_generated_rows_do_not_observe_objective(monkeypatch):
    ddl = "CREATE TABLE t (id INT PRIMARY KEY, value INT);"
    sql = "SELECT id FROM t WHERE value = 5"
    instance = Instance(ddls=ddl, name="speculate_unobserved_branch", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)
    relation = instance.table_id("t")

    def unobserved_rows(self, spec):
        return {"t": [{"id": 1, "value": 4}]}

    def validation_spec(self):
        return [
            BranchSpec(
                branch="positive",
                goals={"value"},
                requirements={relation: TableConstraint(relation=relation)},
                validation_expectation="query_non_empty",
            )
        ]

    monkeypatch.setattr(Propagator, "propagate", validation_spec)
    monkeypatch.setattr(Resolver, "resolve", unobserved_rows)

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
            join_antimatch=0,
            join_fanout=0,
            aggregate_contrast=0,
            rank_contrast=0,
            project_duplicate=0,
        ),
    )

    assert results == []


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


def test_gold_witness_expands_count_distinct_group_with_distinct_values(tmp_path):
    ddl = """
    CREATE TABLE views (
      article_id INT,
      author_id INT,
      viewer_id INT,
      view_date DATE
    );
    """
    sql = """
    SELECT DISTINCT viewer_id AS id
    FROM views
    GROUP BY viewer_id, view_date
    HAVING COUNT(DISTINCT article_id) > 1
    ORDER BY id
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    assert len(rows["views"]) >= 2
    assert len({row["viewer_id"] for row in rows["views"]}) == 1
    assert len({row["view_date"] for row in rows["views"]}) == 1
    assert None not in {row["article_id"] for row in rows["views"]}
    assert len({row["article_id"] for row in rows["views"]}) >= 2

    db_path = tmp_path / "views_distinct.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output


def test_gold_witness_date_range_anti_subquery_returns_product(tmp_path):
    ddl = """
    CREATE TABLE product (
      product_id INT PRIMARY KEY,
      product_name TEXT,
      unit_price INT
    );
    CREATE TABLE sales (
      seller_id INT,
      product_id INT,
      buyer_id INT,
      sale_date DATE,
      quantity INT,
      price INT,
      FOREIGN KEY (product_id) REFERENCES product(product_id)
    );
    """
    sql = """
    SELECT p.product_id, p.product_name
    FROM product AS p
    JOIN sales AS s ON p.product_id = s.product_id
    WHERE s.sale_date BETWEEN '2019-01-01' AND '2019-03-31'
      AND p.product_id NOT IN (
        SELECT product_id
        FROM sales
        WHERE sale_date < '2019-01-01' OR sale_date > '2019-03-31'
      )
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    assert rows["product"]
    assert rows["sales"]
    assert any(
        "2019-01-01" <= row["sale_date"] <= "2019-03-31"
        for row in rows["sales"]
    )

    db_path = tmp_path / "product_sales_range.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output


def test_gold_witness_1084_having_min_max_date_range_returns_product(tmp_path):
    ddl = """
    CREATE TABLE product (
      product_id INT PRIMARY KEY,
      product_name TEXT,
      unit_price INT
    );
    CREATE TABLE sales (
      seller_id INT,
      product_id INT,
      buyer_id INT,
      sale_date DATE,
      quantity INT,
      price INT,
      FOREIGN KEY (product_id) REFERENCES product(product_id)
    );
    """
    sql = """
    SELECT p.product_id
    FROM product AS p
    JOIN sales AS s ON p.product_id = s.product_id
    GROUP BY p.product_id
    HAVING MIN(s.sale_date) >= '2019-01-01'
       AND MAX(s.sale_date) <= '2019-03-31'
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    assert rows["product"]
    assert rows["sales"]
    assert any(
        row["sale_date"] is not None
        and "2019-01-01" <= str(row["sale_date"]) <= "2019-03-31"
        for row in rows["sales"]
    )

    db_path = tmp_path / "product_sales_1084_having.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert output


def test_gold_witness_1084_having_nullable_min_max_keeps_null_input(tmp_path):
    ddl = """
    CREATE TABLE product (
      product_id INT PRIMARY KEY,
      product_name TEXT,
      unit_price INT
    );
    CREATE TABLE sales (
      seller_id INT,
      product_id INT,
      buyer_id INT,
      sale_date DATE,
      quantity INT,
      price INT,
      FOREIGN KEY (product_id) REFERENCES product(product_id)
    );
    """
    sql = """
    SELECT p.product_id
    FROM product AS p
    JOIN sales AS s ON p.product_id = s.product_id
    GROUP BY p.product_id
    HAVING MIN(s.sale_date) >= '2019-01-01'
       AND MAX(s.sale_date) <= '2019-03-31'
    """

    instance, results = _speculate_rows(sql, ddl)

    assert results
    rows = results[0][1]
    product_id = rows["product"][0]["product_id"]
    group_sales = [
        row for row in rows["sales"] if row["product_id"] == product_id
    ]
    assert any(
        row["sale_date"] is not None
        and "2019-01-01" <= str(row["sale_date"]) <= "2019-03-31"
        for row in group_sales
    )
    assert any(row["sale_date"] is None for row in group_sales)

    db_path = tmp_path / "product_sales_1084_having_null.sqlite"
    to_db(instance, f"sqlite:///{db_path}", dialect="sqlite")
    with sqlite3.connect(db_path) as connection:
        output = connection.execute(sql).fetchall()

    assert (product_id,) in output


def test_gold_witness_date_range_anti_subquery_can_return_null_sale_date():
    ddl = """
    CREATE TABLE product (
      product_id INT PRIMARY KEY,
      product_name TEXT,
      unit_price INT
    );
    CREATE TABLE sales (
      seller_id INT,
      product_id INT,
      buyer_id INT,
      sale_date DATE,
      quantity INT,
      price INT,
      FOREIGN KEY (product_id) REFERENCES product(product_id)
    );
    """
    sql = """
    SELECT p.product_id, p.product_name
    FROM product AS p
    JOIN sales AS s ON p.product_id = s.product_id
    WHERE p.product_id NOT IN (
      SELECT product_id
      FROM sales
      WHERE sale_date < '2019-01-01' OR sale_date > '2019-03-31'
    )
    """

    _instance, results = _speculate_rows(sql, ddl)

    null_rows = [
        rows
        for _branch, rows in results
        if any(row["sale_date"] is None for row in rows.get("sales", []))
    ]
    assert null_rows
    assert any(_execute_rows(ddl, rows, sql) for rows in null_rows)


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


def test_materialization_boundary_rewrites_virtual_alias_to_physical_source():
    instance = Instance(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, reputation INTEGER);",
        name="speculate_materialization_alias",
        dialect="sqlite",
    )
    storage_relation = table_relation("users", dialect="sqlite")
    scoped_relation = relation_id(
        RelationKind.TABLE,
        identifier_name("users", dialect="sqlite"),
        scope_id="s1",
    )
    source = physical_column("reputation", storage_relation, dialect="sqlite")
    aggregate = column_id(
        ColumnKind.AGGREGATE,
        identifier_name("max_reputation", dialect="sqlite"),
        scoped_relation,
        source_column_id=source,
    )
    alias_col = exp.column("max_reputation", table="users")
    alias_col.meta[PARSEVAL_COLUMN_ID] = aggregate
    constraint = exp.EQ(this=exp.column("id", table="users"), expression=alias_col)
    spec = BranchSpec(
        branch="positive",
        requirements={
            scoped_relation: TableConstraint(
                relation=scoped_relation,
                constraints=[constraint],
            )
        },
    )

    rows = _normalize_pending_rows_for_materialization(
        {("users", "", "s1", 0): {"max_reputation": 10}},
        spec,
        instance,
    )

    assert rows[("users", "", "s1", 0)] == {"reputation": 10}


def test_materialization_alias_map_requires_exact_alias_and_scope_match():
    instance = Instance(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, a INTEGER, b INTEGER);",
        name="speculate_materialization_exact_alias",
        dialect="sqlite",
    )
    storage_relation = table_relation("users", dialect="sqlite")
    left_relation = relation_id(
        RelationKind.TABLE,
        identifier_name("users", dialect="sqlite"),
        alias=identifier_name("left_user", dialect="sqlite"),
        scope_id="left_scope",
    )
    right_relation = relation_id(
        RelationKind.TABLE,
        identifier_name("users", dialect="sqlite"),
        alias=identifier_name("right_user", dialect="sqlite"),
        scope_id="right_scope",
    )
    left_derived = column_id(
        ColumnKind.PROJECTED,
        identifier_name("derived_value", dialect="sqlite"),
        left_relation,
        source_column_id=physical_column("a", storage_relation, dialect="sqlite"),
    )
    right_derived = column_id(
        ColumnKind.PROJECTED,
        identifier_name("derived_value", dialect="sqlite"),
        right_relation,
        source_column_id=physical_column("b", storage_relation, dialect="sqlite"),
    )
    left_col = exp.column("derived_value", table="left_user")
    left_col.meta[PARSEVAL_COLUMN_ID] = left_derived
    right_col = exp.column("derived_value", table="right_user")
    right_col.meta[PARSEVAL_COLUMN_ID] = right_derived
    spec = BranchSpec(
        branch="positive",
        requirements={
            left_relation: TableConstraint(
                relation=left_relation,
                constraints=[
                    exp.EQ(
                        this=exp.column("id", table="left_user"),
                        expression=left_col,
                    )
                ],
            ),
            right_relation: TableConstraint(
                relation=right_relation,
                constraints=[
                    exp.EQ(
                        this=exp.column("id", table="right_user"),
                        expression=right_col,
                    )
                ],
            ),
        },
    )

    rows = _normalize_pending_rows_for_materialization(
        {
            ("users", "left_user", "left_scope", 0): {"derived_value": 10},
            ("users", "right_user", "right_scope", 0): {"derived_value": 20},
            ("users", "", "left_scope", 0): {"derived_value": 30},
        },
        spec,
        instance,
    )

    assert rows[("users", "left_user", "left_scope", 0)] == {"a": 10}
    assert rows[("users", "right_user", "right_scope", 0)] == {"b": 20}
    assert rows[("users", "", "left_scope", 0)] == {}


def test_materialization_boundary_filters_unknown_non_physical_column():
    instance = Instance(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, reputation INTEGER);",
        name="speculate_materialization_unknown",
        dialect="sqlite",
    )
    relation = table_relation("users", dialect="sqlite")
    spec = BranchSpec(
        branch="positive",
        requirements={relation: TableConstraint(relation=relation)},
    )

    rows = _normalize_pending_rows_for_materialization(
        {("users", "", "", 0): {"not_a_column": 10}},
        spec,
        instance,
    )

    assert rows[("users", "", "", 0)] == {}


def test_group_key_prefill_skips_non_physical_derived_key():
    instance = Instance(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, reputation INTEGER);",
        name="speculate_group_key_physical",
        dialect="sqlite",
    )
    relation = table_relation("users", dialect="sqlite")
    source = physical_column("reputation", relation, dialect="sqlite")
    derived = column_id(
        ColumnKind.AGGREGATE,
        identifier_name("max_reputation", dialect="sqlite"),
        relation,
        source_column_id=source,
    )
    req = TableConstraint(relation=relation, group_key_columns=[derived])
    row = {}

    _pre_fill_group_key_values(
        row,
        RowBinding(relation=relation, row=0),
        req,
        instance,
        {},
    )

    assert row == {}


def test_distinct_prefill_tracks_storage_equivalent_values():
    instance = Instance(
        "CREATE TABLE codes (code TEXT UNIQUE);",
        name="speculate_distinct_storage",
        dialect="sqlite",
    )
    relation = table_relation("codes", dialect="sqlite")
    binding = RowBinding(relation=relation, row=0)
    req = TableConstraint(relation=relation, row_intents={0: {"distinct"}})
    seen = {
        ("codes", "code"): {
            _storage_equivalent_value_key(instance, "codes", "code", 1)
        }
    }
    row = {"code": "1"}

    _pre_fill_plan_distinct_values(row, binding, req, instance, seen)

    assert row["code"] != "1"
    assert _storage_equivalent_value_key(
        instance, "codes", "code", row["code"],
    ) not in {_storage_equivalent_value_key(instance, "codes", "code", 1)}


def test_high_limit_cloning_copies_only_physical_columns():
    instance = Instance(
        "CREATE TABLE users (id INTEGER PRIMARY KEY, reputation INTEGER);",
        name="speculate_clone_physical",
        dialect="sqlite",
    )
    relation = table_relation("users", dialect="sqlite")
    req = TableConstraint(relation=relation, min_rows=2)
    spec = BranchSpec(branch="positive", requirements={relation: req})
    binding = RowBinding(relation=relation, row=0)
    rows = {
        _row_binding_sort_key(binding): {
            "id": 1,
            "reputation": 10,
            "max_reputation": 10,
        }
    }

    cloned = _clone_rows_for_high_limit(
        rows,
        {"users": binding},
        spec,
        instance,
    )

    assert cloned
    assert all(
        set(row) <= set(instance.tables["users"])
        for row in cloned.values()
    )


def test_inequality_prefill_completes_missing_check_partner():
    instance = Instance(
        """
        CREATE TABLE FOLLOW (
          FOLLOWEE TEXT NOT NULL,
          FOLLOWER TEXT NOT NULL,
          CHECK (FOLLOWEE <> FOLLOWER)
        );
        """,
        name="speculate_neq_prefill",
        dialect="sqlite",
    )
    relation = table_relation("follow", dialect="sqlite")
    req = TableConstraint(
        relation=relation,
        constraints=[
            exp.NEQ(
                this=exp.column("followee"),
                expression=exp.column("follower"),
            )
        ],
    )
    row = {"follower": "A"}

    _apply_literal_constraints_to_row(
        row,
        RowBinding(relation=relation, row=0),
        req,
        instance,
    )

    assert row["followee"] != row["follower"]


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

    result = instance.create_rows(
        {
            relation: [
                {customer_id: 38508, date: 1, "Consumption": 994.117864},
            ],
        }
    )

    created = result[relation][0].created[relation][0]
    assert created[date].concrete != 1


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


def test_resolver_global_constraint_includes_mysql_check_for_mentioned_column():
    ddl = """
    CREATE TABLE activity (
      player_id INT,
      games_played INT,
      CHECK (games_played >= 0)
    );
    """
    sql = "SELECT player_id FROM activity WHERE games_played = 1"

    instance = Instance(ddls=ddl, name="speculate_mysql_check", dialect="mysql")
    expr = preprocess_sql(sql, instance, dialect="mysql")
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
    spec = Propagator(plan, instance, "mysql", config=config).propagate()[0]

    constraint, _row_bindings = Resolver(
        plan,
        instance,
        dialect="mysql",
    )._build_global_constraint(spec)

    check_constraints = [
        item
        for item in constraint.constraints
        if isinstance(item, exp.GTE)
        and solver_var(item.this) is not None
        and solver_var(item.this).column_id.name.normalized == "games_played"
    ]
    assert len(check_constraints) == 1
    assert solver_var(check_constraints[0].this).row_scope == "r0"
    assert solver_var(check_constraints[0].this) in constraint.variables


def test_resolver_global_constraint_leaves_unmentioned_check_columns_to_instance():
    ddl = """
    CREATE TABLE activity (
      player_id INT,
      games_played INT,
      CHECK (games_played >= 0)
    );
    """
    sql = "SELECT player_id FROM activity WHERE player_id = 1"

    instance = Instance(ddls=ddl, name="speculate_mysql_unmentioned_check", dialect="mysql")
    expr = preprocess_sql(sql, instance, dialect="mysql")
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
    spec = Propagator(plan, instance, "mysql", config=config).propagate()[0]

    constraint, _row_bindings = Resolver(
        plan,
        instance,
        dialect="mysql",
    )._build_global_constraint(spec)

    check_constraints = [
        item
        for item in constraint.constraints
        if isinstance(item, exp.GTE)
        and isinstance(item.this, exp.Column)
        and item.this.name == "games_played"
    ]
    assert check_constraints == []


def test_resolver_global_constraint_includes_mysql_enum_domain():
    ddl = """
    CREATE TABLE EXPRESSIONS (
        LEFT_OPERAND VARCHAR(10) NOT NULL,
        OPERATOR ENUM('<','>','=') NOT NULL,
        RIGHT_OPERAND VARCHAR(10) NOT NULL,
        PRIMARY KEY (LEFT_OPERAND, OPERATOR, RIGHT_OPERAND)
    );
    """
    sql = "SELECT * FROM EXPRESSIONS WHERE LEFT_OPERAND = 'a'"

    instance = Instance(ddls=ddl, name="speculate_enum_domain", dialect="mysql")
    expr = preprocess_sql(sql, instance, dialect="mysql")
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
    spec = Propagator(plan, instance, "mysql", config=config).propagate()[0]

    constraint, _row_bindings = Resolver(plan, instance, "mysql")._build_global_constraint(spec)

    enum_domains = [
        tuple(item.this for item in in_expr.expressions)
        for in_expr in constraint.constraints
        if isinstance(in_expr, exp.In)
        and isinstance(in_expr.this, exp.Column)
        and solver_var(in_expr.this) is not None
        and solver_var(in_expr.this).column_id.name.normalized == "OPERATOR"
    ]
    assert enum_domains == [("<", ">", "=")]


def test_rewrite_constraint_scopes_correlated_column_to_existing_relation_row():
    instance = Instance(
        "CREATE TABLE pairings (id INTEGER PRIMARY KEY, left_value INTEGER, right_value INTEGER);",
        name="speculate_correlated_row_scope",
        dialect="sqlite",
    )
    left_relation = relation_id(
        RelationKind.TABLE,
        identifier_name("pairings", dialect="sqlite"),
        alias=identifier_name("left_pairing", dialect="sqlite"),
        scope_id="left_scope",
    )
    right_relation = relation_id(
        RelationKind.TABLE,
        identifier_name("pairings", dialect="sqlite"),
        alias=identifier_name("right_pairing", dialect="sqlite"),
        scope_id="right_scope",
    )
    left_column_id = physical_column("left_value", left_relation, dialect="sqlite")
    right_column_id = physical_column("right_value", right_relation, dialect="sqlite")
    left_column = exp.column("left_value", table="left_pairing")
    right_column = exp.column("right_value", table="right_pairing")
    set_solver_var(
        left_column,
        SolverVar(column_id=left_column_id, relation_id=left_relation),
    )
    set_solver_var(
        right_column,
        SolverVar(column_id=right_column_id, relation_id=right_relation),
    )
    constraint = exp.EQ(this=left_column, expression=right_column)
    left_row_1 = RowBinding(relation=left_relation, row=1)
    row_bindings = {
        _row_binding_sort_key(RowBinding(relation=left_relation, row=0)): RowBinding(
            relation=left_relation,
            row=0,
        ),
        _row_binding_sort_key(left_row_1): left_row_1,
        _row_binding_sort_key(RowBinding(relation=right_relation, row=0)): RowBinding(
            relation=right_relation,
            row=0,
        ),
    }

    rewritten = _rewrite_constraint_for_binding(
        constraint,
        left_row_1,
        instance,
        row_bindings=row_bindings,
    )

    assert rewritten is not None
    scopes = {
        solver_var(col).relation_id.alias.normalized: solver_var(col).row_scope
        for col in rewritten.find_all(exp.Column)
        if solver_var(col) is not None
    }
    assert scopes == {"left_pairing": "r1", "right_pairing": "r0"}


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


def test_aggregate_case_operands_create_non_neutral_semantic_contrast_spec():
    ddl = """
    CREATE TABLE customers (
      CustomerID INTEGER PRIMARY KEY,
      Segment TEXT,
      Currency TEXT
    );
    """
    sql = """
    SELECT SUM(Currency = 'CZK') - SUM(Currency = 'EUR')
    FROM customers
    WHERE Segment = 'SME'
    """
    instance = Instance(ddls=ddl, name="speculate_case_contrast", dialect="sqlite")
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

    specs = Propagator(plan, instance, "sqlite", config=config).propagate()

    contrast = next(
        spec for spec in specs
        if spec.branch == "semantic_case_contrast_0"
    )
    assert contrast.has_goal("value")
    constraints = [
        constraint.sql(dialect="sqlite")
        for req in contrast.requirements.values()
        for constraint in req.constraints
    ]
    assert '"customers"."currency" = \'CZK\'' in constraints
    assert '"customers"."currency" <> \'EUR\'' in constraints


def test_aggregate_case_literal_mismatch_disproves_from_single_query_generation(tmp_path):
    ddl = """
    CREATE TABLE customers (
      CustomerID INTEGER PRIMARY KEY,
      Segment TEXT,
      Currency TEXT
    );
    """
    gold_sql = """
    SELECT SUM(Currency = 'CZK') - SUM(Currency = 'EUR')
    FROM customers
    WHERE Segment = 'SME'
    """
    pred_sql = """
    SELECT
      (SELECT COUNT(DISTINCT CustomerID)
       FROM customers
       WHERE Segment = 'SME' AND Currency = 'Czech koruna')
      -
      (SELECT COUNT(DISTINCT CustomerID)
       FROM customers
       WHERE Segment = 'SME' AND Currency = 'Euro')
    """

    result = disprove(
        gold_sql,
        pred_sql,
        ddl,
        f"sqlite:///{tmp_path / 'case_literal_mismatch.sqlite'}",
        "sqlite",
        semantics="bag",
        max_iterations=5,
        atom_null=1,
        atom_false=1,
        atom_dup=3,
        project_null=1,
        distinct_duplicate=1,
        distinct_unique=1,
    )

    assert result.verdict.value == "neq"
    assert result.q1_result.rows != result.q2_result.rows


def test_aggregate_case_count_semantic_witness_keeps_count_argument_non_null():
    ddl = """
    CREATE TABLE molecule (
      molecule_id TEXT PRIMARY KEY,
      label TEXT
    );
    CREATE TABLE bond (
      bond_id TEXT PRIMARY KEY,
      molecule_id TEXT,
      bond_type TEXT,
      FOREIGN KEY (molecule_id) REFERENCES molecule(molecule_id)
    );
    """
    sql = """
    SELECT CAST(COUNT(CASE WHEN T.bond_type = '=' THEN T.bond_id ELSE NULL END) AS REAL)
           * 100 / COUNT(T.bond_id)
    FROM bond AS T
    WHERE T.molecule_id = 'TR047'
    """
    instance = Instance(ddls=ddl, name="speculate_case_count", dialect="sqlite")
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

    specs = Propagator(plan, instance, "sqlite", config=config).propagate()

    contrast = next(
        spec for spec in specs
        if spec.branch == "semantic_case_contrast_0"
    )
    constraints = [
        constraint.sql(dialect="sqlite")
        for req in contrast.requirements.values()
        for constraint in req.constraints
    ]
    assert '"t"."bond_type" = \'=\'' in constraints
    assert any(
        "bond_id" in constraint and "IS NOT NULL" in constraint
        for constraint in constraints
    )
    assert not any(
        "bond_id" in constraint and "IS NULL" in constraint
        for constraint in constraints
    )


def test_aggregate_case_count_literal_mismatch_disproves_from_single_query_generation(tmp_path):
    ddl = """
    CREATE TABLE molecule (
      molecule_id TEXT PRIMARY KEY,
      label TEXT
    );
    CREATE TABLE bond (
      bond_id TEXT PRIMARY KEY,
      molecule_id TEXT,
      bond_type TEXT,
      FOREIGN KEY (molecule_id) REFERENCES molecule(molecule_id)
    );
    """
    gold_sql = """
    SELECT CAST(COUNT(CASE WHEN T.bond_type = '=' THEN T.bond_id ELSE NULL END) AS REAL)
           * 100 / COUNT(T.bond_id)
    FROM bond AS T
    WHERE T.molecule_id = 'TR047'
    """
    pred_sql = """
    SELECT CAST(SUM(CASE WHEN T2.bond_type = ' = ' THEN 1 ELSE 0 END) AS REAL)
           * 100 / COUNT(T2.bond_id)
    FROM molecule AS T1
    INNER JOIN bond AS T2 ON T1.molecule_id = T2.molecule_id
    WHERE T1.molecule_id = 'TR047'
    """

    result = disprove(
        gold_sql,
        pred_sql,
        ddl,
        f"sqlite:///{tmp_path / 'case_count_literal_mismatch.sqlite'}",
        "sqlite",
        semantics="bag",
        max_iterations=5,
        atom_null=1,
        atom_false=1,
        atom_dup=3,
        project_null=1,
        distinct_duplicate=1,
        distinct_unique=1,
    )

    assert result.verdict.value == "neq"


def test_non_distinct_project_creates_semantic_duplicate_spec():
    ddl = """
    CREATE TABLE superhero (
      id INTEGER PRIMARY KEY,
      height_cm INTEGER
    );
    CREATE TABLE hero_power (
      hero_id INTEGER,
      power_id INTEGER
    );
    CREATE TABLE superpower (
      id INTEGER PRIMARY KEY,
      power_name TEXT
    );
    """
    sql = """
    SELECT T3.power_name
    FROM superhero AS T1
    INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id
    INNER JOIN superpower AS T3 ON T2.power_id = T3.id
    WHERE T1.height_cm > 10
    """
    instance = Instance(ddls=ddl, name="speculate_project_duplicate", dialect="sqlite")
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

    specs = Propagator(plan, instance, "sqlite", config=config).propagate()

    duplicate = next(
        spec for spec in specs
        if spec.branch == "semantic_project_duplicate_0"
    )
    assert duplicate.has_goal("value")
    assert duplicate.has_goal("duplicate")
    assert all(req.min_rows >= 2 for req in duplicate.requirements.values())
    superpower_req = next(
        req for req in duplicate.requirements.values()
        if req.table == "superpower"
    )
    assert {
        column.name.normalized for column in superpower_req.duplicate_columns
    } == {"power_name"}


def test_duplicate_project_constraints_keep_reference_rows_distinct():
    ddl = """
    CREATE TABLE superhero (
      id INTEGER PRIMARY KEY,
      height_cm INTEGER
    );
    CREATE TABLE hero_power (
      hero_id INTEGER,
      power_id INTEGER,
      FOREIGN KEY (hero_id) REFERENCES superhero(id),
      FOREIGN KEY (power_id) REFERENCES superpower(id)
    );
    CREATE TABLE superpower (
      id INTEGER PRIMARY KEY,
      power_name TEXT
    );
    """
    sql = """
    SELECT T3.power_name
    FROM superhero AS T1
    INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id
    INNER JOIN superpower AS T3 ON T2.power_id = T3.id
    WHERE T1.height_cm > 10
    """
    instance = Instance(ddls=ddl, name="speculate_distinct_duplicate_rows", dialect="sqlite")
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
    spec = next(
        spec for spec in Propagator(plan, instance, "sqlite", config=config).propagate()
        if spec.branch == "semantic_project_duplicate_0"
    )

    constraint, _row_bindings = Resolver(plan, instance)._build_global_constraint(spec)

    identity_inequalities = [
        item for item in constraint.constraints
        if isinstance(item, exp.NEQ)
        and any(col.name == "id" for col in item.find_all(exp.Column))
    ]
    assert identity_inequalities
    assert any(
        {solver_var(col).row_scope for col in item.find_all(exp.Column)}
        == {"r0", "r1"}
        for item in identity_inequalities
    )


def test_duplicate_project_bridge_keys_follow_distinct_reference_rows():
    ddl = """
    CREATE TABLE superhero (
      id INTEGER PRIMARY KEY,
      height_cm INTEGER
    );
    CREATE TABLE hero_power (
      hero_id INTEGER,
      power_id INTEGER,
      FOREIGN KEY (hero_id) REFERENCES superhero(id),
      FOREIGN KEY (power_id) REFERENCES superpower(id)
    );
    CREATE TABLE superpower (
      id INTEGER PRIMARY KEY,
      power_name TEXT
    );
    """
    sql = """
    SELECT T3.power_name
    FROM superhero AS T1
    INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id
    INNER JOIN superpower AS T3 ON T2.power_id = T3.id
    WHERE T1.height_cm > 10
    """
    instance = Instance(ddls=ddl, name="speculate_bridge_duplicate_rows", dialect="sqlite")
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

    rows = speculate(plan, instance, dialect="sqlite", config=config)

    duplicate_rows = next(
        branch_rows for branch, branch_rows in rows
        if branch == "semantic_project_duplicate_0"
    )
    superhero_rows = duplicate_rows["superhero"]
    superhero_ids = {row["id"] for row in superhero_rows}
    superpower_ids = {
        row["id"] for row in duplicate_rows["superpower"]
    }
    hero_links = {
        (row["hero_id"], row["power_id"])
        for row in duplicate_rows["hero_power"]
    }
    assert all(row["height_cm"] > 10 for row in superhero_rows)
    assert len(superhero_ids) >= 2
    assert len(superpower_ids) >= 2
    assert {hero_id for hero_id, _power_id in hero_links} <= superhero_ids
    assert {power_id for _hero_id, power_id in hero_links} <= superpower_ids


def test_duplicate_project_with_average_filter_binds_filter_column():
    ddl = """
    CREATE TABLE superhero (
      id INTEGER PRIMARY KEY,
      height_cm INTEGER
    );
    CREATE TABLE hero_power (
      hero_id INTEGER,
      power_id INTEGER
    );
    CREATE TABLE superpower (
      id INTEGER PRIMARY KEY,
      power_name TEXT
    );
    """
    sql = """
    SELECT T3.power_name
    FROM superhero AS T1
    INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id
    INNER JOIN superpower AS T3 ON T2.power_id = T3.id
    WHERE T1.height_cm * 100 > (SELECT AVG(height_cm) FROM superhero) * 80
    """
    instance = Instance(ddls=ddl, name="speculate_filtered_duplicate", dialect="sqlite")
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

    specs = Propagator(plan, instance, "sqlite", config=config).propagate()

    duplicate = next(
        spec for spec in specs
        if spec.branch == "semantic_project_duplicate_0"
    )
    superhero_reqs = [
        req for req in duplicate.requirements.values()
        if req.table == "superhero"
    ]
    assert len(superhero_reqs) >= 2
    superhero_req = next(req for req in superhero_reqs if req.alias == "t1")
    avg_req = next(req for req in superhero_reqs if req.alias is None)
    assert {
        column.name.normalized for column in superhero_req.duplicate_columns
    } == {"height_cm"}
    assert {
        column.name.normalized for column in avg_req.duplicate_columns
    } == {"height_cm"}
    constraints = [
        constraint.sql(dialect="sqlite")
        for constraint in superhero_req.constraints
    ]
    assert any(
        "height_cm" in constraint and "> 0" in constraint
        for constraint in constraints
    )


def test_non_distinct_vs_distinct_project_disproves_from_single_query_generation(tmp_path):
    ddl = """
    CREATE TABLE superhero (
      id INTEGER PRIMARY KEY,
      height_cm INTEGER
    );
    CREATE TABLE hero_power (
      hero_id INTEGER,
      power_id INTEGER
    );
    CREATE TABLE superpower (
      id INTEGER PRIMARY KEY,
      power_name TEXT
    );
    """
    from_sql = """
    FROM superhero AS T1
    INNER JOIN hero_power AS T2 ON T1.id = T2.hero_id
    INNER JOIN superpower AS T3 ON T2.power_id = T3.id
    WHERE T1.height_cm > 10
    """
    gold_sql = "SELECT T3.power_name " + from_sql
    pred_sql = "SELECT DISTINCT T3.power_name " + from_sql

    result = disprove(
        gold_sql,
        pred_sql,
        ddl,
        f"sqlite:///{tmp_path / 'project_duplicate.sqlite'}",
        "sqlite",
        semantics="bag",
        max_iterations=5,
        atom_null=1,
        atom_false=1,
        atom_dup=3,
        project_null=1,
        distinct_duplicate=1,
        distinct_unique=1,
    )

    assert result.verdict.value == "neq"


def test_inner_join_semantic_generation_includes_antimatch_and_fanout_rows():
    ddl = """
    CREATE TABLE customers (
      id INTEGER PRIMARY KEY,
      name TEXT
    );
    CREATE TABLE orders (
      id INTEGER PRIMARY KEY,
      customer_id INTEGER,
      FOREIGN KEY (customer_id) REFERENCES customers(id)
    );
    """
    sql = """
    SELECT o.id, c.name
    FROM orders AS o
    JOIN customers AS c ON o.customer_id = c.id
    """
    instance = Instance(ddls=ddl, name="semantic_join_rows", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    results = speculate(
        plan,
        instance,
        dialect="sqlite",
        config=SpeculateConfig(
            positive=0,
            negative=0,
            null=0,
            left_unmatched=0,
            right_unmatched=0,
            having_fail=0,
            case_else=0,
            boundary=0,
            join_antimatch=1,
            join_fanout=1,
            aggregate_contrast=0,
            rank_contrast=0,
        ),
    )

    by_branch = dict(results)
    antimatch = by_branch["semantic_join_antimatch_0"]
    assert antimatch["orders"][0]["customer_id"] != antimatch["customers"][0]["id"]

    fanout = by_branch["semantic_join_fanout_0"]
    fanout_customer_ids = {row["customer_id"] for row in fanout["orders"]}
    assert len(fanout["orders"]) >= 2
    assert len(fanout_customer_ids) == 1
    assert fanout_customer_ids == {fanout["customers"][0]["id"]}


def test_order_by_limit_semantic_generation_produces_winner_and_challenger():
    ddl = """
    CREATE TABLE scores (
      id INTEGER PRIMARY KEY,
      score INTEGER
    );
    """
    sql = "SELECT s.id FROM scores AS s ORDER BY s.score DESC LIMIT 1"
    instance = Instance(ddls=ddl, name="semantic_rank_rows", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    results = speculate(
        plan,
        instance,
        dialect="sqlite",
        config=SpeculateConfig(
            positive=0,
            negative=0,
            null=0,
            left_unmatched=0,
            right_unmatched=0,
            having_fail=0,
            case_else=0,
            boundary=0,
            join_antimatch=0,
            join_fanout=0,
            aggregate_contrast=0,
            rank_contrast=1,
        ),
    )

    rows = dict(results)["semantic_rank_contrast_0"]["scores"]

    assert len(rows) >= 2
    assert rows[0]["score"] > rows[1]["score"]


def test_order_by_asc_limit_semantic_generation_produces_winner_and_challenger():
    ddl = """
    CREATE TABLE scores (
      id INTEGER PRIMARY KEY,
      score INTEGER
    );
    """
    sql = "SELECT s.id FROM scores AS s ORDER BY s.score ASC LIMIT 1"
    instance = Instance(ddls=ddl, name="semantic_rank_rows_asc", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    results = speculate(
        plan,
        instance,
        dialect="sqlite",
        config=SpeculateConfig(
            positive=0,
            negative=0,
            null=0,
            left_unmatched=0,
            right_unmatched=0,
            having_fail=0,
            case_else=0,
            boundary=0,
            join_antimatch=0,
            join_fanout=0,
            aggregate_contrast=0,
            rank_contrast=1,
        ),
    )

    rows = dict(results)["semantic_rank_contrast_0"]["scores"]

    assert len(rows) >= 2
    assert rows[0]["score"] < rows[1]["score"]


def test_grouped_count_order_by_limit_semantic_generation_creates_count_contrast():
    ddl = """
    CREATE TABLE orders (
      id INTEGER PRIMARY KEY,
      customer_id INTEGER
    );
    """
    sql = """
    SELECT o.customer_id, COUNT(*) AS cnt
    FROM orders AS o
    GROUP BY o.customer_id
    ORDER BY cnt DESC
    LIMIT 1
    """
    instance = Instance(ddls=ddl, name="semantic_group_count_rows", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    results = speculate(
        plan,
        instance,
        dialect="sqlite",
        config=SpeculateConfig(
            positive=0,
            negative=0,
            null=0,
            left_unmatched=0,
            right_unmatched=0,
            having_fail=0,
            case_else=0,
            boundary=0,
            join_antimatch=0,
            join_fanout=0,
            aggregate_contrast=1,
            rank_contrast=0,
        ),
    )

    rows = dict(results)["semantic_aggregate_contrast_0"]["orders"]
    counts: dict[int, int] = {}
    for row in rows:
        counts[row["customer_id"]] = counts.get(row["customer_id"], 0) + 1

    assert sorted(counts.values(), reverse=True)[:2] == [2, 1]


def test_grouped_sum_order_by_limit_semantic_generation_creates_sum_contrast():
    ddl = """
    CREATE TABLE orders (
      id INTEGER PRIMARY KEY,
      customer_id INTEGER,
      amount INTEGER
    );
    """
    sql = """
    SELECT o.customer_id, SUM(o.amount) AS total
    FROM orders AS o
    GROUP BY o.customer_id
    ORDER BY total DESC
    LIMIT 1
    """
    instance = Instance(ddls=ddl, name="semantic_group_sum_rows", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    plan = Plan(expr, instance)

    results = speculate(
        plan,
        instance,
        dialect="sqlite",
        config=SpeculateConfig(
            positive=0,
            negative=0,
            null=0,
            left_unmatched=0,
            right_unmatched=0,
            having_fail=0,
            case_else=0,
            boundary=0,
            join_antimatch=0,
            join_fanout=0,
            aggregate_contrast=1,
            rank_contrast=0,
        ),
    )

    rows = dict(results)["semantic_aggregate_contrast_0"]["orders"]
    sums: dict[int, int] = {}
    for row in rows:
        sums[row["customer_id"]] = sums.get(row["customer_id"], 0) + row["amount"]

    assert len(sums) >= 2
    assert len(set(sums.values())) >= 2


def test_bird_case_57_constraint_build_indexes_only_relevant_columns(monkeypatch):
    import parseval.symbolic.speculate as speculate_module

    plan, instance = _bird_plan(57)
    config = SpeculateConfig(
        positive=1,
        negative=0,
        null=0,
        left_unmatched=0,
        right_unmatched=0,
        having_fail=0,
        case_else=0,
        boundary=0,
        join_antimatch=0,
        join_fanout=0,
        aggregate_contrast=0,
        rank_contrast=0,
        project_duplicate=0,
    )
    spec = next(
        spec
        for spec in Propagator(plan, instance, "sqlite", config=config).propagate()
        if spec.branch == "positive"
    )

    if hasattr(speculate_module, "_path_variable_for_check_column"):
        def fail_path_scan(*_args, **_kwargs):
            raise AssertionError("database constraint lowering used path scan")

        monkeypatch.setattr(
            speculate_module,
            "_path_variable_for_check_column",
            fail_path_scan,
        )

    constraint, row_bindings = Resolver(plan, instance)._build_global_constraint(spec)

    row_counts: dict[str, int] = {}
    for binding in row_bindings.values():
        row_counts[binding.table] = row_counts.get(binding.table, 0) + 1
    assert row_counts["satscores"] == 333
    assert row_counts["schools"] == 333

    indexed_columns = {
        (
            variable.relation_id.name.normalized,
            variable.column_id.name.normalized,
        )
        for variable in constraint.variables
    }
    assert ("schools", "statustype") not in indexed_columns
    assert ("schools", "doc") not in indexed_columns
    assert ("schools", "doctype") not in indexed_columns
    assert ("satscores", "rtype") not in indexed_columns
    assert ("satscores", "enroll12") not in indexed_columns
