from __future__ import annotations

from collections import OrderedDict, defaultdict
from typing import Any, Dict, List, Optional, Set
import random

from sqlglot import exp, parse
from sqlglot.helper import name_sequence
from sqlglot.schema import (
    MappingSchema,
    SchemaError,
    dict_depth,
    flatten_schema,
    nested_get,
    nested_set,
)

from parseval.domain import DatabaseBuilder
from parseval.domain.exceptions import ForeignKeyResolutionError, UniqueConflictError
from parseval.helper import normalize_name
from parseval.plan.rex import Row, Symbol, Variable
from parseval.states import raise_exception

from .exporter import InstanceExporter
from .loader import InstanceLoader
from .schema import build_schema_spec
from .types import (
    DatabaseTarget,
    InstanceSnapshot,
    RowCreationResult,
    TableBatch,
)


class Catalog(MappingSchema):
    def __init__(
        self,
        schema=None,
        constraints=None,
        primary_keys=None,
        foreign_keys=None,
        visible=None,
        dialect=None,
        normalize=True,
    ):
        self.constraints = {}
        self.primary_keys = {}
        self.foreign_keys = {}
        schema = OrderedDict() if schema is None else schema
        super().__init__(schema, visible, dialect, normalize)
        constraints = {} if constraints is None else constraints
        primary_keys = {} if primary_keys is None else primary_keys
        foreign_keys = {} if foreign_keys is None else foreign_keys

        for table_name, table_constraints in constraints.items():
            for column_name, column_constraints in table_constraints.items():
                for constraint in column_constraints:
                    self.add_constraint(table_name, column_name, constraint)
        for table_name, pks in primary_keys.items():
            self.add_primary_key(table_name, pks)
        for table_name, fks in foreign_keys.items():
            self.add_foreign_key(table_name, fks)

    def _normalize(self, schema):
        normalized_mapping: Dict = OrderedDict()
        flattened_schema = flatten_schema(schema, depth=dict_depth(schema) - 1)
        for keys in flattened_schema:
            columns = nested_get(schema, *zip(keys, keys))
            if not isinstance(columns, dict):
                raise SchemaError(
                    f"Table {'.'.join(keys[:-1])} must match the schema's nesting level: {len(flattened_schema[0])}."
                )
            normalized_keys = [
                self._normalize_name(
                    key, is_table=True, dialect=self.dialect, normalize=self.normalize
                )
                for key in keys
            ]
            for column_name, column_type in columns.items():
                nested_set(
                    normalized_mapping,
                    normalized_keys
                    + [
                        self._normalize_name(
                            column_name, dialect=self.dialect, normalize=self.normalize
                        )
                    ],
                    column_type,
                )
        return normalized_mapping

    @property
    def tables(self):
        return self.mapping

    def add_primary_key(
        self, table: exp.Table | str, columns: List[exp.Identifier] | exp.Identifier
    ):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        pk_set = self.primary_keys.setdefault(table, set())
        columns = [columns] if isinstance(columns, exp.Identifier) else columns
        pk_set.update(columns)

    def get_primary_key(self, table: exp.Table | str):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        return self.primary_keys.get(table, set())

    def add_foreign_key(
        self, table: exp.Table | str, foreign_key: List[exp.ForeignKey] | exp.ForeignKey
    ):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        fk_list = self.foreign_keys.setdefault(table, [])
        fks = [foreign_key] if isinstance(foreign_key, exp.ForeignKey) else foreign_key
        fk_list.extend(fks)

    def get_foreign_key(self, table: exp.Table | str):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        return self.foreign_keys.get(table, [])

    def add_constraint(
        self,
        table: exp.Table | str,
        column: exp.Column | str,
        constraint,
    ):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        column = self._normalize_name(
            column if isinstance(column, str) else column.this, normalize=self.normalize
        )
        table_constraints = self.constraints.setdefault(table, {})
        column_constraints = table_constraints.setdefault(column, set())
        constraints = [constraint] if not isinstance(constraint, (list, set, tuple)) else constraint
        column_constraints.update(constraints)

    def get_column_constraints(self, table: exp.Table | str, column: exp.Column | str):
        table = self._normalize_name(
            table if isinstance(table, str) else table.this,
            self.dialect,
            self.normalize,
        )
        column = self._normalize_name(
            column if isinstance(column, str) else column.this, normalize=self.normalize
        )
        table_constraints = self.constraints.get(table, {})
        return table_constraints.get(column, set())

    def nullable(
        self,
        table: exp.Table | str,
        column: exp.Column | str,
        normalize: Optional[bool] = None,
    ):
        del normalize
        for constraint in self.get_column_constraints(table, column):
            if isinstance(constraint.kind, exp.NotNullColumnConstraint):
                return constraint.kind.args.get("allow_null", False)
        for pk in self.get_primary_key(table):
            if pk.name == (column if isinstance(column, str) else column.this):
                return False
        return True

    def is_unique(
        self,
        table: exp.Table | str,
        column: exp.Column | str,
        normalize: Optional[bool] = None,
    ):
        del normalize
        pk_columns = self.get_primary_key(table)
        for constraint in self.get_column_constraints(table, column):
            if isinstance(
                constraint.kind,
                (exp.UniqueColumnConstraint, exp.PrimaryKeyColumnConstraint),
            ):
                return True
        if len(pk_columns) != 1:
            return False
        for pk in pk_columns:
            if pk.name == (column if isinstance(column, str) else column.this):
                return True
        return False


