"""Generate databases for queries and use them on a real backend.

``instantiate_db`` writes the database generated for a query; ``disprove``
compares two queries on the databases generated for each of them.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, replace
from enum import Enum

import sqlglot
from sqlalchemy.exc import DBAPIError

from parseval.catalog import Catalog
from parseval.db_manager import Connect, DBManager, Dialect
from parseval.errors import SubEqError
from parseval.generator import GenerationConfig, GenerationResult, generate
from parseval.instance import ExecutionError, Instance


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


class Verdict(str, Enum):
    EQ = "EQ"
    NEQ = "NEQ"
    UNKNOWN = "UNKNOWN"
    SYNTAX_ERROR = "SYNTAX_ERROR"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True, slots=True)
class RunResult:
    """A verdict; for NEQ, the database telling the queries apart and the
    result of each query on it (rows, or the backend's error message)."""

    verdict: Verdict
    reason: str | None = None
    counterexample: Instance | None = None
    results: tuple[object, object] | None = None


def disprove(
    q1: str,
    q2: str,
    schema: str,
    connection_string: str,
    dialect: Dialect,
    *,
    config: GenerationConfig | None = None,
    timeout_s: float = 15,
    set_semantics: bool = False,
) -> RunResult:
    """Look for a database on which ``q1`` and ``q2`` give different results.

    Both queries run on the empty database and on every version of the
    databases generated for each query as generation accepts it, in scratch
    databases of the backend ``connection_string`` names, each within
    ``timeout_s``; the first database telling them apart ends the search.
    Results are compared by ``differ``: sets of rows with ``set_semantics``,
    otherwise lists when both queries order their output and bags when not.
    ``set_semantics`` also goes to the generation config, so generation
    does not aim for duplicate output rows.

    SYNTAX_ERROR: the backend rejects a query on the empty schema, or it is
    not one statement. NEQ: a database tells the queries apart. TIMEOUT: no
    database did, and generation or a query ran out of time. UNKNOWN: no
    database did, and a query does not parse here or the generator does not
    support it, or a database gives neither query any rows. EQ otherwise: the queries agree
    on every database, which is evidence, not a proof.
    """
    queries = (q1, q2)
    try:
        trees = [[tree for tree in sqlglot.parse(sql, read=dialect) if tree is not None] for sql in queries]
    except sqlglot.errors.SqlglotError as error:
        # The backend decides whether the query is valid SQL.
        trees, unparsed = None, error
    if trees is not None and any(len(statements) != 1 for statements in trees):
        return RunResult(Verdict.SYNTAX_ERROR, "a query must be one statement")
    ordered = trees is not None and all(statements[0].args.get("order") for statements in trees)
    config = replace(config or GenerationConfig(), set_semantics=set_semantics)
    database = DBManager(connection_string, dialect)
    catalog = Catalog.from_ddl(schema, dialect=dialect)
    counterexample, results = None, (None, None)

    def check(instance: Instance) -> bool:
        """Run both queries on a database; whether they differ there."""
        nonlocal counterexample, results
        with database.scratch() as connection:
            connection.create_tables(schema)
            connection.load(instance)
            results = tuple(_run(connection, query, timeout_s) for query in queries)
        if not differ(*results, ordered=ordered, set_semantics=set_semantics):
            return False
        counterexample = instance
        return True

    # The empty database, which generation never returns, also checks that
    # the backend accepts both queries.
    check(Instance(catalog))
    for result in results:
        if isinstance(result, str):
            return RunResult(Verdict.SYNTAX_ERROR, result)
    if counterexample is not None:
        return RunResult(Verdict.NEQ, "the empty database tells them apart", counterexample, results)
    if trees is None:
        return RunResult(Verdict.UNKNOWN, f"the queries do not parse: {unparsed}")

    doubts: list[tuple[Verdict, str]] = []
    for index, sql in enumerate(queries, 1):
        try:
            generated = generate(sql, catalog, config=config, on_instance=check)
        except (SubEqError, ExecutionError) as error:
            doubts.append((Verdict.UNKNOWN, f"q{index}: {error}"))
            continue
        if generated.instance is None:
            if generated.timed_out:
                doubts.append((Verdict.TIMEOUT, f"q{index}: generation ran out of time"))
            else:
                doubts.append((Verdict.UNKNOWN, f"q{index}: {generated.unsupported or 'no database generated'}"))
            continue
        if counterexample is not None:
            return RunResult(Verdict.NEQ, f"q{index}'s database tells them apart", counterexample, results)
        if None in results:
            doubts.append((Verdict.TIMEOUT, f"a query ran out of time on q{index}'s database"))
        elif generated.timed_out:
            doubts.append((Verdict.TIMEOUT, f"q{index}: generation ran out of time"))
        elif not any(isinstance(result, list) and result for result in results):
            doubts.append((Verdict.UNKNOWN, f"q{index}'s database gives neither query rows"))
    if doubts:
        verdict = Verdict.TIMEOUT if any(verdict is Verdict.TIMEOUT for verdict, _ in doubts) else Verdict.UNKNOWN
        return RunResult(verdict, "; ".join(reason for _, reason in doubts))
    return RunResult(Verdict.EQ)


def differ(first, second, *, ordered: bool = False, set_semantics: bool = False) -> bool:
    """Whether two query results differ.

    A result is a list of rows, the backend's error message, or None when
    the query ran out of time, which differs from nothing. A failing query
    differs from one that executes; two failing queries agree. Rows compare
    as sets with ``set_semantics``, otherwise as lists when ``ordered`` and
    as bags when not.
    """
    if first is None or second is None:
        return False
    if isinstance(first, str) or isinstance(second, str):
        return isinstance(first, str) != isinstance(second, str)
    if set_semantics:
        return set(first) != set(second)
    if ordered:
        return first != second
    return Counter(first) != Counter(second)


def _run(connection: Connect, sql: str, timeout_s: float):
    """The rows of a query, the backend's error message, or None when
    ``timeout_s`` ran out."""
    started = time.monotonic()
    try:
        return connection.execute(sql, timeout=timeout_s)
    except DBAPIError as error:
        return None if time.monotonic() - started >= timeout_s else str(error.orig)


__all__ = ["RunResult", "Verdict", "differ", "disprove", "instantiate_db"]
