from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from sqlglot import exp


PARSEVAL_COLUMN_ID = "parseval_column_id"
PARSEVAL_SEMANTIC_DATATYPE = "parseval_semantic_datatype"


@dataclass(frozen=True)
class IdentifierName:
    raw: str
    normalized: str
    quoted: bool
    dialect: str | None = None

    @property
    def display(self) -> str:
        return self.raw


class RelationKind(Enum):
    TABLE = "table"
    CTE = "cte"
    SUBQUERY = "subquery"
    VALUES = "values"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class RelationId:
    kind: RelationKind
    name: IdentifierName | None
    catalog: IdentifierName | None = None
    db: IdentifierName | None = None
    alias: IdentifierName | None = None
    scope_id: str | None = None

    @property
    def display(self) -> str:
        visible = self.alias or self.name
        return visible.display if visible is not None else self.kind.value

    @property
    def binding_display(self) -> str:
        visible = self.display
        return f"{visible}@{self.scope_id}" if self.scope_id else visible


class ColumnKind(Enum):
    PHYSICAL = "physical"
    PROJECTED = "projected"
    DERIVED = "derived"
    AGGREGATE = "aggregate"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class ColumnId:
    kind: ColumnKind
    name: IdentifierName
    relation: RelationId | None
    scope_id: str | None = None
    ordinal: int | None = None
    source_column_id: "ColumnId | None" = None

    @property
    def display(self) -> str:
        if self.relation is None:
            return self.name.display
        return f"{self.relation.display}.{self.name.display}"


@dataclass(frozen=True)
class ColumnRef:
    ast: exp.Column
    name: IdentifierName
    qualifier: IdentifierName | None
    scope_id: str
    resolved: ColumnId | None = None


@dataclass(frozen=True)
class CatalogColumn:
    id: ColumnId
    datatype: exp.DataType
    nullable: bool
    unique: bool
    primary_key: bool


def identifier_name(value: exp.Identifier | str, dialect: str | None = None) -> IdentifierName:
    if isinstance(value, exp.Identifier):
        raw = value.name
        quoted = value.quoted
    else:
        raw = str(value)
        quoted = False
    # Use dialect-aware normalization via sqlglot's Dialect.normalize_identifier.
    # This handles dialect-specific rules (e.g., SQLite lowercases even quoted identifiers).
    if dialect:
        from sqlglot.dialects.dialect import Dialect
        dialect_obj = Dialect.get_or_raise(dialect)
        normalized = dialect_obj.normalize_identifier(exp.to_identifier(raw, quoted=quoted)).name
    else:
        normalized = raw.lower()
    return IdentifierName(raw=raw, normalized=normalized, quoted=quoted, dialect=dialect)


def identifier_key(value: exp.Identifier | str, dialect: str | None = None) -> str:
    return identifier_name(value, dialect=dialect).normalized


def relation_id(
    kind: RelationKind,
    name: IdentifierName | None,
    *,
    catalog: IdentifierName | None = None,
    db: IdentifierName | None = None,
    alias: IdentifierName | None = None,
    scope_id: str | None = None,
) -> RelationId:
    return RelationId(kind=kind, name=name, catalog=catalog, db=db, alias=alias, scope_id=scope_id)


def column_id(
    kind: ColumnKind,
    name: IdentifierName,
    relation: RelationId | None,
    *,
    scope_id: str | None = None,
    ordinal: int | None = None,
    source_column_id: ColumnId | None = None,
) -> ColumnId:
    return ColumnId(
        kind=kind,
        name=name,
        relation=relation,
        scope_id=scope_id,
        ordinal=ordinal,
        source_column_id=source_column_id,
    )


def column_identity(node: exp.Column) -> ColumnId | None:
    value: Any = node.meta.get(PARSEVAL_COLUMN_ID)
    return value if isinstance(value, ColumnId) else None


def iter_scope_columns(expression: exp.Expression):
    """Yield columns from one SQL scope without entering subqueries."""
    stack = [expression]
    while stack:
        node = stack.pop()
        if isinstance(node, exp.Column):
            yield node
            continue
        if isinstance(node, (exp.Subquery, exp.Exists)):
            continue
        if isinstance(node, exp.In) and isinstance(
            node.args.get("query"),
            exp.Expression,
        ):
            if isinstance(node.this, exp.Expression):
                stack.append(node.this)
            continue
        for child in node.args.values():
            if isinstance(child, exp.Expression):
                stack.append(child)
            elif isinstance(child, list):
                stack.extend(
                    item for item in child if isinstance(item, exp.Expression)
                )


def table_relation(name: str, dialect: str | None = None) -> RelationId:
    """Create a TABLE RelationId from a raw name string."""
    return relation_id(RelationKind.TABLE, identifier_name(name, dialect=dialect))


def physical_column(name: str, relation: RelationId, dialect: str | None = None) -> ColumnId:
    """Create a PHYSICAL ColumnId from a raw column name string."""
    return column_id(ColumnKind.PHYSICAL, identifier_name(name, dialect=dialect), relation)
