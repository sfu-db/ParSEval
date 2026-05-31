"""Tests for solver shared types: ValueSpace, CSPVariable, CSPConstraint, ColumnPredicate."""
import unittest

from sqlglot import exp

from parseval.dtype import DataType
from parseval.solver.types import (
    ValueSpace, CSPVariable, CSPConstraint, ColumnPredicate, TypeFamily,
    col_type, type_family,
)


class TestValueSpaceInitial(unittest.TestCase):
    def test_not_empty_by_default(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        self.assertFalse(vs.is_empty())

    def test_pick_returns_non_none(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        self.assertIsNotNone(vs.pick())


class TestValueSpaceNarrowEq(unittest.TestCase):
    def test_pick_after_eq(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.narrow_eq(42)
        self.assertEqual(vs.pick(), 42)

    def test_eq_excluded_by_not_equals(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.narrow_eq(42)
        vs.narrow_neq(42)
        self.assertTrue(vs.is_empty())

    def test_eq_outside_range(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.narrow_min(10)
        vs.narrow_max(20)
        vs.narrow_eq(5)
        self.assertTrue(vs.is_empty())


class TestValueSpaceRange(unittest.TestCase):
    def test_pick_within_range(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.narrow_min(10)
        vs.narrow_max(20)
        val = vs.pick()
        self.assertGreaterEqual(val, 10)
        self.assertLessEqual(val, 20)

    def test_inverted_range_is_empty(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.narrow_min(20)
        vs.narrow_max(10)
        self.assertTrue(vs.is_empty())


class TestValueSpaceEmpty(unittest.TestCase):
    def test_must_null_and_not_null(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.must_null = True
        vs.not_null = True
        self.assertTrue(vs.is_empty())


class TestValueSpaceMustNull(unittest.TestCase):
    def test_pick_returns_none_when_must_null(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.must_null = True
        self.assertIsNone(vs.pick())

    def test_not_empty_when_only_must_null(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.must_null = True
        self.assertFalse(vs.is_empty())


class TestValueSpaceNarrowIn(unittest.TestCase):
    def test_allowed_set_intersection(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.narrow_in({1, 2, 3})
        vs.narrow_in({2, 3, 4})
        self.assertEqual(vs.allowed, {2, 3})

    def test_allowed_all_excluded(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        vs.narrow_in({1, 2})
        vs.narrow_neq(1)
        vs.narrow_neq(2)
        self.assertTrue(vs.is_empty())


class TestValueSpaceText(unittest.TestCase):
    def test_pick_text_default(self):
        vs = ValueSpace(family=TypeFamily.TEXT)
        val = vs.pick()
        self.assertIsInstance(val, str)
        self.assertTrue(len(val) > 0)

    def test_pick_text_like_pattern(self):
        vs = ValueSpace(family=TypeFamily.TEXT)
        vs.like_pattern = "ab%"
        val = vs.pick()
        self.assertTrue(val.startswith("ab"))


class TestValueSpaceBoolean(unittest.TestCase):
    def test_pick_boolean_default(self):
        vs = ValueSpace(family=TypeFamily.BOOLEAN)
        val = vs.pick()
        self.assertIn(val, (True, False))

    def test_pick_boolean_returns_none_when_exhausted(self):
        vs = ValueSpace(family=TypeFamily.BOOLEAN)
        vs.narrow_neq(True)
        vs.narrow_neq(False)
        self.assertTrue(vs.is_empty())
        self.assertIsNone(vs.pick())


class TestColumnPredicate(unittest.TestCase):
    def test_basic_fields(self):
        cp = ColumnPredicate(table="t1", column="age", op=">", value=18)
        self.assertEqual(cp.table, "t1")
        self.assertEqual(cp.column, "age")
        self.assertEqual(cp.op, ">")
        self.assertEqual(cp.value, 18)

    def test_equality_op(self):
        cp = ColumnPredicate(table="t1", column="name", op="=", value="alice")
        self.assertEqual(cp.op, "=")
        self.assertEqual(cp.value, "alice")


class TestCSPVariable(unittest.TestCase):
    def test_basic_fields(self):
        vs = ValueSpace(family=TypeFamily.TEXT)
        v = CSPVariable(name="t1.name", table="t1", column="name", space=vs)
        self.assertEqual(v.name, "t1.name")
        self.assertEqual(v.table, "t1")
        self.assertEqual(v.column, "name")
        self.assertIsNone(v.assigned)

    def test_assigned_default(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        v = CSPVariable(name="t1.age", table="t1", column="age", space=vs)
        self.assertIsNone(v.assigned)


class TestCSPConstraint(unittest.TestCase):
    def test_basic_fields(self):
        c = CSPConstraint(kind="eq", left="t1.id", right="t2.t1_id")
        self.assertEqual(c.kind, "eq")
        self.assertEqual(c.left, "t1.id")
        self.assertEqual(c.right, "t2.t1_id")


class TestColType(unittest.TestCase):
    def test_returns_none_when_no_type_annotation(self):
        col = exp.column("age")
        self.assertIsNone(col_type(col))

    def test_returns_datatype_when_annotated(self):
        col = exp.column("age")
        col.type = DataType.build("INT")
        result = col_type(col)
        self.assertIsInstance(result, DataType)
        self.assertTrue(result.is_type(*DataType.INTEGER_TYPES))

    def test_handles_string_type_annotation(self):
        col = exp.column("name")
        col.type = "VARCHAR"
        result = col_type(col)
        self.assertIsInstance(result, DataType)

    def test_returns_none_for_unbuildable_type(self):
        col = exp.column("x")
        # Bypass the property setter by writing directly to __dict__
        object.__setattr__(col, "_type", object())
        result = col_type(col)
        self.assertIsNone(result)


class TestTypeFamily(unittest.TestCase):
    def test_integer_types(self):
        for t in ("TINYINT", "SMALLINT", "INT", "BIGINT", "INTEGER"):
            dtype = DataType.build(t)
            self.assertEqual(type_family(dtype), TypeFamily.INTEGER, msg=t)

    def test_decimal_types(self):
        for t in ("FLOAT", "DOUBLE", "DECIMAL", "REAL"):
            dtype = DataType.build(t)
            self.assertEqual(type_family(dtype), TypeFamily.DECIMAL, msg=t)

    def test_boolean(self):
        dtype = DataType.build("BOOLEAN")
        self.assertEqual(type_family(dtype), TypeFamily.BOOLEAN)

    def test_date(self):
        dtype = DataType.build("DATE")
        self.assertEqual(type_family(dtype), TypeFamily.DATE)

    def test_time(self):
        dtype = DataType.build("TIME")
        self.assertEqual(type_family(dtype), TypeFamily.TIME)

    def test_datetime(self):
        dtype = DataType.build("TIMESTAMP")
        self.assertEqual(type_family(dtype), TypeFamily.DATETIME)

    def test_text_fallback(self):
        dtype = DataType.build("TEXT")
        self.assertEqual(type_family(dtype), TypeFamily.TEXT)

    def test_varchar_is_text(self):
        dtype = DataType.build("VARCHAR")
        self.assertEqual(type_family(dtype), TypeFamily.TEXT)


if __name__ == "__main__":
    unittest.main()
