from __future__ import annotations

from sqlglot import exp

from .serialization import InstanceValueSerializer
from .types import InstanceSnapshot


class InstanceExporter:
    def render_sql(
        self,
        snapshot: InstanceSnapshot,
        serializer: InstanceValueSerializer,
        dialect: str | None = None,
    ) -> tuple[str, ...]:
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
