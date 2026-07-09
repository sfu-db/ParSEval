import unittest

from parseval.domain.spec import ColumnSpec, ForeignKeySpec, SchemaSpec, TableSpec
from parseval.domain.state import SchemaRuntime
from parseval.dtype import DataType
from parseval.identity import ColumnId, RelationId


class RuntimeTests(unittest.TestCase):
    def test_referenced_key_tuples_returns_parent_tuples_for_composite_fk(self):
        parent_a = ColumnSpec(table="parents", column="a", datatype=DataType.build("INT"))
        parent_b = ColumnSpec(table="parents", column="b", datatype=DataType.build("TEXT"))
        fk = ForeignKeySpec(
            source_table="children",
            source_columns=("a", "b"),
            target_table="parents",
            target_columns=("a", "b"),
        )
        child_a = ColumnSpec(table="children", column="a", datatype=DataType.build("INT"), foreign_key=fk)
        child_b = ColumnSpec(table="children", column="b", datatype=DataType.build("TEXT"), foreign_key=fk)
        schema = SchemaSpec(
            tables=(
                TableSpec(name="parents", columns=(parent_a, parent_b)),
                TableSpec(name="children", columns=(child_a, child_b)),
            )
        )
        runtime = SchemaRuntime(schema=schema)
        runtime.remember_row("parents", {"a": 1, "b": "x"})
        runtime.remember_row("parents", {"a": 2, "b": "y"})

        tuples = runtime.referenced_key_tuples(
            schema.get_table("children").foreign_keys[0]
        )

        self.assertEqual(tuples, [(1, "x"), (2, "y")])

    def test_runtime_state_is_keyed_by_relation_and_column_identity(self):
        user_id = ColumnSpec(
            table="users",
            column="id",
            datatype=DataType.build("INT"),
            primary_key=True,
        )
        schema = SchemaSpec(
            tables=(TableSpec(name="users", columns=(user_id,), primary_key=("id",)),)
        )
        table = schema.get_table("users")
        column = table.get_column("id")
        runtime = SchemaRuntime(schema=schema)

        runtime.remember_row(table, {column.id: 1})

        self.assertIn(table.id, runtime.tables)
        self.assertIn(column.id, runtime.columns)
        self.assertIsInstance(next(iter(runtime.tables)), RelationId)
        self.assertIsInstance(next(iter(runtime.columns)), ColumnId)
        self.assertEqual(runtime.table_state(table.id).rows[0][column.id], 1)
        self.assertEqual(runtime.column_state(column.id).used_values, {1})

    def test_mysql_text_used_values_are_storage_keys(self):
        user_id = ColumnSpec(
            table="users",
            column="name",
            datatype=DataType.build("VARCHAR(10)", dialect="mysql"),
            dialect="mysql",
            primary_key=True,
        )
        schema = SchemaSpec(
            tables=(TableSpec(name="users", columns=(user_id,), primary_key=("name",)),),
            dialect="mysql",
        )
        table = schema.get_table("users")
        column = table.get_column("name")
        runtime = SchemaRuntime(schema=schema)

        runtime.remember_row(table, {column.id: "C"})

        self.assertIn("c", runtime.column_state(column.id).used_values)


if __name__ == "__main__":
    unittest.main()
