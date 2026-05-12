import unittest

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
            datatype=DataType.build("VARCHAR(64)"),
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

        parent_values = {str(row["id"]) for row in result["parents"]}
        child_values = {str(row["parent_id"]) for row in result["children"]}
        # print(parent_values)
        # print(f"child values: {child_values}")
        self.assertTrue(child_values.issubset(parent_values))

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
