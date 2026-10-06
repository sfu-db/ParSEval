"""Export benchmark instances as portable SQLite tables and rows."""
from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from parseval.terms.sorts import IntervalValue, TypeKind
from parseval.instance import Instance


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _value(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, IntervalValue):
        return json.dumps([value.months, value.days, value.microseconds])
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    raise TypeError(f'Cannot store {type(value).__name__} in SQLite')


def write_sqlite(instance: Instance, path: str | Path, *, overwrite: bool = False) -> Path:
    """Save all tables, including empty ones, preserving NULLs and duplicates.

    This stores an instance; it does not translate or execute the source SQL.
    SQL FLOAT/DECIMAL cells use REAL, dates/times use ISO text, and intervals
    use [months, days, microseconds] JSON text. Source-dialect constraints are
    validated by the generator rather than transplanted to SQLite.

    A complete file is published atomically. Existing files are preserved
    unless overwrite=True, including when serialization fails.
    """
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    tables = instance.catalog.tables()
    leaf_names = [table.name.parts[-1].text for table in tables]
    # SQLite has no persistent schemas in one file. Qualify colliding names.
    names = [('.'.join(part.text for part in table.name.parts)
              if sum(name.casefold() == leaf.casefold() for name in leaf_names) > 1 else leaf)
             for table, leaf in zip(tables, leaf_names, strict=True)]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError('Catalog table names collide after SQLite qualification')
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{destination.name}.', suffix='.tmp', dir=destination.parent)
    os.close(descriptor)
    try:
        connection = sqlite3.connect(temporary)
        try:
            with connection:
                for table, name in zip(tables, names, strict=True):
                    columns = []
                    for binding in table.columns:
                        kind = table.column_spec(binding.id).sort.sql_type.kind
                        storage = ('INTEGER' if kind in (TypeKind.INTEGER, TypeKind.BOOLEAN)
                                   else 'REAL' if kind in (TypeKind.FLOAT, TypeKind.DECIMAL)
                                   else 'BLOB' if kind is TypeKind.OPAQUE else 'TEXT')
                        columns.append(f'{_quote(binding.name.text)} {storage}')
                    connection.execute(f'CREATE TABLE {_quote(name)} ({", ".join(columns)})')
                    placeholders = ', '.join('?' for _ in table.columns)
                    connection.executemany(f'INSERT INTO {_quote(name)} VALUES ({placeholders})',
                        (tuple(_value(value) for value in row) for row in instance.rows(table.relation)))
        finally:
            connection.close()
        if overwrite:
            os.replace(temporary, destination)
        else:
            # link is atomic and refuses an existing destination, including a
            # file created by another writer after the initial existence check.
            os.link(temporary, destination)
        return destination
    finally:
        Path(temporary).unlink(missing_ok=True)


__all__ = ['write_sqlite']
