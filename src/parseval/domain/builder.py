from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from .coercion import coerce_value, values_equivalent
from .exceptions import (
    ConstraintViolationError,
    ForeignKeyResolutionError,
    TypeCoercionError,
    UniqueConflictError,
)
from .providers.registry import ProviderRegistry
from .spec import SchemaSpec
from .state import RowContext, SchemaRuntime


@dataclass(frozen=True)
class BuildPolicy:
    row_counts: Mapping[str, int] = field(default_factory=dict)
    default_row_count: int = 1
    null_rate: float = 0.0


class DatabaseBuilder:
    """Schema-only database builder backed by the provider registry."""

    def __init__(
        self,
        schema: SchemaSpec,
        registry: Optional[ProviderRegistry] = None,
        seed: int = 142,
    ) -> None:
        self.schema = schema
        self.runtime = SchemaRuntime(schema=schema, seed=seed)
        self.registry = registry or ProviderRegistry.with_builtin_providers()

    def build(self, policy: Optional[BuildPolicy] = None) -> Dict[str, list[Dict[str, Any]]]:
        policy = policy or BuildPolicy()
        for table in self.schema.tables:
            row_count = policy.row_counts.get(table.name, policy.default_row_count)
            for _ in range(row_count):
                self.generate_row(table.name, null_rate=policy.null_rate)
        return {name: table_state.rows for name, table_state in self.runtime.tables.items()}

    def generate_row(self, table_name: str, null_rate: float = 0.0) -> Dict[str, Any]:
        return self.complete_row(table_name, preset_values=None, persist=True, null_rate=null_rate)

    def complete_row(
        self,
        table_name: str,
        preset_values: Optional[Mapping[str, Any]] = None,
        persist: bool = True,
        null_rate: float = 0.0,
    ) -> Dict[str, Any]:
        table = self.schema.get_table(table_name)
        row_context = RowContext(table=table)
        for key, value in (preset_values or {}).items():
            column = table.get_column(key)
            coerced = self._coerce_for_column(column, value)
            self._validate_explicit_value(column, coerced)
            row_context.set_provided(column.column, coerced)

        for column in table.columns:
            if column.column in row_context.values:
                continue
            if self._should_generate_null(column, null_rate):
                row_context.set_generated(column.column, None)
                continue
            provider = self.registry.resolve(column)
            
            # Retry loop for CheckConstraints
            max_retries = 10
            value = None
            for _ in range(max_retries):
                value = provider.generate(column, self.runtime, row_context, null_rate=null_rate)
                if self._check_satisfied(column, value):
                    break
            
            self._validate_generated_value(column, value)
            row_context.set_generated(column.column, value)

        final_row = dict(row_context.values)
        if persist:
            self.runtime.remember_row(table.name, final_row)
        return final_row

    def generate_value(
        self,
        table_name: str,
        column_name: str,
        row_context: Optional[Mapping[str, Any]] = None,
        null_rate: float = 0.0,
    ) -> Any:
        table = self.schema.get_table(table_name)
        column = table.get_column(column_name)
        context = RowContext(table=table)
        for key, value in (row_context or {}).items():
            sibling = table.get_column(key)
            context.set_provided(sibling.column, self._coerce_for_column(sibling, value))
        provider = self.registry.resolve(column)
        value = provider.generate(column, self.runtime, context, null_rate=null_rate)
        self._validate_generated_value(column, value)
        return value

    def _coerce_for_column(self, column, value):
        try:
            return coerce_value(value, column.datatype, dialect=column.dialect)
        except Exception as exc:
            raise TypeCoercionError(
                f"Failed to coerce value for {column.qualified_name}: {value!r}"
            ) from exc

    def _validate_explicit_value(self, column, value) -> None:
        if value is None and not column.nullable:
            raise ConstraintViolationError(f"{column.qualified_name} does not allow NULL")
        if value is not None and (column.unique or column.primary_key):
            state = self.runtime.column_state(column.table, column.column)
            if value in state.used_values:
                raise UniqueConflictError(
                    f"Duplicate value for unique column {column.qualified_name}: {value!r}"
                )
        if column.foreign_key and value is not None:
            referenced = self.runtime.referenced_values(column) or []
            if referenced and not self._matches_foreign_key_target(column, value, referenced):
                raise ForeignKeyResolutionError(
                    f"{column.qualified_name} references missing parent value: {value!r}"
                )

    def _validate_generated_value(self, column, value) -> None:
        if value is None and not column.nullable:
            raise ConstraintViolationError(f"Generated NULL for {column.qualified_name}")
        if column.foreign_key and value is not None:
            referenced = self.runtime.referenced_values(column)
            if referenced is None:
                raise ForeignKeyResolutionError(
                    f"{column.qualified_name} uses an unsupported foreign key shape for generation"
                )
            if not referenced:
                raise ForeignKeyResolutionError(
                    f"{column.qualified_name} cannot be generated before referenced parent values exist"
                )
            if not self._matches_foreign_key_target(column, value, referenced):
                raise ForeignKeyResolutionError(
                    f"{column.qualified_name} references missing parent value: {value!r}"
                )

    def _matches_foreign_key_target(self, column, value, referenced_values) -> bool:
        foreign_key = column.foreign_key
        if foreign_key is None or len(foreign_key.target_columns) != 1:
            return value in referenced_values

        target_column = self.schema.get_table(foreign_key.target_table).get_column(
            foreign_key.target_columns[0]
        )
        for referenced in referenced_values:
            if values_equivalent(
                value,
                column.datatype,
                referenced,
                target_column.datatype,
                left_dialect=column.dialect,
                right_dialect=target_column.dialect,
            ):
                return True
        return False

    def _should_generate_null(self, column, null_rate: float) -> bool:
        if not column.nullable or null_rate <= 0.0:
            return False
        return self.runtime.rng.random() < min(1.0, null_rate)

    def _check_satisfied(self, column, value) -> bool:
        if value is None:
            return True
        from .constraints import CheckConstraint
        for check in column.checks:
            if isinstance(check, CheckConstraint):
                if callable(check.expression):
                    satisfied = check.expression(value)
                    if not satisfied:
                        return False
        return True
