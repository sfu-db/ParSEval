"""Positive-witness tests for speculate gold non-empty mode."""

from __future__ import annotations

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
