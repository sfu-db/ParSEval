from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .models import (
    DatasetAsset,
    DatasetVariant,
    EvaluationJob,
    ModelRun,
    Project,
    ProjectDataset,
    RelaxedEquivalence,
    RunCase,
    db,
    ensure_db_connection_info,
    prune_orphan_db_connections,
)
from .schemas import (
    normalize_metric,
    serialize_dataset_asset,
    serialize_evaluation_job,
    serialize_model_run,
    serialize_project,
    serialize_relaxed_equivalence,
    serialize_run_case_as_eval_record,
)
from .storage import (
    delete_logical_path,
    find_or_create_dataset_asset,
    fingerprint_payload,
    resolve_logical_path,
    sync_dataset_variant_databases,
    write_dataset_asset_source_payload,
    write_dataset_variant_manifest,
    write_run_upload_payload,
    ensure_dataset_asset_storage,
    ensure_dataset_variant_storage,
    ensure_placeholder_sqlite_database,
    ensure_project_storage,
    ensure_run_storage,
    variant_lock,
)


api = Blueprint("querylens_api", __name__)


def error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def json_body() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def require_string(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required")
    return value.strip()


def optional_string(data: dict[str, Any], key: str, default: str = "") -> str:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def require_int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def parse_limit_offset() -> tuple[int, int]:
    limit_arg = request.args.get("limit", "100")
    offset_arg = request.args.get("offset", "0")
    try:
        limit = max(0, int(limit_arg))
        offset = max(0, int(offset_arg))
    except ValueError as exc:
        raise ValueError("limit and offset must be integers") from exc
    return limit, offset


def build_model_run(
    project_id: int,
    payload: dict[str, Any],
    fallback_metric: dict[str, float] | None = None,
) -> ModelRun:
    model = require_string(payload, "model")
    dataset = require_string(payload, "dataset")
    metric = normalize_metric(payload.get("metric"))
    if fallback_metric:
        for key, value in fallback_metric.items():
            if metric.get(key) is None:
                metric[key] = value
    return ModelRun(
        project_id=project_id,
        model=model,
        status=(payload.get("status") or "pending"),
        run_name=payload.get("run"),
        dataset=dataset,
        prompt_template=payload.get("promptTemplate"),
        dataset_asset_id=payload.get("datasetAssetId")
        if isinstance(payload.get("datasetAssetId"), int)
        else None,
        metric_json=metric,
        setting_json=(
            payload.get("setting") if isinstance(payload.get("setting"), dict) else {}
        ),
    )


def build_run_case(run_id: int, payload: dict[str, Any], source: str) -> RunCase:
    question_id = payload.get("question_id")
    if question_id is not None:
        question_id = require_int(question_id, "question_id")
    dataset = require_string(payload, "dataset")
    db_id = require_string(payload, "db_id")
    dialect = (
        payload.get("dialect") if isinstance(payload.get("dialect"), str) else "sqlite"
    )
    connection = ensure_db_connection_info(
        root_path=current_app.config["DATASET_STORAGE_ROOT"],
        dataset=dataset,
        db_id=db_id,
        dialect=dialect,
        host_or_path=optional_string(payload, "host_or_path"),
    )
    schema_payload = payload.get("schema")
    gold = require_string(payload, "gold")
    pred = require_string(payload, "pred")
    return RunCase(
        run_id=run_id,
        db_connection_id=connection.id,
        question_id=question_id,
        db_id=db_id,
        dataset=dataset,
        host_or_path_legacy=connection.host_or_path,
        dialect=dialect,
        default_dataset_asset_id=payload.get("datasetAssetId")
        if isinstance(payload.get("datasetAssetId"), int)
        else None,
        schema_fingerprint=fingerprint_payload(schema_payload)
        if schema_payload is not None
        else None,
        question=(
            payload.get("question")
            if isinstance(payload.get("question"), str)
            else None
        ),
        evidence=(
            payload.get("evidence")
            if isinstance(payload.get("evidence"), str)
            else None
        ),
        prompt=(
            payload.get("prompt") if isinstance(payload.get("prompt"), str) else None
        ),
        gold=gold,
        pred=pred,
        gold_fingerprint=fingerprint_payload(gold),
        pred_fingerprint=fingerprint_payload(pred),
        source=source,
    )


def evaluation_runtime():
    runtime = current_app.extensions.get("evaluation_runtime")
    if runtime is None:
        raise RuntimeError("Evaluation runtime is not configured")
    return runtime


def _app():
    return current_app._get_current_object()


def _dataset_source_payload(
    dataset_asset: DatasetAsset, payload: dict[str, Any]
) -> dict[str, Any]:
    db_id = payload.get("dbId", payload.get("db_id"))
    return {
        "name": dataset_asset.name,
        "description": dataset_asset.description,
        "dialect": dataset_asset.dialect,
        "dbId": db_id,
        "schema": payload.get("schema"),
        "queries": payload.get("queries"),
        "workload": payload.get("workload"),
        "settings": payload.get("settings") if isinstance(payload.get("settings"), dict) else {},
    }


def _run_case_dataset_source_payload(
    *,
    dataset: str,
    dialect: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    db_id = payload.get("dbId", payload.get("db_id"))
    return {
        "name": dataset,
        "description": f"Canonical schema payload for {dataset}",
        "dialect": dialect,
        "dbId": db_id,
        "schema": payload.get("schema"),
        "queries": None,
        "workload": None,
        "settings": {},
    }


def _find_project_or_404(project_id: int):
    project = db.session.get(Project, project_id)
    if project is None:
        return None
    return project


def _prune_dataset_asset_if_orphaned(dataset_asset_id: int) -> None:
    dataset_asset = db.session.get(DatasetAsset, dataset_asset_id)
    if dataset_asset is None:
        return
    if dataset_asset.project_links or dataset_asset.model_runs or dataset_asset.run_cases:
        return
    if any(variant.evaluation_jobs for variant in dataset_asset.variants):
        return
    app = _app()
    delete_logical_path(app, dataset_asset.storage_path)
    db.session.delete(dataset_asset)


def _create_or_get_dataset_variant(
    dataset_asset: DatasetAsset,
    payload: dict[str, Any],
) -> DatasetVariant:
    app = _app()
    settings = payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    schema_fingerprint = fingerprint_payload(payload.get("schema"))
    workload_payload = payload.get("workload")
    if workload_payload is None:
        workload_payload = payload.get("queries")
    workload_fingerprint = fingerprint_payload(workload_payload)
    settings_fingerprint = fingerprint_payload(settings)
    raw_key = {
        "dialect": dataset_asset.dialect,
        "schema": schema_fingerprint,
        "workload": workload_fingerprint,
        "settings": settings_fingerprint,
    }
    variant_key = fingerprint_payload(raw_key)[:24]

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
            payload.get("dbId", dataset_asset.name),
            schema=payload.get("schema"),
            dialect=dataset_asset.dialect,
        )
        sync_dataset_variant_databases(app, existing, dialect=dataset_asset.dialect)
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
                payload.get("dbId", dataset_asset.name),
                schema=payload.get("schema"),
                dialect=dataset_asset.dialect,
            )
            sync_dataset_variant_databases(app, existing, dialect=dataset_asset.dialect)
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
            payload.get("dbId", dataset_asset.name),
            schema=payload.get("schema"),
            dialect=dataset_asset.dialect,
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
                "dialect": dataset_asset.dialect,
                "databases": [sqlite_file] if sqlite_file else [],
            },
        )
        sync_dataset_variant_databases(app, variant, dialect=dataset_asset.dialect)
        return variant


