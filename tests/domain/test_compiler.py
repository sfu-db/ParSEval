import unittest
from datetime import date
import uuid
from parseval.domain.compiler import ConstraintCompiler
from parseval.domain.spec import ColumnSpec
from parseval.domain.constraints import (
    ChoicesConstraint,
    RangeConstraint,
    LengthConstraint,
    CheckConstraint,
)

from parseval.domain.exceptions import ConstraintConflict
from parseval.dtype import DataType

class TestCompiler(unittest.TestCase):
    def setUp(self):
        self.compiler = ConstraintCompiler()

    def test_compile_choices(self):
        spec = ColumnSpec(
            table="t", column="c", datatype=DataType.build("TEXT"),
            checks=(ChoicesConstraint(values=("A", "B", "C")),)
        )
        plan = self.compiler.compile(spec)
        self.assertEqual(plan.allowed_values, ("A", "B", "C"))

    def test_compile_enum_and_choices_intersection(self):
        spec = ColumnSpec(
            table="t", column="c", datatype=DataType.build("ENUM('A', 'B')"),
            checks=(ChoicesConstraint(values=("B", "C")),)
        )
        plan = self.compiler.compile(spec)
        self.assertEqual(plan.allowed_values, ("B",))

    def test_compile_empty_choices_conflict(self):
        spec = ColumnSpec(
            table="t", column="c", datatype=DataType.build("ENUM('A', 'B')"),
            checks=(ChoicesConstraint(values=("C", "D")),)
        )
        with self.assertRaises(ConstraintConflict):
            self.compiler.compile(spec)

    def test_compile_range_intersection(self):
        spec = ColumnSpec(
            table="t", column="c", datatype=DataType.build("INT"),
            checks=(
                RangeConstraint(minimum=10, maximum=100),
                RangeConstraint(minimum=20, maximum=50),
            )
        )
        plan = self.compiler.compile(spec)
        self.assertEqual(plan.minimum, 20)
        self.assertEqual(plan.maximum, 50)

    def test_compile_contradictory_range(self):
        spec = ColumnSpec(
            table="t", column="c", datatype=DataType.build("INT"),
            checks=(RangeConstraint(minimum=100, maximum=50),)
        )
        with self.assertRaises(ConstraintConflict):
            self.compiler.compile(spec)

    def test_compile_length_intersection(self):
        spec = ColumnSpec(
            table="t", column="c", datatype=DataType.build("VARCHAR(100)"),
            checks=(LengthConstraint(minimum=10, maximum=50),)
        )
        plan = self.compiler.compile(spec)
        self.assertEqual(plan.minimum_length, 10)
        self.assertEqual(plan.maximum_length, 50)

    def test_compile_length_datatype_limit(self):
        spec = ColumnSpec(
            table="t", column="c", datatype=DataType.build("VARCHAR(20)"),
            checks=(LengthConstraint(minimum=10, maximum=50),)
        )
        plan = self.compiler.compile(spec)
        self.assertEqual(plan.minimum_length, 10)
        self.assertEqual(plan.maximum_length, 20)

    def test_compile_residual_predicates(self):
        fn = lambda x: x % 2 == 0
        spec = ColumnSpec(
            table="t", column="c", datatype=DataType.build("INT"),
            checks=(CheckConstraint(expression=fn),)
        )
        plan = self.compiler.compile(spec)
        self.assertIn(fn, plan.residual_predicates)

    def test_compile_choices_preserves_non_orderable_values(self):
        first = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
        second = date(2020, 1, 1)
        spec = ColumnSpec(
            table="t",
            column="c",
            datatype=DataType.build("TEXT"),
            checks=(ChoicesConstraint(values=(first, second)),),
        )
        plan = self.compiler.compile(spec)
        self.assertEqual(plan.allowed_values, (first, second))

def test_intersect_preserving_order_method():
    """_intersect_preserving_order must be a method on ConstraintCompiler."""
    from parseval.domain.compiler import ConstraintCompiler
    compiler = ConstraintCompiler.__new__(ConstraintCompiler)
    result = compiler._intersect_preserving_order([1, 2, 3], [2, 3, 4])
    assert result == (2, 3)

if __name__ == "__main__":
    unittest.main()
