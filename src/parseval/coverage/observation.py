"""Typed semantic tests used by concrete and symbolic U-expression evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import TypeAlias

from parseval.terms.terms import TermId


class TruthOutcome(IntEnum):
    UNKNOWN = -1
    FALSE = 0
    TRUE = 1


@dataclass(frozen=True, slots=True)
class WeightCondition:
    term: TermId
    minimum: int = 1
    maximum: int | None = None

    def __post_init__(self) -> None:
        if self.minimum < 0 or (self.maximum is not None and self.maximum < self.minimum):
            raise ValueError("Invalid multiplicity interval")


@dataclass(frozen=True, slots=True)
class PredicateCondition:
    term: TermId
    truth: TruthOutcome


@dataclass(frozen=True, slots=True)
class NullCondition:
    term: TermId
    is_null: bool


@dataclass(frozen=True, slots=True)
class BagCardinalityCondition:
    term: TermId
    minimum: int = 0
    maximum: int | None = None

    def __post_init__(self) -> None:
        if self.minimum < 0 or (self.maximum is not None and self.maximum < self.minimum):
            raise ValueError("Invalid bag-cardinality interval")


@dataclass(frozen=True, slots=True)
class GroupCardinalityCondition:
    """Some existing group of a fold has this many input row occurrences.

    For a global fold, its single group exists even on empty input. The term
    is the fold, not its output bag (whose weights are always one).
    """

    term: TermId
    minimum: int = 1
    maximum: int | None = None

    def __post_init__(self) -> None:
        if self.minimum < 0 or (self.maximum is not None and self.maximum < self.minimum):
            raise ValueError("Invalid group-cardinality interval")


Condition: TypeAlias = (
    WeightCondition | PredicateCondition | NullCondition | BagCardinalityCondition
    | GroupCardinalityCondition
)


__all__ = [
    "BagCardinalityCondition", "GroupCardinalityCondition", "Condition", "NullCondition", "PredicateCondition",
    "TruthOutcome", "WeightCondition",
]
