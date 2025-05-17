# from __future__ import annotations
# from sqlalchemy import create_engine, text, Engine, URL, Connection, MetaData
# from sqlalchemy.schema import CreateTable
# from threading import Lock

# from func_timeout import func_timeout, FunctionTimedOut
# from typing import List, Tuple, Any, Dict, Union
# from pathlib import Path
# from collections import defaultdict
# from src.decorators import *
# import random
# logger = logging.getLogger('app')

# class Connect:
#     def __init__(self, connection: Connection):
#         self.conn: Connection = connection
    
#     def __enter__(self):
#         return self
    
#     def __exit__(self, exc_type, exc_value, exc_tb):
#         self.close()

#     def close(self):
#         self.conn.close()

#     def execute(self, stmt, fetch: Union[str, int] = 'all'):
#         r = self.conn.execute(text(stmt))
#         if fetch == 'all':
#             return r.fetchall()
#         elif fetch == 'one' or str(fetch) == '1':
#             return r.fetchone()
#         elif fetch == 'random':
#             samples = r.fetchmany(10)
#             self.result = random.choice(samples) if samples else []
#         elif isinstance(fetch, int):
#             return r.fetchmany(fetch)
        
#     def create_tables(self, *ddls):
#         for ddl in ddls:
#             self.execute(ddl, fetch= None)

#     def drop_table(self, table_name):
#         self.execute(f"DROP TABLE IF EXISTS {table_name}", fetch= None)


# class DBManager(metaclass = singletonMeta):
#     '''
#         Maintain a connection pool to connect to various databases. Use as 
#         with DBManager().get_connection(host_or_path= host, database= db_name, username= username, password= password, dialect= 'mysql') as conn:
#             conn.create_tables(...)
#             records = conn.execute(stmt, fetch = 'all')
#     '''
#     def __init__(self, **kwargs):
#         self.engines:Dict[str, Dict[str, Engine]] = defaultdict(dict)
#         self.lock = Lock()
#         self.set_options('MAX_CHECKOUTS', kwargs.get('max_checkouts', 100))

#     def set_options(self, key, value):
#         setattr(self, key, value)

#     def _ensure_connection_string(self, host_or_path, database, port = None, username = None, password = None, dialect = 'sqlite') -> URL:
#         import os
#         mapping = {
#             'sqlite' : lambda : URL.create("sqlite", database= os.path.join(host_or_path, database)),
#             'mysql':  lambda : URL.create("mysql+mysqldb", username= username, password = password, host = host_or_path, port = port, database= database)   
#         }
#         return mapping[dialect]()

#     def _assert_engine(self, conn_str: URL, pool_size=20, max_overflow=10, pool_timeout=15, pool_recycle=60, **kwargs) -> Engine:
#         host_or_path = conn_str.host or conn_str.database
#         with self.lock:
#             if conn_str in self.engines[host_or_path]:
#                 return self.engines[host_or_path][conn_str]
            
#             if self._get_checkouts(host_or_path) > self.MAX_CHECKOUTS:
#                 self._clean_unused_engine(host_or_path)
            
#             if conn_str not in self.engines[host_or_path]:
#                 engine = create_engine(
#                     # conn_str.render_as_string(hide_password= False),
#                     # str(conn_str),
#                     conn_str,
#                     # conn_str.render_as_string(hide_password= False),
#                     pool_size=pool_size,
#                     max_overflow=max_overflow,
#                     pool_timeout=pool_timeout,
#                     pool_recycle=pool_recycle
#                 )
#                 self.engines[host_or_path][conn_str] = engine
#                 logger.info(f'create new connection for {conn_str}')
#             return self.engines[host_or_path][conn_str]
        

#     def get_connection(self, host_or_path, database, port = None, username = None, password = None, dialect = 'sqlite',
#                        pool_size=20, max_overflow=10, pool_timeout=15, pool_recycle=60, **kwargs) -> Connect:
#         conn_str = self._ensure_connection_string(host_or_path, database= database, port= port, username= username, password= password, dialect= dialect)        
#         engine = self._assert_engine(conn_str, pool_size, max_overflow, pool_timeout, pool_recycle, **kwargs)
#         return Connect(connection= engine.connect())

#     def get_schema(self, host_or_path, database, port = None, username = None, password = None, dialect = 'sqlite') -> List[str]:
#         conn_str = self._ensure_connection_string(host_or_path, database= database, port= port, username= username, password= password, dialect= dialect)
#         engine = self._assert_engine(conn_str)
#         metadata = MetaData()
#         metadata.reflect(bind = engine)

#         schema = []
#         for table_name, table in metadata.tables.items():
#             ddl = str(CreateTable(table).compile(engine))
#             schema.append(ddl)
#         return '\n'.join(schema)
        
#     def _get_checkouts(self, host_or_path) -> int:
#         '''
#             Return the count of connections in use
#         '''
#         availables = self.engines[host_or_path]
#         checkouts = 0
#         for _, engine in availables.items():
#             checkouts = checkouts + engine.pool.size() + engine.pool.overflow()
#         return checkouts

#     def _clean_unused_engine(self, host_or_path):
#         '''
#             ensure thread safe to clears engiens
#         '''
#         unused = 0
#         for conn_str in list(self.engines[host_or_path].keys()):
#             engine = self.engines[host_or_path][conn_str]
#             if engine.pool.checkedout() < 1:
#                 unused += 1
#                 engine.dispose()
#                 del self.engines[host_or_path][conn_str]
#         logger.info(f'Cleaned {unused} unused connections in the connection pool')

#     def export_database(self, host_or_path, database, port = None, username = None, password = None, dialect = 'sqlite') -> List[str]:
#         database_dump = []

#         conn_str = self._ensure_connection_string(host_or_path, database= database, port= port, username= username, password= password, dialect= dialect)
#         engine = self._assert_engine(conn_str)
#         metadata = MetaData()
#         metadata.reflect(bind = engine)

#         with engine.connect() as conn:
#             for table_name, table in metadata.tables.items():
#                 database_dump.append(str(CreateTable(table).compile(engine)))
#                 result = conn.execute(table.select())
#                 for row in result:
#                     row = row._asdict()
#                     columns = ", ".join(row.keys())
#                     values = ", ".join(
#                         f"'{value}'" if value is not None else "NULL" for value in row.values()
#                     )
#                     insert_stmt = f"INSERT INTO {table_name} ({columns}) VALUES ({values});"
#                     database_dump.append(insert_stmt)
#         return database_dump