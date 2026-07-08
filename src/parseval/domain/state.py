from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Any, Dict, List, Mapping, Optional, Set, Tuple

from parseval.coercion import storage_key
from parseval.identity import ColumnId, RelationId
from .spec import ColumnSpec, ForeignKeySpec, SchemaSpec, TableSpec


@dataclass
class ColumnState:
    """Tracks the generation state for a single column.

    Attributes:
        spec: The column specification.
        generated_values: All values that have been generated for this column.
        used_values: Set of non-None values generated so far (used for uniqueness).
        null_count: Number of times a NULL value was generated.
    """

    spec: ColumnSpec
    generated_values: List[Any] = field(default_factory=list)
    used_values: Set[Any] = field(default_factory=set)
    null_count: int = 0

    def remember(self, value: Any, storage_value: Any = None) -> None:
        """Record a generated value, updating used_values and null_count."""
        self.generated_values.append(value)
        if value is None:
            self.null_count += 1
        else:
            self.used_values.add(value if storage_value is None else storage_value)


@dataclass
class TableState:
    """Tracks the generation state for a single table.

    Attributes:
        spec: The table specification.
        rows: List of generated rows (each row is a dict of column name to value).
    """

    spec: TableSpec
    rows: List[Dict[ColumnId, Any]] = field(default_factory=list)

    def add_row(self, row: Dict[ColumnId, Any]) -> None:
        """Append a generated row to this table's state."""
        self.rows.append(row)


@dataclass
class RowContext:
    """Mutable context for building a single row during generation.

    Tracks which columns have been provided (preset) vs. generated,
    and stores the current values.

    Attributes:
        table: The table being built.
        values: Column name (lowercased) to value mapping.
        provided_columns: Columns whose values were explicitly provided.
        generated_columns: Columns whose values were auto-generated.
    """

    table: TableSpec
    values: Dict[ColumnId, Any] = field(default_factory=dict)
    provided_columns: Set[ColumnId] = field(default_factory=set)
    generated_columns: Set[ColumnId] = field(default_factory=set)

    def set_provided(self, column: ColumnSpec | ColumnId, value: Any) -> None:
        """Mark a column as explicitly provided with the given value."""
        column_id = column.id if isinstance(column, ColumnSpec) else column
        self.values[column_id] = value
        self.provided_columns.add(column_id)

    def set_generated(self, column: ColumnSpec | ColumnId, value: Any) -> None:
        """Mark a column as auto-generated with the given value."""
        column_id = column.id if isinstance(column, ColumnSpec) else column
        self.values[column_id] = value
        self.generated_columns.add(column_id)

    def get(self, column: ColumnSpec | ColumnId, default: Any = None) -> Any:
        """Retrieve a column value by identity."""
        column_id = column.id if isinstance(column, ColumnSpec) else column
        return self.values.get(column_id, default)


@dataclass
class SchemaRuntime:
    """Mutable runtime state for the entire schema during data generation.

    Tracks all generated rows, used values (for uniqueness enforcement),
    and provides foreign key resolution data.

    Attributes:
        schema: The schema being generated.
        seed: Random seed for deterministic generation.
        rng: Random number generator instance.
        tables: Map of RelationId to its TableState.
        columns: Map of ColumnId to its ColumnState.
    """

    schema: SchemaSpec
    seed: int = 142
    rng: random.Random = field(init=False)
    tables: Dict[RelationId, TableState] = field(init=False)
    columns: Dict[ColumnId, ColumnState] = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the random number generator and build column state entries."""
        self.rng = random.Random(self.seed)
        self.tables = {table.id: TableState(table) for table in self.schema.tables}
        self.columns = {}
        for table in self.schema.tables:
            for column in table.columns:
                self.columns[column.id] = ColumnState(column)

    def table_state(self, table: str | RelationId | TableSpec) -> TableState:
        """Look up the mutable state for a table by identity."""
        table_spec = self.schema.get_table(table)
        return self.tables[table_spec.id]

    def column_state(
        self,
        table: str | RelationId | TableSpec | ColumnId | ColumnSpec,
        column: str | ColumnId | ColumnSpec | None = None,
    ) -> ColumnState:
        """Look up the mutable state for a column by identity."""
        if isinstance(table, ColumnSpec):
            return self.columns[table.id]
        if isinstance(table, ColumnId) and column is None:
            return self.columns[table]
        if column is None:
            raise KeyError("Column is required for table/column lookup")
        table_spec = self.schema.get_table(table)
        column_spec = table_spec.get_column(column)
        return self.columns[column_spec.id]

    def referenced_values(self, column: ColumnSpec) -> Optional[List[Any]]:
        """Return all used values from the FK target column for single-column foreign keys.

        Returns None for composite foreign keys (more than one target column),
        since those must be resolved via ``referenced_key_tuples``.
        """
        foreign_key = column.foreign_key
        if foreign_key is None or len(foreign_key.target_column_ids) != 1:
            return None
        target_column = foreign_key.target_column_ids[0]
        target_state = self.column_state(target_column)
        return [value for value in target_state.generated_values if value is not None]

    def referenced_key_tuples(
        self, foreign_key: ForeignKeySpec
    ) -> List[Tuple[Any, ...]]:
        """Return all tuples from the target table for composite FK resolution."""
        table_state = self.table_state(foreign_key.target_table_id)
        tuples: List[Tuple[Any, ...]] = []
        for row in table_state.rows:
            tuples.append(
                tuple(row.get(column) for column in foreign_key.target_column_ids)
            )
        return tuples

    def remember_row(
        self,
        table: str | RelationId | TableSpec,
        row: Mapping[str | ColumnId | ColumnSpec, Any],
    ) -> None:
        """Persist a generated row into the runtime state for uniqueness/FK tracking."""
        table_spec = self.schema.get_table(table)
        identity_row: Dict[ColumnId, Any] = {}
        for key, value in row.items():
            column = key if isinstance(key, ColumnId) else table_spec.get_column(key)
            column_id = column.id if isinstance(column, ColumnSpec) else column
            identity_row[column_id] = value
        stored_row = {
            column.id: identity_row.get(column.id)
            for column in table_spec.columns
        }
        self.table_state(table_spec.id).add_row(stored_row)
        for column in table_spec.columns:
            value = stored_row.get(column.id)
            self.column_state(column.id).remember(
                value,
                None if value is None else self.column_storage_key(column, value),
            )

    def column_storage_key(self, column: ColumnSpec | ColumnId, value: Any) -> Any:
        column_spec = column if isinstance(column, ColumnSpec) else self.column_state(column).spec
        return storage_key(value, column_spec.datatype, dialect=column_spec.dialect)


__all__ = ["ColumnState", "TableState", "SchemaRuntime", "RowContext"]
