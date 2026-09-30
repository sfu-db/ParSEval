from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import TypeVar

from .constraints import (
    CheckDecl,
    ForeignKeyDecl,
    GeneratedColumnDecl,
    NotNullDecl,
    PrimaryKeyDecl,
    UniqueDecl,
    UnsupportedConstraintDecl,
)
from .names import (
    AggregateSpecId,
    CallSiteId,
    CollationId,
    ColumnId,
    ConstraintId,
    FunctionId,
    ParameterId,
    RelationId,
    SchemaId,
)
from .decls import RelationSpec, RowShape
from .sorts import ScalarSort

IdentityT = TypeVar("IdentityT")


class Volatility(str, Enum):
    IMMUTABLE = "immutable"
    STABLE = "stable"
    VOLATILE = "volatile"


class AggregateKind(str, Enum):
    """Trusted semantic family for builtin aggregate overloads."""

    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


@dataclass(frozen=True, slots=True)
class ScalarFunctionSpec:
    parameters: tuple[ScalarSort, ...]
    result: ScalarSort
    volatility: Volatility = Volatility.IMMUTABLE
    operator: str | None = None


@dataclass(frozen=True, slots=True)
class AggregateSpec:
    """Logical aggregate contract used by the semantic IR.

    ``input`` is ``None`` for star aggregates such as COUNT(*).
    """

    input: ScalarSort | None
    output: ScalarSort
    order_sensitive: bool = False
    kind: AggregateKind | None = None
    operator: str | None = None


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    sort: ScalarSort


