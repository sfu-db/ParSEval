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

SCHEMA_FK_CHAIN = (
    "CREATE TABLE grandparent (id INT PRIMARY KEY, label TEXT NOT NULL);"
    "CREATE TABLE parent (id INT PRIMARY KEY, grandparent_id INT REFERENCES grandparent(id), name TEXT NOT NULL);"
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
            "Parent should have at least one row")
        # Child rows should also be present
        self.assertIn("child", rows)
        child_rows = rows["child"]
        self.assertGreater(len(child_rows), 0)

SCHEMA_JOIN = (
    "CREATE TABLE parent (id INT PRIMARY KEY, name TEXT);"
    "CREATE TABLE child (id INT PRIMARY KEY, parent_id INT REFERENCES parent(id), val INT);"
)


class TestHavingCountTableSpecific(unittest.TestCase):
    """HAVING COUNT > N should set min_rows only on the counted table."""

    def test_min_rows_applied_to_counted_table_only(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator

        schema = SCHEMA_JOIN
        sql = "SELECT parent.id, COUNT(child.id) FROM parent JOIN child ON parent.id = child.parent_id GROUP BY parent.id HAVING COUNT(child.id) > 3"
        instance = Instance(ddls=schema, name="test_having", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        alias_map = plan.alias_map

        propagator = Propagator(plan, instance, alias_map, "sqlite")
        specs = propagator.propagate()

        pos_spec = specs[0]  # positive branch
        # child table should have min_rows >= 4 (COUNT > 3 → need 4)
        child_req = pos_spec.requirements.get("child")
        self.assertIsNotNone(child_req, "child table should be in requirements")
        self.assertGreaterEqual(child_req.min_rows, 4,
            f"child min_rows should be >= 4 for COUNT > 3, got {child_req.min_rows}")
        # parent table should NOT be forced to 4 rows
        parent_req = pos_spec.requirements.get("parent")
        if parent_req:
            self.assertLess(parent_req.min_rows, 4,
                f"parent min_rows should be < 4, got {parent_req.min_rows}")


    def test_transitive_fk_chain_grandparent_discovered(self):
        """Resolver should discover grandparent via parent FK chain (child -> parent -> grandparent)."""
        instance = Instance(ddls=SCHEMA_FK_CHAIN, name="test_fk_chain", dialect="sqlite")
        resolver = Resolver(instance, dialect="sqlite")

        spec = BranchSpec(branch="positive")
        # Only require child — parent and grandparent should be auto-discovered
        spec.requirements["child"] = TableRequirement(table="child", min_rows=1)

        rows = resolver.resolve(spec)
        self.assertIn("grandparent", rows,
            "Resolver should discover grandparent via transitive FK chain")
        self.assertGreater(len(rows["grandparent"]), 0,
            "Grandparent should have at least one row")
        self.assertIn("parent", rows,
            "Resolver should discover parent via FK chain")
        self.assertGreater(len(rows["parent"]), 0,
            "Parent should have at least one row")
