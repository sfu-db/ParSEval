from __future__ import annotations

import logging
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Optional, Sequence

from parseval.configuration import DisproverConfig
from parseval.data_generator import DataGenerator
from parseval.db_manager import DBManager
from parseval.instance import Instance
from parseval.query import preprocess_sql
from parseval.speculative import SpeculativeGenerator
from parseval.states import ExecutionResult, RunResult

logger = logging.getLogger("parseval.eq")


@dataclass(frozen=True)
class DatabaseContext:
    host_or_path: str
    database: str
    port: int | None = None
    username: str | None = None
    password: str | None = None

    @classmethod
    def from_legacy_tuple(cls, value: Sequence[object]) -> "DatabaseContext":
        host_or_path, database, port, username, password = value
        return cls(
            host_or_path=str(host_or_path),
            database=str(database),
            port=port if port is None else int(port),
            username=username if username is None else str(username),
            password=password if password is None else str(password),
        )


@dataclass(frozen=True)
class ResultPair:
    q1: ExecutionResult
    q2: ExecutionResult


class Disprover:
    def __init__(
        self,
        q1: str,
        q2: str,
        schema: str,
        dialect: str = "sqlite",
        config: DisproverConfig | None = None,
        existing_dbs: Optional[Sequence[Sequence[object]]] = None,
    ) -> None:
        self.q1 = q1
        self.q2 = q2
        self.schema = schema
        self.dialect = dialect
        self.config: DisproverConfig = config or DisproverConfig(
            host_or_path=tempfile.mkdtemp(prefix="parseval-disprover-"),
            db_id="default",
        )

        self.stop_event = threading.Event()
        self._lock = threading.Lock()

        self.instance: Instance | None = None
        self.preprocessed_q1 = None
        self.preprocessed_q2 = None

        self.existing_dbs = [
            DatabaseContext.from_legacy_tuple(db_context)
            for db_context in (existing_dbs or [])
        ]
        self.last_checked_db: DatabaseContext | None = None
        self.last_result_pair: ResultPair | None = None
        self.counterexample: ResultPair | None = None
        self.witness: ResultPair | None = None

    def run(self) -> RunResult:
        exact_match = self._check_exact_match()
        if exact_match is not None:
            return exact_match

        prepared = self._prepare_queries()
        if prepared is not None:
            return prepared

        syntax_result = self._check_syntax()
        if syntax_result is not None:
            return syntax_result

        self._evaluate_existing_dbs()
        terminal_result = self._finalize_if_complete()
        if terminal_result is not None:
            return terminal_result

        self._run_generators()
        return self._finalize_result()

    def _check_exact_match(self) -> RunResult | None:
        if self.q1.strip() != self.q2.strip():
            return None
        return self._build_result(
            state="EQ",
            q1_result=None,
            q2_result=None,
            host_or_path="N/A",
            db_id="N/A",
        )

    def _prepare_queries(self) -> RunResult | None:
        try:
            self.instance = Instance(
                ddls=self.schema,
                name=self.config.db_id,
                dialect=self.dialect,
            )
            self.preprocessed_q1 = preprocess_sql(
                self.q1,
                self.instance,
                dialect=self.dialect,
            )
            self.preprocessed_q2 = preprocess_sql(
                self.q2,
                self.instance,
                dialect=self.dialect,
            )
            return None
        except Exception as exc:
            if self.instance is None:
                return self._build_result(
                    state="UNKNOWN/NEQ/SYN",
                    q1_result=None,
                    q2_result=None,
                    host_or_path="N/A",
                    db_id="N/A",
                    error_msg=str(exc),
                )

            syntax_context = self._syntax_db_context()
            self.instance.to_db(
                host_or_path=syntax_context.host_or_path,
                database=syntax_context.database,
                port=syntax_context.port,
                username=syntax_context.username,
                password=syntax_context.password,
            )
            pair = self._execute_pair(syntax_context)
            return self._build_result(
                state="SYN",
                q1_result=pair.q1,
                q2_result=pair.q2,
                host_or_path=syntax_context.host_or_path,
                db_id=self._normalize_database_name(syntax_context.database),
                error_msg=str(exc),
            )

    def _check_syntax(self) -> RunResult | None:
        if self.instance is None:
            return None

        syntax_context = self._syntax_db_context()
        self.instance.to_db(
            host_or_path=syntax_context.host_or_path,
            database=syntax_context.database,
            port=syntax_context.port,
            username=syntax_context.username,
            password=syntax_context.password,
        )

        pair = self._execute_pair(syntax_context)
        if pair.q1.error_msg or pair.q2.error_msg:
            return self._build_result(
                state="SYN",
                q1_result=pair.q1,
                q2_result=pair.q2,
                host_or_path=syntax_context.host_or_path,
                db_id=self._normalize_database_name(syntax_context.database),
            )
        return None

    def _execute_query(self, query: str, context: DatabaseContext) -> ExecutionResult:
        database_name = self._normalize_database_name(context.database)
        with DBManager().get_connection(
            host_or_path=context.host_or_path,
            database=database_name,
            dialect=self.dialect,
            port=context.port,
            username=context.username,
            password=context.password,
        ) as conn:
            results = None
            error = ""
            try:
                results = conn.execute(
                    query,
                    fetch="all",
                    timeout=self.config.query_timeout,
                )
            except Exception as exc:
                error = str(exc)
                logger.debug(
                    "Query execution failed against %s: %s",
                    database_name,
                    error,
                    exc_info=True,
                )
            return ExecutionResult(
                host_or_path=context.host_or_path,
                db_id=database_name,
                query=query,
                rows=results if results is not None else [],
                elapsed_time=0.0,
                error_msg=error,
                dialect=self.dialect,
            )

    def _execute_pair(self, context: DatabaseContext) -> ResultPair:
        pair = ResultPair(
            q1=self._execute_query(self.q1, context),
            q2=self._execute_query(self.q2, context),
        )
        self._remember_results(pair, context)
        return pair

    def _remember_results(self, pair: ResultPair, context: DatabaseContext) -> None:
        self.last_checked_db = context
        self.last_result_pair = pair

    def _evaluate_context(self, context: DatabaseContext) -> None:
        if self.stop_event.is_set():
            return

        pair = self._execute_pair(context)
        if not pair.q1.is_equivalent(pair.q2, set_semantic=self.config.set_semantic):
            self._remember_counterexample(pair)
            return

        if pair.q1.rows:
            self._remember_witness(pair)

    def _evaluate_existing_dbs(self) -> None:
        if not self.existing_dbs or self.stop_event.is_set():
            return

        max_workers = max(min(5, len(self.existing_dbs)), 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._evaluate_context, context)
                for context in self.existing_dbs
            ]
            for future in as_completed(futures):
                future.result()
                if self.stop_event.is_set():
                    break

    def _remember_counterexample(self, pair: ResultPair) -> None:
        with self._lock:
            if self.counterexample is None:
                self.counterexample = pair
                self.stop_event.set()

    def _remember_witness(self, pair: ResultPair) -> None:
        with self._lock:
            if self.witness is None:
                self.witness = pair

    def _run_generators(self) -> None:
        start_time = time.monotonic()
        for index, query in enumerate((self.preprocessed_q1, self.preprocessed_q2)):
            if self.stop_event.is_set():
                break
            if (time.monotonic() - start_time) > self.config.global_timeout:
                logger.warning(
                    "Global timeout reached while generating counterexamples"
                )
                self.stop_event.set()
                break
            self._generator(query, f"s_{index}")

    def _generator(self, query, generator_id: str) -> None:
        if self.stop_event.is_set():
            return

        instance = Instance(
            ddls=self.schema,
            name=f"{self.config.db_id}_{generator_id}",
            dialect=self.dialect,
        )
        try:
            speculative = SpeculativeGenerator(
                query,
                instance,
                generator_config=self.config.generator,
            )
            speculative.generate(
                early_stoper=self.early_stop,
                stop_event=self.stop_event,
                timeout=self.config.global_timeout,
            )
            if self.stop_event.is_set():
                return

            if self.config.use_data_generator:
                generator = DataGenerator(
                    query,
                    instance,
                    verbose=False,
                    config=self.config.generator,
                )
                generator.generate(
                    early_stop=self.early_stop,
                    stop_event=self.stop_event,
                )
        except Exception:
            logger.exception("Generator %s failed", generator_id)
            self.stop_event.set()

    def early_stop(self, instance: Instance) -> bool:
        if self.stop_event.is_set():
            return True

        context = self._instance_db_context(instance.name)
        instance.to_db(
            host_or_path=context.host_or_path,
            database=context.database,
            port=context.port,
            username=context.username,
            password=context.password,
            truncate_first=True,
        )
        self._evaluate_context(context)
        return self.stop_event.is_set()

    def _fallback_execute_last_db(self) -> ResultPair:
        context = self.last_checked_db
        if context is None and self.existing_dbs:
            context = self.existing_dbs[0]
        elif context is None:
            context = self._syntax_db_context()

        if self.last_result_pair is not None and self.last_checked_db == context:
            return self.last_result_pair
        return self._execute_pair(context)

    def _finalize_if_complete(self) -> RunResult | None:
        if self.counterexample is not None:
            return self._build_pair_result("NEQ", self.counterexample)
        return None

    def _finalize_result(self) -> RunResult:
        if self.counterexample is not None:
            return self._build_pair_result("NEQ", self.counterexample)

        if self.witness is not None:
            return self._build_pair_result("EQ", self.witness)

        pair = self._fallback_execute_last_db()
        return self._build_pair_result("UNKNOWN", pair)

    def _build_pair_result(self, state: str, pair: ResultPair) -> RunResult:
        return self._build_result(
            state=state,
            q1_result=pair.q1,
            q2_result=pair.q2,
            host_or_path=pair.q1.host_or_path,
            db_id=pair.q1.db_id,
        )

    def _build_result(
        self,
        *,
        state: str,
        q1_result: ExecutionResult | None,
        q2_result: ExecutionResult | None,
        host_or_path: str,
        db_id: str,
        error_msg: str = "",
    ) -> RunResult:
        return RunResult(
            q1=self.q1,
            q2=self.q2,
            host_or_path=host_or_path,
            db_id=db_id,
            q1_result=q1_result,
            q2_result=q2_result,
            state=state,
            set_semantic=self.config.set_semantic,
            error_msg=error_msg,
        )

    def _default_db_context(self, database: str) -> DatabaseContext:
        return DatabaseContext(
            host_or_path=self.config.host_or_path,
            database=database,
            port=self.config.port,
            username=self.config.username,
            password=self.config.password,
        )

    def _syntax_db_context(self) -> DatabaseContext:
        return self._default_db_context(f"{self.config.db_id}_syntax_check")

    def _instance_db_context(self, database: str) -> DatabaseContext:
        return self._default_db_context(self._normalize_database_name(database))

    def _normalize_database_name(self, database: str) -> str:
        if self.dialect.lower() == "sqlite" and not database.endswith(".sqlite"):
            return f"{database}.sqlite"
        return database
