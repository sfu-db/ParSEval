from __future__ import annotations
from collections import UserDict
from contextlib import contextmanager
from typing import Any, Dict, Tuple, Optional, List

import logging

class DerivedSchema:
    def __init__(self, columns, rows=None, column_range=None):
        self.columns = tuple(columns)
        self.column_range = column_range
        self.reader = RowReader(self.columns, self.column_range)
        self.rows = rows or []
        
        if rows:
            assert len(rows[0]) == len(self.columns), f"Row length does not match number of columns. {len(rows[0])} != {len(self.columns)}"
        self.range_reader = RangeReader(self)

    def add_columns(self, *columns: str) -> None:
        self.columns += columns
        if self.column_range:
            self.column_range = range(
                self.column_range.start, self.column_range.stop + len(columns)
            )
        self.reader = RowReader(self.columns, self.column_range)

    def append(self, row):
        assert len(row) == len(self.columns), f"Row length does not match number of columns. {len(row)} != {len(self.columns)}"
        self.rows.append(row)

    def pop(self):
        self.rows.pop()
    
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
        widths = {column: len(column) for column in columns}
        lines = [" ".join(column for column in columns)]

        for i, row in enumerate(self):
            if i > 10:
                break

            lines.append(
                " ".join(
                    str(row[column]) for column in columns # .rjust(widths[column])[0 : widths[column]]
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
            column: i for i, column in enumerate(columns) if not column_range or i in column_range
        }
        self.row = None

    def __getitem__(self, column):
        return self.row[column]


class Context:
    """
    Encoding context for sql expressions.
    Context is used to hold relevant data tables which can then be queried on with eval.
    References to columns can either be scalar or vectors. When set_row is used, column references
    evaluate to scalars while set_range evaluates to vectors. This allows convenient and efficient
    evaluation of aggregation functions.
    """

    def __init__(self, tables: Dict[str, DerivedSchema], external: Optional[Context] = None) -> None:
        """
        Args
            tables: representing the scope of the current execution context.
            env: dictionary of functions within the execution context.
        """
        self.tables = tables
        self._table: Optional[DerivedSchema] = None
        self.range_readers = {name: table.range_reader for name, table in self.tables.items()}
        self.row_readers = {name: table.reader for name, table in tables.items()}
        self.external = external

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
        self.env["scope"] = self.row_readers
        for i in range(len(self.table.rows)):
            for table in self.tables.values():
                reader = table[i]
            yield reader, self

    