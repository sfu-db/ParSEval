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
    DatasetAsset,
    DatasetVariant,
    EvaluationCacheEntry,
    EvaluationJob,
    ModelRun,
    RelaxedEquivalence,
    RunCase,
    db,
    ensure_db_connection_info,
    fingerprint_text_payload,
    utcnow,
)
from .query import (
    disprove_queries,
    execution_result_to_payload,
    witness_db_to_payload,
)
from .storage import (
    ensure_dataset_asset_storage,
    ensure_dataset_variant_storage,
    ensure_placeholder_sqlite_database,
    find_or_create_dataset_asset,
    fingerprint_payload,
    load_run_case_schema,
    read_dataset_asset_source_payload,
    read_logical_json,
    sync_dataset_variant_databases,
    variant_lock,
    write_dataset_asset_source_payload,
    write_dataset_variant_manifest,
    write_evaluation_artifact,
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
    error: str | None = None
    dataset_variant_id: int | None = None
    dataset_variant_key: str | None = None
    generation_root_path: str | None = None
    database_reuse: dict[str, Any] | None = None


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
    def __init__(self, app: Flask) -> None:
        self._app = app

    def evaluate(self, job: EvaluationJob) -> EvaluationResult:
        run_case = job.run_case
        schema = load_run_case_schema(self._app, run_case)
        project_settings = job.run.project.settings_json or {}
        settings = self._build_settings(project_settings)
        normalized_pred = _canonicalize_sql(run_case.pred, run_case.dialect or "sqlite")
        normalized_gold = _canonicalize_sql(run_case.gold, run_case.dialect or "sqlite")
        exact_match = normalized_pred == normalized_gold
        levels: dict[str, LevelResult] = {}
        connection_info = run_case.db_connection_info
        dialect = run_case.dialect or "sqlite"
        dataset_variants_by_setting: dict[str, DatasetVariant] = {}
        for db_level in settings.db_levels:
            for query_level in settings.query_levels:
                key = self._result_key(db_level, query_level)
                logger.info(
                    "Evaluating job %s at db_level=%s query_level=%s",
                    job.id,
                    db_level,
                    query_level,
                )
                dataset_variant = None
                generation_root = None
                if dialect == "sqlite":
                    dataset_variant = _ensure_job_dataset_variant(
                        self._app,
                        job,
                        db_level=db_level,
                        query_level=query_level,
                        project_settings=project_settings,
                    )
                    dataset_variants_by_setting[key] = dataset_variant
                    generation_root = (
                        Path(self._app.config["ARTIFACT_ROOT"])
                        / dataset_variant.storage_path
                        / "databases"
                    )
                    sync_dataset_variant_databases(
                        self._app,
                        dataset_variant,
                        dialect=dialect,
                    )
                if exact_match:
                    levels[key] = self._equivalent_level_result(
                        db_level=db_level,
                        query_level=query_level,
                        run_case=run_case,
                        dialect=dialect,
                        dataset_variant=dataset_variant,
                        generation_root=generation_root,
                    )
                    continue

                run_result = disprove_queries(
                    dataset=run_case.dataset,
                    q1=run_case.gold,
                    q2=run_case.pred,
                    schema=schema,
                    dialect=dialect,
                    db_level=db_level,
                    query_level=query_level,
                    project_settings=project_settings,
                    host_or_path=(
                        connection_info.host
                        if connection_info
                        else run_case.host_or_path
                    ),
                    generation_root=str(generation_root) if generation_root else None,
                    db_id=run_case.db_id,
                    port=connection_info.port if connection_info else None,
                    username=connection_info.username if connection_info else None,
                    password=connection_info.password if connection_info else None,
                )
                if dataset_variant is not None:
                    sync_dataset_variant_databases(
                        self._app,
                        dataset_variant,
                        dialect=dialect,
                    )
                levels[key] = self._level_result_from_run_result(
                    db_level=db_level,
                    query_level=query_level,
                    run_case=run_case,
                    run_result=run_result,
                    query_timeout=_query_timeout(project_settings),
                    dataset_variant=dataset_variant,
                    generation_root=generation_root,
                )
        strictest_key = _strictest_result_key(job.run)
        strictest_variant = dataset_variants_by_setting.get(strictest_key)
        job.dataset_variant_id = (
            strictest_variant.id if strictest_variant is not None else None
        )
        return EvaluationResult(exact_match=exact_match, state="success", levels=levels)

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
        dataset_variant: DatasetVariant | None,
        generation_root: Path | None,
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
            error=None,
            dataset_variant_id=dataset_variant.id if dataset_variant else None,
            dataset_variant_key=(
                dataset_variant.variant_key if dataset_variant else None
            ),
            generation_root_path=str(generation_root) if generation_root else None,
            database_reuse={"hit": False, "source": "none", "database": None},
        )

    def _level_result_from_run_result(
        self,
        *,
        db_level: str,
        query_level: str,
        run_case: RunCase,
        run_result: Any,
        query_timeout: int,
        dataset_variant: DatasetVariant | None,
        generation_root: Path | None,
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
            error=_level_error_message(
                run_result=run_result, q1_result=q1_result, q2_result=q2_result
            ),
            dataset_variant_id=dataset_variant.id if dataset_variant else None,
            dataset_variant_key=(
                dataset_variant.variant_key if dataset_variant else None
            ),
            generation_root_path=str(generation_root) if generation_root else None,
            database_reuse={
                "hit": bool(getattr(run_result, "reuse_hit", False)),
                "source": getattr(run_result, "database_source", "none"),
                "database": getattr(run_result, "database_name", None),
            },
        )


