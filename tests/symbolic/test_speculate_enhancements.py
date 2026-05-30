"""Unit tests for speculative layer enhancements.

Each test targets a specific enhancement to the Propagator or Resolver.
"""

# BIRD benchmark baseline: 1508/1534 (98%) — recorded 2026-05-29

import unittest
from parseval.instance import Instance
from parseval.symbolic.speculate import BranchSpec, Resolver, TableConstraint, TableRequirement


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
    """Propagator should add IS NULL constraint for COUNT/SUM/AVG columns."""

    def test_count_column_gets_is_null_constraint(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator
        from sqlglot import exp

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
        # New approach: IS NULL stored as exp.Is expression in constraints
        has_is_null = any(
            isinstance(e, exp.Is) and isinstance(e.expression, exp.Null) and not e.args.get("not")
            for e in t_req.constraints
        )
        self.assertTrue(has_is_null,
            f"should have IS NULL constraint for COUNT(name), got constraints={t_req.constraints}")
        self.assertGreaterEqual(t_req.min_rows, 2,
            f"min_rows should be >= 2 (one NULL + one non-NULL), got {t_req.min_rows}")


class TestRowValidation(unittest.TestCase):
    """Resolver should produce rows satisfying constraints via the solver."""

    def test_row_satisfies_predicates(self):
        from parseval.instance import Instance
        from parseval.solver.unified import Solver
        from parseval.symbolic.speculate import Resolver, TableConstraint
        from sqlglot import exp

        schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
        instance = Instance(ddls=schema, name="test_validate", dialect="sqlite")
        solver = Solver(dialect="sqlite")
        resolver = Resolver(instance, dialect="sqlite", solver=solver)

        spec = BranchSpec(branch="positive")
        req = TableConstraint(table="t", min_rows=1)
        val_col = exp.Column(this=exp.to_identifier("val"), table=exp.to_identifier("t"))
        val_col.type = exp.DataType.build("INT")
        req.constraints.append(exp.GT(this=val_col, expression=exp.Literal.number(10)))
        req.constraints.append(exp.LT(this=val_col.copy(), expression=exp.Literal.number(20)))
        spec.requirements["t"] = req

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


class TestDeferredSubqueryResolution(unittest.TestCase):
    """Resolver should evaluate deferred scalar subqueries and adjust outer rows."""

    def test_deferred_subquery_adjusts_outer_value(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import speculate

        schema = "CREATE TABLE t (id INT PRIMARY KEY, val INT);"
        instance = Instance(ddls=schema, name="test_deferred", dialect="sqlite")
        # Insert a row with val=10 so AVG(val)=10
        instance.create_row("t", values={"val": 10})

        sql = "SELECT * FROM t WHERE val > (SELECT AVG(val) FROM t)"
        plan = Plan(preprocess_sql(sql, instance, dialect="sqlite"))
        result = speculate(plan, instance, plan.alias_map, "sqlite")

        # After deferred resolution, there should be a row with val > 10
        t_rows = instance.get_rows("t")
        vals = [r["val"].concrete for r in t_rows if r["val"].concrete is not None]
        has_gt_avg = any(v > 10 for v in vals if isinstance(v, (int, float)))
        self.assertTrue(has_gt_avg,
            f"Should have a row with val > 10 (AVG), got vals={vals}")


class TestPropagatorExpressionConstraints(unittest.TestCase):
    """Propagator should build exp.Expression constraints directly."""

    def test_propagator_builds_expression_constraints(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import Propagator
        from sqlglot import exp

        schema = "CREATE TABLE t1 (id INT PRIMARY KEY, val INT NOT NULL);"
        sql = "SELECT * FROM t1 WHERE t1.val > 5"
        instance = Instance(ddls=schema, name="test_prop", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)

        propagator = Propagator(plan, instance, plan.alias_map, "sqlite")
        specs = propagator.propagate()

        pos = specs[0]
        t1 = pos.requirements.get("t1")
        self.assertIsNotNone(t1, "t1 should be in requirements")
        has_gt = any(isinstance(e, exp.GT) for e in t1.constraints)
        self.assertTrue(has_gt, "should have GT for val > 5")
        has_not_null = any(
            isinstance(e, exp.Is)
            and isinstance(e.expression, exp.Not)
            and isinstance(e.expression.this, exp.Null)
            for e in t1.constraints
        )
        self.assertTrue(has_not_null, "should have IS NOT NULL for NOT NULL column")

    def test_table_constraint_dataclass(self):
        """TableConstraint should have the constraints field."""
        from sqlglot import exp

        tc = TableConstraint(table="t1")
        self.assertEqual(tc.table, "t1")
        self.assertEqual(tc.constraints, [])
        self.assertEqual(tc.min_rows, 1)
        self.assertEqual(tc.duplicate_columns, [])
        self.assertEqual(tc.group_key_columns, [])

        # Add an expression constraint
        gt = exp.GT(
            this=exp.column("val", "t1"),
            expression=exp.Literal.number(5),
        )
        tc.constraints.append(gt)
        self.assertEqual(len(tc.constraints), 1)
        self.assertIsInstance(tc.constraints[0], exp.GT)

    def test_backward_compat_alias(self):
        """TableRequirement should be an alias for TableConstraint."""
        self.assertIs(TableRequirement, TableConstraint)
        req = TableRequirement(table="t")
        self.assertIsInstance(req, TableConstraint)


class TestResolverDelegatestoSolver(unittest.TestCase):
    """Resolver should delegate constraint satisfaction to the Solver."""

    def test_resolver_delegates_to_solver(self):
        from parseval.instance import Instance
        from parseval.solver.unified import Solver
        from parseval.symbolic.speculate import BranchSpec, Resolver, TableConstraint
        from sqlglot import exp

        schema = "CREATE TABLE t1 (id INT PRIMARY KEY, val INT);"
        instance = Instance(ddls=schema, name="test_res", dialect="sqlite")
        solver = Solver(dialect="sqlite")
        resolver = Resolver(instance, dialect="sqlite", solver=solver)

        spec = BranchSpec(branch="positive")
        req = TableConstraint(table="t1")
        col = exp.Column(this=exp.to_identifier("val"), table=exp.to_identifier("t1"))
        col.type = exp.DataType.build("INT")
        req.constraints.append(exp.GT(this=col, expression=exp.Literal.number(5)))
        spec.requirements["t1"] = req

        rows = resolver.resolve(spec)
        assert "t1" in rows
        assert rows["t1"][0]["val"] > 5


class TestSpeculateEndToEnd(unittest.TestCase):
    """speculate() should create a Solver and produce valid rows end-to-end."""

    def test_speculate_end_to_end(self):
        from parseval.instance import Instance
        from parseval.plan import Plan
        from parseval.query import preprocess_sql
        from parseval.symbolic.speculate import speculate

        schema = "CREATE TABLE t1 (id INT PRIMARY KEY, val INT);"
        sql = "SELECT * FROM t1 WHERE t1.val > 5"
        instance = Instance(ddls=schema, name="test_e2e", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr)

        results = speculate(plan, instance, plan.alias_map, dialect="sqlite")
        self.assertGreaterEqual(len(results), 1)
        branch, rows = results[0]
        self.assertEqual(branch, "positive")
        self.assertGreater(rows["t1"][0]["val"], 5)
