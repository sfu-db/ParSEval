"""Greedy row completion over InstanceSchema using ValueSpace (CSP domains)."""

from __future__ import annotations

import random
from typing import Any, Mapping, MutableMapping, Sequence, Set

from sqlglot import exp

from parseval.dtype import TypeFamily
from parseval.instance.schema import (
    InstanceSchema,
    TableSchema,
    table_key,
)
from parseval.plan.rex import Environment, concrete

from .exceptions import (
    ConstraintConflict,
    ConstraintViolationError,
    ForeignKeyResolutionError,
    UniqueConflictError,
)
from .plan import space_for_column


class DomainGenerator:
    """Constraint-aware greedy labeler for Instance rows.

    Builds / narrows :class:`ValueSpace` domains then ``pick``s values.
    Does not own a parallel database — callers pass existing/parent rows.

    Row dicts and ``locked`` use resolved ``exp.Identifier`` keys.
    ``parent_rows`` is keyed by resolved ``exp.Table``.
    """

    def __init__(self, schema: InstanceSchema, *, seed: int = 142) -> None:
        self.schema = schema
        self.rng = random.Random(seed)

    def complete_row(
        self,
        table: exp.Table | str,
        *,
        presets: Mapping[exp.Identifier | str, Any] | None = None,
        existing_rows: Sequence[Mapping[exp.Identifier, Any]] = (),
        parent_rows: Mapping[exp.Table, Sequence[Mapping[exp.Identifier, Any]]] | None = None,
        locked: Set[exp.Identifier | str] | None = None,
    ) -> dict[exp.Identifier, Any]:
        table_schema = self.schema.get_table(table)
        locked_ids = self._normalize_locked(table_schema, locked)
        row = self._normalize_presets(table_schema, presets)
        locked_ids.update(row)

        self._bind_foreign_keys(
            table_schema, row, parent_rows or {}, locked_ids, existing_rows
        )
        self._fill_missing(table_schema, row, existing_rows)
        conflict = self._unique_conflict_group(table_schema, row, existing_rows)
        if conflict is not None:
            if all(col in locked_ids for col in conflict):
                raise UniqueConflictError(
                    f"unique_conflict:{table_schema.name}:"
                    f"{tuple(c.name for c in conflict)}"
                )
            # Unlocked columns should have been avoided at fill time.
            raise ConstraintConflict(
                f"unresolved_unique_collision:{table_schema.name}:"
                f"{tuple(c.name for c in conflict)}"
            )
        self._validate_checks(table_schema, row)
        return {col: row[col] for col in table_schema.columns}

    def next_value(
        self,
        table: exp.Table | str,
        column: str | exp.Identifier,
        *,
        existing_rows: Sequence[Mapping[exp.Identifier, Any]] = (),
        avoid: Sequence[Any] = (),
    ) -> Any:
        table_schema = self.schema.get_table(table)
        col = self.schema.resolve_column(table, column)
        column_schema = table_schema.columns[col]
        used = self._used_values(existing_rows, col)
        used.update(v for v in avoid if v is not None)
        space = space_for_column(
            column_schema,
            dialect=self.schema.dialect,
            avoid=tuple(used),
            unique=self.schema.is_unique(table, col),
        )
        if not column_schema.nullable:
            space.not_null = True
        value = space.pick(hint=col.name, rng=self.rng)
        if value is None and not column_schema.nullable:
            raise ConstraintConflict(
                f"empty_value_space:{table_schema.name}.{col.name}"
            )
        return value

    def _normalize_locked(
        self,
        table_schema: TableSchema,
        locked: Set[exp.Identifier | str] | None,
    ) -> set[exp.Identifier]:
        return {
            self.schema.resolve_column(table_schema.table, item)
            for item in (locked or ())
        }

    def _normalize_presets(
        self,
        table_schema: TableSchema,
        presets: Mapping[exp.Identifier | str, Any] | None,
    ) -> dict[exp.Identifier, Any]:
        row: dict[exp.Identifier, Any] = {}
        for key, value in (presets or {}).items():
            col = self.schema.resolve_column(table_schema.table, key)
            if value is None and not table_schema.columns[col].nullable:
                raise ConstraintViolationError(
                    f"explicit_null_for_non_nullable_column:"
                    f"{table_schema.name}.{col.name}"
                )
            row[col] = value
        return row

    def _bind_foreign_keys(
        self,
        table_schema: TableSchema,
        row: MutableMapping[exp.Identifier, Any],
        parent_rows: Mapping[exp.Table, Sequence[Mapping[exp.Identifier, Any]]],
        locked: Set[exp.Identifier],
        existing_rows: Sequence[Mapping[exp.Identifier, Any]] = (),
    ) -> None:
        for fk in table_schema.foreign_keys:
            target = self.schema.resolve_table(fk.target_table)
            parents = list(parent_rows.get(target, ()))
            sources = list(fk.source_columns)
            targets = list(fk.target_columns)

            if all(col in row and row[col] is not None for col in sources):
                locked.update(sources)
                if not any(
                    all(
                        parent.get(target_col) == row[source_col]
                        for source_col, target_col in zip(sources, targets)
                    )
                    for parent in parents
                ):
                    raise ForeignKeyResolutionError(
                        f"dangling_foreign_key:{table_schema.name}->{table_key(target)}"
                    )
                continue

            if not parents:
                raise ForeignKeyResolutionError(
                    f"missing_parent_rows:{table_schema.name}->{table_key(target)}"
                )

            parent = self._choose_parent(
                table_schema,
                row,
                sources,
                targets,
                parents,
                existing_rows,
            )
            for source_col, target_col in zip(sources, targets):
                if row.get(source_col) is not None and source_col in locked:
                    continue
                row[source_col] = parent.get(target_col)
                locked.add(source_col)

    def _choose_parent(
        self,
        table_schema: TableSchema,
        row: Mapping[exp.Identifier, Any],
        sources: list[exp.Identifier],
        targets: list[exp.Identifier],
        parents: Sequence[Mapping[exp.Identifier, Any]],
        existing_rows: Sequence[Mapping[exp.Identifier, Any]],
    ) -> Mapping[exp.Identifier, Any]:
        """Prefer a parent tuple that matches presets and avoids unique collisions."""
        for parent in parents:
            candidate = dict(row)
            compatible = True
            for source_col, target_col in zip(sources, targets):
                parent_val = parent.get(target_col)
                current = candidate.get(source_col)
                if current is not None:
                    if current != parent_val:
                        compatible = False
                        break
                else:
                    candidate[source_col] = parent_val
            if not compatible:
                continue
            if self._unique_conflict_group(table_schema, candidate, existing_rows) is not None:
                continue
            if self._unique_prefix_saturated(table_schema, candidate, existing_rows):
                continue
            return parent
        raise ForeignKeyResolutionError(
            f"no_non_colliding_parent:{table_schema.name}"
        )

    def _fill_missing(
        self,
        table_schema: TableSchema,
        row: MutableMapping[exp.Identifier, Any],
        existing_rows: Sequence[Mapping[exp.Identifier, Any]],
    ) -> None:
        """Fill unset columns via ValueSpace, avoiding single- and composite-unique collisions."""
        for col_ident, column in table_schema.columns.items():
            if col_ident in row:
                continue
            used = self._avoid_for_column(
                table_schema, col_ident, column, row, existing_rows
            )
            space = space_for_column(
                column,
                dialect=self.schema.dialect,
                avoid=tuple(used),
                unique=self.schema.is_unique(table_schema.table, col_ident),
            )
            if not column.nullable:
                space.not_null = True
            value = space.pick(hint=col_ident.name, rng=self.rng)
            if value is None and not column.nullable:
                raise ConstraintConflict(
                    f"empty_value_space:{table_schema.name}.{col_ident.name}"
                )
            row[col_ident] = value

    def _avoid_for_column(
        self,
        table_schema: TableSchema,
        col_ident: exp.Identifier,
        column,
        row: Mapping[exp.Identifier, Any],
        existing_rows: Sequence[Mapping[exp.Identifier, Any]],
    ) -> set[Any]:
        """Values that would violate single-column or composite uniqueness if chosen for ``col_ident``.

        For composite groups, also avoid values whose already-bound prefix is saturated:
        every completion of the unbound finite columns is already present.
        """
        avoid: set[Any] = set()
        if column.unique or self.schema.is_unique(table_schema.table, col_ident):
            avoid |= self._used_values(existing_rows, col_ident)

        for group in table_schema.uniqueness_groups():
            if col_ident not in group or len(group) < 2:
                continue
            others = [c for c in group if c != col_ident]
            bound = [c for c in others if row.get(c) is not None]
            unbound = [c for c in others if row.get(c) is None]
            if not unbound:
                for existing in existing_rows:
                    if all(existing.get(c) == row.get(c) for c in others):
                        val = existing.get(col_ident)
                        if val is not None:
                            avoid.add(val)
                continue

            capacity = self._finite_group_capacity(table_schema, unbound)
            if capacity is None:
                continue
            counts: dict[Any, int] = {}
            for existing in existing_rows:
                if any(existing.get(c) != row.get(c) for c in bound):
                    continue
                if any(existing.get(c) is None for c in unbound):
                    continue
                val = existing.get(col_ident)
                if val is None:
                    continue
                counts[val] = counts.get(val, 0) + 1
            for val, n in counts.items():
                if n >= capacity:
                    avoid.add(val)
        return avoid

    def _unique_prefix_saturated(
        self,
        table_schema: TableSchema,
        row: Mapping[exp.Identifier, Any],
        existing_rows: Sequence[Mapping[exp.Identifier, Any]],
    ) -> bool:
        """True when a bound unique-group prefix has no free finite completion left."""
        for group in table_schema.uniqueness_groups():
            bound = [c for c in group if row.get(c) is not None]
            unbound = [c for c in group if row.get(c) is None]
            if not bound or not unbound:
                continue
            capacity = self._finite_group_capacity(table_schema, unbound)
            if capacity is None:
                continue
            matching = 0
            for existing in existing_rows:
                if any(existing.get(c) != row.get(c) for c in bound):
                    continue
                if any(existing.get(c) is None for c in unbound):
                    continue
                matching += 1
            if matching >= capacity:
                return True
        return False

    def _finite_group_capacity(
        self,
        table_schema: TableSchema,
        columns: Sequence[exp.Identifier],
    ) -> int | None:
        """Product of finite domain sizes for ``columns``, or ``None`` if any is open."""
        capacity = 1
        for col_ident in columns:
            size = self._finite_domain_size(table_schema, col_ident)
            if size is None:
                return None
            capacity *= size
        return capacity

    def _finite_domain_size(
        self,
        table_schema: TableSchema,
        col_ident: exp.Identifier,
    ) -> int | None:
        """Cardinality of a closed domain (enum/allowed/boolean), else ``None``."""
        column = table_schema.columns[col_ident]
        space = space_for_column(
            column,
            dialect=self.schema.dialect,
            unique=self.schema.is_unique(table_schema.table, col_ident),
        )
        if space.allowed is not None:
            return len(space.allowed)
        if space.family == TypeFamily.BOOLEAN:
            return 2
        return None

    def _unique_conflict_group(
        self,
        table_schema: TableSchema,
        row: Mapping[exp.Identifier, Any],
        existing_rows: Sequence[Mapping[exp.Identifier, Any]],
    ) -> tuple[exp.Identifier, ...] | None:
        """Return the first uniqueness group that collides, or ``None``."""
        for group in table_schema.uniqueness_groups():
            if any(row.get(col) is None for col in group):
                continue
            target = tuple(row[col] for col in group)
            for existing in existing_rows:
                if tuple(existing.get(col) for col in group) == target:
                    return group
        return None

    def _validate_checks(
        self,
        table_schema: TableSchema,
        row: Mapping[exp.Identifier, Any],
    ) -> None:
        for check in table_schema.checks:
            if not check.supported:
                raise ConstraintViolationError(
                    f"unsupported_check_constraint:{check.reason or 'unknown'}"
                )
            required = set(check.referenced_columns)
            if not required <= set(row):
                continue
            env = Environment.from_row(row)
            if concrete(check.expression, env) is False:
                raise ConstraintViolationError(
                    f"check_constraint_failed:{table_schema.name}"
                )

    @staticmethod
    def _used_values(
        existing_rows: Sequence[Mapping[exp.Identifier, Any]],
        column: exp.Identifier,
    ) -> set[Any]:
        return {
            row[column]
            for row in existing_rows
            if column in row and row[column] is not None
        }


__all__ = ["DomainGenerator"]
