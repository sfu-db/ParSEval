"""Concrete database instances keyed by semantic relation identities."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TypeAlias

from parseval.catalog import Catalog
from parseval.terms.names import RelationId, SchemaId

ScalarValue: TypeAlias = object
RowValue: TypeAlias = tuple[ScalarValue, ...]


@dataclass(frozen=True, slots=True)
class Row:
    """One typed row. SQL NULL is represented by ``None``."""

    schema: SchemaId
    values: RowValue

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))


@dataclass(frozen=True, slots=True, init=False)
class Instance:
    """A validated concrete state of the materialized catalog relations.

    Relations omitted at construction are valid empty relations. Duplicate
    rows remain repeated entries, preserving SQL bag semantics without
    requiring scalar values to be hashable.
    """

    catalog: Catalog
    _rows: tuple[tuple[RelationId, tuple[Row, ...]], ...]

    def __init__(
        self,
        catalog: Catalog,
        rows: Mapping[
            RelationId, Iterable[Sequence[ScalarValue] | Row]
        ] | None = None,
    ) -> None:
        materialized = tuple(
            sorted(
                (
                    (relation, _materialize_rows(catalog, relation, values))
                    for relation, values in (rows or {}).items()
                ),
                key=lambda item: item[0].value,
            )
        )
        object.__setattr__(self, "catalog", catalog)
        object.__setattr__(self, "_rows", materialized)

    @classmethod
    def empty(cls, catalog: Catalog) -> Instance:
        return cls(catalog)

    @classmethod
    def from_rows(
        cls,
        catalog: Catalog,
        rows: Mapping[
            RelationId, Iterable[Sequence[ScalarValue] | Row]
        ],
    ) -> Instance:
        return cls(catalog, rows)

    def rows(self, relation: RelationId) -> tuple[Row, ...]:
        # Validate ownership before applying the closed-world empty default.
        self.catalog.context.relation(relation)
        return next(
            (rows for candidate, rows in self._rows if candidate == relation),
            (),
        )

    def with_rows(
        self,
        relation: RelationId,
        rows: Iterable[Sequence[ScalarValue] | Row],
    ) -> Instance:
        replacements: dict[
            RelationId, Iterable[Sequence[ScalarValue] | Row]
        ] = dict(self._rows)
        replacements[relation] = rows
        return Instance(self.catalog, replacements)


def _materialize_rows(
    catalog: Catalog,
    relation: RelationId,
    rows: Iterable[Sequence[ScalarValue] | Row],
) -> tuple[Row, ...]:
    specification = catalog.context.relation(relation)
    schema = specification.schema
    width = len(catalog.context.schema(schema).fields)
    materialized = tuple(
        row if isinstance(row, Row) else Row(schema, tuple(row)) for row in rows
    )
    if any(row.schema != schema or len(row.values) != width for row in materialized):
        raise ValueError(f"Rows for {relation!r} do not match {schema!r}")
    return materialized


__all__ = [
    "Instance",
    "Row",
    "RowValue",
    "ScalarValue",
]