class DatabaseResultSink:
    def __init__(self, app: Flask, write_artifacts: bool = True) -> None:
        self._app = app
        self._write_artifacts = write_artifacts

    def persist(self, job: EvaluationJob, result: EvaluationResult) -> None:
        summary = self._summarize_result(result)
        artifact_path = None
        if self._write_artifacts:
            artifact_path = self._write_artifact(job, result, summary)
            job.artifact_path = artifact_path
        job.result_json = self._summary_payload(summary, artifact_path=artifact_path)
        self._upsert_relaxed_equivalence(job, result, summary["equivalence_state"])
        self._upsert_cache_entry(job, summary)

    def persist_cached(self, job: EvaluationJob, cache_entry: EvaluationCacheEntry) -> None:
        job.result_json = cache_entry.result_summary_json or {}
        job.artifact_path = cache_entry.artifact_path
        payload = read_logical_json(self._app, cache_entry.artifact_path)
        summary = job.result_json if isinstance(job.result_json, dict) else {}
        self._upsert_relaxed_equivalence_from_artifact(
            job,
            payload if isinstance(payload, dict) else {},
            summary.get("equivalenceState", "pending"),
        )

    def _summary_payload(
        self, summary: dict[str, Any], *, artifact_path: str | None
    ) -> dict[str, Any]:
        payload = {
            "exactMatch": summary["exact_match"],
            "state": summary["state"],
            "error": summary["error"],
            "equivalenceState": summary["equivalence_state"],
            "resultsBySettings": summary["results_by_settings"],
            "cache": summary["cache"],
        }
        if artifact_path:
            payload["artifactPath"] = artifact_path
        return payload

    def _write_artifact(
        self, job: EvaluationJob, result: EvaluationResult, summary: dict[str, Any]
    ) -> str:
        run_case = job.run_case
        return write_evaluation_artifact(
            self._app,
            job,
            {
                "job": job.id,
                "projectId": job.run.project_id,
                "runId": job.run_id,
                "state": summary["state"],
                "exactMatch": summary["exact_match"],
                "datasetVariantId": job.dataset_variant_id,
                "runCase": {
                    "questionId": run_case.question_id,
                    "dbId": run_case.db_id,
                    "dataset": run_case.dataset,
                    "hostOrPath": run_case.host_or_path,
                    "gold": run_case.gold,
                    "pred": run_case.pred,
                },
                "resultsBySettings": summary["results_by_settings"],
                "error": summary["error"],
                "levels": [
                    {
                        "settingKey": setting_key,
                        "dbLevel": level.db_level,
                        "queryLevel": level.query_level,
                        "state": level.state,
                        "witnessDb": level.witness_db,
                        "q1Result": level.q1_result,
                        "q2Result": level.q2_result,
                        "error": level.error,
                        "datasetVariantId": level.dataset_variant_id,
                        "datasetVariantKey": level.dataset_variant_key,
                        "generationRootPath": level.generation_root_path,
                        "databaseReuse": level.database_reuse,
                    }
                    for setting_key, level in result.levels.items()
                ],
            },
        )

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
        if record.counterexamples:
            db.session.flush()

        for level in result.levels.values():
            db.session.add(
                CounterExample(
                    equivalence_id=record.id,
                    db_level=level.db_level,
                    query_level=level.query_level,
                    state=level.state,
                    artifact_key=f"db={level.db_level}|query={level.query_level}",
                    error_message=level.error,
                )
            )

    def _upsert_relaxed_equivalence_from_artifact(
        self, job: EvaluationJob, artifact_payload: dict[str, Any], overall_state: str
    ) -> None:
        levels = artifact_payload.get("levels")
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
        if not isinstance(levels, list):
            return
        for level in levels:
            if not isinstance(level, dict):
                continue
            db.session.add(
                CounterExample(
                    equivalence_id=record.id,
                    db_level=str(level.get("dbLevel") or "PK_FK"),
                    query_level=str(level.get("queryLevel") or "BAG"),
                    state=str(level.get("state") or "pending"),
                    artifact_key=str(
                        level.get("settingKey")
                        or f"db={level.get('dbLevel')}|query={level.get('queryLevel')}"
                    ),
                    error_message=level.get("error"),
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
                "error": level.error,
                "datasetVariantId": level.dataset_variant_id,
                "datasetVariantKey": level.dataset_variant_key,
                "hasWitnessDb": level.witness_db is not None,
                "hasQueryResults": level.q1_result is not None
                or level.q2_result is not None,
                "databaseReuse": level.database_reuse,
            }
            for key, level in result.levels.items()
        }
        return {
            "exact_match": result.exact_match,
            "state": result.state,
            "error": None,
            "equivalence_state": equivalence_state,
            "results_by_settings": results_by_settings,
            "cache": {"hit": False},
        }

    def _upsert_cache_entry(self, job: EvaluationJob, summary: dict[str, Any]) -> None:
        cache_key, dataset_variant_id, settings_fingerprint = _evaluation_cache_key(
            self._app,
            job.run_case,
            job.run,
            default_dataset_variant_id=job.dataset_variant_id,
        )
        if cache_key is None:
            return
        entry = db.session.scalar(
            select(EvaluationCacheEntry).where(EvaluationCacheEntry.cache_key == cache_key)
        )
        if entry is None:
            entry = EvaluationCacheEntry(
                cache_key=cache_key,
                dataset_variant_id=dataset_variant_id,
                latest_successful_job_id=job.id,
                gold_fingerprint=job.run_case.gold_fingerprint or "",
                pred_fingerprint=job.run_case.pred_fingerprint or "",
                settings_fingerprint=settings_fingerprint,
                state=summary["state"],
                result_summary_json=self._summary_payload(
                    {**summary, "cache": {"hit": True}},
                    artifact_path=job.artifact_path,
                ),
                artifact_path=job.artifact_path,
            )
            db.session.add(entry)
            return
        entry.dataset_variant_id = dataset_variant_id
        entry.latest_successful_job_id = job.id
        entry.settings_fingerprint = settings_fingerprint
        entry.state = summary["state"]
        entry.result_summary_json = self._summary_payload(
            {**summary, "cache": {"hit": True}},
            artifact_path=job.artifact_path,
        )
        entry.artifact_path = job.artifact_path


