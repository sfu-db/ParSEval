"""
schemas.py — Marshmallow schemas for request validation and response serialisation.

Each schema mirrors a model but controls exactly what is exposed over the API:
  • Input schemas (for POST/PATCH) validate and coerce incoming JSON.
  • Response schemas serialise ORM objects to JSON-safe dicts.

Naming convention: <Entity>Schema (full), <Entity>ListSchema (summary for list views).
"""

from marshmallow import (
    Schema,
    fields,
    validate,
    validates,
    ValidationError,
    pre_load,
    post_load,
)


class EnumField(fields.Field):
    """Serialises a str-Enum to its .value; deserialises as plain string."""

    def _serialize(self, value, attr, obj, **kwargs):
        if value is None:
            return None
        return value.value if hasattr(value, "value") else str(value)

    def _deserialize(self, value, attr, data, **kwargs):
        return value  # caller validates enum membership


from .enums import DBLevel, QueryLevel, RunStatus, CounterExampleState


# ─── Shared field types ───────────────────────────────────────────────────────

_DB_LEVELS = [e.value for e in DBLevel]
_QUERY_LEVELS = [e.value for e in QueryLevel]
_RUN_STATUSES = [e.value for e in RunStatus]
_CEX_STATES = [e.value for e in CounterExampleState]


# ─── Project ──────────────────────────────────────────────────────────────────


class ProjectCreateSchema(Schema):
    name = fields.Str(required=True, validate=validate.Length(min=1, max=255))
    description = fields.Str(load_default=None)


class ProjectSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str()
    description = fields.Str(allow_none=True)
    created_at = fields.DateTime(dump_only=True)
    updated_at = fields.DateTime(dump_only=True)


# ─── Dataset ──────────────────────────────────────────────────────────────────


class DatasetSchema(Schema):
    name = fields.Str()
    saved_at = fields.DateTime()


# ─── ModelRun ─────────────────────────────────────────────────────────────────


class ModelRunCreateSchema(Schema):
    """Validates POST /runs body."""

    project_id = fields.Int(required=True)
    model = fields.Str(required=True, validate=validate.Length(min=1, max=255))
    dataset = fields.Str(required=True, validate=validate.Length(min=1, max=255))
    run_name = fields.Str(load_default=None)
    prompt_template = fields.Str(load_default=None)
    setting = fields.Dict(load_default=None)


class ModelRunMetricPatchSchema(Schema):
    """Validates PATCH /runs/:id/metrics body."""

    exec_acc = fields.Float(
        load_default=None, validate=validate.Range(min=0.0, max=1.0)
    )
    exact_match = fields.Float(
        load_default=None, validate=validate.Range(min=0.0, max=1.0)
    )


class ModelRunSchema(Schema):
    """Full response schema for a single run."""

    id = fields.Int(dump_only=True)
    project_id = fields.Int()
    model = fields.Str()
    status = EnumField()
    created_at = fields.DateTime()
    updated_at = fields.DateTime()
    run_name = fields.Str(allow_none=True)
    dataset = fields.Str()
    prompt_template = fields.Str(allow_none=True)
    exec_acc = fields.Float(allow_none=True)
    exact_match = fields.Float(allow_none=True)
    setting = fields.Dict(allow_none=True)

    # Computed convenience: expose metrics as the TypeScript `metric` shape
    metric = fields.Method("get_metric")

    def get_metric(self, obj) -> dict:
        return {
            "EXEC_ACC": obj.exec_acc,
            "EXACT_MATCH": obj.exact_match,
        }


class ModelRunListSchema(Schema):
    """Compact schema for list views (omits prompt_template and setting)."""

    id = fields.Int()
    model = fields.Str()
    status = EnumField()
    created_at = fields.DateTime()
    run_name = fields.Str(allow_none=True)
    dataset = fields.Str()
    exec_acc = fields.Float(allow_none=True)
    exact_match = fields.Float(allow_none=True)


