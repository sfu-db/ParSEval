"""Generate a database for a query and write it into a real backend."""

from __future__ import annotations

from parseval.catalog import Catalog
from parseval.db_manager import DBManager, Dialect
from parseval.generator import GenerationConfig, GenerationResult, generate


def instantiate_db(
    sql: str,
    schema: str,
    connection_string: str,
    dialect: Dialect,
    *,
    config: GenerationConfig | None = None,
    create_tables: bool = True,
) -> GenerationResult:
    """Generate data for ``sql`` over ``schema`` and load it into a database.

    ``schema`` is DDL in ``dialect``; ``connection_string`` is an SQLAlchemy
    URL whose database is created if missing. With ``create_tables`` the DDL
    runs there first; otherwise its tables must exist. Rows are loaded in one
    transaction; nothing is written when generation produced no database.
    """
    result = generate(sql, Catalog.from_ddl(schema, dialect=dialect), config=config)
    if result.instance is not None:
        with DBManager(connection_string, dialect).connect() as connection:
            if create_tables:
                connection.create_tables(schema)
            connection.load(result.instance)
    return result


__all__ = ["instantiate_db"]
