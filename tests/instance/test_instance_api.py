"""Tests for Instance's new row-creation and transactional APIs."""

from __future__ import annotations

import inspect
import logging
import random
import unittest

from parseval.instance import Instance

logger = logging.getLogger(__name__)


SCHEMA = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT, score REAL);"


class TestInstanceSignatures(unittest.TestCase):
    def test_removed_parameters_are_not_part_of_instance_api(self):
        self.assertNotIn("sync_db", inspect.signature(Instance.create_rows).parameters)
        self.assertNotIn("sync_db", inspect.signature(Instance.create_row).parameters)
        self.assertNotIn("normalize", inspect.signature(Instance.nullable).parameters)
        self.assertNotIn("normalize", inspect.signature(Instance.is_unique).parameters)


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
        id_col = inst.column_id("t", "id")
        id_cells = inst.symbols.by_column(id_col)
        self.assertEqual(len(id_cells), 1)
        self.assertEqual(id_cells[0].concrete, 1)
        self.assertEqual(id_cells[0].relation_id, inst.table_id("t"))
        self.assertEqual(id_cells[0].column_id, id_col)
        self.assertEqual(id_cells[0].table_name, "t")
        self.assertEqual(id_cells[0].column_name, "id")

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


class TestDeterministicReferenceSelection(unittest.TestCase):
    def test_fk_parent_choice_does_not_use_global_random_state(self):
        schema = """
        CREATE TABLE parent (id INT PRIMARY KEY);
        CREATE TABLE child (
            id INT PRIMARY KEY,
            parent_id INT,
            FOREIGN KEY (parent_id) REFERENCES parent(id)
        );
        """

        def generated_parent(seed):
            random.seed(seed)
            inst = Instance(ddls=schema, name="fk_choice", dialect="sqlite")
            inst.create_row("parent", {"id": 1})
            inst.create_row("parent", {"id": 2})
            created = inst.create_row("child", {"id": 10}).created[
                inst.table_id("child")
            ][0]
            return created[inst.column_id("child", "parent_id")].concrete

        self.assertEqual(generated_parent(1), generated_parent(5))


BIRD_SCHEMA = """CREATE TABLE frpm (CDSCode TEXT NOT NULL PRIMARY KEY, `Academic Year` TEXT NULL, `County Code` TEXT NULL, `District Code` INT NULL, `School Code` TEXT NULL, `County Name` TEXT NULL, `District Name` TEXT NULL, `School Name` TEXT NULL, `District Type` TEXT NULL, `School Type` TEXT NULL, `Educational Option Type` TEXT NULL, `NSLP Provision Status` TEXT NULL, `Charter School (Y/N)` INT NULL, `Charter School Number` TEXT NULL, `Charter Funding Type` TEXT NULL, IRC INT NULL, `Low Grade` TEXT NULL, `High Grade` TEXT NULL, `Enrollment (K-12)` FLOAT NULL, `Free Meal Count (K-12)` FLOAT NULL, `Percent (%) Eligible Free (K-12)` FLOAT NULL, `FRPM Count (K-12)` FLOAT NULL, `Percent (%) Eligible FRPM (K-12)` FLOAT NULL, `Enrollment (Ages 5-17)` FLOAT NULL, `Free Meal Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible Free (Ages 5-17)` FLOAT NULL, `FRPM Count (Ages 5-17)` FLOAT NULL, `Percent (%) Eligible FRPM (Ages 5-17)` FLOAT NULL, `2013-14 CALPADS Fall 1 Certification Status` INT NULL, FOREIGN KEY (CDSCode) REFERENCES schools (CDSCode));CREATE TABLE satscores (cds TEXT NOT NULL PRIMARY KEY, rtype TEXT NOT NULL, sname TEXT NULL, dname TEXT NULL, cname TEXT NULL, enroll12 INT NOT NULL, NumTstTakr INT NOT NULL, AvgScrRead INT NULL, AvgScrMath INT NULL, AvgScrWrite INT NULL, NumGE1500 INT NULL, FOREIGN KEY (cds) REFERENCES schools (CDSCode));CREATE TABLE schools (CDSCode TEXT NOT NULL PRIMARY KEY, NCESDist TEXT NULL, NCESSchool TEXT NULL, StatusType TEXT NOT NULL, County TEXT NOT NULL, District TEXT NOT NULL, School TEXT NULL, Street TEXT NULL, StreetAbr TEXT NULL, City TEXT NULL, Zip TEXT NULL, State TEXT NULL, MailStreet TEXT NULL, MailStrAbr TEXT NULL, MailCity TEXT NULL, MailZip TEXT NULL, MailState TEXT NULL, Phone TEXT NULL, Ext TEXT NULL, Website TEXT NULL, OpenDate DATE NULL, ClosedDate DATE NULL, Charter INT NULL, CharterNum TEXT NULL, FundingType TEXT NULL, DOC TEXT NOT NULL, DOCType TEXT NOT NULL, SOC TEXT NULL, SOCType TEXT NULL, EdOpsCode TEXT NULL, EdOpType TEXT NULL, EILCode TEXT NULL, EILName TEXT NULL, SOCType TEXT NULL, GradeLow TEXT NULL, GradeHigh TEXT NULL);"""


class TestBuildContextFromInstance(unittest.TestCase):
    def test_instance_to_context(self):
        from parseval.plan import build_context_from_instance

        instance = Instance(ddls=BIRD_SCHEMA, name="test", dialect="sqlite")
        for tbl_name in instance.tables:
            instance.create_row(tbl_name)

        ctx = build_context_from_instance(instance)

        self.assertIsNotNone(ctx.table)

        for table_name, dc in ctx.tables.items():
            for column in dc.columns:
                self.assertIsNotNone(dc.get_column_type(column))


if __name__ == "__main__":
    unittest.main()
