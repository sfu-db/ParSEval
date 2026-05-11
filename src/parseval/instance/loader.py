from __future__ import annotations

from sqlglot import exp

from parseval.db_manager import DBManager
from parseval.helper import normalize_name

from .serialization import InstanceValueSerializer
from .types import DatabaseTarget, InstanceSnapshot, TableBatch, WriteResult


class InstanceLoader:
    def load(
        self,
        snapshot: InstanceSnapshot,
        target: DatabaseTarget,
        serializer: InstanceValueSerializer,
        truncate_first: bool = True,
    ) -> WriteResult:
        inserted_tables: list[str] = []
        inserted_rows = 0

        with DBManager().get_connection(
            connection_string=target.connection_string,
            dialect=target.dialect,
        ) as conn:
            if truncate_first:
                for table in snapshot.tables:
                    conn.drop_table(table.table_name)
            ddls = [ddl.strip() for ddl in snapshot.schema_ddl.split(";") if ddl.strip()]
            if ddls:
                conn.create_tables(*ddls)
            for table in snapshot.tables:
                inserted = self._insert_table(
                    conn,
                    table,
                    target.dialect,
                    serializer=serializer,
                )
                if inserted:
                    inserted_tables.append(table.table_name)
                    inserted_rows += inserted

        return WriteResult(
            inserted_tables=tuple(inserted_tables),
            inserted_rows=inserted_rows,
        )

    def _insert_table(
        self,
        conn,
        table: TableBatch,
        dialect: str,
        serializer: InstanceValueSerializer,
    ) -> int:
        if not table.rows:
            return 0
        parameter_names = {
            column: normalize_name(column)
            for column in table.columns
        }
        statement = exp.Insert(
            this=exp.Schema(
                this=self._quoted_table(table.table_name),
                expressions=[self._quoted_identifier(column) for column in table.columns],
            ),
            expression=exp.Values(
                expressions=[
                    exp.Tuple(
                        expressions=[
                            exp.Placeholder(this=parameter_names[column])
                            for column in table.columns
                        ]
                    )
                ]
            ),
        ).sql(dialect=dialect)
        payload = [
            {
                parameter_names[column]: serialized_row.get(column)
                for column in table.columns
            }
            for serialized_row in (
                serializer.serialize_row(table.table_name, row) for row in table.rows
            )
        ]
        conn.insert(statement, payload)
        return len(payload)

    def _quoted_table(self, table_name: str) -> exp.Table:
        return exp.Table(this=exp.Identifier(this=table_name, quoted=True))

    def _quoted_identifier(self, name: str) -> exp.Identifier:
        return exp.Identifier(this=name, quoted=True)
