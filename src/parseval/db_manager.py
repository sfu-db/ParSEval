"""Database backends: connect, create tables, load instances, execute queries.

    database = DBManager("postgresql://user:password@host/db", "postgres")
    with database.connect() as conn:
        conn.create_tables(ddl)
        conn.load(instance)
        rows = conn.execute("SELECT * FROM users")

    # A fresh database, dropped afterwards, for replaying one instance:
    with database.scratch() as conn:
        ...

Instances are loaded with foreign-key checks on: tables in foreign-key order,
and the rows of a self-referencing table parents first, so the backend
validates the data as it arrives.
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from typing import Any, Literal
from uuid import uuid4

import sqlglot
from sqlglot import exp
from sqlalchemy import Connection, Engine, MetaData, Table, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy.schema import CreateTable

from parseval.instance import Instance
from parseval.terms.constraints import ForeignKeyDecl
from parseval.terms.sorts import IntervalValue

Dialect = Literal["sqlite", "mysql", "postgres"]
Fetch = Literal["all", "one", "random"] | int | None

_BACKENDS = {"sqlite": "sqlite", "mysql": "mysql", "postgres": "postgresql"}


class Connect:
    """Executes SQL against one engine; obtain it from :class:`DBManager`."""

    def __init__(self, engine: Engine, dialect: Dialect, log: logging.Logger | None = None) -> None:
        self.engine = engine
        self.dialect = dialect
        self._log = log or logging.getLogger("parseval.db")
        self._metadata: MetaData | None = None

    @property
    def metadata(self) -> MetaData:
        if self._metadata is None:
            self._metadata = MetaData()
            self._metadata.reflect(bind=self.engine)
        return self._metadata

    @contextmanager
    def begin(self) -> Generator[Connection, None, None]:
        """One connection and transaction; SQLite enforces foreign keys in it."""
        with self.engine.begin() as connection:
            if self.dialect == "sqlite":
                connection.exec_driver_sql("PRAGMA foreign_keys = ON")
            yield connection
        self._metadata = None

    # Statements.

    def execute(
        self,
        stmt: str,
        parameters: Any = None,
        fetch: Fetch = "all",
        with_column_names: bool = False,
        with_column_types: bool = False,
        timeout: float = 15,
    ) -> list[tuple] | None:
        """Run one statement within ``timeout`` seconds; fetch rows unless ``fetch`` is None.

        The rows are preceded by the column names, then the driver's column
        type codes, when asked for.
        """
        with self.begin() as connection:
            with self._deadline(connection, timeout):
                if parameters is None:
                    result = connection.exec_driver_sql(stmt, execution_options={"no_parameters": True})
                else:
                    result = connection.exec_driver_sql(stmt, parameters)
                if fetch is None or not result.returns_rows:
                    return None
                types = tuple(column[1] for column in result.cursor.description)
                rows = _fetch(result, fetch)
                if with_column_types:
                    rows.insert(0, types)
                if with_column_names:
                    rows.insert(0, tuple(result.keys()))
                return rows

    @contextmanager
    def _deadline(self, connection: Connection, timeout: float) -> Generator[None, None, None]:
        """Each backend's own statement timeout."""
        if self.dialect == "postgres":
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout * 1000)}")
            yield
        elif self.dialect == "mysql":
            connection.exec_driver_sql(f"SET SESSION max_execution_time = {int(timeout * 1000)}")
            yield
        else:
            raw = connection.connection.dbapi_connection
            deadline = time.monotonic() + timeout
            done = threading.Event()
            # The progress handler interrupts running bytecode; the timer also
            # interrupts a statement waiting outside it.
            timer = threading.Timer(timeout, lambda: done.is_set() or raw.interrupt())
            raw.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            timer.start()
            try:
                yield
            finally:
                done.set()
                timer.cancel()
                raw.set_progress_handler(None, 0)

    def create_tables(self, *ddls: str) -> None:
        """Run DDL scripts: tables in foreign-key order, as backends such as
        PostgreSQL require, then the other statements in their order."""
        statements = [statement for ddl in ddls for statement in sqlglot.parse(ddl, read=self.dialect) if statement]
        with self.begin() as connection:
            for statement in _creation_order(statements):
                connection.exec_driver_sql(statement.sql(dialect=self.dialect))

    def drop_table(self, name: str) -> None:
        self.metadata.tables[name].drop(self.engine)
        self._metadata = None

    def clear_tables(self, *names: str) -> None:
        with self.begin() as connection:
            for name in names:
                connection.execute(self.metadata.tables[name].delete())

    def insert(self, stmt: str, data: list[dict[str, Any]]) -> None:
        self.execute(stmt, parameters=data, fetch=None)

    # Instances.

    def load(self, instance: Instance) -> None:
        """Insert the stored rows of an instance into existing tables, in one transaction."""
        catalog = instance.catalog
        tables = {table.relation: table for table in catalog.tables()}
        with self.begin() as connection:
            for relation in _foreign_key_order(tables):
                decl = tables[relation]
                rows = _parents_first(decl, instance.rows(relation))
                if not rows:
                    continue
                table = self._table(decl)
                names = [self._name(table.columns.keys(), binding.name.text) for binding in decl.columns]
                # A plain INSERT stores exactly the instance's values; the
                # table construct would treat an integer key as autoincrement.
                quote = self.engine.dialect.identifier_preparer
                insert = text(
                    f"INSERT INTO {quote.format_table(table)} ({', '.join(map(quote.quote, names))}) "
                    f"VALUES ({', '.join(f':p{index}' for index in range(len(names)))})"
                )
                connection.execute(
                    insert, [{f"p{index}": self._value(value) for index, value in enumerate(row)} for row in rows]
                )

    def _table(self, decl) -> Table:
        *schema, name = (part.text for part in decl.name.parts)
        schema = schema[-1] if schema else None
        if schema is not None and not any(table.schema == schema for table in self.metadata.tables.values()):
            self.metadata.reflect(bind=self.engine, schema=schema)
        tables = {table.name: table for table in self.metadata.tables.values() if table.schema == schema}
        return tables[self._name(tables, name)]

    def _name(self, names, name: str) -> str:
        """The backend's spelling of a catalog name: SQLite and MySQL compare
        names without case, PostgreSQL as the catalog folded them."""
        if name in names or self.dialect == "postgres":
            return name
        return next(candidate for candidate in names if candidate.casefold() == name.casefold())

    @staticmethod
    def _value(value):
        """Drivers adapt Python values; intervals keep their months as text."""
        if isinstance(value, IntervalValue):
            return f"{value.months} mons {value.days} days {value.microseconds} microseconds"
        return value

    # Inspection.

    def get_schema(self) -> str:
        return ";\n".join(str(CreateTable(table).compile(self.engine)) for table in self.metadata.tables.values())

    def get_table_rows(self, name: str) -> list[tuple]:
        """Rows as the driver returns them: SQLite date columns may hold any text."""
        with self.begin() as connection:
            result = connection.exec_driver_sql(str(self.metadata.tables[name].select().compile(self.engine)))
            return [tuple(result.keys()), *map(tuple, result)]

    def get_all_table_rows(self) -> dict[str, list[tuple]]:
        return {name: self.get_table_rows(name) for name in self.metadata.tables}


