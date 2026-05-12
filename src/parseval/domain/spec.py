from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Tuple

from parseval.dtype import DataType


@dataclass(frozen=True)
class ForeignKeySpec:
    """Declares a foreign key relationship between source and target columns.

    Attributes:
        source_table: Name of the table containing the FK column(s).
        source_columns: Column names on the source side.
        target_table: Name of the referenced (parent) table.
        target_columns: Column names on the target side, matching source_columns
            positionally.
    """
    source_table: str
    source_columns: Tuple[str, ...]
    target_table: str
    target_columns: Tuple[str, ...]


@dataclass(frozen=True)
class ColumnSpec:
    """Immutable specification for a single database column.

    Attributes:
        table: Owning table name (automatically lowered).
        column: Column name (automatically lowered).
        datatype: The declared SQL data type.
        nullable: Whether NULL values are allowed (default True).
        unique: Whether the column has a UNIQUE constraint.
        primary_key: Whether the column is part of the primary key.
        foreign_key: Optional foreign key reference info.
        default: Default value for the column, if any.
        native_type: Database-native type override string.
        dialect: SQL dialect this column targets.
        length: Character/precision length from the type declaration.
        precision: Numeric precision, if applicable.
        scale: Decimal scale, if applicable.
        semantic_tags: Freeform tags for categorization.
        checks: Column-level CHECK constraints.
    """

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
    """Immutable specification for a single database table.

    Attributes:
        name: Table name (automatically lowered).
        columns: Tuple of ColumnSpec instances for all columns.
        primary_key: Column names forming the primary key.
        unique_constraints: Tuple of column-name-tuples for UNIQUE constraints.
        foreign_keys: Tuple of ForeignKeySpec for outgoing FK references.
    """

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
    """Immutable specification for an entire database schema.

    Attributes:
        tables: Tuple of all TableSpec instances in the schema.
        dialect: Default SQL dialect (e.g. "sqlite", "postgres").
        metadata: Free-form metadata dict attached to the schema.
    """

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