class EvaluationSubmissionService:
    def __init__(self, runtime: "EvaluationRuntime") -> None:
        self._runtime = runtime

    def submit_runcase(self, run_case: RunCase) -> EvaluationJob:
        jobs = self.submit_runcases([run_case])
        return jobs[0]

    def submit_runcases(self, run_cases: list[RunCase]) -> list[EvaluationJob]:
        if not run_cases:
            return []
        jobs: list[EvaluationJob] = []
        queued_jobs: list[EvaluationJob] = []
        sink = self._runtime._sink
        for run_case in run_cases:
            cached = self._find_cache_entry(run_case)
            if cached is not None:
                now = utcnow()
                job = EvaluationJob(
                    run_id=run_case.run_id,
                    run_case_id=run_case.id,
                    dataset_variant_id=cached.dataset_variant_id,
                    status=RunStatus.DONE.value,
                    queued_at=now,
                    started_at=now,
                    finished_at=now,
                    result_json=cached.result_summary_json or {},
                    artifact_path=cached.artifact_path,
                )
                db.session.add(job)
                db.session.flush()
                sink.persist_cached(job, cached)
                jobs.append(job)
                continue

            job = EvaluationJob(
                run_id=run_case.run_id,
                run_case_id=run_case.id,
                status=RunStatus.PENDING.value,
            )
            db.session.add(job)
            db.session.flush()
            jobs.append(job)
            queued_jobs.append(job)

        touched_run_ids = {run_case.run_id for run_case in run_cases}
        for run_id in touched_run_ids:
            recompute_model_run_state(run_id)

        db.session.commit()

        for job in queued_jobs:
            self._runtime.submit(EvaluationTaskPayload(evaluation_job_id=job.id))
            logger.info(
                "Submitted evaluation job %s for run case %s",
                job.id,
                job.run_case_id,
            )
        return jobs

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
        return self.submit_runcase(run_case)

    def _resolve_run_case(self, request: EvaluationRequest) -> RunCase:
        dataset_asset = self._resolve_dataset_asset(request)
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
                root_path=self._runtime._app.config["DATASET_STORAGE_ROOT"],
                dataset=request.dataset,
                db_id=request.db_id,
                dialect=request.dialect,
                host_or_path=request.host_or_path,
            )
            existing.db_connection_id = connection.id
            existing.host_or_path_legacy = connection.host_or_path
            existing.dialect = request.dialect
            existing.default_dataset_asset_id = (
                dataset_asset.id if dataset_asset is not None else None
            )
            existing.schema_fingerprint = fingerprint_payload(request.schema)
            existing.gold_fingerprint = fingerprint_text_payload(request.gold)
            existing.pred_fingerprint = fingerprint_text_payload(request.pred)
            existing.question = request.question
            existing.evidence = request.evidence
            run = db.session.get(ModelRun, request.run_id)
            if run is not None and run.dataset_asset_id is None and dataset_asset is not None:
                run.dataset_asset_id = dataset_asset.id
            return existing

        connection = ensure_db_connection_info(
            root_path=self._runtime._app.config["DATASET_STORAGE_ROOT"],
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
            default_dataset_asset_id=dataset_asset.id if dataset_asset is not None else None,
            schema_fingerprint=fingerprint_payload(request.schema)
            if request.schema is not None
            else None,
            question=request.question,
            evidence=request.evidence,
            prompt=None,
            gold=request.gold,
            pred=request.pred,
            gold_fingerprint=fingerprint_text_payload(request.gold),
            pred_fingerprint=fingerprint_text_payload(request.pred),
            source="queued",
        )
        db.session.add(run_case)
        db.session.flush()
        run = db.session.get(ModelRun, request.run_id)
        if run is not None and run.dataset_asset_id is None and dataset_asset is not None:
            run.dataset_asset_id = dataset_asset.id
        return run_case

    def _resolve_dataset_asset(
        self, request: EvaluationRequest
    ) -> DatasetAsset | None:
        if request.schema is None:
            return None
        source_payload = {
            "name": request.dataset,
            "description": f"Canonical schema payload for {request.dataset}",
            "dialect": request.dialect,
            "dbId": request.db_id,
            "schema": request.schema,
            "queries": None,
            "workload": None,
            "settings": {},
        }
        return find_or_create_dataset_asset(
            self._runtime._app,
            session=db.session,
            name=request.dataset,
            description=f"Canonical schema payload for {request.dataset}",
            dialect=request.dialect,
            source_type="queued_run_case_schema",
            source_payload=source_payload,
        )

    def _find_cache_entry(self, run_case: RunCase) -> EvaluationCacheEntry | None:
        run = db.session.get(ModelRun, run_case.run_id)
        if run is None:
            return None
        cache_key, dataset_variant_id, _ = _evaluation_cache_key(
            self._runtime._app,
            run_case,
            run,
        )
        if cache_key is None:
            return None
        entry = db.session.scalar(
            select(EvaluationCacheEntry).where(
                EvaluationCacheEntry.cache_key == cache_key,
                EvaluationCacheEntry.state == "success",
            )
        )
        if entry is None or not entry.artifact_path:
            return None
        artifact = read_logical_json(self._runtime._app, entry.artifact_path)
        if not isinstance(artifact, dict):
            return None
        entry.dataset_variant_id = entry.dataset_variant_id or dataset_variant_id
        if entry.dataset_variant_id is not None:
            variant = db.session.get(DatasetVariant, entry.dataset_variant_id)
            if variant is not None:
                sync_dataset_variant_databases(
                    self._runtime._app,
                    variant,
                    dialect=run_case.dialect or "sqlite",
                )
        summary = (
            entry.result_summary_json if isinstance(entry.result_summary_json, dict) else {}
        )
        entry.result_summary_json = {
            **summary,
            "cache": {"hit": True},
            "artifactPath": entry.artifact_path,
        }
        return entry

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
            logger.info(
                "Processing evaluation job %s, loaded job=%r",
                payload.evaluation_job_id,
                job,
            )
            if job is None or job.status == RunStatus.DONE.value:
                return
            logger.info(
                "Starting evaluation job %s for run case %s",
                job.id,
                job.run_case_id,
            )
            try:
                job.status = RunStatus.RUNNING.value
                if job.started_at is None:
                    job.started_at = utcnow()
                job.error_message = None
                recompute_model_run_state(job.run_id)
                db.session.commit()
                logger.info("Evaluation job %s marked running", job.id)
                logger.info("Evaluation job %s entering engine.evaluate", job.id)
                result = self._engine.evaluate(job)
                if result.state != "success":
                    raise RuntimeError(
                        f"evaluation returned terminal state {result.state}"
                    )
                logger.info(
                    "Evaluation job %s engine.evaluate completed with state=%s",
                    job.id,
                    result.state,
                )
                logger.info("Evaluation job %s entering result persistence", job.id)
                self._sink.persist(job, result)
                logger.info("Evaluation job %s finished result persistence", job.id)
                job.status = RunStatus.DONE.value
                job.finished_at = utcnow()
                job.error_message = None
                recompute_model_run_state(job.run_id)
                db.session.commit()
                logger.info("Evaluation job %s committed successfully", job.id)
            except Exception as exc:
                logger.exception(
                    "Evaluation job %s failed during process_task",
                    payload.evaluation_job_id,
                )
                db.session.rollback()
                failed_job = db.session.get(EvaluationJob, payload.evaluation_job_id)
                if failed_job is None:
                    return
                failed_job.status = RunStatus.ERROR.value
                failed_job.finished_at = utcnow()
                failed_job.error_message = str(exc)
                failed_job.result_json = _failed_result_payload(str(exc))
                recompute_model_run_state(failed_job.run_id)
                db.session.commit()
            except BaseException:
                logger.exception(
                    "Evaluation job %s aborted by BaseException",
                    payload.evaluation_job_id,
                )
                db.session.rollback()
                raise


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

        self._engine = EvaluationEngine(app)
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
                logger.info(
                    "Evaluation worker picked up job %s",
                    payload.evaluation_job_id,
                )
                self._consumer.process_task(payload)
                logger.info(
                    "Evaluation worker finished job %s",
                    payload.evaluation_job_id,
                )
            except BaseException:
                logger.exception(
                    "Evaluation worker crashed while processing job %s",
                    payload.evaluation_job_id,
                )
                raise
            finally:
                self._queue.task_done()


