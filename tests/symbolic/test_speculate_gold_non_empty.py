"""Positive-witness tests for speculate gold non-empty mode."""

from __future__ import annotations

from sqlglot import exp

from parseval.instance import Instance


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
