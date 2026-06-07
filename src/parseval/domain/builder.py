from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Sequence

from parseval.dtype import DataType
from parseval.identity import ColumnId, RelationId
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
    """Configuration controlling how many rows to generate per table.

    Attributes:
        row_counts: Map of table name to desired row count. Tables not
            listed here fall back to ``default_row_count``.
        default_row_count: Default number of rows for any table (default 1).
        null_rate: Probability (0.0–1.0) of generating a NULL for a nullable
            column on any given row (default 0.0).
    """

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
        """Initialize a DatabaseBuilder for the given schema.

        Args:
            schema: The schema specification to build against.
            registry: Optional provider registry; defaults to built-in providers.
            seed: Random seed for deterministic generation (default 142).
        """
        self.schema = schema
        self.runtime = SchemaRuntime(schema=schema, seed=seed)
        self.registry = registry or ProviderRegistry.with_builtin_providers()
        self.compiler = ConstraintCompiler()
        self.validator = ConstraintValidator()
        self._plans: Dict[ColumnId, ColumnDomainPlan] = {}

    def _get_plan(self, column) -> ColumnDomainPlan:
        if column.id not in self._plans:
            self._plans[column.id] = self.compiler.compile(column)
        return self._plans[column.id]

    def build(self, policy: Optional[BuildPolicy] = None) -> Dict[str, list[Dict[str, Any]]]:
        """Generate synthetic rows for every table in the schema.

        Iterates tables in schema order, generating the number of rows
        specified by ``policy``.  Each row is validated against all column
        constraints, uniqueness, and foreign keys before being persisted.

        Args:
            policy: Optional BuildPolicy controlling row counts and null rate.
                Defaults to an empty policy (1 row per table, no nulls).

        Returns:
            Dict mapping table name to list-of-dict rows.
        """
        policy = policy or BuildPolicy()
        for table in self.schema.tables:
            row_count = policy.row_counts.get(table.name, policy.default_row_count)
            for _ in range(row_count):
                self.generate_row(table.id, null_rate=policy.null_rate)
        return {
            table.name: [
                self._row_to_names(table, row)
                for row in self.runtime.table_state(table.id).rows
            ]
            for table in self.schema.tables
        }

    def generate_row(
        self,
        table_name: str | RelationId,
        null_rate: float = 0.0,
    ) -> Dict[str, Any]:
        """Generate a single row for the given table with no preset values.

        Delegates to ``complete_row`` with ``preset_values=None``.

        Args:
            table_name: Name of the table to generate a row for.
            null_rate: Override probability of generating NULLs.

        Returns:
            A dict of column-name to generated value.
        """
        return self.complete_row(table_name, preset_values=None, persist=True, null_rate=null_rate)

    def complete_row(
        self,
        table_name: str | RelationId,
        preset_values: Optional[Mapping[str | ColumnId, Any]] = None,
        persist: bool = True,
        null_rate: float = 0.0,
    ) -> Dict[str, Any]:
        """Generate a full row, optionally mixing preset and generated values.

        For each column in the table:
        1. If a preset value is provided, coerce and validate it.
        2. Otherwise, generate a candidate via the provider registry and
           validate it against the compiled column domain plan.

        Args:
            table_name: Name of the table to build a row for.
            preset_values: Optional map of column names to explicitly set.
            persist: If True, persist the row into runtime state (default True).
            null_rate: Probability of generating NULL for nullable columns.

        Returns:
            A dict of column-name to final value.

        Raises:
            UniqueConflictError: If a uniqueness constraint is violated.
            ForeignKeyResolutionError: If an FK reference can't be resolved.
            TypeCoercionError: If a preset value can't be coerced.
        """
        table = self.schema.get_table(table_name)
        row_context = RowContext(table=table)
        for column, value in self._normalize_preset_values(table, preset_values).items():
            coerced = self._coerce_for_column(column, value)
            plan = self._get_plan(column)
            self.validator.validate(plan, coerced, column.qualified_name)
            self._validate_explicit_uniqueness_and_fk(column, coerced)
            row_context.set_provided(column, coerced)

        self._apply_composite_fk_bindings(table, row_context)

        for column in table.columns:
            if column.id in row_context.values:
                continue
            
            plan = self._get_plan(column)
            if self._should_generate_null(column, null_rate):
                self.validator.validate(plan, None, column.qualified_name)
                row_context.set_generated(column, None)
                continue
            
            value = self._generate_candidate(
                column=column,
                row_context=row_context,
                plan=plan,
                null_rate=null_rate,
            )
            self.validator.validate(plan, value, column.qualified_name)
            self._validate_generated_uniqueness_and_fk(column, value)
            row_context.set_generated(column, value)

        final_row = dict(row_context.values)
        if persist:
            self.runtime.remember_row(table.id, final_row)
        return self._row_to_names(table, final_row)

    def generate_value(
        self,
        table_name: str | RelationId,
        column_name: str | ColumnId,
        row_context: Optional[Mapping[str | ColumnId, Any]] = None,
        null_rate: float = 0.0,
    ) -> Any:
        """Generate a single value for a specific column, respecting constraints.

        Useful when you need just one column's worth of value generation
        (e.g., for a column with a FK to an already-built parent row).

        Args:
            table_name: Name of the table.
            column_name: Name of the column to generate for.
            row_context: Optional sibling column values to respect cross-column constraints.
            null_rate: Probability of generating NULL.

        Returns:
            A generated value conforming to the column's domain plan.
        """
        table = self.schema.get_table(table_name)
        column = table.get_column(column_name)
        context = RowContext(table=table)
        for sibling, value in self._normalize_preset_values(table, row_context).items():
            context.set_provided(sibling, self._coerce_for_column(sibling, value))

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

    def _normalize_preset_values(
        self,
        table,
        values: Optional[Mapping[str | ColumnId, Any]],
    ) -> Dict[Any, Any]:
        return {
            table.get_column(key): value
            for key, value in (values or {}).items()
        }

    @staticmethod
    def _row_to_names(table, row: Mapping[ColumnId, Any]) -> Dict[str, Any]:
        return {
            column.column: row.get(column.id)
            for column in table.columns
        }

    def _coerce_for_column(self, column, value):
        """Coerce a concrete value to match a column's declared datatype.

        For SQLite temporal columns, string values are preserved as-is
        (SQLite stores dates as TEXT).

        Args:
            column: The target ColumnSpec.
            value: The value to coerce.

        Returns:
            The coerced value.

        Raises:
            TypeCoercionError: If the value cannot be converted.
        """
        try:
            # For SQLite, preserve string values for temporal columns (SQLite stores as TEXT).
            if column.dialect == "sqlite" and isinstance(value, str) and column.datatype and column.datatype.is_type(*DataType.TEMPORAL_TYPES):
                return value
            return coerce_value(value, column.datatype, dialect=column.dialect)
        except Exception as exc:
            raise TypeCoercionError(
                f"Failed to coerce value for {column.qualified_name}: {value!r}"
            ) from exc

    def _validate_explicit_uniqueness_and_fk(self, column, value) -> None:
        """Validate that an explicitly provided value satisfies uniqueness and FK constraints.

        Checks single-column uniqueness and, for single-column FKs, that the
        referenced parent value already exists in the runtime state.

        Raises:
            UniqueConflictError: If a unique column gets a duplicate.
            ForeignKeyResolutionError: If an FK value has no matching parent.
        """
        if value is not None and self._enforces_single_column_uniqueness(column):
            state = self.runtime.column_state(column.id)
            if value in state.used_values:
                raise UniqueConflictError(
                    f"Duplicate value for unique column {column.qualified_name}: {value!r}"
                )
        if column.foreign_key and value is not None:
            if len(column.foreign_key.target_column_ids) > 1:
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
        """Validate that an auto-generated value satisfies uniqueness and FK constraints.

        Same checks as ``_validate_explicit_uniqueness_and_fk`` but for
        generated (non-preset) values.

        Raises:
            UniqueConflictError: If a generated value conflicts with existing ones.
            ForeignKeyResolutionError: If the generated FK has no matching parent.
        """
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
            state = self.runtime.column_state(column.id)
            if value in state.used_values:
                 raise UniqueConflictError(
                    f"Generated duplicate value for unique column {column.qualified_name}: {value!r}"
                )

    def _matches_foreign_key_target(self, column, value, referenced_values) -> bool:
        """Check whether a value matches any of the referenced parent values.

        For composite FKs, uses cross-dialect type comparison via
        ``values_equivalent``.

        Args:
            column: The child column with the FK.
            value: The candidate FK value.
            referenced_values: List of parent column values.

        Returns:
            True if the value matches at least one parent value.
        """
        foreign_key = column.foreign_key
        if foreign_key is None or len(foreign_key.target_column_ids) != 1:
            return value in referenced_values

        target_column = self.schema.get_table(foreign_key.target_table_id).get_column(
            foreign_key.target_column_ids[0]
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
        """Determine whether to generate NULL for a column based on null_rate.

        Args:
            column: The column being evaluated.
            null_rate: Probability threshold (0.0–1.0).

        Returns:
            True if the column is nullable and the random draw falls below null_rate.
        """
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
        """Attempt to generate a valid value for a column respecting its domain plan.

        First tries to pick from a pool of unique allowed values (for columns
        with finite domains and uniqueness constraints). If that fails, falls
        back to the provider registry with up to 10 retries when residual
        predicates exist.

        Args:
            column: The ColumnSpec to generate for.
            row_context: Current row context for cross-column constraints.
            plan: The compiled ColumnDomainPlan for this column.
            null_rate: Probability of returning None.

        Returns:
            A value that satisfies the column's constraints, or the last
            attempted value if all retries are exhausted.
        """
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
        """Pick the first unused allowed value for unique columns with finite domains.

        Returns ``_MISSING`` if the column has no allowed-value pool or is not
        subject to single-column uniqueness.

        Args:
            column: The ColumnSpec.
            plan: The compiled ColumnDomainPlan.

        Returns:
            An unused allowed value, or ``_MISSING`` if none available.

        Raises:
            UniqueConflictError: If all allowed values are exhausted.
        """
        if not self._enforces_single_column_uniqueness(column):
            return _MISSING
        if not plan.allowed_values:
            return _MISSING

        state = self.runtime.column_state(column.id)
        for candidate in plan.allowed_values:
            if candidate not in state.used_values and candidate not in plan.excluded_values:
                return candidate
        raise UniqueConflictError(
            f"No allowed values remain for unique column {column.qualified_name}"
        )

    def _enforces_single_column_uniqueness(self, column) -> bool:
        """Check whether a column requires single-column uniqueness enforcement.

        Returns True for explicitly unique columns or single-column primary keys.
        """
        if column.unique:
            return True
        if not column.primary_key:
            return False
        table = self.schema.get_table(column.table_id)
        return len(table.primary_key_ids) == 1 and table.primary_key_ids[0] == column.id

    def _apply_composite_fk_bindings(self, table, row_context: RowContext) -> None:
        """Resolve and assign multi-column foreign key bindings for a table.

        For each composite FK on the table, selects a matching tuple from the
        parent table and assigns the source-column values into the row context.
        """
        for foreign_key in self._composite_foreign_keys(table):
            source_columns = foreign_key.source_column_ids
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
        """Extract distinct composite foreign keys defined on a table's columns.

        A composite FK has more than one target column. Returns each unique
        FK binding only once even if multiple source columns reference it.
        """
        seen = set()
        bindings = []
        for column in table.columns:
            foreign_key = column.foreign_key
            if foreign_key is None or len(foreign_key.target_column_ids) <= 1:
                continue
            key = (
                foreign_key.source_table_id,
                tuple(foreign_key.source_column_ids),
                foreign_key.target_table_id,
                tuple(foreign_key.target_column_ids),
            )
            if key not in seen:
                seen.add(key)
                bindings.append(foreign_key)
        return bindings

    def _select_referenced_tuple(self, foreign_key, row_context: Optional[RowContext] = None):
        """Select a referenced tuple from the parent table for FK resolution.

        If a ``row_context`` is provided, only tuples matching already-provided
        values are considered.

        Args:
            foreign_key: The ForeignKeySpec to resolve.
            row_context: Optional partial row to filter candidates.

        Returns:
            A randomly chosen matching tuple from the parent table.

        Raises:
            ForeignKeyResolutionError: If no matching tuple exists.
        """
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
        """Check whether a candidate FK tuple matches already-provided row values.

        Compares each source column's already-provided value against the
        corresponding target-column value, using cross-dialect type comparison.

        Args:
            foreign_key: The ForeignKeySpec being resolved.
            target_tuple: A tuple of values from the parent table.
            row_context: The partially-built child row.

        Returns:
            True if all provided source values match their target counterparts.
        """
        source_table = self.schema.get_table(foreign_key.source_table_id)
        target_table = self.schema.get_table(foreign_key.target_table_id)
        for source_column, target_column_id, target_value in zip(
            foreign_key.source_column_ids, foreign_key.target_column_ids, target_tuple
        ):
            if source_column not in row_context.values:
                continue
            source_spec = source_table.get_column(source_column)
            target_spec = target_table.get_column(target_column_id)
            if not values_equivalent(
                row_context.values[source_column],
                source_spec.datatype,
                target_value,
                target_spec.datatype,
                left_dialect=source_spec.dialect,
                right_dialect=target_spec.dialect,
            ):
                return False
        return True

    def _assign_composite_fk_values(self, table, foreign_key, target_tuple, row_context: RowContext) -> None:
        """Assign coerced FK tuple values into the row context for source columns.

        Only assigns values for source columns that don't already have a value
        in the row context. Each value is coerced and validated before assignment.
        """
        for source_column, target_value in zip(foreign_key.source_column_ids, target_tuple):
            if source_column in row_context.values:
                continue
            source_spec = table.get_column(source_column)
            coerced = self._coerce_for_column(source_spec, target_value)
            plan = self._get_plan(source_spec)
            self.validator.validate(plan, coerced, source_spec.qualified_name)
            row_context.set_generated(source_spec, coerced)

    def _validate_explicit_composite_fk(self, table, foreign_key, row_context: RowContext) -> None:
        """Validate that explicitly provided composite FK values reference an existing parent tuple.

        Args:
            table: The child table spec.
            foreign_key: The ForeignKeySpec to validate against.
            row_context: The row containing the provided FK values.

        Raises:
            ForeignKeyResolutionError: If no matching parent tuple exists.
        """
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
