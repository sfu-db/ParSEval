"""Shared types for the solver module."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional
from sqlglot import exp

from parseval.dtype import (
    DataType,
    TypeFamily,
    date_to_epoch_day,
    datetime_to_epoch_second,
    epoch_day_to_date,
    epoch_second_to_datetime,
    infer_type_from_string,
    infer_type_from_value,
    parse_date,
    parse_datetime,
    parse_time,
    seconds_to_time,
    time_to_seconds,
    type_family,
)
from parseval.identity import ColumnId, RelationId
from parseval.domain.value_space import ValueSpace


PARSEVAL_SOLVER_VAR = "parseval_solver_var"


@dataclass(frozen=True, eq=False)
class SolverVar:
    """A logical solver variable for one column binding."""

    column_id: ColumnId
    relation_id: RelationId
    row_scope: str | None = None
    _binding_key: tuple = field(init=False, repr=False)

    def __post_init__(self) -> None:
        relation = self.relation_id
        source = self.column_id.source_column_id
        source_key = None
        if source is not None:
            source_relation = source.relation
            source_key = (
                source.kind.value,
                source_relation.kind.value if source_relation is not None else None,
                source_relation.name.normalized
                if source_relation is not None and source_relation.name is not None
                else None,
                source_relation.alias.normalized
                if source_relation is not None and source_relation.alias is not None
                else None,
                source_relation.scope_id if source_relation is not None else None,
                source.name.normalized,
                source.scope_id,
                source.ordinal,
            )
        object.__setattr__(
            self,
            "_binding_key",
            (
                relation.kind.value,
                relation.name.normalized if relation.name is not None else None,
                relation.alias.normalized if relation.alias is not None else None,
                relation.scope_id,
                self.column_id.kind.value,
                self.column_id.name.normalized,
                self.column_id.scope_id,
                self.column_id.ordinal,
                source_key,
                self.row_scope,
            ),
        )

    @property
    def binding_key(self) -> tuple:
        return self._binding_key

    def __hash__(self) -> int:
        return hash(self.binding_key)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SolverVar):
            return NotImplemented
        return self.binding_key == other.binding_key

    @property
    def display(self) -> str:
        scope = f"#{self.row_scope}" if self.row_scope else ""
        return f"{self.relation_id.binding_display}.{self.column_id.name.display}{scope}"


def set_solver_var(column: exp.Column, variable: SolverVar) -> exp.Column:
    column.meta[PARSEVAL_SOLVER_VAR] = variable
    return column


def solver_var(column: exp.Column) -> SolverVar | None:
    value = column.meta.get(PARSEVAL_SOLVER_VAR)
    return value if isinstance(value, SolverVar) else None


@dataclass
class CSPVariable:
    """A column variable in the CSP solver."""
    variable: SolverVar
    space: ValueSpace
    assigned: Optional[Any] = None

    @property
    def name(self) -> SolverVar:
        return self.variable


@dataclass
class CSPConstraint:
    """A relationship between two CSP variables."""
    kind: str
    left: SolverVar
    right: SolverVar


@dataclass
class ColumnPredicate:
    """A lowered constraint on a single column."""
    variable: SolverVar
    op: str
    value: Any


def col_type(col: exp.Column) -> Optional[DataType]:
    """Read the annotated type from a Column node, or None."""
    dtype = getattr(col, "type", None)
    if dtype is None:
        return None
    if isinstance(dtype, DataType):
        return dtype
    try:
        return DataType.build(str(dtype))
    except Exception:
        return None


__all__ = [
    "PARSEVAL_SOLVER_VAR",
    "SolverVar",
    "TypeFamily",
    "ValueSpace",
    "CSPVariable",
    "CSPConstraint",
    "ColumnPredicate",
    "col_type",
    "type_family",
    "parse_date",
    "parse_time",
    "parse_datetime",
    "date_to_epoch_day",
    "time_to_seconds",
    "datetime_to_epoch_second",
    "epoch_day_to_date",
    "seconds_to_time",
    "epoch_second_to_datetime",
    "infer_type_from_value",
    "infer_type_from_string",
    "set_solver_var",
    "solver_var",
]
