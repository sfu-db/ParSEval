"""Materialize instance snapshots through one database transaction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import MetaData, Table
from sqlglot import exp, parse

from parseval.db_manager import DBManager
from .exporter import InstanceExporter, InstanceSnapshot

if TYPE_CHECKING:
    from .core import Instance


@dataclass(frozen=True)
class WriteResult:
    inserted_tables: tuple[str, ...]
    inserted_rows: int


class InstanceLoader:
    def load(
        self,
        snapshot: InstanceSnapshot,
        connection_string: str,
        dialect: str,
        truncate_first: bool = True,
    ) -> WriteResult:
        """Replace snapshot tables, or create missing tables and append rows.

        PostgreSQL and SQLite commit the entire load together. MySQL table DDL
        commits implicitly; its inserts still share one transaction.
        """
        if snapshot.dialect != dialect:
            raise ValueError("Snapshot and target dialects must match")
        statements = [stmt for stmt in parse(snapshot.schema_ddl, read=dialect) if stmt]
        inserted_tables: list[str] = []
        inserted_rows = 0
        with DBManager().get_connection(connection_string, dialect) as database:
            with database.begin() as conn:
                if truncate_first:
                    for batch in reversed(snapshot.tables):
                        drop = exp.Drop(
                            this=batch.table.copy(), kind="TABLE", exists=True
                        )
                        conn.exec_driver_sql(
                            drop.sql(dialect=dialect, identify=True),
                            execution_options={"no_parameters": True},
                        )
                for statement in statements:
                    if not truncate_first and isinstance(statement, exp.Create):
                        statement.set("exists", True)
                    conn.exec_driver_sql(
                        statement.sql(dialect=dialect, identify=True),
                        execution_options={"no_parameters": True},
                    )
                metadata = MetaData()
                for batch in snapshot.tables:
                    if not batch.rows:
                        continue
                    table = Table(
                        batch.table.name,
                        metadata,
                        schema=batch.table.db or None,
                        quote=True,
                        quote_schema=True,
                        autoload_with=conn,
                        resolve_fks=False,
                    )
                    conn.execute(table.insert(), list(batch.rows))
                    inserted_tables.append(batch.table_name)
                    inserted_rows += len(batch.rows)
        return WriteResult(tuple(inserted_tables), inserted_rows)


def to_db(
    instance: Instance,
    connection_string: str,
    dialect: str | None = None,
    truncate_first: bool = True,
    return_inserted: bool = False,
) -> str | WriteResult:
    """Write the snapshot, optionally returning its rendered INSERT statements.

    ``truncate_first`` replaces only the snapshot's tables. When false, missing
    tables are created and rows are appended to existing tables.
    """
    dialect = dialect or instance.dialect
    snapshot = instance.snapshot()
    result = InstanceLoader().load(
        snapshot=snapshot,
        connection_string=connection_string,
        dialect=dialect,
        truncate_first=truncate_first,
    )
    if return_inserted:
        return "\n".join(InstanceExporter().render_sql(snapshot, dialect=dialect))
    return result


__all__ = ["InstanceLoader", "WriteResult", "to_db"]
