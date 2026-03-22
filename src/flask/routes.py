"""
routes.py — Flask blueprints for every resource.

URL design
----------
/api/v1/projects                          Projects CRUD
/api/v1/projects/<id>/runs               ModelRuns per project
/api/v1/runs/<id>                        ModelRun detail / update
/api/v1/runs/<id>/metrics                Patch metric values
/api/v1/runs/<id>/evals                  EvalRecords (bulk create, list, filter)
/api/v1/runs/<id>/evals/<qid>            Single EvalRecord by question_id
/api/v1/runs/<id>/evals/<qid>/labels     Patch equivalence labels on an EvalRecord
/api/v1/runs/<id>/executions             QueryExecution list / create
/api/v1/runs/<id>/executions/<qid>       Both executions for a question (gold+pred pair)
/api/v1/runs/<id>/equivalences           RelaxedEquivalence list / create
/api/v1/runs/<id>/equivalences/<qid>     Single equivalence record
/api/v1/equivalences/<id>/counterexamples  CounterExample list / create
/api/v1/counterexamples/<id>/state       Patch state of a counterexample
/api/v1/datasets                         Dataset registry
"""

from flask import Blueprint, jsonify, request
from marshmallow import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .models import (
    db,
    Project,
    Dataset,
    ModelRun,
    EvalRecord,
    EquivalenceLabel,
    QueryExecution,
    RelaxedEquivalence,
    CounterExample,
)
from .schemas import (
    ProjectCreateSchema,
    ProjectSchema,
    DatasetSchema,
    ModelRunCreateSchema,
    ModelRunMetricPatchSchema,
    ModelRunSchema,
    ModelRunListSchema,
    EvalRecordBulkCreateSchema,
    EvalRecordSchema,
    EquivalenceLabelPatchSchema,
    QueryExecutionCreateSchema,
    QueryExecutionSchema,
    RelaxedEquivalenceCreateSchema,
    RelaxedEquivalenceSchema,
    RelaxedEquivalenceListSchema,
    CounterExampleCreateSchema,
    CounterExampleSchema,
    CounterExampleStatePatchSchema,
    PaginationSchema,
    validate_label_keys,
)
from .enums import DBLevel, QueryLevel, CounterExampleState, RunStatus

api = Blueprint("api", __name__, url_prefix="/api/v1")


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _bad_request(msg: str, details=None):
    body = {"error": msg}
    if details:
        body["details"] = details
    return jsonify(body), 400


def _not_found(entity: str, id):
    return jsonify({"error": f"{entity} {id!r} not found"}), 404


def _validate(schema_cls, data: dict):
    """Validate and deserialise; raises ValidationError on failure."""
    return schema_cls().load(data)


def _paginate(query, schema_cls):
    """Apply pagination from query-string and return paginated envelope."""
    params = PaginationSchema().load(request.args)
    page, per_page = params["page"], params["per_page"]
    paginated = db.paginate(query, page=page, per_page=per_page, error_out=False)
    schema = schema_cls(many=True)
    return jsonify(
        {
            "items": schema.dump(paginated.items),
            "total": paginated.total,
            "page": paginated.page,
            "per_page": paginated.per_page,
            "pages": paginated.pages,
        }
    )


# ─── Projects ─────────────────────────────────────────────────────────────────


@api.route("/projects", methods=["GET"])
def list_projects():
    projects = db.session.scalars(select(Project).order_by(Project.id)).all()
    return jsonify(ProjectSchema(many=True).dump(projects))


@api.route("/projects", methods=["POST"])
def create_project():
    try:
        data = _validate(ProjectCreateSchema, request.get_json() or {})
    except ValidationError as e:
        return _bad_request("Validation failed", e.messages)

    project = Project(**data)
    db.session.add(project)
    db.session.commit()
    return jsonify(ProjectSchema().dump(project)), 201


