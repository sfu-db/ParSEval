from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Tuple

from sqlglot import exp

from parseval.dtype import DataType
from parseval.instance.schema import normalize_identifier
from parseval.solver.types import SolverVar

CellBinding = SolverVar | exp.Expression


class ScopeResolutionError(ValueError):
    pass


def _identifier(value: object) -> exp.Identifier:
    if isinstance(value, exp.Identifier):
        return value
    if isinstance(value, (exp.Table, exp.Column)):
        return value.this
    return exp.to_identifier(str(value))


def _same_identifier(left: object, right: object, dialect: str) -> bool:
    return normalize_identifier(
        _identifier(left).name,
        dialect,
    ) == normalize_identifier(
        _identifier(right).name,
        dialect,
    )


@dataclass(frozen=True)
class RowBinding:
    table: exp.Table
    alias: Optional[exp.Identifier]
    row_index: int
    columns: Mapping[exp.Identifier, CellBinding]
    scope_id: str
    source_step_type: str
    provenance: str = "generated"

    @classmethod
    def for_table(
        cls,
        *,
        table: exp.Table,
        alias: Optional[exp.Identifier],
        row_index: int,
        columns: Mapping[exp.Identifier, DataType],
        scope: "Scope",
        source_step: object,
    ) -> "RowBinding":
        vars_by_column: Dict[exp.Identifier, SolverVar] = {}
        step_type = source_step.__class__.__name__
        for column, dtype in columns.items():
            column_name = column.name
            table_name = table.name
            alias_name = alias.name if alias is not None else table_name
            key = f"q{scope.query_id}.{scope.scope_id}.{alias_name}.r{row_index}.{column_name}"
            vars_by_column[column] = SolverVar(
                key=key,
                dtype=dtype,
                meta={
                    "table": table_name,
                    "alias": alias_name,
                    "column": column_name,
                    "row_index": row_index,
                    "scope_id": scope.scope_id,
                    "source_step": step_type,
                },
            )
        return cls(
            table=table,
            alias=alias,
            row_index=row_index,
            columns=vars_by_column,
            scope_id=scope.scope_id,
            source_step_type=step_type,
        )

    def resolve(
        self,
        column: exp.Identifier | exp.Column | str,
        *,
        dialect: str = "sqlite",
    ) -> Optional[CellBinding]:
        requested = _identifier(column)
        for candidate, variable in self.columns.items():
            if _same_identifier(candidate, requested, dialect):
                return variable
        return None


@dataclass
class RelationBinding:
    rows: List[RowBinding] = field(default_factory=list)
    expressions: Dict[exp.Expression, exp.Expression] = field(default_factory=dict)

    @property
    def variables(self) -> Tuple[SolverVar, ...]:
        found: Dict[str, SolverVar] = {}
        for row in self.rows:
            for var in row.columns.values():
                if isinstance(var, SolverVar):
                    found[var.var_key] = var
        for expression in self.expressions.values():
            for var in expression.find_all(SolverVar):
                found[var.var_key] = var
        return tuple(found[key] for key in sorted(found))


@dataclass
class Branch:
    relation: RelationBinding
    constraints: List[exp.Expression] = field(default_factory=list)
    equalities: List[Tuple[SolverVar, SolverVar]] = field(default_factory=list)

    @property
    def variables(self) -> Tuple[SolverVar, ...]:
        found = {var.var_key: var for var in self.relation.variables}
        for constraint in self.constraints:
            for var in constraint.find_all(SolverVar):
                found[var.var_key] = var
        for left, right in self.equalities:
            found[left.var_key] = left
            found[right.var_key] = right
        return tuple(found[key] for key in sorted(found))


@dataclass
class Scope:
    query_id: int
    scope_id: str
    parent: Optional["Scope"] = None
    dialect: str = "sqlite"
    _rows: List[RowBinding] = field(default_factory=list)

    def child(self, suffix: str) -> "Scope":
        return Scope(
            query_id=self.query_id,
            scope_id=f"{self.scope_id}.{suffix}",
            parent=self,
            dialect=self.dialect,
        )

    def add_row(self, row: RowBinding) -> None:
        self._rows.append(row)

    def add_rows(self, rows: Iterable[RowBinding]) -> None:
        for row in rows:
            self.add_row(row)

    def resolve_column(self, column: exp.Column) -> CellBinding:
        table = column.args.get("table")
        name = column.this
        matches = self._local_matches(table, name)
        if not matches and self.parent is not None:
            return self.parent.resolve_column(column)
        if not matches:
            raise ScopeResolutionError(f"unknown column {column.sql()}")
        distinct = {
            match.var_key if isinstance(match, SolverVar) else match.sql(): match
            for match in matches
        }
        if len(distinct) > 1:
            raise ScopeResolutionError(f"ambiguous column {column.sql()}")
        return next(iter(distinct.values()))

    def _local_matches(
        self, table: Optional[exp.Identifier], column: exp.Identifier
    ) -> List[CellBinding]:
        matches: List[CellBinding] = []
        for row in self._rows:
            if table is not None and not any(
                candidate is not None and _same_identifier(table, candidate, self.dialect)
                for candidate in (row.alias, row.table.this)
            ):
                continue
            resolved = row.resolve(column, dialect=self.dialect)
            if resolved is not None:
                matches.append(resolved)
        return matches