# ─── EvalRecord ───────────────────────────────────────────────────────────────


class EquivalenceLabelSchema(Schema):
    db_level = fields.Str()
    query_level = fields.Str()
    is_equivalent = fields.Bool()
    level_key = fields.Str(dump_only=True)  # "PK_FK_SET" etc.


class EvalRecordCreateSchema(Schema):
    """Validates a single record in a bulk POST."""

    question_id = fields.Int(required=True)
    db_id = fields.Str(required=True)
    dataset = fields.Str(required=True)
    host_or_path = fields.Str(required=True)
    question = fields.Str(required=True)
    evidence = fields.Str(load_default=None)
    gold = fields.Str(required=True)
    prompt = fields.Str(required=True)
    pred = fields.Str(required=True)


class EvalRecordBulkCreateSchema(Schema):
    """Wrapper for bulk insert endpoint."""

    records = fields.List(
        fields.Nested(EvalRecordCreateSchema),
        required=True,
        validate=validate.Length(min=1),
    )


class EvalRecordSchema(Schema):
    """Full response including labels."""

    id = fields.Int(dump_only=True)
    run_id = fields.Int()
    question_id = fields.Int()
    db_id = fields.Str()
    dataset = fields.Str()
    host_or_path = fields.Str()
    question = fields.Str()
    evidence = fields.Str(allow_none=True)
    gold = fields.Str()
    prompt = fields.Str()
    pred = fields.Str()

    # Labels serialised as the TypeScript shape: { "PK_POSITIVE": true, … }
    labels = fields.Method("get_labels")

    def get_labels(self, obj) -> dict:
        return {lbl.level_key: lbl.is_equivalent for lbl in obj.labels}


class EquivalenceLabelPatchSchema(Schema):
    """Validates PATCH /evals/:id/labels — adds or updates labels."""

    labels = fields.Dict(
        keys=fields.Str(),
        values=fields.Bool(),
        required=True,
    )


# ─── QueryExecution ───────────────────────────────────────────────────────────


class QueryExecutionSchema(Schema):
    id = fields.Int(dump_only=True)
    run_id = fields.Int()
    question_id = fields.Int()
    role = fields.Str()  # 'gold' | 'pred'
    db_id = fields.Str()
    dataset = fields.Str()
    host_or_path = fields.Str()
    query = fields.Str()
    dialect = fields.Str()
    elapsed_ms = fields.Int(allow_none=True)
    columns = fields.List(fields.Str(), allow_none=True)
    rows = fields.List(fields.List(fields.Raw()), allow_none=True)
    error_msg = fields.Str(allow_none=True)


class QueryExecutionCreateSchema(Schema):
    question_id = fields.Int(required=True)
    role = fields.Str(required=True, validate=validate.OneOf(["gold", "pred"]))
    db_id = fields.Str(required=True)
    dataset = fields.Str(required=True)
    host_or_path = fields.Str(required=True)
    query = fields.Str(required=True)
    dialect = fields.Str(load_default="sqlite")
    elapsed_ms = fields.Int(load_default=None)
    columns = fields.List(fields.Str(), load_default=None)
    rows = fields.List(fields.List(fields.Raw()), load_default=None)
    error_msg = fields.Str(load_default=None)


# ─── Witness ──────────────────────────────────────────────────────────────────


class WitnessTableSchema(Schema):
    """Shape of each table inside a witness database (stored as JSON)."""

    name = fields.Str(required=True)
    columns = fields.List(fields.Str(), required=True)
    rows = fields.List(fields.List(fields.Raw()), required=True)


class WitnessDatabaseSchema(Schema):
    """The synthesised counterexample database (stored as JSON in CounterExample)."""

    db_id = fields.Str(required=True)
    host_or_path = fields.Str(required=True)
    database = fields.Str(required=True)
    tables = fields.List(fields.Nested(WitnessTableSchema), required=True)


