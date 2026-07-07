"""
db_manager.py
Database connection manager using SQLAlchemy connection URLs.

Each call creates and owns its SQLAlchemy engine for the lifetime of the
connection context.  Temporary database workflows can drop and recreate
databases without stale cached engine or initialisation state.

Usage:
    with get_connection("sqlite:////path/to/mydb.sqlite", dialect="sqlite") as conn:
        conn.create_tables(ddl_string)
        rows = conn.execute("SELECT * FROM users", fetch="all")

    # class-based (for a custom logger threaded through all calls)
    mgr = DBManager(log=my_logger)
    with mgr.get_connection("sqlite:////path/to/mydb.sqlite", dialect="sqlite") as conn:
        ...
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from contextlib import contextmanager
from threading import Lock
from typing import Any, Dict, Generator, List, Literal, Optional, Tuple, Union, overload

from sqlglot import exp
from sqlalchemy import Connection, Engine, MetaData, URL, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool, StaticPool
from sqlalchemy.schema import CreateTable


# ---------------------------------------------------------------------------
# Dialect mappings + quoting helpers
# ---------------------------------------------------------------------------

_DIALECT_TO_BACKEND = {
    "sqlite": "sqlite",
    "mysql": "mysql",
    "postgres": "postgresql",
}

_DIALECT_TO_SQLGLOT = {
    "sqlite": "sqlite",
    "mysql": "mysql",
    "postgres": "postgres",
}


def _quote_postgres_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _quote_mysql_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def dispose_all() -> None:
    """
    Compatibility hook for callers that used to clear cached engines.

    Engines are no longer cached by this module, so there is no module-level
    state to dispose.
    """
    return None


# ---------------------------------------------------------------------------
# Connect — SQL execution wrapper
# ---------------------------------------------------------------------------

class Connect:
    """
    Executes SQL against a given SQLAlchemy engine.

    Do **not** instantiate directly; use :func:`get_connection` or
    :meth:`DBManager.get_connection`.
    """

    def __init__(self, engine: Engine, log: Optional[logging.Logger] = None) -> None:
        self.engine = engine
        self._log = log or logging.getLogger("qrank.db.connect")
        self._metadata: Optional[MetaData] = None

    def __enter__(self) -> "Connect":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        self._log.debug(
            "Closed connection to %s",
            self.engine.url.render_as_string(hide_password=True),
        )

    @property
    def metadata(self) -> MetaData:
        if self._metadata is None:
            self._metadata = MetaData()
            self._metadata.reflect(bind=self.engine)
        return self._metadata

    def _invalidate_metadata(self) -> None:
        self._metadata = None

    @overload
    def execute(
        self,
        stmt: str,
        parameters: Optional[Any] = ...,
        fetch: None = ...,
        with_column_names: bool = ...,
        timeout: int = ...,
    ) -> None: ...

    @overload
    def execute(
        self,
        stmt: str,
        parameters: Optional[Any] = ...,
        fetch: Union[Literal["all", "one", "random"], int] = ...,
        with_column_names: bool = ...,
        timeout: int = ...,
    ) -> List[Tuple[Any, ...]]: ...

    def execute(
        self,
        stmt: str,
        parameters: Optional[Any] = None,
        fetch: Optional[Union[Literal["all", "one", "random"], int]] = "all",
        with_column_names: bool = False,
        timeout: int = 15,
    ) -> Optional[List[Tuple[Any, ...]]]:
        is_sqlite = self.engine.url.get_backend_name() == "sqlite"
        results: Optional[List[Tuple[Any, ...]]] = None
        raw_conn = None
        guard: Optional["Connect._TimeoutGuard"] = None
        cancelled: Optional[Any] = None

        deadline = time.monotonic() + timeout

        with self.engine.begin() as conn:
            if is_sqlite:
                guard = Connect._TimeoutGuard()
                raw_conn, cancelled = self._arm_sqlite_timeout(conn, timeout, guard=guard)
            self._log.debug(
                "Preparing to execute query with timeout %d seconds: %.120s",
                timeout,
                stmt,
            )
            try:
                if parameters is None:
                    cursor_result = conn.exec_driver_sql(stmt)
                else:
                    cursor_result = conn.exec_driver_sql(stmt, parameters)
                if (
                    fetch is not None
                    and cursor_result is not None
                    and (cancelled is None or not cancelled.is_set())
                ):
                    results = self._fetch(cursor_result, fetch, deadline, with_column_names)
            except TimeoutError:
                raise
            except Exception as exc:
                if is_sqlite and "interrupted" in str(exc).lower():
                    self._log.error(
                        "Error executing query: %s. after %d, Error: %s",
                        stmt[:120],
                        timeout,
                        str(exc),
                    )
                raise
            finally:
                if guard is not None:
                    guard.disarm()
                if raw_conn is not None:
                    try:
                        if cancelled is not None:
                            cancelled.set()
                    except Exception:
                        ...
                    try:
                        raw_conn.set_progress_handler(None, 0)
                    except Exception:
                        pass
        return results

    class _TimeoutGuard:
        def __init__(self) -> None:
            self._lock = Lock()
            self._armed = True

        def is_armed(self) -> bool:
            with self._lock:
                return self._armed

        def disarm(self) -> None:
            with self._lock:
                self._armed = False

    @staticmethod
    def _arm_sqlite_timeout(conn: Connection, timeout: int, guard: "_TimeoutGuard"):
        raw_conn = conn.connection.dbapi_connection
        deadline = time.monotonic() + timeout
        cancelled = threading.Event()

        def _timer_interrupt() -> None:
            if not cancelled.wait(timeout=timeout) and guard.is_armed():
                try:
                    raw_conn.interrupt()
                except Exception:
                    pass

        threading.Thread(target=_timer_interrupt, daemon=True).start()

        def _progress():
            return 1 if time.monotonic() > deadline else 0

        raw_conn.set_progress_handler(_progress, 100)
        return raw_conn, cancelled

    @staticmethod
    def _fetch(
        cursor_result,
        fetch: Union[Literal["all", "one", "random"], int],
        deadline: Optional[float] = None,
        with_column_names: Optional[bool] = False,
    ) -> List[Tuple[Any, ...]]:
        chunk_size = 50

        def _check() -> bool:
            return deadline is not None and time.monotonic() > deadline

        if fetch in {"one", 1}:
            row = cursor_result.fetchone()
            rows: list = [row] if row is not None else []
        elif fetch == "random":
            sample = cursor_result.fetchmany(chunk_size)
            rows = [random.choice(sample)] if sample else []
        elif fetch == "all" or (isinstance(fetch, int) and fetch > 1):
            remaining = fetch if isinstance(fetch, int) else None
            rows = []
            while not _check():
                batch_limit = min(chunk_size, remaining) if remaining else chunk_size
                batch = cursor_result.fetchmany(batch_limit)
                if not batch:
                    break
                rows.extend(batch)
                if remaining is not None:
                    remaining -= len(batch)
                    if remaining <= 0:
                        break
        else:
            rows = []

        records: List[Tuple[Any, ...]] = [tuple(row) for row in rows]
        if with_column_names and records and hasattr(cursor_result, "keys"):
            records.insert(0, tuple(cursor_result.keys()))
        return records

    def create_tables(self, *ddls: str) -> None:
        for ddl in ddls:
            self.execute(ddl, fetch=None)
        self._invalidate_metadata()

    def clear_tables(self, *table_names: str) -> None:
        for name in table_names:
            self.execute(self._render_delete_table(name), fetch=None)

    def drop_table(self, table_name: str) -> None:
        self.execute(self._render_drop_table(table_name), fetch=None)
        self._invalidate_metadata()

    def insert(self, stmt: str, data: List[Dict[str, Any]]) -> None:
        self.execute(stmt, parameters=data, fetch=None)

    def get_schema(self) -> str:
        ddls: List[str] = []
        for table in self.metadata.tables.values():
            ddl = str(
                CreateTable(table).compile(compile_kwargs={"literal_binds": True})
            )
            ddls.append(ddl)
        return ";\n".join(ddls)

    def get_table_rows(self, table_name: str) -> Optional[List[Tuple[Any, ...]]]:
        table = self.metadata.tables[table_name]
        stmt = str(table.select().compile(compile_kwargs={"literal_binds": True}))
        return self.execute(stmt=stmt, fetch="all", with_column_names=True)

    def get_all_table_rows(self) -> Dict[str, Optional[List[Tuple[Any, ...]]]]:
        return {name: self.get_table_rows(name) for name in self.metadata.tables}

    def export_database(self) -> List[str]:
        statements: List[str] = [self.get_schema()]
        for table in self.metadata.tables.values():
            with self.engine.connect() as conn:
                rows = conn.execute(table.select()).fetchall()
            if not rows:
                continue
            values = [row._asdict() for row in rows]
            insert_stmt = (
                table.insert()
                .values(values)
                .compile(compile_kwargs={"literal_binds": True})
            )
            statements.append(str(insert_stmt))
        return statements

    def _render_delete_table(self, table_name: str) -> str:
        return exp.delete(self._table_identifier(table_name)).sql(
            dialect=self._sqlglot_dialect
        )

    def _render_drop_table(self, table_name: str) -> str:
        cascade = self._sqlglot_dialect == "postgres"
        return exp.Drop(
            this=self._table_identifier(table_name),
            kind="TABLE",
            exists=True,
            cascade=cascade,
        ).sql(dialect=self._sqlglot_dialect)

    @property
    def _sqlglot_dialect(self) -> str:
        backend_name = self.engine.url.get_backend_name()
        for dialect, backend in _DIALECT_TO_BACKEND.items():
            if backend == backend_name:
                return _DIALECT_TO_SQLGLOT[dialect]
        return backend_name

    @staticmethod
    def _table_identifier(table_name: str) -> exp.Table:
        return exp.Table(this=exp.Identifier(this=table_name, quoted=True))


# ---------------------------------------------------------------------------
# Backend providers
# ---------------------------------------------------------------------------

class _BackendProvider:
    dialect: str
    backend_name: str

    def ensure_database(self, url: URL) -> None:
        raise NotImplementedError

    def create_engine(
        self,
        url: URL,
        *,
        pool_size: int,
        max_overflow: int,
        pool_timeout: int,
        pool_recycle: int,
        connect_timeout: int,
    ) -> Engine:
        raise NotImplementedError


class _SQLiteProvider(_BackendProvider):
    dialect = "sqlite"
    backend_name = "sqlite"

    def ensure_database(self, url: URL) -> None:
        database = url.database
        if database in (None, "", ":memory:"):
            return
        parent = os.path.dirname(database)
        if parent:
            os.makedirs(parent, exist_ok=True)
        if not os.path.exists(database):
            open(database, "a").close()

    def create_engine(self, url, *, pool_size, max_overflow, pool_timeout, pool_recycle, connect_timeout) -> Engine:
        is_memory = url.database in (None, ":memory:")
        connect_args: Dict[str, Any] = {
            "check_same_thread": False,
            "timeout": connect_timeout,
        }
        if is_memory:
            return create_engine(url, poolclass=StaticPool, connect_args=connect_args)
        return create_engine(url, poolclass=NullPool, connect_args=connect_args)


class _MySQLProvider(_BackendProvider):
    dialect = "mysql"
    backend_name = "mysql"

    def ensure_database(self, url: URL) -> None:
        if not url.database:
            raise ValueError("MySQL connection string must include a database name")
        admin_url = url.set(database="")
        engine = create_engine(admin_url)
        try:
            with engine.begin() as conn:
                conn.execute(
                    text(f"CREATE DATABASE IF NOT EXISTS {_quote_mysql_identifier(url.database)}")
                )
        finally:
            engine.dispose()

    def create_engine(self, url, *, pool_size, max_overflow, pool_timeout, pool_recycle, connect_timeout) -> Engine:
        return create_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            pool_pre_ping=True,
            connect_args={"connect_timeout": connect_timeout},
        )


class _PostgresProvider(_BackendProvider):
    dialect = "postgres"
    backend_name = "postgresql"

    def ensure_database(self, url: URL) -> None:
        if not url.database:
            raise ValueError("Postgres connection string must include a database name")
        admin_url = url.set(database="postgres")
        engine = create_engine(admin_url)
        try:
            with engine.connect() as conn:
                conn = conn.execution_options(isolation_level="AUTOCOMMIT")
                result = conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :name"),
                    {"name": url.database},
                )
                if not result.fetchone():
                    conn.execute(
                        text(f"CREATE DATABASE {_quote_postgres_identifier(url.database)}")
                    )
        finally:
            engine.dispose()

    def create_engine(self, url, *, pool_size, max_overflow, pool_timeout, pool_recycle, connect_timeout) -> Engine:
        return create_engine(
            url,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            pool_pre_ping=True,
            connect_args={"connect_timeout": connect_timeout},
        )


# ---------------------------------------------------------------------------
# Module-level provider registry + internal helpers
# ---------------------------------------------------------------------------

_providers: Dict[str, _BackendProvider] = {
    "sqlite": _SQLiteProvider(),
    "mysql": _MySQLProvider(),
    "postgres": _PostgresProvider(),
}


def _normalize_target(
    connection_string: str,
    dialect: str,
) -> Tuple[URL, _BackendProvider]:
    provider = _providers.get(dialect)
    if provider is None:
        raise ValueError(
            f"Unsupported dialect '{dialect}'. Supported: {list(_providers)}"
        )
    url = make_url(connection_string)
    backend_name = url.get_backend_name()
    expected_backend = _DIALECT_TO_BACKEND[dialect]
    if backend_name != expected_backend:
        raise ValueError(
            f"Connection string backend '{backend_name}' does not match dialect '{dialect}'"
        )
    return url, provider


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@contextmanager
def get_connection(
    connection_string: str,
    dialect: Literal["sqlite", "mysql", "postgres"],
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_timeout: int = 15,
    pool_recycle: int = 60,
    connect_timeout: int = 25,
    create_if_missing: bool = True,
    log: Optional[logging.Logger] = None,
) -> Generator[Connect, None, None]:
    """
    Yield a :class:`Connect` instance bound to the requested database.

    The returned connection context owns its engine.  The database is ensured
    for each call when ``create_if_missing`` is true, and the engine is disposed
    when the context exits.
    """
    _log = log or logging.getLogger("qrank.db")
    url, provider = _normalize_target(connection_string, dialect)

    if create_if_missing:
        provider.ensure_database(url)

    engine = provider.create_engine(
        url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        connect_timeout=connect_timeout,
    )
    _log.debug(
        "Created engine for %s",
        url.render_as_string(hide_password=True),
    )
    try:
        yield Connect(engine=engine, log=_log)
    finally:
        engine.dispose()


class DBManager:
    """
    Thin factory for :class:`Connect` instances.

    No singleton, no shared state on the instance.  Each connection context owns
    and disposes its engine.

    The only reason to use DBManager over get_connection() directly is to bind
    a custom logger once and have it applied to every connection from that manager.
    """

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self._log = log or logging.getLogger("qrank.db")

    @contextmanager
    def get_connection(
        self,
        connection_string: str,
        dialect: Literal["sqlite", "mysql", "postgres"],
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout: int = 15,
        pool_recycle: int = 60,
        connect_timeout: int = 25,
        create_if_missing: bool = True,
    ) -> Generator[Connect, None, None]:
        with get_connection(
            connection_string=connection_string,
            dialect=dialect,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            connect_timeout=connect_timeout,
            create_if_missing=create_if_missing,
            log=self._log,
        ) as conn:
            yield conn


def execute_query(
    sql: str,
    connection_string: str,
    dialect: str = "sqlite",
    timeout: int = 60,
) -> "ExecutionResult":
    """Execute a query and return an ExecutionResult."""
    from parseval.states import ExecutionResult

    t0 = time.time()
    try:
        with get_connection(connection_string, dialect) as conn:
            rows = conn.execute(sql, fetch="all", timeout=timeout)
            return ExecutionResult(
                query=sql,
                rows=rows or [],
                elapsed_time=time.time() - t0,
            )
    except Exception as e:
        return ExecutionResult(
            query=sql,
            error_msg=str(e),
            elapsed_time=time.time() - t0,
        )
