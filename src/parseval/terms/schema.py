"""Anonymous row shapes and semantic base-relation declarations."""

from __future__ import annotations

from dataclasses import dataclass

from .constraints import ConstraintDecl, PrimaryKeyDecl, UniqueDecl
from .names import CollationId, ColumnId, SchemaId
from .sorts import ScalarSort


@dataclass(frozen=True, slots=True)
class Schema:
    """Structural row shape; SQL names are owned by the catalog."""

    fields: tuple[ScalarSort, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.fields, tuple) or not all(
            isinstance(field, ScalarSort) for field in self.fields
        ):
            raise TypeError("Schema.fields must be a tuple of ScalarSort values")


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    id: ColumnId
    sort: ScalarSort
    collation: CollationId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.id, ColumnId):
            raise TypeError("ColumnSpec.id must be ColumnId")
        if not isinstance(self.sort, ScalarSort):
            raise TypeError("ColumnSpec.sort must be ScalarSort")


@dataclass(frozen=True, slots=True)
class RelationSpec:
    schema: SchemaId
    columns: tuple[ColumnSpec, ...]
    constraints: tuple[ConstraintDecl, ...] = ()

    def column_position(self, column: ColumnId) -> int:
        for index, candidate in enumerate(self.columns):
            if candidate.id == column:
                return index
        raise KeyError(f"Unknown column {column!r}")

    def has_unconditional_key(self, columns: tuple[ColumnId, ...]) -> bool:
        """Whether this column set has an active, non-partial PK/UNIQUE.

        NULL policy is retained on the declaration; this is not a claim that
        a nullable UNIQUE key identifies every row.
        """
        return any(
            item.metadata.proof_active
            and isinstance(item, (PrimaryKeyDecl, UniqueDecl))
            and len(item.columns) == len(columns)
            and set(item.columns) == set(columns)
            for item in self.constraints
        )
