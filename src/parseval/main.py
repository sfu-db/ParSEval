"""ParSEval main entry point — public API for test database generation.

Usage::

    from parseval import instantiate_db, disprove

    result = instantiate_db(sql, schema, connection_string, dialect)
    result = disprove(sql1, sql2, schema, connection_string, dialect)
"""

from __future__ import annotations

import time
import re

from sqlglot import exp, parse_one

from parseval.db_manager import execute_query
from parseval.generator import BmcBounds, generate
from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.logger import log as _log
from parseval.states import (
    DisproveResult,
    ExecutionResult,
    InstantiateResult,
    RunResult,
    Verdict,
    compare_results,
)


def instantiate_db(
    sql: str,
    schema: str,
    connection_string: str,
    dialect: str = "sqlite",
    *,
    table_rows: int = 1,
    join_width: int = 1,
    result_rows: int = 3,
    groups: int = 3,
    rows_per_group: int = 3,
    subquery_rows: int = 1,
    order_competitors: int = 0,
    max_iterations: int = 4,
    max_table_rows: int = 512,
    generate_negatives: bool = True,
    timeout: int = 60,
) -> InstantiateResult:
    """Generate a test database instance for a SQL query and persist it.

    1. Generate an Instance from DDL/query via parseval.generator.generate().
    2. Persist instance to the target database via to_db().
    3. Execute the SQL query against the persisted database.

    Args:
        sql: SQL query to generate test data for.
        schema: DDL schema string.
        connection_string: Database connection string.
        dialect: SQL dialect (default: "sqlite").
        table_rows: Initial row target per table.
        join_width: Join-width generation bound.
        result_rows: Root result row target.
        groups: Aggregate group target.
        rows_per_group: Aggregate rows-per-group target.
        subquery_rows: Scalar subquery row target.
        order_competitors: Extra rows for ordering competitors.
        max_iterations: Max bounded expansion iterations.
        max_table_rows: Safety cap for generated rows per table.
        generate_negatives: Whether speculative seeding should include negative rows.
        timeout: Query execution timeout in seconds (default: 60).

    Returns:
        InstantiateResult with generation metadata and query execution result.
    """
    t0 = time.time()
    _log.info("instantiate_db: dialect=%s, sql=%.80s", dialect, sql)
    try:
        bounds = BmcBounds(
            table_rows=table_rows,
            join_width=join_width,
            result_rows=result_rows,
            groups=groups,
            rows_per_group=rows_per_group,
            subquery_rows=subquery_rows,
            order_competitors=order_competitors,
            max_iterations=max_iterations,
            max_table_rows=max_table_rows,
        )
        instance = generate(
            schema,
            sql,
            dialect=dialect,
            bounds=bounds,
            generate_negatives=generate_negatives,
        )
        generation_state = getattr(instance, "generation", None)
        rows_generated = _rows_generated(instance)
        status = str(getattr(generation_state, "status", "") or "")
        coverage = float(getattr(generation_state, "coverage_ratio", 0.0) or 0.0)
        generation = RunResult(
            success=True,
            status=status,
            rows_generated=rows_generated,
            coverage=coverage,
            elapsed_time=time.time() - t0,
        )
    except Exception as e:
        _log.error("instantiate_db: generation failed: %s", e, exc_info=True)
        return InstantiateResult(
            success=False,
            generation=RunResult(success=False, error_msg=str(e), elapsed_time=time.time() - t0),
            connection_string=connection_string, error_msg=str(e),
        )

    try:
        to_db(instance, connection_string, dialect=dialect)
    except Exception as e:
        _log.error("instantiate_db: DB write failed: %s", e, exc_info=True)
        return InstantiateResult(
            success=False,
            generation=generation,
            connection_string=connection_string, error_msg=f"DB write failed: {e}",
        )

    q_result = execute_query(sql, connection_string, dialect, timeout)

    _log.info("instantiate_db: done, %d rows, coverage=%.2f, %.3fs",
              generation.rows_generated, generation.coverage, time.time() - t0)
    return InstantiateResult(
        success=True, generation=generation, q_result=q_result,
        connection_string=connection_string,
    )


def _rows_generated(instance) -> int:
    generation_state = getattr(instance, "generation", None)
    create_rows = getattr(generation_state, "create_rows", None)
    if create_rows:
        return sum(len(rows) for rows in create_rows.values())
    return sum(len(instance.get_rows(table)) for table in instance.tables)


