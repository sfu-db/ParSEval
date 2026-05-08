from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from parseval.dtype import DataType


@dataclass(frozen=True)
class ForeignKeySpec:
    source_table: str
    source_columns: Tuple[str, ...]
    target_table: str
    target_columns: Tuple[str, ...]


@dataclass(frozen=True)
class ColumnSpec:
    table: str
    column: str
    datatype: DataType
    nullable: bool = True
    unique: bool = False
    primary_key: bool = False
    foreign_key: Optional[ForeignKeySpec] = None
    default: Any = None
    native_type: Optional[str] = None
    dialect: Optional[str] = None
    length: Optional[int] = None
    precision: Optional[int] = None
    scale: Optional[int] = None
    semantic_tags: Tuple[str, ...] = ()
    checks: Tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "datatype", DataType.build(self.datatype))
        object.__setattr__(self, "table", self.table.lower())
        object.__setattr__(self, "column", self.column.lower())
        object.__setattr__(self, "semantic_tags", tuple(self.semantic_tags))
        object.__setattr__(self, "checks", tuple(self.checks))

    @property
    def qualified_name(self) -> str:
        return f"{self.table}.{self.column}"


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: Tuple[ColumnSpec, ...]
    primary_key: Tuple[str, ...] = ()
    unique_constraints: Tuple[Tuple[str, ...], ...] = ()
    foreign_keys: Tuple[ForeignKeySpec, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", self.name.lower())
        object.__setattr__(
            self, "primary_key", tuple(column.lower() for column in self.primary_key)
        )
        object.__setattr__(
            self,
            "unique_constraints",
            tuple(
                tuple(column.lower() for column in columns)
                for columns in self.unique_constraints
            ),
        )
        object.__setattr__(self, "foreign_keys", tuple(self.foreign_keys))

    def get_column(self, column_name: str) -> ColumnSpec:
        normalized = column_name.lower()
        for column in self.columns:
            if column.column == normalized:
                return column
        raise KeyError(f"Unknown column {self.name}.{column_name}")


@dataclass(frozen=True)
class SchemaSpec:
    tables: Tuple[TableSpec, ...]
    dialect: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_table(self, table_name: str) -> TableSpec:
        normalized = table_name.lower()
        for table in self.tables:
            if table.name == normalized:
                return table
        raise KeyError(f"Unknown table {table_name}")


__all__ = ["SchemaSpec", "TableSpec", "ColumnSpec", "ForeignKeySpec"]