@api.route("/projects/<int:project_id>", methods=["GET"])
def get_project(project_id: int):
    project = db.session.get(Project, project_id)
    if not project:
        return _not_found("Project", project_id)
    return jsonify(ProjectSchema().dump(project))


@api.route("/projects/<int:project_id>", methods=["DELETE"])
def delete_project(project_id: int):
    project = db.session.get(Project, project_id)
    if not project:
        return _not_found("Project", project_id)
    db.session.delete(project)
    db.session.commit()
    return "", 204


# ─── ModelRuns ────────────────────────────────────────────────────────────────


@api.route("/projects/<int:project_id>/runs", methods=["GET"])
def list_runs(project_id: int):
    project = db.session.get(Project, project_id)
    if not project:
        return _not_found("Project", project_id)

    # Optional filters from query-string
    dataset = request.args.get("dataset")
    status = request.args.get("status")
    model = request.args.get("model")

    q = (
        select(ModelRun)
        .where(ModelRun.project_id == project_id)
        .order_by(ModelRun.created_at.desc())
    )
    if dataset:
        q = q.where(ModelRun.dataset == dataset)
    if status:
        q = q.where(ModelRun.status == status)
    if model:
        q = q.where(ModelRun.model.ilike(f"%{model}%"))

    return _paginate(q, ModelRunListSchema)


@api.route("/runs", methods=["POST"])
def create_run():
    try:
        data = _validate(ModelRunCreateSchema, request.get_json() or {})
    except ValidationError as e:
        return _bad_request("Validation failed", e.messages)

    if not db.session.get(Project, data["project_id"]):
        return _bad_request(f"Project {data['project_id']} not found")

    run = ModelRun(**data)
    db.session.add(run)
    db.session.commit()
    return jsonify(ModelRunSchema().dump(run)), 201


@api.route("/runs/<int:run_id>", methods=["GET"])
def get_run(run_id: int):
    run = db.session.get(ModelRun, run_id)
    if not run:
        return _not_found("ModelRun", run_id)
    return jsonify(ModelRunSchema().dump(run))


@api.route("/runs/<int:run_id>/status", methods=["PATCH"])
def patch_run_status(run_id: int):
    run = db.session.get(ModelRun, run_id)
    if not run:
        return _not_found("ModelRun", run_id)

    body = request.get_json() or {}
    new_status = body.get("status")
    if new_status not in [s.value for s in RunStatus]:
        return _bad_request(f"Invalid status {new_status!r}")

    run.status = new_status
    db.session.commit()
    return jsonify(ModelRunSchema().dump(run))


@api.route("/runs/<int:run_id>/metrics", methods=["PATCH"])
def patch_run_metrics(run_id: int):
    run = db.session.get(ModelRun, run_id)
    if not run:
        return _not_found("ModelRun", run_id)

    try:
        data = _validate(ModelRunMetricPatchSchema, request.get_json() or {})
    except ValidationError as e:
        return _bad_request("Validation failed", e.messages)

    if data.get("exec_acc") is not None:
        run.exec_acc = data["exec_acc"]
    if data.get("exact_match") is not None:
        run.exact_match = data["exact_match"]

    db.session.commit()
    return jsonify(ModelRunSchema().dump(run))


# ─── EvalRecords ──────────────────────────────────────────────────────────────


@api.route("/runs/<int:run_id>/evals", methods=["GET"])
def list_evals(run_id: int):
    if not db.session.get(ModelRun, run_id):
        return _not_found("ModelRun", run_id)

    db_id = request.args.get("db_id")
    dataset = request.args.get("dataset")

    q = (
        select(EvalRecord)
        .where(EvalRecord.run_id == run_id)
        .options(selectinload(EvalRecord.labels))
        .order_by(EvalRecord.question_id)
    )
    if db_id:
        q = q.where(EvalRecord.db_id == db_id)
    if dataset:
        q = q.where(EvalRecord.dataset == dataset)

    return _paginate(q, EvalRecordSchema)


