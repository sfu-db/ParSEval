from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .models import (
    CounterExample,
    EvaluationJob,
    ModelRun,
    Project,
    RelaxedEquivalence,
    RunCase,
)
METRIC_KEYS = ("EXEC ACC", "EXACT MATCH")


def isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_metric(metric: dict[str, Any] | None) -> dict[str, Any]:
    metric = metric or {}
    return {
        "EXEC ACC": metric.get("EXEC ACC", metric.get("EXEC_ACC")),
        "EXACT MATCH": metric.get("EXACT MATCH", metric.get("EXACT_MATCH")),
    }


def serialize_project(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "settings": project.settings_json or {},
    }


def serialize_model_run(run: ModelRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "projectId": run.project_id,
        "model": run.model,
        "status": run.status,
        "createdAt": isoformat(run.created_at),
        "run": run.run_name,
        "dataset": run.dataset,
        "promptTemplate": run.prompt_template,
        "uploadedFilePath": run.uploaded_file_path,
        "metric": normalize_metric(run.metric_json),
        "setting": run.setting_json or {},
    }


def serialize_run_case_as_eval_record(run_case: RunCase) -> dict[str, Any]:
    latest_job = max(
        run_case.evaluation_jobs,
        key=lambda job: (job.finished_at or job.started_at or job.queued_at, job.id),
        default=None,
    )
    labels: dict[str, bool | None] = {}
    if latest_job and isinstance(latest_job.result_json, dict):
        results_by_settings = latest_job.result_json.get("resultsBySettings")
        if isinstance(results_by_settings, dict):
            for payload in results_by_settings.values():
                if not isinstance(payload, dict):
                    continue
                db_level = payload.get("dbLevel")
                query_level = payload.get("queryLevel")
                state = payload.get("state")
                if not isinstance(db_level, str) or not isinstance(query_level, str):
                    continue
                key = f"{db_level}_{query_level}"
                if state == "equivalent":
                    labels[key] = True
                elif state == "witnessed":
                    labels[key] = False
                else:
                    labels[key] = None

    return {
        "runId": run_case.run_id,
        "question_id": run_case.question_id,
        "db_id": run_case.db_id,
        "dataset": run_case.dataset,
        "host_or_path": run_case.host_or_path,
        "question": run_case.question or "",
        "evidence": run_case.evidence,
        "gold": run_case.gold,
        "prompt": run_case.prompt or "",
        "pred": run_case.pred,
        "labels": labels,
    }


def serialize_evaluation_job(job: EvaluationJob) -> dict[str, Any]:
    run_case = job.run_case
    return {
        "id": job.id,
        "projectId": job.run.project_id,
        "runId": job.run_id,
        "status": job.status,
        "gold": run_case.gold,
        "pred": run_case.pred,
        "dbId": run_case.db_id,
        "dataset": run_case.dataset,
        "hostOrPath": run_case.host_or_path,
        "schema": run_case.schema_json,
        "questionId": run_case.question_id,
        "question": run_case.question,
        "evidence": run_case.evidence,
        "error": job.error_message,
        "result": job.result_json or {},
        "artifactPath": job.artifact_path,
        "queuedAt": isoformat(job.queued_at),
        "startedAt": isoformat(job.started_at),
        "finishedAt": isoformat(job.finished_at),
    }


def serialize_counterexample(counterexample: CounterExample) -> dict[str, Any]:
    run_case = counterexample.equivalence.evaluation_job.run_case
    database = run_case.db_id
    if isinstance(counterexample.q1_result_json, dict):
        database = counterexample.q1_result_json.get("db_id", database)
    return {
        "runId": counterexample.equivalence.evaluation_job.run_id,
        "dataset": run_case.dataset,
        "question_id": run_case.question_id,
        "db_id": run_case.db_id,
        "host_or_path": run_case.host_or_path,
        "database": database,
        "gold": run_case.gold,
        "pred": run_case.pred,
        "settings": [
            {
                "db_level": counterexample.db_level,
                "query_level": counterexample.query_level,
            }
        ],
        "witeness_db": counterexample.witness_db_json,
        "q1_result": counterexample.q1_result_json,
        "q2_result": counterexample.q2_result_json,
        "state": counterexample.state,
    }


def serialize_relaxed_equivalence(record: RelaxedEquivalence) -> dict[str, Any]:
    run_case = record.evaluation_job.run_case
    counterexamples = sorted(
        record.counterexamples,
        key=lambda item: (item.db_level, item.query_level, item.id),
    )
    return {
        "runId": record.evaluation_job.run_id,
        "dataset": run_case.dataset,
        "db_id": run_case.db_id,
        "question_id": run_case.question_id,
        "host_or_path": run_case.host_or_path,
        "gold": run_case.gold,
        "pred": run_case.pred,
        "counternexample": [serialize_counterexample(item) for item in counterexamples],
        "state": record.state,
    }