@api.errorhandler(ValueError)
def handle_value_error(error: ValueError):
    return jsonify({"error": str(error)}), 400


@api.get("/datasets")
def list_datasets():
    datasets = db.session.scalars(select(DatasetAsset).order_by(DatasetAsset.id)).all()
    return jsonify([serialize_dataset_asset(item) for item in datasets])


@api.post("/datasets")
def create_dataset():
    payload = json_body()
    name = require_string(payload, "name")
    description = (
        payload.get("description") if isinstance(payload.get("description"), str) else None
    )
    dialect = optional_string(payload, "dialect", "sqlite") or "sqlite"
    dataset_stub = DatasetAsset(name=name, description=description, dialect=dialect)
    dataset_asset = find_or_create_dataset_asset(
        _app(),
        session=db.session,
        name=name,
        description=description,
        dialect=dialect,
        source_type="uploaded_json",
        source_payload=_dataset_source_payload(dataset_stub, payload),
    )
    db.session.commit()
    return jsonify(serialize_dataset_asset(dataset_asset)), 201


@api.get("/datasets/<int:dataset_id>")
def get_dataset(dataset_id: int):
    dataset_asset = db.session.get(DatasetAsset, dataset_id)
    if dataset_asset is None:
        return error(f"Dataset {dataset_id} not found", 404)
    return jsonify(serialize_dataset_asset(dataset_asset))


