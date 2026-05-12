"""Tests for Instance's new row-creation and transactional APIs."""

from __future__ import annotations

import unittest

from parseval.instance import Instance


SCHEMA = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT, score REAL);"


class TestPlaceRow(unittest.TestCase):
    def test_place_row_appends_without_validation(self):
        inst = Instance(ddls=SCHEMA, name="place", dialect="sqlite")
        row = inst.place_row("t", {"id": 1, "name": "alice", "score": 9.5})
        self.assertEqual(len(inst.get_rows("t")), 1)
        self.assertEqual(row["id"].concrete, 1)
        self.assertEqual(row["name"].concrete, "alice")
        self.assertEqual(row["score"].concrete, 9.5)

    def test_place_row_fills_missing_columns_with_none(self):
        inst = Instance(ddls=SCHEMA, name="place", dialect="sqlite")
        row = inst.place_row("t", {"id": 2})
        self.assertIsNone(row["name"].concrete)
        self.assertIsNone(row["score"].concrete)

    def test_place_row_registers_symbols_with_backpointers(self):
        inst = Instance(ddls=SCHEMA, name="place", dialect="sqlite")
        inst.place_row("t", {"id": 1, "name": "bob", "score": 8.0})
        id_cells = inst.symbols.by_column("t", "id")
        self.assertEqual(len(id_cells), 1)
        self.assertEqual(id_cells[0].concrete, 1)
        self.assertEqual(id_cells[0].args.get("table"), "t")
        self.assertEqual(id_cells[0].args.get("column"), "id")

    def test_place_row_allows_duplicate_pk_without_error(self):
        """place_row is unchecked — no unique validation."""
        inst = Instance(ddls=SCHEMA, name="place", dialect="sqlite")
        inst.place_row("t", {"id": 1, "name": "a", "score": 1.0})
        inst.place_row("t", {"id": 1, "name": "b", "score": 2.0})
        self.assertEqual(len(inst.get_rows("t")), 2)

    def test_place_row_raises_on_unknown_table(self):
        inst = Instance(ddls=SCHEMA, name="place", dialect="sqlite")
        with self.assertRaises(KeyError):
            inst.place_row("nonexistent", {"id": 1})


class TestCheckpointRollback(unittest.TestCase):
    def test_rollback_restores_row_count(self):
        inst = Instance(ddls=SCHEMA, name="cp", dialect="sqlite")
        inst.create_row("t", {"id": 1, "name": "a"})
        cp = inst.checkpoint()
        inst.create_row("t", {"id": 2, "name": "b"})
        self.assertEqual(len(inst.get_rows("t")), 2)
        inst.rollback(cp)
        self.assertEqual(len(inst.get_rows("t")), 1)

    def test_rollback_unregisters_new_symbols(self):
        inst = Instance(ddls=SCHEMA, name="cp", dialect="sqlite")
        inst.create_row("t", {"id": 1, "name": "a"})
        cp = inst.checkpoint()
        symbols_before = len(inst.symbols)
        inst.create_row("t", {"id": 2, "name": "b"})
        self.assertGreater(len(inst.symbols), symbols_before)
        inst.rollback(cp)
        self.assertEqual(len(inst.symbols), symbols_before)

    def test_rollback_allows_re_creation_after_undo(self):
        inst = Instance(ddls=SCHEMA, name="cp", dialect="sqlite")
        cp = inst.checkpoint()
        inst.create_row("t", {"id": 1, "name": "a"})
        inst.rollback(cp)
        # Should be able to create the same row again without conflict.
        inst.create_row("t", {"id": 1, "name": "a"})
        self.assertEqual(len(inst.get_rows("t")), 1)

    def test_checkpoint_is_lightweight(self):
        """Checkpoint doesn't deep-copy row data — it's a shallow snapshot."""
        inst = Instance(ddls=SCHEMA, name="cp", dialect="sqlite")
        inst.create_row("t", {"id": 1, "name": "a"})
        cp = inst.checkpoint()
        # Mutating the checkpoint dict shouldn't affect the instance.
        for key in list(cp["data"].keys()):
            cp["data"][key].clear()
        self.assertEqual(len(inst.get_rows("t")), 1)


if __name__ == "__main__":
    unittest.main()