class Context:
    """Registry of stable declarations. IDs are local to this context."""

    __slots__ = (
        "dialect",
        "_schemas",
        "_schema_ids",
        "_relations",
        "_functions",
        "_function_ids",
        "_aggregates",
        "_aggregate_ids",
        "_parameters",
        "_collations",
        "_next_ids",
    )

    def __init__(self, *, dialect: str | None = None) -> None:
        self.dialect = dialect
        self._schemas: list[RowShape] = []
        self._schema_ids: dict[RowShape, SchemaId] = {}
        self._relations: dict[RelationId, RelationSpec] = {}
        self._functions: dict[FunctionId, ScalarFunctionSpec] = {}
        self._function_ids: dict[ScalarFunctionSpec, FunctionId] = {}
        self._aggregates: dict[AggregateSpecId, AggregateSpec] = {}
        self._aggregate_ids: dict[AggregateSpec, AggregateSpecId] = {}
        self._parameters: dict[ParameterId, ParameterSpec] = {}
        self._collations: set[CollationId] = set()
        self._next_ids: dict[type, int] = {}

    def intern_schema(self, schema: RowShape) -> SchemaId:
        existing = self._schema_ids.get(schema)
        if existing is not None:
            return existing
        schema_id = SchemaId(len(self._schemas))
        self._schemas.append(schema)
        self._schema_ids[schema] = schema_id
        return schema_id

    def schema(self, schema_id: SchemaId) -> RowShape:
        try:
            return self._schemas[schema_id.value]
        except IndexError as error:
            raise KeyError(f"Unknown schema: {schema_id!r}") from error

    def schemas(self) -> Iterator[tuple[SchemaId, RowShape]]:
        for index, schema in enumerate(self._schemas):
            yield SchemaId(index), schema

    def concat_schema(self, left: SchemaId, right: SchemaId) -> SchemaId:
        left_schema = self.schema(left)
        right_schema = self.schema(right)
        return self.intern_schema(RowShape(left_schema.fields + right_schema.fields))

    def nullable_schema(self, schema_id: SchemaId) -> SchemaId:
        schema = self.schema(schema_id)
        return self.intern_schema(
            RowShape(
                tuple(
                    ScalarSort(field.sql_type, nullable=True) for field in schema.fields
                )
            )
        )

    def outer_join_schema(
        self,
        left: SchemaId,
        right: SchemaId,
        *,
        nullable_left: bool = False,
        nullable_right: bool = False,
    ) -> SchemaId:
        left_schema = self.nullable_schema(left) if nullable_left else left
        right_schema = self.nullable_schema(right) if nullable_right else right
        return self.concat_schema(left_schema, right_schema)

    def register_relation(self, relation_id: RelationId, spec: RelationSpec) -> None:
        schema = self.schema(spec.schema)
        if tuple(column.sort for column in spec.columns) != schema.fields:
            raise ValueError("Relation columns must match its schema")
        if len({column.id for column in spec.columns}) != len(spec.columns):
            raise ValueError("Relation column IDs must be unique")
        owned = {
            column.id
            for other_id, other in self._relations.items()
            if other_id != relation_id
            for column in other.columns
        }
        if owned.intersection(column.id for column in spec.columns):
            raise ValueError("Column IDs must be unique across relations")
        for column in spec.columns:
            if column.collation is not None:
                self.require_collation(column.collation)
        self._validate_constraints(relation_id, spec)
        self._insert_unique(self._relations, relation_id, spec, "relation")
        self._reserve_id(relation_id)
        for column in spec.columns:
            self._reserve_id(column.id)
        for constraint in spec.constraints:
            self._reserve_id(constraint.metadata.id)

    def relation(self, relation_id: RelationId) -> RelationSpec:
        return self._lookup(self._relations, relation_id, "relation")

    def replace_relation(self, relation_id: RelationId, spec: RelationSpec) -> None:
        """Update constraints without invalidating existing term sorts or fields."""
        previous = self.relation(relation_id)
        if spec.schema != previous.schema or spec.columns != previous.columns:
            raise ValueError("Replacing a relation may only update constraints")
        self._validate_constraints(relation_id, spec)
        for other in self._relations.values():
            for item in other.constraints:
                if (
                    isinstance(item, ForeignKeyDecl)
                    and item.metadata.proof_active
                    and item.target_relation == relation_id
                    and not spec.has_unconditional_key(item.target)
                ):
                    raise ValueError(
                        "Cannot remove a key referenced by an active foreign key"
                    )
        self._relations[relation_id] = spec
        for constraint in spec.constraints:
            self._reserve_id(constraint.metadata.id)

    def relations(self) -> Iterator[tuple[RelationId, RelationSpec]]:
        yield from sorted(self._relations.items(), key=lambda item: item[0].value)

    def register_function(
        self, function_id: FunctionId, spec: ScalarFunctionSpec
    ) -> None:
        self._insert_unique(self._functions, function_id, spec, "function")
        self._reserve_id(function_id)

    def intern_function(self, spec: ScalarFunctionSpec) -> FunctionId:
        if spec.operator is None:
            raise ValueError("Interned scalar operations require an operator key")
        existing = self._function_ids.get(spec)
        if existing is not None:
            return existing
        identity = self.allocate_id(FunctionId)
        self.register_function(identity, spec)
        self._function_ids[spec] = identity
        return identity

    def function(self, function_id: FunctionId) -> ScalarFunctionSpec:
        return self._lookup(self._functions, function_id, "function")

    def functions(self) -> Iterator[tuple[FunctionId, ScalarFunctionSpec]]:
        yield from sorted(self._functions.items(), key=lambda item: item[0].value)

    def register_aggregate(
        self, aggregate_id: AggregateSpecId, spec: AggregateSpec
    ) -> None:
        self._insert_unique(self._aggregates, aggregate_id, spec, "aggregate")
        self._reserve_id(aggregate_id)

    def intern_aggregate(self, spec: AggregateSpec) -> AggregateSpecId:
        if spec.operator is None:
            raise ValueError("Interned aggregates require an operator key")
        existing = self._aggregate_ids.get(spec)
        if existing is not None:
            return existing
        identity = self.allocate_id(AggregateSpecId)
        self.register_aggregate(identity, spec)
        self._aggregate_ids[spec] = identity
        return identity

    def aggregate(self, aggregate_id: AggregateSpecId) -> AggregateSpec:
        return self._lookup(self._aggregates, aggregate_id, "aggregate")

    def aggregates(self) -> Iterator[tuple[AggregateSpecId, AggregateSpec]]:
        yield from sorted(self._aggregates.items(), key=lambda item: item[0].value)

    def register_parameter(
        self, parameter_id: ParameterId, spec: ParameterSpec
    ) -> None:
        self._insert_unique(self._parameters, parameter_id, spec, "parameter")
        self._reserve_id(parameter_id)

    def parameter(self, parameter_id: ParameterId) -> ParameterSpec:
        return self._lookup(self._parameters, parameter_id, "parameter")

    def parameters(self) -> Iterator[tuple[ParameterId, ParameterSpec]]:
        yield from sorted(self._parameters.items(), key=lambda item: item[0].value)

    def register_collation(self, collation_id: CollationId) -> None:
        self._collations.add(collation_id)
        self._reserve_id(collation_id)

    def require_collation(self, collation_id: CollationId) -> None:
        if collation_id not in self._collations:
            raise KeyError(f"Unknown collation: {collation_id!r}")

    def allocate_id(self, identity: type[IdentityT]) -> IdentityT:
        """Allocate a context-local identity; IDs are never reused."""
        if identity not in (
            RelationId,
            ColumnId,
            ConstraintId,
            FunctionId,
            AggregateSpecId,
            ParameterId,
            CollationId,
            CallSiteId,
        ):
            raise TypeError(f"Not a declaration identity: {identity!r}")
        value = self._next_ids.get(identity, 0)
        self._next_ids[identity] = value + 1
        return identity(value)

    def _reserve_id(self, identity) -> None:
        kind = type(identity)
        self._next_ids[kind] = max(self._next_ids.get(kind, 0), identity.value + 1)

    def _validate_constraints(
        self, relation_id: RelationId, spec: RelationSpec
    ) -> None:
        columns = {column.id: column for column in spec.columns}
        identities = set()
        names = set()
        generated = set()
        primary_keys = 0
        owned = {
            item.metadata.id
            for other_id, other in self._relations.items()
            if other_id != relation_id
            for item in other.constraints
        }
        for item in spec.constraints:
            if item.relation != relation_id:
                raise ValueError("Constraint belongs to another relation")
            if item.metadata.id in identities or item.metadata.id in owned:
                raise ValueError("Duplicate constraint identity")
            identities.add(item.metadata.id)
            if item.metadata.name is not None:
                name = item.metadata.name.text
                if name in names:
                    raise ValueError(f"Duplicate constraint name {name!r}")
                names.add(name)
            if isinstance(item, (PrimaryKeyDecl, UniqueDecl)):
                references = item.columns
                primary_keys += isinstance(item, PrimaryKeyDecl)
                if primary_keys > 1:
                    raise ValueError("A relation may have only one primary key")
            elif isinstance(item, (NotNullDecl, GeneratedColumnDecl)):
                references = (item.column,)
                if isinstance(item, GeneratedColumnDecl):
                    if item.column in generated:
                        raise ValueError("Duplicate generated column")
                    generated.add(item.column)
            elif isinstance(item, ForeignKeyDecl):
                references = item.source
                target = (
                    spec
                    if item.target_relation == relation_id
                    else self.relation(item.target_relation)
                )
                if item.metadata.proof_active and len(set(item.target)) != len(
                    item.target
                ):
                    raise ValueError("Duplicate foreign-key target column")
                target_columns = tuple(
                    target.columns[target.column_position(c)] for c in item.target
                )
                if item.metadata.proof_active and not target.has_unconditional_key(
                    item.target
                ):
                    raise ValueError("Foreign key requires an active target key")
                if all(c in columns for c in references) and tuple(
                    columns[c].sort.sql_type for c in references
                ) != tuple(c.sort.sql_type for c in target_columns):
                    raise ValueError("Foreign-key column types differ")
            elif isinstance(item, (CheckDecl, UnsupportedConstraintDecl)):
                references = ()
            else:
                raise TypeError(f"Unknown constraint declaration {type(item).__name__}")
            if len(set(references)) != len(references):
                raise ValueError("Duplicate constraint column")
            if any(column not in columns for column in references):
                raise ValueError("Constraint references an unknown column")
            if isinstance(item, NotNullDecl) and columns[item.column].sort.nullable:
                raise ValueError("NOT NULL requires a non-nullable column sort")

    @staticmethod
    def _insert_unique(mapping: dict, key: object, value: object, label: str) -> None:
        previous = mapping.get(key)
        if previous is not None and previous != value:
            raise ValueError(f"Conflicting {label} declaration for {key!r}")
        mapping[key] = value

    @staticmethod
    def _lookup(mapping: dict, key: object, label: str):
        try:
            return mapping[key]
        except KeyError as error:
            raise KeyError(f"Unknown {label}: {key!r}") from error
