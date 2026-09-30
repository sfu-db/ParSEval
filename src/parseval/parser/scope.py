from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, NoReturn

from sqlglot import exp

from parseval.errors import ErrorCode, fail
from parseval.parser.dialect import SQLDialect
from parseval.terms.builder import TermRef
from parseval.terms.names import CollationId, Identifier, NameKey, SchemaId
from parseval.terms.sorts import ScalarSort

from .syntax import strip_alias


@dataclass(frozen=True, slots=True)
class FieldSlot:
    index: int
    sort: ScalarSort


@dataclass(frozen=True, slots=True)
class ColumnBinding:
    name: Identifier
    sort: ScalarSort
    qualifiers: frozenset[NameKey]
    collation: CollationId | None = None


@dataclass(frozen=True, slots=True)
class Relation:
    term: TermRef
    schema: SchemaId
    columns: tuple[ColumnBinding, ...]


@dataclass(frozen=True, slots=True)
class OuterScope:
    relation: Relation
    row: TermRef


@dataclass(frozen=True, slots=True)
class EmitEnvironment:
    relation: Relation
    row: TermRef
    expressions: Mapping[str, FieldSlot]
    outer_scopes: tuple[OuterScope, ...] = ()


class ColumnBinder:
    """Resolve a SQL column against one lexical relation-scope chain."""

    __slots__ = ("dialect",)

    def __init__(self, dialect: SQLDialect) -> None:
        self.dialect = dialect

    def column_name(
        self, expression: exp.Expression
    ) -> tuple[Identifier, NameKey | None]:
        expression = strip_alias(expression)
        if not isinstance(expression, exp.Column):
            raise TypeError("Expected a SQL column expression")
        name = self.dialect.identifier(expression.this)
        parts = tuple(
            self.dialect.identifier(
                expression.args[key],
                is_table=True,
            ).text
            for key in ("catalog", "db", "table")
            if expression.args.get(key) is not None
        )
        return name, parts or None

    def resolve(
        self,
        expression: exp.Expression,
        relation: Relation,
        *,
        outer_scopes: tuple[OuterScope, ...] = (),
        allow_missing: bool = False,
    ) -> tuple[OuterScope | None, int, ColumnBinding] | None:
        expression = strip_alias(expression)
        if not isinstance(expression, exp.Column):
            return None
        name, qualifier = self.column_name(expression)
        matches = self.matching_indices(expression, relation)
        if len(matches) == 1:
            index = matches[0]
            return None, index, relation.columns[index]
        if len(matches) > 1:
            self._ambiguous(name, qualifier, expression)
        for scope in outer_scopes:
            matches = self.matching_indices(expression, scope.relation)
            if len(matches) == 1:
                index = matches[0]
                return scope, index, scope.relation.columns[index]
            if len(matches) > 1:
                self._ambiguous(name, qualifier, expression)
        if allow_missing:
            return None
        display = ".".join((*qualifier, name.text)) if qualifier else name.text
        fail(
            ErrorCode.UNKNOWN_COLUMN,
            f"Unknown column {display}",
            node=expression,
        )

    def local_index(
        self,
        expression: exp.Expression,
        relation: Relation,
        *,
        allow_missing: bool = False,
    ) -> int | None:
        resolved = self.resolve(
            expression,
            relation,
            allow_missing=allow_missing,
        )
        if resolved is None:
            return None
        scope, index, _ = resolved
        return index if scope is None else None

    def matching_indices(
        self,
        expression: exp.Expression,
        relation: Relation,
    ) -> tuple[int, ...]:
        name, qualifier = self.column_name(expression)
        return tuple(
            index
            for index, column in enumerate(relation.columns)
            if column.name.text == name.text
            and (qualifier is None or qualifier in column.qualifiers)
        )

    @staticmethod
    def _ambiguous(
        name: Identifier,
        qualifier: NameKey | None,
        expression: exp.Expression,
    ) -> NoReturn:
        display = ".".join((*qualifier, name.text)) if qualifier else name.text
        fail(
            ErrorCode.AMBIGUOUS_COLUMN,
            f"Ambiguous column {display!r}",
            node=expression,
        )
