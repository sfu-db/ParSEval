from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

from sqlglot import exp


@dataclass(frozen=True)
class TableBatch:
    table: exp.Table
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]

    @property
    def table_name(self) -> str:
        return ".".join(part.name for part in self.table.parts)


@dataclass(frozen=True)
class InstanceSnapshot:
    schema_ddl: str
    dialect: str
    tables: tuple[TableBatch, ...]


class InstanceExporter:
    def render_sql(
        self,
        snapshot: InstanceSnapshot,
        dialect: str | None = None,
    ) -> tuple[str, ...]:
        dialect = dialect or snapshot.dialect
        statements: list[str] = []
        for table in snapshot.tables:
            if not table.rows:
                continue
            statements.append(f"-- Inserting into table: {table.table_name} --")
            for row in table.rows:
                columns = table.columns
                insert = exp.Insert(
                    this=exp.Schema(
                        this=table.table.copy(),
                        expressions=[
                            exp.Identifier(this=column, quoted=True)
                            for column in columns
                        ],
                    ),
                    expression=exp.Values(
                        expressions=[
                            exp.Tuple(
                                expressions=[
                                    self._literal(row[column]) for column in columns
                                ]
                            )
                        ]
                    ),
                )
                statements.append(f"{insert.sql(dialect=dialect, identify=True)};\n")
        return tuple(statements)

    @staticmethod
    def _literal(value: Any) -> exp.Expression:
        if isinstance(value, Decimal):
            return exp.Literal.number(str(value))
        if isinstance(value, datetime):
            return exp.Literal.string(value.isoformat(sep=" "))
        if isinstance(value, (date, time)):
            return exp.Literal.string(value.isoformat())
        return exp.convert(value)
