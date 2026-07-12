from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlglot import exp


@dataclass(frozen=True)
class TableBatch:
    table_name: str
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class InstanceSnapshot:
    schema_ddl: str
    dialect: str
    tables: tuple[TableBatch, ...]


class InstanceValueSerializer:
    """Concrete-value coerce for DB binding / SQL fixtures."""

    def serialize_row(self, table_name: str, row: dict[str, Any]) -> dict[str, Any]:
        del table_name
        return {key: self._coerce(value) for key, value in row.items()}

    @staticmethod
    def _coerce(value: Any) -> Any:
        if isinstance(value, Decimal):
            return float(value)
        if isinstance(value, datetime):
            return value.isoformat(sep=" ")
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, time):
            return value.isoformat()
        return value


class InstanceExporter:
    def render_sql(
        self,
        snapshot: InstanceSnapshot,
        serializer: InstanceValueSerializer | None = None,
        dialect: str | None = None,
    ) -> tuple[str, ...]:
        serializer = serializer or InstanceValueSerializer()
        dialect = dialect or snapshot.dialect
        statements: list[str] = []
        for table in snapshot.tables:
            if not table.rows:
                continue
            statements.append(f"-- Inserting into table: {table.table_name} --")
            for row in table.rows:
                serialized_row = serializer.serialize_row(table.table_name, row)
                columns = tuple(serialized_row.keys())
                insert = exp.Insert(
                    this=exp.Schema(
                        this=exp.Table(
                            this=exp.Identifier(this=table.table_name, quoted=True)
                        ),
                        expressions=[
                            exp.Identifier(this=column, quoted=True)
                            for column in columns
                        ],
                    ),
                    expression=exp.Values(
                        expressions=[
                            exp.Tuple(
                                expressions=[
                                    exp.convert(serialized_row[column])
                                    for column in columns
                                ]
                            )
                        ]
                    ),
                )
                statements.append(f"{insert.sql(dialect=dialect)};\n")
        return tuple(statements)
