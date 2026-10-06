"""Relational values produced by U-expression execution.

Rows hold one Term per field. A bag keeps one entry per contribution, so an
entry's weight is a multiplicity Term and equal rows are not merged: the
multiplicity of a row is the sum of the weights of the entries holding it.
"""

from __future__ import annotations

from dataclasses import dataclass

from parseval.terms.names import SchemaId
from parseval.terms.terms import TermId


@dataclass(frozen=True, slots=True)
class RowValue:
    schema: SchemaId
    cells: tuple[TermId, ...]


@dataclass(frozen=True, slots=True)
class Entry:
    """A weighted row; ``candidates`` counts the candidate rows it depends on."""

    row: RowValue
    weight: TermId
    candidates: int = 0


@dataclass(frozen=True, slots=True, eq=False)
class Bag:
    schema: SchemaId
    entries: tuple[Entry, ...]


@dataclass(frozen=True, slots=True, eq=False)
class Sequence:
    """Ordered entries: an entry's occurrences start at its position.

    A position is the summed weight of the entries ordered before it, so
    candidate rows can enter or leave a LIMIT symbolically. Positions are
    derived from the sort ``keys`` only when a LIMIT or OFFSET needs them;
    ``positions`` is set once they are known.
    """

    schema: SchemaId
    entries: tuple[Entry, ...]
    keys: tuple[tuple[TermId, ...], ...]
    specs: tuple
    positions: tuple[TermId, ...] | None = None


Relation = Bag | Sequence


@dataclass(frozen=True, slots=True)
class Binding:
    """Rows assigned to free row variables, with the weight of the assignment.

    ``presence`` multiplies only the weights of the rows that were bound, so a
    branch is reached by real data when its presence is positive regardless
    of the other factors evaluated alongside it.
    """

    rows: tuple[tuple[int, RowValue], ...]
    weight: TermId
    presence: TermId
    candidates: int = 0


@dataclass(frozen=True, slots=True, eq=False)
class Environment:
    """De Bruijn environments; index 0 is the innermost binder. ``None`` is free."""

    rows: tuple[RowValue | None, ...] = ()
    relations: tuple[Relation, ...] = ()
    candidates: int = 0
    """Candidate rows among the bound rows."""

    def push(self, row: RowValue | None) -> Environment:
        return Environment((row, *self.rows), self.relations, self.candidates)

    def push_relation(self, relation: Relation) -> Environment:
        return Environment(self.rows, (relation, *self.relations), self.candidates)

    def bind(self, rows: tuple[tuple[int, RowValue], ...], candidates: int = 0) -> Environment:
        if not rows and not candidates:
            return self
        values = list(self.rows)
        for index, row in rows:
            values[index] = row
        return Environment(tuple(values), self.relations, self.candidates + candidates)

    @property
    def free(self) -> frozenset[int]:
        return frozenset(index for index, row in enumerate(self.rows) if row is None)

    def key(self, rows: frozenset[int]) -> tuple:
        """Identity of the parts of this environment a term can observe."""
        return (
            tuple(self.rows[index] if index < len(self.rows) else None for index in sorted(rows)),
            self.relations,
        )


__all__ = ["Bag", "Binding", "Entry", "Environment", "Relation", "RowValue", "Sequence"]