def _ensure_job_dataset_variant(
    app: Flask,
    job: EvaluationJob,
    *,
    db_level: str,
    query_level: str,
    project_settings: dict[str, Any],
) -> DatasetVariant:
    run_case = job.run_case
    schema = load_run_case_schema(app, run_case)
    dialect = run_case.dialect or "sqlite"
    dataset_asset = run_case.default_dataset_asset or job.run.dataset_asset
    cache_settings = _database_cache_settings(
        db_level=db_level,
        query_level=query_level,
        project_settings=project_settings,
    )
    source_payload = {
        "schema": schema,
        "dbId": run_case.db_id,
        "cacheScope": "dataset_schema_settings",
        "settings": cache_settings,
        "workload": None,
        "examples": [
            {
                "gold": run_case.gold,
                "pred": run_case.pred,
                "question": run_case.question,
            }
        ],
    }
    source_fingerprint = fingerprint_payload(
        {
            "dataset": run_case.dataset,
            "schema": source_payload["schema"],
            "dialect": dialect,
        }
    )
    if dataset_asset is None:
        dataset_asset = db.session.scalar(
            select(DatasetAsset).where(
                DatasetAsset.name == run_case.dataset,
                DatasetAsset.dialect == dialect,
                DatasetAsset.source_fingerprint == source_fingerprint,
            )
        )
    if dataset_asset is None:
        dataset_asset = find_or_create_dataset_asset(
            app,
            session=db.session,
            name=run_case.dataset,
            description=f"Auto-generated dataset asset for {run_case.dataset}",
            dialect=dialect,
            source_type="evaluation_dataset_cache",
            source_payload=source_payload,
        )
    elif not dataset_asset.source_payload_path:
        write_dataset_asset_source_payload(app, dataset_asset, source_payload)

    run_case.default_dataset_asset_id = dataset_asset.id
    if job.run.dataset_asset_id is None:
        job.run.dataset_asset_id = dataset_asset.id

    schema_fingerprint = fingerprint_payload(source_payload["schema"])
    workload_fingerprint = None
    settings_fingerprint = fingerprint_payload(cache_settings)
    variant_key = fingerprint_payload(
        {
            "datasetAssetId": dataset_asset.id,
            "schema": schema_fingerprint,
            "settings": settings_fingerprint,
        }
    )[:24]

    existing = db.session.scalar(
        select(DatasetVariant).where(
            DatasetVariant.dataset_asset_id == dataset_asset.id,
            DatasetVariant.variant_key == variant_key,
        )
    )
    if existing is not None:
        ensure_dataset_variant_storage(app, existing)
        ensure_placeholder_sqlite_database(
            app,
            existing,
            run_case.db_id,
            schema=schema,
            dialect=dialect,
        )
        sync_dataset_variant_databases(app, existing, dialect=dialect)
        return existing

    with variant_lock(f"{dataset_asset.id}:{variant_key}"):
        existing = db.session.scalar(
            select(DatasetVariant).where(
                DatasetVariant.dataset_asset_id == dataset_asset.id,
                DatasetVariant.variant_key == variant_key,
            )
        )
        if existing is not None:
            ensure_dataset_variant_storage(app, existing)
            ensure_placeholder_sqlite_database(
                app,
                existing,
                run_case.db_id,
                schema=schema,
                dialect=dialect,
            )
            sync_dataset_variant_databases(app, existing, dialect=dialect)
            return existing

        variant = DatasetVariant(
            dataset_asset_id=dataset_asset.id,
            variant_key=variant_key,
            schema_fingerprint=schema_fingerprint,
            workload_fingerprint=workload_fingerprint,
            settings_fingerprint=settings_fingerprint,
            status="generated",
        )
        db.session.add(variant)
        db.session.flush()
        ensure_dataset_variant_storage(app, variant)
        sqlite_file = ensure_placeholder_sqlite_database(
            app,
            variant,
            run_case.db_id,
            schema=schema,
            dialect=dialect,
        )
        write_dataset_variant_manifest(
            app,
            variant,
            {
                "datasetAssetId": dataset_asset.id,
                "datasetVariantId": variant.id,
                "variantKey": variant.variant_key,
                "schemaFingerprint": schema_fingerprint,
                "workloadFingerprint": workload_fingerprint,
                "settingsFingerprint": settings_fingerprint,
                "dialect": dialect,
                "databases": [sqlite_file] if sqlite_file else [],
            },
        )
        sync_dataset_variant_databases(app, variant, dialect=dialect)
        return variant


