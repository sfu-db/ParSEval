"""create_rows: domain-backed full rows, Instance/Domain boundary."""

from __future__ import annotations

import unittest

from parseval.instance import Instance
from parseval.instance.core import Instance as InstanceCore


DDL = """
CREATE TABLE parent (
    id INT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE child (
    id INT PRIMARY KEY,
    parent_id INT NOT NULL,
    label TEXT,
    FOREIGN KEY (parent_id) REFERENCES parent(id)
);
CREATE TABLE item (
    a_id INT NOT NULL,
    b_id INT NOT NULL,
    seq INT NOT NULL,
    PRIMARY KEY (a_id, b_id, seq)
);
"""


class TestCreateRowsEmptyPayload(unittest.TestCase):
    def test_empty_mapping_payload_generates_one_full_row(self):
        inst = Instance(ddls=DDL, name="empty_map", dialect="sqlite")
        results = inst.create_rows({"parent": {}})
        self.assertEqual(list(results), ["parent"])
        self.assertEqual(len(results["parent"]), 1)
        row = inst.get_rows("parent")[0]
        self.assertIsNotNone(row["id"].concrete)
        self.assertIsNotNone(row["name"].concrete)
        self.assertEqual(len(inst.get_rows("child")), 0)

    def test_empty_sequence_payload_generates_one_full_row(self):
        inst = Instance(ddls=DDL, name="empty_seq", dialect="sqlite")
        results = inst.create_rows({"parent": []})
        self.assertEqual(len(results["parent"]), 1)
        self.assertEqual(len(inst.get_rows("parent")), 1)

    def test_omitted_concretes_creates_nothing(self):
        inst = Instance(ddls=DDL, name="none", dialect="sqlite")
        self.assertEqual(inst.create_rows(), {})
        self.assertEqual(inst.create_rows({}), {})
        self.assertEqual(len(inst.get_rows("parent")), 0)


class TestCreateRowsConstraints(unittest.TestCase):
    def test_child_only_creates_parent_and_binds_fk(self):
        inst = Instance(ddls=DDL, name="fk", dialect="sqlite")
        inst.create_rows({"child": {}})
        self.assertEqual(len(inst.get_rows("parent")), 1)
        self.assertEqual(len(inst.get_rows("child")), 1)
        parent_id = inst.get_rows("parent")[0]["id"].concrete
        child = inst.get_rows("child")[0]
        self.assertEqual(child["parent_id"].concrete, parent_id)
        self.assertIsNotNone(child["id"].concrete)

    def test_two_empty_parents_have_distinct_pks(self):
        inst = Instance(ddls=DDL, name="uniq", dialect="sqlite")
        inst.create_rows({"parent": {}})
        inst.create_rows({"parent": {}})
        ids = [r["id"].concrete for r in inst.get_rows("parent")]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_partial_presets_fill_missing_columns(self):
        inst = Instance(ddls=DDL, name="partial", dialect="sqlite")
        inst.create_rows({"parent": [{"id": 7}]})
        row = inst.get_rows("parent")[0]
        self.assertEqual(row["id"].concrete, 7)
        self.assertIsNotNone(row["name"].concrete)

    def test_composite_pk_freshen_across_batch(self):
        inst = Instance(ddls=DDL, name="comp", dialect="sqlite")
        inst.create_rows(
            {
                "item": [
                    {"a_id": 1, "b_id": 1, "seq": 1},
                    {"a_id": 1, "b_id": 1},
                ]
            }
        )
        rows = inst.get_rows("item")
        self.assertEqual(len(rows), 2)
        keys = {
            (r["a_id"].concrete, r["b_id"].concrete, r["seq"].concrete)
            for r in rows
        }
        self.assertEqual(len(keys), 2)
        self.assertEqual(rows[0]["seq"].concrete, 1)
        self.assertNotEqual(rows[1]["seq"].concrete, 1)


class TestInstanceDomainBoundary(unittest.TestCase):
    def test_instance_has_no_value_invention_helpers(self):
        forbidden = {
            "_default_for_type",
            "_next_default_value",
            "_freshen_uniques",
        }
        methods = {name for name in dir(InstanceCore) if not name.startswith("__")}
        self.assertTrue(forbidden.isdisjoint(methods), methods & forbidden)


if __name__ == "__main__":
    unittest.main()
