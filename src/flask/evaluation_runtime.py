from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from flask import Flask
from sqlalchemy import select
from sqlglot import parse_one

from .enums import DBLevel, QueryLevel, RunStatus
from .models import (
    CounterExample,
    EvaluationJob,
    ModelRun,
    RelaxedEquivalence,
    RunCase,
    db,
    ensure_db_connection_info,
    utcnow,
)
from .query import (
    disprove_queries,
    execution_result_to_payload,
    sqlite_generation_root,
    witness_db_to_payload,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EvaluationRequest:
    project_id: int
    run_id: int
    gold: str
    pred: str
    schema: dict[str, Any] | list[Any] | str | None
    db_id: str
    dataset: str
    host_or_path: str
    dialect: str
    question_id: int | None = None
    question: str | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class EvaluationTaskPayload:
    evaluation_job_id: int


@dataclass(frozen=True)
class LevelResult:
    db_level: str
    query_level: str
    state: str
    witness_db: dict[str, Any] | None = None
    q1_result: dict[str, Any] | None = None
    q2_result: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvaluationResult:
    exact_match: bool
    state: str
    levels: dict[str, LevelResult] = field(default_factory=dict)


@dataclass
class EvaluationSettings:
    db_levels: list[str] = field(default_factory=list)
    query_levels: list[str] = field(default_factory=list)


class EvaluationEngine:

    def evaluate(self, job: EvaluationJob) -> EvaluationResult:
        run_case = job.run_case
        project_settings = job.run.project.settings_json or {}

        print(
            f"Evaluating job {job.id} for run case {run_case.id} with project settings: {project_settings}"
        )

        settings = self._build_settings(project_settings)
        try:
            normalized_pred = _canonicalize_sql(
                run_case.pred, run_case.dialect or "sqlite"
            )
            normalized_gold = _canonicalize_sql(
                run_case.gold, run_case.dialect or "sqlite"
            )
            exact_match = normalized_pred == normalized_gold
            levels: dict[str, LevelResult] = {}
            connection_info = run_case.db_connection_info
            dialect = run_case.dialect or "sqlite"
            generation_host = (
                sqlite_generation_root(run_case.host_or_path or ".")
                if dialect == "sqlite"
                else (
                    connection_info.host if connection_info else run_case.host_or_path
                )
            )
            for db_level in settings.db_levels:
                for query_level in settings.query_levels:
                    key = self._result_key(db_level, query_level)
                    print(
                        f"Evaluating job {job.id} at db_level={db_level} query_level={query_level}"
                    )
                    logger.info(
                        "Evaluating job %s at db_level=%s query_level=%s",
                        job.id,
                        db_level,
                        query_level,
                    )
                    if exact_match:
                        levels[key] = self._equivalent_level_result(
                            db_level=db_level,
                            query_level=query_level,
                            run_case=run_case,
                            dialect=dialect,
                        )
                        continue

                    run_result = disprove_queries(
                        dataset=run_case.dataset,
                        q1=run_case.gold,
                        q2=run_case.pred,
                        schema=run_case.schema_json,
                        dialect=dialect,
                        db_level=db_level,
                        query_level=query_level,
                        project_settings=project_settings,
                        host_or_path=generation_host,
                        db_id=run_case.db_id,
                        port=connection_info.port if connection_info else None,
                        username=connection_info.username if connection_info else None,
                        password=connection_info.password if connection_info else None,
                    )
                    levels[key] = self._level_result_from_run_result(
                        db_level=db_level,
                        query_level=query_level,
                        run_case=run_case,
                        run_result=run_result,
                        query_timeout=_query_timeout(project_settings),
                    )
            return EvaluationResult(
                exact_match=exact_match, state="success", levels=levels
            )
        except Exception:
            logger.exception("Evaluation failed for job %s", job.id)
            return EvaluationResult(exact_match=False, state="failed", levels={})

    def _build_settings(self, project_settings: dict[str, Any]) -> EvaluationSettings:
        db_levels = (
            project_settings.get("dbLevels")
            or project_settings.get("db_levels")
            or [level.value for level in DBLevel.ordered()]
        )
        query_levels = (
            project_settings.get("queryLevels")
            or project_settings.get("query_levels")
            or [level.value for level in QueryLevel.ordered()]
        )
        return EvaluationSettings(
            db_levels=list(db_levels), query_levels=list(query_levels)
        )

    def _result_key(self, db_level: str, query_level: str) -> str:
        return f"db={db_level}|query={query_level}"

    def _equivalent_level_result(
        self,
        *,
        db_level: str,
        query_level: str,
        run_case: RunCase,
        dialect: str,
    ) -> LevelResult:
        q1_result = {
            "query": run_case.gold,
            "host_or_path": run_case.host_or_path,
            "db_id": run_case.db_id,
            "question_id": run_case.question_id,
            "dataset": run_case.dataset,
            "dialect": dialect,
            "elapsed_time": 0,
            "rows": [],
            "columns": [],
            "error_msg": "",
        }
        q2_result = {
            "query": run_case.pred,
            "host_or_path": run_case.host_or_path,
            "db_id": run_case.db_id,
            "question_id": run_case.question_id,
            "dataset": run_case.dataset,
            "dialect": dialect,
            "elapsed_time": 0,
            "rows": [],
            "columns": [],
            "error_msg": "",
        }
        return LevelResult(
            db_level=db_level,
            query_level=query_level,
            state="equivalent",
            witness_db=None,
            q1_result=q1_result,
            q2_result=q2_result,
        )

    def _level_result_from_run_result(
        self,
        *,
        db_level: str,
        query_level: str,
        run_case: RunCase,
        run_result: Any,
        query_timeout: int,
    ) -> LevelResult:
        state = _map_run_state(run_result.state)
        q1_result = execution_result_to_payload(
            run_result.q1_result, query_timeout=query_timeout
        )
        q2_result = execution_result_to_payload(
            run_result.q2_result, query_timeout=query_timeout
        )
        if q1_result is not None:
            q1_result["question_id"] = run_case.question_id
            q1_result["dataset"] = run_case.dataset
        if q2_result is not None:
            q2_result["question_id"] = run_case.question_id
            q2_result["dataset"] = run_case.dataset

        witness_db = None
        if state != "error":
            witness_db = witness_db_to_payload(
                host_or_path=run_result.host_or_path,
                db_id=run_result.db_id,
                dialect=(
                    run_result.q1_result.dialect if run_result.q1_result else "sqlite"
                ),
            )

        return LevelResult(
            db_level=db_level,
            query_level=query_level,
            state=state,
            witness_db=witness_db,
            q1_result=q1_result,
            q2_result=q2_result,
        )


class DatabaseResultSink:
    def __init__(self, app: Flask, write_artifacts: bool = True) -> None:
        self._app = app
        self._write_artifacts = write_artifacts

    def persist(self, job: EvaluationJob, result: EvaluationResult) -> None:
        summary = self._summarize_result(result)
        job.result_json = {
            "exactMatch": summary["exact_match"],
            "state": summary["state"],
            "resultsBySettings": summary["results_by_settings"],
        }
        if self._write_artifacts:
            artifact_path = self._write_artifact(job, result, summary)
            job.artifact_path = artifact_path
            job.result_json = {**job.result_json, "artifactPath": artifact_path}
        self._upsert_relaxed_equivalence(job, result, summary["equivalence_state"])

    def _write_artifact(
        self, job: EvaluationJob, result: EvaluationResult, summary: dict[str, Any]
    ) -> str:
        run_case = job.run_case
        static_root = Path(self._app.root_path) / "static" / "results"
        target_dir = static_root / f"project-{job.run.project_id}" / f"run-{job.run_id}"
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / f"job-{job.id}.json"
        file_path.write_text(
            json.dumps(
                {
                    "job": job.id,
                    "projectId": job.run.project_id,
                    "runId": job.run_id,
                    "state": summary["state"],
                    "exactMatch": summary["exact_match"],
                    "runCase": {
                        "questionId": run_case.question_id,
                        "dbId": run_case.db_id,
                        "dataset": run_case.dataset,
                        "hostOrPath": run_case.host_or_path,
                        "gold": run_case.gold,
                        "pred": run_case.pred,
                    },
                    "resultsBySettings": summary["results_by_settings"],
                    "levels": [
                        {
                            "settingKey": setting_key,
                            "dbLevel": level.db_level,
                            "queryLevel": level.query_level,
                            "state": level.state,
                            "witnessDb": level.witness_db,
                            "q1Result": level.q1_result,
                            "q2Result": level.q2_result,
                        }
                        for setting_key, level in result.levels.items()
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return "/" + file_path.relative_to(Path(self._app.root_path)).as_posix()

    def _upsert_relaxed_equivalence(
        self, job: EvaluationJob, result: EvaluationResult, overall_state: str
    ) -> None:
        record = db.session.scalar(
            select(RelaxedEquivalence).where(
                RelaxedEquivalence.evaluation_job_id == job.id
            )
        )
        if record is None:
            record = RelaxedEquivalence(evaluation_job_id=job.id, state=overall_state)
            db.session.add(record)
            db.session.flush()
        else:
            record.state = overall_state

        for item in list(record.counterexamples):
            db.session.delete(item)

        for level in result.levels.values():
            db.session.add(
                CounterExample(
                    equivalence_id=record.id,
                    db_level=level.db_level,
                    query_level=level.query_level,
                    state=level.state,
                    witness_db_json=level.witness_db,
                    q1_result_json=level.q1_result,
                    q2_result_json=level.q2_result,
                )
            )

    def _summarize_result(self, result: EvaluationResult) -> dict[str, Any]:
        level_states = [level.state for level in result.levels.values()]
        if any(state == "witnessed" for state in level_states):
            equivalence_state = "witnessed"
        elif level_states and all(state == "equivalent" for state in level_states):
            equivalence_state = "equivalent"
        elif result.state == "failed":
            equivalence_state = "failed"
        else:
            equivalence_state = "pending"
        results_by_settings = {
            key: {
                "state": level.state,
                "dbLevel": level.db_level,
                "queryLevel": level.query_level,
                "witnessDb": level.witness_db,
                "q1Result": level.q1_result,
                "q2Result": level.q2_result,
            }
            for key, level in result.levels.items()
        }
        return {
            "exact_match": result.exact_match,
            "state": result.state,
            "equivalence_state": equivalence_state,
            "results_by_settings": results_by_settings,
        }


class EvaluationSubmissionService:
    def __init__(self, runtime: "EvaluationRuntime") -> None:
        self._runtime = runtime

    def submit_runcase(self, run_case: RunCase) -> EvaluationJob:
        job = EvaluationJob(
            run_id=run_case.run_id,
            run_case_id=run_case.id,
            status=RunStatus.PENDING.value,
        )
        db.session.add(job)
        db.session.flush()
        recompute_model_run_status(run_case.run_id)
        db.session.commit()
        self._runtime.submit(EvaluationTaskPayload(evaluation_job_id=job.id))
        logger.info(f"Submitted evaluation job {job.id} for run case {run_case.id}")
        return job

    def submit(
        self, request_dict: dict[str, Any], project_id: int, run_id: int
    ) -> EvaluationJob:
        request = self._build_request(project_id, run_id, request_dict)
        run = db.session.get(ModelRun, run_id)
        if run is None or run.project_id != project_id:
            raise LookupError(f"ModelRun {run_id} not found")
        if run.dataset != request.dataset:
            raise ValueError("dataset must match the target model run")

        run_case = self._resolve_run_case(request)
        job = EvaluationJob(
            run_id=run_id, run_case_id=run_case.id, status=RunStatus.PENDING.value
        )
        db.session.add(job)
        db.session.flush()
        recompute_model_run_status(run_id)
        db.session.commit()
        self._runtime.submit(EvaluationTaskPayload(evaluation_job_id=job.id))
        return job

    def _resolve_run_case(self, request: EvaluationRequest) -> RunCase:
        if request.question_id is not None:
            existing = db.session.scalar(
                select(RunCase).where(
                    RunCase.run_id == request.run_id,
                    RunCase.question_id == request.question_id,
                )
            )
        else:
            existing = db.session.scalar(
                select(RunCase).where(
                    RunCase.run_id == request.run_id,
                    RunCase.question_id.is_(None),
                    RunCase.db_id == request.db_id,
                    RunCase.dataset == request.dataset,
                    RunCase.gold == request.gold,
                    RunCase.pred == request.pred,
                )
            )
        if existing is not None:
            connection = ensure_db_connection_info(
                root_path=self._runtime._app.root_path,
                dataset=request.dataset,
                db_id=request.db_id,
                dialect=request.dialect,
                host_or_path=request.host_or_path,
            )
            existing.db_connection_id = connection.id
            existing.host_or_path_legacy = connection.host_or_path
            existing.dialect = request.dialect
            existing.schema_json = request.schema
            existing.question = request.question
            existing.evidence = request.evidence
            return existing

        connection = ensure_db_connection_info(
            root_path=self._runtime._app.root_path,
            dataset=request.dataset,
            db_id=request.db_id,
            dialect=request.dialect,
            host_or_path=request.host_or_path,
        )
        run_case = RunCase(
            run_id=request.run_id,
            db_connection_id=connection.id,
            question_id=request.question_id,
            db_id=request.db_id,
            dataset=request.dataset,
            host_or_path_legacy=connection.host_or_path,
            dialect=request.dialect,
            schema_json=request.schema,
            question=request.question,
            evidence=request.evidence,
            prompt=None,
            gold=request.gold,
            pred=request.pred,
            source="queued",
        )
        db.session.add(run_case)
        db.session.flush()
        return run_case

    def _build_request(
        self, project_id: int, run_id: int, payload: dict[str, Any]
    ) -> EvaluationRequest:
        normalized_payload = {
            "gold": payload.get("gold", payload.get("referenceSql")),
            "pred": payload.get("pred", payload.get("sqlQuery")),
            "schema": payload.get("schema"),
            "db_id": payload.get("db_id", payload.get("dbId")),
            "dataset": payload.get("dataset"),
            "host_or_path": payload.get("host_or_path", payload.get("hostOrPath")),
            "dialect": payload.get("dialect"),
            "question_id": payload.get("question_id", payload.get("questionId")),
            "question": payload.get("question"),
            "evidence": payload.get("evidence"),
        }
        return EvaluationRequest(
            project_id=project_id,
            run_id=run_id,
            gold=_require_string(normalized_payload, "gold"),
            pred=_require_string(normalized_payload, "pred"),
            schema=normalized_payload.get("schema"),
            db_id=_require_string(normalized_payload, "db_id"),
            dataset=_require_string(normalized_payload, "dataset"),
            host_or_path=_optional_string(normalized_payload, "host_or_path"),
            dialect=_optional_string(normalized_payload, "dialect", "sqlite"),
            question_id=_optional_int(normalized_payload, "question_id"),
            question=_optional_nullable_string(normalized_payload, "question"),
            evidence=_optional_nullable_string(normalized_payload, "evidence"),
        )


class EvaluationConsumer:
    def __init__(
        self, app: Flask, engine: EvaluationEngine, sink: DatabaseResultSink
    ) -> None:
        self._app = app
        self._engine = engine
        self._sink = sink

    def set_engine(self, engine: EvaluationEngine) -> None:
        self._engine = engine

    def process_task(self, payload: EvaluationTaskPayload) -> None:
        with self._app.app_context():
            job = db.session.get(EvaluationJob, payload.evaluation_job_id)
            # EvaluationTaskPayload(evaluation_job_id=job.id)
            logger.info(f"Processing evaluation job {payload.evaluation_job_id}, {job}")
            if job is None or job.status == RunStatus.DONE.value:
                return
            logger.info(
                f"Starting evaluation job {job.id} for run case {job.run_case_id}"
            )
            job.status = RunStatus.RUNNING.value
            if job.started_at is None:
                job.started_at = utcnow()
            job.error_message = None
            recompute_model_run_status(job.run_id)
            db.session.commit()

            try:
                result = self._engine.evaluate(job)
                self._sink.persist(job, result)
                job.status = RunStatus.DONE.value
                job.finished_at = utcnow()
                job.error_message = None
                recompute_model_run_status(job.run_id)
                db.session.commit()
            except Exception as exc:
                logger.error(f"Evaluation job {job.id} failed with exception: {exc}")
                logger.exception("Evaluation job %s failed", payload.evaluation_job_id)
                db.session.rollback()
                failed_job = db.session.get(EvaluationJob, payload.evaluation_job_id)
                if failed_job is None:
                    return
                failed_job.status = RunStatus.ERROR.value
                failed_job.finished_at = utcnow()
                failed_job.error_message = str(exc)
                recompute_model_run_status(failed_job.run_id)
                db.session.commit()


class EvaluationRuntime:
    def __init__(
        self,
        app: Flask,
        num_workers: int = 1,
        queue_maxsize: int = 0,
        write_artifacts: bool = True,
    ) -> None:
        self._app = app
        self._queue: queue.Queue[EvaluationTaskPayload] = queue.Queue(
            maxsize=queue_maxsize
        )
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._started = False
        self._submitted_count = 0
        self._lock = threading.Lock()

        self._engine = EvaluationEngine()
        self._sink = DatabaseResultSink(app, write_artifacts=write_artifacts)
        self._consumer = EvaluationConsumer(app, self._engine, self._sink)
        self._num_workers = num_workers
        self.submission_service = EvaluationSubmissionService(self)

    @property
    def submitted_count(self) -> int:
        with self._lock:
            return self._submitted_count

    def replace_engine(self, engine: EvaluationEngine) -> None:
        self._engine = engine
        self._consumer.set_engine(engine)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        for index in range(self._num_workers):
            thread = threading.Thread(
                target=self._worker_loop, name=f"evaluation-worker-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        atexit.register(self.stop)

    def submit(self, payload: EvaluationTaskPayload) -> None:
        if not self._started:
            raise RuntimeError("EvaluationRuntime not started")
        with self._lock:
            self._submitted_count += 1
        self._queue.put(payload)

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=timeout)

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                payload = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._consumer.process_task(payload)
            finally:
                self._queue.task_done()


def recompute_model_run_status(run_id: int) -> None:
    run = db.session.get(ModelRun, run_id)
    if run is None:
        return
    statuses = list(
        db.session.scalars(
            select(EvaluationJob.status).where(EvaluationJob.run_id == run_id)
        )
    )
    if not statuses:
        return
    if RunStatus.ERROR.value in statuses:
        run.status = RunStatus.ERROR.value
    elif RunStatus.RUNNING.value in statuses:
        run.status = RunStatus.RUNNING.value
    elif RunStatus.PENDING.value in statuses:
        run.status = RunStatus.PENDING.value
    elif all(status == RunStatus.DONE.value for status in statuses):
        run.status = RunStatus.DONE.value


def build_evaluation_runtime(app: Flask) -> EvaluationRuntime:
    runtime = EvaluationRuntime(
        app,
        num_workers=int(app.config.get("EVALUATION_WORKERS", 1)),
        queue_maxsize=int(app.config.get("EVALUATION_QUEUE_MAXSIZE", 0)),
        write_artifacts=bool(app.config.get("EVALUATION_WRITE_ARTIFACTS", True)),
    )
    runtime.start()
    return runtime


def _require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def _optional_string(data: dict[str, Any], key: str, default: str = "") -> str:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_nullable_string(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_int(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _canonicalize_sql(sql: str, dialect: str = "sqlite") -> str:
    parsed = parse_one(sql, read=dialect)
    return " ".join(parsed.sql(dialect=dialect).split()).lower()


def _query_timeout(project_settings: dict[str, Any]) -> int:
    value = project_settings.get("query_timeout", 10)
    if isinstance(value, bool):
        return 10
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 10
    return 10


def _map_run_state(state: str) -> str:
    if state == "NEQ":
        return "witnessed"
    if state == "EQ":
        return "equivalent"
    return "error"