def recompute_model_run_state(run_id: int) -> None:
    run = db.session.get(ModelRun, run_id)
    if run is None:
        return
    jobs = db.session.scalars(
        select(EvaluationJob).where(EvaluationJob.run_id == run_id)
    ).all()
    if not jobs:
        return
    statuses = [job.status for job in jobs]
    if RunStatus.ERROR.value in statuses:
        run.status = RunStatus.ERROR.value
        run.metric_json = _empty_metric_json()
    elif RunStatus.RUNNING.value in statuses:
        run.status = RunStatus.RUNNING.value
        run.metric_json = _empty_metric_json()
    elif RunStatus.PENDING.value in statuses:
        run.status = RunStatus.PENDING.value
        run.metric_json = _empty_metric_json()
    elif all(status == RunStatus.DONE.value for status in statuses):
        run.status = RunStatus.DONE.value
        run.metric_json = _aggregate_metric_json(run, jobs)


def recompute_model_run_status(run_id: int) -> None:
    recompute_model_run_state(run_id)


def build_evaluation_runtime(app: Flask) -> EvaluationRuntime:
    runtime = EvaluationRuntime(
        app,
        num_workers=int(app.config.get("EVALUATION_WORKERS", 1)),
        queue_maxsize=int(app.config.get("EVALUATION_QUEUE_MAXSIZE", 0)),
        write_artifacts=bool(app.config.get("EVALUATION_WRITE_ARTIFACTS", True)),
    )
    runtime.start()
    return runtime


