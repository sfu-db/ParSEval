"""Compile Instance schema constraints into ColumnDomainPlan / ValueSpace / descriptors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from parseval.dtype import TypeProfile, TypeService
from parseval.instance.schema import ColumnSchema, TableSchema, table_key

from .value_space import ValueSpace


@dataclass(frozen=True)
class ColumnDomainPlan:
    """Normalized generation/validation plan for one column (CSP variable domain)."""

    profile: TypeProfile
    nullable: bool = True
    unique: bool = False
    excluded_values: Tuple[Any, ...] = ()
    minimum: Optional[Any] = None
    maximum: Optional[Any] = None

    def to_value_space(self) -> ValueSpace:
        space = ValueSpace.from_profile(self.profile)
        space.not_equals.update(self.excluded_values)
        if self.minimum is not None:
            space.narrow_min(self.minimum)
        if self.maximum is not None:
            space.narrow_max(self.maximum)
        if not self.nullable:
            space.not_null = True
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


def compile_column(
    column: ColumnSchema,
    *,
    dialect: str,
    unique: bool = False,
) -> ColumnDomainPlan:
    """Build a ColumnDomainPlan from an Instance ColumnSchema."""
    profile = TypeService().profile_datatype(column.datatype, dialect)
    return ColumnDomainPlan(
        profile=profile,
        nullable=column.nullable,
        unique=unique or column.unique or (column.primary_key and unique),
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
