from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Optional, Tuple

from parseval.dtype import DataType
from parseval.identity import (
    ColumnId,
    ColumnKind,
    RelationId,
    RelationKind,
    column_id,
    identifier_key,
    identifier_name,
    relation_id,
)


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
    source_table_id: Optional[RelationId] = None
    source_column_ids: Tuple[ColumnId, ...] = ()
    target_table_id: Optional[RelationId] = None
    target_column_ids: Tuple[ColumnId, ...] = ()


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
    id: Optional[ColumnId] = None
    table_id: Optional[RelationId] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "datatype", DataType.build(self.datatype))
        object.__setattr__(self, "table", identifier_key(self.table, dialect=self.dialect))
        object.__setattr__(self, "column", identifier_key(self.column, dialect=self.dialect))
        object.__setattr__(self, "semantic_tags", tuple(self.semantic_tags))
        object.__setattr__(self, "checks", tuple(self.checks))
        table_id = self.table_id or relation_id(
            RelationKind.TABLE,
            identifier_name(self.table, dialect=self.dialect),
        )
        object.__setattr__(self, "table_id", table_id)
        if self.id is None:
            object.__setattr__(
                self,
                "id",
                column_id(
                    ColumnKind.PHYSICAL,
                    identifier_name(self.column, dialect=self.dialect),
                    table_id,
                ),
            )

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
    id: Optional[RelationId] = None
    primary_key_ids: Tuple[ColumnId, ...] = ()
    unique_constraint_ids: Tuple[Tuple[ColumnId, ...], ...] = ()
    dialect: Optional[str] = None

    def __post_init__(self) -> None:
        dialect = self.dialect
        object.__setattr__(self, "name", identifier_key(self.name, dialect=dialect))
        object.__setattr__(
            self, "primary_key", tuple(identifier_key(column, dialect=dialect) for column in self.primary_key)
        )
        object.__setattr__(
            self,
            "unique_constraints",
            tuple(
                tuple(identifier_key(column, dialect=dialect) for column in columns)
                for columns in self.unique_constraints
            ),
        )
        object.__setattr__(self, "foreign_keys", tuple(self.foreign_keys))
        object.__setattr__(self, "primary_key_ids", tuple(self.primary_key_ids))
        object.__setattr__(
            self,
            "unique_constraint_ids",
            tuple(tuple(columns) for columns in self.unique_constraint_ids),
        )

    def get_column(self, column_name: str) -> ColumnSpec:
        if isinstance(column_name, ColumnSpec):
            column_name = column_name.id
        if isinstance(column_name, ColumnId):
            for column in self.columns:
                if column.id == column_name:
                    return column
            raise KeyError(f"Unknown column {column_name.display}")
        normalized = identifier_key(str(column_name), dialect=self.dialect)
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

    def __post_init__(self) -> None:
        table_ids: dict[str, RelationId] = {}
        column_ids: dict[str, dict[str, ColumnId]] = {}
        dialect = self.dialect

        for table in self.tables:
            table_key = identifier_key(table.name, dialect=dialect)
            table_id = table.id or relation_id(
                RelationKind.TABLE,
                identifier_name(table.name, dialect=dialect),
            )
            table_ids[table_key] = table_id
            column_ids[table_key] = {}
            for column in table.columns:
                column_key = identifier_key(column.column, dialect=dialect)
                existing_id = column.id if column.table_id == table_id else None
                column_ids[table_key][column_key] = existing_id or column_id(
                    ColumnKind.PHYSICAL,
                    identifier_name(column.column, dialect=dialect),
                    table_id,
                )

        normalized_tables = []
        for table in self.tables:
            table_key = identifier_key(table.name, dialect=dialect)
            table_id = table_ids[table_key]
            foreign_keys = self._normalize_foreign_keys(
                table,
                table_ids,
                column_ids,
            )
            foreign_key_by_source = {
                tuple(fk.source_column_ids): fk
                for fk in foreign_keys
            }
            columns = []
            for column in table.columns:
                column_key = identifier_key(column.column, dialect=dialect)
                normalized_fk = None
                if column.foreign_key is not None:
                    fk = self._normalize_foreign_key(
                        column.foreign_key,
                        table_ids,
                        column_ids,
                    )
                    normalized_fk = foreign_key_by_source.get(
                        tuple(fk.source_column_ids),
                        fk,
                    )
                columns.append(
                    replace(
                        column,
                        table=table.name,
                        id=column_ids[table_key][column_key],
                        table_id=table_id,
                        foreign_key=normalized_fk,
                    )
                )
            primary_key_ids = table.primary_key_ids or tuple(
                column_ids[table_key][identifier_key(column, dialect=dialect)]
                for column in table.primary_key
            )
            unique_constraint_ids = table.unique_constraint_ids or tuple(
                tuple(column_ids[table_key][identifier_key(column, dialect=dialect)] for column in columns)
                for columns in table.unique_constraints
            )
            normalized_tables.append(
                replace(
                    table,
                    id=table_id,
                    columns=tuple(columns),
                    foreign_keys=foreign_keys,
                    primary_key_ids=primary_key_ids,
                    unique_constraint_ids=unique_constraint_ids,
                )
            )
        object.__setattr__(self, "tables", tuple(normalized_tables))

    def _normalize_foreign_keys(
        self,
        table: TableSpec,
        table_ids: dict[str, RelationId],
        column_ids: dict[str, dict[str, ColumnId]],
    ) -> Tuple[ForeignKeySpec, ...]:
        seen = set()
        foreign_keys = []
        for fk in tuple(table.foreign_keys) + tuple(
            column.foreign_key
            for column in table.columns
            if column.foreign_key is not None
        ):
            normalized = self._normalize_foreign_key(fk, table_ids, column_ids)
            key = (
                normalized.source_table_id,
                normalized.source_column_ids,
                normalized.target_table_id,
                normalized.target_column_ids,
            )
            if key not in seen:
                seen.add(key)
                foreign_keys.append(normalized)
        return tuple(foreign_keys)

    def _normalize_foreign_key(
        self,
        fk: ForeignKeySpec,
        table_ids: dict[str, RelationId],
        column_ids: dict[str, dict[str, ColumnId]],
    ) -> ForeignKeySpec:
        dialect = self.dialect
        source_key = identifier_key(fk.source_table, dialect=dialect)
        target_key = identifier_key(fk.target_table, dialect=dialect)
        return replace(
            fk,
            source_table_id=fk.source_table_id or table_ids[source_key],
            source_column_ids=fk.source_column_ids or tuple(
                column_ids[source_key][identifier_key(column, dialect=dialect)]
                for column in fk.source_columns
            ),
            target_table_id=fk.target_table_id or table_ids[target_key],
            target_column_ids=fk.target_column_ids or tuple(
                column_ids[target_key][identifier_key(column, dialect=dialect)]
                for column in fk.target_columns
            ),
        )

    def get_table(self, table_name: str | RelationId | TableSpec) -> TableSpec:
        if isinstance(table_name, TableSpec):
            table_name = table_name.id
        if isinstance(table_name, RelationId):
            for table in self.tables:
                if table.id == table_name:
                    return table
            raise KeyError(f"Unknown table {table_name.display}")
        normalized = identifier_key(str(table_name), dialect=self.dialect)
        for table in self.tables:
            if table.name == normalized:
                return table
        raise KeyError(f"Unknown table {table_name}")


__all__ = ["SchemaSpec", "TableSpec", "ColumnSpec", "ForeignKeySpec"]
