"""Disprover — attempt to disprove equivalence of two SQL queries.

Uses multiple strategies to find a distinguishing database instance:
1. Textual identity check (quick win)
2. Coverage-based generation (generate for each query, compare results)

Usage::

    disprover = Disprover(sql1, sql2, schema, dialect="sqlite",
                          connection_string="sqlite:///...")
    result = disprover.disprove()
"""

from __future__ import annotations

import logging
import re
import time

from parseval.db_manager import DBManager
from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.states import (
    DisproveResult,
    ExecutionResult,
    GenerationResult,
    Semantics,
    Verdict,
    compare_results,
)
from parseval.symbolic import CoverageThresholds, SymbolicEngine

logger = logging.getLogger("parseval.disprover")


class Disprover:
    """Attempt to disprove equivalence of two SQL queries.

    Strategies are tried in order until a verdict is reached:
    1. Textual identity check
    2. Coverage-based generation (generate for each query, compare results)

    Args:
        sql1: First SQL query.
        sql2: Second SQL query.
        schema: DDL schema string.
        dialect: SQL dialect (default: "sqlite").
        connection_string: Database connection string for execution.
        semantics: How to compare results (BAG or SET).
        max_iterations: Max iterations per SymbolicEngine run (default: 10).
        timeout: Query execution timeout in seconds (default: 60).
        atom_null: Threshold for NULL branch coverage (default: 1).
        atom_dup: Threshold for duplicate detection (default: 1).
    """

    def __init__(
        self,
        sql1: str,
        sql2: str,
        schema: str,
        dialect: str = "sqlite",
        *,
        connection_string: str,
        semantics: Semantics = Semantics.BAG,
        max_iterations: int = 10,
        timeout: int = 60,
        atom_null: int = 1,
        atom_dup: int = 1,
    ):
        self.sql1 = sql1
        self.sql2 = sql2
        self.schema = schema
        self.dialect = dialect
        self.connection_string = connection_string
        self.semantics = semantics
        self.max_iterations = max_iterations
        self.timeout = timeout
        self.thresholds = CoverageThresholds(atom_null=atom_null, atom_dup=atom_dup)
        # Generate unique DB paths for each query to avoid overwriting
        self._conn1 = connection_string
        self._conn2 = connection_string.replace(".db", "_q2.db") if ".db" in connection_string else connection_string

    def disprove(self) -> DisproveResult:
        """Attempt to disprove equivalence using all strategies.

        Tries both queries to generate data. Returns NEQ immediately if
        a distinguishing instance is found. Returns EQ if both queries
        produce identical results. Returns RUNTIME_ERROR if generation
        or execution fails for both queries.

        Returns:
            DisproveResult with verdict:
            - NEQ: queries produce different results on some instance
            - EQ: queries produce identical results on some instance
            - SYNTAX_ERROR/RUNTIME_ERROR: one or both queries have errors
        """
        t0 = time.time()

        # Strategy 1: Textual identity check
        if _normalize_sql(self.sql1) == _normalize_sql(self.sql2):
            logger.info("disprove: textual identity -> EQ, %.3fs", time.time() - t0)
            return DisproveResult(
                verdict=Verdict.EQ,
                semantics=self.semantics,
                q1_result=ExecutionResult(query=self.sql1),
                q2_result=ExecutionResult(query=self.sql2),
                generation=GenerationResult(success=True, elapsed_time=time.time() - t0),
                connection_string=self.connection_string,
            )

        # Strategy 2: Coverage-based generation
        # Try both queries with separate databases, track results
        last_result = None

        # Try sql1 with first database
        result = self._try_generate_and_compare(self.sql1, self._conn1, t0)
        if result.verdict == Verdict.NEQ:
            return result  # Found distinguishing instance
        if result.verdict in (Verdict.SYNTAX_ERROR, Verdict.RUNTIME_ERROR):
            if last_result is None:
                last_result = result  # Track first error
        else:
            last_result = result  # Track EQ/UNKNOWN result

        # Try sql2 with second database
        result = self._try_generate_and_compare(self.sql2, self._conn2, t0)
        if result.verdict == Verdict.NEQ:
            return result  # Found distinguishing instance
        if result.verdict in (Verdict.SYNTAX_ERROR, Verdict.RUNTIME_ERROR):
            if last_result is None or last_result.verdict in (Verdict.EQ, Verdict.UNKNOWN):
                last_result = result  # Prefer error over EQ/UNKNOWN
        else:
            if last_result is None or last_result.verdict in (Verdict.RUNTIME_ERROR,):
                last_result = result  # Prefer EQ/UNKNOWN over error

        # Return best result
        if last_result is not None:
            return last_result

        # Should not reach here, but just in case
        return DisproveResult(
            verdict=Verdict.UNKNOWN,
            semantics=self.semantics,
            q1_result=ExecutionResult(query=self.sql1),
            q2_result=ExecutionResult(query=self.sql2),
            generation=GenerationResult(success=True, elapsed_time=time.time() - t0),
            connection_string=self.connection_string,
        )

    def _try_generate_and_compare(
        self, target_sql: str, connection_string: str, t0: float
    ) -> DisproveResult:
        """Generate data for target_sql, execute both queries, check results.

        Args:
            target_sql: The SQL query to generate data for.
            connection_string: Database connection string to use.
            t0: Start time for elapsed time calculation.

        Returns:
            DisproveResult with verdict:
            - NEQ: queries produce different results
            - RUNTIME_ERROR: generation or execution failed
            - EQ: queries produce identical results (may be empty)
        """
        # Generate instance
        try:
            instance = Instance(ddls=self.schema, name="disprove", dialect=self.dialect)
            engine = SymbolicEngine(
                instance, target_sql,
                dialect=self.dialect,
                max_iterations=self.max_iterations,
            )
            gen_result = engine.generate(thresholds=self.thresholds)
        except Exception as e:
            logger.debug("disprove: generation failed: %s", e)
            return DisproveResult(
                verdict=Verdict.RUNTIME_ERROR,
                semantics=self.semantics,
                q1_result=ExecutionResult(query=self.sql1, error_msg=str(e)),
                q2_result=ExecutionResult(query=self.sql2),
                generation=GenerationResult(success=False, error_msg=str(e), elapsed_time=time.time() - t0),
                connection_string=connection_string,
                error_msg=f"Generation failed: {e}",
            )

        # Dump to DB and execute both queries
        try:
            to_db(instance, connection_string, dialect=self.dialect)
        except Exception as e:
            logger.debug("disprove: DB write failed: %s", e)
            return DisproveResult(
                verdict=Verdict.RUNTIME_ERROR,
                semantics=self.semantics,
                q1_result=ExecutionResult(query=self.sql1, error_msg=str(e)),
                q2_result=ExecutionResult(query=self.sql2),
                generation=GenerationResult(
                    success=True,
                    rows_generated=gen_result.rows_generated,
                    coverage=gen_result.coverage,
                    elapsed_time=time.time() - t0,
                ),
                connection_string=connection_string,
                error_msg=f"DB write failed: {e}",
            )

        q1_result = self._execute(self.sql1, connection_string)
        q2_result = self._execute(self.sql2, connection_string)
        verdict = compare_results(q1_result, q2_result, self.semantics)

        # Handle errors
        if verdict in (Verdict.SYNTAX_ERROR, Verdict.RUNTIME_ERROR):
            logger.info("disprove: %s, %.3fs", verdict.value, time.time() - t0)
            return DisproveResult(
                verdict=verdict,
                semantics=self.semantics,
                q1_result=q1_result,
                q2_result=q2_result,
                generation=GenerationResult(
                    success=True,
                    rows_generated=gen_result.rows_generated,
                    coverage=gen_result.coverage,
                    elapsed_time=time.time() - t0,
                ),
                connection_string=connection_string,
            )

        # NEQ — found distinguishing instance
        if verdict == Verdict.NEQ:
            logger.info("disprove: NEQ found, %.3fs", time.time() - t0)
            return DisproveResult(
                verdict=Verdict.NEQ,
                semantics=self.semantics,
                q1_result=q1_result,
                q2_result=q2_result,
                generation=GenerationResult(
                    success=True,
                    rows_generated=gen_result.rows_generated,
                    coverage=gen_result.coverage,
                    elapsed_time=time.time() - t0,
                ),
                connection_string=connection_string,
            )

        # EQ — but only if both queries returned non-empty results
        # Empty results don't prove equivalence
        if not q1_result.rows and not q2_result.rows:
            logger.info("disprove: both empty, %.3fs", time.time() - t0)
            return DisproveResult(
                verdict=Verdict.UNKNOWN,
                semantics=self.semantics,
                q1_result=q1_result,
                q2_result=q2_result,
                generation=GenerationResult(
                    success=True,
                    rows_generated=gen_result.rows_generated,
                    coverage=gen_result.coverage,
                    elapsed_time=time.time() - t0,
                ),
                connection_string=connection_string,
                error_msg="Both queries returned empty results - cannot prove equivalence",
            )

        # Non-empty EQ — valid equivalence proof
        logger.info("disprove: EQ (non-empty), %.3fs", time.time() - t0)
        return DisproveResult(
            verdict=Verdict.EQ,
            semantics=self.semantics,
            q1_result=q1_result,
            q2_result=q2_result,
            generation=GenerationResult(
                success=True,
                rows_generated=gen_result.rows_generated,
                coverage=gen_result.coverage,
                elapsed_time=time.time() - t0,
            ),
            connection_string=connection_string,
        )

    def _execute(self, sql: str, connection_string: str = None) -> ExecutionResult:
        """Execute a query using DBManager."""
        conn = connection_string or self.connection_string
        t0 = time.time()
        try:
            with DBManager().get_connection(conn, self.dialect) as connection:
                rows = connection.execute(sql, fetch="all", timeout=self.timeout)
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
    s = sql.strip().rstrip(";").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()


__all__ = ["Disprover"]
