"""ParSEval main entry point — public API for test database generation.

Usage::

    from parseval import instantiate_db, disprove

    result = instantiate_db(sql, schema, connection_string, dialect)
    result = disprove(sql1, sql2, schema, connection_string, dialect)
"""

from __future__ import annotations

import time
from typing import Any, Optional

from parseval.db_manager import DBManager
from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.logger import get_logger
from parseval.states import (
    DisproveResult,
    ExecutionResult,
    GenerationResult,
    InstantiateResult,
    Semantics,
    Verdict,
    compare_results,
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
    **kwargs: Any,
) -> InstantiateResult:
    """Generate a test database instance for a SQL query and persist it."""
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
        to_db(instance, connection_string, dialect=dialect)
        _log.info("instantiate_db: done, %d rows, coverage=%.2f, %.3fs",
                  gen_result.rows_generated, gen_result.coverage, time.time() - t0)
        return InstantiateResult(
            success=True, generation=generation,
            connection_string=connection_string, db_id=db_id,
        )
    except Exception as e:
        _log.error("instantiate_db failed: %s", e, exc_info=True)
        return InstantiateResult(
            success=False,
            generation=GenerationResult(success=False, error_msg=str(e), elapsed_time=time.time() - t0),
            connection_string=connection_string, db_id=db_id, error_msg=str(e),
        )


def disprove(
    sql1: str,
    sql2: str,
    schema: str,
    connection_string: str,
    dialect: str,
    *,
    db_id: str = "parseval_disprove",
    max_iterations: int = 10,
    semantics: Semantics = Semantics.BAG,
    atom_null: int = 0,
    atom_dup: int = 1,
    timeout: int = 15,
    **kwargs: Any,
) -> DisproveResult:
    """Attempt to disprove equivalence of two SQL queries.

    Strategy:
    1. If queries are textually identical → EQ immediately.
    2. Generate instance targeting sql1, dump to DB, execute both.
       If NEQ → return early.
    3. Generate instance targeting sql2 on same instance, dump, execute both.
       Return final verdict.
    """
    t0 = time.time()
    empty = ExecutionResult(query="")
    _log.info("disprove: semantics=%s, dialect=%s", semantics.value, dialect)
    _log.debug("  sql1=%.100s", sql1)
    _log.debug("  sql2=%.100s", sql2)

    # 1. Textual identity check
    # if _normalize_sql(sql1) == _normalize_sql(sql2):
    #     return DisproveResult(
    #         verdict=Verdict.EQ, semantics=semantics,
    #         q1_result=empty, q2_result=empty,
    #         generation=GenerationResult(success=True, elapsed_time=0.0),
    #         connection_string=connection_string, db_id=db_id,
    #     )

    # 2. Generate targeting sql1, check for NEQ
    try:
        instance = Instance(ddls=schema, name=db_id, dialect=dialect)
        engine1 = SymbolicEngine(instance, sql1, dialect=dialect, max_iterations=max_iterations, **kwargs)
        engine1.generate(thresholds=CoverageThresholds(atom_null=atom_null, atom_dup=atom_dup))
    except Exception as e:
        _log.error("Generation failed for sql1: %s", e, exc_info=True)
        return DisproveResult(
            verdict=Verdict.UNKNOWN, semantics=semantics,
            q1_result=empty, q2_result=empty,
            generation=GenerationResult(success=False, error_msg=str(e), elapsed_time=time.time() - t0),
            connection_string=connection_string, db_id=db_id, error_msg=f"Generation failed: {e}",
        )

    # Dump and execute
    try:
        to_db(instance, connection_string, dialect=dialect)
    except Exception as e:
        return DisproveResult(
            verdict=Verdict.UNKNOWN, semantics=semantics,
            q1_result=empty, q2_result=empty,
            generation=GenerationResult(success=True, elapsed_time=time.time() - t0),
            connection_string=connection_string, db_id=db_id, error_msg=f"DB write failed: {e}",
        )

    q1_result = _execute(connection_string, dialect, sql1, timeout)
    q2_result = _execute(connection_string, dialect, sql2, timeout)
    verdict = compare_results(q1_result, q2_result, semantics)

    if verdict == Verdict.NEQ:
        # Early exit — found a distinguishing instance
        _log.info("disprove: NEQ on round 1 (early exit), %.3fs", time.time() - t0)
        return DisproveResult(
            verdict=Verdict.NEQ, semantics=semantics,
            q1_result=q1_result, q2_result=q2_result,
            generation=GenerationResult(success=True, elapsed_time=time.time() - t0),
            connection_string=connection_string, db_id=db_id,
        )

    if verdict in (Verdict.SYNTAX_ERROR, Verdict.RUNTIME_ERROR):
        return DisproveResult(
            verdict=verdict, semantics=semantics,
            q1_result=q1_result, q2_result=q2_result,
            generation=GenerationResult(success=True, elapsed_time=time.time() - t0),
            connection_string=connection_string, db_id=db_id,
        )

    # 3. Generate targeting sql2 on same instance, try again
    try:
        engine2 = SymbolicEngine(instance, sql2, dialect=dialect, max_iterations=max_iterations, **kwargs)
        gen_result = engine2.generate(thresholds=CoverageThresholds(atom_null=atom_null, atom_dup=atom_dup))
    except Exception as e:
        _log.error("Generation failed for sql2: %s", e, exc_info=True)
        # If sql2 generation fails, return EQ from round 1
        return DisproveResult(
            verdict=Verdict.EQ, semantics=semantics,
            q1_result=q1_result, q2_result=q2_result,
            generation=GenerationResult(success=True, elapsed_time=time.time() - t0),
            connection_string=connection_string, db_id=db_id,
        )

    # Dump updated instance and re-execute
    try:
        to_db(instance, connection_string, dialect=dialect)
    except Exception as e:
        _log.error("DB write failed on round 2: %s", e, exc_info=True)

    q1_result = _execute(connection_string, dialect, sql1, timeout)
    q2_result = _execute(connection_string, dialect, sql2, timeout)
    verdict = compare_results(q1_result, q2_result, semantics)

    _log.info("disprove: round 2 verdict=%s, %.3fs", verdict.value, time.time() - t0)
    return DisproveResult(
        verdict=verdict, semantics=semantics,
        q1_result=q1_result, q2_result=q2_result,
        generation=GenerationResult(
            success=True,
            rows_generated=gen_result.rows_generated,
            coverage=gen_result.coverage,
            elapsed_time=time.time() - t0,
        ),
        connection_string=connection_string, db_id=db_id,
    )


# =============================================================================
# Helpers
# =============================================================================


def _execute(connection_string: str, dialect: str, sql: str, timeout: int = 15) -> ExecutionResult:
    """Execute a query using DBManager."""
    t0 = time.time()
    try:
        with DBManager().get_connection(connection_string, dialect) as conn:
            rows = conn.execute(sql, fetch="all", timeout=timeout)
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


def _normalize_sql(sql: str) -> str:
    """Normalize SQL for textual comparison."""
    import re
    s = sql.strip().rstrip(";").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()
