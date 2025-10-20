from __future__ import annotations
from sqlalchemy import __version__ as SQLALCHEMY_VERSION
from sqlalchemy.schema import CreateTable
from packaging.version import Version

from sqlalchemy import create_engine, text, Connection, MetaData, Engine, URL

from threading import Lock
from typing import List, Tuple, Any, Dict, Union, Literal, overload, Optional, NewType
from collections import defaultdict
import random, logging, os
from sqlglot import parse_one, exp, parse
from sqlglot.schema import MappingSchema

logger = logging.getLogger(f"src.db_manager")

from threading import Lock


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
    def __init__(self, connection: Connection):
        self.conn: Connection = connection
        self._metadata = None

    @property
    def metadata(self):
        if self._metadata is None:
            engine = self.conn.engine
            self._metadata = MetaData()
            self.metadata.reflect(bind=engine)
        return self._metadata

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, exc_tb):
        self.close()

    def close(self):
        self.conn.close()

    @overload
    def execute(
        self,
        stmt: str,
        parameters: Optional[Any] = ...,
        fetch: None = ...,
        commit: bool = ...,
        with_column_name: bool = ...,
    ) -> None: ...

    @overload
    def execute(
        self,
        stmt: str,
        parameters: Optional[Any] = ...,
        fetch: Union[Literal["all", "one", "1", "random"], int] = ...,
        commit: bool = ...,
        with_column_name: bool = ...,
    ) -> List[Tuple[Any]]: ...

    def execute(
        self,
        stmt,
        parameters: Optional[Any] = None,
        fetch: Optional[Union[Literal["all", "one", "1", "random"], int]] = "all",
        commit: bool = False,
        with_column_name: bool = False,
    ):
        r = self.conn.execute(text(stmt), parameters=parameters)
        results = None
        if fetch:
            results = self._fetch_query_results(
                r, fetch=fetch, with_column_name=with_column_name
            )
        if commit:
            self.conn.commit()
        return results

    def _fetch_query_results(
        self,
        cursor_result,
        fetch: Optional[Union[Literal["all", "one", "1", "random"], int]],
        with_column_name,
    ):
        results_sizes = {
            "all": lambda cur: cur.fetchall(),
            "one": lambda cur: cur.fetchone(),
            "1": lambda cur: cur.fetchone(),
            "random": lambda cur: random.choice([] + cur.fetchmany(20)),
            None: lambda cur: None,
        }
        results = None
        if fetch in results_sizes:
            results = results_sizes.get(fetch)(cursor_result)
        elif isinstance(fetch, int):
            results = cursor_result.fetchmany(fetch)
        column_names = []
        if results:
            records = []
            column_names = tuple(cursor_result.keys())
            for row in results:
                records.append(tuple(row))
            results = records
        if with_column_name and column_names:
            results.insert(0, tuple(column_names))
        return results

    def create_tables(self, *ddls):
        for ddl in ddls:
            self.execute(ddl, fetch=None)

    def drop_table(self, table_name):
        self.execute(f"DROP TABLE IF EXISTS {table_name}", fetch=None)

    def insert(self, stmt: str, data: List[Dict[str, Any]]):
        """
        INSERT data into tables accordingly.
        """
        self.execute(stmt, parameters=data, commit=True, fetch=None)

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
            rows = self.conn.execute(table.select())
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

    CONNECTION_STR_MAPPING: Dict[str, URL] = {
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

    def __init__(self, **kwargs):
        self.engines: Dict[str, Dict[str, Engine]] = defaultdict(dict)
        self.lock = Lock()
        self.set_options("MAX_CHECKOUTS", kwargs.get("max_checkouts", 100))

    def set_options(self, key, value):
        setattr(self, key, value)

    def _assert_engine(
        self,
        conn_str: URL,
        pool_size=20,
        max_overflow=10,
        pool_timeout=15,
        pool_recycle=60,
        **kwargs,
    ) -> Engine:
        host_or_path = conn_str.host or conn_str.database
        with self.lock:
            if conn_str in self.engines[host_or_path]:
                return self.engines[host_or_path][conn_str]

            if self._get_checkouts(host_or_path) > self.MAX_CHECKOUTS:
                self._clean_unused_engine(host_or_path)

            if conn_str not in self.engines[host_or_path]:
                engine = create_engine(
                    conn_str,
                    pool_size=pool_size,
                    max_overflow=max_overflow,
                    pool_timeout=pool_timeout,
                    pool_recycle=pool_recycle,
                )
                self.engines[host_or_path][conn_str] = engine
                logger.debug(f"create new connection for {conn_str}")
            return self.engines[host_or_path][conn_str]

    def get_connection(
        self,
        host_or_path,
        database,
        port=None,
        username=None,
        password=None,
        dialect="sqlite",
        pool_size=20,
        max_overflow=10,
        pool_timeout=15,
        pool_recycle=60,
        **kwargs,
    ) -> Connect:
        conn_str = self.CONNECTION_STR_MAPPING[dialect](
            host_or_path,
            database=database,
            port=port,
            username=username,
            password=password,
        )
        engine = self._assert_engine(
            conn_str, pool_size, max_overflow, pool_timeout, pool_recycle, **kwargs
        )
        return Connect(connection=engine.connect())

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

    def _get_checkouts(self, host_or_path) -> int:
        """
        Return the count of connections in use
        """
        availables = self.engines[host_or_path]
        checkouts = 0
        for _, engine in availables.items():
            checkouts = checkouts + engine.pool.size() + engine.pool.overflow()
        return checkouts

    def _clean_unused_engine(self, host_or_path):
        """
        ensure thread safe to clears engiens
        """
        unused = 0
        for conn_str in list(self.engines[host_or_path].keys()):
            engine = self.engines[host_or_path][conn_str]
            if engine.pool.checkedout() < 1:
                unused += 1
                engine.dispose()
                del self.engines[host_or_path][conn_str]
        logger.debug(f"Cleaned {unused} unused connections in the connection pool")

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
                raise ValueError(f"cannot parse schema {schemas}. {e}")

        with self.get_connection(
            host_or_path, database, port, username, password, dialect
        ) as conn:
            conn.create_tables(*ddls)