def disprove(
    sql1: str,
    sql2: str,
    schema: str,
    connection_string: str,
    dialect: str,
    *,
    semantics: str = "bag",
    table_rows: int = 1,
    join_width: int = 1,
    result_rows: int = 3,
    groups: int = 3,
    rows_per_group: int = 3,
    subquery_rows: int = 1,
    order_competitors: int = 0,
    max_iterations: int = 4,
    max_table_rows: int = 512,
    generate_negatives: bool = True,
    timeout: int = 60,
) -> DisproveResult:
    """Attempt to disprove equivalence of two SQL queries.

    Args:
        sql1: First SQL query.
        sql2: Second SQL query.
        schema: DDL schema string.
        connection_string: Database connection string.
        dialect: SQL dialect.
        semantics: How to compare results (BAG or SET).
        table_rows: Initial row target per table.
        join_width: Join-width generation bound.
        result_rows: Root result row target.
        groups: Aggregate group target.
        rows_per_group: Aggregate rows-per-group target.
        subquery_rows: Scalar subquery row target.
        order_competitors: Extra rows for ordering competitors.
        max_iterations: Max bounded expansion iterations.
        max_table_rows: Safety cap for generated rows per table.
        generate_negatives: Whether speculative seeding should include negative rows.
        timeout: Query execution timeout in seconds.

    Returns:
        DisproveResult with verdict.
    """
    t0 = time.time()
    if _normalize_sql(sql1) == _normalize_sql(sql2):
        _log.info("disprove: textual identity -> EQ, %.3fs", time.time() - t0)
        return DisproveResult(
            verdict=Verdict.EQ,
            semantics=semantics,
            q1_result=ExecutionResult(query=sql1),
            q2_result=ExecutionResult(query=sql2),
            generation=RunResult(success=True, elapsed_time=time.time() - t0),
            connection_string=connection_string,
        )

    syntax_error = _execution_syntax_error_for_pair(
        schema,
        sql1,
        sql2,
        connection_string,
        dialect,
        timeout,
    )
    if syntax_error is not None:
        return _syntax_error_result(
            sql1,
            sql2,
            semantics,
            connection_string,
            syntax_error.error_msg,
            t0,
        )

    projection_counts = (_final_projection_count(sql1, dialect), _final_projection_count(sql2, dialect))
    if None not in projection_counts and projection_counts[0] != projection_counts[1]:
        return DisproveResult(
            verdict=Verdict.NEQ,
            semantics=semantics,
            q1_result=ExecutionResult(query=sql1),
            q2_result=ExecutionResult(query=sql2),
            generation=RunResult(success=True, elapsed_time=time.time() - t0),
            connection_string=connection_string,
        )

    sql1, sql2 = _preprocess_sql_pair(sql1, sql2, dialect)

    bounds = BmcBounds(
        table_rows=table_rows,
        join_width=join_width,
        result_rows=result_rows,
        groups=groups,
        rows_per_group=rows_per_group,
        subquery_rows=subquery_rows,
        order_competitors=order_competitors,
        max_iterations=max_iterations,
        max_table_rows=max_table_rows,
    )
    first = _run_disprove_candidate(
        sql1,
        sql1,
        sql2,
        schema,
        connection_string,
        dialect,
        bounds,
        generate_negatives,
        timeout,
        semantics,
    )
    if first.verdict != Verdict.EQ:
        return first
    return _run_disprove_candidate(
        sql2,
        sql1,
        sql2,
        schema,
        connection_string,
        dialect,
        bounds,
        generate_negatives,
        timeout,
        semantics,
    )