def _empty_metric_json() -> dict[str, float | None]:
    return {"EXEC ACC": None, "EXACT MATCH": None}


def _aggregate_metric_json(
    run: ModelRun, jobs: list[EvaluationJob]
) -> dict[str, float | None]:
    if not jobs:
        return _empty_metric_json()

    strictest_key = _strictest_result_key(run)
    exact_matches = 0
    execution_matches = 0
    for job in jobs:
        result = job.result_json if isinstance(job.result_json, dict) else {}
        if job.status == RunStatus.ERROR.value or result.get("state") == "failed":
            continue
        if result.get("exactMatch") is True:
            exact_matches += 1
        results_by_settings = result.get("resultsBySettings")
        if not isinstance(results_by_settings, dict):
            continue
        strictest_result = results_by_settings.get(strictest_key)
        if not isinstance(strictest_result, dict):
            continue
        if strictest_result.get("state") == "equivalent":
            execution_matches += 1

    total = len(jobs)
    return {
        "EXEC ACC": execution_matches / total,
        "EXACT MATCH": exact_matches / total,
    }


def _strictest_result_key(run: ModelRun) -> str:
    settings = run.setting_json if isinstance(run.setting_json, dict) else {}
    project_settings = (
        run.project.settings_json
        if run.project and isinstance(run.project.settings_json, dict)
        else {}
    )
    db_levels = (
        settings.get("dbLevels")
        or settings.get("db_levels")
        or project_settings.get("dbLevels")
        or project_settings.get("db_levels")
        or [level.value for level in DBLevel.ordered()]
    )
    query_levels = (
        settings.get("queryLevels")
        or settings.get("query_levels")
        or project_settings.get("queryLevels")
        or project_settings.get("query_levels")
        or [level.value for level in QueryLevel.ordered()]
    )
    db_level = list(db_levels)[-1]
    query_level = list(query_levels)[-1]
    return f"db={db_level}|query={query_level}"


