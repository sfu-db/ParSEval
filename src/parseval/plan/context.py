from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Tuple, Optional, List, TYPE_CHECKING
from itertools import product

from sqlglot import exp

from parseval.helper import normalize_name
from parseval.identity import PARSEVAL_COLUMN_ID, ColumnId

if TYPE_CHECKING:
    from parseval.instance import Instance
    from parseval.dtype import DATATYPE
    from .rex import Symbol


@dataclass(frozen=True)
class AggregateGroup:
    """Execution metadata for one SQL aggregate output row."""

    output_row_id: Tuple[Any, ...]
    group_key: Tuple[Any, ...]
    source_row_ids: Tuple[Tuple[Any, ...], ...]
    aggregate_values: Mapping[Any, Any]
    group_expressions: Mapping[ColumnId, exp.Expression] = field(default_factory=dict)
    group_sources: Mapping[ColumnId, Tuple[ColumnId, ...]] = field(default_factory=dict)
    group_key_values: Mapping[ColumnId, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WindowFrame:
    """Execution metadata for one window-derived value on one output row."""

    column_id: ColumnId
    source_row_id: Tuple[Any, ...]
    partition_key: Tuple[Any, ...]
    order_key: Tuple[Any, ...]
    frame_row_ids: Tuple[Tuple[Any, ...], ...]
    value: Any


class Row(exp.Expression):
    """One logical row produced by a plan step.

    A ``Row`` pairs a stable row identity (``this`` — a tuple of ids) with
    a mapping from column identity/name to the cell value. Rows are the
    unit of currency for :class:`DerivedSchema` and
    flow through :class:`parseval.symbolic.encoder.SymbolicScopeEncoder`
    between plan steps.

    Although it subclasses :class:`sqlglot.exp.Expression` (so it can be
    embedded in SQL-like pretty-printing and share the Symbol dispatch),
    ``Row`` is a runtime container, not an AST node used for planning.
    Keep the AST-flavored operations (``sql()`` etc.) minimal.
    """

    arg_types = {"this": True, "columns": True}

    @property
    def column_values(self) -> Mapping[Any, "Symbol"]:
        return self.args.get("columns", {})

    @property
    def columns(self) -> Tuple[Any, ...]:
        return tuple(self.column_values.keys())

    @property
    def rowid(self) -> Tuple[Any, ...]:
        if isinstance(self.this, tuple):
            return self.this
        return (self.this,)

    def _key_name(self, key):
        if isinstance(key, ColumnId):
            return key.name.normalized
        if isinstance(key, exp.Expression):
            return key.alias_or_name or key.sql()
        return str(key)

    def items(self):
        return self.column_values.items()

    def values(self):
        return self.column_values.values()

    def __iter__(self):
        return iter(self.column_values)

    def __contains__(self, key):
        try:
            self[key]
        except KeyError:
            return False
        return True

    def __getitem__(self, key):
        columns = self.column_values
        if key in columns:
            return columns[key]
        if isinstance(key, ColumnId):
            source = key.source_column_id
            if source is not None and source in columns:
                return columns[source]
        if isinstance(key, exp.Column):
            resolved = key.meta.get(PARSEVAL_COLUMN_ID)
            if isinstance(resolved, ColumnId):
                if resolved in columns:
                    return columns[resolved]
                source = resolved.source_column_id
                if source is not None and source in columns:
                    return columns[source]
        normalized = normalize_name(self._key_name(key))
        for column_name, value in columns.items():
            if normalize_name(self._key_name(column_name)) == normalized:
                return value
        raise KeyError(key)

    def get(self, table, column):
        del table
        return self[column]

    def __len__(self):
        return len(self.column_values)

    def __add__(self, other):
        assert isinstance(other, Row), f"Cannot add Row with {type(other)}"
        new_columns = {**self.column_values, **other.column_values}
        rid = self.rowid + other.rowid
        return Row(this=rid, columns=new_columns)

    def sql(self, dialect=None, **opts):
        return f"{self.key}({self.this})"


class DerivedSchema:
    def __init__(
        self,
        columns,
        rows=None,
        column_range=None,
        datatypes: Optional[Dict[Any, DATATYPE]] = None,
        nullables: Optional[Dict[Any, bool]] = None,
        uniqueness: Optional[Dict[Any, bool]] = None,
        aggregate_groups: Optional[Dict[Tuple[Any, ...], AggregateGroup]] = None,
        window_frames: Optional[Dict[Tuple[Any, ...], Tuple[WindowFrame, ...]]] = None,
    ):
        self.columns = tuple(columns)
        self.column_range = column_range
        self.reader = RowReader(self.columns, self.column_range)
        self.rows = rows or []
        self.mask = [True] * len(self.rows)
        self.datatypes = datatypes or {}
        self.nullables = nullables or {}
        self.uniqueness: Dict[Any, bool] = uniqueness or {}
        self.aggregate_groups = aggregate_groups or {}
        self.window_frames = window_frames or {}

        if rows:
            assert len(rows[0]) == len(
                self.columns
            ), f"Row length does not match number of columns. {len(rows[0])} != {len(self.columns)}"
        self.range_reader = RangeReader(self)

    def is_unique(self, column):
        return self.uniqueness.get(column, False)

    def nullable(self, column):
        return self.nullables.get(column, False)

    def get_column_type(self, column):
        return self.datatypes.get(column, None)

    def add_columns(self, *columns: Any) -> None:
        self.columns += columns
        if self.column_range:
            self.column_range = range(
                self.column_range.start, self.column_range.stop + len(columns)
            )
        self.reader = RowReader(self.columns, self.column_range)

    def append(self, row):
        assert len(row) == len(
            self.columns
        ), f"Row length does not match number of columns. {len(row)} != {len(self.columns)}"
        self.rows.append(row)
        self.mask.append(True)

    def pop(self):
        row = self.rows.pop()
        self.aggregate_groups.pop(row.rowid, None)
        self.window_frames.pop(row.rowid, None)

    def with_rows(
        self,
        rows: List[Row],
        *,
        columns: Optional[Tuple[Any, ...]] = None,
        column_range=None,
    ) -> "DerivedSchema":
        row_ids = {row.rowid for row in rows}
        return DerivedSchema(
            columns=self.columns if columns is None else columns,
            rows=rows,
            column_range=self.column_range if column_range is None else column_range,
            datatypes=self.datatypes,
            nullables=self.nullables,
            uniqueness=self.uniqueness,
            aggregate_groups={
                row_id: group
                for row_id, group in self.aggregate_groups.items()
                if row_id in row_ids
            },
            window_frames={
                row_id: frames
                for row_id, frames in self.window_frames.items()
                if row_id in row_ids
            },
        )

    @property
    def width(self):
        return len(self.columns)

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        return TableIter(self)

    def __getitem__(self, index):
        self.reader.row = self.rows[index]
        return self.reader

    def __repr__(self):
        columns = tuple(
            column
            for i, column in enumerate(self.columns)
            if not self.column_range or i in self.column_range
        )
        lines = [" ".join(str(column) for column in columns)]

        for i, row in enumerate(self):
            if i > 10:
                break

            lines.append(
                " ".join(
                    str(row[column])
                    for column in columns  # .rjust(widths[column])[0 : widths[column]]
                )
            )
        return "\n".join(lines)


class TableIter:
    def __init__(self, table):
        self.table = table
        self.index = -1

    def __iter__(self):
        return self

    def __next__(self):
        self.index += 1
        if self.index < len(self.table):
            return self.table[self.index]
        raise StopIteration


class RangeReader:
    def __init__(self, table):
        self.table = table
        self.range = range(0)

    def __len__(self):
        return len(self.range)

    def __getitem__(self, column):
        return (self.table[i][column] for i in self.range)


class RowReader:
    def __init__(self, columns, column_range=None):
        self.columns = {
            column: i
            for i, column in enumerate(columns)
            if not column_range or i in column_range
        }
        self.row: Row | None = None

    def rowid(self):
        if self.row is not None:
            return self.row.rowid

    def __getitem__(self, column):
        if self.row is not None:
            return self.row[column]

    def get(self, table, column):
        del table
        if self.row is not None:
            return self.row[column]

    def items(self):
        if self.row is not None:
            return self.row.items()
        return ()

    def get_cell(self, table, column):
        if self.row is not None:
            return self.row[self.columns[column]]


class ProductReader:
    def __init__(self, table_names, rows):
        self._names = table_names
        self._rows = rows

    def rowid(self):
        return sum((row.rowid for row in self._rows), ())

    def get(self, table, column):
        idx = self._names.index(table)
        return self._rows[idx][column]


class Context:
    """
    Encoding context for sql expressions.
    Context is used to hold relevant data tables which can then be queried on with eval.
    References to columns can either be scalar or vectors. When set_row is used, column references
    evaluate to scalars while set_range evaluates to vectors. This allows convenient and efficient
    evaluation of aggregation functions.
    """

    def __init__(
        self, tables: Dict[str, DerivedSchema], external: Optional[Context] = None
    ) -> None:
        """
        Args
            tables: representing the scope of the current execution context.
            env: dictionary of functions within the execution context.
        """
        self.tables = tables
        self._table: Optional[DerivedSchema] = None
        self.range_readers = {
            name: table.range_reader for name, table in self.tables.items()
        }

        self.row_readers = {name: table.reader for name, table in tables.items()}

        self.external = external
        self.masks = set()

    @property
    def table(self):
        if self._table is None:
            self._table = list(self.tables.values())[0]
        return self._table

    def add_columns(self, *columns: str) -> None:
        for table in self.tables.values():
            table.add_columns(*columns)

    @property
    def columns(self) -> Tuple:
        return self.table.columns

    def resolve_table(self, name: str) -> DerivedSchema:
        """
        Resolve table through lexical scope chain.
        """
        if name in self.tables:
            return self.tables[name]
        if self.external:
            return self.external.resolve_table(name)
        raise KeyError(f"Table '{name}' not found in scope chain.")

    def resolve_reader(self, name: str):
        """
        Resolve row reader for correlated access.
        """
        if name in self.row_readers:
            return self.row_readers[name]
        if self.external:
            return self.external.resolve_reader(name)
        raise KeyError(f"Reader '{name}' not found.")

    def __contains__(self, table: str) -> bool:
        return table in self.tables

    def __iter__(self):
        # self.env["scope"] = self.row_readers
        for i in range(len(self.table.rows)):
            for table in self.tables.values():
                reader = table[i]
            yield reader, self

    def table_iter(self, table: str) -> TableIter:
        return iter(self.tables[table])

    def set_mask(self, rowid) -> None:
        self.masks.add(rowid)

    def iters(self, mask=True):
        names = list(self.tables.keys())
        tables = [self.tables[name].rows for name in names]
        for rows in product(*tables):
            reader = ProductReader(names, rows)
            if mask and reader.rowid() in self.masks:
                continue
            yield reader


def build_context_from_instance(instance: Instance) -> Context:
    tables = {}
    for table_name, columns in instance.tables.items():
        rows = instance.get_rows(table_name)
        relation = instance.table_id(table_name)
        derived_columns = []
        datatypes = {}
        uniques = {}
        nullables = {}
        for col_name in columns:
            col_id = instance._stored_column_id(relation, col_name)
            dtype = instance.get_column_type(table_name, col_name)
            is_unique = instance.is_unique(relation, col_id)
            nullable = instance.nullable(relation, col_id)
            datatypes[col_id] = dtype
            uniques[col_id] = is_unique
            nullables[col_id] = nullable
            derived_columns.append(col_id)
        tables[table_name] = DerivedSchema(
            columns=derived_columns,
            rows=rows,
            datatypes=datatypes,
            uniqueness=uniques,
            nullables=nullables,
        )
    return Context(tables=tables)
