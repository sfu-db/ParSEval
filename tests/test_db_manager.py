"""Loading generated instances into database backends and querying them."""

import os
import sqlite3
from collections import Counter

import pytest

from parseval import Catalog, GenerationConfig, generate
from parseval.db_manager import DBManager
from parseval.instance import Machine, Valuation
from parseval.parser.query import lower_query
from parseval.uexpr.lowering import UExprCompiler

# The child table comes first, so loading must follow foreign keys.
DDL = (
    "CREATE TABLE s (id INT PRIMARY KEY, tid INT REFERENCES t(id), y INT, note VARCHAR(8));"
    "CREATE TABLE t (id INT PRIMARY KEY, g INT, x INT, born DATE)"
)

QUERIES = [
    "SELECT t.x, s.y FROM t JOIN s ON s.tid = t.id WHERE t.x > 3",
    "SELECT g, COUNT(*) FROM t GROUP BY g HAVING COUNT(*) > 1",
    "SELECT x FROM t WHERE NOT EXISTS (SELECT 1 FROM s WHERE s.tid = t.id)",
    "SELECT note FROM s WHERE note LIKE 'ab%' AND y IS NULL",
    "SELECT x FROM t WHERE born > '2001-01-01'",
]

BACKENDS = [("sqlite:///:memory:", "sqlite")]
if os.environ.get("PARSEVAL_POSTGRES_DSN"):
    BACKENDS.append((os.environ["PARSEVAL_POSTGRES_DSN"], "postgres"))


def returned(connection, sql):
    """Rows as the backend returns them, CHAR(n) padding removed (PostgreSQL bpchar)."""
    types, *rows = connection.execute(sql, with_column_types=True)
    return Counter(
        tuple(value.rstrip(" ") if oid == 1042 and isinstance(value, str) else value for value, oid in zip(row, types))
        for row in rows
    )


def expected(sql, catalog, instance):
    query = lower_query(sql, catalog)
    root = UExprCompiler(query.arena, instance.arena).compile(query.root).simplified_root
    valuation = Valuation(instance)
    machine = Machine(valuation)
    rows = machine.occurrences(machine.run(root).relation)
    return Counter(tuple(valuation.concrete(cell) for cell in row.cells) for row in rows)


@pytest.mark.parametrize(("url", "dialect"), BACKENDS)
@pytest.mark.parametrize("sql", QUERIES)
def test_backend_returns_what_the_instance_produces(url, dialect, sql):
    catalog = Catalog.from_ddl(DDL, dialect=dialect)
    result = generate(sql, catalog, config=GenerationConfig(timeout_ms=3000))
    assert result.nonempty
    with DBManager(url, dialect).scratch() as connection:
        connection.create_tables(DDL)
        connection.load(result.instance)
        rows = connection.execute(sql)
    assert rows
    assert Counter(rows) == expected(sql, catalog, result.instance)


def test_tables_and_rows_can_be_read_back():
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    result = generate(QUERIES[0], catalog)
    with DBManager("sqlite:///:memory:", "sqlite").connect() as connection:
        connection.create_tables(DDL)
        connection.load(result.instance)
        tables = connection.get_all_table_rows()
        assert set(tables) == {"s", "t"}
        assert len(tables["t"]) - 1 == len(result.instance.rows(catalog.tables()[1].relation))
        assert "CREATE TABLE" in connection.get_schema()


def test_loading_keeps_foreign_keys_enforced():
    catalog = Catalog.from_ddl(DDL, dialect="sqlite")
    instance = generate(QUERIES[0], catalog).instance
    with DBManager("sqlite:///:memory:", "sqlite").connect() as connection:
        connection.create_tables(DDL)
        connection.load(instance)
        with pytest.raises(Exception, match="FOREIGN KEY"):
            connection.execute("INSERT INTO s VALUES (999, 12345, 1, 'x')", fetch=None)


def test_statements_stop_at_their_timeout():
    endless = "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n) SELECT COUNT(*) FROM n"
    with DBManager("sqlite:///:memory:", "sqlite").connect() as connection:
        with pytest.raises(Exception, match="interrupt"):
            connection.execute(endless, timeout=0.5)


def test_scratch_sqlite_database_is_isolated():
    with DBManager("sqlite:///:memory:", "sqlite").scratch() as connection:
        connection.create_tables(DDL)
    with DBManager("sqlite:///:memory:", "sqlite").scratch() as connection:
        assert connection.execute("SELECT name FROM sqlite_master") == []
    assert sqlite3.sqlite_version


CONSTRAINED = (
    "CREATE TABLE p (id INT PRIMARY KEY, flag CHAR(1) NOT NULL CHECK (flag IN ('Y', 'N')),"
    " code VARCHAR(3), score INT CHECK (score BETWEEN 1 AND 5), rate DECIMAL(3, 2));"
    "CREATE TABLE q (id INT PRIMARY KEY, pid INT REFERENCES p(id), tag CHAR(2) NOT NULL)"
)


@pytest.mark.parametrize(("url", "dialect"), BACKENDS)
@pytest.mark.parametrize("sql", [
    "SELECT q.tag FROM q JOIN p ON p.id = q.pid WHERE p.score > 3",
    "SELECT id FROM p WHERE flag = 'N' OR code LIKE 'a%'",
    "SELECT COUNT(*) FROM q GROUP BY tag HAVING COUNT(*) > 2",
])
@pytest.mark.parametrize("speculate", [True, False])
def test_generated_rows_satisfy_checks_and_storage_limits(url, dialect, sql, speculate):
    catalog = Catalog.from_ddl(CONSTRAINED, dialect=dialect)
    result = generate(sql, catalog, config=GenerationConfig(timeout_ms=3000, speculate=speculate))
    assert result.nonempty
    with DBManager(url, dialect).scratch() as connection:
        connection.create_tables(CONSTRAINED)
        connection.load(result.instance)
        assert returned(connection, sql) == expected(sql, catalog, result.instance)


def test_enum_columns_take_listed_values():
    catalog = Catalog.from_ddl(
        "CREATE TABLE e (id INT PRIMARY KEY, status ENUM('open', 'shut') NOT NULL, x INT)", dialect="mysql"
    )
    for speculate in (True, False):
        result = generate("SELECT x FROM e WHERE x > 2", catalog, config=GenerationConfig(speculate=speculate))
        assert result.nonempty
        assert {row[1] for row in result.instance.rows(catalog.tables()[0].relation)} <= {"open", "shut"}


@pytest.mark.parametrize(("url", "dialect"), BACKENDS)
def test_instantiate_db_writes_a_productive_database(tmp_path, url, dialect):
    from uuid import uuid4

    from sqlalchemy.engine import make_url

    from parseval import instantiate_db

    sql = "SELECT q.tag FROM q JOIN p ON p.id = q.pid WHERE p.score > 3"
    database = str(tmp_path / "out.sqlite") if dialect == "sqlite" else "parseval_test_" + uuid4().hex
    target = make_url(url).set(database=database).render_as_string(hide_password=False)
    try:
        result = instantiate_db(sql, CONSTRAINED, target, dialect)
        assert result.nonempty
        with DBManager(target, dialect).connect(create_if_missing=False) as connection:
            rows = returned(connection, sql)
        assert rows and rows == expected(sql, result.instance.catalog, result.instance)
    finally:
        if dialect != "sqlite":
            from parseval.db_manager import _drop
            _drop(make_url(target), dialect)
