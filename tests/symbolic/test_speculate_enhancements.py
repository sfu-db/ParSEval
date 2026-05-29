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


class TestOffsetTableSpecific(unittest.TestCase):
    """OFFSET should set min_rows on the driving table only, not all tables."""

    def test_offset_applies_to_driving_table_only(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator

        schema = SCHEMA_JOIN
        sql = "SELECT parent.id FROM parent JOIN child ON parent.id = child.parent_id LIMIT 5 OFFSET 10"
        instance = Instance(ddls=schema, name="test_offset", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        alias_map = plan.alias_map

        propagator = Propagator(plan, instance, alias_map, "sqlite")
        specs = propagator.propagate()

        pos_spec = specs[0]
        # Driving table (parent) should have min_rows >= 15 (offset 10 + limit 5)
        parent_req = pos_spec.requirements.get("parent")
        self.assertIsNotNone(parent_req)
        self.assertGreaterEqual(parent_req.min_rows, 15,
            f"parent (driving table) min_rows should be >= 15, got {parent_req.min_rows}")
        # Non-driving table (child) should NOT be forced to 15
        child_req = pos_spec.requirements.get("child")
        if child_req:
            self.assertLess(child_req.min_rows, 15,
                f"child min_rows should be < 15, got {child_req.min_rows}")


class TestAggregateNullColumns(unittest.TestCase):
    """Propagator should mark COUNT/SUM/AVG columns as must_null."""

    def test_count_column_marked_must_null(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator

        schema = "CREATE TABLE t (id INT PRIMARY KEY, name TEXT);"
        sql = "SELECT COUNT(name) FROM t"
        instance = Instance(ddls=schema, name="test_agg_null", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        alias_map = plan.alias_map

        propagator = Propagator(plan, instance, alias_map, "sqlite")
        specs = propagator.propagate()

        pos_spec = specs[0]
        t_req = pos_spec.requirements.get("t")
        self.assertIsNotNone(t_req)
        self.assertIn("name", t_req.must_null,
            f"'name' should be in must_null for COUNT(name), got must_null={t_req.must_null}")
        self.assertGreaterEqual(t_req.min_rows, 2,
            f"min_rows should be >= 2 (one NULL + one non-NULL), got {t_req.min_rows}")


class TestRowValidation(unittest.TestCase):
    """Resolver should validate generated rows and retry on predicate failure."""

    def test_row_satisfies_predicates(self):
        from parseval.instance import Instance
        from parseval.symbolic.speculate import Resolver, TableRequirement

        schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
        instance = Instance(ddls=schema, name="test_validate", dialect="sqlite")
        resolver = Resolver(instance, dialect="sqlite")

        spec = BranchSpec(branch="positive")
        spec.requirements["t"] = TableRequirement(
            table="t",
            min_rows=1,
            predicates=[("val", ">", 10), ("val", "<", 20)],
        )

        rows = resolver.resolve(spec)
        self.assertIn("t", rows, "Resolver should produce rows for table t")
        t_rows = rows["t"]
        self.assertGreater(len(t_rows), 0)
        val = t_rows[0]["val"]
        self.assertGreater(val, 10, f"val should be > 10, got {val}")
        self.assertLess(val, 20, f"val should be < 20, got {val}")


class TestScalarSubqueryDetection(unittest.TestCase):
    """Propagator should detect scalar subqueries in Filter atoms and mark them for deferred evaluation."""

    def test_scalar_subquery_detected(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator

        schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
        sql = "SELECT * FROM t WHERE val > (SELECT AVG(val) FROM t)"
        instance = Instance(ddls=schema, name="test_scalar", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)
        alias_map = plan.alias_map

        propagator = Propagator(plan, instance, alias_map, "sqlite")
        specs = propagator.propagate()

        pos_spec = specs[0]
        self.assertTrue(hasattr(pos_spec, 'deferred'),
            "BranchSpec should have a 'deferred' field")
        self.assertGreater(len(pos_spec.deferred), 0,
            "Should have at least one deferred subquery atom")
