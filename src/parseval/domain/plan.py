"""Compile Instance schema constraints into ColumnDomainPlan / ValueSpace / descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from sqlglot import exp

from parseval.dtype import DataType, TypeService, enum_values, type_family
from parseval.instance.schema import ColumnSchema, TableSchema, table_key

from .value_space import ValueSpace


@dataclass(frozen=True)
class ColumnDomainPlan:
    """Normalized generation/validation plan for one column (CSP variable domain)."""

    datatype: DataType
    nullable: bool = True
    unique: bool = False
    allowed_values: Optional[Tuple[Any, ...]] = None
    excluded_values: Tuple[Any, ...] = ()
    minimum: Optional[Any] = None
    maximum: Optional[Any] = None
    maximum_length: Optional[int] = None
    dialect: Optional[str] = None

    def to_value_space(self) -> ValueSpace:
        space = ValueSpace(family=type_family(self.datatype))
        if self.allowed_values is not None:
            space.allowed = set(self.allowed_values)
        space.not_equals.update(self.excluded_values)
        if self.minimum is not None:
            space.narrow_min(self.minimum)
        if self.maximum is not None:
            space.narrow_max(self.maximum)
        if not self.nullable:
            space.not_null = True
        if self.maximum_length is not None:
            space.max_length = self.maximum_length
        return space


@dataclass(frozen=True)
class ForeignKeyDescriptor:
    """FK equality group for solvers (source cols = target cols)."""

    source_table: str
    source_columns: Tuple[str, ...]
    target_table: str
    target_columns: Tuple[str, ...]


@dataclass(frozen=True)
class CheckDescriptor:
    """CHECK constraint descriptor; ``supported`` gates greedy/CSP handling."""

    expression_sql: str
    referenced_columns: Tuple[str, ...]
    supported: bool
    reason: Optional[str] = None


@dataclass(frozen=True)
class TableConstraintDescriptors:
    """Schema-level constraint data for CSP / DomainGenerator consumers."""

    table: str
    uniqueness_groups: Tuple[Tuple[str, ...], ...]
    foreign_keys: Tuple[ForeignKeyDescriptor, ...]
    checks: Tuple[CheckDescriptor, ...]


def _extract_length(datatype: DataType) -> Optional[int]:
    length = getattr(datatype, "length", None)
    if length is not None:
        try:
            return int(length.this if isinstance(length, exp.Literal) else length)
        except (TypeError, ValueError):
            pass
    expressions = datatype.args.get("expressions") or ()
    if expressions and not datatype.is_type(DataType.Type.ENUM):
        try:
            first = expressions[0]
            return int(first.this if isinstance(first, exp.Literal) else first)
        except (TypeError, ValueError):
            return None
    return None


def compile_column(
    column: ColumnSchema,
    *,
    dialect: str,
    unique: bool = False,
) -> ColumnDomainPlan:
    """Build a ColumnDomainPlan from an Instance ColumnSchema."""
    datatype = DataType.build(column.datatype)
    profile = TypeService().profile_datatype(datatype, dialect)
    allowed = profile.metadata.get("allowed_values")
    if allowed is not None:
        allowed_values: Optional[Tuple[Any, ...]] = tuple(allowed)
    else:
        allowed_values = enum_values(datatype)

    return ColumnDomainPlan(
        datatype=datatype,
        nullable=column.nullable,
        unique=unique or column.unique or (column.primary_key and unique),
        allowed_values=allowed_values,
        maximum_length=_extract_length(datatype),
        dialect=dialect,
    )


def space_for_column(
    column: ColumnSchema,
    *,
    dialect: str,
    avoid: Tuple[Any, ...] = (),
    unique: bool = False,
) -> ValueSpace:
    plan = compile_column(column, dialect=dialect, unique=unique)
    space = plan.to_value_space()
    for value in avoid:
        if value is not None:
            space.narrow_neq(value)
    return space


def compile_table(table: TableSchema) -> TableConstraintDescriptors:
    """Expose uniqueness / FK / CHECK descriptors for solvers (data only)."""
    return TableConstraintDescriptors(
        table=table.name,
        uniqueness_groups=tuple(
            tuple(col.name for col in group) for group in table.uniqueness_groups()
        ),
        foreign_keys=tuple(
            ForeignKeyDescriptor(
                source_table=table.name,
                source_columns=tuple(c.name for c in fk.source_columns),
                target_table=table_key(fk.target_table),
                target_columns=tuple(c.name for c in fk.target_columns),
            )
            for fk in table.foreign_keys
        ),
        checks=tuple(
            CheckDescriptor(
                expression_sql=check.expression.sql(),
                referenced_columns=tuple(c.name for c in check.referenced_columns),
                supported=check.supported,
                reason=check.reason,
            )
            for check in table.checks
        ),
    )


__all__ = [
    "CheckDescriptor",
    "ColumnDomainPlan",
    "ForeignKeyDescriptor",
    "TableConstraintDescriptors",
    "compile_column",
    "compile_table",
    "space_for_column",
]
