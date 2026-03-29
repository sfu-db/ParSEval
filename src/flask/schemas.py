from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import current_app

from .models import (
    CounterExample,
    DatasetAsset,
    DatasetVariant,
    EvaluationJob,
    ModelRun,
    Project,
    RelaxedEquivalence,
    RunCase,
)
from .storage import load_run_case_schema, read_logical_json
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
        "storagePath": project.storage_path,
        "metadataPath": project.metadata_path,
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
        "storagePath": run.storage_path,
        "metadataPath": run.metadata_path,
        "datasetAssetId": run.dataset_asset_id,
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
    artifact = read_logical_json(current_app, job.artifact_path)
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
        "schema": load_run_case_schema(current_app, run_case),
        "questionId": run_case.question_id,
        "question": run_case.question,
        "evidence": run_case.evidence,
        "error": job.error_message,
        "result": job.result_json or {},
        "artifactPath": job.artifact_path,
        "artifact": artifact if isinstance(artifact, dict) else None,
        "datasetVariantId": job.dataset_variant_id,
        "queuedAt": isoformat(job.queued_at),
        "startedAt": isoformat(job.started_at),
        "finishedAt": isoformat(job.finished_at),
    }


def serialize_dataset_variant(dataset_variant: DatasetVariant) -> dict[str, Any]:
    return {
        "id": dataset_variant.id,
        "datasetAssetId": dataset_variant.dataset_asset_id,
        "variantKey": dataset_variant.variant_key,
        "schemaFingerprint": dataset_variant.schema_fingerprint,
        "workloadFingerprint": dataset_variant.workload_fingerprint,
        "settingsFingerprint": dataset_variant.settings_fingerprint,
        "storagePath": dataset_variant.storage_path,
        "manifestPath": dataset_variant.manifest_path,
        "status": dataset_variant.status,
        "createdAt": isoformat(dataset_variant.created_at),
    }


def serialize_dataset_asset(dataset_asset: DatasetAsset) -> dict[str, Any]:
    return {
        "id": dataset_asset.id,
        "name": dataset_asset.name,
        "slug": dataset_asset.slug,
        "description": dataset_asset.description,
        "dialect": dataset_asset.dialect,
        "sourceType": dataset_asset.source_type,
        "sourcePayloadPath": dataset_asset.source_payload_path,
        "storagePath": dataset_asset.storage_path,
        "metadataPath": dataset_asset.metadata_path,
        "status": dataset_asset.status,
        "createdAt": isoformat(dataset_asset.created_at),
        "variants": [
            serialize_dataset_variant(variant)
            for variant in sorted(dataset_asset.variants, key=lambda item: item.id)
        ],
    }


def serialize_counterexample(counterexample: CounterExample) -> dict[str, Any]:
    run_case = counterexample.equivalence.evaluation_job.run_case
    artifact = read_logical_json(
        current_app, counterexample.equivalence.evaluation_job.artifact_path
    )
    level = _artifact_level_payload(artifact, counterexample.artifact_key)
    database = run_case.db_id
    q1_result = level.get("q1Result") if isinstance(level, dict) else None
    q2_result = level.get("q2Result") if isinstance(level, dict) else None
    if isinstance(q1_result, dict):
        database = q1_result.get("db_id", database)
    witness_db = level.get("witnessDb") if isinstance(level, dict) else None
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
        "witnessDb": witness_db,
        "witeness_db": witness_db,
        "q1Result": q1_result,
        "q1_result": q1_result,
        "q2Result": q2_result,
        "q2_result": q2_result,
        "error": counterexample.error_message,
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
        "counterexamples": [serialize_counterexample(item) for item in counterexamples],
        "counternexample": [serialize_counterexample(item) for item in counterexamples],
        "error": record.evaluation_job.error_message,
        "state": record.state,
    }


def _artifact_level_payload(
    artifact: dict[str, Any] | list[Any] | None, artifact_key: str | None
) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        return {}
    levels = artifact.get("levels")
    if not isinstance(levels, list):
        return {}
    for item in levels:
        if not isinstance(item, dict):
            continue
        candidate = item.get("settingKey")
        if artifact_key and candidate == artifact_key:
            return item
        fallback = f"db={item.get('dbLevel')}|query={item.get('queryLevel')}"
        if artifact_key and fallback == artifact_key:
            return item
    return {}
