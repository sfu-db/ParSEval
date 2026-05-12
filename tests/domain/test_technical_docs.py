import unittest
from datetime import date
from decimal import Decimal
import uuid

from parseval.domain import (
    BuildPolicy,
    ColumnSpec,
    ConstraintViolationError,
    DatabaseBuilder,
    ForeignKeyResolutionError,
    SchemaSpec,
    TableSpec,
    UniqueConflictError,
)
from parseval.domain.constraints import (
    CheckConstraint,
    ChoicesConstraint,
    ContainsConstraint,
    LengthConstraint,
    ModuloConstraint,
    PatternConstraint,
    PrefixConstraint,
    RangeConstraint,
    SuffixConstraint,
)
from parseval.domain.providers import ValueProvider
from parseval.domain.spec import ForeignKeySpec
from parseval.dtype import DataType


class DomainTechnicalUseCasesTests(unittest.TestCase):
    """
    Unit tests for technical use cases described in docs/domain.md.
    Ensures that documentation and implementation are in sync.
    """

    def test_constraint_intersection_range(self):
        # Doc 1.2: Intersecting [0, 100] and [50, 150] -> [50, 100]
        col = ColumnSpec(
            table="t", column="c", datatype=DataType.build("INT"),
            checks=[
                RangeConstraint(minimum=0, maximum=100),
                RangeConstraint(minimum=50, maximum=150)
            ]
        )
        builder = DatabaseBuilder(SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),)))
        plan = builder._get_plan(col)
        
        self.assertEqual(plan.minimum, 50)
        self.assertEqual(plan.maximum, 100)
        
        # Validate values
        builder.validator.validate(plan, 75)
        with self.assertRaises(ConstraintViolationError):
            builder.validator.validate(plan, 49)
        with self.assertRaises(ConstraintViolationError):
            builder.validator.validate(plan, 101)

    def test_pattern_merging(self):
        # Doc 1.3: Pattern, Prefix, Suffix, Contains merging
        col = ColumnSpec(
            table="t", column="c", datatype=DataType.build("TEXT"),
            checks=[
                PrefixConstraint(prefix="START_"),
                SuffixConstraint(suffix="_END"),
                ContainsConstraint(substring="MIDDLE")
            ]
        )
        builder = DatabaseBuilder(SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),)))
        plan = builder._get_plan(col)
        
        self.assertEqual(plan.prefix, "START_")
        self.assertEqual(plan.suffix, "_END")
        self.assertIn("MIDDLE", plan.contains)
        
        builder.validator.validate(plan, "START_MIDDLE_END")
        with self.assertRaises(ConstraintViolationError):
            builder.validator.validate(plan, "START_MIDDLE")
        with self.assertRaises(ConstraintViolationError):
            builder.validator.validate(plan, "MIDDLE_END")

    def test_provider_resolution_priority(self):
        # Doc 2.1: Selection Priority
        class CustomProvider(ValueProvider):
            def supports(self, spec, profile): return 0
            def generate(self, *args, **kwargs): return "CUSTOM"

        col = ColumnSpec(table="users", column="email", datatype=DataType.build("TEXT"), semantic_tags=("email",))
        schema = SchemaSpec(tables=(TableSpec(name="users", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        # 1. Type match by default (StringProvider)
        provider = builder.registry.resolve(col)
        self.assertEqual(provider.__class__.__name__, "StringProvider")
        
        # 2. Semantic Tag Match
        builder.registry.register_semantic("email", CustomProvider())
        provider = builder.registry.resolve(col)
        self.assertEqual(provider.__class__.__name__, "CustomProvider")
        
        # 3. Direct Column Override
        class OverrideProvider(ValueProvider):
            def supports(self, spec, profile): return 0
            def generate(self, *args, **kwargs): return "OVERRIDE"
            
        builder.registry.register_column("users.email", OverrideProvider())
        provider = builder.registry.resolve(col)
        self.assertEqual(provider.__class__.__name__, "OverrideProvider")

    def test_uniqueness_retry_logic(self):
        # Doc 3.2: Unique Conflict Resolution
        col = ColumnSpec(table="t", column="c", datatype=DataType.build("INT"), unique=True,
                         checks=[ChoicesConstraint(values=(1, 2))])
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        # First row uses '1'
        builder.complete_row("t", {"c": 1})
        
        # Second row MUST use '2' even if the provider might randomly pick '1' first
        # (The builder handles retries)
        row2 = builder.generate_row("t")
        self.assertEqual(row2["c"], 2)
        
        # Third row should fail because no allowed values remain
        with self.assertRaises(UniqueConflictError):
            builder.generate_row("t")

    def test_custom_check_constraint_residual(self):
        # Doc 5.2: Implementing Custom Constraints via residual predicates
        col = ColumnSpec(
            table="t", column="even", datatype=DataType.build("INT"),
            checks=[CheckConstraint(lambda x: x % 2 == 0)]
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        # generate_row should eventually find an even number if provider is random
        # IntegerProvider is random, so we might need multiple tries or just validate the plan
        plan = builder._get_plan(col)
        self.assertTrue(len(plan.residual_predicates) > 0)
        
        builder.validator.validate(plan, 2)
        builder.validator.validate(plan, 42)
        with self.assertRaises(ConstraintViolationError):
            builder.validator.validate(plan, 3)

    def test_type_coercion_numeric(self):
        # Doc 4.1: Numeric Coercion
        col = ColumnSpec(table="t", column="c", datatype=DataType.build("DECIMAL(10,2)"))
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        # Provide a string, it should be coerced to Decimal (or float depending on adapter)
        row = builder.complete_row("t", {"c": "123.45"})
        self.assertIsInstance(row["c"], (Decimal, float))

    def test_semantic_provider_extension(self):
        # Doc 5.1: Creating a Semantic Provider
        class PhoneNumberProvider(ValueProvider):
            priority = 100
            def supports(self, spec, profile):
                return 100 if "phone" in spec.column.lower() else 0
            def generate(self, spec, runtime, row_context, **kwargs):
                return "555-0199"

        # Use semantic_tags to match register_semantic
        col = ColumnSpec(
            table="t", column="phone_number", 
            datatype=DataType.build("TEXT"),
            semantic_tags=("phone_number",)
        )
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        builder.registry.register_semantic("phone_number", PhoneNumberProvider())
        row = builder.generate_row("t")
        self.assertEqual(row["phone_number"], "555-0199")

    def test_heuristic_provider_registration(self):
        # Doc 5.1: General Registration (Type-based)
        class MyHeuristicProvider(ValueProvider):
            priority = 100
            def supports(self, spec, profile):
                return 100 if "heuristic" in spec.column.lower() else 0
            def generate(self, spec, runtime, row_context, **kwargs):
                return "HEURISTIC_MATCH"

        col = ColumnSpec(table="t", column="my_heuristic_col", datatype=DataType.build("TEXT"))
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        builder.registry.register(MyHeuristicProvider())
        row = builder.generate_row("t")
        self.assertEqual(row["my_heuristic_col"], "HEURISTIC_MATCH")

    def test_modulo_constraint(self):
        # Doc 1.3: Modulo divisor and remainder
        col = ColumnSpec(
            table="t", column="c", datatype=DataType.build("INT"),
            checks=[ModuloConstraint(divisor=10, remainder=3)]
        )
        builder = DatabaseBuilder(SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),)))
        plan = builder._get_plan(col)
        
        self.assertEqual(plan.modulo_divisor, 10)
        self.assertEqual(plan.modulo_remainder, 3)
        
        builder.validator.validate(plan, 3)
        builder.validator.validate(plan, 13)
        builder.validator.validate(plan, 23)
        with self.assertRaises(ConstraintViolationError):
            builder.validator.validate(plan, 4)

    def test_composite_foreign_key_satisfaction(self):
        # Doc 3.2: Composite Foreign Keys
        parent_cols = (
            ColumnSpec(table="parent", column="pk1", datatype=DataType.build("INT"), primary_key=True),
            ColumnSpec(table="parent", column="pk2", datatype=DataType.build("INT"), primary_key=True),
        )
        child_cols = (
            ColumnSpec(table="child", column="id", datatype=DataType.build("INT"), primary_key=True),
            ColumnSpec(
                table="child", column="ref1", datatype=DataType.build("INT"),
                foreign_key=ForeignKeySpec(
                    source_table="child", source_columns=("ref1", "ref2"),
                    target_table="parent", target_columns=("pk1", "pk2")
                )
            ),
            ColumnSpec(table="child", column="ref2", datatype=DataType.build("INT")),
        )
        
        schema = SchemaSpec(tables=(
            TableSpec(name="parent", columns=parent_cols, primary_key=("pk1", "pk2")),
            TableSpec(name="child", columns=child_cols, primary_key=("id",))
        ))
        
        builder = DatabaseBuilder(schema)
        # Create parent rows first
        builder.complete_row("parent", {"pk1": 10, "pk2": 20})
        builder.complete_row("parent", {"pk1": 30, "pk2": 40})
        
        # Generate child row - should sample one of the parent tuples
        row = builder.generate_row("child")
        
        # Verify that (ref1, ref2) matches one of the parent (pk1, pk2)
        parent_tuples = {(10, 20), (30, 40)}
        self.assertIn((row["ref1"], row["ref2"]), parent_tuples)

if __name__ == "__main__":
    unittest.main()