@api.post("/datasets/<int:dataset_id>/generate")
def generate_dataset_variant(dataset_id: int):
    dataset_asset = db.session.get(DatasetAsset, dataset_id)
    if dataset_asset is None:
        return error(f"Dataset {dataset_id} not found", 404)
    payload = json_body()
    source_payload = {
        "schema": payload.get("schema"),
        "queries": payload.get("queries"),
        "workload": payload.get("workload"),
        "settings": payload.get("settings"),
        "dbId": payload.get("dbId", payload.get("db_id", dataset_asset.name)),
    }
    if source_payload["schema"] is None and dataset_asset.source_payload_path:
        source_path = resolve_logical_path(_app(), dataset_asset.source_payload_path)
        if source_path is None or not source_path.is_file():
            raise ValueError("Dataset source payload is not available")
        with open(source_path, "r", encoding="utf-8") as fh:
            saved = json.load(fh)
        source_payload = {
            "schema": saved.get("schema"),
            "queries": payload.get("queries", saved.get("queries")),
            "workload": payload.get("workload", saved.get("workload")),
            "settings": payload.get("settings", saved.get("settings")),
            "dbId": payload.get("dbId", payload.get("db_id", dataset_asset.name)),
        }
    variant = _create_or_get_dataset_variant(dataset_asset, source_payload)
    db.session.commit()
    return jsonify(
        {
            "dataset": serialize_dataset_asset(dataset_asset),
            "variant": {
                "id": variant.id,
                "datasetAssetId": variant.dataset_asset_id,
                "variantKey": variant.variant_key,
                "storagePath": variant.storage_path,
                "manifestPath": variant.manifest_path,
                "status": variant.status,
            },
        }
    ), 201


@api.delete("/datasets/<int:dataset_id>")
def delete_dataset(dataset_id: int):
    dataset_asset = db.session.get(DatasetAsset, dataset_id)
    if dataset_asset is None:
        return error(f"Dataset {dataset_id} not found", 404)
    if dataset_asset.project_links or dataset_asset.model_runs or dataset_asset.run_cases:
        return error("Dataset is still attached to projects or runs", 409)
    if any(variant.evaluation_jobs for variant in dataset_asset.variants):
        return error("Dataset variants are still referenced by evaluation jobs", 409)
    storage_path = dataset_asset.storage_path
    db.session.delete(dataset_asset)
    db.session.commit()
    delete_logical_path(_app(), storage_path)
    return "", 204


@api.get("/projects")
def list_projects():
    projects = db.session.scalars(select(Project).order_by(Project.id)).all()
    return jsonify([serialize_project(project) for project in projects])


@api.post("/projects")
def create_project():
    payload = json_body()
    settings = (
        payload.get("settings") if isinstance(payload.get("settings"), dict) else {}
    )
    project = Project(
        name=require_string(payload, "name"),
        description=payload.get("description"),
        settings_json=settings,
    )
    db.session.add(project)
    db.session.flush()
    ensure_project_storage(_app(), project)
    db.session.commit()
    return jsonify(serialize_project(project)), 201


@api.get("/projects/<int:project_id>")
def get_project(project_id: int):
    project = db.session.get(Project, project_id)
    if project is None:
        return error(f"Project {project_id} not found", 404)
    return jsonify(serialize_project(project))


@api.delete("/projects/<int:project_id>")
def delete_project(project_id: int):
    project = db.session.get(Project, project_id)
    if project is None:
        return error(f"Project {project_id} not found", 404)
    project_storage_path = project.storage_path
    for run in project.runs:
        delete_logical_path(_app(), run.storage_path)
    db.session.delete(project)
    db.session.flush()
    prune_orphan_db_connections()
    delete_logical_path(_app(), project_storage_path)
    db.session.commit()
    return "", 204


@api.get("/projects/<int:project_id>/datasets")
def list_project_datasets(project_id: int):
    project = _find_project_or_404(project_id)
    if project is None:
        return error(f"Project {project_id} not found", 404)
    datasets = [link.dataset_asset for link in sorted(project.dataset_links, key=lambda item: item.id)]
    return jsonify([serialize_dataset_asset(item) for item in datasets])


@api.post("/projects/<int:project_id>/datasets/<int:dataset_id>")
def attach_dataset_to_project(project_id: int, dataset_id: int):
    project = _find_project_or_404(project_id)
    if project is None:
        return error(f"Project {project_id} not found", 404)
    dataset_asset = db.session.get(DatasetAsset, dataset_id)
    if dataset_asset is None:
        return error(f"Dataset {dataset_id} not found", 404)
    link = db.session.scalar(
        select(ProjectDataset).where(
            ProjectDataset.project_id == project_id,
            ProjectDataset.dataset_asset_id == dataset_id,
        )
    )
    if link is None:
        db.session.add(ProjectDataset(project_id=project_id, dataset_asset_id=dataset_id))
        db.session.commit()
    return jsonify(serialize_dataset_asset(dataset_asset)), 201


