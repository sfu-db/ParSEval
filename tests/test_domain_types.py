import unittest
from datetime import date, datetime
from decimal import Decimal
import uuid

from parseval.domain.coercion import coerce_reference_value, coerce_value, values_equivalent
from parseval.domain.spec import ColumnSpec
from parseval.dtype import DataType, TypeFamily, TypeProfile, TypeService


class DomainTypeTests(unittest.TestCase):
    def test_mysql_tinyint_1_profiles_as_boolean_like(self):
        spec = ColumnSpec(
            table="t",
            column="flag",
            datatype=DataType.build("TINYINT(1)", dialect="mysql"),
            dialect="mysql",
        )

        profile = TypeService().profile(spec)

        self.assertEqual(profile.family, TypeFamily.BOOLEAN)
        self.assertEqual(profile.exact_type, "TINYINT")
        self.assertEqual(profile.length, 1)

    def test_mysql_tinyint_4_profiles_as_integer(self):
        spec = ColumnSpec(
            table="t",
            column="code",
            datatype=DataType.build("TINYINT(4)", dialect="mysql"),
            dialect="mysql",
        )

        profile = TypeService().profile(spec)

        self.assertEqual(profile.family, TypeFamily.INTEGER)
        self.assertEqual(profile.exact_type, "TINYINT")
        self.assertEqual(profile.length, 4)

    def test_postgres_uuid_profiles_as_uuid(self):
        spec = ColumnSpec(
            table="t",
            column="id",
            datatype=DataType.build("UUID", dialect="postgres"),
            dialect="postgres",
        )

        profile = TypeService().profile(spec)

        self.assertEqual(profile.family, TypeFamily.UUID)
        self.assertEqual(profile.exact_type, "UUID")

    def test_decimal_profile_keeps_precision_and_scale(self):
        spec = ColumnSpec(
            table="t",
            column="amount",
            datatype=DataType.build("DECIMAL(10,2)", dialect="postgres"),
            dialect="postgres",
        )

        profile = TypeService().profile(spec)

        self.assertEqual(profile.family, TypeFamily.DECIMAL)
        self.assertEqual(profile.precision, 10)
        self.assertEqual(profile.scale, 2)

    def test_varchar_profile_keeps_length(self):
        spec = ColumnSpec(
            table="t",
            column="name",
            datatype=DataType.build("VARCHAR(10)", dialect="postgres"),
            dialect="postgres",
        )

        profile = TypeService().profile(spec)

        self.assertEqual(profile.family, TypeFamily.TEXT)
        self.assertEqual(profile.length, 10)

    def test_uuid_coercion_returns_uuid_object(self):
        value = "550e8400-e29b-41d4-a716-446655440000"
        datatype = DataType.build("UUID", dialect="postgres")

        coerced = coerce_value(value, datatype, dialect="postgres")

        self.assertEqual(
            coerced, uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
        )
        self.assertIsInstance(coerced, uuid.UUID)

    def test_uuid_column_generates_uuid_values(self):
        from parseval.domain import DatabaseBuilder, SchemaSpec, TableSpec

        spec = ColumnSpec(
            table="t",
            column="id",
            datatype=DataType.build("UUID", dialect="postgres"),
            dialect="postgres",
            unique=True,
        )
        builder = DatabaseBuilder(SchemaSpec(tables=(TableSpec(name="t", columns=(spec,)),)))

        row = builder.generate_row("t")

        self.assertIsInstance(row["id"], uuid.UUID)

    def test_mysql_tinyint_1_column_generates_boolean_values(self):
        from parseval.domain import DatabaseBuilder, SchemaSpec, TableSpec

        spec = ColumnSpec(
            table="t",
            column="flag",
            datatype=DataType.build("TINYINT(1)", dialect="mysql"),
            dialect="mysql",
        )
        builder = DatabaseBuilder(SchemaSpec(tables=(TableSpec(name="t", columns=(spec,)),)))

        row = builder.generate_row("t")

        self.assertIsInstance(row["flag"], bool)

    def test_decimal_coercion_returns_decimal_with_scale(self):
        datatype = DataType.build("DECIMAL(10,2)", dialect="postgres")

        coerced = coerce_value("12.345", datatype, dialect="postgres")

        self.assertEqual(coerced, Decimal("12.35"))

    def test_reference_projection_from_date_to_datetime(self):
        parent_value = date(2020, 1, 2)
        child_datatype = DataType.build("DATETIME", dialect="postgres")

        projected = coerce_reference_value(parent_value, child_datatype, dialect="postgres")

        self.assertEqual(projected, datetime(2020, 1, 2, 0, 0, 0))

    def test_uuid_equivalence_matches_string_and_uuid_object(self):
        datatype = DataType.build("UUID", dialect="postgres")
        left = "550e8400-e29b-41d4-a716-446655440000"
        right = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")

        self.assertTrue(
            values_equivalent(left, datatype, right, datatype, left_dialect="postgres", right_dialect="postgres")
        )

    def test_type_profile_is_cached_per_column_spec(self):
        spec = ColumnSpec(
            table="t",
            column="id",
            datatype=DataType.build("UUID", dialect="postgres"),
            dialect="postgres",
        )
        service = TypeService()

        first = service.profile(spec)
        second = service.profile(spec)

        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
