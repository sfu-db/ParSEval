"""Opaque declaration and catalog identities for the semantic IR."""

from __future__ import annotations

from dataclasses import dataclass


def _validate_nonnegative_int(value: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    if value < 0:
        raise ValueError(f"{label} must be nonnegative")


@dataclass(frozen=True, slots=True, order=True)
class RelationId:
    """Identity of a catalog relation, not a query-local alias occurrence."""

    value: int

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.value, "RelationId")


@dataclass(frozen=True, slots=True, order=True)
class ColumnId:
    """Stable catalog identity of a column, independent of row position."""

    value: int

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.value, "ColumnId")


@dataclass(frozen=True, slots=True, order=True)
class ConstraintId:
    """Stable identity used by declarations and proof provenance."""

    value: int

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.value, "ConstraintId")


@dataclass(frozen=True, slots=True, order=True)
class SchemaId:
    """Identity of an interned row shape (``RowShape``)."""

    value: int

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.value, "SchemaId")


@dataclass(frozen=True, slots=True, order=True)
class FunctionId:
    value: int

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.value, "FunctionId")


@dataclass(frozen=True, slots=True, order=True)
class AggregateSpecId:
    value: int

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.value, "AggregateSpecId")


@dataclass(frozen=True, slots=True, order=True)
class ParameterId:
    value: int

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.value, "ParameterId")


@dataclass(frozen=True, slots=True, order=True)
class CollationId:
    value: int

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.value, "CollationId")


@dataclass(frozen=True, slots=True, order=True)
class CallSiteId:
    value: int

    def __post_init__(self) -> None:
        _validate_nonnegative_int(self.value, "CallSiteId")


@dataclass(frozen=True, slots=True, order=True)
class DefinitionId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("DefinitionId must be a nonempty string")


@dataclass(frozen=True, slots=True, order=True)
class TheoremId:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not self.value:
            raise ValueError("TheoremId must be a nonempty string")