@api.get("/projects/<int:project_id>/model-runs")
def list_model_runs(project_id: int):
    project = db.session.get(Project, project_id)
    if project is None:
        return error(f"Project {project_id} not found", 404)
    limit, offset = parse_limit_offset()
    runs = db.session.scalars(
        select(ModelRun)
        .where(ModelRun.project_id == project_id)
        .order_by(ModelRun.created_at.desc(), ModelRun.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return jsonify([serialize_model_run(run) for run in runs])


@api.post("/projects/<int:project_id>/model-runs")
def create_model_run(project_id: int):
    project = db.session.get(Project, project_id)
    if project is None:
        return error(f"Project {project_id} not found", 404)
    run = build_model_run(project_id, json_body())
    db.session.add(run)
    db.session.flush()
    run.project = project
    ensure_run_storage(_app(), run)
    db.session.commit()
    return jsonify(serialize_model_run(run)), 201


@api.post("/projects/<int:project_id>/model-runs/upload")
def upload_model_run(project_id: int):
    project = db.session.get(Project, project_id)
    if project is None:
        return error(f"Project {project_id} not found", 404)

    payload = json_body()
    run_payload = payload.get("run")
    results_payload = payload.get("results")
    if not isinstance(run_payload, dict):
        raise ValueError("run is required")
    if not isinstance(results_payload, list):
        raise ValueError("results must be an array")

    run = build_model_run(project_id, run_payload)
    db.session.add(run)
    db.session.flush()
    run.project = project
    ensure_run_storage(_app(), run)
    write_run_upload_payload(_app(), run, payload)

    runtime = evaluation_runtime()
    run_cases: list[RunCase] = []
    seen_question_ids: set[int] = set()
    for item in results_payload:
        if not isinstance(item, dict):
            raise ValueError("Each result must be an object")
        dialect = (
            item.get("dialect") if isinstance(item.get("dialect"), str) else "sqlite"
        )
        if item.get("schema") is not None:
            dataset_asset = find_or_create_dataset_asset(
                _app(),
                session=db.session,
                name=require_string(item, "dataset"),
                description=f"Canonical schema payload for {require_string(item, 'dataset')}",
                dialect=dialect,
                source_type="run_case_schema",
                source_payload=_run_case_dataset_source_payload(
                    dataset=require_string(item, "dataset"),
                    dialect=dialect,
                    payload=item,
                ),
            )
            item = {**item, "datasetAssetId": dataset_asset.id}
            if run.dataset_asset_id is None:
                run.dataset_asset_id = dataset_asset.id
        run_case = build_run_case(run.id, item, source="upload")
        if run_case.question_id is not None:
            if run_case.question_id in seen_question_ids:
                raise ValueError(
                    f"Duplicate question_id {run_case.question_id} in upload results"
                )
            seen_question_ids.add(run_case.question_id)
        run_cases.append(run_case)

    db.session.add_all(run_cases)
    db.session.flush()
    db.session.commit()

    persisted_run_cases: list[RunCase] = []
    for run_case in run_cases:
        persisted_run_case = db.session.get(RunCase, run_case.id)
        if persisted_run_case is None:
            raise RuntimeError(f"RunCase {run_case.id} not found after commit")
        persisted_run_cases.append(persisted_run_case)

    runtime.submission_service.submit_runcases(persisted_run_cases)

    persisted_run = db.session.get(ModelRun, run.id)
    if persisted_run is None:
        raise RuntimeError(f"ModelRun {run.id} not found after commit")
    return jsonify(serialize_model_run(persisted_run)), 201


@api.post("/projects/<int:project_id>/model-runs/<int:model_run_id>/evaluations")
def create_evaluation_job(project_id: int, model_run_id: int):
    run = db.session.get(ModelRun, model_run_id)
    if run is None or run.project_id != project_id:
        return error(f"ModelRun {model_run_id} not found", 404)
    runtime = evaluation_runtime()
    job = runtime.submission_service.submit(json_body(), project_id, model_run_id)
    return jsonify(serialize_evaluation_job(job)), 202


@api.get("/projects/<int:project_id>/model-runs/<int:model_run_id>/evaluations")
def list_evaluation_jobs(project_id: int, model_run_id: int):
    run = db.session.get(ModelRun, model_run_id)
    if run is None or run.project_id != project_id:
        return error(f"ModelRun {model_run_id} not found", 404)
    limit, offset = parse_limit_offset()
    jobs = db.session.scalars(
        select(EvaluationJob)
        .where(EvaluationJob.run_id == model_run_id)
        .order_by(EvaluationJob.queued_at.desc(), EvaluationJob.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return jsonify([serialize_evaluation_job(job) for job in jobs])


@api.get("/evaluations/<int:evaluation_job_id>")
def get_evaluation_job(evaluation_job_id: int):
    job = db.session.get(EvaluationJob, evaluation_job_id)
    if job is None:
        return error(f"EvaluationJob {evaluation_job_id} not found", 404)
    return jsonify(serialize_evaluation_job(job))


@api.delete("/projects/<int:project_id>/model-runs/<int:model_run_id>")
def delete_model_run(project_id: int, model_run_id: int):
    run = db.session.get(ModelRun, model_run_id)
    if run is None or run.project_id != project_id:
        return error(f"ModelRun {model_run_id} not found", 404)
    delete_logical_path(_app(), run.storage_path)
    db.session.delete(run)
    db.session.flush()
    prune_orphan_db_connections()
    db.session.commit()
    return "", 204


@api.get("/model-runs/<int:model_run_id>")
def get_model_run(model_run_id: int):
    run = db.session.get(ModelRun, model_run_id)
    if run is None:
        return error(f"ModelRun {model_run_id} not found", 404)
    return jsonify(serialize_model_run(run))


@api.get("/model-runs/<int:model_run_id>/eval-records")
def list_eval_records(model_run_id: int):
    run = db.session.get(ModelRun, model_run_id)
    if run is None:
        return error(f"ModelRun {model_run_id} not found", 404)
    limit, offset = parse_limit_offset()
    run_cases = db.session.scalars(
        select(RunCase)
        .where(RunCase.run_id == model_run_id)
        .order_by(RunCase.question_id, RunCase.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return jsonify(
        [serialize_run_case_as_eval_record(run_case) for run_case in run_cases]
    )


@api.get("/model-runs/<int:model_run_id>/relaxed-equivalence-record")
def get_relaxed_equivalence_record(model_run_id: int):
    run = db.session.get(ModelRun, model_run_id)
    if run is None:
        return error(f"ModelRun {model_run_id} not found", 404)
    dataset = request.args.get("dataset")
    db_id = request.args.get("db-id")
    question_id = request.args.get("question-id")
    if not dataset or not db_id or question_id is None:
        raise ValueError("dataset, db-id, and question-id are required")
    try:
        question_id_int = int(question_id)
    except ValueError as exc:
        raise ValueError("question-id must be an integer") from exc

    run_case = db.session.scalar(
        select(RunCase).where(
            RunCase.run_id == model_run_id,
            RunCase.dataset == dataset,
            RunCase.db_id == db_id,
            RunCase.question_id == question_id_int,
        )
    )
    if run_case is None:
        return error("RelaxedEquivalenceRecord not found", 404)

    latest_job = db.session.scalar(
        select(EvaluationJob)
        .where(
            EvaluationJob.run_id == model_run_id,
            EvaluationJob.run_case_id == run_case.id,
        )
        .order_by(
            EvaluationJob.finished_at.desc().nullslast(),
            EvaluationJob.started_at.desc().nullslast(),
            EvaluationJob.queued_at.desc(),
            EvaluationJob.id.desc(),
        )
    )
    if latest_job is None:
        return error("RelaxedEquivalenceRecord not found", 404)

    record = db.session.scalar(
        select(RelaxedEquivalence)
        .options(selectinload(RelaxedEquivalence.counterexamples))
        .where(RelaxedEquivalence.evaluation_job_id == latest_job.id)
    )
    if record is not None:
        return jsonify(serialize_relaxed_equivalence(record))

    result = latest_job.result_json if isinstance(latest_job.result_json, dict) else {}
    if latest_job.status == "error" or result.get("state") == "failed":
        return jsonify(
            {
                "runId": latest_job.run_id,
                "dataset": run_case.dataset,
                "db_id": run_case.db_id,
                "question_id": run_case.question_id,
                "host_or_path": run_case.host_or_path,
                "gold": run_case.gold,
                "pred": run_case.pred,
                "counterexamples": [],
                "counternexample": [],
                "error": latest_job.error_message or result.get("error"),
                "state": "error",
            }
        )

    return error("No relaxed equivalence details available for this query pair", 404)
