"""Persistence helpers for :class:`parseval.instance.Instance`.

Extracted from ``Instance.to_db`` so the Instance class itself stays
focused on in-memory row management. Callers that need to write an
Instance to a live database or render SQL fixtures import from here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Union

from .exporter import InstanceExporter, InstanceValueSerializer
from dataclasses import dataclass
from sqlglot import exp
from parseval.db_manager import DBManager
from .exporter import InstanceSnapshot, InstanceValueSerializer, TableBatch
if TYPE_CHECKING:
    from .core import Instance



@dataclass(frozen=True)
class WriteResult:
    inserted_tables: tuple[str, ...]
    inserted_rows: int
    statements: tuple[str, ...] = ()


class InstanceLoader:
    def load(
        self,
        snapshot: InstanceSnapshot,
        connection_string,
        serializer: InstanceValueSerializer,
        dialect: str ,
        truncate_first: bool = True,
        
    ) -> WriteResult:
        inserted_tables: list[str] = []
        inserted_rows = 0

        with DBManager().get_connection(
            connection_string=connection_string,
            dialect=dialect,
        ) as conn:
            if truncate_first:
                is_mysql = dialect == "mysql"
                if is_mysql:
                    conn.execute("SET FOREIGN_KEY_CHECKS = 0", fetch=None)
                for table in reversed(snapshot.tables):
                    conn.drop_table(table.table_name)
                if is_mysql:
                    conn.execute("SET FOREIGN_KEY_CHECKS = 1", fetch=None)
            ddls = [ddl.strip() for ddl in snapshot.schema_ddl.split(";") if ddl.strip()]
            if ddls:
                conn.create_tables(*ddls)
            for table in snapshot.tables:
                inserted = self._insert_table(
                    conn,
                    table,
                    dialect,
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
            column: f"p{index}"
            for index, column in enumerate(table.columns)
        }
        # pymysql uses %(name)s placeholders via exec_driver_sql; others use :name
        if dialect == "mysql":
            cols = ", ".join(f"`{c}`" for c in table.columns)
            phs = ", ".join(f"%({parameter_names[c]})s" for c in table.columns)
            statement = f"INSERT INTO `{table.table_name}` ({cols}) VALUES ({phs})"
        else:
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


def to_db(
    instance: "Instance",
    connection_string: str,
    dialect: Optional[str] = None,
    truncate_first: bool = True,
    return_inserted: bool = False,
) -> Union[str, None]:
    """Write ``instance``'s current rows to a live database.

    This keeps live database writes separate from the in-memory
    row-management code in :class:`Instance`.

    Parameters
    ----------
    instance : Instance
        The in-memory instance to persist.
    connection_string : str
        SQLAlchemy-style connection string (e.g. ``sqlite:///path``).
    dialect : str, optional
        SQL dialect for the target. Defaults to ``instance.dialect``.
    truncate_first : bool
        Whether to drop existing tables before inserting.
    return_inserted : bool
        If True, return the rendered INSERT SQL instead of the write result.
    """
    dialect = dialect or instance.dialect
    snapshot = instance.snapshot()
    
    serializer = InstanceValueSerializer()
    result = InstanceLoader().load(
        snapshot=snapshot,
        connection_string=connection_string,
        dialect = dialect,
        serializer=serializer,
        truncate_first=truncate_first,
    )
    if return_inserted:
        return "\n".join(
            InstanceExporter().render_sql(
                snapshot,
                serializer=serializer,
                dialect=dialect,
            )
        )
    return result


__all__ = ["to_db"]
