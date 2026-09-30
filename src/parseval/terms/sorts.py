from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias, TypeGuard

from .names import (
    AggregateSpecId,
    SchemaId,
)
from .types import ScalarType


class Sort:
    __slots__ = ()


@dataclass(frozen=True, slots=True)
class ScalarSort(Sort):
    sql_type: ScalarType
    nullable: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.sql_type, ScalarType):
            raise TypeError("ScalarSort.sql_type must be a ScalarType")
        if not isinstance(self.nullable, bool):
            raise TypeError("ScalarSort.nullable must be a bool")


@dataclass(frozen=True, slots=True)
class PredicateSort(Sort):
    """SQL three-valued predicate domain."""


@dataclass(frozen=True, slots=True)
class RowSort(Sort):
    schema: SchemaId


@dataclass(frozen=True, slots=True)
class MultiplicitySort(Sort):
    """Natural-number bag multiplicities."""


@dataclass(frozen=True, slots=True)
class BagSort(Sort):
    schema: SchemaId


@dataclass(frozen=True, slots=True)
class SeqSort(Sort):
    schema: SchemaId


@dataclass(frozen=True, slots=True)
class AggStateSort(Sort):
    aggregate: AggregateSpecId


@dataclass(frozen=True, slots=True)
class RowFunctionSort(Sort):
    input: RowSort
    result: Sort


PREDICATE = PredicateSort()
MULTIPLICITY = MultiplicitySort()
RelationSort: TypeAlias = BagSort | SeqSort


def is_relation_sort(sort: object) -> TypeGuard[RelationSort]:
    return isinstance(sort, (BagSort, SeqSort))


def relation_schema(sort: Sort) -> SchemaId:
    if isinstance(sort, (BagSort, SeqSort)):
        return sort.schema
    raise TypeError(f"Expected relation sort, got {sort!r}")
