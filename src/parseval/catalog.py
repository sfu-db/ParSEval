"""SQL names and metadata bound to one semantic context."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from parseval.errors import CatalogError
from parseval.parser.dialect import SQLDialect
from parseval.terms import Context, TermArena, terms, verify_closed
from parseval.terms.constraints import (
    CatalogExpression,
    CheckDecl,
    ConstraintDecl,
    ConstraintMetadata,
    ForeignKeyDecl,
    ForeignKeyMatch,
    GeneratedColumnDecl,
    NotNullDecl,
    NullConflictPolicy,
    PrimaryKeyDecl,
    UniqueDecl,
    UnsupportedConstraintDecl,
)
from parseval.terms.context import AggregateSpec, ScalarFunctionSpec, Volatility
from parseval.identifiers import (
    Identifier,
    NameInput,
    NameKey,
    QualifiedName,
    name_key,
)
from parseval.terms.names import (
    AggregateSpecId,
    CollationId,
    ColumnId,
    ConstraintId,
    FunctionId,
    RelationId,
    SchemaId,
)
from parseval.terms.decls import ColumnSpec, RelationSpec, RowShape
from parseval.terms.sorts import PREDICATE, RowFunctionSort, ScalarSort, Sort
@dataclass(frozen=True, slots=True)
class ColumnDecl:
    """Input column before allocation of its semantic identity."""

    name: Identifier
    sort: ScalarSort
    declared_type: str | None = None
    collation: CollationId | None = None
    default_sql: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnBinding:
    id: ColumnId
    name: Identifier
    declared_type: str
    default_sql: str | None = None


@dataclass(frozen=True, slots=True)
class TableDecl:
    name: QualifiedName
    relation: RelationId
    context: Context
    columns: tuple[ColumnBinding, ...]
    source_sql: str | None = None

    @property
    def spec(self) -> RelationSpec:
        return self.context.relation(self.relation)

    @property
    def schema(self) -> SchemaId:
        return self.spec.schema

    @property
    def constraints(self) -> tuple[ConstraintDecl, ...]:
        return self.spec.constraints

    def column_spec(self, column: ColumnId) -> ColumnSpec:
        return self.spec.columns[self.spec.column_position(column)]


@dataclass(frozen=True, slots=True)
class FunctionDecl:
    name: QualifiedName
    function: FunctionId


@dataclass(frozen=True, slots=True)
class AggregateDecl:
    name: QualifiedName
    aggregate: AggregateSpecId


KeyColumnInput = str | Identifier | ColumnId


class Catalog:
    """Names, source metadata, and constraint expressions for one Context.

    DDL construction uses a private, fresh catalog. No rollback or term-ID
    reuse is needed: a failed import is never published to its caller.
    """

    def __init__(self, context: Context | None = None, *, dialect: str | None = None):
        self.dialect = SQLDialect(
            dialect or (context.dialect if context else None) or "postgres"
        )
        if context is not None and context.dialect is not None:
            if SQLDialect(context.dialect).name != self.dialect.name:
                raise CatalogError("Catalog dialect conflicts with its context")
        self.context = context if context is not None else Context()
        self.context.dialect = self.dialect.name
        self.constraint_arena = TermArena(self.context)
        self._tables: dict[NameKey, TableDecl] = {}
        self._functions: dict[NameKey, list[FunctionDecl]] = {}
        self._aggregates: dict[NameKey, list[AggregateDecl]] = {}
        self._collations: dict[NameKey, CollationId] = {}

    @classmethod
    def from_ddl(cls, ddl: str, *, dialect: str = "postgres") -> Catalog:
        from parseval.parser.ddls.parse import _populate_catalog

        catalog = cls(dialect=dialect)
        _populate_catalog(catalog, ddl)
        return catalog

    def tables(self) -> tuple[TableDecl, ...]:
        return tuple(self._tables.values())

    def register_table(
        self,
        name: NameInput,
        columns: Iterable[ColumnDecl],
        *,
        source_sql: str | None = None,
    ) -> TableDecl:
        qualified = self.dialect.qualified_name(name)
        key = name_key(qualified)
        if key in self._tables:
            raise CatalogError(f"Duplicate table {qualified.qualified_name!r}")
        columns = tuple(
            replace(c, name=self.dialect.identifier(c.name, column=True))
            for c in columns
        )
        if not columns or len({c.name.text for c in columns}) != len(columns):
            raise CatalogError("Table columns must be nonempty and have unique names")
        declared_types = []
        for column in columns:
            if not isinstance(column.sort, ScalarSort):
                raise CatalogError("Column sort must be ScalarSort")
            if column.collation is not None:
                self.context.require_collation(column.collation)
            declared_types.append(
                column.declared_type or self.dialect.type_sql(column.sort.sql_type)
            )
        relation = self.context.allocate_id(RelationId)
        specs = tuple(
            ColumnSpec(self.context.allocate_id(ColumnId), c.sort, c.collation)
            for c in columns
        )
        schema = self.context.intern_schema(RowShape(tuple(c.sort for c in specs)))
        not_null = tuple(
            NotNullDecl(self._metadata(), relation, c.id)
            for c in specs
            if not c.sort.nullable
        )
        self.context.register_relation(relation, RelationSpec(schema, specs, not_null))
        table = TableDecl(
            qualified,
            relation,
            self.context,
            tuple(
                ColumnBinding(spec.id, column.name, declared, column.default_sql)
                for spec, column, declared in zip(specs, columns, declared_types)
            ),
            source_sql,
        )
        self._tables[key] = table
        return table

    def resolve_table(self, name: NameInput) -> TableDecl:
        qualified = self.dialect.qualified_name(name)
        key = name_key(qualified)
        if key in self._tables:
            return self._tables[key]
        matches = [
            table
            for candidate, table in self._tables.items()
            if len(key) == 1 and candidate[-1] == key[0]
        ]
        if len(matches) == 1:
            return matches[0]
        reason = "Ambiguous" if matches else "Unknown"
        raise CatalogError(f"{reason} table {qualified.qualified_name!r}")

    def resolve_column(self, table: TableDecl, column: KeyColumnInput) -> ColumnBinding:
        self._require_table(table)
        if isinstance(column, ColumnId):
            matches = [c for c in table.columns if c.id == column]
        else:
            identifier = self.dialect.identifier(column, column=True)
            matches = [c for c in table.columns if c.name.text == identifier.text]
        if len(matches) != 1:
            raise CatalogError(
                f"Unknown column {column!r} in {table.name.qualified_name}"
            )
        return matches[0]

    def register_not_null(
        self,
        table: TableDecl,
        column: KeyColumnInput,
        *,
        name: Identifier | None = None,
        source_sql: str | None = None,
    ) -> NotNullDecl:
        target = self.resolve_column(table, column)
        if table.column_spec(target.id).sort.nullable:
            raise CatalogError("NOT NULL must be resolved before relation registration")
        existing = next(
            (
                c
                for c in table.constraints
                if isinstance(c, NotNullDecl) and c.column == target.id
            ),
            None,
        )
        if existing is None:
            item = NotNullDecl(
                self._metadata(name, source_sql=source_sql), table.relation, target.id
            )
            self._attach(table, item)
            return item
        metadata = replace(
            existing.metadata,
            name=self.dialect.identifier(name) if name is not None else None,
            source_sql=source_sql,
        )
        updated = replace(existing, metadata=metadata)
        constraints = tuple(updated if c is existing else c for c in table.constraints)
        try:
            self.context.replace_relation(
                table.relation, replace(table.spec, constraints=constraints)
            )
        except ValueError as exc:
            raise CatalogError(str(exc)) from exc
        return updated

    def register_primary_key(
        self,
        table: TableDecl,
        columns: Iterable[KeyColumnInput],
        *,
        name: Identifier | None = None,
        source_sql: str | None = None,
        inactive_reason: str | None = None,
    ) -> PrimaryKeyDecl:
        ids = self._column_ids(table, columns)
        if self.dialect.name != "sqlite" and any(
            table.column_spec(c).sort.nullable for c in ids
        ):
            raise CatalogError(
                "Primary-key columns must be non-null before registration"
            )
        inactive_reason = inactive_reason or self._key_inactive_reason(table, ids)
        item = PrimaryKeyDecl(
            self._metadata(
                name, source_sql=source_sql, inactive_reason=inactive_reason
            ),
            table.relation,
            ids,
        )
        self._attach(table, item)
        return item

    def register_unique(
        self,
        table: TableDecl,
        columns: Iterable[KeyColumnInput],
        *,
        null_policy: NullConflictPolicy = NullConflictPolicy.NULLS_DISTINCT,
        name: Identifier | None = None,
        source_sql: str | None = None,
        inactive_reason: str | None = None,
    ) -> UniqueDecl:
        ids = self._column_ids(table, columns)
        inactive_reason = inactive_reason or self._key_inactive_reason(table, ids)
        item = UniqueDecl(
            self._metadata(
                name, source_sql=source_sql, inactive_reason=inactive_reason
            ),
            table.relation,
            ids,
            null_policy,
        )
        self._attach(table, item)
        return item

    def register_foreign_key(
        self,
        source: TableDecl,
        source_columns: Iterable[KeyColumnInput],
        target: TableDecl,
        target_columns: Iterable[KeyColumnInput] | None = None,
        *,
        match: ForeignKeyMatch = ForeignKeyMatch.SIMPLE,
        name: Identifier | None = None,
        source_sql: str | None = None,
        inactive_reason: str | None = None,
    ) -> ForeignKeyDecl:
        self._require_table(target)
        source_ids = self._column_ids(source, source_columns)
        if target_columns is None:
            keys = [c for c in target.constraints if isinstance(c, PrimaryKeyDecl)]
            if len(keys) != 1:
                raise CatalogError(
                    "Foreign key omits target columns but target has no primary key"
                )
            target_ids = keys[0].columns
        else:
            target_ids = tuple(
                self.resolve_column(target, column).id for column in target_columns
            )
            if not target_ids:
                raise CatalogError("Foreign-key target columns must be nonempty")
        if len(source_ids) != len(target_ids):
            raise CatalogError("Foreign-key columns must be paired")
        if not target.spec.has_unconditional_key(target_ids):
            inactive_reason = (
                inactive_reason or "Referenced key is not an active proof assumption"
            )
        inactive_reason = inactive_reason or self._key_inactive_reason(
            source, source_ids
        )
        if match is ForeignKeyMatch.PARTIAL:
            inactive_reason = "MATCH PARTIAL semantics are unsupported"
        item = ForeignKeyDecl(
            self._metadata(
                name, source_sql=source_sql, inactive_reason=inactive_reason
            ),
            source.relation,
            source_ids,
            target.relation,
            target_ids,
            match,
        )
        self._attach(source, item)
        return item

    def register_check(
        self,
        table: TableDecl,
        predicate: CatalogExpression,
        *,
        name: Identifier | None = None,
        source_sql: str | None = None,
    ) -> CheckDecl:
        self._require_expression(table, predicate, PREDICATE)
        item = CheckDecl(
            self._metadata(name, source_sql=source_sql), table.relation, predicate
        )
        self._attach(table, item)
        return item

    def register_generated_column(
        self,
        table: TableDecl,
        column: KeyColumnInput,
        expression: CatalogExpression,
        *,
        name: Identifier | None = None,
        source_sql: str | None = None,
    ) -> GeneratedColumnDecl:
        target = self.resolve_column(table, column)
        self._require_expression(table, expression, table.column_spec(target.id).sort)
        generated = {
            c.column for c in table.constraints if isinstance(c, GeneratedColumnDecl)
        } | {target.id}
        for term in self.constraint_arena.post_order((expression.term,)):
            node = self.constraint_arena[term]
            if (
                isinstance(node, terms.Field)
                and table.spec.columns[node.payload.index].id in generated
            ):
                raise CatalogError(
                    "Generated expressions cannot reference generated columns"
                )
        # Also reject a newly generated column referenced by an earlier one.
        for item in table.constraints:
            if isinstance(item, GeneratedColumnDecl):
                for term in self.constraint_arena.post_order((item.expression.term,)):
                    node = self.constraint_arena[term]
                    if (
                        isinstance(node, terms.Field)
                        and table.spec.columns[node.payload.index].id == target.id
                    ):
                        raise CatalogError(
                            "Generated expressions cannot reference generated columns"
                        )
        item = GeneratedColumnDecl(
            self._metadata(name, source_sql=source_sql),
            table.relation,
            target.id,
            expression,
        )
        self._attach(table, item)
        return item

    def register_unsupported_constraint(
        self,
        table: TableDecl,
        kind: str,
        reason: str,
        *,
        name: Identifier | None = None,
        source_sql: str | None = None,
    ) -> UnsupportedConstraintDecl:
        item = UnsupportedConstraintDecl(
            self._metadata(name, source_sql=source_sql, inactive_reason=reason),
            table.relation,
            kind,
        )
        self._attach(table, item)
        return item

    def register_collation(self, name: NameInput) -> CollationId:
        key = name_key(self.dialect.qualified_name(name))
        if key not in self._collations:
            identity = self.context.allocate_id(CollationId)
            self.context.register_collation(identity)
            self._collations[key] = identity
        return self._collations[key]

    def register_scalar_function(
        self, name: NameInput, spec: ScalarFunctionSpec
    ) -> FunctionDecl:
        qualified = self.dialect.qualified_name(name)
        bucket = self._functions.setdefault(name_key(qualified), [])
        for item in bucket:
            previous = self.context.function(item.function)
            if previous.parameters == spec.parameters:
                if previous != spec:
                    raise CatalogError("Conflicting scalar function signature")
                return item
        identity = self.context.allocate_id(FunctionId)
        self.context.register_function(identity, spec)
        item = FunctionDecl(qualified, identity)
        bucket.append(item)
        return item

    def resolve_scalar_function(
        self, name: NameInput, arguments: tuple[ScalarSort, ...]
    ) -> FunctionDecl:
        qualified = self.dialect.qualified_name(name)
        matches = [
            item
            for item in self._named(self._functions, name_key(qualified))
            if self.context.function(item.function).parameters == arguments
        ]
        if len(matches) != 1:
            raise CatalogError(
                f"Unknown or ambiguous scalar function {qualified.qualified_name!r}"
            )
        return matches[0]

    def register_aggregate(self, name: NameInput, spec: AggregateSpec) -> AggregateDecl:
        qualified = self.dialect.qualified_name(name)
        bucket = self._aggregates.setdefault(name_key(qualified), [])
        for item in bucket:
            previous = self.context.aggregate(item.aggregate)
            if previous.input == spec.input:
                if previous != spec:
                    raise CatalogError("Conflicting aggregate signature")
                return item
        identity = self.context.allocate_id(AggregateSpecId)
        self.context.register_aggregate(identity, spec)
        item = AggregateDecl(qualified, identity)
        bucket.append(item)
        return item

    def resolve_aggregate(
        self, name: NameInput, argument: ScalarSort | None
    ) -> AggregateDecl:
        qualified = self.dialect.qualified_name(name)
        matches = [
            item
            for item in self._named(self._aggregates, name_key(qualified))
            if self.context.aggregate(item.aggregate).input == argument
        ]
        if len(matches) != 1:
            raise CatalogError(
                f"Unknown or ambiguous aggregate {qualified.qualified_name!r}"
            )
        return matches[0]

    def to_schema_mapping(self):
        from sqlglot import exp
        from sqlglot.schema import MappingSchema

        mapping = MappingSchema(dialect=self.dialect.name, normalize=False)
        depths = {len(t.name.parts) for t in self.tables()}
        if len(depths) > 1:
            raise CatalogError(
                "SQLGlot schema export requires equally qualified table names"
            )
        for table in self.tables():
            identifiers = [
                exp.Identifier(this=p.text, quoted=p.quoted) for p in table.name.parts
            ]
            node = exp.Table(this=identifiers[-1])
            if len(identifiers) >= 2:
                node.set("db", identifiers[-2])
            if len(identifiers) == 3:
                node.set("catalog", identifiers[0])
            mapping.add_table(
                node,
                {c.name.text: c.declared_type for c in table.columns},
                normalize=False,
            )
        return mapping

    def _require_table(self, table: TableDecl) -> None:
        if (
            table.context is not self.context
            or self._tables.get(name_key(table.name)) is not table
        ):
            raise CatalogError("Table belongs to another catalog")

    def _column_ids(
        self, table: TableDecl, columns: Iterable[KeyColumnInput]
    ) -> tuple[ColumnId, ...]:
        self._require_table(table)
        ids = tuple(self.resolve_column(table, c).id for c in columns)
        if not ids or len(set(ids)) != len(ids):
            raise CatalogError("Constraint columns must be nonempty and distinct")
        return ids

    @staticmethod
    def _key_inactive_reason(
        table: TableDecl, columns: tuple[ColumnId, ...]
    ) -> str | None:
        if any(table.column_spec(c).collation is not None for c in columns):
            return "Collated key comparisons are unsupported"
        return None

    def _metadata(
        self, name=None, *, source_sql=None, inactive_reason=None
    ) -> ConstraintMetadata:
        return ConstraintMetadata(
            self.context.allocate_id(ConstraintId),
            self.dialect.identifier(name) if name is not None else None,
            inactive_reason,
            source_sql,
        )

    def _attach(self, table: TableDecl, item: ConstraintDecl) -> None:
        self._require_table(table)
        try:
            self.context.replace_relation(
                table.relation,
                replace(table.spec, constraints=(*table.constraints, item)),
            )
        except (ValueError, KeyError) as exc:
            raise CatalogError(str(exc)) from exc

    def _require_expression(
        self, table: TableDecl, expression: CatalogExpression, result: Sort
    ) -> None:
        self._require_table(table)
        actual = verify_closed(self.constraint_arena, expression.term)
        if (
            not isinstance(actual, RowFunctionSort)
            or actual.input.schema != table.schema
        ):
            raise CatalogError(
                "Constraint expression must be a row function over its table"
            )
        compatible = actual.result == result
        if isinstance(actual.result, ScalarSort) and isinstance(result, ScalarSort):
            compatible = (
                actual.result.sql_type == result.sql_type
                and actual.result.nullable <= result.nullable
            )
        if not compatible:
            raise CatalogError("Constraint expression has an incompatible result sort")
        allowed = (
            terms.RowLambda,
            terms.RowVar,
            terms.Field,
            terms.Literal,
            terms.Null,
            terms.ScalarCall,
            terms.Case,
            terms.ToPredicate,
            terms.ToBoolean,
            terms.True3,
            terms.False3,
            terms.Unknown3,
            terms.Eq3,
            terms.Lt3,
            terms.Like3,
            terms.ILike3,
            terms.IsNull,
            terms.IsNotNull,
            terms.IsNotDistinct,
            terms.And3,
            terms.Or3,
            terms.Not3,
        )
        for term in self.constraint_arena.post_order((expression.term,)):
            node = self.constraint_arena[term]
            if not isinstance(node, allowed):
                raise CatalogError(
                    f"Constraint expression is not row-local: {node.key}"
                )
            if isinstance(node, terms.RowLambda) and term != expression.term:
                raise CatalogError(
                    "Nested row functions are not allowed in constraints"
                )
            if isinstance(node, terms.Field):
                if table.spec.columns[node.payload.index].collation is not None:
                    raise CatalogError(
                        "Collated constraint expressions are unsupported"
                    )
            if isinstance(node, terms.ScalarCall):
                if (
                    self.context.function(node.payload.function).volatility
                    is not Volatility.IMMUTABLE
                ):
                    raise CatalogError(
                        "Constraint expressions require immutable functions"
                    )

    @staticmethod
    def _named(mapping, key):
        if key in mapping:
            return mapping[key]
        return [
            item
            for candidate, bucket in mapping.items()
            if len(key) == 1 and candidate[-1] == key[0]
            for item in bucket
        ]