@api.route("/runs/<int:run_id>/evals", methods=["POST"])
def bulk_create_evals(run_id: int):
    """
    Bulk insert eval records for a run.
    Idempotent: existing (run_id, question_id) rows are skipped.
    """
    if not db.session.get(ModelRun, run_id):
        return _not_found("ModelRun", run_id)

    try:
        data = _validate(EvalRecordBulkCreateSchema, request.get_json() or {})
    except ValidationError as e:
        return _bad_request("Validation failed", e.messages)

    # Load existing question_ids to skip duplicates
    existing_qids = set(
        db.session.scalars(
            select(EvalRecord.question_id).where(EvalRecord.run_id == run_id)
        ).all()
    )

    created = []
    for rec_data in data["records"]:
        if rec_data["question_id"] in existing_qids:
            continue
        rec = EvalRecord(run_id=run_id, **rec_data)
        db.session.add(rec)
        created.append(rec_data["question_id"])

    db.session.commit()
    return (
        jsonify(
            {"created": len(created), "skipped": len(data["records"]) - len(created)}
        ),
        201,
    )


@api.route("/runs/<int:run_id>/evals/<int:question_id>", methods=["GET"])
def get_eval(run_id: int, question_id: int):
    record = db.session.scalar(
        select(EvalRecord)
        .where(EvalRecord.run_id == run_id, EvalRecord.question_id == question_id)
        .options(selectinload(EvalRecord.labels))
    )
    if not record:
        return _not_found(f"EvalRecord (run={run_id}, question={question_id})", "")
    return jsonify(EvalRecordSchema().dump(record))


@api.route("/runs/<int:run_id>/evals/<int:question_id>/labels", methods=["PATCH"])
def patch_eval_labels(run_id: int, question_id: int):
    """
    Upsert equivalence labels on an EvalRecord.
    Body: { "labels": { "PK_FK_POSITIVE": true, "NONE_FULL": false, … } }
    """
    record = db.session.scalar(
        select(EvalRecord)
        .where(EvalRecord.run_id == run_id, EvalRecord.question_id == question_id)
        .options(selectinload(EvalRecord.labels))
    )
    if not record:
        return _not_found(f"EvalRecord (run={run_id}, question={question_id})", "")

    try:
        data = _validate(EquivalenceLabelPatchSchema, request.get_json() or {})
    except ValidationError as e:
        return _bad_request("Validation failed", e.messages)

    invalid_keys = validate_label_keys(data["labels"])
    if invalid_keys:
        return _bad_request("Invalid equivalence level keys", {"invalid": invalid_keys})

    # Build index of existing labels
    existing = {
        (lbl.db_level.value, lbl.query_level.value): lbl for lbl in record.labels
    }

    for level_key, is_equiv in data["labels"].items():
        # Find the split point: try each DBLevel as a prefix
        # Sort longest-first so PK_FK_NULL matches before PK_FK, PK_FK before PK
        db_level = next(
            (
                db
                for db in sorted(DBLevel, key=lambda d: len(d.value), reverse=True)
                if level_key.startswith(db.value + "_")
            ),
            None,
        )
        if db_level is None:
            continue
        ql_str = level_key[len(db_level.value) + 1 :]
        try:
            query_level = QueryLevel(ql_str)
        except ValueError:
            continue

        key = (db_level.value, query_level.value)
        if key in existing:
            existing[key].is_equivalent = is_equiv
        else:
            lbl = EquivalenceLabel(
                eval_record_id=record.id,
                db_level=db_level,
                query_level=query_level,
                is_equivalent=is_equiv,
            )
            db.session.add(lbl)

    db.session.commit()
    db.session.refresh(record)
    return jsonify(EvalRecordSchema().dump(record))


# ─── QueryExecutions ──────────────────────────────────────────────────────────


