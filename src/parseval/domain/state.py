from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Any, Dict, List, Optional, Set, Tuple

from .spec import ColumnSpec, ForeignKeySpec, SchemaSpec, TableSpec


@dataclass
class ColumnState:
    spec: ColumnSpec
    generated_values: List[Any] = field(default_factory=list)
    used_values: Set[Any] = field(default_factory=set)
    null_count: int = 0

    def remember(self, value: Any) -> None:
        self.generated_values.append(value)
        if value is None:
            self.null_count += 1
        else:
            self.used_values.add(value)


@dataclass
class TableState:
    spec: TableSpec
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def add_row(self, row: Dict[str, Any]) -> None:
        self.rows.append(row)


@dataclass
class RowContext:
    table: TableSpec
    values: Dict[str, Any] = field(default_factory=dict)
    provided_columns: Set[str] = field(default_factory=set)
    generated_columns: Set[str] = field(default_factory=set)

    def set_provided(self, column: str, value: Any) -> None:
        normalized = column.lower()
        self.values[normalized] = value
        self.provided_columns.add(normalized)

    def set_generated(self, column: str, value: Any) -> None:
        normalized = column.lower()
        self.values[normalized] = value
        self.generated_columns.add(normalized)

    def get(self, column: str, default: Any = None) -> Any:
        return self.values.get(column.lower(), default)


@dataclass
class SchemaRuntime:
    schema: SchemaSpec
    seed: int = 142
    rng: random.Random = field(init=False)
    tables: Dict[str, TableState] = field(init=False)
    columns: Dict[str, ColumnState] = field(init=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.seed)
        self.tables = {table.name: TableState(table) for table in self.schema.tables}
        self.columns = {}
        for table in self.schema.tables:
            for column in table.columns:
                self.columns[column.qualified_name] = ColumnState(column)

    def table_state(self, table_name: str) -> TableState:
        return self.tables[table_name.lower()]

    def column_state(self, table_name: str, column_name: str) -> ColumnState:
        return self.columns[f"{table_name.lower()}.{column_name.lower()}"]

    def referenced_values(self, column: ColumnSpec) -> Optional[List[Any]]:
        foreign_key = column.foreign_key
        if foreign_key is None or len(foreign_key.target_columns) != 1:
            return None
        target_column = foreign_key.target_columns[0]
        target_state = self.column_state(foreign_key.target_table, target_column)
        return list(target_state.used_values)

    def referenced_key_tuples(
        self, foreign_key: ForeignKeySpec
    ) -> List[Tuple[Any, ...]]:
        table_state = self.table_state(foreign_key.target_table)
        tuples: List[Tuple[Any, ...]] = []
        for row in table_state.rows:
            tuples.append(
                tuple(row.get(column.lower()) for column in foreign_key.target_columns)
            )
        return tuples

    def remember_row(self, table_name: str, row: Dict[str, Any]) -> None:
        table = self.schema.get_table(table_name)
        normalized_row = {key.lower(): value for key, value in row.items()}
        self.table_state(table.name).add_row(normalized_row)
        for column in table.columns:
            self.column_state(table.name, column.column).remember(
                normalized_row.get(column.column)
            )


__all__ = ["ColumnState", "TableState", "SchemaRuntime", "RowContext"]
