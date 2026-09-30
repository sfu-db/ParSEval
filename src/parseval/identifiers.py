"""SQL surface identifiers; opaque IR IDs live in ``parseval.terms.names``."""

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
