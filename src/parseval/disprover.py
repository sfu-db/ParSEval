from __future__ import annotations
import threading
import queue
import time
import uuid
from abc import ABC, abstractmethod
from typing import List, Any, Tuple, Dict
from dataclasses import dataclass, field
from parseval.db_manager import DBManager
from concurrent.futures import ThreadPoolExecutor
from parseval.speculative import SpeculativeGenerator
from parseval.query import preprocess_sql
from parseval.instance import Instance
from parseval.states import RunResult, ExecutionResult
from parseval.configuration import DisproverConfig


class Disprover:
    def __init__(
        self,
        q1,
        q2,
        schema,
        dialect="sqlite",
        config: DisproverConfig = None,
        exisiting_db_contexts=None,
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

        self.instance = Instance(
            ddls=self.schema,
            name=self.config.db_id,
            dialect=self.dialect,
        )
        self.preprocessed_q1 = None
        self.preprocessed_q2 = None
        self.counterexample_results = None

    def syntax_check(self):
        host_or_path = self.config.host_or_path
        db_id = f"{self.config.db_id}_syntax_check"
        self.instance.to_db(host_or_path=host_or_path, database=db_id)

        q1_result = self._execute_query(self.q1, host_or_path, db_id)
        q2_result = self._execute_query(self.q2, host_or_path, db_id)

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
            self.preprocessed_q1 = preprocess_sql(
                self.q1, self.instance, dialect=self.dialect
            )
            self.preprocessed_q2 = preprocess_sql(
                self.q2, self.instance, dialect=self.dialect
            )
        except Exception as e:
            return RunResult(
                q1=self.q1,
                q2=self.q2,
                host_or_path="N/A",
                db_id="N/A",
                q1_result=None,
                q2_result=None,
                state="UNKNOWN",
                set_semantic=self.config.set_semantic,
            )

    def _execute_query(self, query: str, host_or_path, db_id) -> ExecutionResult:
        if self.dialect == "sqlite":
            db_id = db_id if db_id.endswith(".sqlite") else db_id + ".sqlite"
        with DBManager().get_connection(
            host_or_path=host_or_path, database=db_id, dialect=self.dialect
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

    def _generator(self, generator_id: str):
        while not self.stop_event.is_set():
            db_context = self.generator_strategy.generate(self.schema)
            try:
                self.db_queue.put(db_context, timeout=0.5)
            except queue.Full:
                continue

    def _generat_speculative(self, query, generator_id: str):
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
            )
        except Exception as e:
            import traceback

            traceback.print_exc()
            self.stop_event.set()

    def _checker(self, worker_id: str):
        while not self.stop_event.is_set() or not self.queue.empty():
            try:
                db_context = self.queue.get(timeout=1)  # Wait for a test case
            except queue.Empty:
                if self.stop_event.is_set():
                    break
                continue

            q1_result = self._execute_query(self.q1, **db_context)
            q2_result = self._execute_query(self.q2, **db_context)

            print(
                f'worker checking on {db_context["host_or_path"]}/{db_context["db_id"]} with q1_result: {q1_result.rows} and q2_result: {q2_result.rows}'
            )

            if not q1_result.is_equivalent(
                q2_result, set_semantic=self.config.set_semantic
            ):
                self.counterexample_results = (q1_result, q2_result)
                self.stop_event.set()
            self.queue.task_done()

    def run(self):
        for func in [self.syntax_check, self.exact_match_check, self.prepare]:
            result = func()
            if result is not None:
                return result
        total_workers = self.num_generators + 1
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = []
            futures.append(executor.submit(self._checker, 0))
            print("Checker started.")

            for index, query in enumerate([self.preprocessed_q1, self.preprocessed_q2]):
                futures.append(
                    executor.submit(self._generat_speculative, query, f"s_{index}")
                )
                print(f"Generator s_{index} started.")

            try:
                timed_out = not self.stop_event.wait(timeout=self.config.global_timeout)
                if timed_out:
                    self.stop_event.set()
                else:
                    print("\n✅ Pipeline stopped early (Counterexample found).")
            except KeyboardInterrupt:
                self.stop_event.set()

        if self.counterexample_results:
            q1_res, q2_res = self.counterexample_results
            return RunResult(
                q1=self.q1,
                q2=self.q2,
                host_or_path=q1_res.host_or_path,
                db_id=q1_res.db_id,
                q1_result=q1_res,
                q2_result=q2_res,
                state="NEQ",
                set_semantic=self.config.set_semantic,
            )
        else:
            return RunResult(
                q1=self.q1,
                q2=self.q2,
                host_or_path="N/A",
                db_id="N/A",
                q1_result=ExecutionResult(
                    host_or_path="N/A",
                    db_id="N/A",
                    query=self.q1,
                    rows=[],
                    elapsed_time=0.0,
                    error_msg="Timeout/Max Tries reached.",
                    dialect=self.dialect,
                ),
                q2_result=ExecutionResult(
                    host_or_path="N/A",
                    db_id="N/A",
                    query=self.q2,
                    rows=[],
                    elapsed_time=0.0,
                    error_msg="Timeout/Max Tries reached.",
                    dialect=self.dialect,
                ),
                state="EQ",
                set_semantic=self.config.set_semantic,
            )
