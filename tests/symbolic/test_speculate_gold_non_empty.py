"""Positive-witness tests for speculate gold non-empty mode."""

from __future__ import annotations

import sqlite3

from sqlglot import exp

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.speculate import speculate


def _plan(sql: str, schema: str) -> tuple[Instance, Plan]:
    instance = Instance(ddls=schema, name="gold_non_empty", dialect="sqlite")
    expr = preprocess_sql(sql, instance, dialect="sqlite")
    return instance, Plan(expr, instance)


def _execute_candidate_rows(
    instance: Instance,
    sql: str,
    rows_per_table: dict[str, list[dict[str, object]]],
) -> list[tuple]:
    conn = sqlite3.connect(":memory:")
    try:
        for ddl in instance.ddls.split(";"):
            ddl = ddl.strip()
            if ddl:
                conn.execute(ddl)

        for table_name in instance.tables:
            existing_rows = instance.get_rows(table_name)
            candidate_rows = rows_per_table.get(table_name, [])
            cols = list(instance.tables[table_name].keys())
            if not existing_rows and not candidate_rows:
                continue
            placeholders = ",".join(["?"] * len(cols))
            quoted_cols = ",".join(f'"{col}"' for col in cols)
            stmt = f'INSERT INTO "{table_name}" ({quoted_cols}) VALUES ({placeholders})'

            for row in existing_rows:
                values = []
                for col in cols:
                    value = row[col].concrete if col in row.columns else None
                    if value is not None and not isinstance(value, (int, float, str, bytes)):
                        value = str(value)
                    values.append(value)
                conn.execute(stmt, values)

            for row in candidate_rows:
                values = []
                for col in cols:
                    value = row.get(col)
                    if value is not None and not isinstance(value, (int, float, str, bytes)):
                        value = str(value)
                    values.append(value)
                conn.execute(stmt, values)

        conn.commit()
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def _gold_non_empty_results(schema: str, sql: str):
    instance, plan = _plan(sql, schema)
    return instance, plan, speculate(
        plan,
        instance,
        plan.alias_map,
        dialect="sqlite",
        objective="gold_non_empty",
    )


def test_row_scoped_solver_key_includes_table_alias_and_row():
    from parseval.symbolic.speculate import RowBinding, _solver_table_key

    binding = RowBinding(table="orders", alias="o", row=2)

    assert _solver_table_key(binding) == "orders__o__r2"


def test_rows_from_flat_solver_assignments_decodes_physical_rows():
    from parseval.symbolic.speculate import RowBinding, _rows_from_solver_assignments

    schema = "CREATE TABLE orders (id INT PRIMARY KEY, total INT);"
    instance = Instance(ddls=schema, name="decode_rows", dialect="sqlite")
    bindings = {
        "orders__o__r0": RowBinding(table="orders", alias="o", row=0),
        "orders__o__r1": RowBinding(table="orders", alias="o", row=1),
    }
    assignments = {
        "orders__o__r0.id": 1,
        "orders__o__r0.total": 125,
        "orders__o__r1.id": 2,
        "orders__o__r1.total": 140,
    }

    rows = _rows_from_solver_assignments(assignments, bindings, instance)

    assert rows == {
        "orders": [{"id": 1, "total": 125}, {"id": 2, "total": 140}]
    }


def test_rewrite_expr_for_row_scope_preserves_column_type():
    from parseval.symbolic.speculate import RowBinding, _rewrite_expr_for_row_scope

    col = exp.column("total", "o")
    col.type = exp.DataType.build("INT")
    expr = exp.GT(this=col, expression=exp.Literal.number(100))
    bindings = {
        "orders__o__r0": RowBinding(table="orders", alias="o", row=0),
    }

    rewritten = _rewrite_expr_for_row_scope(expr, bindings, {"o": "orders"})
    rewritten_col = next(rewritten.find_all(exp.Column))

    assert rewritten_col.table == "orders__o__r0"
    assert rewritten_col.name == "total"
    assert rewritten_col.type is not None


def test_gold_non_empty_materializes_single_table_filter_through_instance():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
    sql = "SELECT id FROM t WHERE val > 5"
    instance, plan = _plan(sql, schema)

    results = speculate(
        plan,
        instance,
        plan.alias_map,
        dialect="sqlite",
        objective="gold_non_empty",
    )

    assert results
    assert results[0][0] == "positive"
    assert instance.get_rows("t")
    assert _execute_candidate_rows(instance, sql, {})


def test_gold_non_empty_objective_returns_only_positive_rows_for_simple_filter():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
    sql = "SELECT id FROM t WHERE val > 5"
    instance, plan = _plan(sql, schema)

    results = speculate(
        plan,
        instance,
        plan.alias_map,
        dialect="sqlite",
        objective="gold_non_empty",
    )

    assert results
    assert [branch for branch, _rows in results] == ["positive"]
    assert _execute_candidate_rows(instance, sql, {})


def test_gold_non_empty_row_scoped_inner_join_uses_one_solver_batch():
    schema = (
        "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);"
        "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT, val INT);"
    )
    sql = (
        "SELECT parent.name "
        "FROM parent JOIN child ON parent.id = child.parent_id "
        "WHERE child.val > 5"
    )
    instance, plan = _plan(sql, schema)

    results = speculate(
        plan,
        instance,
        plan.alias_map,
        dialect="sqlite",
        objective="gold_non_empty",
    )

    assert results
    assert _execute_candidate_rows(instance, sql, {})


def test_gold_non_empty_row_scoped_self_join_keeps_alias_rows_distinct():
    schema = "CREATE TABLE people (id INT PRIMARY KEY, manager_id INT, name TEXT);"
    sql = (
        "SELECT e.name "
        "FROM people e JOIN people m ON e.manager_id = m.id "
        "WHERE e.name = 'Alice' AND m.name = 'Bob'"
    )
    instance, plan = _plan(sql, schema)

    results = speculate(
        plan,
        instance,
        plan.alias_map,
        dialect="sqlite",
        objective="gold_non_empty",
    )

    assert results
    assert len(instance.get_rows("people")) >= 2
    assert _execute_candidate_rows(instance, sql, {})


def test_gold_non_empty_grouped_count_star_returns_witness_rows():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, a INT);"
    sql = "SELECT a, COUNT(*) FROM t GROUP BY a"

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert results
    rows = _execute_candidate_rows(instance, sql, results[0][1])
    assert rows


def test_gold_non_empty_grouped_count_star_having_returns_witness_rows():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, a INT);"
    sql = "SELECT a, COUNT(*) FROM t GROUP BY a HAVING COUNT(*) > 1"

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert results
    assert len(results[0][1]["t"]) >= 2
    rows = _execute_candidate_rows(instance, sql, results[0][1])
    assert rows


def test_gold_non_empty_having_hidden_group_key_predicate_returns_witness_rows():
    schema = "CREATE TABLE t (id INT PRIMARY KEY, a INT);"
    sql = "SELECT a, COUNT(*) FROM t GROUP BY a HAVING COUNT(*) > 1 AND a > 5"

    instance, _plan_obj, results = _gold_non_empty_results(schema, sql)

    assert results
    rows_per_table = results[0][1]
    assert len(rows_per_table["t"]) >= 2
    assert all(row["a"] > 5 for row in rows_per_table["t"])
    rows = _execute_candidate_rows(instance, sql, rows_per_table)
    assert rows