# ─── CounterExample ───────────────────────────────────────────────────────────


class CounterExampleSchema(Schema):
    id = fields.Int(dump_only=True)
    equivalence_id = fields.Int()
    db_level = EnumField()
    query_level = EnumField()
    level_key = fields.Str(dump_only=True)
    state = EnumField()
    witness_db = fields.Nested(WitnessDatabaseSchema, allow_none=True)
    gold_execution = fields.Nested(QueryExecutionSchema, allow_none=True)
    pred_execution = fields.Nested(QueryExecutionSchema, allow_none=True)


class CounterExampleCreateSchema(Schema):
    """Validates POST /equivalences/:id/counterexamples."""

    db_level = fields.Str(required=True, validate=validate.OneOf(_DB_LEVELS))
    query_level = fields.Str(required=True, validate=validate.OneOf(_QUERY_LEVELS))
    witness_db = fields.Nested(WitnessDatabaseSchema, load_default=None)
    gold_execution_id = fields.Int(load_default=None)
    pred_execution_id = fields.Int(load_default=None)


class CounterExampleStatePatchSchema(Schema):
    state = fields.Str(required=True, validate=validate.OneOf(_CEX_STATES))


# ─── RelaxedEquivalence ───────────────────────────────────────────────────────


class RelaxedEquivalenceSchema(Schema):
    """Full response including nested counterexamples."""

    id = fields.Int(dump_only=True)
    run_id = fields.Int()
    question_id = fields.Int()
    db_id = fields.Str()
    dataset = fields.Str()
    host_or_path = fields.Str()
    gold = fields.Str()
    pred = fields.Str()
    state = EnumField()
    updated_at = fields.DateTime()
    counterexamples = fields.List(fields.Nested(CounterExampleSchema))


class RelaxedEquivalenceListSchema(Schema):
    """Compact schema for list views — omits SQL text and nested counterexamples."""

    id = fields.Int()
    run_id = fields.Int()
    question_id = fields.Int()
    db_id = fields.Str()
    dataset = fields.Str()
    state = fields.Str()
    updated_at = fields.DateTime()
    # Summary: how many of the checked levels are witnessed (distinguishable)?
    witnessed_count = fields.Method("get_witnessed_count")
    equivalent_count = fields.Method("get_equivalent_count")

    def get_witnessed_count(self, obj) -> int:
        return sum(
            1 for c in obj.counterexamples if c.state == CounterExampleState.WITNESSED
        )

    def get_equivalent_count(self, obj) -> int:
        return sum(
            1 for c in obj.counterexamples if c.state == CounterExampleState.EQUIVALENT
        )


class RelaxedEquivalenceCreateSchema(Schema):
    question_id = fields.Int(required=True)
    db_id = fields.Str(required=True)
    dataset = fields.Str(required=True)
    host_or_path = fields.Str(required=True)
    gold = fields.Str(required=True)
    pred = fields.Str(required=True)


# ─── Pagination ───────────────────────────────────────────────────────────────


class PaginationSchema(Schema):
    """Query-string params for paginated list endpoints."""

    page = fields.Int(load_default=1, validate=validate.Range(min=1))
    per_page = fields.Int(load_default=50, validate=validate.Range(min=1, max=500))


class PaginatedResponseSchema(Schema):
    """Envelope for paginated list responses."""

    items = fields.List(fields.Raw())
    total = fields.Int()
    page = fields.Int()
    per_page = fields.Int()
    pages = fields.Int()


# ─── Standalone validators ────────────────────────────────────────────────────

_VALID_LEVELS = frozenset(
    f"{db.value}_{ql.value}" for db in DBLevel for ql in QueryLevel
)


def validate_label_keys(labels: dict) -> list[str]:
    """Return list of invalid level keys (empty list = all valid)."""
    return [k for k in labels if k not in _VALID_LEVELS]