@api.route("/runs/<int:run_id>/executions", methods=["POST"])
def create_execution(run_id: int):
    if not db.session.get(ModelRun, run_id):
        return _not_found("ModelRun", run_id)

    try:
        data = _validate(QueryExecutionCreateSchema, request.get_json() or {})
    except ValidationError as e:
        return _bad_request("Validation failed", e.messages)

    execution = QueryExecution(run_id=run_id, **data)
    db.session.add(execution)
    db.session.commit()
    return jsonify(QueryExecutionSchema().dump(execution)), 201


@api.route("/runs/<int:run_id>/executions/<int:question_id>", methods=["GET"])
def get_executions_for_question(run_id: int, question_id: int):
    """Returns both gold and pred executions for a question as {gold: …, pred: …}."""
    executions = db.session.scalars(
        select(QueryExecution).where(
            QueryExecution.run_id == run_id,
            QueryExecution.question_id == question_id,
        )
    ).all()

    result = {ex.role: QueryExecutionSchema().dump(ex) for ex in executions}
    return jsonify(result)


# ─── RelaxedEquivalences ──────────────────────────────────────────────────────


@api.route("/runs/<int:run_id>/equivalences", methods=["GET"])
def list_equivalences(run_id: int):
    if not db.session.get(ModelRun, run_id):
        return _not_found("ModelRun", run_id)

    state = request.args.get("state")
    db_id = request.args.get("db_id")
    dataset = request.args.get("dataset")

    q = (
        select(RelaxedEquivalence)
        .where(RelaxedEquivalence.run_id == run_id)
        .options(selectinload(RelaxedEquivalence.counterexamples))
        .order_by(RelaxedEquivalence.question_id)
    )
    if state:
        q = q.where(RelaxedEquivalence.state == state)
    if db_id:
        q = q.where(RelaxedEquivalence.db_id == db_id)
    if dataset:
        q = q.where(RelaxedEquivalence.dataset == dataset)

    return _paginate(q, RelaxedEquivalenceListSchema)


@api.route("/runs/<int:run_id>/equivalences", methods=["POST"])
def create_equivalence(run_id: int):
    if not db.session.get(ModelRun, run_id):
        return _not_found("ModelRun", run_id)

    try:
        data = _validate(RelaxedEquivalenceCreateSchema, request.get_json() or {})
    except ValidationError as e:
        return _bad_request("Validation failed", e.messages)

    equiv = RelaxedEquivalence(run_id=run_id, **data)
    db.session.add(equiv)
    db.session.commit()
    return jsonify(RelaxedEquivalenceSchema().dump(equiv)), 201


@api.route("/runs/<int:run_id>/equivalences/<int:question_id>", methods=["GET"])
def get_equivalence(run_id: int, question_id: int):
    equiv = db.session.scalar(
        select(RelaxedEquivalence)
        .where(
            RelaxedEquivalence.run_id == run_id,
            RelaxedEquivalence.question_id == question_id,
        )
        .options(
            selectinload(RelaxedEquivalence.counterexamples).selectinload(
                CounterExample.gold_execution
            ),
            selectinload(RelaxedEquivalence.counterexamples).selectinload(
                CounterExample.pred_execution
            ),
        )
    )
    if not equiv:
        return _not_found(
            f"RelaxedEquivalence (run={run_id}, question={question_id})", ""
        )
    return jsonify(RelaxedEquivalenceSchema().dump(equiv))


# ─── CounterExamples ──────────────────────────────────────────────────────────


@api.route("/equivalences/<int:equivalence_id>/counterexamples", methods=["GET"])
def list_counterexamples(equivalence_id: int):
    equiv = db.session.get(RelaxedEquivalence, equivalence_id)
    if not equiv:
        return _not_found("RelaxedEquivalence", equivalence_id)

    cexs = db.session.scalars(
        select(CounterExample)
        .where(CounterExample.equivalence_id == equivalence_id)
        .options(
            selectinload(CounterExample.gold_execution),
            selectinload(CounterExample.pred_execution),
        )
        .order_by(CounterExample.db_level, CounterExample.query_level)
    ).all()
    return jsonify(CounterExampleSchema(many=True).dump(cexs))


