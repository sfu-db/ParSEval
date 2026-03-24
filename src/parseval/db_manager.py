"""
db_manager.py
Database connection manager with support for SQLite and MySQL.

Usage:
    with DBManager().get_connection(
        host_or_path="/path/to/dir",
        database="mydb.sqlite",
        dialect="sqlite"
    ) as conn:
        conn.create_tables(ddl_string)
        rows = conn.execute("SELECT * FROM users", fetch="all")
"""

from __future__ import annotations

from sqlalchemy.schema import CreateTable
from sqlalchemy import create_engine, text, Connection, MetaData, Engine, URL
from sqlalchemy.pool import NullPool, StaticPool
from threading import Lock
from typing import (
    List,
    Tuple,
    Any,
    Dict,
    Union,
    Literal,
    overload,
    Optional,
    NewType,
    Callable,
    Generator,
)
from collections import defaultdict
import random, logging, os
from contextlib import contextmanager
import time, threading
import atexit
from concurrent.futures import ThreadPoolExecutor


_SQLITE_URL: Callable = (
    lambda host_or_path, *, port=None, username=None, password=None, database: URL.create(
        "sqlite",
        database=os.path.join(host_or_path, database),
    )
)

_MYSQL_URL: Callable = (
    lambda host_or_path, *, port=3306, username, password, database: URL.create(
        "mysql+mysqldb",
        username=username,
        password=password,
        host=host_or_path,
        port=port or 3306,
        database=database,
    )
)

_POSTGRES_URL: Callable = (
    lambda host_or_path, *, port=5432, username, password, database: URL.create(
        "postgresql+psycopg2",
        username=username,
        password=password,
        host=host_or_path,
        port=port or 5432,
        database=database,
    )
)

_CONNECTION_STR_MAPPING: Dict[str, Callable] = {
    "sqlite": _SQLITE_URL,
    "mysql": _MYSQL_URL,
    "postgres": _POSTGRES_URL,
}


def singleton(cls):
    instance = {}
    _lock: Lock = Lock()

    def _singleton(*args, **kwargs):
        with _lock:
            if cls not in instance:
                instance[cls] = cls(*args, **kwargs)
            return instance[cls]

    return _singleton


class singletonMeta(type):
    """
    This is a thread-safe implementation of Singleton.
    """

    _instances = {}
    _lock: Lock = Lock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[cls] = instance
        return cls._instances[cls]


