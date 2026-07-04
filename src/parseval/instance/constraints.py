from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

from sqlglot import exp

from parseval.identity import ColumnId, RelationId


@dataclass(frozen=True)
class DatabaseCheckConstraint:
    relation: RelationId
    expression: exp.Expression
    referenced_columns: Tuple[ColumnId, ...]
    origin: str
    supported: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class DatabaseConstraints:
    relation: RelationId
    not_null_columns: Tuple[ColumnId, ...] = ()
    primary_key: Tuple[ColumnId, ...] = ()
    unique_constraints: Tuple[Tuple[ColumnId, ...], ...] = ()
    foreign_keys: Tuple[Any, ...] = ()
    checks: Tuple[DatabaseCheckConstraint, ...] = ()

