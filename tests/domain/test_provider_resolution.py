import unittest
import uuid

from parseval.domain.providers.registry import ProviderRegistry
from parseval.domain.spec import ColumnSpec
from parseval.domain.types import TypeService
from parseval.dtype import DataType


class ProviderResolutionTests(unittest.TestCase):
    def test_uuid_provider_outranks_text_family(self):
        spec = ColumnSpec(
            table="t",
            column="id",
            datatype=DataType.build("UUID", dialect="postgres"),
            dialect="postgres",
        )
        registry = ProviderRegistry.with_builtin_providers()

        provider = registry.resolve(spec)

        self.assertEqual(provider.__class__.__name__, "UUIDProvider")

    def test_mysql_tinyint_1_uses_boolean_like_provider(self):
        spec = ColumnSpec(
            table="t",
            column="flag",
            datatype=DataType.build("TINYINT(1)", dialect="mysql"),
            dialect="mysql",
        )
        registry = ProviderRegistry.with_builtin_providers()

        provider = registry.resolve(spec)

        self.assertEqual(provider.__class__.__name__, "BooleanLikeTinyIntProvider")

    def test_enum_provider_outranks_string_provider(self):
        spec = ColumnSpec(
            table="t",
            column="kind",
            datatype=DataType.build("ENUM('A', 'B')"),
        )
        registry = ProviderRegistry.with_builtin_providers()

        provider = registry.resolve(spec)

        self.assertEqual(provider.__class__.__name__, "EnumProvider")


def test_fk_resolution_in_base_provider():
    """Base ValueProvider should have a helper for FK resolution."""
    from parseval.domain.providers.base import ValueProvider
    assert hasattr(ValueProvider, '_resolve_foreign_key')


if __name__ == "__main__":
    unittest.main()
