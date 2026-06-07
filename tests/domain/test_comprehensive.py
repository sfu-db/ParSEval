import unittest
from datetime import date, datetime, time
from decimal import Decimal
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
from parseval.dtype import DataType


class ComprehensiveDomainTests(unittest.TestCase):
    def setUp(self):
        # Basic schema with various types
        self.basic_columns = (
            ColumnSpec(table="basic", column="id", datatype=DataType.build("INT"), primary_key=True),
            ColumnSpec(table="basic", column="name", datatype=DataType.build("TEXT"), length=50, nullable=False),
            ColumnSpec(table="basic", column="score", datatype=DataType.build("DECIMAL(10,2)"), scale=2),
            ColumnSpec(table="basic", column="is_active", datatype=DataType.build("BOOLEAN")),
            ColumnSpec(table="basic", column="created_at", datatype=DataType.build("DATE")),
        )
        self.basic_table = TableSpec(name="basic", columns=self.basic_columns, primary_key=("id",))
        self.schema = SchemaSpec(tables=(self.basic_table,))

    def test_generate_all_basic_types(self):
        builder = DatabaseBuilder(self.schema)
        row = builder.generate_row("basic")

        self.assertIsInstance(row["id"], int)
        self.assertIsInstance(row["name"], str)
        self.assertIsInstance(row["score"], (Decimal, float, int))  # Decimal for real types with scale
        self.assertIsInstance(row["is_active"], bool)
        self.assertIsInstance(row["created_at"], (date, datetime)) # Depending on provider implementation

    def test_null_constraint_violation(self):
        # 'name' is NOT NULL
        builder = DatabaseBuilder(self.schema)
        with self.assertRaises(ConstraintViolationError):
            builder.complete_row("basic", {"name": None})

    def test_unique_constraint_violation(self):
        builder = DatabaseBuilder(self.schema)
        builder.complete_row("basic", {"id": 1})
        
        with self.assertRaises(UniqueConflictError):
            builder.complete_row("basic", {"id": 1})

    def test_primary_key_is_unique(self):
        builder = DatabaseBuilder(self.schema)
        rows = builder.build(BuildPolicy(default_row_count=10))
        
        ids = [row["id"] for row in rows["basic"]]
        self.assertEqual(len(ids), len(set(ids)), "Primary key values must be unique")

    def test_foreign_key_satisfaction(self):
        parent_id = ColumnSpec(table="parent", column="id", datatype=DataType.build("INT"), primary_key=True)
        child_id = ColumnSpec(table="child", column="id", datatype=DataType.build("INT"), primary_key=True)
        child_ref = ColumnSpec(
            table="child", 
            column="parent_id", 
            datatype=DataType.build("INT"),
            foreign_key=ForeignKeySpec(
                source_table="child", source_columns=("parent_id",),
                target_table="parent", target_columns=("id",)
            )
        )
        
        schema = SchemaSpec(tables=(
            TableSpec(name="parent", columns=(parent_id,)),
            TableSpec(name="child", columns=(child_id, child_ref))
        ))
        
        builder = DatabaseBuilder(schema)
        # Generate parent first (essential due to current implementation's "topological blindness")
        builder.build(BuildPolicy(row_counts={"parent": 5, "child": 10}, default_row_count=0))
        
        parent_table = schema.get_table("parent")
        child_table = schema.get_table("child")
        parent_id_col = parent_table.get_column("id").id
        child_parent_id_col = child_table.get_column("parent_id").id
        parent_ids = {
            row[parent_id_col]
            for row in builder.runtime.table_state(parent_table.id).rows
        }
        child_rows = builder.runtime.table_state("child").rows
        
        for row in child_rows:
            self.assertIn(row[child_parent_id_col], parent_ids, "Child must reference an existing parent ID")

    def test_multiple_tables_generation(self):
        table1 = TableSpec(name="t1", columns=(ColumnSpec(table="t1", column="c1", datatype=DataType.build("INT")),))
        table2 = TableSpec(name="t2", columns=(ColumnSpec(table="t2", column="c2", datatype=DataType.build("TEXT")),))
        schema = SchemaSpec(tables=(table1, table2))
        
        builder = DatabaseBuilder(schema)
        result = builder.build(BuildPolicy(row_counts={"t1": 3, "t2": 2}))
        
        self.assertEqual(len(result["t1"]), 3)
        self.assertEqual(len(result["t2"]), 2)

    def test_temporal_types(self):
        cols = (
            ColumnSpec(table="temp", column="d", datatype=DataType.build("DATE")),
            ColumnSpec(table="temp", column="dt", datatype=DataType.build("DATETIME")),
            ColumnSpec(table="temp", column="t", datatype=DataType.build("TIME")),
        )
        schema = SchemaSpec(tables=(TableSpec(name="temp", columns=cols),))
        builder = DatabaseBuilder(schema)
        row = builder.generate_row("temp")
        
        # We check if they are at least generated (specific types might depend on providers)
        self.assertIsNotNone(row["d"])
        self.assertIsNotNone(row["dt"])
        self.assertIsNotNone(row["t"])

    def test_null_rate_generation(self):
        # Even if implementation is shallow, let's see what happens with null_rate=1.0
        # If the providers ignore it, this test will fail if we expect NULLs.
        col = ColumnSpec(table="null_test", column="c", datatype=DataType.build("TEXT"), nullable=True)
        schema = SchemaSpec(tables=(TableSpec(name="null_test", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        # We expect many NULLs if null_rate is high
        rows = builder.build(BuildPolicy(default_row_count=100, null_rate=1.0))
        null_count = sum(1 for row in rows["null_test"] if row["c"] is None)
        
        # Sharp Point: Currently providers ignore null_rate, so this might be 0.
        # This test documents the current (possibly broken) behavior.
        print(f"Null count with null_rate=1.0: {null_count}")
        self.assertEqual(null_count, 100, "Should generate exactly 100 NULLs when null_rate=1.0")

    def test_topological_blindness_failure(self):
        # Child before Parent in schema
        parent_id = ColumnSpec(table="parent", column="id", datatype=DataType.build("INT"), primary_key=True)
        child_ref = ColumnSpec(
            table="child", 
            column="parent_id", 
            datatype=DataType.build("INT"),
            nullable=False,
            foreign_key=ForeignKeySpec(
                source_table="child", source_columns=("parent_id",),
                target_table="parent", target_columns=("id",)
            )
        )
        
        schema = SchemaSpec(tables=(
            TableSpec(name="child", columns=(child_ref,)),
            TableSpec(name="parent", columns=(parent_id,))
        ))
        
        builder = DatabaseBuilder(schema)
        
        # This is expected to fail with ForeignKeyResolutionError because 'parent' hasn't been generated yet
        with self.assertRaises(ForeignKeyResolutionError):
            builder.build(BuildPolicy(default_row_count=1))

    def test_dialect_specific_profiling(self):
        # Test with a dialect-specific varchar
        col = ColumnSpec(
            table="pg_table", 
            column="name", 
            datatype=DataType.build("VARCHAR(255)"),
            dialect="postgres"
        )
        schema = SchemaSpec(tables=(TableSpec(name="pg_table", columns=(col,)),), dialect="postgres")
        builder = DatabaseBuilder(schema)
        
        row = builder.generate_row("pg_table")
        self.assertIsInstance(row["name"], str)

    def test_unsupported_type_fails_gracefully(self):
        # UUID should now be handled by an exact/native-type provider
        col = ColumnSpec(table="t", column="u", datatype=DataType.build("UUID"))
        schema = SchemaSpec(tables=(TableSpec(name="t", columns=(col,)),))
        builder = DatabaseBuilder(schema)
        
        row = builder.generate_row("t")
        import uuid

        self.assertIsInstance(row["u"], uuid.UUID)

if __name__ == "__main__":
    unittest.main()
