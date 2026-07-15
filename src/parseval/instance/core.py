"""In-memory Instance: DDL schema, symbolic Variables, concrete export.

Uses sqlglot ``exp.Table`` / ``exp.Identifier`` keys only — no
``parseval.identity`` dependency.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, TYPE_CHECKING

from sqlglot import exp, parse

from parseval.plan.context import Row
from parseval.plan.rex import Symbol, Variable
from .exporter import InstanceSnapshot, TableBatch
from .schema import (
    ForeignKeyConstraint,
    InstanceSchema,
    TableSchema,
    table_key,
)
from .symbols import SymbolIndex

if TYPE_CHECKING:
    from parseval.domain.generator import DomainGenerator

_BOOTSTRAP_MISSING = object()


@dataclass(frozen=True)
class RowCreationResult:
    """Rows created keyed by resolved ``exp.Table`` nodes."""

    created: dict[exp.Table, tuple[Row, ...]]
    positions: dict[exp.Table, int]


class Instance:
    def __init__(self, ddls: str, name: str, dialect: str) -> None:
        self.ddls = ddls
        self.name = name
        self.dialect = dialect
        self.schema = InstanceSchema.from_ddl(ddls, dialect)
        self.data: Dict[exp.Table, List[Row]] = defaultdict(list)
        self.symbols = SymbolIndex()
        from parseval.domain.generator import DomainGenerator

        self._domain: DomainGenerator = DomainGenerator(self.schema)
        self._bootstrapping: set[exp.Table] = set()
        self._bootstrapping_values: Dict[exp.Table, Dict[exp.Identifier, Any]] = {}
        self._expanding_unique_fks: set[exp.Table] = set()

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
    def _row_value_dict(row: Row) -> dict[exp.Identifier, Any]:
        values: dict[exp.Identifier, Any] = {}
        for column, symbol in row.items():
            if isinstance(column, exp.Identifier):
                values[column] = symbol.concrete
            else:
                # Legacy / unexpected key — skip non-identifiers.
                continue
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
        *,
        defer_parents: Set[exp.Table] | None = None,
    ) -> RowCreationResult:
        """Create one constraint-valid row.

        Instance orchestrates FK parent *rows* and placement. Missing cell
        values are invented only by ``DomainGenerator.complete_row``.

        ``defer_parents``: tables that will be created later in a batch;
        do not auto-create those parents here (avoids duplicate listed rows).
        """
        table_node = self.resolve_table(table)
        values_by_col = self._normalize_values(table_node, values)

        created: Dict[exp.Table, List[Row]] = defaultdict(list)
        positions: Dict[exp.Table, int] = {}
        prev_vals = self._bootstrapping_values.get(table_node)
        # Cycle partners may have already chosen shared key values — keep them.
        if prev_vals:
            for col, val in prev_vals.items():
                values_by_col.setdefault(col, val)
        self._bootstrapping.add(table_node)
        self._bootstrapping_values[table_node] = dict(values_by_col)
        phantoms: set[exp.Table] = set()
        created_ok = False
        try:
            self._merge_created(
                created,
                self._ensure_fk_parents(
                    table_node,
                    values_by_col,
                    defer_parents=defer_parents,
                    phantoms=phantoms,
                ),
            )
            main_pos = self._materialize_row(table_node, values_by_col)
            created_ok = True
        finally:
            for phantom in phantoms:
                self._bootstrapping.discard(phantom)
            self._bootstrapping.discard(table_node)
            if created_ok:
                # Placed row owns its keys; stale bootstrap uniques must not
                # leak into the next create_row (cycle partner leftovers).
                self._bootstrapping_values.pop(table_node, None)
            elif prev_vals is None:
                self._bootstrapping_values.pop(table_node, None)
            else:
                self._bootstrapping_values[table_node] = prev_vals

        created[table_node].append(self.get_row(table_node, main_pos))
        positions[table_node] = main_pos
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
    ) -> Dict[exp.Table, List[RowCreationResult]]:
        """Create rows for explicitly listed tables only.

        Empty ``{}`` or ``[]`` for a table means one fully domain-completed
        row. ``None`` / ``{}`` overall creates nothing. Unlisted tables stay
        empty unless created as FK parents of a listed table.
        """
        concretes = concretes or {}
        normalized: Dict[exp.Table, List[Dict[exp.Identifier, Any]]] = {}
        for table, payload in concretes.items():
            table_node = self.resolve_table(table)
            normalized[table_node] = self._normalize_batch(table_node, payload)

        ordered = self._creation_order(list(normalized))
        results: Dict[exp.Table, List[RowCreationResult]] = {}
        pending = set(ordered)
        for table_node in ordered:
            pending.discard(table_node)
            results[table_node] = []
            for row_values in normalized[table_node]:
                results[table_node].append(
                    self.create_row(
                        table_node, row_values, defer_parents=pending
                    )
                )
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

    def _creation_order(self, tables: List[exp.Table]) -> List[exp.Table]:
        requested = set(tables)
        visited: set[exp.Table] = set()
        ordered: list[exp.Table] = []

        def visit(table: exp.Table) -> None:
            if table in visited:
                return
            visited.add(table)
            for fk in self.get_foreign_keys(table):
                target = self.resolve_table(fk.target_table)
                if target in requested:
                    visit(target)
            ordered.append(table)

        for table in tables:
            visit(table)
        return ordered

    def _materialize_row(
        self, table: exp.Table, concretes: Dict[exp.Identifier, Any]
    ) -> int:
        """Ask domain for a full row, then place it. Always creates a new row."""
        tuple_index = len(self.get_rows(table))
        existing_rows = [self._row_value_dict(row) for row in self.get_rows(table)]
        parent_rows = self._parent_row_maps(table)
        presets = dict(concretes)
        locked = set(presets)

        if self._has_bootstrapping_fk(table):
            table_schema = self.schema.tables[table]
            for fk in self.get_foreign_keys(table):
                target = self.resolve_table(fk.target_table)
                if target not in self._bootstrapping:
                    continue
                sources = list(fk.source_columns)
                targets = list(fk.target_columns)
                # Self-FK after the first row:
                # - PK/unique sources (country_id→country_id): fresh self-key
                # - non-unique sources (reportsto→employeenumber): bind via Domain
                if target == table and existing_rows:
                    self_key = any(
                        src in table_schema.primary_key
                        or self.schema.is_unique(table, src)
                        or table_schema.columns[src].unique
                        for src in sources
                    )
                    if not self_key:
                        continue
                    synthetic: Dict[exp.Identifier, Any] = {}
                    for local_col, target_col in zip(sources, targets):
                        value = self._domain.next_value(
                            table,
                            target_col,
                            existing_rows=existing_rows,
                        )
                        presets[local_col] = value
                        locked.add(local_col)
                        synthetic[target_col] = value
                        if local_col != target_col:
                            presets[target_col] = value
                            locked.add(target_col)
                    parent_rows.setdefault(target, []).append(synthetic)
                    continue
                synthetic = {}
                for local_col, target_col in zip(sources, targets):
                    if (
                        local_col in concretes
                        and concretes[local_col] is None
                        and table_schema.columns[local_col].nullable
                    ):
                        presets[local_col] = None
                        locked.add(local_col)
                        continue
                    preferred = (
                        concretes[local_col]
                        if local_col in concretes and concretes[local_col] is not None
                        else _BOOTSTRAP_MISSING
                    )
                    if preferred is not _BOOTSTRAP_MISSING:
                        value = preferred
                    else:
                        value = self._ensure_bootstrap_value(
                            target, target_col, preferred=preferred
                        )
                        presets[local_col] = value
                        locked.add(local_col)
                    synthetic[target_col] = value
                    if target == table and target_col not in presets:
                        presets[target_col] = value
                        locked.add(target_col)
                # Domain fail-closed on dangling FKs; cycle partners are not
                # placed yet, so expose the shared bootstrap key as a parent map.
                parent_rows.setdefault(target, []).append(synthetic)

        completed = self._domain.complete_row(
            table,
            presets=presets,
            existing_rows=existing_rows,
            parent_rows=parent_rows,
            locked=locked,
        )
        self.place_row(table, completed)
        return tuple_index

    def _parent_row_maps(
        self, table: exp.Table
    ) -> Dict[exp.Table, List[Dict[exp.Identifier, Any]]]:
        parents: Dict[exp.Table, List[Dict[exp.Identifier, Any]]] = {}
        for fk in self.get_foreign_keys(table):
            target = self.resolve_table(fk.target_table)
            parents[target] = [
                self._row_value_dict(row) for row in self.get_rows(target)
            ]
        return parents

    def _has_bootstrapping_fk(self, table: exp.Table) -> bool:
        return any(
            self.resolve_table(fk.target_table) in self._bootstrapping
            for fk in self.get_foreign_keys(table)
        )

    def _ensure_bootstrap_value(
        self,
        table: exp.Table,
        column: exp.Identifier,
        *,
        preferred: Any,
    ) -> Any:
        active = self._bootstrapping_values.setdefault(table, {})
        current = active.get(column, _BOOTSTRAP_MISSING)
        if current is not _BOOTSTRAP_MISSING and current is not None:
            return current
        if preferred is not _BOOTSTRAP_MISSING and preferred is not None:
            active[column] = preferred
            return preferred
        # Ask Domain / ValueSpace — never hardcode a sentinel like ``1``.
        value = self._domain.next_value(table, column)
        active[column] = value
        return value

    def _ensure_fk_parents(
        self,
        table: exp.Table,
        values: Dict[exp.Identifier, Any],
        *,
        defer_parents: Set[exp.Table] | None = None,
        phantoms: set[exp.Table] | None = None,
    ) -> dict[exp.Table, list[Row]]:
        """Ensure parent *rows* exist; Domain binds FK cell values later.

        If the child FK is fully set and no matching parent row exists, create
        one. If the parent table is empty, create a parent so Domain can bind.
        """
        defer = defer_parents or set()
        phantom_out = phantoms if phantoms is not None else set()
        created: dict[exp.Table, list[Row]] = defaultdict(list)
        for fk in self.get_foreign_keys(table):
            target = self.resolve_table(fk.target_table)
            if target in self._bootstrapping:
                continue
            if target in defer:
                if self.get_rows(target):
                    # Parent already has rows — Domain can bind to them. Do not
                    # mark bootstrapping or materialize will overwrite with
                    # synthetic keys that collide with existing unique FKs.
                    continue
                # Empty cycle partner listed later: share bootstrap keys.
                self._bootstrapping.add(target)
                phantom_out.add(target)
                self._bootstrapping_values.setdefault(target, {})
                continue

            if all(values.get(c) is not None for c in fk.source_columns):
                parent_vals = {
                    t: values[s]
                    for s, t in zip(fk.source_columns, fk.target_columns)
                }
                if self._find_existing_row(target, parent_vals) is None:
                    # No matching parent for this child FK → add one.
                    result = self.create_row(target, parent_vals)
                    self._merge_created(created, result.created)
                continue

            if self.get_rows(target):
                continue

            # Parent table empty and child FK unset → create a parent to bind to.
            proposed: Dict[exp.Identifier, Any] = {
                target_col: values[source]
                for source, target_col in zip(fk.source_columns, fk.target_columns)
                if source in values and values[source] is not None
            }
            result = self.create_row(target, proposed)
            self._merge_created(created, result.created)

        self._expand_parents_for_unique_fks(table, values, created, defer)
        return created

    def _expand_parents_for_unique_fks(
        self,
        table: exp.Table,
        values: Dict[exp.Identifier, Any],
        created: dict[exp.Table, list[Row]],
        defer: Set[exp.Table],
    ) -> None:
        """Spawn extra parents when FK columns form a uniqueness group.

        Spawns even if the parent is deferred in the current ``create_rows``
        batch (cyclic FK graphs): a deferred parent that is not yet placed
        cannot contribute a free combo, and Domain would otherwise lock a
        colliding all-FK unique key.
        """
        if table in self._expanding_unique_fks:
            return
        self._expanding_unique_fks.add(table)
        try:
            self._expand_parents_for_unique_fks_body(
                table, values, created, defer
            )
        finally:
            self._expanding_unique_fks.discard(table)

    def _expand_parents_for_unique_fks_body(
        self,
        table: exp.Table,
        values: Dict[exp.Identifier, Any],
        created: dict[exp.Table, list[Row]],
        defer: Set[exp.Table],
    ) -> None:
        table_schema = self.schema.tables[table]
        presets = dict(values)
        fk_sources = {
            col
            for fk in table_schema.foreign_keys
            for col in fk.source_columns
        }
        existing = [self._row_value_dict(row) for row in self.get_rows(table)]
        for group in table_schema.uniqueness_groups():
            if not group or not all(col in fk_sources for col in group):
                continue
            fks = self._fks_for_unique_group(table, group)
            if not fks:
                continue
            # Self-FK unique groups cannot gain a free combo by spawning.
            if any(target == table for _, target in fks):
                continue
            if all(presets.get(col) is not None for col in group):
                continue

            spawns = 0
            while self._pick_free_fk_combo(table, group, existing, presets) is None:
                if spawns >= 8:
                    break
                spawn = next(
                    (
                        (fk, target)
                        for fk, target in reversed(fks)
                        if any(
                            presets.get(c) is None
                            for c in fk.source_columns
                            if c in group
                        )
                    ),
                    None,
                )
                if spawn is None:
                    break
                fk, target = spawn
                # Empty deferred cycle partner: bootstrap shares keys with the
                # later batch row. Spawning here would duplicate that row.
                if target in defer and not self.get_rows(target):
                    break
                # Outer create_row(target) is in flight — do not nest another
                # place of the same table; just pin a fresh key on this row.
                if target in self._bootstrapping:
                    for col in group:
                        if presets.get(col) is not None:
                            continue
                        fresh = self._domain.next_value(
                            table, col, existing_rows=existing
                        )
                        values[col] = fresh
                        presets[col] = fresh
                    break
                before = len(self.get_rows(target))
                # Spawn a full parent key for this FK (composite-safe).
                proposed: Dict[exp.Identifier, Any] = {}
                parent_existing = [
                    self._row_value_dict(row) for row in self.get_rows(target)
                ]
                for source, target_col in zip(fk.source_columns, fk.target_columns):
                    if presets.get(source) is not None:
                        proposed[target_col] = presets[source]
                        continue
                    used = {
                        row.get(source)
                        for row in existing
                        if row.get(source) is not None
                    }
                    fresh = self._domain.next_value(
                        target,
                        target_col,
                        existing_rows=parent_existing,
                        avoid=tuple(used),
                    )
                    proposed[target_col] = fresh
                    if source in group:
                        values[source] = fresh
                        presets[source] = fresh
                result = self.create_row(target, proposed)
                self._merge_created(created, result.created)
                spawns += 1
                if len(self.get_rows(target)) <= before:
                    break

            # Domain binds each FK independently and can miss a free joint
            # combo; pin one here so complete_row locks a non-colliding key.
            combo = self._pick_free_fk_combo(table, group, existing, presets)
            if combo is None:
                continue
            for col, val in zip(group, combo):
                if values.get(col) is None:
                    values[col] = val
                    presets[col] = val

    def _fks_for_unique_group(
        self,
        table: exp.Table,
        group: Tuple[exp.Identifier, ...],
    ) -> list[tuple[ForeignKeyConstraint, exp.Table]]:
        """FKs that intersect ``group`` and together cover every group column."""
        group_set = set(group)
        fks: list[tuple[ForeignKeyConstraint, exp.Table]] = []
        covered: set[exp.Identifier] = set()
        for fk in self.get_foreign_keys(table):
            overlap = group_set.intersection(fk.source_columns)
            if not overlap:
                continue
            fks.append((fk, self.resolve_table(fk.target_table)))
            covered.update(overlap)
        if covered != group_set:
            return []
        return fks

    def _pick_free_fk_combo(
        self,
        table: exp.Table,
        group: Tuple[exp.Identifier, ...],
        existing: list[dict[exp.Identifier, Any]],
        presets: dict[exp.Identifier, Any],
    ) -> tuple[Any, ...] | None:
        """Pick an unused uniqueness-group key built from full parent FK keys.

        Each FK that covers the group contributes assignments projected from
        real parent rows. Shared columns must agree — validity is by
        construction (no post-hoc dangling-FK filter).
        """
        from itertools import product

        fks = self._fks_for_unique_group(table, group)
        if not fks:
            return None

        group_set = set(group)
        options_per_fk: list[list[dict[exp.Identifier, Any]]] = []
        for fk, target in fks:
            sources_in_group = [c for c in fk.source_columns if c in group_set]
            parents = self.get_rows(target)
            if not parents:
                return None
            opts: list[dict[exp.Identifier, Any]] = []
            seen: set[tuple[Any, ...]] = set()
            for parent in parents:
                parent_vals = self._row_value_dict(parent)
                assignment = {
                    source: parent_vals.get(target_col)
                    for source, target_col in zip(
                        fk.source_columns, fk.target_columns
                    )
                    if source in group_set
                }
                if any(
                    presets.get(col) is not None and presets[col] != assignment.get(col)
                    for col in assignment
                ):
                    continue
                key = tuple(assignment[col] for col in sources_in_group)
                if key in seen:
                    continue
                seen.add(key)
                opts.append(assignment)
            if not opts:
                return None
            options_per_fk.append(opts)

        used = {tuple(row.get(col) for col in group) for row in existing}
        for parts in product(*options_per_fk):
            by_col: dict[exp.Identifier, Any] = {}
            conflict = False
            for part in parts:
                for col, val in part.items():
                    if col in by_col and by_col[col] != val:
                        conflict = True
                        break
                    by_col[col] = val
                if conflict:
                    break
            if conflict or any(col not in by_col for col in group):
                continue
            combo = tuple(by_col[col] for col in group)
            if combo in used:
                continue
            if any(
                presets.get(col) is not None and presets[col] != by_col[col]
                for col in group
            ):
                continue
            return combo
        return None

    @staticmethod
    def _merge_created(
        target: dict[exp.Table, list[Row]],
        source: Mapping[exp.Table, Tuple[Row, ...] | list[Row]],
    ) -> None:
        for table, rows in source.items():
            target[table].extend(rows)

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
                        {
                            col: self._row_value_dict(row).get(
                                self.resolve_column(table_node, col)
                            )
                            for col in columns
                        }
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
            raw = self.ddls
        else:
            raw = "; ".join(by_name[n] for n in order) + ";"
        # Quote identifiers so reserved names (transaction, index, …) load.
        parts: list[str] = []
        for stmt in parse(raw, read=self.dialect):
            if stmt is None:
                continue
            parts.append(stmt.sql(dialect=self.dialect, identify=True))
        return ";\n".join(parts) + (";" if parts else "")

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
