from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time as dt_time
from typing import AbstractSet, Any, Mapping, Sequence

from sqlglot import exp

from parseval.instance.schema import table_key
from parseval.solver.types import SolverVar


class SchemaConstraintLoweringError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def schema_constraints_for_solver_row(
    instance: Any,
    table: exp.Table,
    sv_map: Mapping[str, SolverVar],
    *,
    exact_columns: AbstractSet[str] = frozenset(),
    include_checks: bool = True,
    include_existing_uniques: bool = True,
    include_existing_fks: bool = True,
    constrain_exact_fks: bool = True,
) -> list[exp.Expression]:
    table_schema = instance.database_constraints(table)
    constraints: list[exp.Expression] = []
    available = set(sv_map)
    required_non_null: set[str] = set(exact_columns)
    if not exact_columns:
        required_non_null.clear()

    if include_checks:
        constraints.extend(_check_constraints_for_solver_row(instance, table, sv_map))

    if include_existing_uniques:
        for group in table_schema.uniqueness_groups():
            names = tuple(column.name for column in group)
            if not set(names) <= available:
                continue
            for row in instance.get_rows(table_schema.table):
                values = instance._row_value_dict(row)
                existing = [values.get(column) for column in group]
                if any(value is None for value in existing):
                    continue
                constraints.append(_unique_non_collision_constraint(sv_map, names, existing))
                required_non_null.update(names)

    if include_existing_fks:
        for fk in table_schema.foreign_keys:
            names = tuple(column.name for column in fk.source_columns)
            if len(names) != 1 or not set(names) <= available:
                continue
            if exact_columns and not set(names).intersection(exact_columns):
                continue
            if not constrain_exact_fks and set(names).intersection(exact_columns):
                continue
            target_values = []
            target_column = fk.target_columns[0]
            for parent_row in instance.get_rows(fk.target_table):
                value = instance._row_value_dict(parent_row).get(target_column)
                if value is not None:
                    target_values.append(value)
            if target_values:
                constraints.append(
                    exp.In(
                        this=sv_map[names[0]],
                        expressions=[
                            _literal_for_value(value)
                            for value in dict.fromkeys(target_values)
                        ],
                    )
                )
                required_non_null.update(names)

    return _not_null_constraints_for_columns(
        table_schema,
        sv_map,
        required_non_null,
    ) + constraints


def batch_unique_constraints_for_solver_rows(
    instance: Any,
    table: exp.Table,
    sv_rows: Sequence[Mapping[str, SolverVar]],
) -> list[exp.Expression]:
    table_schema = instance.database_constraints(table)
    constraints: list[exp.Expression] = []
    for group in table_schema.uniqueness_groups():
        names = tuple(column.name for column in group)
        if any(not set(names) <= set(sv_map) for sv_map in sv_rows):
            continue
        for left_index, left in enumerate(sv_rows):
            for right in sv_rows[left_index + 1 :]:
                constraints.append(_unique_non_collision_constraint(left, names, [right[name] for name in names]))
    return constraints


def _check_constraints_for_solver_row(
    instance: Any,
    table: exp.Table,
    sv_map: Mapping[str, SolverVar],
) -> list[exp.Expression]:
    table_schema = instance.database_constraints(table)
    table_name = table_key(table_schema.table)
    constraints: list[exp.Expression] = []
    available = set(sv_map)
    for check in table_schema.checks:
        if not check.supported:
            raise SchemaConstraintLoweringError(
                f"unsupported_check_constraint:{table_name}:{check.reason or 'unknown'}"
            )
        referenced = {column.name for column in check.referenced_columns}
        if not referenced:
            continue
        missing = sorted(referenced - available)
        if missing:
            raise SchemaConstraintLoweringError(
                f"unlowerable_check_constraint:{table_name}:{missing[0]}"
            )
        rewritten = deepcopy(check.expression)
        for col in list(rewritten.find_all(exp.Column)):
            if isinstance(col.this, exp.Identifier):
                replacement = sv_map.get(col.this.name)
                if replacement is not None:
                    col.replace(replacement.copy())
        for col in rewritten.find_all(exp.Column):
            column_name = col.name or col.sql(dialect=instance.dialect)
            raise SchemaConstraintLoweringError(
                f"unlowerable_check_constraint:{table_name}:{column_name}"
            )
        constraints.append(rewritten)
    return constraints


def _not_null_constraints_for_columns(
    table_schema: Any,
    sv_map: Mapping[str, SolverVar],
    column_names: AbstractSet[str],
) -> list[exp.Expression]:
    constraints: list[exp.Expression] = []
    for column, column_schema in table_schema.columns.items():
        if (
            column.name not in column_names
            or column.name not in sv_map
            or column_schema.nullable
        ):
            continue
        constraints.append(
            exp.Not(this=exp.Is(this=sv_map[column.name], expression=exp.Null()))
        )
    return constraints


def _unique_non_collision_constraint(
    sv_map: Mapping[str, SolverVar],
    names: tuple[str, ...],
    existing: Sequence[Any],
) -> exp.Expression:
    atoms = [
        exp.NEQ(
            this=sv_map[name],
            expression=value.copy() if isinstance(value, exp.Expression) else _literal_for_value(value),
        )
        for name, value in zip(names, existing)
    ]
    expr = atoms[0]
    for atom in atoms[1:]:
        expr = exp.Or(this=expr, expression=atom)
    return expr


def literal_for_value(value: Any) -> exp.Expression:
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    return exp.Literal(
        this=str(value),
        is_string=isinstance(value, (str, date, datetime, dt_time)),
    )


_literal_for_value = literal_for_value