def _fetch(result, fetch: Fetch) -> list[tuple]:
    if fetch in ("one", 1):
        row = result.fetchone()
        return [] if row is None else [tuple(row)]
    if fetch == "random":
        rows = result.fetchall()
        return [tuple(random.choice(rows))] if rows else []
    rows = result.fetchall() if fetch == "all" else result.fetchmany(fetch)
    return [tuple(row) for row in rows]


def _creation_order(statements: list) -> list:
    """CREATE TABLE statements with referenced tables first, then the rest."""
    def name(table: exp.Table) -> str:
        return table.name.casefold()

    creates = {
        name(statement.this.find(exp.Table)): statement
        for statement in statements
        if isinstance(statement, exp.Create) and statement.kind == "TABLE"
    }
    references = {
        table: {name(reference.this.find(exp.Table)) for reference in statement.find_all(exp.Reference)} & creates.keys()
        for table, statement in creates.items()
    }
    order = _topological(references, lambda table: table)
    return [creates[table] for table in order] + [statement for statement in statements if statement not in creates.values()]


def _topological(edges: dict, label) -> list:
    """Nodes with every node they point to first; a cycle cannot be ordered."""
    order, state = [], {}

    def visit(node, path):
        if state.get(node) == "done":
            return
        if state.get(node) == "open":
            raise ValueError("Foreign keys form a cycle: " + " -> ".join(map(label, (*path, node))))
        state[node] = "open"
        for other in edges[node]:
            if other != node:
                visit(other, (*path, node))
        state[node] = "done"
        order.append(node)

    for node in edges:
        visit(node, ())
    return order


def _foreign_key_order(tables: dict) -> list:
    """Relations with every referenced relation first."""
    edges = {
        relation: {item.target_relation for item in table.constraints if isinstance(item, ForeignKeyDecl)}
        for relation, table in tables.items()
    }
    return _topological(edges, lambda relation: tables[relation].name.qualified_name)


