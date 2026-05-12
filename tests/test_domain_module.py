import unittest
from datetime import date, datetime

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
from parseval.domain.spec import ForeignKeySpec
from parseval.domain.state import RowContext, SchemaRuntime
from parseval.domain.providers import ProviderRegistry, SemanticProvider
from parseval.dtype import DataType


class DomainModuleTests(unittest.TestCase):
    def test_public_state_types_are_available_from_state_module(self):
        self.assertIsNotNone(RowContext)
        self.assertIsNotNone(SchemaRuntime)

    def test_build_policy_null_rate_generates_null_for_nullable_columns(self):
        optional_text = ColumnSpec(
            table="items",
            column="note",
            datatype=DataType.build("TEXT"),
            nullable=True,
        )
        schema = SchemaSpec(tables=(TableSpec(name="items", columns=(optional_text,)),))
        builder = DatabaseBuilder(schema=schema, seed=1)

        result = builder.build(BuildPolicy(row_counts={"items": 25}, null_rate=1.0))

        self.assertTrue(any(row["note"] is None for row in result["items"]))

    def test_builder_generates_foreign_key_values_from_parent_domain(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("INT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)
        result = builder.build(
            BuildPolicy(row_counts={"parents": 2, "children": 3}, default_row_count=0)
        )

        parent_values = {row["id"] for row in result["parents"]}
        child_values = {row["parent_id"] for row in result["children"]}
        self.assertTrue(child_values.issubset(parent_values))

    def test_build_rejects_child_before_parent_order_for_foreign_key_generation(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("INT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="children", columns=(child_parent_id,)),
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
            )
        )
        builder = DatabaseBuilder(schema)

        with self.assertRaises(ForeignKeyResolutionError):
            builder.build(
                BuildPolicy(
                    row_counts={"children": 1, "parents": 1},
                    default_row_count=0,
                )
            )

    def test_builder_coerces_generated_foreign_key_to_child_datatype(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("TEXT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"id": 1})

        row = builder.complete_row("children")

        self.assertEqual(row["parent_id"], "1")
        self.assertIsInstance(row["parent_id"], str)

    def test_complete_row_accepts_foreign_key_after_cross_type_coercion(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("TEXT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"id": 1})

        row = builder.complete_row("children", {"parent_id": "1"})

        self.assertEqual(row["parent_id"], "1")

    def test_complete_row_rejects_explicit_foreign_key_when_parent_not_generated(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("INT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)

        with self.assertRaises(ForeignKeyResolutionError):
            builder.complete_row("children", {"parent_id": 1})

    def test_build_rejects_composite_foreign_key_generation_gap(self):
        parent_a = ColumnSpec(
            table="parents",
            column="part_a",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
        )
        parent_b = ColumnSpec(
            table="parents",
            column="part_b",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
        )
        child_a = ColumnSpec(
            table="children",
            column="part_a",
            datatype=DataType.build("INT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("part_a", "part_b"),
                target_table="parents",
                target_columns=("part_a", "part_b"),
            ),
        )
        child_b = ColumnSpec(
            table="children",
            column="part_b",
            datatype=DataType.build("INT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("part_a", "part_b"),
                target_table="parents",
                target_columns=("part_a", "part_b"),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(
                    name="parents",
                    columns=(parent_a, parent_b),
                    unique_constraints=(("part_a", "part_b"),),
                ),
                TableSpec(name="children", columns=(child_a, child_b)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"part_a": 1, "part_b": 2})

        row = builder.complete_row("children")
        self.assertEqual(row["part_a"], 1)
        self.assertEqual(row["part_b"], 2)

    def test_complete_row_accepts_existing_composite_foreign_key_tuple(self):
        parent_a = ColumnSpec(table="parents", column="part_a", datatype=DataType.build("INT"), nullable=False)
        parent_b = ColumnSpec(table="parents", column="part_b", datatype=DataType.build("TEXT"), nullable=False)
        fk = ForeignKeySpec(
            source_table="children",
            source_columns=("part_a", "part_b"),
            target_table="parents",
            target_columns=("part_a", "part_b"),
        )
        child_a = ColumnSpec(table="children", column="part_a", datatype=DataType.build("INT"), nullable=False, foreign_key=fk)
        child_b = ColumnSpec(table="children", column="part_b", datatype=DataType.build("TEXT"), nullable=False, foreign_key=fk)
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_a, parent_b)),
                TableSpec(name="children", columns=(child_a, child_b)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"part_a": 1, "part_b": "x"})

        row = builder.complete_row("children", {"part_a": 1, "part_b": "x"})

        self.assertEqual((row["part_a"], row["part_b"]), (1, "x"))

    def test_complete_row_rejects_missing_composite_foreign_key_tuple(self):
        parent_a = ColumnSpec(table="parents", column="part_a", datatype=DataType.build("INT"), nullable=False)
        parent_b = ColumnSpec(table="parents", column="part_b", datatype=DataType.build("TEXT"), nullable=False)
        fk = ForeignKeySpec(
            source_table="children",
            source_columns=("part_a", "part_b"),
            target_table="parents",
            target_columns=("part_a", "part_b"),
        )
        child_a = ColumnSpec(table="children", column="part_a", datatype=DataType.build("INT"), nullable=False, foreign_key=fk)
        child_b = ColumnSpec(table="children", column="part_b", datatype=DataType.build("TEXT"), nullable=False, foreign_key=fk)
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_a, parent_b)),
                TableSpec(name="children", columns=(child_a, child_b)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"part_a": 1, "part_b": "x"})

        with self.assertRaises(ForeignKeyResolutionError):
            builder.complete_row("children", {"part_a": 1, "part_b": "missing"})

    def test_builder_coerces_text_parent_foreign_key_to_integer_child(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("TEXT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("INT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"id": "7"})

        row = builder.complete_row("children")

        self.assertEqual(row["parent_id"], 7)
        self.assertIsInstance(row["parent_id"], int)

    def test_builder_coerces_date_parent_to_datetime_child_foreign_key(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("DATE"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("DATETIME"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"id": date(2020, 1, 2)})

        row = builder.complete_row("children")

        self.assertEqual(row["parent_id"], datetime(2020, 1, 2, 0, 0, 0))
        self.assertIsInstance(row["parent_id"], datetime)

    def test_complete_row_accepts_datetime_foreign_key_for_date_parent(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("DATE"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("DATETIME"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"id": date(2020, 1, 2)})

        row = builder.complete_row("children", {"parent_id": "2020-01-02 00:00:00"})

        self.assertEqual(row["parent_id"], datetime(2020, 1, 2, 0, 0, 0))

    def test_complete_row_rejects_non_coercible_foreign_key_value(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("DATE"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"id": 1})

        with self.assertRaises(ForeignKeyResolutionError):
            builder.complete_row("children", {"parent_id": "2020-01-02"})

    def test_builder_coerces_integer_parent_to_boolean_child_foreign_key(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("BOOLEAN"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"id": 1})

        row = builder.complete_row("children")

        self.assertEqual(row["parent_id"], True)
        self.assertIsInstance(row["parent_id"], bool)

    def test_semantic_provider_can_override_builtin_provider(self):
        email = ColumnSpec(
            table="users",
            column="email",
            datatype=DataType.build("TEXT"),
            nullable=False,
            semantic_tags=("email",),
        )
        schema = SchemaSpec(tables=(TableSpec(name="users", columns=(email,)),))
        registry = ProviderRegistry.with_builtin_providers()
        registry.register_semantic(
            "email",
            SemanticProvider("email", lambda **_kwargs: "person@example.com"),
        )
        builder = DatabaseBuilder(schema=schema, registry=registry)

        row = builder.generate_row("users")
        self.assertEqual(row["email"], "person@example.com")

    def test_complete_row_preserves_preset_values_and_generates_rest(self):
        identifier = ColumnSpec(
            table="users",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        email = ColumnSpec(
            table="users",
            column="email",
            datatype=DataType.build("TEXT"),
            nullable=False,
        )
        schema = SchemaSpec(
            tables=(TableSpec(name="users", columns=(identifier, email), primary_key=("id",)),)
        )
        builder = DatabaseBuilder(schema=schema)

        row = builder.complete_row("users", {"id": 7})

        self.assertEqual(row["id"], 7)
        self.assertIn("email", row)
        self.assertEqual(builder.runtime.table_state("users").rows[0]["id"], 7)

    def test_complete_row_rejects_duplicate_unique_preset(self):
        identifier = ColumnSpec(
            table="users",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        schema = SchemaSpec(
            tables=(TableSpec(name="users", columns=(identifier,), primary_key=("id",)),)
        )
        builder = DatabaseBuilder(schema=schema)
        builder.complete_row("users", {"id": 1})

        with self.assertRaises(UniqueConflictError):
            builder.complete_row("users", {"id": 1})

    def test_complete_row_rejects_unknown_foreign_key_value_when_parent_exists(self):
        parent_id = ColumnSpec(
            table="parents",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
            unique=True,
            primary_key=True,
        )
        child_parent_id = ColumnSpec(
            table="children",
            column="parent_id",
            datatype=DataType.build("INT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="children",
                source_columns=("parent_id",),
                target_table="parents",
                target_columns=("id",),
            ),
        )
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_id,), primary_key=("id",)),
                TableSpec(name="children", columns=(child_parent_id,)),
            )
        )
        builder = DatabaseBuilder(schema)
        builder.complete_row("parents", {"id": 1})

        with self.assertRaises(ForeignKeyResolutionError):
            builder.complete_row("children", {"parent_id": 999})

    def test_complete_row_rejects_null_for_not_nullable_preset(self):
        identifier = ColumnSpec(
            table="users",
            column="id",
            datatype=DataType.build("INT"),
            nullable=False,
        )
        schema = SchemaSpec(tables=(TableSpec(name="users", columns=(identifier,)),))
        builder = DatabaseBuilder(schema=schema)

        with self.assertRaises(ConstraintViolationError):
            builder.complete_row("users", {"id": None})


if __name__ == "__main__":
    unittest.main()