def _run_disprove_candidate(
    seed_sql: str,
    sql1: str,
    sql2: str,
    schema: str,
    connection_string: str,
    dialect: str,
    bounds: BmcBounds,
    generate_negatives: bool,
    timeout: int,
    semantics: str,
) -> DisproveResult:
    t0 = time.time()
    try:
        instance = generate(
            schema,
            seed_sql,
            dialect=dialect,
            bounds=bounds,
            generate_negatives=generate_negatives,
        )
        generation = _run_result_for_instance(instance, t0)
    except Exception as e:
        message = str(e)
        return DisproveResult(
            verdict=Verdict.UNKNOWN,
            semantics=semantics,
            q1_result=ExecutionResult(query=sql1),
            q2_result=ExecutionResult(query=sql2),
            generation=RunResult(success=False, error_msg=message, elapsed_time=time.time() - t0),
            connection_string=connection_string,
            error_msg=message,
        )

    try:
        to_db(instance, connection_string, dialect=dialect)
    except Exception as e:
        message = f"DB write failed: {e}"
        return DisproveResult(
            verdict=Verdict.UNKNOWN,
            semantics=semantics,
            q1_result=ExecutionResult(query=sql1),
            q2_result=ExecutionResult(query=sql2),
            generation=generation,
            connection_string=connection_string,
            error_msg=message,
        )

    q1 = execute_query(sql1, connection_string, dialect, timeout)
    q2 = execute_query(sql2, connection_string, dialect, timeout)
    verdict = compare_results(q1, q2, semantics)
    error_msg = q1.error_msg or q2.error_msg
    return DisproveResult(
        verdict=verdict,
        semantics=semantics,
        q1_result=q1,
        q2_result=q2,
        generation=generation,
        connection_string=connection_string,
        error_msg=error_msg,
    )


def _run_result_for_instance(instance, started_at: float) -> RunResult:
    generation_state = getattr(instance, "generation", None)
    return RunResult(
        success=True,
        status=str(getattr(generation_state, "status", "") or ""),
        rows_generated=_rows_generated(instance),
        coverage=float(getattr(generation_state, "coverage_ratio", 0.0) or 0.0),
        elapsed_time=time.time() - started_at,
    )


def _execution_syntax_error_for_pair(
    schema: str,
    sql1: str,
    sql2: str,
    connection_string: str,
    dialect: str,
    timeout: int,
) -> ExecutionResult | None:
    instance = Instance(schema, name="syntax_check", dialect=dialect)
    to_db(instance, connection_string, dialect=dialect)
    for sql in (sql1, sql2):
        result = execute_query(sql, connection_string, dialect, timeout)
        if result.is_syntax_error:
            return result
    return None


def _syntax_error_result(
    sql1: str,
    sql2: str,
    semantics: str,
    connection_string: str,
    error_msg: str,
    started_at: float,
) -> DisproveResult:
    return DisproveResult(
        verdict=Verdict.SYNTAX_ERROR,
        semantics=semantics,
        q1_result=ExecutionResult(query=sql1, error_msg=error_msg),
        q2_result=ExecutionResult(query=sql2),
        generation=RunResult(
            success=False,
            error_msg=error_msg,
            elapsed_time=time.time() - started_at,
        ),
        connection_string=connection_string,
        error_msg=error_msg,
    )


def _final_projection_count(sql: str, dialect: str) -> int | None:
    try:
        expression = parse_one(sql, dialect=dialect)
    except Exception:
        return None
    if not isinstance(expression, exp.Select):
        return None
    expressions = expression.expressions
    if any(
        isinstance(projection, exp.Star)
        or isinstance(getattr(projection, "this", None), exp.Star)
        for projection in expressions
    ):
        return None
    return len(expressions)


def _normalize_sql(sql: str) -> str:
    normalized = sql.strip().rstrip(";").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.lower()


def _preprocess_sql_pair(sql1: str, sql2: str, dialect: str = "sqlite") -> tuple[str, str]:
    """Strip matching LIMIT / ORDER BY tails so generation is not artificially constrained."""
    try:
        expr1 = parse_one(sql1, dialect=dialect)
        expr2 = parse_one(sql2, dialect=dialect)
    except Exception:
        return sql1, sql2

    limit1 = expr1.args.get("limit")
    limit2 = expr2.args.get("limit")
    offset1 = expr1.args.get("offset")
    offset2 = expr2.args.get("offset")
    if (
        limit1 is not None
        and limit2 is not None
        and limit1.sql() == limit2.sql()
        and (offset1 is None) == (offset2 is None)
        and (offset1 is None or offset1.sql() == offset2.sql())
    ):
        expr1.set("limit", None)
        expr1.set("offset", None)
        expr2.set("limit", None)
        expr2.set("offset", None)

    order1 = expr1.args.get("order")
    order2 = expr2.args.get("order")
    if order1 is not None and order2 is not None and order1.sql() == order2.sql():
        expr1.set("order", None)
        expr2.set("order", None)

    return expr1.sql(dialect=dialect), expr2.sql(dialect=dialect)