def _parents_first(table, rows: Sequence[tuple]) -> list[tuple]:
    """Rows of a self-referencing table ordered so a referenced row comes first."""
    links = [
        ([table.spec.column_position(column) for column in item.source],
         [table.spec.column_position(column) for column in item.target])
        for item in table.constraints
        if isinstance(item, ForeignKeyDecl) and item.target_relation == table.relation
    ]
    if not links:
        return list(rows)
    pending, placed, order = list(rows), set(), []
    while pending:
        ready = [
            row for row in pending
            if all(
                None in (key := tuple(row[p] for p in source))
                or key == tuple(row[p] for p in target)
                or (tuple(target), key) in placed
                for source, target in links
            )
        ]
        if not ready:
            raise ValueError(f"Rows of {table.name.qualified_name} reference each other in a cycle")
        for row in ready:
            pending.remove(row)
            order.append(row)
            placed.update((tuple(target), tuple(row[p] for p in target)) for _, target in links)
    return order


# Backends.


def _ensure(url: URL, dialect: Dialect) -> None:
    """Create the database of a URL if it does not exist."""
    if dialect == "sqlite":
        if url.database not in (None, "", ":memory:"):
            os.makedirs(os.path.dirname(os.path.abspath(url.database)), exist_ok=True)
        return
    if not url.database:
        raise ValueError(f"A {dialect} URL must name a database")
    with _admin(url, dialect) as connection:
        if dialect == "postgres":
            exists = connection.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database})
            if not exists.first():
                connection.exec_driver_sql(f"CREATE DATABASE {_quote(url.database, dialect)}")
        else:
            connection.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS {_quote(url.database, dialect)}")


def _drop(url: URL, dialect: Dialect) -> None:
    if dialect == "sqlite":
        if url.database not in (None, "", ":memory:") and os.path.exists(url.database):
            os.remove(url.database)
        return
    with _admin(url, dialect) as connection:
        force = " WITH (FORCE)" if dialect == "postgres" else ""
        connection.exec_driver_sql(f"DROP DATABASE IF EXISTS {_quote(url.database, dialect)}{force}")


@contextmanager
def _admin(url: URL, dialect: Dialect) -> Generator[Connection, None, None]:
    """An autocommit connection to the server's maintenance database."""
    engine = create_engine(url.set(database="postgres" if dialect == "postgres" else ""), poolclass=NullPool)
    try:
        with engine.connect() as connection:
            yield connection.execution_options(isolation_level="AUTOCOMMIT")
    finally:
        engine.dispose()


def _quote(identifier: str, dialect: Dialect) -> str:
    mark = "`" if dialect == "mysql" else '"'
    return mark + identifier.replace(mark, mark * 2) + mark


def _engine(url: URL, dialect: Dialect, connect_timeout: int) -> Engine:
    if dialect == "sqlite":
        args = {"check_same_thread": False, "timeout": connect_timeout}
        pool = StaticPool if url.database in (None, "", ":memory:") else NullPool
        return create_engine(url, poolclass=pool, connect_args=args)
    return create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": connect_timeout})


class DBManager:
    """A database server or file, given by an SQLAlchemy URL and its dialect.

    ``connect`` opens the URL's database, creating it if missing; ``scratch``
    opens a new database on the same server, dropped afterwards. Each opened
    connection owns its engine.
    """

    def __init__(
        self, connection_string: str, dialect: Dialect, *,
        connect_timeout: int = 25, log: logging.Logger | None = None,
    ) -> None:
        if dialect not in _BACKENDS:
            raise ValueError(f"Unsupported dialect {dialect!r}; supported: {sorted(_BACKENDS)}")
        url = make_url(connection_string)
        if url.get_backend_name() != _BACKENDS[dialect]:
            raise ValueError(f"URL backend {url.get_backend_name()!r} does not match dialect {dialect!r}")
        self.url = url
        self.dialect = dialect
        self.connect_timeout = connect_timeout
        self._log = log or logging.getLogger("parseval.db")

    @contextmanager
    def connect(self, *, create_if_missing: bool = True) -> Generator[Connect, None, None]:
        if create_if_missing:
            _ensure(self.url, self.dialect)
        with self._open(self.url) as connection:
            yield connection

    @contextmanager
    def scratch(self) -> Generator[Connect, None, None]:
        """A new, uniquely named database, dropped afterwards; in memory for SQLite."""
        url = self.url.set(database=":memory:" if self.dialect == "sqlite" else "parseval_" + uuid4().hex)
        try:
            _ensure(url, self.dialect)
            with self._open(url) as connection:
                yield connection
        finally:
            _drop(url, self.dialect)

    @contextmanager
    def _open(self, url: URL) -> Generator[Connect, None, None]:
        engine = _engine(url, self.dialect, self.connect_timeout)
        try:
            yield Connect(engine, self.dialect, self._log)
        finally:
            engine.dispose()


__all__ = ["Connect", "DBManager"]