def _evaluation_settings_payload(run: ModelRun) -> dict[str, Any]:
    settings = run.setting_json if isinstance(run.setting_json, dict) else {}
    project_settings = (
        run.project.settings_json
        if run.project and isinstance(run.project.settings_json, dict)
        else {}
    )
    return {
        "dbLevels": settings.get("dbLevels")
        or settings.get("db_levels")
        or project_settings.get("dbLevels")
        or project_settings.get("db_levels")
        or [level.value for level in DBLevel.ordered()],
        "queryLevels": settings.get("queryLevels")
        or settings.get("query_levels")
        or project_settings.get("queryLevels")
        or project_settings.get("query_levels")
        or [level.value for level in QueryLevel.ordered()],
        "projectSettings": project_settings,
        "runSettings": settings,
    }


def _evaluation_cache_key(
    app: Flask,
    run_case: RunCase,
    run: ModelRun,
    *,
    default_dataset_variant_id: int | None = None,
) -> tuple[str | None, int | None, str]:
    settings_payload = _evaluation_settings_payload(run)
    settings_fingerprint = fingerprint_payload(settings_payload)
    dataset_variant_id = default_dataset_variant_id or run_case.default_dataset_asset_id
    if dataset_variant_id is None:
        dataset_asset = run_case.default_dataset_asset or run.dataset_asset
        if dataset_asset is None:
            return None, None, settings_fingerprint
        dataset_variant_id = dataset_asset.id
    payload = {
        "datasetVariantId": dataset_variant_id,
        "gold": run_case.gold_fingerprint or fingerprint_text_payload(run_case.gold),
        "pred": run_case.pred_fingerprint or fingerprint_text_payload(run_case.pred),
        "settings": settings_fingerprint,
    }
    return fingerprint_payload(payload), dataset_variant_id, settings_fingerprint


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


