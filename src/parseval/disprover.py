"""Disprover — attempt to disprove equivalence of two SQL queries.

Uses multiple strategies to find a distinguishing database instance:
1. Textual identity check (quick win)
2. LIMIT / ORDER BY stripping (if both queries share the same tail)
3. Coverage-based generation (generate for each query, compare results)

Usage::

    disprover = Disprover(sql1, sql2, schema, dialect="sqlite",
                          connection_string="sqlite:///...")
    result = disprover.disprove()
"""

from __future__ import annotations

import logging
import re
import time

from sqlglot import exp, parse_one

from parseval.db_manager import DBManager, execute_query
from parseval.instance import Instance
from parseval.instance.io import to_db
from parseval.states import (
    DisproveResult,
    ExecutionResult,
    GenerationResult,
    SchemaException,
    SyntaxException,
    Verdict,
    compare_results,
)
from parseval.symbolic import CoverageThresholds, SymbolicEngine

logger = logging.getLogger("parseval")


class Disprover:
    """Attempt to disprove equivalence of two SQL queries.

    Args:
        sql1: First SQL query.
        sql2: Second SQL query.
        schema: DDL schema string.
        dialect: SQL dialect (default: "sqlite").
        connection_string: Database connection string for execution.
        semantics: "bag" or "set" (default: "bag").
        max_iterations: Max iterations per SymbolicEngine run (default: 10).
        timeout: Query execution timeout in seconds (default: 60).
        atom_null: Threshold for NULL branch coverage (0 = disabled).
        atom_false: Threshold for FALSE branch coverage (0 = disabled).
        atom_dup: Threshold for duplicate detection (0 = disabled).
        project_null: Threshold for projected NULL coverage (0 = disabled).
        distinct_duplicate: Threshold for DISTINCT duplicate elimination (0 = disabled).
        distinct_unique: Threshold for DISTINCT unique rows (0 = disabled).
    """

    def __init__(
        self,
        sql1: str,
        sql2: str,
        schema: str,
        dialect: str = "sqlite",
        *,
        connection_string: str,
        semantics: str = "bag",
        max_iterations: int = 10,
        timeout: int = 60,
        atom_null: int = 1,
        atom_false: int = 1,
        atom_dup: int = 1,
        project_null: int = 1,
        distinct_duplicate: int = 1,
        distinct_unique: int = 1,
    ):
        if semantics not in ("bag", "set"):
            raise ValueError(f"semantics must be 'bag' or 'set', got {semantics!r}")
        self.sql1, self.sql2 = _preprocess_sql_pair(sql1, sql2, dialect)
        self.schema = schema
        self.dialect = dialect
        self.connection_string = connection_string
        self.semantics = semantics
        self.max_iterations = max_iterations
        self.timeout = timeout
        self.thresholds = CoverageThresholds(
            atom_null=atom_null,
            atom_false=atom_false,
            atom_dup=atom_dup,
            project_null=project_null,
            distinct_duplicate=distinct_duplicate,
            distinct_unique=distinct_unique,
        )

    def disprove(self) -> DisproveResult:
        """Attempt to disprove equivalence using all strategies."""
        t0 = time.time()

        if _normalize_sql(self.sql1) == _normalize_sql(self.sql2):
            logger.info("disprove: textual identity -> EQ, %.3fs", time.time() - t0)
            return self._make_result(Verdict.EQ, t0)

        # Try sql1: generate data, execute both queries
        r1 = self._try_generate_and_compare(self.sql1, t0)
        if r1.verdict == Verdict.NEQ:
            return r1

        # Clean DB so sql2 starts fresh
        self._clear_database()

        # Try sql2: generate data, execute both queries
        r2 = self._try_generate_and_compare(self.sql2, t0)
        if r2.verdict == Verdict.NEQ:
            return r2

        # Pick the best non-NEQ result: prefer EQ over UNKNOWN over errors
        return self._pick_best(r1, r2)

    def check_syntax(self, sql1: str, sql2: str) -> DisproveResult:
        """Check both queries for syntax errors against the DDL schema.

        Creates the database tables from the schema, then executes both
        queries.  Returns SYNTAX_ERROR if either query fails (parse error,
        unknown column, etc.) or if the schema itself cannot be loaded.
        """
        t0 = time.time()

        try:
            instance = Instance(
                ddls=self.schema, name="syntax_check", dialect=self.dialect
            )
        except Exception as e:
            return self._make_result(
                Verdict.SYNTAX_ERROR, t0,
                error_msg=f"Schema parsing failed: {e}",
            )

        try:
            to_db(instance, self.connection_string, dialect=self.dialect)
        except Exception as e:
            return self._make_result(
                Verdict.SYNTAX_ERROR, t0,
                error_msg=f"DB write failed: {e}",
            )

        q1 = execute_query(sql1, self.connection_string, self.dialect, self.timeout)
        q2 = execute_query(sql2, self.connection_string, self.dialect, self.timeout)

        verdict = compare_results(q1, q2, self.semantics)
        if verdict in (Verdict.SYNTAX_ERROR, Verdict.RUNTIME_ERROR):
            return self._make_result(
                verdict, t0, q1=q1, q2=q2,
                error_msg=q1.error_msg or q2.error_msg,
            )

        return self._make_result(Verdict.EQ, t0, q1=q1, q2=q2)

    def _try_generate_and_compare(
        self, target_sql: str, t0: float
    ) -> DisproveResult:
        """Generate data for target_sql, execute both queries, compare."""
        # Generate
        gen_result = None
        instance = None
        try:
            instance = Instance(ddls=self.schema, name="disprove", dialect=self.dialect)
            engine = SymbolicEngine(
                instance, target_sql,
                dialect=self.dialect,
                max_iterations=self.max_iterations,
                connection_string=self.connection_string,
            )
            gen_result = engine.generate(thresholds=self.thresholds)
        except Exception as e:
            logger.debug("disprove: generation failed: %s", e)
            verdict = _verdict_for_generation_exception(e)
            return self._make_result(
                verdict,
                t0,
                gen_result=gen_result,
                generation_error_msg=str(e),
                error_msg=str(e),
            )

        # Persist
        try:
            to_db(instance, self.connection_string, dialect=self.dialect)
        except Exception as e:
            logger.debug("disprove: DB write failed: %s", e)
            verdict = _verdict_for_db_write_exception(e)
            return self._make_result(
                verdict,
                t0,
                gen_result=gen_result,
                error_msg=f"DB write failed: {e}",
            )

        # Execute both queries
        q1 = execute_query(self.sql1, self.connection_string, self.dialect, self.timeout)
        q2 = execute_query(self.sql2, self.connection_string, self.dialect, self.timeout)
        verdict = compare_results(q1, q2, self.semantics)

        if verdict == Verdict.SYNTAX_ERROR:
            return self._make_result(
                verdict,
                t0,
                gen_result=gen_result,
                q1=q1,
                q2=q2,
                error_msg=q1.error_msg or q2.error_msg,
            )

        if verdict == Verdict.RUNTIME_ERROR:
            return self._make_result(
                verdict,
                t0,
                gen_result=gen_result,
                q1=q1,
                q2=q2,
                error_msg=q1.error_msg or q2.error_msg,
            )

        if verdict == Verdict.NEQ:
            return self._make_result(Verdict.NEQ, t0, gen_result=gen_result, q1=q1, q2=q2)

        if verdict == Verdict.UNKNOWN and (q1.is_error or q2.is_error):
            return self._make_result(
                Verdict.UNKNOWN,
                t0,
                gen_result=gen_result,
                q1=q1,
                q2=q2,
                error_msg=q1.error_msg or q2.error_msg,
            )

        # Both empty — can't prove equivalence
        if not q1.rows and not q2.rows:
            return self._make_result(
                Verdict.UNKNOWN, t0, gen_result=gen_result, q1=q1, q2=q2,
                error_msg="Both queries returned empty results - cannot prove equivalence",
            )

        return self._make_result(Verdict.EQ, t0, gen_result=gen_result, q1=q1, q2=q2)

    def _make_result(
        self,
        verdict: Verdict,
        t0: float,
        *,
        gen_result=None,
        q1: ExecutionResult | None = None,
        q2: ExecutionResult | None = None,
        generation_error_msg: str = "",
        error_msg: str = "",
    ) -> DisproveResult:
        generation = GenerationResult(
            success=gen_result is not None,
            rows_generated=gen_result.rows_generated if gen_result else 0,
            coverage=gen_result.coverage if gen_result else 0.0,
            error_msg=generation_error_msg,
            elapsed_time=time.time() - t0,
        )
        return DisproveResult(
            verdict=verdict,
            semantics=self.semantics,
            q1_result=q1 or ExecutionResult(query=self.sql1),
            q2_result=q2 or ExecutionResult(query=self.sql2),
            generation=generation,
            connection_string=self.connection_string,
            error_msg=error_msg,
        )

    @staticmethod
    def _pick_best(r1: DisproveResult, r2: DisproveResult) -> DisproveResult:
        """Pick the better of two non-NEQ results: EQ > syntax/schema errors > UNKNOWN."""
        priority = {Verdict.EQ: 0, Verdict.RUNTIME_ERROR: 1, Verdict.SYNTAX_ERROR: 1, Verdict.UNKNOWN: 2}
        p1 = priority.get(r1.verdict, 9)
        p2 = priority.get(r2.verdict, 9)
        return r1 if p1 <= p2 else r2

    def _clear_database(self) -> None:
        """Drop all tables so the next generate starts with a clean slate."""
        try:
            with DBManager().get_connection(self.connection_string, self.dialect) as conn:
                for table in reversed(conn.metadata.sorted_tables):
                    conn.drop_table(table.name)
        except Exception as e:
            logger.debug("disprove: cleanup failed: %s", e)


