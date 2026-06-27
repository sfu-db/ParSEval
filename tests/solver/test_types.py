"""Tests for solver shared types: ValueSpace, CSPVariable, CSPConstraint, ColumnPredicate."""
import unittest

from sqlglot import exp

from parseval.dtype import DataType
from parseval.identity import (
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.solver.types import (
    ValueSpace, CSPVariable, CSPConstraint, ColumnPredicate, SolverVar, TypeFamily,
    col_type, set_solver_var, solver_var, type_family,
)


REL = relation_id(RelationKind.TABLE, identifier_name("t1"))
COL_AGE = column_id(ColumnKind.PHYSICAL, identifier_name("age"), REL)
COL_ID = column_id(ColumnKind.PHYSICAL, identifier_name("id"), REL)
AGE = SolverVar(COL_AGE, REL)
ID = SolverVar(COL_ID, REL)


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
        cp = ColumnPredicate(variable=AGE, op=">", value=18)
        self.assertEqual(cp.variable, AGE)
        self.assertEqual(cp.op, ">")
        self.assertEqual(cp.value, 18)

    def test_equality_op(self):
        cp = ColumnPredicate(variable=AGE, op="=", value="alice")
        self.assertEqual(cp.op, "=")
        self.assertEqual(cp.value, "alice")


class TestCSPVariable(unittest.TestCase):
    def test_basic_fields(self):
        vs = ValueSpace(family=TypeFamily.TEXT)
        v = CSPVariable(variable=AGE, space=vs)
        self.assertEqual(v.name, AGE)
        self.assertEqual(v.variable, AGE)
        self.assertIsNone(v.assigned)

    def test_assigned_default(self):
        vs = ValueSpace(family=TypeFamily.INTEGER)
        v = CSPVariable(variable=AGE, space=vs)
        self.assertIsNone(v.assigned)


class TestCSPConstraint(unittest.TestCase):
    def test_basic_fields(self):
        c = CSPConstraint(kind="eq", left=AGE, right=ID)
        self.assertEqual(c.kind, "eq")
        self.assertEqual(c.left, AGE)
        self.assertEqual(c.right, ID)


class TestSolverVarMetadata(unittest.TestCase):
    def test_round_trips_on_column_meta(self):
        col = exp.column("age", table="t1")

        set_solver_var(col, AGE)

        self.assertEqual(solver_var(col), AGE)

    def test_solver_var_identity_uses_binding_scope_and_normalized_column_name(self):
        upper = column_id(ColumnKind.PHYSICAL, identifier_name("CDSCode"), REL)
        lower = column_id(ColumnKind.PHYSICAL, identifier_name("cdscode"), REL)
        left_rel = relation_id(
            RelationKind.TABLE,
            identifier_name("people"),
            alias=identifier_name("p"),
            scope_id="left",
        )
        right_rel = relation_id(
            RelationKind.TABLE,
            identifier_name("people"),
            alias=identifier_name("p"),
            scope_id="right",
        )

        self.assertEqual(SolverVar(upper, REL, "r0"), SolverVar(lower, REL, "r0"))
        self.assertNotEqual(SolverVar(upper, left_rel, "r0"), SolverVar(upper, right_rel, "r0"))
        self.assertNotEqual(SolverVar(upper, REL, "r0"), SolverVar(upper, REL, "r1"))

    def test_projected_alias_names_remain_distinct_even_with_same_source(self):
        source = column_id(ColumnKind.PHYSICAL, identifier_name("name"), REL)
        first = column_id(
            ColumnKind.PROJECTED,
            identifier_name("first_name"),
            REL,
            source_column_id=source,
        )
        second = column_id(
            ColumnKind.PROJECTED,
            identifier_name("second_name"),
            REL,
            source_column_id=source,
        )

        self.assertNotEqual(SolverVar(first, REL, "r0"), SolverVar(second, REL, "r0"))


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
