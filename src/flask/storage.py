from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from flask import Flask
from sqlalchemy import inspect, text


_variant_lock_guard = threading.Lock()
_variant_locks: dict[str, threading.Lock] = {}


def slugify(value: str | None, default: str) -> str:
    import re

    base = (value or default).strip().lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", base).strip("-._")
    return slug or default


def artifact_root(app: Flask) -> Path:
    return Path(str(app.config["ARTIFACT_ROOT"]))


def project_storage_root(app: Flask) -> Path:
    return Path(str(app.config["PROJECT_STORAGE_ROOT"]))


def dataset_storage_root(app: Flask) -> Path:
    return Path(str(app.config["DATASET_STORAGE_ROOT"]))


def logical_path_for(app: Flask, path: Path) -> str:
    root = artifact_root(app)
    return path.relative_to(root).as_posix()


def resolve_logical_path(app: Flask, logical_path: str | None) -> Path | None:
    if not logical_path:
        return None
    return artifact_root(app) / logical_path


def delete_logical_path(app: Flask, logical_path: str | None) -> None:
    path = resolve_logical_path(app, logical_path)
    if path is None or not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            for child in sorted(path.rglob("*"), reverse=True):
                try:
                    if child.is_file() or child.is_symlink():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                except FileNotFoundError:
                    ...
                except OSError:
                    ...
            try:
                path.rmdir()
            except OSError:
                ...
    else:
        path.unlink()


