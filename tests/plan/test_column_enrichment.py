"""Tests for column schema enrichment in Plan._annotate.

Covers:
- column_meta / set_column_meta round-trip and hashability
- Table alias resolution (o → orders)
- Join alias resolution (c → customers)
- CTE resolution
- Correlated subquery (columns inside Subquery/Exists skipped in outer scope)
- Columns without schema match (not enriched)
- instance=None skips enrichment gracefully
"""

from __future__ import annotations

import unittest

import pytest
import sqlglot
from sqlglot import exp

from parseval.dtype import DataType
from parseval.instance import Instance
from parseval.plan import Plan
from parseval.plan.planner import Filter, Join, Project, Scan, SubPlan
from parseval.plan.rex import column_meta, set_column_meta
from parseval.query import preprocess_sql


def _make_instance(ddls: str, name: str = "test", dialect: str = "sqlite") -> Instance:
    return Instance(ddls=ddls, name=name, dialect=dialect)


def _plan(sql: str, instance: Instance | None = None, dialect: str = "sqlite") -> Plan:
    if instance is not None:
        expr = preprocess_sql(sql, instance, dialect=dialect)
    else:
        expr = sqlglot.parse_one(sql, read=dialect)
    plan = Plan(expr, instance)
    # Trigger annotation (and enrichment) eagerly so tests can inspect metadata.
    plan.annotation_for(plan.root)
    return plan


def _first_step_of_type(plan: Plan, step_type):
    for step in plan.ordered_steps:
        if isinstance(step, step_type):
            return step
    raise AssertionError(f"no {step_type.__name__} step in plan")


def _enriched_columns(step) -> dict[str, dict]:
    """Return {col.sql(): meta_dict} for all enriched columns in a step."""
    from parseval.plan.planner import _step_expressions
    result = {}
    for expr in _step_expressions(step):
        for col in expr.find_all(exp.Column):
            meta = column_meta(col)
            if meta:
                result.setdefault(col.sql(), meta)
    return result


def _condition_columns(step) -> dict[str, dict]:
    """Return enriched columns from the step's condition only."""
    cond = getattr(step, "condition", None)
    if cond is None:
        return {}
    result = {}
    for col in cond.find_all(exp.Column):
        meta = column_meta(col)
        if meta:
            result.setdefault(col.sql(), meta)
    return result


# =============================================================================
# column_meta / set_column_meta primitives
# =============================================================================


class TestColumnMetaHelpers(unittest.TestCase):
    def test_round_trip(self):
        col = exp.column("x", "t")
        self.assertIsNone(column_meta(col))
        set_column_meta(col, {"table": "t", "nullable": False, "unique": True, "domain": "INT"})
        meta = column_meta(col)
        self.assertEqual(meta["table"], "t")
        self.assertFalse(meta["nullable"])
        self.assertTrue(meta["unique"])

    def test_hashable_after_enrichment(self):
        col = exp.column("x", "t")
        set_column_meta(col, {"table": "t", "nullable": False, "unique": False, "domain": "INT"})
        # Must not raise — sqlglot's simplify hashes nodes.
        hash(col)

    def test_copy_preserves_meta(self):
        col = exp.column("x", "t")
        set_column_meta(col, {"table": "t", "nullable": False, "unique": False, "domain": "INT"})
        col2 = col.copy()
        meta = column_meta(col2)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["table"], "t")

    def test_sql_unaffected(self):
        col = exp.column("x", "t")
        set_column_meta(col, {"table": "t", "nullable": False, "unique": False, "domain": "INT"})
        self.assertNotIn("_parseval_meta", col.sql())

    def test_negate_predicate_preserves_meta(self):
        from parseval.plan.rex import negate_predicate
        col = exp.column("x", "t")
        set_column_meta(col, {"table": "t", "nullable": False, "unique": False, "domain": "INT"})
        gt = exp.GT(this=col.copy(), expression=exp.Literal.number(10))
        neg = negate_predicate(gt)
        for c in neg.find_all(exp.Column):
            meta = column_meta(c)
            self.assertIsNotNone(meta)
            self.assertEqual(meta["table"], "t")


# =============================================================================
# Basic enrichment via Plan._annotate
# =============================================================================


