from __future__ import annotations

from sqlalchemy.schema import CreateTable
from sqlalchemy import create_engine, text, MetaData, Engine, URL
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
    Callable,
)
from collections import defaultdict
import random, logging, os
from sqlglot import parse_one, exp, parse
from sqlglot.schema import MappingSchema
from contextlib import contextmanager
import time
import atexit
from concurrent.futures import ThreadPoolExecutor
from .singleton import singletonMeta
from .states import SchemaException

class Connect:
    def __init__(self, engine: Engine, executor, dialect: str, logger=None):
        self.engine = engine
        self.executor = executor
        self.logger = logger or logging.getLogger(f"parseval.db_manager.connect")
        self.dialect = dialect
        self._metadata = None

    @property
    def metadata(self):
        if self._metadata is None:
            self._metadata = MetaData()
            self.metadata.reflect(bind=self.engine)
        return self._metadata

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.close()

    def close(self):
        self.logger.debug(
            f"closed connection {self.engine.url.render_as_string(hide_password=True)}"
        )

    @overload
    def execute(
        self,
        stmt: str,
        parameters: Optional[Any] = ...,
        fetch: None = ...,
        with_column_name: bool = ...,
        timeout: int = ...,
    ) -> None: ...

    @overload
    def execute(
        self,
        stmt: str,
        parameters: Optional[Any] = ...,
        fetch: Union[Literal["all", "one", "1", "random"], int] = ...,
        with_column_name: bool = ...,
        timeout: int = ...,
    ) -> List[Tuple[Any]]: ...

    def execute(
        self,
        stmt,
        parameters: Optional[Any] = None,
        fetch: Optional[Union[Literal["all", "one", "1", "random"], int]] = "all",
        with_column_name: bool = False,
        timeout: int = 20,
    ):
        """
        Execute a SQL statement with optional fetch and commit.

        Args:
            stmt: SQL query string.
            parameters: Optional query parameters.
            fetch: 'all', 'one', integer, or 'random' (default: 'all').
            commit: Whether to commit the transaction (for DML queries).
            with_column_name: Whether to include column names in the result.
        """
        results = None
        try:
            raw_conn = None
            conn_result = None
            with self.engine.begin() as conn:
                statement = text(stmt)
                if "sqlite" in str(self.engine.url):
                    start_time = time.time()
                    raw_conn = conn.connection.dbapi_connection

                    def progress_handler():
                        if time.time() - start_time > timeout:
                            return 1
                        return 0

                    raw_conn.set_progress_handler(progress_handler, 1000)
                self.logger.debug(f"start to execute statement: {stmt[:100]}")
                conn_result = conn.execute(statement, parameters=parameters)
                if fetch and conn_result is not None:
                    self.logger.debug(f"fetching results for statement: {stmt[:100]}")
                    results = self._fetch_query_results(
                        conn_result, fetch=fetch, with_column_name=with_column_name
                    )
        finally:
            if raw_conn is not None:
                try:
                    raw_conn.set_progress_handler(None, 0)
                except:
                    ...
            if conn_result is not None:
                try:
                    conn_result.close()
                except Exception as e:
                    ...
        return results

    def _fetch_query_results(
        self,
        cursor_result,
        fetch: Optional[Union[Literal["all", "one", "1", "random"], int]],
        with_column_name,
    ):
        if cursor_result is None:
            return None

        if fetch == "random":
            rows = cursor_result.fetchmany(20)
            results = [random.choice(rows)] if rows else []
        elif fetch == "all":
            results = cursor_result.fetchall()
        elif fetch in {"one", 1, "1"}:
            results = cursor_result.fetchone()
        elif isinstance(fetch, int):
            results = cursor_result.fetchmany(fetch)
        else:
            results = []

        if not results:
            return []
        # Convert to tuples
        records = [tuple(row) for row in results]
        # Include column names if requested
        if with_column_name and hasattr(cursor_result, "keys"):
            records.insert(0, tuple(cursor_result.keys()))
        return records

    def create_tables(self, *ddls):
        for ddl in ddls:
            self.execute(ddl, fetch=None)

    def create_schema(self, schema: MappingSchema | List[str] | Dict[str, Dict[str, str]] | str, dialect: Optional[str] = None):
        if isinstance(schema, MappingSchema):
            schema = schema.mapping
        ddls = []
        if isinstance(schema, list):
            ddls.extend(schema)
        elif isinstance(schema, dict):
            """
            we should convert Dict schema to ddl first
            """
            for table_name, column_defs in schema.items():
                columns = [
                    exp.ColumnDef(
                        this=exp.to_identifier(column_name, quoted=True),
                        kind=exp.DataType.build(column_typ),
                    )
                    for column_name, column_typ in column_defs.items()
                ]
                ddl = exp.Create(
                    this=exp.Schema(
                        this=exp.to_identifier(table_name, quoted=True),
                        expressions=columns,
                    ),
                    exists=True,
                    kind="TABLE",
                )
                ddls.append(ddl.sql(dialect=dialect))
        else:
            try:
                for ddl in parse(schema, read=dialect):
                    ddl.set('exists', True)
                    ddls.append(ddl.sql(dialect=dialect))
            except Exception as e:
                raise SchemaException(f"cannot parse schema {schema}. {e}")
        
        self.create_tables(*ddls)

    def drop_table(self, table_name):
        self.execute(f"DROP TABLE IF EXISTS {table_name}", fetch=None)

    def insert(self, stmt: str, data: List[Dict[str, Any]]):
        """
        INSERT data into tables accordingly.
        """
        self.execute(stmt, parameters=data, fetch=None)

    def get_schema(self) -> str:
        """
        Return all table names within database
        """
        schema = []
        for _, table in self.metadata.tables.items():
            ddl = str(
                CreateTable(table).compile(compile_kwargs={"literal_binds": True})
            )
            ddl = ddl.replace("watermark", '"watermark"')
            schema.append(ddl)
        return ";\n".join(schema)

    def get_table_rows(self, table_name: str):
        """
        Return all rows in table named `table_name`
        """
        table = self.metadata.tables[table_name]
        stmt = str(table.select().compile(compile_kwargs={"literal_binds": True}))
        return self.execute(stmt=stmt, fetch="all", with_column_name=True)

    def get_all_table_rows(self) -> Dict[str, List[Tuple[Any]]]:
        """
        Return all contents of target database.
        Return:
            {tbl : [(table columns), (rows)]}
        """
        content = {}
        for table_name, table in self.metadata.tables.items():
            stmt = str(table.select().compile(compile_kwargs={"literal_binds": True}))
            content[table_name] = self.execute(
                stmt=stmt, fetch="all", with_column_name=True
            )
        return content

    def export_database(self) -> List[str]:
        """
        Export entire database. Return a list of DDL and INSERT Statements
        """
        schema = self.get_schema()
        inserts = []
        for table_name, table in self.metadata.tables.items():
            rows = self._conn.execute(table.select())
            values = []
            for row in rows.fetchall():
                values.append(row._asdict())
            insert = (
                table.insert()
                .values(values)
                .compile(compile_kwargs={"literal_binds": True})
            )
            inserts.append(str(insert))
        return [schema, *inserts]


