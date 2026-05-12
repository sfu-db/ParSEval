from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Any, Dict, List, Optional, Set, Tuple

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

    def remember(self, value: Any) -> None:
        """Record a generated value, updating used_values and null_count."""
        self.generated_values.append(value)
        if value is None:
            self.null_count += 1
        else:
            self.used_values.add(value)


@dataclass
class TableState:
    """Tracks the generation state for a single table.

    Attributes:
        spec: The table specification.
        rows: List of generated rows (each row is a dict of column name to value).
    """

    spec: TableSpec
    rows: List[Dict[str, Any]] = field(default_factory=list)

    def add_row(self, row: Dict[str, Any]) -> None:
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
    values: Dict[str, Any] = field(default_factory=dict)
    provided_columns: Set[str] = field(default_factory=set)
    generated_columns: Set[str] = field(default_factory=set)

    def set_provided(self, column: str, value: Any) -> None:
        """Mark a column as explicitly provided with the given value."""
        normalized = column.lower()
        self.values[normalized] = value
        self.provided_columns.add(normalized)

    def set_generated(self, column: str, value: Any) -> None:
        """Mark a column as auto-generated with the given value."""
        normalized = column.lower()
        self.values[normalized] = value
        self.generated_columns.add(normalized)

    def get(self, column: str, default: Any = None) -> Any:
        """Retrieve a column value (case-insensitive lookup)."""
        return self.values.get(column.lower(), default)


@dataclass
class SchemaRuntime:
    """Mutable runtime state for the entire schema during data generation.

    Tracks all generated rows, used values (for uniqueness enforcement),
    and provides foreign key resolution data.

    Attributes:
        schema: The schema being generated.
        seed: Random seed for deterministic generation.
        rng: Random number generator instance.
        tables: Map of table name (lowered) to its TableState.
        columns: Map of qualified column name (table.column) to its ColumnState.
    """

    schema: SchemaSpec
    seed: int = 142
    rng: random.Random = field(init=False)
    tables: Dict[str, TableState] = field(init=False)
    columns: Dict[str, ColumnState] = field(init=False)

    def __post_init__(self) -> None:
        """Initialize the random number generator and build column state entries."""
        self.rng = random.Random(self.seed)
        self.tables = {table.name: TableState(table) for table in self.schema.tables}
        self.columns = {}
        for table in self.schema.tables:
            for column in table.columns:
                self.columns[column.qualified_name] = ColumnState(column)

    def table_state(self, table_name: str) -> TableState:
        """Look up the mutable state for a table by name (case-insensitive)."""
        return self.tables[table_name.lower()]

    def column_state(self, table_name: str, column_name: str) -> ColumnState:
        """Look up the mutable state for a column by table and column name (case-insensitive)."""
        return self.columns[f"{table_name.lower()}.{column_name.lower()}"]

    def referenced_values(self, column: ColumnSpec) -> Optional[List[Any]]:
        """Return all used values from the FK target column for single-column foreign keys.

        Returns None for composite foreign keys (more than one target column),
        since those must be resolved via ``referenced_key_tuples``.
        """
        foreign_key = column.foreign_key
        if foreign_key is None or len(foreign_key.target_columns) != 1:
            return None
        target_column = foreign_key.target_columns[0]
        target_state = self.column_state(foreign_key.target_table, target_column)
        return list(target_state.used_values)

    def referenced_key_tuples(
        self, foreign_key: ForeignKeySpec
    ) -> List[Tuple[Any, ...]]:
        """Return all tuples from the target table for composite FK resolution."""
        table_state = self.table_state(foreign_key.target_table)
        tuples: List[Tuple[Any, ...]] = []
        for row in table_state.rows:
            tuples.append(
                tuple(row.get(column.lower()) for column in foreign_key.target_columns)
            )
        return tuples

    def remember_row(self, table_name: str, row: Dict[str, Any]) -> None:
        """Persist a generated row into the runtime state for uniqueness/FK tracking."""
        table = self.schema.get_table(table_name)
        normalized_row = {key.lower(): value for key, value in row.items()}
        self.table_state(table.name).add_row(normalized_row)
        for column in table.columns:
            self.column_state(table.name, column.column).remember(
                normalized_row.get(column.column)
            )


__all__ = ["ColumnState", "TableState", "SchemaRuntime", "RowContext"]
