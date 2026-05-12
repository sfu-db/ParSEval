"""ParSEval main entry point — public API for test database generation.

Usage::

    from parseval.main import instantiate_db, disprove

    # Generate a test database for a query
    instantiate_db(
        sql="SELECT * FROM users WHERE age > 25",
        schema="CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT)",
        connection_string="sqlite:////tmp/test.db",
        dialect="sqlite",
    )

    # Test equivalence of two queries
    result = disprove(
        sql1="SELECT name FROM users WHERE age > 25",
        sql2="SELECT name FROM users WHERE age >= 26",
        schema="CREATE TABLE users (id INT PRIMARY KEY, name TEXT, age INT)",
        connection_string="sqlite:////tmp/test.db",
        dialect="sqlite",
    )
"""

from __future__ import annotations

from typing import Any, Optional

from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.symbolic import CoverageThresholds, SymbolicEngine


def instantiate_db(
    sql: str,
    schema: str,
    connection_string: str,
    dialect: str = "sqlite",
    *,
    db_id: str = "parseval",
    max_iterations: int = 10,
    atom_null: int = 0,
    return_instance: bool = False,
    **kwargs: Any,
) -> Optional[Instance]:
    """Generate a test database instance for a SQL query and persist it.

    Parameters
    ----------
    sql : str
        The SQL query to generate test data for.
    schema : str
        DDL statements (semicolon-separated) defining the database schema.
    connection_string : str
        SQLAlchemy connection string (e.g. "sqlite:////tmp/test.db",
        "mysql+pymysql://user:pass@host/db", "postgresql://user@host/db").
    dialect : str
        SQL dialect: "sqlite", "mysql", or "postgres".
    db_id : str
        Identifier for the generated instance (used internally).
    max_iterations : int
        Maximum coverage-loop iterations for the symbolic engine.
    atom_null : int
        Coverage threshold for NULL branches (0 to skip NULL coverage).
    return_instance : bool
        If True, return the Instance object without writing to DB.
    **kwargs
        Additional keyword arguments passed to SymbolicEngine.

    Returns
    -------
    Instance or None
        The generated Instance if return_instance=True, else None.
    """
    instance = Instance(ddls=schema, name=db_id, dialect=dialect)

    thresholds = CoverageThresholds(atom_null=atom_null)
    engine = SymbolicEngine(
        instance, sql, dialect=dialect, max_iterations=max_iterations, **kwargs
    )
    engine.generate(thresholds=thresholds)

    if return_instance:
        return instance

    to_db(instance, connection_string, dialect=dialect)
    return None


def disprove(
    sql1: str,
    sql2: str,
    schema: str,
    connection_string: str,
    dialect: str,
    *,
    db_id: str = "parseval_disprove",
    max_iterations: int = 10,
    **kwargs: Any,
) -> dict:
    """Attempt to disprove equivalence of two SQL queries.

    Generates test data targeting both queries and checks if they produce
    different results on the same database instance.

    Parameters
    ----------
    sql1 : str
        First SQL query.
    sql2 : str
        Second SQL query.
    schema : str
        DDL statements defining the database schema.
    connection_string : str
        SQLAlchemy connection string for query execution.
    dialect : str
        SQL dialect: "sqlite", "mysql", or "postgres".
    db_id : str
        Identifier for the generated instance.
    max_iterations : int
        Maximum iterations for the symbolic engine.

    Returns
    -------
    dict
        {"equivalent": bool, "instance": Instance, "result1": list, "result2": list}
    """
    instance = Instance(ddls=schema, name=db_id, dialect=dialect)

    # Generate for sql1
    engine1 = SymbolicEngine(
        instance, sql1, dialect=dialect, max_iterations=max_iterations, **kwargs
    )
    engine1.generate(thresholds=CoverageThresholds(atom_null=0))

    # Generate for sql2 on the same instance
    engine2 = SymbolicEngine(
        instance, sql2, dialect=dialect, max_iterations=max_iterations, **kwargs
    )
    engine2.generate(thresholds=CoverageThresholds(atom_null=0))

    # Execute both queries
    result1, result2 = _execute_both(instance, sql1, sql2)

    return {
        "equivalent": result1 == result2,
        "instance": instance,
        "result1": result1,
        "result2": result2,
    }


def _execute_both(instance: Instance, sql1: str, sql2: str):
    """Execute both queries against the instance using in-memory SQLite."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    try:
        for ddl in instance.ddls.split(";"):
            ddl = ddl.strip()
            if ddl:
                try:
                    conn.execute(ddl)
                except Exception:
                    pass

        for table_name in instance.tables:
            rows = instance.get_rows(table_name)
            if not rows:
                continue
            cols = list(instance.tables[table_name].keys())
            placeholders = ",".join(["?"] * len(cols))
            col_names = ",".join(f'"{c}"' for c in cols)
            stmt = f'INSERT INTO "{table_name}" ({col_names}) VALUES ({placeholders})'
            for row in rows:
                values = []
                for c in cols:
                    v = row[c].concrete if c in row.columns else None
                    if v is not None and not isinstance(v, (int, float, str, bytes)):
                        v = str(v)
                    values.append(v)
                try:
                    conn.execute(stmt, values)
                except Exception:
                    pass
        conn.commit()

        try:
            result1 = sorted(conn.execute(sql1).fetchall())
        except Exception:
            result1 = []
        try:
            result2 = sorted(conn.execute(sql2).fetchall())
        except Exception:
            result2 = []

        return result1, result2
    finally:
        conn.close()