class DBManager(metaclass=singletonMeta):
    """
    Maintain a connection pool to connect to various databases. Use as
    with DBManager().get_connection(host_or_path= host, database= db_name, username= username, password= password, dialect= 'mysql') as conn:
        conn.create_tables(...)
        records = conn.execute(stmt, fetch = 'all')
    """

    CONNECTION_STR_MAPPING: Dict[str, Callable] = {
        "sqlite": lambda host_or_path, port, username, password, database: URL.create(
            "sqlite", database=os.path.join(host_or_path, database)
        ),
        "mysql": lambda host_or_path, port, username, password, database: URL.create(
            "mysql+mysqldb",
            username=username,
            password=password,
            host=host_or_path,
            port=port,
            database=database,
        ),
    }

    def __init__(self, logger=None, base_pool_size=10, max_pool_size=30, **kwargs):
        self.logger = logger or logging.getLogger(f"parseval.db_manager")
        self.engines: Dict[str, Dict[URL, Engine]] = defaultdict(dict)
        self.lock = Lock()
        self.set_options("max_workers", kwargs.get("max_workers", 10))
        self.pools: dict[URL, ThreadPoolExecutor] = {}
        self.base_pool_size = base_pool_size
        self.max_pool_size = max_pool_size
        self.last_used = defaultdict(float)

        atexit.register(self._shutdown_dbmanager)

    def set_options(self, key, value):
        setattr(self, key, value)

    def get_pool(self, conn_str: URL) -> ThreadPoolExecutor:
        """Get or create thread pool for this database URI."""
        with self.lock:
            if conn_str not in self.pools:
                pool = ThreadPoolExecutor(
                    max_workers=self.base_pool_size, thread_name_prefix="db_conn_"
                )
                self.pools[conn_str] = pool
            self.last_used[conn_str] = time.time()
            return self.pools[conn_str]

    def _assert_engine(
        self,
        conn_str: URL,
        pool_size=150,
        max_overflow=100,
        pool_timeout=15,
        pool_recycle=60,
        connect_timeout=5,
        **kwargs,
    ) -> Engine:
        host_or_path = conn_str.host or conn_str.database
        with self.lock:
            if conn_str not in self.engines[host_or_path]:
                params = dict(
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                    pool_timeout=pool_timeout,
                    pool_recycle=pool_recycle,
                    pool_pre_ping=True,
                    connect_args={
                        "timeout": connect_timeout,
                        "check_same_thread": False,
                    },
                )
                engine = create_engine(conn_str, **params)
                self.engines[host_or_path][conn_str] = engine

        self.last_used[conn_str] = time.time()
        return self.engines[host_or_path][conn_str]

    @contextmanager
    def get_connection(
        self,
        host_or_path,
        database,
        port=None,
        username=None,
        password=None,
        dialect="sqlite",
        pool_size=5,
        max_overflow=5,
        pool_timeout=15,
        pool_recycle=25,
        connect_timeout=5,
        **kwargs,
    ):
        conn_str = self.CONNECTION_STR_MAPPING[dialect](
            host_or_path,
            database=database,
            port=port,
            username=username,
            password=password,
        )

        engine = self._assert_engine(
            conn_str,
            pool_size,
            max_overflow,
            pool_timeout,
            pool_recycle,
            connect_timeout,
            **kwargs,
        )
        executor = self.get_pool(conn_str)
        conn = Connect(engine=engine, executor=executor, dialect= dialect, logger=self.logger)
        try:
            yield conn
        finally:
            self._clean_unused_engine()

    def drop_schema(
        self,
        host_or_path,
        database,
        port=None,
        username=None,
        password=None,
        dialect="sqlite",
    ) -> bool:
        """
        Drop all tables
        """
        conn_str = self.CONNECTION_STR_MAPPING[dialect](
            host_or_path,
            database=database,
            port=port,
            username=username,
            password=password,
        )
        engine = self._assert_engine(conn_str)
        metadata = MetaData()
        metadata.reflect(bind=engine)
        metadata.drop_all(bind=engine)

    def _clean_unused_engine(self, timeout=60):
        """
        Close unused pools/engines after 5 minutes of inactivity.
        """
        unused = 0
        now = time.time()
        with self.lock:
            for conn_str in list(self.pools.keys()):
                host_or_path = conn_str.host or conn_str.database
                last_used = self.last_used.get(conn_str, None)
                if last_used and now - last_used > timeout:
                    pool = self.pools.pop(conn_str, None)
                    # engine = self.engines[host_or_path].pop(conn_str, None)
                    # if engine and engine.pool.checkedout() == 0:
                    #     engine.dispose()
                    if pool:
                        pool.shutdown(wait=False, cancel_futures=True)
                    del self.last_used[conn_str]
                    unused += 1

        self.logger.debug(f"Cleaned {unused} unused connections in the connection pool")

    def create_schema(
        self,
        schemas: Union[List[str], Dict[str, Dict[str, str]], str],
        host_or_path,
        database,
        port=None,
        username=None,
        password=None,
        dialect="sqlite",
    ):
        if isinstance(schemas, MappingSchema):
            schemas = schemas.mapping
        ddls = []
        if isinstance(schemas, list):
            ddls.extend(schemas)
        elif isinstance(schemas, dict):
            """
            we should convert Dict schema to ddl firs
            """
            for table_name, column_defs in schemas.items():
                columns = [
                    exp.ColumnDef(
                        this=exp.to_identifier(column_name, quoted=True),
                        kind=exp.DataType.build(column_typ),
                    )
                    for column_name, column_typ in column_defs.items()
                ]
                ddl = exp.Create(
                    this=exp.Schema(
                        this=exp.to_identifier(table_name, quoted=True),
                        expressions=columns,
                    ),
                    exists=True,
                    kind="TABLE",
                )
                ddls.append(ddl.sql(dialect=dialect))
        else:
            try:
                for ddl in parse(schemas, read=dialect):
                    ddls.append(ddl.sql(dialect=dialect))
            except Exception as e:
                raise SchemaException(f"cannot parse schema {schemas}. {e}")

        with self.get_connection(
            host_or_path, database, port, username, password, dialect
        ) as conn:
            conn.create_tables(*ddls)

    def _shutdown_dbmanager(self):
        self._clean_unused_engine(timeout=-1)
