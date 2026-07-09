from __future__ import annotations

from decimal import Decimal
import sqlite3

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.engine import SymbolicEngine
from parseval.symbolic.types import BranchType, CoverageThresholds
from sqlglot import exp


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


def test_customers_not_in_generates_outer_row_without_matching_order():
    schema = """
    CREATE TABLE customers (id INT PRIMARY KEY, name TEXT);
    CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT);
    """
    sql = """
    SELECT name
    FROM customers
    WHERE id NOT IN (SELECT customer_id FROM orders)
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=8,
        max_rows_per_table=8,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert _execute_generated(schema, instance, sql)
    customer_id_col = instance.column_id("customers", exp.to_identifier("id"))
    order_customer_col = instance.column_id("orders", exp.to_identifier("customer_id"))
    customer_ids = {
        row[customer_id_col].concrete for row in instance.get_rows("customers")
    }
    order_customer_ids = {
        row[order_customer_col].concrete
        for row in instance.get_rows("orders")
        if row[order_customer_col].concrete is not None
    }
    assert customer_ids
    assert customer_ids - order_customer_ids


def test_not_exists_generates_outer_row_and_keeps_target_deferred_not_infeasible():
    schema = """
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
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    result = SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=8,
        max_rows_per_table=8,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert _execute_generated(schema, instance, sql)
    assert all(
        not node.is_infeasible(0, BranchType.ATOM_TRUE)
        for node in result.tree.nodes
        if node.site == "root_result"
    )


def test_count_zero_anti_subquery_generates_surviving_salesperson_row():
    schema = """
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
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert _execute_generated(schema, instance, sql)


def test_generic_label_not_in_generates_alpha_without_beta_buyer():
    schema = """
    CREATE TABLE purchases (id INT PRIMARY KEY, buyer TEXT, label TEXT);
    """
    sql = """
    SELECT buyer
    FROM purchases
    WHERE label = 'alpha'
      AND buyer NOT IN (
        SELECT buyer FROM purchases WHERE label = 'beta'
      )
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=8,
        max_rows_per_table=8,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    output_rows = _execute_generated(schema, instance, sql)
    assert output_rows
    beta_buyers = {
        row[0]
        for row in _execute_generated(
            schema,
            instance,
            "SELECT buyer FROM purchases WHERE label = 'beta'",
        )
    }
    assert any(row[0] not in beta_buyers for row in output_rows)


def test_duplicate_sum_case_having_aliases_generate_non_empty_group():
    schema = """
    CREATE TABLE purchases (id INT PRIMARY KEY, buyer TEXT, label TEXT);
    """
    sql = """
    SELECT buyer,
           SUM(CASE WHEN label = 'alpha' THEN 1 ELSE 0 END) AS sum,
           SUM(CASE WHEN label = 'beta' THEN 1 ELSE 0 END) AS sum
    FROM purchases
    GROUP BY buyer
    HAVING SUM(CASE WHEN label = 'alpha' THEN 1 ELSE 0 END) > 0
       AND SUM(CASE WHEN label = 'beta' THEN 1 ELSE 0 END) = 0
    """
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert _execute_generated(schema, instance, sql)


def test_scalar_subquery_or_generates_root_output_row():
    schema = """
    CREATE TABLE cinema (seat_id INT PRIMARY KEY, free INT);
    """
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
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=10,
        max_rows_per_table=10,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)

    assert _execute_generated(schema, instance, sql)


def test_speculation_budget_rescue_allows_one_root_witness(monkeypatch):
    schema = "CREATE TABLE t (id INT PRIMARY KEY, flag INT);"
    sql = "SELECT id FROM t WHERE flag = 1"
    instance = Instance(ddls=schema, name="test", dialect="sqlite")

    def seed_non_surviving_row(*args, **kwargs):
        instance.create_row("t", values={"id": 1, "flag": 0})

    monkeypatch.setattr(
        "parseval.symbolic.speculate.speculate",
        seed_non_surviving_row,
    )

    SymbolicEngine(
        instance,
        sql,
        dialect="sqlite",
        max_iterations=5,
        max_rows_per_table=1,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=True)

    output_rows = _execute_generated(schema, instance, sql)

    assert output_rows
    assert (1,) not in output_rows