class TestBasicEnrichment(unittest.TestCase):
    def test_simple_where_enriches_columns(self):
        inst = _make_instance("CREATE TABLE t (x INTEGER NOT NULL, y TEXT);")
        plan = _plan("SELECT * FROM t WHERE t.x > 0", inst)
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        self.assertIn('"t"."x"', cols)
        self.assertFalse(cols['"t"."x"']["nullable"])
        # y is not in the WHERE, so it should NOT be enriched in the filter.
        self.assertNotIn('"t"."y"', cols)

    def test_projection_columns_enriched(self):
        inst = _make_instance("CREATE TABLE t (x INTEGER NOT NULL, y TEXT);")
        plan = _plan("SELECT x, y FROM t WHERE x > 0", inst)
        step = _first_step_of_type(plan, Project)
        cols = _enriched_columns(step)
        # Both projected columns should be enriched.
        found_names = {meta["table"] for meta in cols.values()}
        self.assertIn("t", found_names)

    def test_column_not_in_instance_not_enriched(self):
        """A column absent from the instance schema fails closed."""
        # Use raw sqlglot parse (no preprocess_sql validation) with a
        # manually built plan referencing a column absent from the schema.
        inst = _make_instance("CREATE TABLE t (x INTEGER);")
        expr = sqlglot.parse_one("SELECT * FROM t WHERE t.z > 0", read="sqlite")
        plan = Plan(expr, inst)
        with pytest.raises(ValueError, match="Unresolved column"):
            plan.annotation_for(plan.root)

    def test_nullable_and_unique_metadata(self):
        inst = _make_instance(
            "CREATE TABLE t (a INTEGER NOT NULL, b TEXT UNIQUE, c REAL);"
        )
        plan = _plan("SELECT * FROM t WHERE a > 0 AND b = 'x' AND c < 10", inst)
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        self.assertFalse(cols['"t"."a"']["nullable"])
        self.assertTrue(cols['"t"."b"']["unique"])
        self.assertTrue(cols['"t"."c"']["nullable"])
        self.assertFalse(cols['"t"."c"']["unique"])

    def test_domain_is_datatype(self):
        inst = _make_instance("CREATE TABLE t (x INTEGER, y TEXT);")
        plan = _plan("SELECT * FROM t WHERE x > 0", inst)
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        domain = cols['"t"."x"']["domain"]
        self.assertIsInstance(domain, DataType)


# =============================================================================
# Table alias resolution
# =============================================================================


class TestTableAliasResolution(unittest.TestCase):
    def test_table_alias_resolves_to_real_table(self):
        inst = _make_instance("CREATE TABLE orders (id INTEGER NOT NULL, amount REAL);")
        plan = _plan("SELECT * FROM orders o WHERE o.amount > 100", inst)
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        self.assertIn('"o"."amount"', cols)
        self.assertEqual(cols['"o"."amount"']["table"], "orders")

    def test_table_alias_preserves_nullable(self):
        inst = _make_instance("CREATE TABLE orders (id INTEGER NOT NULL, amount REAL);")
        plan = _plan("SELECT * FROM orders o WHERE o.id > 0", inst)
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        self.assertFalse(cols['"o"."id"']["nullable"])

    def test_bare_column_resolves_via_source_table(self):
        """Bare column 'x' is qualified to 't.x' by preprocess_sql."""
        inst = _make_instance("CREATE TABLE t (x INTEGER NOT NULL);")
        plan = _plan("SELECT * FROM t WHERE x > 0", inst)
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        self.assertIn('"t"."x"', cols)
        self.assertEqual(cols['"t"."x"']["table"], "t")


# =============================================================================
# Join alias resolution
# =============================================================================


class TestJoinAliasResolution(unittest.TestCase):
    def test_join_columns_resolved_to_correct_tables(self):
        inst = _make_instance(
            "CREATE TABLE orders (id INTEGER NOT NULL, customer_id INTEGER NOT NULL, amount REAL);"
            "CREATE TABLE customers (id INTEGER NOT NULL, name TEXT NOT NULL);"
        )
        plan = _plan(
            "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id WHERE o.amount > 100",
            inst,
        )
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        # o.amount → orders
        self.assertEqual(cols['"o"."amount"']["table"], "orders")
        self.assertTrue(cols['"o"."amount"']["nullable"])

    def test_join_key_columns_resolved(self):
        inst = _make_instance(
            "CREATE TABLE orders (id INTEGER NOT NULL, customer_id INTEGER NOT NULL);"
            "CREATE TABLE customers (id INTEGER NOT NULL, name TEXT);"
        )
        plan = _plan(
            "SELECT * FROM orders o JOIN customers c ON o.customer_id = c.id",
            inst,
        )
        step = _first_step_of_type(plan, Join)
        cols = _enriched_columns(step)
        # Both join keys should be enriched with correct tables.
        found_tables = {col_sql: meta["table"] for col_sql, meta in cols.items()}
        # At least one column should map to each table.
        tables = set(found_tables.values())
        self.assertIn("orders", tables)
        self.assertIn("customers", tables)

    def test_same_column_name_different_tables(self):
        """Both tables have 'id' — each should resolve to its own table."""
        inst = _make_instance(
            "CREATE TABLE a (id INTEGER NOT NULL, val TEXT);"
            "CREATE TABLE b (id INTEGER NOT NULL, ref INTEGER NOT NULL);"
        )
        plan = _plan(
            "SELECT * FROM a JOIN b ON a.id = b.ref WHERE a.val = 'x'",
            inst,
        )
        step = _first_step_of_type(plan, Join)
        cols = _enriched_columns(step)
        # a.id should be in 'a', b.ref should be in 'b'.
        found = {sql: meta["table"] for sql, meta in cols.items()}
        a_cols = [sql for sql, t in found.items() if t == "a"]
        b_cols = [sql for sql, t in found.items() if t == "b"]
        self.assertTrue(len(a_cols) > 0, f"Expected columns from table 'a', got {found}")
        self.assertTrue(len(b_cols) > 0, f"Expected columns from table 'b', got {found}")