# ---------------------------------------------------------------------------
# SQL preprocessing
# ---------------------------------------------------------------------------

_LIMIT_RE = re.compile(r"LIMIT\s+\d+(?:\s+OFFSET\s+\d+)?$", re.IGNORECASE)


def _normalize_sql(sql: str) -> str:
    """Normalize SQL for textual comparison."""
    s = sql.strip().rstrip(";").strip()
    s = re.sub(r"\s+", " ", s)
    return s.lower()


def _verdict_for_generation_exception(exc: Exception) -> Verdict:
    if isinstance(exc, (SyntaxException, SchemaException)):
        return Verdict.SYNTAX_ERROR
    if isinstance(exc, ValueError):
        message = str(exc)
        if (
            message.startswith("Unresolved column:")
            or message.startswith("Ambiguous column:")
            or "could not be resolved" in message
        ):
            return Verdict.SYNTAX_ERROR
    return Verdict.UNKNOWN


def _verdict_for_db_write_exception(exc: Exception) -> Verdict:
    message = str(exc).lower()
    if "sql syntax" in message or "syntax error" in message:
        return Verdict.SYNTAX_ERROR
    return Verdict.RUNTIME_ERROR


def _preprocess_sql_pair(sql1: str, sql2: str, dialect: str = "sqlite") -> tuple[str, str]:
    """Strip matching LIMIT / ORDER BY tails so generation isn't artificially constrained."""
    try:
        expr1 = parse_one(sql1, dialect=dialect)
        expr2 = parse_one(sql2, dialect=dialect)
    except Exception:
        return sql1, sql2

    # Strip matching LIMIT + OFFSET
    limit1 = expr1.args.get("limit")
    limit2 = expr2.args.get("limit")
    offset1 = expr1.args.get("offset")
    offset2 = expr2.args.get("offset")
    if (
        limit1 is not None and limit2 is not None
        and limit1.sql() == limit2.sql()
        and (offset1 is None) == (offset2 is None)
        and (offset1 is None or offset1.sql() == offset2.sql())
    ):
        expr1.set("limit", None)
        expr1.set("offset", None)
        expr2.set("limit", None)
        expr2.set("offset", None)

    # Strip matching ORDER BY
    order1 = expr1.args.get("order")
    order2 = expr2.args.get("order")
    if order1 is not None and order2 is not None and order1.sql() == order2.sql():
        expr1.set("order", None)
        expr2.set("order", None)

    return expr1.sql(dialect=dialect), expr2.sql(dialect=dialect)


__all__ = ["Disprover"]
