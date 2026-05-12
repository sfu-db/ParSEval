import unittest

from parseval.domain.spec import ColumnSpec, ForeignKeySpec, SchemaSpec, TableSpec
from parseval.domain.state import SchemaRuntime
from parseval.dtype import DataType


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

        tuples = runtime.referenced_key_tuples(fk)

        self.assertEqual(tuples, [(1, "x"), (2, "y")])


if __name__ == "__main__":
    unittest.main()
