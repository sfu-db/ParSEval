"""Tests for :class:`parseval.instance.symbols.SymbolIndex`."""

from __future__ import annotations

import unittest

from parseval.identity import (
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.instance.symbols import SymbolIndex
from parseval.plan.rex import Variable


REL_T = relation_id(RelationKind.TABLE, identifier_name("t"))
COL_X = column_id(ColumnKind.PHYSICAL, identifier_name("x"), REL_T)
COL_Y = column_id(ColumnKind.PHYSICAL, identifier_name("y"), REL_T)
COL_Z = column_id(ColumnKind.PHYSICAL, identifier_name("z"), REL_T)


def _var(name: str, *, column=COL_X, rowid="row_0") -> Variable:
    return Variable(this=name, column_id=column, rowid=rowid)


class TestRegister(unittest.TestCase):
    def test_register_stores_under_name(self):
        index = SymbolIndex()
        v = _var("x1")
        index.register(v)
        self.assertEqual(index.by_name("x1"), v)

    def test_register_idempotent_for_same_variable(self):
        index = SymbolIndex()
        v = _var("x1")
        index.register(v)
        index.register(v)
        self.assertEqual(len(index.by_column(COL_X)), 1)
        self.assertEqual(len(index.by_row(REL_T, "row_0")), 1)

    def test_register_replaces_existing_name(self):
        """Registering a new Variable under the same name swaps cleanly."""
        index = SymbolIndex()
        v_old = _var("x1", rowid="row_0")
        v_new = _var("x1", rowid="row_1")
        index.register(v_old)
        index.register(v_new)
        self.assertIs(index.by_name("x1"), v_new)
        # Old should be gone from reverse indices.
        self.assertEqual(index.by_row(REL_T, "row_0"), [])
        self.assertEqual(index.by_row(REL_T, "row_1"), [v_new])

    def test_register_rejects_string_only_back_pointers(self):
        index = SymbolIndex()
        with self.assertRaises(ValueError):
            Variable(this="x1", table="t", column="x", rowid="row_0")


class TestLookup(unittest.TestCase):
    def test_by_column_returns_all_rows_for_a_column(self):
        index = SymbolIndex()
        a_r0 = _var("t_x_0", column=COL_X, rowid="row_0")
        a_r1 = _var("t_x_1", column=COL_X, rowid="row_1")
        b_r0 = _var("t_y_0", column=COL_Y, rowid="row_0")
        for v in (a_r0, a_r1, b_r0):
            index.register(v)
        self.assertEqual(index.by_column(COL_X), [a_r0, a_r1])
        self.assertEqual(index.by_column(COL_Y), [b_r0])
        self.assertEqual(index.by_column(COL_Z), [])

    def test_by_row_returns_all_columns_for_a_row(self):
        index = SymbolIndex()
        x_r0 = _var("t_x_0", column=COL_X, rowid="row_0")
        y_r0 = _var("t_y_0", column=COL_Y, rowid="row_0")
        x_r1 = _var("t_x_1", column=COL_X, rowid="row_1")
        for v in (x_r0, y_r0, x_r1):
            index.register(v)
        self.assertEqual(index.by_row(REL_T, "row_0"), [x_r0, y_r0])
        self.assertEqual(index.by_row(REL_T, "row_1"), [x_r1])

    def test_by_name_miss_returns_none(self):
        self.assertIsNone(SymbolIndex().by_name("nope"))


class TestDictErgonomics(unittest.TestCase):
    """Back-compat accessors for call sites that used to treat
    ``instance.symbols`` as a plain dict."""

    def test_getitem_contains_iter_len(self):
        index = SymbolIndex()
        v = _var("x1")
        index.register(v)
        self.assertIs(index["x1"], v)
        self.assertIn("x1", index)
        self.assertEqual(len(index), 1)
        self.assertEqual(list(index), [v])

    def test_setitem_registers(self):
        index = SymbolIndex()
        v = _var("x1")
        index["x1"] = v
        self.assertIs(index.by_name("x1"), v)

    def test_setitem_renames_mismatched(self):
        index = SymbolIndex()
        v = _var("orig")
        index["renamed"] = v
        self.assertIs(index.by_name("renamed"), v)
        self.assertEqual(v.name, "renamed")

    def test_get_default(self):
        index = SymbolIndex()
        self.assertIsNone(index.get("missing"))
        self.assertEqual(index.get("missing", "fallback"), "fallback")


class TestUnregisterClear(unittest.TestCase):
    def test_unregister_removes_from_all_indices(self):
        index = SymbolIndex()
        v = _var("t_x_0")
        index.register(v)
        removed = index.unregister("t_x_0")
        self.assertIs(removed, v)
        self.assertIsNone(index.by_name("t_x_0"))
        self.assertEqual(index.by_column(COL_X), [])
        self.assertEqual(index.by_row(REL_T, "row_0"), [])

    def test_unregister_missing_returns_none(self):
        self.assertIsNone(SymbolIndex().unregister("nope"))

    def test_clear_wipes_everything(self):
        index = SymbolIndex()
        index.register(_var("t_x_0", column=COL_X))
        index.register(_var("t_y_0", column=COL_Y))
        index.clear()
        self.assertEqual(len(index), 0)
        self.assertEqual(index.by_row(REL_T, "row_0"), [])


class TestInstanceIntegration(unittest.TestCase):
    """End-to-end: Instance.create_row populates SymbolIndex with back-pointers."""

    def test_create_row_populates_symbols_with_backpointers(self):
        from parseval.instance import Instance

        ddl = "CREATE TABLE users (id INT PRIMARY KEY, name TEXT);"
        inst = Instance(ddls=ddl, name="t", dialect="sqlite")
        inst.create_row("users", values={"id": 1, "name": "alice"})

        users_symbols = list(inst.symbols)
        self.assertTrue(users_symbols)
        # Every Variable should carry identity and row back-pointers.
        for v in users_symbols:
            self.assertIsNotNone(v.args.get("relation_id"))
            self.assertIsNotNone(v.args.get("column_id"))
            self.assertIsNotNone(v.args.get("rowid"))

        # Reverse indices should resolve.
        id_cells = inst.symbols.by_column(inst.column_id("users", "id"))
        name_cells = inst.symbols.by_column(inst.column_id("users", "name"))
        self.assertEqual(len(id_cells), 1)
        self.assertEqual(len(name_cells), 1)

    def test_reset_clears_symbols(self):
        from parseval.instance import Instance

        ddl = "CREATE TABLE users (id INT PRIMARY KEY);"
        inst = Instance(ddls=ddl, name="t", dialect="sqlite")
        inst.create_row("users", values={"id": 1})
        self.assertTrue(len(inst.symbols) > 0)
        inst.reset()
        self.assertEqual(len(inst.symbols), 0)


if __name__ == "__main__":
    unittest.main()