def write_json_file(path: Path, payload: dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def read_json_file(path: Path) -> dict[str, Any] | list[Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if isinstance(payload, (dict, list)):
        return payload
    return None


def fingerprint_payload(payload: Any) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@contextmanager
def variant_lock(key: str) -> Iterator[None]:
    with _variant_lock_guard:
        lock = _variant_locks.setdefault(key, threading.Lock())
    with lock:
        yield


def ensure_project_storage(app: Flask, project) -> Path:
    project.storage_slug = project.storage_slug or slugify(project.name, "project")
    project.storage_path = project.storage_path or (
        f"projects/project-{project.id}-{project.storage_slug}"
    )
    project.metadata_path = project.metadata_path or (
        f"{project.storage_path}/project.json"
    )
    root = artifact_root(app) / project.storage_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def ensure_run_storage(app: Flask, run) -> Path:
    project_root = ensure_project_storage(app, run.project)
    run.storage_slug = run.storage_slug or slugify(run.run_name, run.model or "run")
    run.storage_path = run.storage_path or (
        f"{run.project.storage_path}/runs/run-{run.id}-{run.storage_slug}"
    )
    run.metadata_path = run.metadata_path or f"{run.storage_path}/run.json"
    root = artifact_root(app) / run.storage_path
    root.mkdir(parents=True, exist_ok=True)
    (root / "upload").mkdir(parents=True, exist_ok=True)
    (root / "eval").mkdir(parents=True, exist_ok=True)
    return root


def write_run_upload_payload(app: Flask, run, payload: dict[str, Any]) -> str:
    ensure_run_storage(app, run)
    logical = f"{run.storage_path}/upload/source_payload.json"
    write_json_file(artifact_root(app) / logical, payload)
    run.uploaded_file_path = logical
    return logical


def write_run_metadata(app: Flask, run) -> None:
    ensure_run_storage(app, run)


def write_project_metadata(app: Flask, project) -> None:
    ensure_project_storage(app, project)


def write_evaluation_artifact(
    app: Flask, job, payload: dict[str, Any]
) -> str:
    ensure_run_storage(app, job.run)
    logical = f"{job.run.storage_path}/eval/job-{job.id}.json"
    write_json_file(artifact_root(app) / logical, payload)
    return logical


def ensure_dataset_asset_storage(app: Flask, dataset_asset) -> Path:
    dataset_asset.slug = dataset_asset.slug or slugify(dataset_asset.name, "dataset")
    dataset_asset.storage_path = dataset_asset.storage_path or (
        f"datasets/dataset-{dataset_asset.id}-{dataset_asset.slug}"
    )
    dataset_asset.metadata_path = dataset_asset.metadata_path or (
        f"{dataset_asset.storage_path}/dataset.json"
    )
    root = artifact_root(app) / dataset_asset.storage_path
    (root / "source").mkdir(parents=True, exist_ok=True)
    (root / "variants").mkdir(parents=True, exist_ok=True)
    return root


def write_dataset_asset_source_payload(
    app: Flask, dataset_asset, payload: dict[str, Any]
) -> str:
    ensure_dataset_asset_storage(app, dataset_asset)
    logical = f"{dataset_asset.storage_path}/source/upload.json"
    write_json_file(artifact_root(app) / logical, payload)
    dataset_asset.source_payload_path = logical
    return logical


def write_dataset_asset_metadata(app: Flask, dataset_asset) -> None:
    ensure_dataset_asset_storage(app, dataset_asset)


def ensure_dataset_variant_storage(app: Flask, dataset_variant) -> Path:
    asset = dataset_variant.dataset_asset
    ensure_dataset_asset_storage(app, asset)
    dataset_variant.storage_path = dataset_variant.storage_path or (
        f"{asset.storage_path}/variants/{dataset_variant.variant_key}"
    )
    dataset_variant.manifest_path = dataset_variant.manifest_path or (
        f"{dataset_variant.storage_path}/manifest.json"
    )
    root = artifact_root(app) / dataset_variant.storage_path
    (root / "databases").mkdir(parents=True, exist_ok=True)
    return root


def write_dataset_variant_manifest(
    app: Flask, dataset_variant, payload: dict[str, Any]
) -> str:
    ensure_dataset_variant_storage(app, dataset_variant)
    logical = dataset_variant.manifest_path
    write_json_file(artifact_root(app) / logical, payload)
    return logical


def read_dataset_variant_manifest(app: Flask, dataset_variant) -> dict[str, Any]:
    ensure_dataset_variant_storage(app, dataset_variant)
    manifest_path = artifact_root(app) / str(dataset_variant.manifest_path)
    if not manifest_path.exists():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def read_logical_json(app: Flask, logical_path: str | None) -> dict[str, Any] | list[Any] | None:
    path = resolve_logical_path(app, logical_path)
    if path is None or not path.exists():
        return None
    return read_json_file(path)


def read_dataset_asset_source_payload(app: Flask, dataset_asset) -> dict[str, Any]:
    payload = read_logical_json(app, dataset_asset.source_payload_path)
    return payload if isinstance(payload, dict) else {}


def find_or_create_dataset_asset(
    app: Flask,
    *,
    session,
    name: str,
    description: str | None,
    dialect: str,
    source_type: str,
    source_payload: dict[str, Any],
):
    from sqlalchemy import select

    from .models import DatasetAsset

    source_fingerprint = fingerprint_payload(source_payload)
    dataset_asset = session.scalar(
        select(DatasetAsset).where(
            DatasetAsset.dialect == dialect,
            DatasetAsset.source_type == source_type,
            DatasetAsset.source_fingerprint == source_fingerprint,
        )
    )
    if dataset_asset is None:
        dataset_asset = DatasetAsset(
            name=name,
            description=description,
            dialect=dialect,
            source_type=source_type,
            source_fingerprint=source_fingerprint,
            status="ready",
        )
        session.add(dataset_asset)
        session.flush()

    ensure_dataset_asset_storage(app, dataset_asset)
    if not dataset_asset.source_payload_path:
        write_dataset_asset_source_payload(app, dataset_asset, source_payload)
    return dataset_asset


def load_run_case_schema(app: Flask, run_case) -> Any | None:
    dataset_asset = run_case.default_dataset_asset or run_case.run.dataset_asset
    if dataset_asset is None:
        return None
    payload = read_dataset_asset_source_payload(app, dataset_asset)
    return payload.get("schema")


def list_reusable_sqlite_database_files(root: Path) -> list[Path]:
    databases_dir = root if root.name == "databases" else root / "databases"
    if not databases_dir.exists():
        return []
    files: list[Path] = []
    for file_path in sorted(databases_dir.glob("*.sqlite")):
        if file_path.name.endswith("_syntax_check.sqlite"):
            continue
        if not _sqlite_has_tables(file_path):
            continue
        files.append(file_path)
    return files


def sync_dataset_variant_databases(
    app: Flask,
    dataset_variant,
    *,
    dialect: str = "sqlite",
) -> list[str]:
    root = ensure_dataset_variant_storage(app, dataset_variant)
    existing_manifest = read_dataset_variant_manifest(app, dataset_variant)
    databases = existing_manifest.get("databases")
    if not isinstance(databases, list):
        databases = []

    if dialect.strip().lower() == "sqlite":
        databases = [
            logical_path_for(app, file_path)
            for file_path in list_reusable_sqlite_database_files(root)
        ]

    payload = {
        "datasetAssetId": dataset_variant.dataset_asset_id,
        "datasetVariantId": dataset_variant.id,
        "variantKey": dataset_variant.variant_key,
        "schemaFingerprint": dataset_variant.schema_fingerprint,
        "workloadFingerprint": dataset_variant.workload_fingerprint,
        "settingsFingerprint": dataset_variant.settings_fingerprint,
        "dialect": dialect,
        "databases": databases,
    }
    write_dataset_variant_manifest(app, dataset_variant, payload)
    return databases


def ensure_placeholder_sqlite_database(
    app: Flask,
    dataset_variant,
    db_id: str,
    *,
    schema: Any | None = None,
    dialect: str = "sqlite",
) -> str | None:
    root = ensure_dataset_variant_storage(app, dataset_variant)
    file_path = root / "databases" / f"{slugify(db_id, 'database')}.sqlite"
    file_exists = file_path.exists()
    if not file_exists:
        sqlite3.connect(file_path).close()

    ddl_statements = _schema_to_ddl_statements(schema)
    if not ddl_statements:
        if not file_exists:
            try:
                file_path.unlink()
            except FileNotFoundError:
                ...
            return None
        if not _sqlite_has_tables(file_path):
            return None
        return logical_path_for(app, file_path)

    if not _sqlite_has_tables(file_path):
        db_manager_cls = _load_db_manager()
        with db_manager_cls().get_connection(
            host_or_path=str(file_path.parent),
            database=file_path.name,
            dialect=dialect,
        ) as conn:
            conn.create_tables(*ddl_statements)
    return logical_path_for(app, file_path)


def backfill_storage_metadata(app: Flask, *, models_module) -> None:
    artifact_root(app).mkdir(parents=True, exist_ok=True)
    project_storage_root(app).mkdir(parents=True, exist_ok=True)
    dataset_storage_root(app).mkdir(parents=True, exist_ok=True)

    for project in app.extensions.get("sqlalchemy").session.query(models_module.Project):
        ensure_project_storage(app, project)

    for run in app.extensions.get("sqlalchemy").session.query(models_module.ModelRun):
        ensure_run_storage(app, run)
        legacy_upload = _legacy_root_file(app, run.uploaded_file_path)
        if legacy_upload is not None and legacy_upload.is_file():
            target = artifact_root(app) / f"{run.storage_path}/upload/source_payload.json"
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy_upload), str(target))
            run.uploaded_file_path = logical_path_for(app, target)
        write_run_metadata(app, run)

    for job in app.extensions.get("sqlalchemy").session.query(models_module.EvaluationJob):
        legacy_artifact = _legacy_root_file(app, job.artifact_path)
        if legacy_artifact is not None and legacy_artifact.is_file():
            ensure_run_storage(app, job.run)
            target = artifact_root(app) / f"{job.run.storage_path}/eval/job-{job.id}.json"
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy_artifact), str(target))
            job.artifact_path = logical_path_for(app, target)

    _backfill_legacy_payload_refs(app)
    app.extensions.get("sqlalchemy").session.commit()


