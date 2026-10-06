"""Semantic integrity constraints; SQL parsing and proof lowering live elsewhere."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from parseval.identifiers import Identifier
from .names import ColumnId, ConstraintId, RelationId
from .terms import TermId


class NullConflictPolicy(str, Enum):
    NULLS_DISTINCT = "nulls_distinct"
    NULLS_NOT_DISTINCT = "nulls_not_distinct"


class ForeignKeyMatch(str, Enum):
    SIMPLE = "simple"
    FULL = "full"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class CatalogExpression:
    """A row function in the owning catalog's constraint arena.

    Its input and result sorts are derived from the arena, never duplicated.
    """

    term: TermId


@dataclass(frozen=True, slots=True)
class ConstraintMetadata:
    """Source and proof status shared by all constraint declarations."""

    id: ConstraintId
    name: Identifier | None = None
    inactive_reason: str | None = None
    source_sql: str | None = None

    def __post_init__(self) -> None:
        if self.inactive_reason == "":
            raise ValueError("inactive constraint reason must be nonempty")

    @property
    def proof_active(self) -> bool:
        return self.inactive_reason is None


@dataclass(frozen=True, slots=True)
class ConstraintDecl:
    """Common identity and owning relation for every constraint."""

    metadata: ConstraintMetadata
    relation: RelationId


@dataclass(frozen=True, slots=True)
class NotNullDecl(ConstraintDecl):
    column: ColumnId


@dataclass(frozen=True, slots=True)
class PrimaryKeyDecl(ConstraintDecl):
    columns: tuple[ColumnId, ...]
    null_policy: NullConflictPolicy = NullConflictPolicy.NULLS_DISTINCT

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError("primary key requires at least one column")


@dataclass(frozen=True, slots=True)
class UniqueDecl(ConstraintDecl):
    columns: tuple[ColumnId, ...]
    null_policy: NullConflictPolicy

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError("unique constraint requires at least one column")


@dataclass(frozen=True, slots=True)
class ForeignKeyDecl(ConstraintDecl):
    source: tuple[ColumnId, ...]
    target_relation: RelationId
    target: tuple[ColumnId, ...]
    match: ForeignKeyMatch

    def __post_init__(self) -> None:
        width = len(self.source)
        if width == 0 or len(self.target) != width:
            raise ValueError("foreign-key components must be nonempty and paired")


@dataclass(frozen=True, slots=True)
class CheckDecl(ConstraintDecl):
    """A row predicate that must be TRUE or UNKNOWN for each stored row."""

    predicate: CatalogExpression


@dataclass(frozen=True, slots=True)
class GeneratedColumnDecl(ConstraintDecl):
    column: ColumnId
    expression: CatalogExpression


@dataclass(frozen=True, slots=True)
class UnsupportedConstraintDecl(ConstraintDecl):
    """Preserved metadata which is intentionally never a proof assumption."""

    kind: str

    def __post_init__(self) -> None:
        if self.metadata.proof_active:
            raise ValueError("unsupported constraints must be proof-inactive")
