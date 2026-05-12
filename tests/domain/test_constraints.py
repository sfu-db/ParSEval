import unittest
from parseval.domain import (
    ColumnSpec,
    UniqueConflictError,
    DatabaseBuilder,
    SchemaSpec,
    TableSpec,
)
from parseval.domain.constraints import (
    CheckConstraint,
    ChoicesConstraint,
    ContainsConstraint,
    ModuloConstraint,
    PrefixConstraint,
    RangeConstraint,
    SuffixConstraint,
)
from parseval.dtype import DataType


class ConstraintTests(unittest.TestCase):
    def test_check_constraint_validation(self):
        # Test if CheckConstraint (lambda) is enforced
        # We need a provider that can generate something that satisfies this,
        # or a validator that rejects if it doesn't.
        # For now, let's assume we want it to be enforced.
        col = ColumnSpec(
            table="t", 
            column="even_num", 
            datatype=DataType.build("INT"),
            checks=(CheckConstraint(expression=lambda x: x % 2 == 0),)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        # If we don't have a smart generator, we might need to retry.
        # But let's see if we can at least detect a violation.
        row = builder.generate_row("t")
        self.assertEqual(row["even_num"] % 2, 0, f"Value {row['even_num']} should be even")
    def test_choices_constraint_enforcement(self):
        # We want to see if the generator respects ChoicesConstraint
        choices = ("A", "B", "C")
        col = ColumnSpec(
            table="t", 
            column="status", 
            datatype=DataType.build("TEXT"),
            checks=(ChoicesConstraint(values=choices),)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        # Generate multiple rows
        for _ in range(50):
            row = builder.generate_row("t")
            self.assertIn(row["status"], choices, f"Value {row['status']} should be one of {choices}")

    def test_range_constraint_enforcement(self):
        # Test if RangeConstraint is respected for integers
        col = ColumnSpec(
            table="t", 
            column="age", 
            datatype=DataType.build("INT"),
            checks=(RangeConstraint(minimum=18, maximum=65),)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        for _ in range(50):
            row = builder.generate_row("t")
            self.assertTrue(18 <= row["age"] <= 65, f"Value {row['age']} should be between 18 and 65")

    def test_enum_type_enforcement(self):
        # Test if ENUM type values are respected
        col = ColumnSpec(
            table="t", 
            column="category", 
            datatype=DataType.build("ENUM('X', 'Y', 'Z')")
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        for _ in range(50):
            row = builder.generate_row("t")
            self.assertIn(row["category"], ("X", "Y", "Z"))

    def test_choices_uniqueness_exhaustion(self):
        # If unique=True and we have ChoicesConstraint with N values, N+1 should fail.
        choices = ("A", "B")
        col = ColumnSpec(
            table="t", 
            column="val", 
            datatype=DataType.build("TEXT"),
            unique=True,
            checks=(ChoicesConstraint(values=choices),)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        builder.generate_row("t")
        builder.generate_row("t")
        
        from parseval.domain.exceptions import UniqueConflictError
        with self.assertRaises(UniqueConflictError):
            builder.generate_row("t")

    def test_contradictory_range(self):
        # RangeConstraint(minimum=10, maximum=5)
        col = ColumnSpec(
            table="t", 
            column="val", 
            datatype=DataType.build("INT"),
            checks=(RangeConstraint(minimum=10, maximum=5),)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        from parseval.domain.exceptions import ConstraintConflict
        with self.assertRaises(ConstraintConflict):
            builder.generate_row("t")

    def test_length_constraint_enforcement(self):
        # Test if LengthConstraint is respected for strings
        from parseval.domain.constraints import LengthConstraint
        col = ColumnSpec(
            table="t", 
            column="name", 
            datatype=DataType.build("TEXT"),
            checks=(LengthConstraint(minimum=5, maximum=10),)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        # Now StringProvider supports LengthConstraint via domain_plan
        row = builder.generate_row("t")
        self.assertTrue(5 <= len(row["name"]) <= 10)

    def test_check_constraint_retry_exhaustion(self):
        # If lambda always returns False, it should fail after 10 retries.
        col = ColumnSpec(
            table="t", 
            column="val", 
            datatype=DataType.build("INT"),
            checks=(CheckConstraint(expression=lambda x: False),)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        from parseval.domain.exceptions import ConstraintViolationError
        with self.assertRaises(ConstraintViolationError):
            builder.generate_row("t")
        
    def test_enum_and_choices_interaction(self):
        # ENUM('A', 'B') + ChoicesConstraint(values=('B', 'C')) -> should intersect to ('B',)
        col = ColumnSpec(
            table="t", 
            column="val", 
            datatype=DataType.build("ENUM('A', 'B')"),
            checks=(ChoicesConstraint(values=("B", "C")),)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        row = builder.generate_row("t")
        self.assertEqual(row["val"], "B")

    def test_explicit_value_validation_against_constraints(self):
        # Test if preset_values are validated against RangeConstraint etc.
        from parseval.domain.exceptions import ConstraintViolationError
        col = ColumnSpec(
            table="t", 
            column="val", 
            datatype=DataType.build("INT"),
            checks=(RangeConstraint(minimum=10, maximum=20),)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        with self.assertRaises(ConstraintViolationError):
            builder.complete_row("t", preset_values={"val": 5})

    def test_generate_value_retries_residual_predicates_like_generate_row(self):
        attempts = {"count": 0}

        def predicate(value):
            attempts["count"] += 1
            return value % 2 == 0

        col = ColumnSpec(
            table="t",
            column="val",
            datatype=DataType.build("INT"),
            checks=(CheckConstraint(expression=predicate),),
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)

        value = builder.generate_value("t", "val")

        self.assertEqual(value % 2, 0)

    def test_unique_allowed_values_exhaustion_is_deterministic(self):
        choices = ("A", "B")
        col = ColumnSpec(
            table="t",
            column="val",
            datatype=DataType.build("TEXT"),
            unique=True,
            checks=(ChoicesConstraint(values=choices),),
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)

        first = builder.generate_row("t")["val"]
        second = builder.generate_row("t")["val"]

        self.assertEqual({first, second}, set(choices))
        with self.assertRaises(UniqueConflictError):
            builder.generate_row("t")

    def test_modulo_constraint_generates_structured_values(self):
        col = ColumnSpec(
            table="t",
            column="even_num",
            datatype=DataType.build("INT"),
            checks=(ModuloConstraint(divisor=2, remainder=0),),
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)

        for _ in range(10):
            row = builder.generate_row("t")
            self.assertEqual(row["even_num"] % 2, 0)

    def test_prefix_suffix_contains_constraints_generate_structured_values(self):
        col = ColumnSpec(
            table="t",
            column="name",
            datatype=DataType.build("TEXT"),
            checks=(
                PrefixConstraint(prefix="pre"),
                SuffixConstraint(suffix="suf"),
                ContainsConstraint(substring="mid"),
            ),
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)

        row = builder.generate_row("t")
        self.assertTrue(row["name"].startswith("pre"))
        self.assertTrue(row["name"].endswith("suf"))
        self.assertIn("mid", row["name"])



if __name__ == "__main__":
    unittest.main()
