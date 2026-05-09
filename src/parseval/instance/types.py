from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from parseval.plan.rex import Row


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


@dataclass(frozen=True)
class DatabaseTarget:
    connection_string: str
    dialect: str


@dataclass(frozen=True)
class WriteResult:
    inserted_tables: tuple[str, ...]
    inserted_rows: int
    statements: tuple[str, ...] = ()


@dataclass(frozen=True)
class RowCreationResult:
    created: dict[str, tuple[Row, ...]]
    positions: dict[str, int]
