from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence

from .coercion import coerce_value, values_equivalent
from .compiler import ConstraintCompiler
from .exceptions import (
    ConstraintViolationError,
    ForeignKeyResolutionError,
    TypeCoercionError,
    UniqueConflictError,
)
from .providers.registry import ProviderRegistry
from .spec import SchemaSpec
from .state import RowContext, SchemaRuntime

from .compiler import ColumnDomainPlan, ConstraintValidator

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
        self.compiler = ConstraintCompiler()
        self.validator = ConstraintValidator()
        self._plans: Dict[str, ColumnDomainPlan] = {}

    def _get_plan(self, column) -> ColumnDomainPlan:
        if column.qualified_name not in self._plans:
            self._plans[column.qualified_name] = self.compiler.compile(column)
        return self._plans[column.qualified_name]

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
            plan = self._get_plan(column)
            self.validator.validate(plan, coerced, column.qualified_name)
            self._validate_explicit_uniqueness_and_fk(column, coerced)
            row_context.set_provided(column.column, coerced)

        self._apply_composite_fk_bindings(table, row_context)

        for column in table.columns:
            if column.column in row_context.values:
                continue
            
            plan = self._get_plan(column)
            if self._should_generate_null(column, null_rate):
                self.validator.validate(plan, None, column.qualified_name)
                row_context.set_generated(column.column, None)
                continue
            
            value = self._generate_candidate(
                column=column,
                row_context=row_context,
                plan=plan,
                null_rate=null_rate,
            )
            self.validator.validate(plan, value, column.qualified_name)
            self._validate_generated_uniqueness_and_fk(column, value)
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
        
        plan = self._get_plan(column)
        value = self._generate_candidate(
            column=column,
            row_context=context,
            plan=plan,
            null_rate=null_rate,
        )
        self.validator.validate(plan, value, column.qualified_name)
        self._validate_generated_uniqueness_and_fk(column, value)
        return value

    def _coerce_for_column(self, column, value):
        try:
            return coerce_value(value, column.datatype, dialect=column.dialect)
        except Exception as exc:
            raise TypeCoercionError(
                f"Failed to coerce value for {column.qualified_name}: {value!r}"
            ) from exc

    def _validate_explicit_uniqueness_and_fk(self, column, value) -> None:
        if value is not None and self._enforces_single_column_uniqueness(column):
            state = self.runtime.column_state(column.table, column.column)
            if value in state.used_values:
                raise UniqueConflictError(
                    f"Duplicate value for unique column {column.qualified_name}: {value!r}"
                )
        if column.foreign_key and value is not None:
            if len(column.foreign_key.target_columns) > 1:
                return
            referenced = self.runtime.referenced_values(column)
            if referenced is None:
                raise ForeignKeyResolutionError(
                    f"{column.qualified_name} uses an unsupported foreign key shape for generation"
                )
            if not referenced:
                raise ForeignKeyResolutionError(
                    f"{column.qualified_name} cannot be set before referenced parent values exist"
                )
            if not self._matches_foreign_key_target(column, value, referenced):
                raise ForeignKeyResolutionError(
                    f"{column.qualified_name} references missing parent value: {value!r}"
                )

    def _validate_generated_uniqueness_and_fk(self, column, value) -> None:
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
        if value is not None and self._enforces_single_column_uniqueness(column):
            state = self.runtime.column_state(column.table, column.column)
            if value in state.used_values:
                 raise UniqueConflictError(
                    f"Generated duplicate value for unique column {column.qualified_name}: {value!r}"
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

    def _generate_candidate(
        self,
        column,
        row_context: RowContext,
        plan: ColumnDomainPlan,
        null_rate: float,
    ) -> Any:
        unique_pool_value = self._generate_unique_allowed_value(column, plan)
        if unique_pool_value is not _MISSING:
            return unique_pool_value

        provider = self.registry.resolve(column)
        type_profile = self.registry.type_service.profile(column)
        max_retries = 10 if plan.residual_predicates else 1
        value = None
        for _ in range(max_retries):
            value = provider.generate(
                column,
                self.runtime,
                row_context,
                domain_plan=plan,
                type_profile=type_profile,
                null_rate=null_rate,
            )
            if self.validator.is_valid(plan, value):
                return value
        return value

    def _generate_unique_allowed_value(self, column, plan: ColumnDomainPlan) -> Any:
        if not self._enforces_single_column_uniqueness(column):
            return _MISSING
        if not plan.allowed_values:
            return _MISSING

        state = self.runtime.column_state(column.table, column.column)
        for candidate in plan.allowed_values:
            if candidate not in state.used_values and candidate not in plan.excluded_values:
                return candidate
        raise UniqueConflictError(
            f"No allowed values remain for unique column {column.qualified_name}"
        )

    def _enforces_single_column_uniqueness(self, column) -> bool:
        if column.unique:
            return True
        if not column.primary_key:
            return False
        table = self.schema.get_table(column.table)
        return len(table.primary_key) == 1 and table.primary_key[0] == column.column

    def _apply_composite_fk_bindings(self, table, row_context: RowContext) -> None:
        for foreign_key in self._composite_foreign_keys(table):
            source_columns = tuple(column.lower() for column in foreign_key.source_columns)
            provided = [column for column in source_columns if column in row_context.values]
            if not provided:
                values = self._select_referenced_tuple(foreign_key)
                self._assign_composite_fk_values(table, foreign_key, values, row_context)
                continue
            if len(provided) == len(source_columns):
                self._validate_explicit_composite_fk(table, foreign_key, row_context)
                continue
            values = self._select_referenced_tuple(foreign_key, row_context)
            self._assign_composite_fk_values(table, foreign_key, values, row_context)

    def _composite_foreign_keys(self, table) -> list:
        seen = set()
        bindings = []
        for column in table.columns:
            foreign_key = column.foreign_key
            if foreign_key is None or len(foreign_key.target_columns) <= 1:
                continue
            key = (
                foreign_key.source_table,
                tuple(foreign_key.source_columns),
                foreign_key.target_table,
                tuple(foreign_key.target_columns),
            )
            if key not in seen:
                seen.add(key)
                bindings.append(foreign_key)
        return bindings

    def _select_referenced_tuple(self, foreign_key, row_context: Optional[RowContext] = None):
        tuples = self.runtime.referenced_key_tuples(foreign_key)
        if not tuples:
            raise ForeignKeyResolutionError(
                f"{foreign_key.source_table}.{','.join(foreign_key.source_columns)} cannot be generated before referenced parent values exist"
            )
        candidates = tuples
        if row_context is not None:
            candidates = [
                candidate
                for candidate in tuples
                if self._tuple_matches_context(foreign_key, candidate, row_context)
            ]
        if not candidates:
            raise ForeignKeyResolutionError(
                f"{foreign_key.source_table}.{','.join(foreign_key.source_columns)} references missing parent tuple"
            )
        return self.runtime.rng.choice(candidates)

    def _tuple_matches_context(self, foreign_key, target_tuple: Sequence[Any], row_context: RowContext) -> bool:
        source_table = self.schema.get_table(foreign_key.source_table)
        target_table = self.schema.get_table(foreign_key.target_table)
        for source_column, target_column_name, target_value in zip(
            foreign_key.source_columns, foreign_key.target_columns, target_tuple
        ):
            normalized_source = source_column.lower()
            if normalized_source not in row_context.values:
                continue
            source_spec = source_table.get_column(normalized_source)
            target_spec = target_table.get_column(target_column_name)
            if not values_equivalent(
                row_context.values[normalized_source],
                source_spec.datatype,
                target_value,
                target_spec.datatype,
                left_dialect=source_spec.dialect,
                right_dialect=target_spec.dialect,
            ):
                return False
        return True

    def _assign_composite_fk_values(self, table, foreign_key, target_tuple, row_context: RowContext) -> None:
        for source_column, target_value in zip(foreign_key.source_columns, target_tuple):
            normalized_source = source_column.lower()
            if normalized_source in row_context.values:
                continue
            source_spec = table.get_column(normalized_source)
            coerced = self._coerce_for_column(source_spec, target_value)
            plan = self._get_plan(source_spec)
            self.validator.validate(plan, coerced, source_spec.qualified_name)
            row_context.set_generated(normalized_source, coerced)

    def _validate_explicit_composite_fk(self, table, foreign_key, row_context: RowContext) -> None:
        values = self.runtime.referenced_key_tuples(foreign_key)
        if not values:
            raise ForeignKeyResolutionError(
                f"{foreign_key.source_table}.{','.join(foreign_key.source_columns)} cannot be set before referenced parent values exist"
            )
        if not any(self._tuple_matches_context(foreign_key, candidate, row_context) for candidate in values):
            raise ForeignKeyResolutionError(
                f"{foreign_key.source_table}.{','.join(foreign_key.source_columns)} references missing parent tuple"
            )


_MISSING = object()