@api.route("/equivalences/<int:equivalence_id>/counterexamples", methods=["POST"])
def create_counterexample(equivalence_id: int):
    equiv = db.session.get(RelaxedEquivalence, equivalence_id)
    if not equiv:
        return _not_found("RelaxedEquivalence", equivalence_id)

    try:
        data = _validate(CounterExampleCreateSchema, request.get_json() or {})
    except ValidationError as e:
        return _bad_request("Validation failed", e.messages)

    cex = CounterExample(equivalence_id=equivalence_id, **data)
    db.session.add(cex)
    db.session.commit()
    return jsonify(CounterExampleSchema().dump(cex)), 201


@api.route("/counterexamples/<int:cex_id>/state", methods=["PATCH"])
def patch_counterexample_state(cex_id: int):
    cex = db.session.get(CounterExample, cex_id)
    if not cex:
        return _not_found("CounterExample", cex_id)

    try:
        data = _validate(CounterExampleStatePatchSchema, request.get_json() or {})
    except ValidationError as e:
        return _bad_request("Validation failed", e.messages)

    cex.state = data["state"]
    db.session.commit()
    return jsonify(CounterExampleSchema().dump(cex))


# ─── Datasets ─────────────────────────────────────────────────────────────────


@api.route("/datasets", methods=["GET"])
def list_datasets():
    datasets = db.session.scalars(select(Dataset).order_by(Dataset.name)).all()
    return jsonify(DatasetSchema(many=True).dump(datasets))


# ─── Aggregation endpoints ────────────────────────────────────────────────────


@api.route("/runs/<int:run_id>/summary", methods=["GET"])
def run_summary(run_id: int):
    """
    Returns per-equivalence-level accuracy for a run — the key metric
    for the QueryLens dashboard.

    Response shape:
    {
      "run_id": 1,
      "total_questions": 200,
      "metrics": {
        "EXEC_ACC": 0.82,
        "EXACT_MATCH": 0.71
      },
      "equivalence_rates": {
        "NONE_POSITIVE": { "checked": 200, "equivalent": 160, "rate": 0.80 },
        "PK_POSITIVE":   { "checked": 200, "equivalent": 155, "rate": 0.775 },
        ...
      }
    }
    """
    run = db.session.get(ModelRun, run_id)
    if not run:
        return _not_found("ModelRun", run_id)

    total = (
        db.session.scalar(
            select(db.func.count())
            .select_from(EvalRecord)
            .where(EvalRecord.run_id == run_id)
        )
        or 0
    )

    # Aggregate per (db_level, query_level)
    label_agg = db.session.execute(
        select(
            EquivalenceLabel.db_level,
            EquivalenceLabel.query_level,
            db.func.count().label("checked"),
            db.func.sum(db.func.cast(EquivalenceLabel.is_equivalent, db.Integer)).label(
                "equivalent"
            ),
        )
        .join(EvalRecord, EquivalenceLabel.eval_record_id == EvalRecord.id)
        .where(EvalRecord.run_id == run_id)
        .group_by(EquivalenceLabel.db_level, EquivalenceLabel.query_level)
    ).all()

    rates = {}
    for row in label_agg:
        key = f"{row.db_level.value}_{row.query_level.value}"
        checked = row.checked
        equiv = row.equivalent or 0
        rates[key] = {
            "checked": checked,
            "equivalent": equiv,
            "rate": round(equiv / checked, 4) if checked else None,
        }

    return jsonify(
        {
            "run_id": run_id,
            "total_questions": total,
            "metrics": {
                "EXEC_ACC": run.exec_acc,
                "EXACT_MATCH": run.exact_match,
            },
            "equivalence_rates": rates,
        }
    )
