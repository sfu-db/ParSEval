"""Unit tests for speculative layer enhancements.

Each test targets a specific enhancement to the Propagator or Resolver.
"""

# BIRD benchmark baseline: 1508/1534 (98%) — recorded 2026-05-29

import unittest
from parseval.instance import Instance
from parseval.symbolic.speculate import BranchSpec, Resolver, TableRequirement


SCHEMA_FK = (
    "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT NOT NULL);"
    "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT REFERENCES parent(id), val INT);"
)


class TestFKReferencedTableRows(unittest.TestCase):
    """Resolver should create rows for FK-referenced parent tables even if not in spec.requirements."""

    def test_parent_table_created_when_only_child_in_spec(self):
        instance = Instance(ddls=SCHEMA_FK, name="test_fk", dialect="sqlite")
        resolver = Resolver(instance, dialect="sqlite")

        spec = BranchSpec(branch="positive")
        # Only require child — parent is FK-referenced but not in spec
        spec.requirements["child"] = TableRequirement(table="child", min_rows=1)

        rows = resolver.resolve(spec)
        # Parent table should have rows created via FK resolution
        self.assertIn("parent", rows,
            "Resolver should create parent rows for FK-referenced tables")
        parent_rows = rows["parent"]
        self.assertGreater(len(parent_rows), 0,
            "Resolver should create parent rows for FK-referenced tables")
        # Child rows should also be present
        self.assertIn("child", rows)
        child_rows = rows["child"]
        self.assertGreater(len(child_rows), 0)
