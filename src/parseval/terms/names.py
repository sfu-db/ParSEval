from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Identifier:
    """An SQL identifier as written in source."""

    text: str
    quoted: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("Identifier.text must be a string")
        if not self.text:
            raise ValueError("Identifier must not be empty")
        if not isinstance(self.quoted, bool):
            raise TypeError("Identifier.quoted must be a bool")

    @property
    def qualified_name(self) -> str:
        """Render the identifier using SQL double-quote escaping."""
        if not self.quoted:
            return self.text
        return f'"{self.text.replace(chr(34), chr(34) * 2)}"'


@dataclass(frozen=True, slots=True)
class QualifiedName:
    parts: tuple[Identifier, ...]

    def __post_init__(self) -> None:
        if not self.parts:
            raise ValueError("Qualified name requires at least one component")
        if not all(isinstance(part, Identifier) for part in self.parts):
            raise TypeError("Qualified name components must be Identifier values")

    @property
    def qualified_name(self) -> str:
        """Render the components, preserving explicit quoting."""
        return ".".join(part.qualified_name for part in self.parts)


NameKey = tuple[str, ...]

NameInput = str | Identifier | QualifiedName | Iterable[str | Identifier]


def name_key(name: QualifiedName) -> NameKey:
    """Return the dialect-normalized catalog identity of ``name``.

    Quoting is intentionally absent from the key. The dialect has already used
    quoting when deciding how to normalize each identifier's text.
    """

    return tuple(part.text for part in name.parts)


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