def _legacy_root_file(app: Flask, logical_path: str | None) -> Path | None:
    if not logical_path:
        return None
    path = Path(logical_path)
    if path.is_absolute():
        return path
    legacy = Path(app.root_path) / logical_path.lstrip("/")
    if legacy.exists():
        return legacy
    candidate = artifact_root(app) / logical_path
    if candidate.exists():
        return candidate
    return None


def _isoformat(value) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _sqlite_has_tables(file_path: Path) -> bool:
    with sqlite3.connect(file_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()
    return bool(row and row[0])


def _schema_to_ddl_statements(schema: Any | None) -> list[str]:
    if schema is None:
        return []
    if isinstance(schema, str):
        return [stmt.strip() for stmt in schema.split(";") if stmt.strip()]
    if isinstance(schema, list):
        statements: list[str] = []
        for item in schema:
            if isinstance(item, str):
                statements.extend(stmt.strip() for stmt in item.split(";") if stmt.strip())
        return statements
    return []


def _load_db_manager():
    try:
        from parseval.db_manager import DBManager
    except ModuleNotFoundError:
        from src.parseval.db_manager import DBManager
    return DBManager


def _backfill_legacy_payload_refs(app: Flask) -> None:
    session = app.extensions.get("sqlalchemy").session
    inspector = inspect(app.extensions.get("sqlalchemy").engine)
    run_case_columns = {column["name"] for column in inspector.get_columns("run_cases")}
    if "schema_json" in run_case_columns:
        rows = session.execute(
            text(
                """
                SELECT id, dataset, dialect, schema_json, default_dataset_asset_id,
                       gold, pred
                FROM run_cases
                WHERE default_dataset_asset_id IS NULL AND schema_json IS NOT NULL
                """
            )
        ).mappings()
        for row in rows:
            try:
                schema = json.loads(row["schema_json"])
            except (TypeError, ValueError):
                schema = row["schema_json"]
            source_payload = {
                "name": row["dataset"],
                "description": f"Canonical schema payload for {row['dataset']}",
                "dialect": row["dialect"] or "sqlite",
                "schema": schema,
                "queries": None,
                "workload": None,
                "settings": {},
            }
            dataset_asset = find_or_create_dataset_asset(
                app,
                session=session,
                name=row["dataset"],
                description=f"Canonical schema payload for {row['dataset']}",
                dialect=row["dialect"] or "sqlite",
                source_type="legacy_run_case_schema",
                source_payload=source_payload,
            )
            session.execute(
                text(
                    """
                    UPDATE run_cases
                    SET default_dataset_asset_id = :dataset_asset_id,
                        schema_fingerprint = :schema_fingerprint,
                        gold_fingerprint = :gold_fingerprint,
                        pred_fingerprint = :pred_fingerprint
                    WHERE id = :id
                    """
                ),
                {
                    "dataset_asset_id": dataset_asset.id,
                    "schema_fingerprint": fingerprint_payload(schema),
                    "gold_fingerprint": fingerprint_payload(row["gold"]),
                    "pred_fingerprint": fingerprint_payload(row["pred"]),
                    "id": row["id"],
                },
            )

    counter_columns = {column["name"] for column in inspector.get_columns("counterexamples")}
    if "artifact_key" in counter_columns:
        session.execute(
            text(
                """
                UPDATE counterexamples
                SET artifact_key = COALESCE(
                    artifact_key,
                    'db=' || COALESCE(db_level, 'PK_FK') || '|query=' || COALESCE(query_level, 'BAG')
                )
                """
            )
        )
