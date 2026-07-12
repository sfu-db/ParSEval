"""ParSEval main entry point — public API for test database generation.

Usage::

    from parseval import instantiate_db, disprove

    result = instantiate_db(sql, schema, connection_string, dialect)
    result = disprove(sql1, sql2, schema, connection_string, dialect)
"""

from __future__ import annotations

import time

from parseval.db_manager import DBManager, execute_query
from parseval.disprover import Disprover
from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.logger import log as _log
from parseval.states import (
    DisproveResult,
    ExecutionResult,
    GenerationResult,
    InstantiateResult,
)
from parseval.symbolic import CoverageThresholds, SymbolicEngine


def instantiate_db(
    sql: str,
    schema: str,
    connection_string: str,
    dialect: str = "sqlite",
    *,
    max_iterations: int = 10,
    atom_null: int = 1,
    atom_false: int = 1,
    atom_dup: int = 1,
    project_null: int = 1,
    distinct_duplicate: int = 1,
    distinct_unique: int = 1,
    timeout: int = 60,
) -> InstantiateResult:
    """Generate a test database instance for a SQL query and persist it.

    1. Build Instance from DDL schema.
    2. Run SymbolicEngine.generate() (with speculate seeding) for coverage-driven test data.
    3. Persist instance to the target database via to_db().
    4. Execute the SQL query against the persisted database.

    Args:
        sql: SQL query to generate test data for.
        schema: DDL schema string.
        connection_string: Database connection string.
        dialect: SQL dialect (default: "sqlite").
        max_iterations: Max iterations for the symbolic engine.
        atom_null: Threshold for NULL branch coverage (0 = disabled).
        atom_false: Threshold for FALSE branch coverage (0 = disabled).
        atom_dup: Threshold for duplicate detection (0 = disabled).
        project_null: Threshold for projected NULL coverage (0 = disabled).
        distinct_duplicate: Threshold for DISTINCT duplicate elimination (0 = disabled).
        distinct_unique: Threshold for DISTINCT unique rows (0 = disabled).
        timeout: Query execution timeout in seconds (default: 60).

    Returns:
        InstantiateResult with generation metadata and query execution result.
    """
    t0 = time.time()
    _log.info("instantiate_db: dialect=%s, sql=%.80s", dialect, sql)
    try:
        instance = Instance(ddls=schema, name="parseval", dialect=dialect)
        thresholds = CoverageThresholds(
            atom_null=atom_null,
            atom_false=atom_false,
            atom_dup=atom_dup,
            project_null=project_null,
            distinct_duplicate=distinct_duplicate,
            distinct_unique=distinct_unique,
        )
        engine = SymbolicEngine(
            instance,
            sql,
            dialect=dialect,
            max_iterations=max_iterations,
            connection_string=connection_string,
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
              gen_result.rows_generated, gen_result.coverage, time.time() - t0)
    return InstantiateResult(
        success=True, generation=generation, q_result=q_result,
        connection_string=connection_string,
    )


def disprove(
    sql1: str,
    sql2: str,
    schema: str,
    connection_string: str,
    dialect: str,
    *,
    max_iterations: int = 10,
    semantics: str = "bag",
    atom_null: int = 1,
    atom_false: int = 1,
    atom_dup: int = 1,
    project_null: int = 1,
    distinct_duplicate: int = 1,
    distinct_unique: int = 1,
    timeout: int = 60,
) -> DisproveResult:
    """Attempt to disprove equivalence of two SQL queries.

    Args:
        sql1: First SQL query.
        sql2: Second SQL query.
        schema: DDL schema string.
        connection_string: Database connection string.
        dialect: SQL dialect.
        max_iterations: Max iterations per SymbolicEngine run.
        semantics: How to compare results (BAG or SET).
        atom_null: Threshold for NULL branch coverage (0 = disabled).
        atom_false: Threshold for FALSE branch coverage (0 = disabled).
        atom_dup: Threshold for duplicate detection (0 = disabled).
        project_null: Threshold for projected NULL coverage (0 = disabled).
        distinct_duplicate: Threshold for DISTINCT duplicate elimination (0 = disabled).
        distinct_unique: Threshold for DISTINCT unique rows (0 = disabled).
        timeout: Query execution timeout in seconds.

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
        atom_false=atom_false,
        atom_dup=atom_dup,
        project_null=project_null,
        distinct_duplicate=distinct_duplicate,
        distinct_unique=distinct_unique,
    )
    return disprover.disprove()
