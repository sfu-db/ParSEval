from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple


@dataclass(frozen=True)
class SchemaConstraint:
    """Base type for schema-only constraints."""


@dataclass(frozen=True)
class NotNullConstraint(SchemaConstraint):
    pass


@dataclass(frozen=True)
class UniqueConstraint(SchemaConstraint):
    columns: Tuple[str, ...]


@dataclass(frozen=True)
class RangeConstraint(SchemaConstraint):
    minimum: Optional[Any] = None
    maximum: Optional[Any] = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True


@dataclass(frozen=True)
class LengthConstraint(SchemaConstraint):
    minimum: Optional[int] = None
    maximum: Optional[int] = None


@dataclass(frozen=True)
class ChoicesConstraint(SchemaConstraint):
    values: Tuple[Any, ...]


@dataclass(frozen=True)
class PatternConstraint(SchemaConstraint):
    pattern: str


@dataclass(frozen=True)
class CheckConstraint(SchemaConstraint):
    expression: Any


@dataclass(frozen=True)
class ModuloConstraint(SchemaConstraint):
    divisor: int
    remainder: int = 0


@dataclass(frozen=True)
class PrefixConstraint(SchemaConstraint):
    prefix: str


@dataclass(frozen=True)
class SuffixConstraint(SchemaConstraint):
    suffix: str


@dataclass(frozen=True)
class ContainsConstraint(SchemaConstraint):
    substring: str