def _database_cache_settings(
    *,
    db_level: str,
    query_level: str,
    project_settings: dict[str, Any],
) -> dict[str, Any]:
    generator_keys = (
        "null_threshold",
        "unique_threshold",
        "duplicate_threshold",
        "group_count_threshold",
        "group_size_threshold",
        "positive_threshold",
        "negative_threshold",
        "max_tries",
    )
    return {
        "dbLevel": db_level,
        "queryLevel": query_level,
        "generator": {
            key: project_settings.get(key)
            for key in generator_keys
            if key in project_settings
        },
    }


def _map_run_state(state: str) -> str:
    if state == "NEQ":
        return "witnessed"
    if state == "EQ":
        return "equivalent"
    return "error"


def _level_error_message(
    *,
    run_result: Any,
    q1_result: dict[str, Any] | None,
    q2_result: dict[str, Any] | None,
) -> str | None:
    if getattr(run_result, "error_msg", None):
        return run_result.error_msg
    for payload in (q1_result, q2_result):
        if isinstance(payload, dict):
            error_msg = payload.get("error_msg")
            if isinstance(error_msg, str) and error_msg.strip():
                return error_msg
    return None


def _failed_result_payload(error_message: str) -> dict[str, Any]:
    return {
        "exactMatch": False,
        "state": "failed",
        "error": error_message,
        "equivalenceState": "failed",
        "resultsBySettings": {},
        "cache": {"hit": False},
    }
