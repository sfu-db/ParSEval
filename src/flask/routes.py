from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select

from .models import (
    EvaluationJob,
    ModelRun,
    Project,
    RelaxedEquivalence,
    RunCase,
    db,
    ensure_db_connection_info,
    prune_orphan_db_connections,
)
from .schemas import (
    normalize_metric,
    serialize_evaluation_job,
    serialize_model_run,
    serialize_project,
    serialize_relaxed_equivalence,
    serialize_run_case_as_eval_record,
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


def slugify_filename(value: str | None, default: str) -> str:
    base = (value or default).strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", base).strip("-._")
    return slug or default


def save_uploaded_payload(
    project_id: int, run: ModelRun, payload: dict[str, Any]
) -> str:
    static_root = Path(current_app.root_path) / "static" / "upload"
    static_root.mkdir(parents=True, exist_ok=True)
    filename = f"project-{project_id}-run-{run.id}-{slugify_filename(run.run_name, run.model)}.json"
    file_path = static_root / filename
    file_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return f"/static/upload/{filename}"


def delete_relative_file(relative_url: str | None) -> None:
    if not relative_url:
        return
    file_path = Path(current_app.root_path) / relative_url.lstrip("/")
    if file_path.is_file():
        file_path.unlink()


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
        root_path=current_app.root_path,
        dataset=dataset,
        db_id=db_id,
        dialect=dialect,
        host_or_path=optional_string(payload, "host_or_path"),
    )
    return RunCase(
        run_id=run_id,
        db_connection_id=connection.id,
        question_id=question_id,
        db_id=db_id,
        dataset=dataset,
        host_or_path_legacy=connection.host_or_path,
        dialect=dialect,
        schema_json=payload.get("schema"),
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
        gold=require_string(payload, "gold"),
        pred=require_string(payload, "pred"),
        source=source,
    )


def evaluation_runtime():
    runtime = current_app.extensions.get("evaluation_runtime")
    if runtime is None:
        raise RuntimeError("Evaluation runtime is not configured")
    return runtime


@api.errorhandler(ValueError)
def handle_value_error(error: ValueError):
    return jsonify({"error": str(error)}), 400


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
    for run in project.runs:
        delete_relative_file(run.uploaded_file_path)
        for job in run.evaluation_jobs:
            delete_relative_file(job.artifact_path)
    db.session.delete(project)
    db.session.flush()
    prune_orphan_db_connections()
    db.session.commit()
    return "", 204


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
    if db.session.get(Project, project_id) is None:
        return error(f"Project {project_id} not found", 404)
    run = build_model_run(project_id, json_body())
    db.session.add(run)
    db.session.commit()
    return jsonify(serialize_model_run(run)), 201


@api.post("/projects/<int:project_id>/model-runs/upload")
def upload_model_run(project_id: int):
    if db.session.get(Project, project_id) is None:
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
    run.uploaded_file_path = save_uploaded_payload(project_id, run, payload)

    runtime = evaluation_runtime()
    run_cases: list[RunCase] = []
    seen_question_ids: set[int] = set()
    for item in results_payload:
        if not isinstance(item, dict):
            raise ValueError("Each result must be an object")
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

    for run_case in run_cases:
        persisted_run_case = db.session.get(RunCase, run_case.id)
        if persisted_run_case is None:
            raise RuntimeError(f"RunCase {run_case.id} not found after commit")
        runtime.submission_service.submit_runcase(persisted_run_case)

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
    delete_relative_file(run.uploaded_file_path)
    for job in run.evaluation_jobs:
        delete_relative_file(job.artifact_path)
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

    record = db.session.scalar(
        select(RelaxedEquivalence)
        .join(RelaxedEquivalence.evaluation_job)
        .join(EvaluationJob.run_case)
        .where(
            EvaluationJob.run_id == model_run_id,
            RunCase.dataset == dataset,
            RunCase.db_id == db_id,
            RunCase.question_id == question_id_int,
        )
    )
    if record is None:
        return error("RelaxedEquivalenceRecord not found", 404)
    return jsonify(serialize_relaxed_equivalence(record))