class Connect:
    """
    Executes SQL against a given SQLAlchemy engine.

    Do **not** instantiate directly; use :meth:`DBManager.get_connection`.
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
        """Force a metadata refresh on the next access."""
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
        """
        Execute *stmt* and optionally return rows.

        Args:
            stmt:             SQL statement string.
            parameters:       Bound parameters (dict or list of dicts for bulk ops).
            fetch:            ``"all"`` | ``"one"`` | ``"random"`` | integer N | ``None``.
                              ``None`` performs a write with no result set.
            with_column_names: Prepend a tuple of column names as the first element.
            timeout:          Per-query timeout in seconds (SQLite only; for MySQL use
                              server-side ``wait_timeout``).

        Returns:
            List of row tuples, a single row tuple, or ``None``.
        Raises:
            TimeoutError: If the query exceeds *timeout* seconds.
        """

        is_sqlite = "sqlite" in str(self.engine.url)
        results: Optional[List[Tuple[Any, ...]]] = None
        raw_conn = None
        cursor_result = None
        guard: Optional["Connect._TimeoutGuard"] = None
        cancelled: Optional[Any] = None

        start_time = time.monotonic()
        deadline = start_time + timeout

        with self.engine.begin() as conn:
            if is_sqlite:
                guard = Connect._TimeoutGuard()
                raw_conn, cancelled = self._arm_sqlite_timeout(
                    conn, timeout, guard=guard
                )
            self._log.debug(
                "Preparing to execute query with timeout %d seconds: %.120s",
                timeout,
                stmt,
            )
            try:
                cursor_result = conn.execute(text(stmt), parameters or {})
                if (
                    fetch is not None
                    and cursor_result is not None
                    and (cancelled is None or not cancelled.is_set())
                ):
                    self._log.debug(
                        "Fetching results with fetch=%s, timeout: %s",
                        fetch,
                        str(timeout),
                    )
                    results = self._fetch(
                        cursor_result, fetch, deadline, with_column_names
                    )
            except TimeoutError as exc:
                self._log.error(
                    "Query timed out: %s. after %d seconds. Error: %s",
                    stmt[:120],
                    timeout,
                    str(exc),
                )
                raise
            except Exception as exc:
                if is_sqlite and "interrupted" in str(exc).lower():
                    self._log.error(
                        "Error executing query: %s. after %d, Error: %s",
                        stmt[:120],
                        timeout,
                        str(exc),
                    )
                raise exc
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
        """
        A thin armed/disarmed flag shared between the query thread and the
        timer thread.  The timer thread checks ``is_armed()`` under a lock
        before calling ``interrupt()``, so once the main thread calls
        ``disarm()`` the timer can never touch the (possibly closed)
        connection again.
        """

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
        """
        Install a SQLite progress handler that aborts the query after *timeout* seconds.
        Returns the raw dbapi connection so the caller can disarm it in a finally block.
        """
        raw_conn = conn.connection.dbapi_connection
        deadline = time.monotonic() + timeout
        cancelled = threading.Event()

        def _timer_interrupt() -> None:
            if not cancelled.wait(timeout=timeout):
                # Timeout elapsed.  Check the guard under its lock before
                # touching the connection — by this point the main thread
                # may have already closed it via NullPool.
                if guard.is_armed():
                    try:
                        raw_conn.interrupt()
                    except Exception:
                        pass

        timer = threading.Thread(target=_timer_interrupt, daemon=True)
        timer.start()

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
        _CHUNK = 50

        def _check() -> bool:
            return deadline is not None and time.monotonic() > deadline

        if fetch in {"one", 1}:
            row = cursor_result.fetchone()
            rows: list = [row] if row is not None else []

        elif fetch == "random":
            sample = cursor_result.fetchmany(_CHUNK)
            rows = [random.choice(sample)] if sample else []

        elif fetch == "all" or (isinstance(fetch, int) and fetch > 1):
            remaining = fetch if isinstance(fetch, int) else None
            rows = []
            while not _check():
                batch_size = min(_CHUNK, remaining) if remaining else _CHUNK
                batch = cursor_result.fetchmany(batch_size)
                if not batch:
                    break
                rows.extend(batch)
                if remaining is not None:
                    remaining -= len(batch)
                    if remaining <= 0:
                        break
        else:
            rows = []
        records: List[Tuple[Any, ...]] = [tuple(r) for r in rows]
        if with_column_names and records and hasattr(cursor_result, "keys"):
            records.insert(0, tuple(cursor_result.keys()))

        return records

        # if fetch == "all":
        #     rows = cursor_result.fetchall()
        # elif fetch in {"one", 1}:
        #     row = cursor_result.fetchone()
        #     rows = [row] if row is not None else []
        # elif fetch == "random":
        #     sample = cursor_result.fetchmany(20)
        #     rows = [random.choice(sample)] if sample else []
        # elif isinstance(fetch, int) and fetch > 1:
        #     rows = cursor_result.fetchmany(fetch)
        # else:
        #     rows = []

    def create_tables(self, *ddls: str) -> None:
        """Execute one or more DDL statements (CREATE TABLE …)."""
        for ddl in ddls:
            self.execute(ddl, fetch=None)
        self._invalidate_metadata()

    def drop_table(self, table_name: str) -> None:
        """Drop a table if it exists."""
        self.execute(f"DROP TABLE IF EXISTS {table_name}", fetch=None)
        self._invalidate_metadata()

    def insert(self, stmt: str, data: List[Dict[str, Any]]) -> None:
        """
        Bulk-insert *data* using *stmt* (e.g. ``INSERT INTO t VALUES (:col1, :col2)``).
        """
        self.execute(stmt, parameters=data, fetch=None)

    def get_schema(self) -> str:
        """Return CREATE TABLE DDL for every table in the database."""
        ddls: List[str] = []
        for table in self.metadata.tables.values():
            ddl = str(
                CreateTable(table).compile(compile_kwargs={"literal_binds": True})
            )
            ddls.append(ddl)
        return ";\n".join(ddls)

    def get_table_rows(self, table_name: str) -> Optional[List[Tuple[Any, ...]]]:
        """Return all rows (with column names as first row) from *table_name*."""
        table = self.metadata.tables[table_name]
        stmt = str(table.select().compile(compile_kwargs={"literal_binds": True}))
        return self.execute(stmt=stmt, fetch="all", with_column_names=True)

    def get_all_table_rows(self) -> Dict[str, Optional[List[Tuple[Any, ...]]]]:
        """
        Return the full contents of every table.

        Returns:
            ``{table_name: [(col_names_tuple), row1, row2, …]}``
        """
        return {name: self.get_table_rows(name) for name in self.metadata.tables}

    def export_database(self) -> List[str]:
        """
        Serialize the entire database as a list of DDL + INSERT statements.
        Useful for backups or test fixtures.
        """
        statements: List[str] = [self.get_schema()]

        for table_name, table in self.metadata.tables.items():
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


class DBManager(metaclass=singletonMeta):
    """
    Maintain a connection pool to connect to various databases. Use as
    with DBManager().get_connection(host_or_path= host, database= db_name, username= username, password= password, dialect= 'mysql') as conn:
        conn.create_tables(...)
        records = conn.execute(stmt, fetch = 'all')
    """

    def __init__(self, log: Optional[logging.Logger] = None) -> None:
        self._log = log or logging.getLogger("qrank.db")
        self._lock = Lock()
        # Nested dict: host_or_path → {URL → Engine}
        self._engines: Dict[str, Dict[URL, Engine]] = defaultdict(dict)
        self._last_used: Dict[URL, float] = defaultdict(float)
        self._initialzed_dbs: set = set()
        atexit.register(self._shutdown)

    def _ensure_database(
        self, host_or_path: str, database: str, port, username, password, dialect: str
    ) -> None:
        if dialect == "sqlite":
            db_path = os.path.join(host_or_path, database)
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            if not os.path.exists(db_path):
                open(db_path, "a").close()
            return

        base_url = _CONNECTION_STR_MAPPING[dialect](
            host_or_path, port=port, username=username, password=password, database=None
        )

        engine = create_engine(base_url)
        try:
            with engine.begin() as conn:
                if dialect == "mysql":
                    conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{database}`"))
                elif dialect == "postgres":
                    result = conn.execute(
                        text("SELECT 1 FROM pg_database WHERE datname = :name"),
                        {"name": database},
                    )
                    if not result.fetchone():
                        conn.execute(text(f'CREATE DATABASE "{database}"'))
        finally:
            engine.dispose()

    @contextmanager
    def get_connection(
        self,
        host_or_path: str,
        database: str,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        dialect: Literal["sqlite", "mysql", "postgres"] = "sqlite",
        # Engine / pool tuning
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout: int = 15,
        pool_recycle: int = 60,
        connect_timeout: int = 25,
        create_if_missing: bool = True,
    ) -> Generator[Connect, None, None]:
        """
        Yield a :class:`Connect` instance bound to the requested database.

        All keyword arguments beyond *dialect* are forwarded to SQLAlchemy's
        ``create_engine`` and the connection pool.
        """
        if create_if_missing:
            db_key = (dialect, host_or_path, port, username, database)
            with self._lock:
                if db_key not in self._initialzed_dbs:
                    self._ensure_database(
                        host_or_path, database, port, username, password, dialect
                    )
                    self._initialzed_dbs.add(db_key)

        conn_url = self._build_url(
            dialect,
            host_or_path,
            port=port,
            username=username,
            password=password,
            database=database,
        )
        engine = self._assert_engine(
            conn_url,
            dialect=dialect,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            connect_timeout=connect_timeout,
        )
        conn = Connect(engine=engine, log=self._log)
        try:
            yield conn
        finally:
            self._clean_stale_pools()

    @staticmethod
    def _build_url(
        dialect: str,
        host_or_path: str,
        *,
        port: Optional[int],
        username: Optional[str],
        password: Optional[str],
        database: str,
    ) -> URL:
        builder = _CONNECTION_STR_MAPPING.get(dialect)
        if builder is None:
            raise ValueError(
                f"Unsupported dialect '{dialect}'. "
                f"Supported: {list(_CONNECTION_STR_MAPPING)}"
            )
        return builder(
            host_or_path,
            port=port,
            username=username,
            password=password,
            database=database,
        )

    def _assert_engine(
        self,
        conn_url: URL,
        dialect: str = "sqlite",
        pool_size: int = 10,
        max_overflow: int = 20,
        pool_timeout: int = 15,
        pool_recycle: int = 60,
        connect_timeout: int = 5,
    ) -> Engine:
        host_key = conn_url.host or conn_url.database or str(conn_url)

        with self._lock:
            if conn_url not in self._engines[host_key]:
                engine = self._create_engine(
                    conn_url,
                    dialect=dialect,
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                    pool_timeout=pool_timeout,
                    pool_recycle=pool_recycle,
                    connect_timeout=connect_timeout,
                )
                self._engines[host_key][conn_url] = engine
                self._log.debug(
                    "Created new engine for %s",
                    conn_url.render_as_string(hide_password=True),
                )

        self._last_used[conn_url] = time.monotonic()
        return self._engines[host_key][conn_url]

    @staticmethod
    def _create_engine(
        conn_url: URL,
        dialect: str,
        pool_size: int,
        max_overflow: int,
        pool_timeout: int,
        pool_recycle: int,
        connect_timeout: int,
    ) -> Engine:
        if dialect == "sqlite":
            is_memory = conn_url.database in (None, ":memory:")
            # SQLite does not support connection pooling arguments in the
            # same way; use StaticPool for in-memory or NullPool for files.
            connect_args: Dict[str, Any] = {
                "check_same_thread": False,
                "timeout": connect_timeout,  # busy/lock-wait timeout
            }
            if is_memory:
                return create_engine(
                    conn_url,
                    poolclass=StaticPool,
                    connect_args=connect_args,
                )
            else:
                return create_engine(
                    conn_url,
                    poolclass=NullPool,  # no pool = no pool-level blocking
                    connect_args=connect_args,
                )
        else:
            connect_args = {"connect_timeout": connect_timeout}
            return create_engine(
                conn_url,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=pool_timeout,
                pool_recycle=pool_recycle,
                pool_pre_ping=True,
                connect_args=connect_args,
            )

    def _clean_stale_pools(self, idle_seconds: float = 60.0) -> None:
        """Shut down thread pools that have been idle longer than *idle_seconds*."""
        now = time.monotonic()
        evicted = 0

        with self._lock:
            for host_key in list(self._engines.keys()):
                for conn_url in list(self._engines[host_key]):
                    last_used = self._last_used.get(conn_url, 0)
                    if now - last_used > idle_seconds:
                        engine = self._engines[host_key].pop(conn_url)
                        engine.dispose()
                        self._last_used.pop(conn_url, None)
                        evicted += 1
                if not self._engines[host_key]:
                    del self._engines[host_key]
        if evicted:
            self._log.debug("Evicted %d stale thread pool(s).", evicted)

    def _shutdown(self) -> None:
        """Called at process exit — drain all pools immediately."""
        self._clean_stale_pools(idle_seconds=-1)
