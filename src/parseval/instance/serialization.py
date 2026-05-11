from __future__ import annotations

from typing import Any

from parseval.domain.types import TypeService


class InstanceValueSerializer:
    def __init__(self, schema_spec):
        self.schema = schema_spec
        self.type_service = TypeService()

    def serialize_row(self, table_name: str, row: dict[str, Any]) -> dict[str, Any]:
        table = self.schema.get_table(table_name)
        serialized: dict[str, Any] = {}
        for column_name, value in row.items():
            column = table.get_column(column_name)
            profile = self.type_service.profile(column)
            adapter = self.type_service.registry.resolve(profile.datatype, profile.dialect)
            serialized[column_name] = adapter.coerce_out(value, profile)
        return serialized