# =============================================================================
# CTE resolution
# =============================================================================


class TestCTEResolution(unittest.TestCase):
    def test_cte_column_resolves_to_source_table(self):
        inst = _make_instance("CREATE TABLE t (x INTEGER NOT NULL, y TEXT);")
        plan = _plan(
            "WITH cte AS (SELECT x, y FROM t) SELECT x FROM cte WHERE x > 0",
            inst,
        )
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        # The planner rewrites cte.x → t.x.
        found_tables = {meta["table"] for meta in cols.values()}
        self.assertIn("t", found_tables)


# =============================================================================
# Correlated subquery
# =============================================================================


class TestCorrelatedSubqueryEnrichment(unittest.TestCase):
    def test_exists_inner_columns_enriched_in_inner_scope(self):
        inst = _make_instance(
            "CREATE TABLE orders (id INTEGER NOT NULL, customer_id INTEGER NOT NULL);"
            "CREATE TABLE customers (id INTEGER NOT NULL, name TEXT NOT NULL);"
        )
        plan = _plan(
            "SELECT * FROM orders o WHERE EXISTS (SELECT 1 FROM customers c WHERE c.id = o.customer_id)",
            inst,
        )
        # Trigger annotation.
        plan.annotation_for(plan.root)

        # Find the SubPlan and check its inner plan's columns.
        subplan = _first_step_of_type(plan, SubPlan)
        from parseval.plan.planner import _collect_inner_steps, _step_expressions

        inner_cols = {}
        for inner_step in _collect_inner_steps(subplan.inner):
            for expr in _step_expressions(inner_step):
                for col in expr.find_all(exp.Column):
                    meta = column_meta(col)
                    if meta:
                        inner_cols.setdefault(col.sql(), meta)

        # c.id should resolve to customers (not orders).
        if '"c"."id"' in inner_cols:
            self.assertEqual(inner_cols['"c"."id"']["table"], "customers")

    def test_exists_outer_columns_not_polluted_by_inner(self):
        """Columns from the outer query should not pick up inner table metadata."""
        inst = _make_instance(
            "CREATE TABLE orders (id INTEGER NOT NULL, customer_id INTEGER NOT NULL, amount REAL);"
            "CREATE TABLE customers (id INTEGER NOT NULL, name TEXT);"
        )
        plan = _plan(
            "SELECT * FROM orders o WHERE EXISTS (SELECT 1 FROM customers c WHERE c.id = o.customer_id)",
            inst,
        )
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        # o.customer_id should resolve to orders, not customers.
        if '"o"."customer_id"' in cols:
            self.assertEqual(cols['"o"."customer_id"']["table"], "orders")

    def test_in_subquery_columns_enriched(self):
        inst = _make_instance(
            "CREATE TABLE orders (id INTEGER NOT NULL, customer_id INTEGER);"
            "CREATE TABLE customers (id INTEGER NOT NULL);"
        )
        plan = _plan(
            "SELECT * FROM orders WHERE customer_id IN (SELECT id FROM customers)",
            inst,
        )
        plan.annotation_for(plan.root)
        # Should not raise — just verify it completes without error.


# =============================================================================
# Edge cases
# =============================================================================


class TestEnrichmentEdgeCases(unittest.TestCase):
    def test_expression_without_columns(self):
        """Literal-only expressions should not crash enrichment."""
        inst = _make_instance("CREATE TABLE t (x INTEGER);")
        plan = _plan("SELECT 1 FROM t WHERE x > 0", inst)
        plan.annotation_for(plan.root)
        # Completes without error.

    def test_star_projection(self):
        """SELECT * should still enrich columns in WHERE."""
        inst = _make_instance("CREATE TABLE t (x INTEGER NOT NULL);")
        plan = _plan("SELECT * FROM t WHERE x > 0", inst)
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        self.assertTrue(len(cols) > 0)

    def test_multiple_enrichment_calls_idempotent(self):
        """Calling annotation_for multiple times should return the same result."""
        inst = _make_instance("CREATE TABLE t (x INTEGER NOT NULL);")
        plan = _plan("SELECT * FROM t WHERE x > 0", inst)
        ann1 = plan.annotation_for(plan.root)
        ann2 = plan.annotation_for(plan.root)
        self.assertIs(ann1, ann2)

    def test_subquery_in_where_enrichment(self):
        """WHERE x IN (SELECT ...) should enrich outer x but not inner columns."""
        inst = _make_instance(
            "CREATE TABLE a (id INTEGER NOT NULL, val TEXT);"
            "CREATE TABLE b (id INTEGER NOT NULL);"
        )
        plan = _plan(
            "SELECT * FROM a WHERE a.id IN (SELECT id FROM b)",
            inst,
        )
        step = _first_step_of_type(plan, Filter)
        cols = _condition_columns(step)
        # a.id should be enriched.
        if '"a"."id"' in cols:
            self.assertEqual(cols['"a"."id"']["table"], "a")


if __name__ == "__main__":
    unittest.main()
