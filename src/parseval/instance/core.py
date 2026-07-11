"""In-memory Instance: DDL schema, symbolic Variables, concrete export.

Uses sqlglot ``exp.Table`` / ``exp.Identifier`` keys only — no
``parseval.identity`` dependency.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, TYPE_CHECKING

from sqlglot import exp

from parseval.domain.exceptions import ConstraintViolationError, UniqueConflictError
from parseval.plan.rex import Row, Symbol, Variable

from .exporter import InstanceSnapshot, TableBatch
from .schema import (
    ForeignKeyConstraint,
    InstanceSchema,
    table_key,
)
from .symbols import SymbolIndex

if TYPE_CHECKING:
    from parseval.domain.generator import DomainGenerator

_BOOTSTRAP_MISSING = object()


@dataclass(frozen=True)
class RowCreationResult:
    """Rows created keyed by canonical table name (normalized)."""

    created: dict[str, tuple[Row, ...]]
    positions: dict[str, int]


class Instance:
    def __init__(self, ddls: str, name: str, dialect: str, normalize: bool = True) -> None:
        del normalize
        self.ddls = ddls
        self.name = name
        self.dialect = dialect
        self.schema = InstanceSchema.from_ddl(ddls, dialect)
        self.data: Dict[exp.Table, List[Row]] = defaultdict(list)
        self.symbols = SymbolIndex()
        from parseval.domain.generator import DomainGenerator

        self._domain: DomainGenerator = DomainGenerator(self.schema)
        self._bootstrapping: set[str] = set()
        self._bootstrapping_values: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # schema facade
    # ------------------------------------------------------------------

    @property
    def tables(self) -> Dict[str, Dict[str, str]]:
        return self.schema.name_mapping()

    def resolve_table(self, table: exp.Table | str) -> exp.Table:
        return self.schema.resolve_table(table)

    def resolve_column(
        self, table: exp.Table | str, column: exp.Identifier | str | exp.Column
    ) -> exp.Identifier:
        return self.schema.resolve_column(table, column)

    def database_constraints(self, table: exp.Table | str) -> TableSchema:
        return self.schema.database_constraints(table)

    def nullable(
        self, table: exp.Table | str, column: exp.Identifier | str | exp.Column
    ) -> bool:
        return self.schema.nullable(table, column)

    def is_unique(
        self, table: exp.Table | str, column: exp.Identifier | str | exp.Column
    ) -> bool:
        return self.schema.is_unique(table, column)

    def get_column_type(
        self, table: exp.Table | str, column: exp.Identifier | str | exp.Column
    ) -> exp.DataType:
        table_node = self.resolve_table(table)
        col = self.resolve_column(table_node, column)
        return self.schema.tables[table_node].columns[col].datatype

    def column_names(self, table: exp.Table | str) -> Tuple[str, ...]:
        return self.schema.column_names(table)

    def get_primary_key(self, table: exp.Table | str) -> Tuple[exp.Identifier, ...]:
        return self.schema.get_table(table).primary_key

    def get_foreign_keys(self, table: exp.Table | str) -> Tuple[ForeignKeyConstraint, ...]:
        return self.schema.get_table(table).foreign_keys

    # ------------------------------------------------------------------
    # rows
    # ------------------------------------------------------------------

    def add_row(self, table: exp.Table | str, row: Row) -> None:
        self.data[self.resolve_table(table)].append(row)

    def get_rows(self, table: exp.Table | str) -> List[Row]:
        return self.data[self.resolve_table(table)]

    def get_row(self, table: exp.Table | str, index: int) -> Row:
        return self.get_rows(table)[index]

    def get_column_data(
        self, table: exp.Table | str, column: exp.Identifier | str | exp.Column
    ) -> List[Symbol]:
        col = self.resolve_column(table, column)
        return [row[col] for row in self.get_rows(table)]

    @staticmethod
    def _row_value_dict(row: Row) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for column, symbol in row.items():
            if isinstance(column, exp.Identifier):
                key = column.name
            else:
                key = getattr(getattr(column, "name", None), "normalized", None) or str(column)
            values[key] = symbol.concrete
        return values

    def _normalize_values(
        self,
        table: exp.Table,
        values: Mapping[exp.Identifier | str | exp.Column, Any] | None,
    ) -> Dict[exp.Identifier, Any]:
        result: Dict[exp.Identifier, Any] = {}
        for column, value in (values or {}).items():
            result[self.resolve_column(table, column)] = value
        return result

    # ------------------------------------------------------------------
    # place / create
    # ------------------------------------------------------------------

    def place_row(
        self,
        table: exp.Table | str,
        values: Mapping[exp.Identifier | str | exp.Column, Any],
    ) -> Row:
        table_node = self.resolve_table(table)
        table_schema = self.schema.tables[table_node]
        tname = table_key(table_node)
        tuple_index = len(self.get_rows(table_node))
        rowid = f"{tname}_rowid_{tuple_index}"
        values_by_col = self._normalize_values(table_node, values)
        cells: Dict[Any, Variable] = {}
        for col_ident, col in table_schema.columns.items():
            dtype_sql = col.datatype.sql(dialect=self.dialect)
            z_name = f"{tname}_{col_ident.name}_{dtype_sql}_{tuple_index}"
            var = Variable(
                this=z_name,
                _type=dtype_sql,
                concrete=values_by_col.get(col_ident),
                table=table_node,
                column=col_ident,
                rowid=rowid,
            )
            var.type = dtype_sql
            cells[col_ident] = var
            self.symbols.register(var)
        row = Row(this=rowid, columns=cells)
        self.add_row(table_node, row)
        return row

    def create_row(
        self,
        table: exp.Table | str,
        values: Mapping[exp.Identifier | str | exp.Column, Any] | None = None,
    ) -> RowCreationResult:
        """Create one constraint-valid row.

        Instance orchestrates FK parent *rows* and placement. Missing cell
        values are invented only by ``DomainGenerator.complete_row``.
        """
        table_node = self.resolve_table(table)
        tname = table_key(table_node)
        values_by_col = self._normalize_values(table_node, values)

        created: Dict[str, List[Row]] = defaultdict(list)
        positions: Dict[str, int] = {}
        prev_vals = self._bootstrapping_values.get(tname)
        self._bootstrapping.add(tname)
        self._bootstrapping_values[tname] = {
            c.name: v for c, v in values_by_col.items()
        }
        try:
            self._merge_created(
                created,
                self._ensure_fk_parents(table_node, values_by_col),
            )
            main_pos = self._materialize_row(table_node, values_by_col)
        finally:
            self._bootstrapping.discard(tname)
            if prev_vals is None:
                self._bootstrapping_values.pop(tname, None)
            else:
                self._bootstrapping_values[tname] = prev_vals

        created[tname].append(self.get_row(table_node, main_pos))
        positions[tname] = main_pos
        return RowCreationResult(
            created={k: tuple(v) for k, v in created.items()},
            positions=positions,
        )

    def create_rows(
        self,
        concretes: Mapping[
            exp.Table | str,
            Mapping[exp.Identifier | str, Sequence[Any]]
            | Sequence[Mapping[exp.Identifier | str, Any]],
        ]
        | None = None,
    ) -> Dict[str, List[RowCreationResult]]:
        """Create rows for explicitly listed tables only.

        Empty ``{}`` or ``[]`` for a table means one fully domain-completed
        row. ``None`` / ``{}`` overall creates nothing. Unlisted tables stay
        empty unless created as FK parents of a listed table.
        """
        concretes = concretes or {}
        normalized: Dict[str, List[Dict[exp.Identifier, Any]]] = {}
        for table, payload in concretes.items():
            table_node = self.resolve_table(table)
            tname = table_key(table_node)
            normalized[tname] = self._normalize_batch(table_node, payload)

        ordered = self._creation_order(list(normalized))
        results: Dict[str, List[RowCreationResult]] = {}
        name_to_table = {table_key(t): t for t in self.schema.tables}
        for tname in ordered:
            results[tname] = []
            table_node = name_to_table[tname]
            for row_values in normalized[tname]:
                results[tname].append(self.create_row(table_node, row_values))
        return results

    def _normalize_batch(
        self,
        table: exp.Table,
        payload: (
            Mapping[exp.Identifier | str, Sequence[Any]]
            | Sequence[Mapping[exp.Identifier | str, Any]]
        ),
    ) -> List[Dict[exp.Identifier, Any]]:
        if isinstance(payload, Mapping):
            if not payload:
                return [{}]
            cols: Dict[exp.Identifier, Sequence[Any]] = {}
            for column, values in payload.items():
                col = self.resolve_column(table, column)
                cols[col] = values if isinstance(values, (list, tuple)) else [values]
            n = max(len(v) for v in cols.values())
            return [
                {c: vals[i] for c, vals in cols.items() if i < len(vals)}
                for i in range(n)
            ]
        rows: List[Dict[exp.Identifier, Any]] = []
        for row in payload:
            rows.append(
                {self.resolve_column(table, c): v for c, v in row.items()}
            )
        return rows or [{}]

    def _creation_order(self, names: List[str]) -> List[str]:
        requested = set(names)
        visited: set[str] = set()
        ordered: list[str] = []
        name_to_table = {table_key(t): t for t in self.schema.tables}

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            table = name_to_table[name]
            for fk in self.get_foreign_keys(table):
                target = table_key(fk.target_table)
                if target in requested:
                    visit(target)
            ordered.append(name)

        for name in names:
            visit(name)
        return ordered

    def _materialize_row(
        self, table: exp.Table, concretes: Dict[exp.Identifier, Any]
    ) -> int:
        """Ask domain for a full row, then place it. Always creates a new row."""
        tuple_index = len(self.get_rows(table))
        existing_rows = [self._row_value_dict(row) for row in self.get_rows(table)]
        parent_rows = self._parent_row_maps(table)
        presets = {col.name: value for col, value in concretes.items()}
        locked = set(presets)

        if self._has_bootstrapping_fk(table):
            for fk in self.get_foreign_keys(table):
                target_name = table_key(fk.target_table)
                if target_name not in self._bootstrapping:
                    continue
                for local_col, target_col in zip(fk.source_columns, fk.target_columns):
                    preferred = (
                        concretes[local_col]
                        if local_col in concretes and concretes[local_col] is not None
                        else _BOOTSTRAP_MISSING
                    )
                    value = self._ensure_bootstrap_value(
                        target_name, target_col.name, preferred=preferred
                    )
                    presets[local_col.name] = value
                    locked.add(local_col.name)
                    if target_name == table_key(table):
                        presets[target_col.name] = value
                        locked.add(target_col.name)

        completed = self._domain.complete_row(
            table,
            presets=presets,
            existing_rows=existing_rows,
            parent_rows=parent_rows,
            locked=locked,
        )
        row_values = {
            self.resolve_column(table, name): value for name, value in completed.items()
        }
        self.place_row(table, row_values)
        return tuple_index

    def _parent_row_maps(
        self, table: exp.Table
    ) -> Dict[str, List[Dict[str, Any]]]:
        parents: Dict[str, List[Dict[str, Any]]] = {}
        for fk in self.get_foreign_keys(table):
            target_name = table_key(fk.target_table)
            parents[target_name] = [
                self._row_value_dict(row) for row in self.get_rows(fk.target_table)
            ]
        return parents

    def _has_bootstrapping_fk(self, table: exp.Table) -> bool:
        return any(
            table_key(fk.target_table) in self._bootstrapping
            for fk in self.get_foreign_keys(table)
        )

    def _ensure_bootstrap_value(
        self, table_name: str, column_name: str, *, preferred: Any
    ) -> Any:
        active = self._bootstrapping_values.get(table_name)
        if active is None:
            return preferred if preferred is not _BOOTSTRAP_MISSING else 1
        current = active.get(column_name, _BOOTSTRAP_MISSING)
        if current is not _BOOTSTRAP_MISSING and current is not None:
            return current
        value = (
            preferred
            if preferred is not _BOOTSTRAP_MISSING and preferred is not None
            else 1
        )
        active[column_name] = value
        return value

    def _ensure_fk_parents(
        self, table: exp.Table, values: Dict[exp.Identifier, Any]
    ) -> dict[str, list[Row]]:
        """Create missing parent rows; do not invent FK cell values (domain does)."""
        created: dict[str, list[Row]] = defaultdict(list)
        for fk in self.get_foreign_keys(table):
            target_name = table_key(fk.target_table)
            if target_name in self._bootstrapping:
                continue

            if all(values.get(c) is not None for c in fk.source_columns):
                parent_vals = {
                    t: values[s]
                    for s, t in zip(fk.source_columns, fk.target_columns)
                }
                if self._find_existing_row(fk.target_table, parent_vals) is None:
                    result = self.create_row(fk.target_table, parent_vals)
                    self._merge_created(created, result.created)
                continue

            if self.get_rows(fk.target_table):
                continue

            # No parent yet — create one; domain will bind child FK cells later.
            proposed: Dict[exp.Identifier, Any] = {}
            for source, target in zip(fk.source_columns, fk.target_columns):
                if source in values and values[source] is not None:
                    proposed[target] = values[source]
            result = self.create_row(fk.target_table, proposed)
            self._merge_created(created, result.created)
        return created

    @staticmethod
    def _merge_created(
        target: dict[str, list[Row]],
        source: Mapping[str, Tuple[Row, ...] | list[Row]],
    ) -> None:
        for name, rows in source.items():
            target[name].extend(rows)

    def _find_existing_row(
        self, table: exp.Table, concretes: Dict[exp.Identifier, Any]
    ) -> Optional[int]:
        if not concretes:
            return None
        for index, row in enumerate(self.get_rows(table)):
            if all(row[c].concrete == v for c, v in concretes.items()):
                return index
        return None

    # ------------------------------------------------------------------
    # checkpoint / export
    # ------------------------------------------------------------------

    def checkpoint(self) -> Dict[str, Any]:
        return {
            "data": {table: list(rows) for table, rows in self.data.items()},
            "symbols": list(self.symbols.names()),
        }

    def rollback(self, token: Dict[str, Any]) -> None:
        keep = set(token["symbols"])
        for name in list(self.symbols.names()):
            if name not in keep:
                self.symbols.unregister(name)
        self.data.clear()
        for table, rows in token["data"].items():
            self.data[table] = list(rows)

    def reset(self) -> None:
        self.data.clear()
        self.symbols.clear()
        self.schema = InstanceSchema.from_ddl(self.ddls, self.dialect)
        from parseval.domain.generator import DomainGenerator

        self._domain = DomainGenerator(self.schema)

    def snapshot(self) -> InstanceSnapshot:
        tables: list[TableBatch] = []
        for table_node in self.schema.fk_safe_table_order():
            tname = table_key(table_node)
            rows = self.get_rows(table_node)
            if rows:
                columns = tuple(
                    c.name if isinstance(c, exp.Identifier) else str(c)
                    for c in rows[0].columns
                )
            else:
                columns = self.column_names(table_node)
            tables.append(
                TableBatch(
                    table_name=tname,
                    columns=columns,
                    rows=tuple(
                        {col: self._row_value_dict(row).get(col) for col in columns}
                        for row in rows
                    ),
                )
            )
        order = tuple(table_key(t) for t in self.schema.fk_safe_table_order())
        return InstanceSnapshot(
            schema_ddl=self._fk_safe_ddl(order),
            dialect=self.dialect,
            tables=tuple(tables),
        )

    def _fk_safe_ddl(self, order: tuple[str, ...]) -> str:
        by_name = {
            table_key(t): ts.create_sql.strip()
            for t, ts in self.schema.tables.items()
            if ts.create_sql.strip()
        }
        if set(by_name) != set(order):
            return self.ddls
        return "; ".join(by_name[n] for n in order) + ";"

    def to_db(
        self,
        connection_string: str,
        dialect: str = None,
        truncate_first: bool = True,
        return_inserted: bool = False,
    ):
        from .io import to_db as _to_db

        return _to_db(
            self,
            connection_string=connection_string,
            dialect=dialect,
            truncate_first=truncate_first,
            return_inserted=return_inserted,
        )

    def __repr__(self) -> str:
        return f"Instance(name={self.name}, tables={list(self.tables.keys())})"


__all__ = ["Instance"]
