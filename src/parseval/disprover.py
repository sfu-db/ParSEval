from __future__ import annotations
import threading
import queue
import time
from typing import List, Any, Tuple, Dict, Optional
from parseval.db_manager import DBManager
from concurrent.futures import ThreadPoolExecutor, as_completed
from parseval.speculative import SpeculativeGenerator
from parseval.data_generator import DataGenerator
from parseval.query import preprocess_sql
from parseval.instance import Instance
from parseval.states import RunResult, ExecutionResult
from parseval.configuration import DisproverConfig
import tempfile
import uuid


class Disprover:
    def __init__(
        self,
        q1,
        q2,
        schema,
        dialect="sqlite",
        config: DisproverConfig = None,
        exisiting_dbs: Optional[List] = None,
    ):
        self.q1 = q1
        self.q2 = q2
        self.schema = schema
        self.dialect = dialect
        self.config: DisproverConfig = config or DisproverConfig(
            host_or_path="/workspace", db_id="default"
        )
        self.num_generators = 2
        self.queue = queue.Queue()
        self.stop_event = threading.Event()
        self._lock = threading.Lock()

        self.instance = None
        self.preprocessed_q1 = None
        self.preprocessed_q2 = None
        self.counterexamples = []
        self.witness = []
        self.existing_dbs = exisiting_dbs or []
        self.last_q1_result = None
        self.last_q2_result = None
        self.last_checked_db = None

    def syntax_check(self):
        host_or_path = self.config.host_or_path
        db_id = f"{self.config.db_id}_syntax_check"
        port = self.config.port
        username = self.config.username
        password = self.config.password
        self.instance.to_db(
            host_or_path=host_or_path,
            database=db_id,
            port=port,
            username=username,
            password=password,
        )

        q1_result = self._execute_query(
            self.q1,
            host_or_path,
            db_id,
            port=port,
            username=username,
            password=password,
        )
        q2_result = self._execute_query(
            self.q2,
            host_or_path,
            db_id,
            port=port,
            username=username,
            password=password,
        )

        if q1_result.error_msg or q2_result.error_msg:
            return RunResult(
                q1=self.q1,
                q2=self.q2,
                host_or_path=self.config.host_or_path,
                db_id=f"{self.config.db_id}_syntax_check",
                q1_result=q1_result,
                q2_result=q2_result,
                state="SYN",
                set_semantic=self.config.set_semantic,
            )

    def exact_match_check(self):
        if self.q1.strip() == self.q2.strip():
            return RunResult(
                q1=self.q1,
                q2=self.q2,
                host_or_path="N/A",
                db_id="N/A",
                q1_result=None,
                q2_result=None,
                state="EQ",
                set_semantic=self.config.set_semantic,
            )

    def prepare(self):
        try:
            self.instance = Instance(
                ddls=self.schema,
                name=self.config.db_id,
                dialect=self.dialect,
            )
            self.preprocessed_q1 = preprocess_sql(
                self.q1, self.instance, dialect=self.dialect
            )
            self.preprocessed_q2 = preprocess_sql(
                self.q2, self.instance, dialect=self.dialect
            )
        except Exception as e:
            if self.instance is None:
                return RunResult(
                    q1=self.q1,
                    q2=self.q2,
                    host_or_path="N/A",
                    db_id="N/A",
                    q1_result=None,
                    q2_result=None,
                    state="UNKNOWN/NEQ/SYN",
                    set_semantic=self.config.set_semantic,
                    error_msg=str(e),
                )
            syntax_db_id = f"{self.config.db_id}_syntax_check"
            self.instance.to_db(
                host_or_path=self.config.host_or_path,
                database=syntax_db_id,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
            )
            q1_result = self._execute_query(
                self.q1,
                self.config.host_or_path,
                syntax_db_id,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
            )
            q2_result = self._execute_query(
                self.q2,
                self.config.host_or_path,
                syntax_db_id,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
            )
            self._record_results(
                q1_result,
                q2_result,
                host_or_path=self.config.host_or_path,
                db_id=syntax_db_id,
            )
            return self._build_result(
                state="SYN",
                q1_result=q1_result,
                q2_result=q2_result,
                host_or_path=self.config.host_or_path,
                db_id=syntax_db_id,
                error_msg=str(e),
            )

    def _execute_query(
        self, query: str, host_or_path, db_id, *, port, username, password
    ) -> ExecutionResult:
        if self.dialect == "sqlite":
            db_id = db_id if db_id.endswith(".sqlite") else db_id + ".sqlite"
        with DBManager().get_connection(
            host_or_path=host_or_path,
            database=db_id,
            dialect=self.dialect,
            port=port,
            username=username,
            password=password,
        ) as conn:
            results = None
            error = ""
            try:
                results = conn.execute(
                    query, fetch="all", timeout=self.config.query_timeout
                )
            except Exception as e:
                error = str(e)
            return ExecutionResult(
                host_or_path=host_or_path,
                db_id=db_id,
                query=query,
                rows=results if results is not None else [],
                elapsed_time=0.0,
                error_msg=error,
                dialect=self.dialect,
            )

    def _record_results(
        self,
        q1_result: ExecutionResult | None,
        q2_result: ExecutionResult | None,
        *,
        host_or_path: str | None = None,
        db_id: str | None = None,
    ) -> None:
        if q1_result is not None:
            self.last_q1_result = q1_result
        if q2_result is not None:
            self.last_q2_result = q2_result
        if host_or_path is None and q1_result is not None:
            host_or_path = q1_result.host_or_path
        if db_id is None and q1_result is not None:
            db_id = q1_result.db_id
        if host_or_path is not None and db_id is not None:
            self.last_checked_db = (host_or_path, db_id)

    def _generator(self, query, generator_id: str):
        if self.stop_event.is_set():
            return
        instance = Instance(
            ddls=self.schema,
            name=f"{self.config.db_id}_{uuid.uuid4().hex[:6]}_{generator_id}",
            dialect=self.dialect,
        )
        try:
            spec = SpeculativeGenerator(
                query, instance, generator_config=self.config.generator
            )
            spec.generate(
                early_stoper=self.early_stop,
                stop_event=self.stop_event,
                timeout=self.config.global_timeout,
            )
            if self.stop_event.is_set():
                return

            if getattr(self.config, "use_data_generator", True):
                generator = DataGenerator(
                    query,
                    instance,
                    verbose=False,
                    config=self.config.generator,
                )

                generator.generate(early_stop=self.early_stop, stop_event=self.stop_event)

        except Exception as e:
            import traceback

            traceback.print_exc()
            self.stop_event.set()

    def _generate_speculative(self, query, generator_id: str):
        if self.stop_event.is_set():
            return
        instance = Instance(
            ddls=self.schema,
            name=f"{self.config.db_id}_{generator_id}",
            dialect=self.dialect,
        )
        try:
            spec = SpeculativeGenerator(
                query, instance, generator_config=self.config.generator
            )
            spec.generate(
                db_queue=self.queue,
                stop_event=self.stop_event,
                host_or_path=self.config.host_or_path,
                username=self.config.username,
                password=self.config.password,
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.stop_event.set()

    def early_stop(self, instance: Instance) -> bool:
        if self.stop_event.is_set():
            return True
        database_name = instance.name
        database_name = (
            f"{database_name}.sqlite"
            if self.dialect.lower() == "sqlite"
            and not database_name.endswith(".sqlite")
            else database_name
        )
        instance.to_db(
            host_or_path=self.config.host_or_path,
            database=database_name,
            port=self.config.port,
            username=self.config.username,
            password=self.config.password,
            truncate_first=True,
        )

        self._checker(
            host_or_path=self.config.host_or_path,
            database=database_name,
            port=self.config.port,
            username=self.config.username,
            password=self.config.password,
        )
        return self.stop_event.is_set()

    def _checker(
        self,
        host_or_path,
        database,
        *,
        port: Optional[int] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ):
        if self.stop_event.is_set():
            return
        try:
            if self.stop_event.is_set():
                return
            q1_res = self._execute_query(
                self.q1,
                host_or_path=host_or_path,
                db_id=database,
                port=port,
                username=username,
                password=password,
            )
            if self.stop_event.is_set():
                return
            q2_res = self._execute_query(
                self.q2,
                host_or_path=host_or_path,
                db_id=database,
                port=port,
                username=username,
                password=password,
            )
            self._record_results(
                q1_res,
                q2_res,
                host_or_path=host_or_path,
                db_id=database,
            )

            if not q1_res.is_equivalent(q2_res, set_semantic=self.config.set_semantic):
                with self._lock:
                    self.counterexamples.append(
                        (
                            q1_res,
                            q2_res,
                        )
                    )
                    self.stop_event.set()
            elif q1_res.rows:
                with self._lock:
                    self.witness.append(
                        (
                            q1_res,
                            q2_res,
                        )
                    )
        finally:
            ...

    def _check_exisiting_dbs(self):
        max_workers = max(min(5, len(self.existing_dbs)), 1)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            for db_context in self.existing_dbs:
                host_or_path, database, port, username, password = db_context
                futures.append(
                    executor.submit(
                        self._checker,
                        host_or_path=host_or_path,
                        database=database,
                        port=port,
                        username=username,
                        password=password,
                    )
                )
            for future in as_completed(futures):
                if self.stop_event.is_set():
                    break

    def _fallback_execute_last_db(self) -> tuple[ExecutionResult | None, ExecutionResult | None]:
        db_context = self.last_checked_db
        if db_context is None and self.existing_dbs:
            host_or_path, database, port, username, password = self.existing_dbs[0]
        elif db_context is None:
            syntax_db_id = f"{self.config.db_id}_syntax_check"
            host_or_path = self.config.host_or_path
            database = syntax_db_id
            port = self.config.port
            username = self.config.username
            password = self.config.password
        else:
            host_or_path, database = db_context
            port = self.config.port
            username = self.config.username
            password = self.config.password

        q1_result = self.last_q1_result
        q2_result = self.last_q2_result
        if q1_result is None:
            q1_result = self._execute_query(
                self.q1,
                host_or_path=host_or_path,
                db_id=database,
                port=port,
                username=username,
                password=password,
            )
        if q2_result is None:
            q2_result = self._execute_query(
                self.q2,
                host_or_path=host_or_path,
                db_id=database,
                port=port,
                username=username,
                password=password,
            )
        self._record_results(
            q1_result,
            q2_result,
            host_or_path=host_or_path,
            db_id=database,
        )
        return q1_result, q2_result

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

    def run(self):
        for func in [self.exact_match_check, self.prepare, self.syntax_check]:
            result = func()
            if result is not None:
                return result

        self._check_exisiting_dbs()
        if self.counterexamples:
            (
                q1_res,
                q2_res,
            ) = self.counterexamples.pop()
            return self._build_result(
                state="NEQ",
                q1_result=q1_res,
                q2_result=q2_res,
                host_or_path=q1_res.host_or_path,
                db_id=q1_res.db_id,
            )

        total_workers = 2

        with ThreadPoolExecutor(max_workers=total_workers) as executor:
            generator_futures = []
            for index, query in enumerate([self.preprocessed_q1, self.preprocessed_q2]):
                generator_futures.append(
                    executor.submit(self._generator, query, f"s_{index}")
                )
                print(f"Generator s_{index} started.")

            start_time = time.time()
            try:
                while True:
                    # Check A: Did we find a counterexample?
                    if self.stop_event.is_set():
                        break
                    if all(f.done() for f in generator_futures):
                        break
                    if (time.time() - start_time) > self.config.global_timeout:
                        print(" Global timeout reached.")
                        self.stop_event.set()
                        break

                    time.sleep(0.1)  # Prevent CPU spinning
            except KeyboardInterrupt:
                self.stop_event.set()
            finally:
                self.stop_event.set()

        if self.counterexamples:
            q1_res, q2_res = self.counterexamples.pop()
            return self._build_result(
                state="NEQ",
                q1_result=q1_res,
                q2_result=q2_res,
                host_or_path=q1_res.host_or_path,
                db_id=q1_res.db_id,
            )
        elif self.witness:
            q1_res, q2_res = self.witness.pop()
            return self._build_result(
                state="EQ",
                q1_result=q1_res,
                q2_result=q2_res,
                host_or_path=q1_res.host_or_path,
                db_id=q1_res.db_id,
            )
        else:
            q1_res, q2_res = self._fallback_execute_last_db()
            host_or_path = q1_res.host_or_path if q1_res is not None else "N/A"
            db_id = q1_res.db_id if q1_res is not None else "N/A"
            return self._build_result(
                state="UNKNOWN",
                q1_result=q1_res,
                q2_result=q2_res,
                host_or_path=host_or_path,
                db_id=db_id,
            )
