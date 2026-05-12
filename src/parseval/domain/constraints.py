from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple


@dataclass(frozen=True)
class SchemaConstraint:
    """Base type for schema-only constraints."""


@dataclass(frozen=True)
class NotNullConstraint(SchemaConstraint):
    """Ensures the column value is never NULL."""


@dataclass(frozen=True)
class UniqueConstraint(SchemaConstraint):
    """Enforces uniqueness across the specified columns.

    Attributes:
        columns: Tuple of column names that must be unique together.
    """
    columns: Tuple[str, ...]


@dataclass(frozen=True)
class RangeConstraint(SchemaConstraint):
    """Constrains a value to lie within an inclusive or exclusive range.

    Attributes:
        minimum: Lower bound, or None for no lower bound.
        maximum: Upper bound, or None for no upper bound.
        minimum_inclusive: Whether the minimum value is allowed (default True).
        maximum_inclusive: Whether the maximum value is allowed (default True).
    """
    minimum: Optional[Any] = None
    maximum: Optional[Any] = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True


@dataclass(frozen=True)
class LengthConstraint(SchemaConstraint):
    """Constrains the length of a string or bytes value.

    Attributes:
        minimum: Minimum allowed length, or None for no minimum.
        maximum: Maximum allowed length, or None for no maximum.
    """
    minimum: Optional[int] = None
    maximum: Optional[int] = None


@dataclass(frozen=True)
class ChoicesConstraint(SchemaConstraint):
    """Restricts a column value to one of an explicit set of choices.

    Attributes:
        values: Tuple of allowed values.
    """
    values: Tuple[Any, ...]


@dataclass(frozen=True)
class PatternConstraint(SchemaConstraint):
    """Restricts a column value to match a regular expression pattern.

    Attributes:
        pattern: Regular expression pattern to match against.
    """
    pattern: str


@dataclass(frozen=True)
class CheckConstraint(SchemaConstraint):
    """Arbitrary predicate constraint evaluated after value generation.

    Attributes:
        expression: Callable that takes a value and returns bool, or any
            opaque expression checked at validation time.
    """
    expression: Any


@dataclass(frozen=True)
class ModuloConstraint(SchemaConstraint):
    """Constrains a value to satisfy ``value % divisor == remainder``.

    Attributes:
        divisor: The modulus divisor.
        remainder: Required remainder (default 0).
    """
    divisor: int
    remainder: int = 0


@dataclass(frozen=True)
class PrefixConstraint(SchemaConstraint):
    """Requires the value to start with a given prefix string.

    Attributes:
        prefix: Required leading substring.
    """
    prefix: str


@dataclass(frozen=True)
class SuffixConstraint(SchemaConstraint):
    """Requires the value to end with a given suffix string.

    Attributes:
        suffix: Required trailing substring.
    """
    suffix: str


@dataclass(frozen=True)
class ContainsConstraint(SchemaConstraint):
    """Requires the value to contain a given substring.

    Attributes:
        substring: Required substring.
    """
    substring: str