class Instance(Catalog):
    def __init__(self, ddls: str, name: str, dialect: str, normalize=True):
        super().__init__(dialect=dialect, normalize=normalize)
        self.ddls = ddls
        self.name = name
        self.data: Dict[str, List[Row]] = defaultdict(list)
        self.symbols = {}
        self.symbol_to_table = {}
        self.symbol_to_tuple_id = {}
        self.tuple_id_to_symbols = {}
        self.pk_fk_symbols = {}
        self.name_seq = name_sequence(self.name)
        self.schema_spec = build_schema_spec(ddls, dialect)
        self.builder = DatabaseBuilder(self.schema_spec)
        self._build_catalog(ddls, dialect)

    @property
    def catalog(self) -> "Instance":
        return self

    def _build_catalog(self, ddls: str, dialect: str):
        dependency, table_constraints = {}, {}

        def _build(
            ddl: exp.Create,
            maps: Dict,
            deps: Dict,
            pks: Dict,
            fks: Dict,
            tbl_constraints: Dict,
        ):
            table_name = ddl.this.this.name
            if table_name not in deps:
                deps[table_name] = 0
            table_mapping = maps.setdefault(table_name, {})
            constraints = tbl_constraints.setdefault(table_name, {})
            for node in ddl.dfs():
                if isinstance(node, exp.ColumnDef):
                    table_mapping[node.name] = node.kind.sql(dialect=dialect)
                    constraints.setdefault(node.name, set()).update(node.constraints)
                elif isinstance(node, exp.PrimaryKey):
                    pks.setdefault(table_name, set()).update(node.expressions)
                elif isinstance(node, exp.ForeignKey):
                    ref_table = node.args.get("reference").find(exp.Table).name
                    deps[ref_table] = deps.get(ref_table, 0) + 1
                    fks.setdefault(table_name, []).append(node)

        parsed_ddls = parse(ddls, dialect=dialect)
        mappings = {}
        primary_keys: Dict[str, Set[exp.Identifier]] = {}
        foreign_keys: Dict[str, List[exp.ForeignKey]] = {}
        for stmt_expr in parsed_ddls:
            _build(
                ddl=stmt_expr.this,
                maps=mappings,
                deps=dependency,
                pks=primary_keys,
                fks=foreign_keys,
                tbl_constraints=table_constraints,
            )
        sorted_table = OrderedDict(
            {
                table_name: mappings[table_name]
                for table_name, _ in sorted(
                    dependency.items(),
                    key=lambda item: item[1],
                    reverse=True,
                )
            }
        )
        for table_name, table_columns in sorted_table.items():
            self.add_table(table_name, table_columns, dialect=dialect)
            self.add_primary_key(table_name, primary_keys.get(table_name, set()))
            self.add_foreign_key(table_name, foreign_keys.get(table_name, []))
            for column in table_columns:
                if column in table_constraints.get(table_name, {}):
                    self.add_constraint(
                        table_name,
                        column,
                        table_constraints[table_name][column],
                    )
    def __repr__(self):
        return f"Instance(name={self.name}, tables={list(self.tables.keys())})"

    def add_row(self, table_name: str, row: Row):
        table_name = self._normalize_table(table_name, dialect=self.dialect)
        self.data[table_name].append(row)

    def get_rows(self, table_name) -> List[Row]:
        table_name = self._normalize_table(table_name, dialect=self.dialect)
        return self.data[table_name]

    def get_row(self, table_name, index):
        return self.get_rows(table_name)[index]

    def get_column_data(self, table_name, column_name) -> List[Symbol]:
        column_name = self._normalize_name(column_name, dialect=self.dialect)
        return [row[column_name] for row in self.get_rows(table_name)]

    def create_rows(
        self, concretes: Dict[str, Dict[str, List[Any]]], sync_db: bool = False
    ) -> Dict[str, List[RowCreationResult]]:
        del sync_db
        created = {}
        normalized_concretes = {}
        for table_name, table_data in concretes.items():
            if not table_name:
                continue
            normalized_table = self._normalize_table(table_name, dialect=self.dialect)
            for column_name, values in table_data.items():
                normalized_column = self._normalize_name(column_name, dialect=self.dialect)
                normalized_concretes.setdefault(normalized_table, {})[
                    normalized_column
                ] = values
        for table_name in self.tables:
            normalized_table = self._normalize_table(table_name, dialect=self.dialect)
            if normalized_table not in normalized_concretes:
                continue
            table_data = normalized_concretes[normalized_table]
            num_rows = max(len(v) for v in table_data.values()) if table_data else 1
            created[normalized_table] = []
            for index in range(num_rows):
                row_values = {
                    column: values[index]
                    for column, values in table_data.items()
                    if index < len(values)
                }
                created[normalized_table].append(
                    self.create_row(table_name=normalized_table, values=row_values)
                )
        return created

    def create_row(
        self,
        table_name: str,
        values: Dict[str, Any] | None = None,
        alias: Optional[str] = None,
        sync_db: bool = False,
    ) -> RowCreationResult:
        del sync_db
        table_name = self._normalize_name(table_name, dialect=self.dialect)
        values = values or {}
        provided_columns = {
            self._normalize_name(column, dialect=self.dialect) for column in values
        }
        new_tuples = defaultdict(list)
        positions: Dict[str, int] = {}
        self._merge_created_rows(
            new_tuples,
            self._bootstrap_reference_rows(
                table_name,
                values,
                locked_columns=provided_columns,
            ),
        )
        self._merge_created_rows(
            new_tuples,
            self._resolve_composite_reference_conflicts(
                table_name,
                values,
                locked_columns=provided_columns,
            ),
        )
        try:
            main_pos = self._create_row(table_name, values, alias=alias)
        except UniqueConflictError:
            created = self._bootstrap_reference_rows(
                table_name,
                values,
                prefer_new_for_unique=True,
                locked_columns=provided_columns,
            )
            if not created:
                raise
            self._merge_created_rows(new_tuples, created)
            self._merge_created_rows(
                new_tuples,
                self._resolve_composite_reference_conflicts(
                    table_name,
                    values,
                    locked_columns=provided_columns,
                ),
            )
            main_pos = self._create_row(table_name, values, alias=alias)
        new_tuples[table_name].append(self.get_row(table_name, main_pos))
        positions[table_name] = main_pos
        return RowCreationResult(
            created={table: tuple(rows) for table, rows in new_tuples.items()},
            positions=positions,
        )

    def _create_row(
        self,
        table_name: str,
        concretes: Dict[str, Any],
        alias: Optional[str] = None,
    ):
        del alias
        table_name = self._normalize_name(table_name, dialect=self.dialect, is_table=True)
        if table_name not in self.tables:
            return None
        tuple_index = len(self.get_rows(table_name))
        concretes = {self._normalize_name(k): v for k, v in concretes.items()}

        existing_index = self._find_existing_row(table_name, concretes)
        if existing_index is not None:
            return existing_index
        conflict_index = self._find_conflicting_unique_row(table_name, concretes)
        if conflict_index is not None:
            return conflict_index

        for _ in range(100):
            try:
                completed = self.builder.complete_row(
                    table_name,
                    preset_values=concretes,
                    persist=False,
                )
            except (UniqueConflictError, ForeignKeyResolutionError):
                raise
            new_values = {}
            for column, datatype in self.tables[table_name].items():
                z_name = normalize_name(f"{table_name}_{column}_{datatype}_{tuple_index}")
                concrete = completed.get(column)
                z_value = Variable(this=z_name, _type=datatype, concrete=concrete)
                z_value.type = datatype
                new_values[column] = z_value
                self.symbols[z_name] = z_value
                self.symbol_to_table[z_name] = (table_name, column)
            if self._row_violates_unique_constraints(table_name, new_values):
                continue
            rowid = f"{table_name}_rowid_{tuple_index}"
            self.add_row(table_name, Row(this=rowid, columns=new_values))
            self.builder.runtime.remember_row(
                table_name,
                {column: value.concrete for column, value in new_values.items()},
            )
            return tuple_index
        raise_exception(f"Failed to create row for table {table_name} after 100 attempts")

    def _bootstrap_reference_rows(
        self,
        table_name: str,
        values: Dict[str, Any],
        prefer_new_for_unique: bool = False,
        locked_columns: Optional[set[str]] = None,
    ) -> dict[str, list[Row]]:
        created_rows: dict[str, list[Row]] = defaultdict(list)
        locked_columns = locked_columns or set()
        normalized_values = {
            self._normalize_name(key, dialect=self.dialect): value
            for key, value in values.items()
        }
        values.clear()
        values.update(normalized_values)

        for fk in self.get_foreign_key(table_name):
            local_col = self._normalize_name(fk.expressions[0].name, dialect=self.dialect)
            ref_table = self._normalize_table(
                fk.args.get("reference").find(exp.Table).name,
                dialect=self.dialect,
            )
            ref_col = self._normalize_name(
                fk.args.get("reference").this.expressions[0].name,
                dialect=self.dialect,
            )

            explicit_value = values.get(local_col)
            existing_parent_values = [
                symbol.concrete for symbol in self.get_column_data(ref_table, ref_col)
            ]
            used_child_values = {
                symbol.concrete for symbol in self.get_column_data(table_name, local_col)
            }

            if explicit_value is not None:
                if (
                    prefer_new_for_unique
                    and local_col not in locked_columns
                    and self.is_unique(table_name, local_col)
                    and explicit_value in used_child_values
                ):
                    ref_position = self._create_row(ref_table, {}, alias=None)
                    ref_value = self.get_column_data(ref_table, ref_col)[ref_position]
                    values[local_col] = ref_value.concrete
                    created_rows[ref_table].append(self.get_row(ref_table, ref_position))
                    continue
                if explicit_value not in existing_parent_values:
                    ref_position = self._create_row(
                        ref_table,
                        {ref_col: explicit_value},
                        alias=None,
                    )
                    created_rows[ref_table].append(self.get_row(ref_table, ref_position))
                continue

            should_force_new_parent = (
                prefer_new_for_unique
                and self.is_unique(table_name, local_col)
                and bool(existing_parent_values)
            )
            if not should_force_new_parent:
                available_values = [
                    value
                    for value in existing_parent_values
                    if not (
                        self.is_unique(table_name, local_col) and value in used_child_values
                    )
                ]
                if available_values:
                    values[local_col] = random.choice(available_values)
                    continue

            ref_position = self._create_row(ref_table, {}, alias=None)
            ref_value = self.get_column_data(ref_table, ref_col)[ref_position]
            values[local_col] = ref_value.concrete
            created_rows[ref_table].append(self.get_row(ref_table, ref_position))

        return created_rows

    def _merge_created_rows(
        self,
        target: dict[str, list[Row]],
        created: dict[str, list[Row]],
    ) -> None:
        for table_name, rows in created.items():
            target[table_name].extend(rows)

    def _find_conflicting_unique_row(
        self, table_name: str, concretes: Dict[str, Any]
    ) -> Optional[int]:
        for column, concrete in concretes.items():
            if concrete is None or not self.is_unique(table_name, column):
                continue
            for idx, symbol in enumerate(self.get_column_data(table_name, column)):
                if symbol.concrete == concrete:
                    return idx
        return None

    def _find_existing_row(
        self, table_name: str, concretes: Dict[str, Any]
    ) -> Optional[int]:
        grouped_index = self._find_existing_row_for_constraint_groups(table_name, concretes)
        if grouped_index is not None:
            return grouped_index
        unique_columns = [
            column
            for column in concretes
            if column in self.tables[table_name] and self.is_unique(table_name, column)
        ]
        if not unique_columns:
            return None
        candidate_indexes = None
        for column in unique_columns:
            matching_indexes = {
                idx
                for idx, symbol in enumerate(self.get_column_data(table_name, column))
                if symbol.concrete == concretes[column]
            }
            if not matching_indexes:
                return None
            candidate_indexes = (
                matching_indexes
                if candidate_indexes is None
                else candidate_indexes & matching_indexes
            )
            if not candidate_indexes:
                return None
        for idx in sorted(candidate_indexes):
            row = self.get_row(table_name, idx)
            if all(row[column].concrete == concrete for column, concrete in concretes.items()):
                return idx
        return None

    def _find_existing_row_for_constraint_groups(
        self,
        table_name: str,
        concretes: Dict[str, Any],
    ) -> Optional[int]:
        for columns in self._constraint_groups(table_name):
            if not all(column in concretes for column in columns):
                continue
            target = tuple(concretes[column] for column in columns)
            for idx, row in enumerate(self.get_rows(table_name)):
                candidate = tuple(row[column].concrete for column in columns)
                if candidate == target:
                    return idx
        return None

    def _row_violates_unique_constraints(
        self, table_name: str, row_values: Dict[str, Variable]
    ) -> bool:
        for columns in self._constraint_groups(table_name):
            concretes = tuple(row_values[column].concrete for column in columns)
            if any(value is None for value in concretes):
                continue
            for existing_row in self.get_rows(table_name):
                existing = tuple(existing_row[column].concrete for column in columns)
                if existing == concretes:
                    return True
        unique_columns = [
            column_name
            for column_name in self.tables[table_name]
            if self.is_unique(table_name, column_name)
        ]
        for column in unique_columns:
            concrete = row_values[column].concrete
            if concrete is None:
                continue
            for existing in self.get_column_data(table_name, column):
                if existing.concrete == concrete:
                    return True
        return False

    def _constraint_groups(self, table_name: str) -> list[tuple[str, ...]]:
        table = self.schema_spec.get_table(table_name)
        groups: list[tuple[str, ...]] = []
        if len(table.primary_key) > 1:
            groups.append(tuple(column.lower() for column in table.primary_key))
        for columns in table.unique_constraints:
            if len(columns) > 1:
                groups.append(tuple(column.lower() for column in columns))
        return groups

    def _resolve_composite_reference_conflicts(
        self,
        table_name: str,
        values: Dict[str, Any],
        locked_columns: Optional[set[str]] = None,
    ) -> dict[str, list[Row]]:
        created_rows: dict[str, list[Row]] = defaultdict(list)
        locked_columns = locked_columns or set()
        fk_map = self._foreign_key_map(table_name)

        for _ in range(20):
            duplicate_group = None
            for columns in self._constraint_groups(table_name):
                if not all(column in values for column in columns):
                    continue
                target = tuple(values[column] for column in columns)
                if any(value is None for value in target):
                    continue
                if any(
                    tuple(row[column].concrete for column in columns) == target
                    for row in self.get_rows(table_name)
                ):
                    duplicate_group = columns
                    break
            if duplicate_group is None:
                return created_rows

            progress = False
            for column in duplicate_group:
                if column in locked_columns:
                    continue
                fk_target = fk_map.get(column)
                if fk_target is None:
                    continue
                ref_table, ref_col = fk_target
                ref_position = self._create_row(ref_table, {}, alias=None)
                ref_value = self.get_column_data(ref_table, ref_col)[ref_position].concrete
                values[column] = ref_value
                created_rows[ref_table].append(self.get_row(ref_table, ref_position))
                progress = True
                break
            if not progress:
                return created_rows

        return created_rows

    def _foreign_key_map(self, table_name: str) -> dict[str, tuple[str, str]]:
        mapping: dict[str, tuple[str, str]] = {}
        for fk in self.get_foreign_key(table_name):
            local_col = self._normalize_name(fk.expressions[0].name, dialect=self.dialect)
            ref_table = self._normalize_table(
                fk.args.get("reference").find(exp.Table).name,
                dialect=self.dialect,
            )
            ref_col = self._normalize_name(
                fk.args.get("reference").this.expressions[0].name,
                dialect=self.dialect,
            )
            mapping[local_col] = (ref_table, ref_col)
        return mapping

    def reset(self):
        self.data.clear()
        self.symbols.clear()
        self.symbol_to_table.clear()
        self.symbol_to_tuple_id.clear()
        self.tuple_id_to_symbols.clear()
        self.pk_fk_symbols.clear()
        self.builder = DatabaseBuilder(self.schema_spec)
        self._build_catalog(self.ddls, self.dialect)

    def snapshot(self) -> InstanceSnapshot:
        tables: list[TableBatch] = []
        for table_name in self.tables:
            rows = self._row_dicts(table_name)
            columns = tuple(self.column_names(table_name))
            tables.append(
                TableBatch(
                    table_name=table_name,
                    columns=columns,
                    rows=tuple(
                        {column: row.get(column) for column in columns} for row in rows
                    ),
                )
            )
        return InstanceSnapshot(
            schema_ddl=self.ddls,
            dialect=self.dialect,
            tables=tuple(tables),
        )

    def _row_dicts(self, table_name: str) -> list[dict[str, Any]]:
        rows = []
        for row in self.get_rows(table_name):
            rows.append(
                {column_name: symbol.concrete for column_name, symbol in row.items()}
            )
        return rows

    def to_db(
        self,
        connection_string: str,
        dialect: str,
        truncate_first: bool = True,
        return_inserted: bool = False,
    ):
        snapshot = self.snapshot()
        target = DatabaseTarget(
            connection_string=connection_string,
            dialect=dialect,
        )
        result = InstanceLoader().load(
            snapshot=snapshot,
            target=target,
            truncate_first=truncate_first,
        )
        if return_inserted:
            return "\n".join(InstanceExporter().render_sql(snapshot))
        return result
