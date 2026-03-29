from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, inspect, select, text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fingerprint_text_payload(payload) -> str | None:
    if payload is None:
        return None
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class DBConnectionInfo(db.Model):
    __tablename__ = "db_connection_info"
    __table_args__ = (
        UniqueConstraint(
            "dataset",
            "db_id",
            "dialect",
            "host",
            name="uq_db_connection_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(db.String(255), nullable=False)
    dataset: Mapped[str | None] = mapped_column(db.String(255))
    db_id: Mapped[str | None] = mapped_column(db.String(255))
    host: Mapped[str] = mapped_column(db.String(255), nullable=False)
    port: Mapped[int] = mapped_column(nullable=False)
    username: Mapped[str] = mapped_column(db.String(255), nullable=False)
    password: Mapped[str] = mapped_column(db.String(255), nullable=False)
    database: Mapped[str] = mapped_column(db.String(255), nullable=True)
    dialect: Mapped[str] = mapped_column(db.String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    run_cases: Mapped[list["RunCase"]] = relationship(
        back_populates="db_connection_info"
    )

    @property
    def host_or_path(self) -> str:
        if self.dialect == "sqlite":
            return str(Path(self.host) / self.database) if self.database else self.host
        if self.port:
            return f"{self.host}:{self.port}"
        return self.host


class Project(db.Model):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(db.String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(db.Text)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    storage_slug: Mapped[str | None] = mapped_column(db.String(255))
    storage_path: Mapped[str | None] = mapped_column(db.Text)
    metadata_path: Mapped[str | None] = mapped_column(db.Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    runs: Mapped[list["ModelRun"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    dataset_links: Mapped[list["ProjectDataset"]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class ModelRun(db.Model):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        db.ForeignKey("projects.id"), nullable=False
    )
    model: Mapped[str] = mapped_column(db.String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        db.String(64), default="pending", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    run_name: Mapped[str | None] = mapped_column(db.String(255))
    dataset: Mapped[str] = mapped_column(db.String(255), nullable=False)
    prompt_template: Mapped[str | None] = mapped_column(db.Text)
    uploaded_file_path: Mapped[str | None] = mapped_column(db.Text)
    storage_slug: Mapped[str | None] = mapped_column(db.String(255))
    storage_path: Mapped[str | None] = mapped_column(db.Text)
    metadata_path: Mapped[str | None] = mapped_column(db.Text)
    dataset_asset_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("dataset_assets.id")
    )
    metric_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    setting_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    project: Mapped[Project] = relationship(back_populates="runs")
    dataset_asset: Mapped["DatasetAsset | None"] = relationship(
        back_populates="model_runs"
    )
    run_cases: Mapped[list["RunCase"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    evaluation_jobs: Mapped[list["EvaluationJob"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class DatasetAsset(db.Model):
    __tablename__ = "dataset_assets"
    __table_args__ = (
        UniqueConstraint(
            "dialect",
            "source_type",
            "source_fingerprint",
            name="uq_dataset_asset_source",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(db.String(255), nullable=False)
    slug: Mapped[str | None] = mapped_column(db.String(255))
    description: Mapped[str | None] = mapped_column(db.Text)
    dialect: Mapped[str] = mapped_column(db.String(64), default="sqlite", nullable=False)
    source_type: Mapped[str] = mapped_column(
        db.String(64), default="uploaded_json", nullable=False
    )
    source_fingerprint: Mapped[str | None] = mapped_column(db.String(128))
    source_payload_path: Mapped[str | None] = mapped_column(db.Text)
    storage_path: Mapped[str | None] = mapped_column(db.Text)
    metadata_path: Mapped[str | None] = mapped_column(db.Text)
    status: Mapped[str] = mapped_column(db.String(64), default="ready", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    variants: Mapped[list["DatasetVariant"]] = relationship(
        back_populates="dataset_asset", cascade="all, delete-orphan"
    )
    project_links: Mapped[list["ProjectDataset"]] = relationship(
        back_populates="dataset_asset", cascade="all, delete-orphan"
    )
    model_runs: Mapped[list[ModelRun]] = relationship(back_populates="dataset_asset")
    run_cases: Mapped[list["RunCase"]] = relationship(
        back_populates="default_dataset_asset"
    )


class DatasetVariant(db.Model):
    __tablename__ = "dataset_variants"
    __table_args__ = (
        UniqueConstraint("dataset_asset_id", "variant_key", name="uq_dataset_variant"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_asset_id: Mapped[int] = mapped_column(
        db.ForeignKey("dataset_assets.id"), nullable=False
    )
    variant_key: Mapped[str] = mapped_column(db.String(255), nullable=False)
    schema_fingerprint: Mapped[str | None] = mapped_column(db.String(128))
    workload_fingerprint: Mapped[str | None] = mapped_column(db.String(128))
    settings_fingerprint: Mapped[str | None] = mapped_column(db.String(128))
    storage_path: Mapped[str | None] = mapped_column(db.Text)
    manifest_path: Mapped[str | None] = mapped_column(db.Text)
    status: Mapped[str] = mapped_column(
        db.String(64), default="generated", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    dataset_asset: Mapped[DatasetAsset] = relationship(back_populates="variants")
    evaluation_jobs: Mapped[list["EvaluationJob"]] = relationship(
        back_populates="dataset_variant"
    )


class ProjectDataset(db.Model):
    __tablename__ = "project_datasets"
    __table_args__ = (
        UniqueConstraint("project_id", "dataset_asset_id", name="uq_project_dataset"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        db.ForeignKey("projects.id"), nullable=False
    )
    dataset_asset_id: Mapped[int] = mapped_column(
        db.ForeignKey("dataset_assets.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    project: Mapped[Project] = relationship(back_populates="dataset_links")
    dataset_asset: Mapped[DatasetAsset] = relationship(back_populates="project_links")


class RunCase(db.Model):
    __tablename__ = "run_cases"
    __table_args__ = (
        UniqueConstraint("run_id", "question_id", name="uq_run_case_question"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(db.ForeignKey("model_runs.id"), nullable=False)
    db_connection_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("db_connection_info.id")
    )
    default_dataset_asset_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("dataset_assets.id")
    )
    question_id: Mapped[int | None] = mapped_column(nullable=True)
    db_id: Mapped[str] = mapped_column(db.String(255), nullable=False)
    dataset: Mapped[str] = mapped_column(db.String(255), nullable=False)
    host_or_path_legacy: Mapped[str] = mapped_column(
        "host_or_path", db.Text, nullable=False, default=""
    )
    dialect: Mapped[str | None] = mapped_column(db.String(255))
    schema_fingerprint: Mapped[str | None] = mapped_column(db.String(128))
    question: Mapped[str | None] = mapped_column(db.Text)
    evidence: Mapped[str | None] = mapped_column(db.Text)
    prompt: Mapped[str | None] = mapped_column(db.Text)
    gold: Mapped[str] = mapped_column(db.Text, nullable=False)
    pred: Mapped[str] = mapped_column(db.Text, nullable=False)
    gold_fingerprint: Mapped[str | None] = mapped_column(db.String(128))
    pred_fingerprint: Mapped[str | None] = mapped_column(db.String(128))
    source: Mapped[str] = mapped_column(db.String(64), default="upload", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)

    run: Mapped[ModelRun] = relationship(back_populates="run_cases")
    db_connection_info: Mapped[DBConnectionInfo | None] = relationship(
        back_populates="run_cases"
    )
    default_dataset_asset: Mapped[DatasetAsset | None] = relationship(
        back_populates="run_cases"
    )
    evaluation_jobs: Mapped[list["EvaluationJob"]] = relationship(
        back_populates="run_case", cascade="all, delete-orphan"
    )

    @property
    def host_or_path(self) -> str:
        if self.db_connection_info is None:
            return ""
        return self.db_connection_info.host_or_path


class EvaluationJob(db.Model):
    __tablename__ = "evaluation_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(db.ForeignKey("model_runs.id"), nullable=False)
    run_case_id: Mapped[int] = mapped_column(
        db.ForeignKey("run_cases.id"), nullable=False
    )
    dataset_variant_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("dataset_variants.id")
    )
    status: Mapped[str] = mapped_column(
        db.String(64), default="pending", nullable=False
    )
    queued_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column()
    finished_at: Mapped[datetime | None] = mapped_column()
    error_message: Mapped[str | None] = mapped_column(db.Text)
    result_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    artifact_path: Mapped[str | None] = mapped_column(db.Text)

    run: Mapped[ModelRun] = relationship(back_populates="evaluation_jobs")
    run_case: Mapped[RunCase] = relationship(back_populates="evaluation_jobs")
    dataset_variant: Mapped["DatasetVariant | None"] = relationship(
        back_populates="evaluation_jobs"
    )
    relaxed_equivalence: Mapped["RelaxedEquivalence | None"] = relationship(
        back_populates="evaluation_job", cascade="all, delete-orphan", uselist=False
    )
    cache_entries: Mapped[list["EvaluationCacheEntry"]] = relationship(
        back_populates="latest_successful_job"
    )


class RelaxedEquivalence(db.Model):
    __tablename__ = "relaxed_equivalences"

    id: Mapped[int] = mapped_column(primary_key=True)
    evaluation_job_id: Mapped[int] = mapped_column(
        db.ForeignKey("evaluation_jobs.id"), nullable=False, unique=True
    )
    state: Mapped[str] = mapped_column(db.String(64), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow, onupdate=utcnow, nullable=False
    )

    evaluation_job: Mapped[EvaluationJob] = relationship(
        back_populates="relaxed_equivalence"
    )
    counterexamples: Mapped[list["CounterExample"]] = relationship(
        back_populates="equivalence", cascade="all, delete-orphan"
    )


class CounterExample(db.Model):
    __tablename__ = "counterexamples"
    __table_args__ = (
        UniqueConstraint(
            "equivalence_id",
            "db_level",
            "query_level",
            name="uq_counterexample_level",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    equivalence_id: Mapped[int] = mapped_column(
        db.ForeignKey("relaxed_equivalences.id"), nullable=False
    )
    db_level: Mapped[str] = mapped_column(db.String(64), nullable=False)
    query_level: Mapped[str] = mapped_column(db.String(64), nullable=False)
    state: Mapped[str] = mapped_column(db.String(64), default="pending", nullable=False)
    artifact_key: Mapped[str | None] = mapped_column(db.String(255))
    error_message: Mapped[str | None] = mapped_column(db.Text)

    equivalence: Mapped[RelaxedEquivalence] = relationship(
        back_populates="counterexamples"
    )


class EvaluationCacheEntry(db.Model):
    __tablename__ = "evaluation_cache_entries"
    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_evaluation_cache_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_variant_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("dataset_variants.id")
    )
    latest_successful_job_id: Mapped[int | None] = mapped_column(
        db.ForeignKey("evaluation_jobs.id")
    )
    cache_key: Mapped[str] = mapped_column(db.String(128), nullable=False)
    gold_fingerprint: Mapped[str] = mapped_column(db.String(128), nullable=False)
    pred_fingerprint: Mapped[str] = mapped_column(db.String(128), nullable=False)
    settings_fingerprint: Mapped[str] = mapped_column(db.String(128), nullable=False)
    state: Mapped[str] = mapped_column(db.String(64), nullable=False)
    result_summary_json: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    artifact_path: Mapped[str | None] = mapped_column(db.Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow, onupdate=utcnow, nullable=False
    )

    dataset_variant: Mapped["DatasetVariant | None"] = relationship()
    latest_successful_job: Mapped["EvaluationJob | None"] = relationship(
        back_populates="cache_entries"
    )


def migrate_legacy_flask_schema(root_path: str) -> None:
    engine = db.engine
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        if "projects" in tables:
            _maybe_add_column(conn, inspector, "projects", "storage_slug", "TEXT")
            _maybe_add_column(conn, inspector, "projects", "storage_path", "TEXT")
            _maybe_add_column(conn, inspector, "projects", "metadata_path", "TEXT")
        if "model_runs" in tables:
            _maybe_add_column(conn, inspector, "model_runs", "storage_slug", "TEXT")
            _maybe_add_column(conn, inspector, "model_runs", "storage_path", "TEXT")
            _maybe_add_column(conn, inspector, "model_runs", "metadata_path", "TEXT")
            _maybe_add_column(conn, inspector, "model_runs", "dataset_asset_id", "INTEGER")
        if "run_cases" in tables:
            _maybe_add_column(
                conn, inspector, "run_cases", "default_dataset_asset_id", "INTEGER"
            )
            _maybe_add_column(conn, inspector, "run_cases", "schema_fingerprint", "TEXT")
            _maybe_add_column(conn, inspector, "run_cases", "gold_fingerprint", "TEXT")
            _maybe_add_column(conn, inspector, "run_cases", "pred_fingerprint", "TEXT")
        if "evaluation_jobs" in tables:
            _maybe_add_column(
                conn, inspector, "evaluation_jobs", "dataset_variant_id", "INTEGER"
            )
        if "db_connection_info" in tables:
            _maybe_add_column(conn, inspector, "db_connection_info", "dataset", "TEXT")
            _maybe_add_column(conn, inspector, "db_connection_info", "db_id", "TEXT")
        if "evaluation_jobs" in tables:
            _maybe_add_column(
                conn, inspector, "evaluation_jobs", "run_case_id", "INTEGER"
            )
        if "run_cases" in tables:
            _maybe_add_column(
                conn, inspector, "run_cases", "db_connection_id", "INTEGER"
            )
        if "relaxed_equivalences" in tables:
            _maybe_add_column(
                conn, inspector, "relaxed_equivalences", "evaluation_job_id", "INTEGER"
            )
        if "counterexamples" in tables:
            _maybe_add_column(conn, inspector, "counterexamples", "db_level", "TEXT")
            _maybe_add_column(conn, inspector, "counterexamples", "query_level", "TEXT")
            _maybe_add_column(
                conn, inspector, "counterexamples", "error_message", "TEXT"
            )
            _maybe_add_column(conn, inspector, "counterexamples", "artifact_key", "TEXT")

        if "eval_records" in tables:
            rows = conn.execute(
                text(
                    """
                    SELECT run_id, question_id, db_id, dataset, host_or_path, schema_json,
                           question, evidence, gold, prompt, pred
                    FROM eval_records
                    """
                )
            ).mappings()
            for row in rows:
                _ensure_run_case(
                    conn,
                    root_path=root_path,
                    run_id=row["run_id"],
                    question_id=row["question_id"],
                    db_id=row["db_id"],
                    dataset=row["dataset"],
                    host_or_path=row["host_or_path"] or "",
                    dialect=None,
                    schema_json=_load_jsonish(row["schema_json"]),
                    question=row["question"],
                    evidence=row["evidence"],
                    prompt=row["prompt"],
                    gold=row["gold"],
                    pred=row["pred"],
                    source="upload",
                )

        inspector = inspect(engine)
        job_columns = (
            {column["name"] for column in inspector.get_columns("evaluation_jobs")}
            if "evaluation_jobs" in tables
            else set()
        )
        legacy_job_columns = {
            "gold",
            "pred",
            "ddls",
            "db_id",
            "dataset",
            "host_or_path",
            "question_id",
            "question",
            "evidence",
            "dialect",
        }
        if "run_case_id" in job_columns and legacy_job_columns.issubset(job_columns):
            rows = conn.execute(
                text(
                    """
                    SELECT id, run_id, run_case_id, gold, pred, ddls, db_id, dataset,
                           host_or_path, question_id, question, evidence, dialect
                    FROM evaluation_jobs
                    WHERE run_case_id IS NULL
                    """
                )
            ).mappings()
            for row in rows:
                run_case_id = _ensure_run_case(
                    conn,
                    root_path=root_path,
                    run_id=row["run_id"],
                    question_id=row["question_id"],
                    db_id=row["db_id"],
                    dataset=row["dataset"],
                    host_or_path=row["host_or_path"] or "",
                    dialect=row["dialect"],
                    schema_json=_load_jsonish(row["ddls"]),
                    question=row["question"],
                    evidence=row["evidence"],
                    prompt=None,
                    gold=row["gold"],
                    pred=row["pred"],
                    source="queued",
                )
                conn.execute(
                    text(
                        "UPDATE evaluation_jobs SET run_case_id = :run_case_id WHERE id = :id"
                    ),
                    {"run_case_id": run_case_id, "id": row["id"]},
                )

        inspector = inspect(engine)
        relaxed_columns = (
            {column["name"] for column in inspector.get_columns("relaxed_equivalences")}
            if "relaxed_equivalences" in tables
            else set()
        )
        legacy_relaxed_columns = {
            "run_id",
            "dataset",
            "db_id",
            "question_id",
            "gold",
            "pred",
        }
        if "evaluation_job_id" in relaxed_columns and legacy_relaxed_columns.issubset(
            relaxed_columns
        ):
            rows = conn.execute(
                text(
                    """
                    SELECT id, run_id, dataset, db_id, question_id, gold, pred
                    FROM relaxed_equivalences
                    WHERE evaluation_job_id IS NULL
                    """
                )
            ).mappings()
            for row in rows:
                job = (
                    conn.execute(
                        text(
                            """
                        SELECT ej.id
                        FROM evaluation_jobs ej
                        JOIN run_cases rc ON rc.id = ej.run_case_id
                        WHERE ej.run_id = :run_id
                          AND rc.dataset = :dataset
                          AND rc.db_id = :db_id
                          AND ((rc.question_id IS NULL AND :question_id IS NULL) OR rc.question_id = :question_id)
                          AND rc.gold = :gold
                          AND rc.pred = :pred
                        ORDER BY ej.id DESC
                        LIMIT 1
                        """
                        ),
                        row,
                    )
                    .mappings()
                    .first()
                )
                if job is not None:
                    conn.execute(
                        text(
                            "UPDATE relaxed_equivalences SET evaluation_job_id = :evaluation_job_id WHERE id = :id"
                        ),
                        {"evaluation_job_id": job["id"], "id": row["id"]},
                    )

        inspector = inspect(engine)
        run_case_columns = (
            {column["name"] for column in inspector.get_columns("run_cases")}
            if "run_cases" in tables
            else set()
        )
        if {"db_connection_id", "dataset", "db_id"}.issubset(run_case_columns):
            select_host = "host_or_path," if "host_or_path" in run_case_columns else ""
            rows = conn.execute(
                text(
                    f"""
                    SELECT id, dataset, db_id, dialect, {select_host} db_connection_id
                    FROM run_cases
                    WHERE db_connection_id IS NULL
                    """
                )
            ).mappings()
            for row in rows:
                db_connection_id = _ensure_db_connection(
                    conn,
                    root_path=root_path,
                    dataset=row["dataset"],
                    db_id=row["db_id"],
                    dialect=row["dialect"],
                    host_or_path=row.get("host_or_path") or "",
                )
                conn.execute(
                    text(
                        "UPDATE run_cases SET db_connection_id = :db_connection_id WHERE id = :id"
                    ),
                    {"db_connection_id": db_connection_id, "id": row["id"]},
                )

        inspector = inspect(engine)
        counter_columns = (
            {column["name"] for column in inspector.get_columns("counterexamples")}
            if "counterexamples" in tables
            else set()
        )
        if {"db_level", "query_level", "settings_json"}.issubset(counter_columns):
            rows = conn.execute(
                text(
                    """
                    SELECT id, settings_json
                    FROM counterexamples
                    WHERE db_level IS NULL OR query_level IS NULL
                    """
                )
            ).mappings()
            for row in rows:
                payload = _load_jsonish(row["settings_json"]) or []
                first = (
                    payload[0]
                    if isinstance(payload, list)
                    and payload
                    and isinstance(payload[0], dict)
                    else {}
                )
                conn.execute(
                    text(
                        "UPDATE counterexamples SET db_level = :db_level, query_level = :query_level WHERE id = :id"
                    ),
                    {
                        "db_level": first.get("db_level", "PK_FK"),
                        "query_level": first.get("query_level", "BAG"),
                        "id": row["id"],
                    },
                )


def _ensure_run_case(
    conn,
    *,
    root_path: str,
    run_id: int,
    question_id: int | None,
    db_id: str,
    dataset: str,
    host_or_path: str,
    dialect: str | None,
    schema_json,
    question: str | None,
    evidence: str | None,
    prompt: str | None,
    gold: str,
    pred: str,
    source: str,
) -> int:
    if question_id is not None:
        existing = conn.execute(
            text(
                "SELECT id FROM run_cases WHERE run_id = :run_id AND question_id = :question_id LIMIT 1"
            ),
            {"run_id": run_id, "question_id": question_id},
        ).scalar_one_or_none()
    else:
        existing = conn.execute(
            text(
                """
                SELECT id FROM run_cases
                WHERE run_id = :run_id
                  AND question_id IS NULL
                  AND db_id = :db_id
                  AND dataset = :dataset
                  AND gold = :gold
                  AND pred = :pred
                LIMIT 1
                """
            ),
            {
                "run_id": run_id,
                "db_id": db_id,
                "dataset": dataset,
                "gold": gold,
                "pred": pred,
            },
        ).scalar_one_or_none()
    if existing is not None:
        return int(existing)

    inserted = conn.execute(
        text(
            """
            INSERT INTO run_cases (
                run_id, db_connection_id, question_id, db_id, dataset, host_or_path, dialect, schema_fingerprint,
                question, evidence, prompt, gold, pred, gold_fingerprint, pred_fingerprint, source, created_at
            ) VALUES (
                :run_id, :db_connection_id, :question_id, :db_id, :dataset, :host_or_path, :dialect, :schema_fingerprint,
                :question, :evidence, :prompt, :gold, :pred, :gold_fingerprint, :pred_fingerprint, :source, :created_at
            )
            """
        ),
        {
            "host_or_path": _normalize_connection_fields(
                root_path=root_path,
                dataset=dataset,
                db_id=db_id,
                dialect=dialect,
                host_or_path=host_or_path,
            )["host_or_path"],
            "run_id": run_id,
            "db_connection_id": _ensure_db_connection(
                conn,
                root_path=root_path,
                dataset=dataset,
                db_id=db_id,
                dialect=dialect,
                host_or_path=host_or_path,
            ),
            "question_id": question_id,
            "db_id": db_id,
            "dataset": dataset,
            "dialect": dialect,
            "schema_fingerprint": fingerprint_text_payload(schema_json),
            "question": question,
            "evidence": evidence,
            "prompt": prompt,
            "gold": gold,
            "pred": pred,
            "gold_fingerprint": fingerprint_text_payload(gold),
            "pred_fingerprint": fingerprint_text_payload(pred),
            "source": source,
            "created_at": utcnow(),
        },
    )
    return int(inserted.lastrowid)


def ensure_db_connection_info(
    *,
    root_path: str,
    dataset: str,
    db_id: str,
    dialect: str | None,
    host_or_path: str,
) -> DBConnectionInfo:
    normalized = _normalize_connection_fields(
        root_path=root_path,
        dataset=dataset,
        db_id=db_id,
        dialect=dialect,
        host_or_path=host_or_path,
    )
    create_payload = {
        key: value for key, value in normalized.items() if key != "host_or_path"
    }
    if create_payload["dialect"] == "sqlite":
        Path(str(create_payload["host"])).mkdir(parents=True, exist_ok=True)
    connection = (
        db.session.query(DBConnectionInfo)
        .filter_by(
            dataset=create_payload["dataset"],
            db_id=create_payload["db_id"],
            dialect=create_payload["dialect"],
            host=create_payload["host"],
            database=create_payload["database"],
        )
        .one_or_none()
    )
    if connection is not None:
        return connection
    connection = DBConnectionInfo(**create_payload)
    db.session.add(connection)
    db.session.flush()
    return connection


def prune_orphan_db_connections() -> None:
    connection_ids = set(
        db.session.scalars(
            select(RunCase.db_connection_id).where(
                RunCase.db_connection_id.is_not(None)
            )
        )
    )
    orphaned = db.session.query(DBConnectionInfo)
    if connection_ids:
        orphaned = orphaned.filter(DBConnectionInfo.id.not_in(connection_ids))
    orphaned.delete(synchronize_session=False)


def _ensure_db_connection(
    conn,
    *,
    root_path: str,
    dataset: str,
    db_id: str,
    dialect: str | None,
    host_or_path: str,
) -> int:
    normalized = _normalize_connection_fields(
        root_path=root_path,
        dataset=dataset,
        db_id=db_id,
        dialect=dialect,
        host_or_path=host_or_path,
    )
    existing = conn.execute(
        text(
            """
            SELECT id
            FROM db_connection_info
            WHERE dataset = :dataset
              AND db_id = :db_id
              AND dialect = :dialect
              AND host = :host
              AND database = :database
            LIMIT 1
            """
        ),
        normalized,
    ).scalar_one_or_none()
    if existing is not None:
        return int(existing)
    inserted = conn.execute(
        text(
            """
            INSERT INTO db_connection_info (
                name, dataset, db_id, host, port, username, password, database, dialect, created_at
            ) VALUES (
                :name, :dataset, :db_id, :host, :port, :username, :password, :database, :dialect, :created_at
            )
            """
        ),
        {**normalized, "created_at": utcnow()},
    )
    return int(inserted.lastrowid)


def _normalize_connection_fields(
    *,
    root_path: str,
    dataset: str,
    db_id: str,
    dialect: str | None,
    host_or_path: str,
) -> dict[str, object]:
    normalized_dialect = (dialect or "sqlite").strip().lower()
    if normalized_dialect == "sqlite":
        host = str(Path(root_path) / "_connections" / dataset)
        database = f"{db_id}.sqlite"
    else:
        host = host_or_path.strip()
        database = db_id
    resolved_host_or_path = (
        str(Path(host) / database) if normalized_dialect == "sqlite" else host
    )
    return {
        "name": f"{dataset}:{db_id}:{normalized_dialect}",
        "dataset": dataset,
        "db_id": db_id,
        "host": host,
        "port": 0,
        "username": "",
        "password": "",
        "database": database,
        "dialect": normalized_dialect,
        "host_or_path": resolved_host_or_path,
    }


def _maybe_add_column(
    conn, inspector, table_name: str, column_name: str, column_type: str
) -> None:
    existing = {column["name"] for column in inspector.get_columns(table_name)}
    if column_name in existing:
        return
    conn.execute(
        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
    )


def _load_jsonish(value):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value
