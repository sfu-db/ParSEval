"""ParSEval main entry point — public API for test database generation.

Usage::

    from parseval import instantiate_db, disprove

    result = instantiate_db(sql, schema, connection_string, dialect)
    result = disprove(sql1, sql2, schema, connection_string, dialect)
"""

from __future__ import annotations

import time
from typing import Any

from parseval.db_manager import DBManager
from parseval.disprover import Disprover
from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.logger import get_logger
from parseval.states import (
    DisproveResult,
    ExecutionResult,
    GenerationResult,
    InstantiateResult,
    Semantics,
)
from parseval.symbolic import CoverageThresholds, SymbolicEngine

_log = get_logger("engine")


def instantiate_db(
    sql: str,
    schema: str,
    connection_string: str,
    dialect: str = "sqlite",
    *,
    db_id: str = "parseval",
    max_iterations: int = 10,
    atom_null: int = 0,
    atom_dup: int = 1,
    timeout: int = 60,
    **kwargs: Any,
) -> InstantiateResult:
    """Generate a test database instance for a SQL query and persist it.

    Procedure mirrors the disprover:
    1. Build Instance from DDL schema.
    2. Run SymbolicEngine.generate() for coverage-driven test data.
    3. Persist instance to the target database via to_db().
    4. Execute the SQL query against the persisted database.

    Args:
        sql: SQL query to generate test data for.
        schema: DDL schema string.
        connection_string: Database connection string.
        dialect: SQL dialect (default: "sqlite").
        db_id: Identifier for the database instance.
        max_iterations: Max iterations for the symbolic engine.
        atom_null: Threshold for NULL branch coverage (0 = disabled).
        atom_dup: Threshold for duplicate detection.
        timeout: Query execution timeout in seconds (default: 60).
        **kwargs: Additional arguments passed to SymbolicEngine.

    Returns:
        InstantiateResult with generation metadata and query execution result.
    """
    t0 = time.time()
    _log.info("instantiate_db: dialect=%s, sql=%.80s", dialect, sql)
    try:
        instance = Instance(ddls=schema, name=db_id, dialect=dialect)
        thresholds = CoverageThresholds(atom_null=atom_null, atom_dup=atom_dup)
        engine = SymbolicEngine(
            instance, sql, dialect=dialect, max_iterations=max_iterations, **kwargs
        )
        gen_result = engine.generate(thresholds=thresholds)
        generation = GenerationResult(
            success=True,
            rows_generated=gen_result.rows_generated,
            coverage=gen_result.coverage,
            elapsed_time=time.time() - t0,
        )
    except Exception as e:
        _log.error("instantiate_db: generation failed: %s", e, exc_info=True)
        return InstantiateResult(
            success=False,
            generation=GenerationResult(success=False, error_msg=str(e), elapsed_time=time.time() - t0),
            connection_string=connection_string, db_id=db_id, error_msg=str(e),
        )

    try:
        to_db(instance, connection_string, dialect=dialect)
    except Exception as e:
        _log.error("instantiate_db: DB write failed: %s", e, exc_info=True)
        return InstantiateResult(
            success=False,
            generation=generation,
            connection_string=connection_string, db_id=db_id, error_msg=f"DB write failed: {e}",
        )

    q_result = _execute(sql, connection_string, dialect, timeout)

    _log.info("instantiate_db: done, %d rows, coverage=%.2f, %.3fs",
              gen_result.rows_generated, gen_result.coverage, time.time() - t0)
    return InstantiateResult(
        success=True, generation=generation, q_result=q_result,
        connection_string=connection_string, db_id=db_id,
    )


def _execute(sql: str, connection_string: str, dialect: str, timeout: int) -> ExecutionResult:
    """Execute a query against a persisted database using DBManager."""
    t0 = time.time()
    try:
        with DBManager().get_connection(connection_string, dialect) as connection:
            rows = connection.execute(sql, fetch="all", timeout=timeout)
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


def disprove(
    sql1: str,
    sql2: str,
    schema: str,
    connection_string: str,
    dialect: str,
    *,
    max_iterations: int = 10,
    semantics: Semantics = Semantics.BAG,
    atom_null: int = 1,
    atom_dup: int = 1,
    timeout: int = 60,
    **kwargs: Any,
) -> DisproveResult:
    """Attempt to disprove equivalence of two SQL queries.

    Uses the Disprover class with multiple strategies:
    1. Textual identity check (quick win)
    2. Coverage-based generation (generate for each query, compare results)

    Args:
        sql1: First SQL query.
        sql2: Second SQL query.
        schema: DDL schema string.
        connection_string: Database connection string.
        dialect: SQL dialect.
        max_iterations: Max iterations per SymbolicEngine run.
        semantics: How to compare results (BAG or SET).
        atom_null: Threshold for NULL branch coverage (1 = enabled).
        atom_dup: Threshold for duplicate detection.
        timeout: Query execution timeout in seconds.
        **kwargs: Additional arguments (unused).

    Returns:
        DisproveResult with verdict.
    """
    disprover = Disprover(
        sql1, sql2, schema, dialect,
        connection_string=connection_string,
        semantics=semantics,
        max_iterations=max_iterations,
        timeout=timeout,
        atom_null=atom_null,
        atom_dup=atom_dup,
    )
    return disprover.disprove()
