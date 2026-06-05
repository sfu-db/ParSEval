import sqlglot

from parseval.identity import PARSEVAL_COLUMN_ID
from parseval.instance import Instance


def test_create_row_stores_cells_by_column_id():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);", name="db", dialect="sqlite")
    result = inst.create_row("users", {"id": 1, "name": "Ada"})
    row = result.created["users"][0]
    id_col = inst.column_id("users", "id")

    assert row[id_col].concrete == 1
    assert row["id"].concrete == 1


def test_exp_column_lookup_uses_resolved_column_id():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    row = inst.create_row("users", {"id": 7}).created["users"][0]
    col = sqlglot.parse_one("SELECT id FROM users").expressions[0]
    col.meta[PARSEVAL_COLUMN_ID] = inst.column_id("users", "id")

    assert row[col].concrete == 7


def test_variable_carries_relation_and_column_ids():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    row = inst.create_row("users", {"id": 1}).created["users"][0]
    column_id = inst.column_id("users", "id")
    var = row[column_id]

    assert var.args["relation_id"] == inst.table_id("users")
    assert var.args["column_id"] == column_id


def test_symbol_index_lookup_by_identity():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    inst.create_row("users", {"id": 1})
    cells = inst.symbols.by_column(inst.column_id("users", "id"))

    assert len(cells) == 1
    assert cells[0].concrete == 1
