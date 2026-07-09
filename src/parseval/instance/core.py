from __future__ import annotations

from collections import OrderedDict, defaultdict
from functools import cached_property
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, TYPE_CHECKING

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

from parseval.coercion import coerce_literal_value, coerce_value, storage_key
from parseval.domain import DatabaseBuilder
from parseval.domain.exceptions import (
    ConstraintViolationError,
    ForeignKeyResolutionError,
    UniqueConflictError,
)
from parseval.dtype import DataType, TypeFamily, type_family
from parseval.identity import (
    CatalogColumn,
    ColumnId,
    ColumnKind,
    RelationId,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
from parseval.plan.rex import Environment, Row, Symbol, Variable, concrete
from parseval.states import raise_exception

from .exporter import InstanceExporter
from .constraints import DatabaseCheckConstraint, DatabaseConstraints
from .loader import InstanceLoader
from .serialization import InstanceValueSerializer
from .symbols import SymbolIndex
from .types import (
    DatabaseTarget,
    InstanceSnapshot,
    RowCreationResult,
    TableBatch,
)

if TYPE_CHECKING:
    from parseval.domain import SchemaSpec


_BOOTSTRAP_MISSING = object()


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
        self.unique_constraints = {}
        self.table_check_constraints = {}
        self._relation_ids: Dict[str, RelationId] = {}
        self._column_ids: Dict[Tuple[str, str], ColumnId] = {}
        self._relation_keys_by_id: Dict[RelationId, str] = {}
        self._column_keys_by_id: Dict[ColumnId, Tuple[str, str]] = {}
        self._catalog_columns: Dict[ColumnId, CatalogColumn] = {}
        self._constraints_by_column_id: Dict[ColumnId, set] = {}
        self._primary_key_ids_by_relation_id: Dict[RelationId, Tuple[ColumnId, ...]] = {}
        self._unique_constraint_ids_by_relation_id: Dict[
            RelationId,
            Tuple[Tuple[ColumnId, ...], ...],
        ] = {}
        self._foreign_keys_by_relation_id: Dict[RelationId, Tuple[Any, ...]] = {}
        self._database_constraints_by_relation_id: Dict[
            RelationId,
            DatabaseConstraints,
        ] = {}
        self._table_sources: Dict[str, exp.Table | str] = {}
        self._column_sources: Dict[Tuple[str, str], exp.Identifier | str] = {}
        self._ddl_columns: Dict[str, Dict[str, str]] = {}
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

    def _identifier_key(self, value: exp.Column | exp.Identifier | str) -> str:
        if isinstance(value, exp.Column):
            value = value.this
        if isinstance(value, exp.Identifier) and value.quoted:
            return identifier_name(value, dialect=self.dialect).normalized
        return self._normalize_name(
            value.name if isinstance(value, exp.Identifier) else str(value),
            dialect=self.dialect,
            normalize=self.normalize,
        )

    def _identity_key(
        self,
        value: exp.Column | exp.Identifier | exp.Table | str,
        *,
        is_table: bool = False,
    ) -> str:
        if isinstance(value, exp.Table):
            parts = [
                self._identifier_key(part)
                for part in (value.args.get("catalog"), value.args.get("db"), value.this)
                if part is not None
            ]
            return ".".join(parts)
        if is_table and isinstance(value, str) and ("." in value or '"' in value):
            return self._identity_key(exp.to_table(value), is_table=True)
        return self._identifier_key(value)

    def _resolve_declared_key(self, key: str, candidates) -> str:
        if key in candidates or not self.normalize:
            return key
        normalized = self._normalize_name(
            key,
            dialect=self.dialect,
            normalize=True,
        )
        matches = [
            candidate
            for candidate in candidates
            if self._normalize_name(
                candidate,
                dialect=self.dialect,
                normalize=True,
            )
            == normalized
        ]
        return matches[0] if len(matches) == 1 else key

    def _resolve_declared_table_key(self, table: exp.Table | str) -> str:
        key = self._identity_key(table, is_table=True)
        candidates = self._ddl_columns or self.tables
        return self._resolve_declared_key(key, candidates)

    def _resolve_declared_column_key(
        self,
        table_key: str,
        column: exp.Identifier | str,
    ) -> str:
        column_key = self._identity_key(column)
        columns = self._ddl_columns.get(table_key)
        if columns is None:
            table_columns = self.tables.get(table_key, {})
            columns = table_columns if isinstance(table_columns, dict) else {}
        return self._resolve_declared_key(column_key, columns)

    def _relation_identity_from_source(self, table: exp.Table | str) -> Any:
        if isinstance(table, exp.Table):
            return relation_id(
                RelationKind.TABLE,
                identifier_name(table.this, dialect=self.dialect),
                catalog=(
                    identifier_name(table.args["catalog"], dialect=self.dialect)
                    if table.args.get("catalog") is not None
                    else None
                ),
                db=(
                    identifier_name(table.args["db"], dialect=self.dialect)
                    if table.args.get("db") is not None
                    else None
                ),
            )
        return relation_id(
            RelationKind.TABLE,
            identifier_name(table, dialect=self.dialect),
        )

    def _remember_table_identity(
        self,
        table: exp.Table | str,
        *,
        table_key: str | None = None,
    ) -> None:
        key = table_key or self._identity_key(table, is_table=True)
        if key not in self._relation_ids:
            self._relation_ids[key] = self._relation_identity_from_source(table)
        self._relation_keys_by_id[self._relation_ids[key]] = key

    def _remember_column_identity(
        self,
        table: exp.Table | str,
        column: exp.Identifier | str,
        datatype_sql: str,
        *,
        table_key: str | None = None,
        column_key: str | None = None,
    ) -> None:
        table_key = table_key or self._identity_key(table, is_table=True)
        column_key = column_key or self._identity_key(column)
        self._remember_table_identity(table, table_key=table_key)
        rel_id = self._relation_ids[table_key]
        col_id = column_id(
            ColumnKind.PHYSICAL,
            identifier_name(column, dialect=self.dialect),
            rel_id,
        )
        self._column_ids[(table_key, column_key)] = col_id
        self._column_keys_by_id[col_id] = (table_key, column_key)
        datatype = self._datatype_node_for(column_key, datatype_sql)
        raw_constraints = self.constraints.get(table_key, {}).get(column_key, set())
        inline_primary_key = any(
            isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
            for constraint in raw_constraints
        )
        primary_key = inline_primary_key or column_key in self._primary_key_names(table)
        self._catalog_columns[col_id] = CatalogColumn(
            id=col_id,
            datatype=datatype,
            nullable=self.nullable(table, column) and not primary_key,
            unique=self.is_unique(table, column),
            primary_key=primary_key,
        )

    def _rebuild_identity_indexes(self) -> None:
        self._relation_ids.clear()
        self._column_ids.clear()
        self._relation_keys_by_id.clear()
        self._column_keys_by_id.clear()
        self._catalog_columns.clear()
        self._constraints_by_column_id.clear()
        self._primary_key_ids_by_relation_id.clear()
        self._unique_constraint_ids_by_relation_id.clear()
        self._foreign_keys_by_relation_id.clear()
        self._database_constraints_by_relation_id.clear()
        table_columns = self._ddl_columns or self.tables
        for table_key, columns in table_columns.items():
            table_source = self._table_sources.get(table_key, table_key)
            self._remember_table_identity(table_source, table_key=table_key)
            for column_key, datatype_sql in columns.items():
                column_source = self._column_sources.get(
                    (table_key, column_key),
                    column_key,
                )
                self._remember_column_identity(
                    table_source,
                    column_source,
                    datatype_sql,
                    table_key=table_key,
                    column_key=column_key,
                )
        self._rebuild_constraint_identity_indexes()
        self._refresh_catalog_column_metadata()

    def _rebuild_constraint_identity_indexes(self) -> None:
        table_columns = self._ddl_columns or self.tables
        for table_key, columns in table_columns.items():
            table_source = self._table_sources.get(table_key, table_key)
            rel_id = self.table_id(table_source)

            pk_ids = tuple(
                self._column_id_for_declared_key(table_key, table_source, column)
                for column in self.primary_keys.get(table_key, ())
            )
            self._primary_key_ids_by_relation_id[rel_id] = pk_ids

            unique_ids = []
            for unique_columns in self.unique_constraints.get(table_key, ()):
                unique_ids.append(
                    tuple(
                        self._column_id_for_declared_key(
                            table_key,
                            table_source,
                            column,
                        )
                        for column in unique_columns
                    )
                )
            self._unique_constraint_ids_by_relation_id[rel_id] = tuple(unique_ids)

            for column_key, constraints in self.constraints.get(table_key, {}).items():
                col_id = self.column_id(
                    table_source,
                    self._column_sources.get((table_key, column_key), column_key),
                )
                self._constraints_by_column_id.setdefault(col_id, set()).update(
                    constraints
                )

        for table_key in table_columns:
            table_source = self._table_sources.get(table_key, table_key)
            rel_id = self.table_id(table_source)
            fk_specs = []
            for fk in self.foreign_keys.get(table_key, ()):
                fk_spec = self._foreign_key_spec_for_node(table_key, table_source, fk)
                if fk_spec is not None:
                    fk_specs.append(fk_spec)
            self._foreign_keys_by_relation_id[rel_id] = tuple(fk_specs)
            self._database_constraints_by_relation_id[rel_id] = (
                self._database_constraints_for_table_key(
                    table_key,
                    table_source,
                    rel_id,
                    tuple(fk_specs),
                )
            )

    def _database_constraints_for_table_key(
        self,
        table_key: str,
        table_source: exp.Table | str,
        rel_id: RelationId,
        fk_specs: Tuple[Any, ...],
    ) -> DatabaseConstraints:
        not_null_columns = []
        inline_checks = []
        for column_key, constraints in self.constraints.get(table_key, {}).items():
            column_id = self.column_id(
                table_source,
                self._column_sources.get((table_key, column_key), column_key),
            )
            for constraint in constraints:
                if isinstance(constraint.kind, exp.NotNullColumnConstraint):
                    not_null_columns.append(column_id)
                if isinstance(constraint.kind, exp.CheckColumnConstraint):
                    inline_checks.append(
                        self._database_check_constraint(
                            table_key,
                            table_source,
                            rel_id,
                            constraint.kind.this,
                            origin="inline",
                        )
                    )

        table_checks = [
            self._database_check_constraint(
                table_key,
                table_source,
                rel_id,
                check_expr,
                origin="table",
            )
            for check_expr in self.table_check_constraints.get(table_key, ())
        ]

        return DatabaseConstraints(
            relation=rel_id,
            not_null_columns=tuple(not_null_columns),
            primary_key=self._primary_key_ids_by_relation_id.get(rel_id, ()),
            unique_constraints=self._unique_constraint_ids_by_relation_id.get(
                rel_id,
                (),
            ),
            foreign_keys=fk_specs,
            checks=tuple(table_checks + inline_checks),
        )

    def _database_check_constraint(
        self,
        table_key: str,
        table_source: exp.Table | str,
        rel_id: RelationId,
        expression: exp.Expression,
        *,
        origin: str,
    ) -> DatabaseCheckConstraint:
        referenced_columns = []
        supported = True
        reason = None
        if expression.find(exp.Subquery):
            supported = False
            reason = "subquery"
        for col in expression.find_all(exp.Column):
            if col.table:
                col_table_key = self._resolve_declared_table_key(col.table)
                if col_table_key != table_key:
                    supported = False
                    reason = "cross_relation"
                    continue
            column_key = self._resolve_declared_column_key(table_key, col.this)
            referenced_columns.append(
                self.column_id(
                    table_source,
                    self._column_sources.get((table_key, column_key), column_key),
                )
            )
        return DatabaseCheckConstraint(
            relation=rel_id,
            expression=expression,
            referenced_columns=tuple(dict.fromkeys(referenced_columns)),
            origin=origin,
            supported=supported,
            reason=reason,
        )

    def _refresh_catalog_column_metadata(self) -> None:
        table_columns = self._ddl_columns or self.tables
        for table_key, columns in table_columns.items():
            table_source = self._table_sources.get(table_key, table_key)
            rel_id = self.table_id(table_source)
            pk_ids = set(self.get_primary_key_ids(rel_id))
            for column_key, datatype_sql in columns.items():
                column_source = self._column_sources.get(
                    (table_key, column_key),
                    column_key,
                )
                col_id = self.column_id(table_source, column_source)
                datatype = self._datatype_node_for(column_key, datatype_sql)
                self._catalog_columns[col_id] = CatalogColumn(
                    id=col_id,
                    datatype=datatype,
                    nullable=self.nullable(rel_id, col_id) and col_id not in pk_ids,
                    unique=self.is_unique(rel_id, col_id),
                    primary_key=col_id in pk_ids,
                )

    def _foreign_key_spec_for_node(
        self,
        table_key: str,
        table_source: exp.Table | str,
        fk_node: exp.ForeignKey,
    ):
        from parseval.domain import ForeignKeySpec

        reference = fk_node.args.get("reference")
        if reference is None:
            return None
        target_table = reference.find(exp.Table)
        if target_table is None:
            return None
        source_columns = tuple(
            self._resolve_declared_column_key(table_key, identifier)
            for identifier in fk_node.expressions
        )
        target_table_key = self._resolve_declared_table_key(target_table)
        target_columns = tuple(
            self._resolve_declared_column_key(target_table_key, identifier)
            for identifier in reference.this.expressions
        )
        if not target_columns:
            target_columns = self.resolve_fk_ref_columns(fk_node)
        if len(source_columns) != len(target_columns):
            raise ValueError(
                "Foreign key column count does not match referenced columns: "
                f"{table_key}({', '.join(source_columns)}) -> "
                f"{target_table.name}({', '.join(target_columns)})"
            )
        target_table_source = self._table_sources.get(target_table_key, target_table)
        return ForeignKeySpec(
            source_table=table_key,
            source_columns=source_columns,
            target_table=target_table_key,
            target_columns=target_columns,
            source_table_id=self.table_id(table_source),
            source_column_ids=tuple(
                self.column_id(
                    table_source,
                    self._column_sources.get((table_key, column), column),
                )
                for column in source_columns
            ),
            target_table_id=self.table_id(target_table_source),
            target_column_ids=tuple(
                self.column_id(
                    target_table_source,
                    self._column_sources.get((target_table_key, column), column),
                )
                for column in target_columns
            ),
        )

    def _resolve_relation_id(self, table: RelationId | exp.Table | str) -> RelationId:
        return table if isinstance(table, RelationId) else self.table_id(table)

    def _column_id_for_declared_key(
        self,
        table_key: str,
        table_source: exp.Table | str,
        column: exp.Identifier | str,
    ) -> ColumnId:
        column_key = self._resolve_declared_column_key(table_key, column)
        return self.column_id(
            table_source,
            self._column_sources.get((table_key, column_key), column_key),
        )

    def table_id(self, table: RelationId | exp.Table):
        if isinstance(table, RelationId):
            return table
        key = self._identity_key(table, is_table=True)
        if key not in self._relation_ids:
            key = self._resolve_declared_table_key(table)
        return self._relation_ids[key]

    def column_id(
        self,
        table: RelationId | exp.Table,
        column: exp.Column | exp.Identifier,
    ):
        table_key = (
            self._relation_keys_by_id[table]
            if isinstance(table, RelationId)
            else self._resolve_declared_table_key(table)
        )
        column_key = self._identity_key(column)
        try:
            return self._column_ids[(table_key, column_key)]
        except KeyError:
            # Fallback: try the raw key (for quoted identifiers stored with original case).
            raw_key = column.name if isinstance(column, exp.Identifier) else str(column)
            return self._column_ids[(table_key, raw_key)]

    def _column_id_for_metadata_lookup(
        self,
        table: RelationId | exp.Table | str,
        column: ColumnId | exp.Column | exp.Identifier | str,
    ) -> ColumnId:
        if isinstance(column, ColumnId):
            return column
        try:
            return self.column_id(table, column)
        except KeyError:
            table_key = (
                self._relation_keys_by_id[table]
                if isinstance(table, RelationId)
                else self._identity_key(table, is_table=True)
            )
            column_key = self._resolve_declared_column_key(table_key, column)
            return self._column_ids[(table_key, column_key)]

    def catalog_column(
        self,
        table: RelationId | exp.Table,
        column: exp.Column | exp.Identifier,
    ) -> CatalogColumn:
        return self._catalog_columns[self.column_id(table, column)]

    def add_primary_key(
        self, table: RelationId | exp.Table, columns: List[exp.Identifier] | exp.Identifier
    ):
        table = self._identity_key(table, is_table=True)
        pk_columns = self.primary_keys.setdefault(table, [])
        columns = [columns] if isinstance(columns, exp.Identifier) else columns
        seen = {self._identity_key(column) for column in pk_columns}
        for column in columns:
            key = self._identity_key(column)
            if key not in seen:
                pk_columns.append(column)
                seen.add(key)

    def get_primary_key(self, table: RelationId | exp.Table):
        table = self._identity_key(table, is_table=True)
        return tuple(self.primary_keys.get(table, ()))

    def get_primary_key_ids(
        self,
        table: RelationId | exp.Table,
    ) -> Tuple[ColumnId, ...]:
        return self._primary_key_ids_by_relation_id.get(
            self._resolve_relation_id(table),
            (),
        )

    def _primary_key_names(self, table: RelationId | exp.Table) -> Tuple[str, ...]:
        return tuple(
            self._identity_key(identifier)
            for identifier in self.get_primary_key(table)
        )

    def resolve_fk_ref_columns(self, fk: exp.ForeignKey) -> Tuple[str, ...]:
        """Resolve referenced column names from a ForeignKey node.

        When the FK is defined as ``REFERENCES parent_table`` without
        specifying the column (implying the parent's PK), this method
        infers referenced columns from the parent table's primary key or
        column-level PK constraints. Returns an empty tuple if unresolvable.
        """
        ref = fk.args.get("reference")
        if ref is None:
            return ()
        # Explicit referenced column.
        if ref.this.expressions:
            ref_table_node = ref.find(exp.Table)
            if ref_table_node is None:
                return tuple(
                    self._identity_key(identifier)
                    for identifier in ref.this.expressions
                )
            ref_table = self._resolve_declared_table_key(ref_table_node)
            return tuple(
                self._resolve_declared_column_key(ref_table, identifier)
                for identifier in ref.this.expressions
            )
        # Implicit: resolve from parent table's PK.
        ref_table_node = ref.find(exp.Table)
        if ref_table_node is None:
            return ()
        ref_table = self._resolve_declared_table_key(ref_table_node)
        # Check table-level PK first.
        pk_columns = self._primary_key_names(ref_table)
        if pk_columns:
            return pk_columns
        # Check column-level PK constraints.
        for col_name in (self.mapping.get(ref_table) or {}):
            for constraint in self.get_column_constraints(ref_table, col_name):
                if isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint):
                    return (self._identity_key(col_name),)
        return ()

    def resolve_fk_ref_column(self, fk: exp.ForeignKey) -> Optional[str]:
        columns = self.resolve_fk_ref_columns(fk)
        return columns[0] if len(columns) == 1 else None

    def add_foreign_key(
        self, table: RelationId | exp.Table, foreign_key: List[exp.ForeignKey] | exp.ForeignKey
    ):
        table = self._identity_key(table, is_table=True)
        fk_list = self.foreign_keys.setdefault(table, [])
        fks = [foreign_key] if isinstance(foreign_key, exp.ForeignKey) else foreign_key
        fk_list.extend(fks)

    def get_foreign_key(self, table: RelationId | exp.Table):
        table = self._identity_key(table, is_table=True)
        return self.foreign_keys.get(table, [])

    def get_foreign_keys_by_relation_id(self, relation: RelationId):
        return self._foreign_keys_by_relation_id.get(relation, ())

    def add_check_constraint(
        self,
        table: RelationId | exp.Table,
        check: exp.Expression,
    ):
        table = self._identity_key(table, is_table=True)
        self.table_check_constraints.setdefault(table, []).append(check)

    def database_constraints(
        self,
        table: RelationId | exp.Table | str,
    ) -> DatabaseConstraints:
        if isinstance(table, RelationId):
            relation = self.table_id(self._table_key_for_storage(table))
        else:
            relation = self._resolve_relation_id(table)
        constraints = self._database_constraints_by_relation_id.get(relation)
        if constraints is not None:
            return constraints
        return DatabaseConstraints(relation=relation)

    def add_unique_constraint(
        self,
        table: RelationId | exp.Table,
        columns: List[exp.Identifier],
    ):
        table = self._identity_key(table, is_table=True)
        normalized_columns = tuple(
            self._identity_key(column)
            for column in columns
        )
        if not normalized_columns:
            return
        unique_constraints = self.unique_constraints.setdefault(table, [])
        if normalized_columns not in unique_constraints:
            unique_constraints.append(normalized_columns)

    def get_unique_constraints(self, table: RelationId | exp.Table):
        table = self._identity_key(table, is_table=True)
        return tuple(self.unique_constraints.get(table, ()))

    def get_unique_constraint_ids(
        self,
        table: RelationId | exp.Table,
    ) -> Tuple[Tuple[ColumnId, ...], ...]:
        return self._unique_constraint_ids_by_relation_id.get(
            self._resolve_relation_id(table),
            (),
        )

    def add_constraint(
        self,
        table: RelationId | exp.Table,
        column: exp.Column | exp.Identifier,
        constraint,
    ):
        table = self._identity_key(table, is_table=True)
        column = self._identity_key(column)
        table_constraints = self.constraints.setdefault(table, {})
        column_constraints = table_constraints.setdefault(column, set())
        constraints = [constraint] if not isinstance(constraint, (list, set, tuple)) else constraint
        column_constraints.update(constraints)

    def get_column_constraints(
        self,
        table: RelationId | exp.Table,
        column: ColumnId | exp.Column | exp.Identifier,
    ):
        if isinstance(column, ColumnId):
            return self.get_column_constraints_by_id(column)
        if self._constraints_by_column_id:
            return self.get_column_constraints_by_id(
                self._column_id_for_metadata_lookup(table, column)
            )
        table = self._identity_key(table, is_table=True)
        column = self._identity_key(column)
        table_constraints = self.constraints.get(table, {})
        return table_constraints.get(column, set())

    def get_column_constraints_by_id(self, column: ColumnId):
        return self._constraints_by_column_id.get(column, set())

    def get_check_constraints(self, table: RelationId | exp.Table) -> List[exp.Expression]:
        """Return parsed CHECK constraint expressions for a table."""
        return [
            check.expression
            for check in self.database_constraints(table).checks
        ]

    def nullable(
        self,
        table: RelationId | exp.Table,
        column: ColumnId | exp.Column,
    ):
        for constraint in self.get_column_constraints(table, column):
            if isinstance(constraint.kind, exp.NotNullColumnConstraint):
                return constraint.kind.args.get("allow_null", False)
        if isinstance(column, ColumnId):
            return column not in self.get_primary_key_ids(column.relation)
        if self._primary_key_ids_by_relation_id:
            col_id = self._column_id_for_metadata_lookup(table, column)
            return col_id not in self.get_primary_key_ids(col_id.relation)
        column_name = self._identity_key(column)
        if column_name in self._primary_key_names(table):
            return False
        return True

    def is_unique(
        self,
        table: RelationId | exp.Table,
        column: ColumnId | exp.Column,
    ):
        for constraint in self.get_column_constraints(table, column):
            if isinstance(
                constraint.kind,
                (exp.UniqueColumnConstraint, exp.PrimaryKeyColumnConstraint),
            ):
                return True
        if isinstance(column, ColumnId):
            pk_columns = self.get_primary_key_ids(column.relation)
            unique_constraints = self.get_unique_constraint_ids(column.relation)
            return (
                (len(pk_columns) == 1 and column in pk_columns)
                or any(
                    len(unique_columns) == 1 and unique_columns[0] == column
                    for unique_columns in unique_constraints
                )
            )
        if self._unique_constraint_ids_by_relation_id:
            col_id = self._column_id_for_metadata_lookup(table, column)
            return self.is_unique(col_id.relation, col_id)
        pk_columns = self._primary_key_names(table)
        column_name = self._identity_key(column)
        for unique_columns in self.get_unique_constraints(table):
            if len(unique_columns) == 1 and unique_columns[0] == column_name:
                return True
        if len(pk_columns) != 1:
            return False
        return column_name in pk_columns

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_ddls(
        cls,
        ddls: str,
        dialect: str,
        *,
        normalize: bool = True,
    ) -> "Catalog":
        """Build a :class:`Catalog` by parsing ``ddls``.

        This is the single DDL entry point for every schema-aware layer
        in ParSEval. Prior to this, the domain module (``SchemaSpec``)
        and the planner (``Catalog``) each parsed the same DDL through
        their own walkers; they now share this one walk, and anything
        that needs a dataclass view of the schema (the domain module's
        value generators) derives it via :meth:`to_schema_spec`.
        """
        catalog = cls(dialect=dialect, normalize=normalize)
        catalog._ingest_ddls(ddls, dialect)
        return catalog

    def _ingest_ddls(self, ddls: str, dialect: str) -> None:
        """Parse ``ddls`` and populate tables / constraints / keys in place."""
        self._table_sources.clear()
        self._column_sources.clear()
        self._ddl_columns.clear()
        dependency: Dict[str, int] = {}
        table_constraints: Dict[str, Dict[str, set]] = {}
        table_checks: Dict[str, list] = {}

        def _walk(
            ddl: exp.Create,
            maps: Dict[str, Dict[str, str]],
            deps: Dict[str, int],
            pks: Dict[str, list],
            fks: Dict[str, list],
            uniques: Dict[str, list],
            tbl_constraints: Dict[str, Dict[str, set]],
            tbl_checks: Dict[str, list],
        ) -> None:
            schema = ddl.this if isinstance(ddl.this, exp.Schema) else None
            table_node = schema.this if schema is not None else ddl.this
            table_name = self._identity_key(table_node, is_table=True)
            self._table_sources.setdefault(table_name, table_node)
            if table_name not in deps:
                deps[table_name] = 0
            table_mapping = maps.setdefault(table_name, {})
            constraints = tbl_constraints.setdefault(table_name, {})
            ddl_expressions = schema.expressions if schema is not None else ddl.expressions
            for node in ddl_expressions:
                if isinstance(node, exp.ColumnDef):
                    column_name = self._identity_key(node.this)
                    self._column_sources.setdefault((table_name, column_name), node.this)
                    table_mapping[column_name] = node.kind.sql(dialect=dialect)
                    constraints.setdefault(column_name, set()).update(node.constraints)
                    for constraint in node.constraints:
                        if isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint):
                            pks.setdefault(table_name, []).append(node.this)
                        elif isinstance(constraint.kind, exp.UniqueColumnConstraint):
                            uniques.setdefault(table_name, []).append((node.this,))
                    # Capture inline FK references (REFERENCES table(col)).
                    for constraint in node.constraints:
                        if isinstance(constraint.kind, exp.Reference):
                            ref_table = self._identity_key(
                                constraint.kind.find(exp.Table),
                                is_table=True,
                            )
                            deps[ref_table] = deps.get(ref_table, 0) + 1
                            # Build a synthetic ForeignKey node for uniform handling.
                            synthetic_fk = exp.ForeignKey(
                                expressions=[exp.Identifier(this=column_name)],
                                reference=constraint.kind,
                            )
                            fks.setdefault(table_name, []).append(synthetic_fk)
                elif isinstance(node, exp.PrimaryKey):
                    pks.setdefault(table_name, []).extend(node.expressions)
                elif isinstance(node, exp.ForeignKey):
                    ref_table = self._identity_key(
                        node.args.get("reference").find(exp.Table),
                        is_table=True,
                    )
                    deps[ref_table] = deps.get(ref_table, 0) + 1
                    fks.setdefault(table_name, []).append(node)
                elif isinstance(node, exp.UniqueColumnConstraint) and node.this is not None:
                    uniques.setdefault(table_name, []).append(tuple(node.this.expressions))
                elif isinstance(node, exp.CheckColumnConstraint):
                    tbl_checks.setdefault(table_name, []).append(node.this)
                elif isinstance(node, exp.Constraint):
                    for constraint_expr in node.expressions:
                        if isinstance(constraint_expr, exp.PrimaryKey):
                            pks.setdefault(table_name, []).extend(
                                constraint_expr.expressions
                            )
                        elif (
                            isinstance(constraint_expr, exp.UniqueColumnConstraint)
                            and constraint_expr.this is not None
                        ):
                            uniques.setdefault(table_name, []).append(
                                tuple(constraint_expr.this.expressions)
                            )
                        elif isinstance(constraint_expr, exp.CheckColumnConstraint):
                            tbl_checks.setdefault(table_name, []).append(
                                constraint_expr.this
                            )

        parsed_ddls = parse(ddls, dialect=dialect)
        mappings: Dict[str, Dict[str, str]] = {}
        primary_keys: Dict[str, list] = {}
        foreign_keys: Dict[str, list] = {}
        unique_constraints: Dict[str, list] = {}
        for stmt_expr in parsed_ddls:
            _walk(
                ddl=stmt_expr.this,
                maps=mappings,
                deps=dependency,
                pks=primary_keys,
                fks=foreign_keys,
                uniques=unique_constraints,
                tbl_constraints=table_constraints,
                tbl_checks=table_checks,
            )
        self._ddl_columns = OrderedDict(
            (self._resolve_declared_table_key(table_name), OrderedDict(columns))
            for table_name, columns in mappings.items()
        )
        normalized_table_sources = {}
        normalized_column_sources = {}
        normalized_primary_keys: Dict[str, list] = {}
        normalized_foreign_keys: Dict[str, list] = {}
        normalized_unique_constraints: Dict[str, list] = {}
        normalized_table_constraints: Dict[str, Dict[str, set]] = {}
        normalized_table_checks: Dict[str, list] = {}
        for table_name, columns in mappings.items():
            table_key = self._resolve_declared_table_key(table_name)
            if table_name in self._table_sources:
                normalized_table_sources[table_key] = self._table_sources[table_name]
            normalized_primary_keys.setdefault(table_key, []).extend(
                primary_keys.get(table_name, [])
            )
            normalized_foreign_keys.setdefault(table_key, []).extend(
                foreign_keys.get(table_name, [])
            )
            normalized_unique_constraints.setdefault(table_key, []).extend(
                unique_constraints.get(table_name, [])
            )
            for column_name in columns:
                column_key = self._resolve_declared_column_key(table_key, column_name)
                source = self._column_sources.get((table_name, column_name))
                if source is not None:
                    normalized_column_sources[(table_key, column_key)] = source
                constraints = table_constraints.get(table_name, {}).get(column_name)
                if constraints:
                    normalized_table_constraints.setdefault(table_key, {}).setdefault(
                        column_key,
                        set(),
                    ).update(constraints)
        for table_name, pks in primary_keys.items():
            table_key = self._resolve_declared_table_key(table_name)
            normalized_primary_keys.setdefault(table_key, []).extend(pks)
        for table_name, fks in foreign_keys.items():
            table_key = self._resolve_declared_table_key(table_name)
            normalized_foreign_keys.setdefault(table_key, []).extend(fks)
        for table_name, uniques in unique_constraints.items():
            table_key = self._resolve_declared_table_key(table_name)
            normalized_unique_constraints.setdefault(table_key, []).extend(uniques)
        for table_name, checks in table_checks.items():
            table_key = self._resolve_declared_table_key(table_name)
            normalized_table_checks.setdefault(table_key, []).extend(checks)
        for table_name, columns in table_constraints.items():
            table_key = self._resolve_declared_table_key(table_name)
            for column_name, constraints in columns.items():
                column_key = self._resolve_declared_column_key(table_key, column_name)
                normalized_table_constraints.setdefault(table_key, {}).setdefault(
                    column_key,
                    set(),
                ).update(constraints)
        self._table_sources = normalized_table_sources
        self._column_sources = normalized_column_sources
        primary_keys = normalized_primary_keys
        foreign_keys = normalized_foreign_keys
        unique_constraints = normalized_unique_constraints
        table_constraints = normalized_table_constraints
        table_checks = normalized_table_checks
        normalized_dependency: Dict[str, int] = {}
        for table_name, dependency_count in dependency.items():
            table_key = self._resolve_declared_table_key(table_name)
            normalized_dependency[table_key] = max(
                normalized_dependency.get(table_key, 0),
                dependency_count,
            )
        dependency = normalized_dependency

        # Order tables so that FK dependencies are built after their targets.
        sorted_table = OrderedDict(
            {
                table_name: self._ddl_columns[table_name]
                for table_name in sorted(
                    self._ddl_columns,
                    key=lambda key: dependency.get(key, 0),
                    reverse=True,
                )
            }
        )
        for table_name, table_columns in sorted_table.items():
            table_source = self._table_sources.get(table_name, table_name)
            self.add_table(table_name, table_columns, dialect=dialect)
            self.add_primary_key(table_name, primary_keys.get(table_name, []))
            self.add_foreign_key(table_name, foreign_keys.get(table_name, []))
            for unique_columns in unique_constraints.get(table_name, []):
                self.add_unique_constraint(table_name, list(unique_columns))
            for check_expr in table_checks.get(table_name, []):
                self.add_check_constraint(table_name, check_expr)
            for column in table_columns:
                if column in table_constraints.get(table_name, {}):
                    self.add_constraint(
                        table_name,
                        self._column_sources.get((table_name, column), column),
                        table_constraints[table_name][column],
                    )
        self._rebuild_identity_indexes()

    def to_schema_spec(self) -> "SchemaSpec":
        """Derive the domain-module :class:`SchemaSpec` view of this catalog.

        This is the single bridge between the sqlglot-native schema
        representation held by :class:`Catalog` and the dataclass view
        the domain module's value generators expect. Callers that want
        the sqlglot perspective should read ``catalog.tables`` and
        friends directly; callers that want the dataclass view (e.g.
        ``DatabaseBuilder``) go through this derivation.
        """
        # Deferred import avoids a circular dependency at module load time
        # (parseval.domain imports from parseval.instance via tests / helpers).
        from parseval.domain import ColumnSpec, ForeignKeySpec, SchemaSpec, TableSpec

        table_specs: List[TableSpec] = []

        table_columns = self._ddl_columns or self.tables
        for table_name, column_types in table_columns.items():
            table_source = self._table_sources.get(table_name, table_name)
            table_id = self.table_id(table_source)
            pk_column_ids = self.get_primary_key_ids(table_id)
            unique_constraint_ids = self.get_unique_constraint_ids(table_id)
            pk_columns = list(self._primary_key_names(table_source))
            for column_name in column_types:
                column_source = self._column_sources.get(
                    (table_name, column_name),
                    column_name,
                )
                column_id = self.column_id(table_source, column_source)
                raw_constraints = self.get_column_constraints_by_id(column_id)
                if any(
                    isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
                    for constraint in raw_constraints
                ) and column_name not in pk_columns:
                    pk_columns.append(column_name)
            unique_constraints = tuple(self.get_unique_constraints(table_source))
            fk_specs = list(self.get_foreign_keys_by_relation_id(table_id))
            single_column_fk_map: Dict[ColumnId, ForeignKeySpec] = {}
            for fk_spec in fk_specs:
                if len(fk_spec.source_column_ids) == 1:
                    single_column_fk_map[fk_spec.source_column_ids[0]] = fk_spec

            column_specs: List[ColumnSpec] = []
            for column_name, type_sql in column_types.items():
                column_source = self._column_sources.get(
                    (table_name, column_name),
                    column_name,
                )
                column_id = self.column_id(table_source, column_source)
                raw_constraints = self.get_column_constraints_by_id(column_id)
                column_pk = any(
                    isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
                    for constraint in raw_constraints
                )
                column_unique = any(
                    isinstance(constraint.kind, exp.UniqueColumnConstraint)
                    for constraint in raw_constraints
                )
                single_column_unique = any(
                    len(columns) == 1 and columns[0] == column_id
                    for columns in unique_constraint_ids
                )
                nullable = not any(
                    isinstance(constraint.kind, exp.NotNullColumnConstraint)
                    for constraint in raw_constraints
                )
                is_pk = column_pk or column_id in pk_column_ids
                datatype_node = self._datatype_node_for(column_name, type_sql)
                checks = self._domain_checks_for_column(
                    table_id,
                    column_id,
                    datatype_node,
                )
                column_specs.append(
                    ColumnSpec(
                        table=table_name,
                        column=column_name,
                        datatype=datatype_node.copy(),
                        nullable=nullable and not is_pk,
                        unique=column_unique or single_column_unique,
                        primary_key=is_pk,
                        foreign_key=single_column_fk_map.get(column_id),
                        default=None,
                        native_type=type_sql,
                        dialect=self.dialect,
                        length=getattr(datatype_node, "length", None),
                        precision=getattr(datatype_node, "precision", None),
                        scale=getattr(datatype_node, "scale", None),
                        checks=checks,
                        id=column_id,
                        table_id=table_id,
                    )
                )

            table_specs.append(
                TableSpec(
                    name=table_name,
                    columns=tuple(column_specs),
                    primary_key=tuple(pk_columns),
                    unique_constraints=tuple(unique_constraints),
                    foreign_keys=tuple(fk_specs),
                    id=table_id,
                    primary_key_ids=pk_column_ids,
                    unique_constraint_ids=unique_constraint_ids,
                    dialect=self.dialect,
                )
            )

        return SchemaSpec(tables=tuple(table_specs), dialect=self.dialect)

    def _domain_checks_for_column(
        self,
        table_id: RelationId,
        column_id: ColumnId,
        datatype: exp.DataType,
    ) -> Tuple[Any, ...]:
        checks = []
        for check in self.database_constraints(table_id).checks:
            if not check.supported or check.referenced_columns != (column_id,):
                continue
            translated = self._domain_check_from_expression(
                check.expression,
                column_id,
                datatype,
            )
            if translated is not None:
                checks.append(translated)
        return tuple(checks)

    def _domain_check_from_expression(
        self,
        expression: exp.Expression,
        column_id: ColumnId,
        datatype: exp.DataType,
    ) -> Any | None:
        from parseval.domain.constraints import ChoicesConstraint, RangeConstraint

        if isinstance(expression, exp.Between) and self._is_column_ref(
            expression.this,
            column_id,
        ):
            return RangeConstraint(
                minimum=self._domain_literal(expression.args.get("low"), datatype),
                maximum=self._domain_literal(expression.args.get("high"), datatype),
            )
        if isinstance(expression, exp.In) and self._is_column_ref(
            expression.this,
            column_id,
        ):
            values = []
            for literal in expression.expressions:
                if not self._is_literal(literal):
                    return None
                values.append(self._domain_literal(literal, datatype))
            return ChoicesConstraint(values=tuple(values))

        range_types = {
            exp.GT: (None, False, None, True),
            exp.GTE: (None, True, None, True),
            exp.LT: (None, True, None, False),
            exp.LTE: (None, True, None, True),
        }
        for expr_type, (
            minimum,
            minimum_inclusive,
            maximum,
            maximum_inclusive,
        ) in range_types.items():
            if not isinstance(expression, expr_type):
                continue
            lhs = expression.this
            rhs = expression.expression
            if not self._is_column_ref(lhs, column_id) or not self._is_literal(rhs):
                return None
            value = self._domain_literal(rhs, datatype)
            if expr_type in (exp.GT, exp.GTE):
                minimum = value
            else:
                maximum = value
            return RangeConstraint(
                minimum=minimum,
                maximum=maximum,
                minimum_inclusive=minimum_inclusive,
                maximum_inclusive=maximum_inclusive,
            )
        return None

    def _is_literal(self, expression: exp.Expression) -> bool:
        if isinstance(expression, exp.Literal):
            return True
        return isinstance(expression, exp.Neg) and isinstance(expression.this, exp.Literal)

    def _is_column_ref(self, expression: exp.Expression, column_id: ColumnId) -> bool:
        if not isinstance(expression, exp.Column):
            return False
        column_name = identifier_name(expression.name, dialect=self.dialect).normalized
        return column_name == column_id.name.normalized

    def _domain_literal(self, expression: exp.Expression, datatype: exp.DataType) -> Any:
        sign = -1 if isinstance(expression, exp.Neg) else 1
        if isinstance(expression, exp.Neg):
            expression = expression.this
        if isinstance(expression, exp.Literal):
            if expression.is_string:
                value: Any = expression.this
            elif "." in expression.this:
                value = sign * float(expression.this)
            else:
                value = sign * int(expression.this)
        else:
            value = expression
        return coerce_literal_value(value, datatype, dialect=self.dialect)

    @staticmethod
    def _datatype_node_for(column_name: str, type_sql: str) -> exp.DataType:
        """Build a fresh :class:`exp.DataType` node from a stored type SQL string."""
        try:
            return exp.DataType.build(type_sql)
        except Exception:  # pragma: no cover - defensive
            return exp.DataType.build("TEXT")


class Instance(Catalog):
    def __init__(self, ddls: str, name: str, dialect: str, normalize=True):
        super().__init__(dialect=dialect, normalize=normalize)
        self.ddls = ddls
        self.name = name
        self.data: Dict[str, List[Row]] = defaultdict(list)
        self.symbols: SymbolIndex = SymbolIndex()
        self.name_seq = name_sequence(self.name)
        self._bootstrapping: set[RelationId] = set()
        self._bootstrapping_values: Dict[RelationId, Dict[ColumnId, Any]] = {}
        self._bootstrapping_locked_columns: Dict[RelationId, set[ColumnId]] = {}
        self._column_storage_value_cache: Dict[Tuple[str, str, Any], Any] = {}
        self._unique_column_ids_cache: Dict[RelationId, Tuple[ColumnId, ...]] = {}
        self._column_data_cache: Dict[Tuple[str, str], List[Symbol]] = {}
        self._column_value_index_cache: Dict[
            Tuple[str, str],
            Dict[Any, Set[int]],
        ] = {}

        # Parse the DDL exactly once, into the sqlglot-native catalog state
        # this Instance inherits. ``schema_spec`` is a lazy domain-module
        # view over that state (built on first access, cached thereafter).
        self._ingest_ddls(ddls, dialect)
        self.builder = DatabaseBuilder(self.schema_spec)

    @cached_property
    def schema_spec(self) -> "SchemaSpec":
        """Domain-module :class:`SchemaSpec` derived from this Instance's catalog.

        Cached; safe to call repeatedly. Invalidated only if ``self.ddls``
        is replaced (not currently supported).
        """
        return self.to_schema_spec()

    @property
    def catalog(self) -> "Instance":
        return self

    def __repr__(self):
        return f"Instance(name={self.name}, tables={list(self.tables.keys())})"

    def add_row(self, table: RelationId | exp.Table, row: Row):
        table_key = self._table_key_for_storage(table)
        row_index = len(self.data[table_key])
        self.data[table_key].append(row)
        for (cached_table, column_name), values in self._column_data_cache.items():
            if cached_table != table_key:
                continue
            try:
                values.append(row[self._stored_column_id(table, column_name)])
            except KeyError:
                pass
        for (cached_table, column_name), index in self._column_value_index_cache.items():
            if cached_table != table_key:
                continue
            column = self._stored_column_id(table, column_name)
            try:
                storage_value = self._column_storage_value(
                    self._resolve_relation_for_storage(table),
                    column,
                    row[column].concrete,
                )
            except (KeyError, TypeError):
                continue
            index.setdefault(storage_value, set()).add(row_index)

    def get_rows(self, table: RelationId | exp.Table) -> List[Row]:
        table_key = self._table_key_for_storage(table)
        return self.data[table_key]

    def get_row(self, table: RelationId | exp.Table, index):
        return self.get_rows(table)[index]

    def get_column_data(
        self,
        table: RelationId | exp.Table,
        column: ColumnId | exp.Column | exp.Identifier,
    ) -> List[Symbol]:
        table_key = self._table_key_for_storage(table)
        col_id = self._stored_column_id(table, column)
        cache_key = (table_key, col_id.name.normalized)
        if cache_key not in self._column_data_cache:
            self._column_data_cache[cache_key] = [
                row[col_id] for row in self.get_rows(table)
            ]
        return list(self._column_data_cache[cache_key])

    @staticmethod
    def _row_value_dict(row: Row) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for column, symbol in row.items():
            key = column.name.normalized if isinstance(column, ColumnId) else str(column)
            values[key] = symbol.concrete
        return values

    def _table_key_for_storage(self, table: RelationId | exp.Table | str) -> str:
        if isinstance(table, RelationId):
            try:
                return self._relation_keys_by_id[table]
            except KeyError:
                # Fallback: look up by physical table name (ignoring alias).
                if table.name:
                    return table.name.normalized
                raise
        return self._resolve_declared_table_key(table)

    def _table_source_for_storage(self, table: RelationId | exp.Table | str):
        table_key = self._table_key_for_storage(table)
        return self._table_sources.get(table_key, table_key)

    def _table_columns_for_storage(self, table: RelationId | exp.Table | str):
        table_key = self._table_key_for_storage(table)
        return (self._ddl_columns or self.tables)[table_key]

    def _column_key_for_storage(
        self,
        table: RelationId | exp.Table | str,
        column: ColumnId | exp.Column | exp.Identifier | str,
    ) -> str:
        table_key = self._table_key_for_storage(table)
        if isinstance(column, ColumnId):
            column_table_key, column_key = self._column_keys_by_id[column]
            if column_table_key != table_key:
                raise KeyError(f"Column {column.display} does not belong to {table_key}")
            return column_key
        return self._resolve_declared_column_key(table_key, column)

    def _column_source_for_storage(
        self,
        table: RelationId | exp.Table | str,
        column: ColumnId | exp.Column | exp.Identifier | str,
    ):
        table_key = self._table_key_for_storage(table)
        column_key = self._column_key_for_storage(table, column)
        return self._column_sources.get((table_key, column_key), column_key)

    def _stored_column_id(
        self,
        table: RelationId | exp.Table | str,
        column: ColumnId | exp.Column | exp.Identifier | str,
    ):
        if isinstance(column, ColumnId):
            self._column_key_for_storage(table, column)
            return column
        table_key = self._table_key_for_storage(table)
        table_source = self._table_sources.get(table_key, table_key)
        column_source = self._column_source_for_storage(table, column)
        return self.column_id(table_source, column_source)

    def _resolve_relation_for_storage(
        self,
        table: RelationId | exp.Table | str,
    ) -> RelationId:
        return table if isinstance(table, RelationId) else self.table_id(table)

    def _normalize_row_values_by_id(
        self,
        table: RelationId,
        values: Mapping[ColumnId | exp.Column | exp.Identifier | str, Any],
    ) -> Dict[ColumnId, Any]:
        return {
            self._stored_column_id(table, column): value
            for column, value in values.items()
        }

    def _builder_values(self, values: Mapping[ColumnId, Any]) -> Dict[str, Any]:
        return {
            self._column_keys_by_id[column][1]: value
            for column, value in values.items()
        }

    def generate_value(
        self,
        table: RelationId | str,
        column: str | ColumnId,
        row_context: Optional[Mapping[str | ColumnId, Any]] = None,
        null_rate: float = 0.0,
    ) -> Any:
        """Generate a single value for a specific column, respecting constraints.

        This is the public API for value generation.  Callers should use this
        instead of accessing ``self.builder.generate_value`` directly.

        Args:
            table: Table name or relation ID.
            column: Column name or column ID.
            row_context: Optional sibling column values to respect cross-column constraints.
            null_rate: Probability of generating NULL.

        Returns:
            A generated value conforming to the column's domain plan.
        """
        return self.builder.generate_value(
            table, column,
            row_context=row_context,
            null_rate=null_rate,
        )

    def create_rows(
        self,
        concretes: Mapping[
            RelationId | exp.Table,
            Mapping[ColumnId | exp.Column | exp.Identifier | str, Sequence[Any]]
            | Sequence[Mapping[ColumnId | exp.Column | exp.Identifier | str, Any]],
        ],
    ) -> Dict[RelationId, List[RowCreationResult]]:
        created: Dict[RelationId, List[RowCreationResult]] = {}
        normalized_rows: Dict[RelationId, List[Dict[ColumnId, Any]]] = {}
        for table, table_data in concretes.items():
            if not table:
                continue
            relation = self._resolve_relation_for_storage(table)
            normalized_rows[relation] = self._normalize_create_rows_payload(
                relation,
                table_data,
            )
        for relation in self._creation_order(normalized_rows):
            created[relation] = []
            for row_values in normalized_rows[relation]:
                created[relation].append(self.create_row(relation, values=row_values))
        return created

    def _complete_sparse_composite_keys(
        self,
        relation: RelationId,
        rows: List[Dict[ColumnId, Any]],
        *,
        freshen_duplicates: bool,
    ) -> None:
        groups = self._constraint_groups(relation)
        if not groups:
            return
        table_key = self._table_key_for_storage(relation)
        seen: Dict[Tuple[ColumnId, ...], Set[Tuple[Any, ...]]] = {}
        for group in groups:
            group_seen = seen.setdefault(group, set())
            for existing_row in self.get_rows(relation):
                group_seen.add(
                    self._constraint_key(
                        relation,
                        group,
                        {column: existing_row[column].concrete for column in group},
                    )
                )
        for row in rows:
            for group in groups:
                if not any(column in row for column in group):
                    continue
                for column in group:
                    if column in row:
                        continue
                    row[column] = self.builder.generate_value(
                        table_key,
                        column.name.normalized,
                        row_context=self._builder_values(row),
                    )
                current = self._constraint_key(relation, group, row)
                group_seen = seen.setdefault(group, set())
                if current not in group_seen:
                    group_seen.add(current)
                    continue
                if not freshen_duplicates:
                    continue
                target = next((column for column in group if column in row), group[0])
                context = dict(row)
                context.pop(target, None)
                for _attempt in range(16):
                    row[target] = self.builder.generate_value(
                        table_key,
                        target.name.normalized,
                        row_context=self._builder_values(context),
                    )
                    current = self._constraint_key(relation, group, row)
                    if current not in group_seen:
                        group_seen.add(current)
                        break

    def _normalize_create_rows_payload(
        self,
        relation: RelationId,
        table_data: (
            Mapping[ColumnId | exp.Column | exp.Identifier | str, Sequence[Any]]
            | Sequence[Mapping[ColumnId | exp.Column | exp.Identifier | str, Any]]
        ),
    ) -> List[Dict[ColumnId, Any]]:
        if isinstance(table_data, Mapping):
            normalized_columns: Dict[ColumnId, Sequence[Any]] = {}
            for column, values in table_data.items():
                column_id = self._stored_column_id(relation, column)
                normalized_columns[column_id] = values
            num_rows = max(len(v) for v in normalized_columns.values()) if normalized_columns else 1
            return [
                {
                    column: values[index]
                    for column, values in normalized_columns.items()
                    if index < len(values)
                }
                for index in range(num_rows)
            ]
        column_ids_by_name = {
            column_name: self._stored_column_id(relation, column_name)
            for column_name in self._table_columns_for_storage(relation)
        }
        rows: List[Dict[ColumnId, Any]] = []
        for row_values in table_data:
            normalized: Dict[ColumnId, Any] = {}
            for column, value in row_values.items():
                if isinstance(column, str):
                    column_id = column_ids_by_name.get(column)
                    if column_id is None:
                        column_id = self._stored_column_id(relation, column)
                else:
                    column_id = self._stored_column_id(relation, column)
                normalized[column_id] = value
            rows.append(normalized)
        return rows

    def _creation_order(
        self,
        concretes: Mapping[RelationId, Mapping[ColumnId, Sequence[Any]]],
    ) -> List[RelationId]:
        requested = list(concretes.keys())
        requested_set = set(requested)
        visited: Set[RelationId] = set()
        ordered: List[RelationId] = []

        def visit(relation: RelationId) -> None:
            if relation in visited:
                return
            visited.add(relation)
            for fk in self.get_foreign_keys_by_relation_id(relation):
                if fk.target_table_id in requested_set:
                    visit(fk.target_table_id)
            ordered.append(relation)

        for relation in requested:
            visit(relation)
        return ordered

    # ------------------------------------------------------------------
    # Row creation — Level 0 (primitive, unchecked)
    # ------------------------------------------------------------------

    def place_row(
        self,
        table: RelationId | exp.Table,
        values: Mapping[ColumnId | exp.Column | exp.Identifier, Any],
    ) -> Row:
        """Append a row with explicit values. No FK/unique validation.

        Creates a :class:`Variable` for each column, registers it in the
        :class:`SymbolIndex`, and appends the :class:`Row`. This is the
        foundation that :meth:`create_row` builds on; tests and the
        solver use it when they want full control without policy.

        ``values`` must contain an entry for every column in the table.
        Missing columns are filled with ``None`` (SQL NULL).
        """
        relation = self._resolve_relation_for_storage(table)
        table_key = self._table_key_for_storage(relation)
        tuple_index = len(self.get_rows(relation))
        rowid = f"{table_key}_rowid_{tuple_index}"
        values_by_id = self._normalize_row_values_by_id(relation, values)
        table_columns = self._table_columns_for_storage(relation)
        row_cells: Dict[Any, Variable] = {}
        for column, datatype in table_columns.items():
            col_id = self._stored_column_id(relation, column)
            z_name = f"{table_key}_{column}_{datatype}_{tuple_index}"
            concrete = values_by_id.get(col_id)
            z_value = Variable(
                this=z_name,
                _type=datatype,
                concrete=concrete,
                relation_id=relation,
                column_id=col_id,
                rowid=rowid,
            )
            z_value.type = datatype
            row_cells[col_id] = z_value
            self.symbols.register(z_value)
        row = Row(this=rowid, columns=row_cells)
        self.add_row(relation, row)
        return row

    # ------------------------------------------------------------------
    # Row creation — Level 1 (policy-driven, validated)
    # ------------------------------------------------------------------

    def create_row(
        self,
        table: RelationId | exp.Table,
        values: Mapping[ColumnId | exp.Column | exp.Identifier, Any] | None = None,
    ) -> RowCreationResult:
        relation = self._resolve_relation_for_storage(table)
        values_by_id = self._normalize_row_values_by_id(relation, values or {})
        for column, value in values_by_id.items():
            if value is None and not self.nullable(relation, column):
                raise ConstraintViolationError(
                    "explicit_null_for_non_nullable_column:"
                    f"{relation.display}.{column.name.normalized}"
                )
        if self._row_violates_check_constraints(
            relation,
            values_by_id,
            require_complete=True,
        ):
            raise ConstraintViolationError(
                f"check_constraint_failed:{relation.display}"
            )
        provided_columns = set(values_by_id)
        new_tuples: Dict[RelationId, List[Row]] = defaultdict(list)
        positions: Dict[RelationId, int] = {}
        previous_bootstrap_values = self._bootstrapping_values.get(relation)
        previous_locked_columns = self._bootstrapping_locked_columns.get(relation)
        self._bootstrapping.add(relation)
        self._bootstrapping_values[relation] = values_by_id
        self._bootstrapping_locked_columns[relation] = provided_columns
        try:
            self._merge_created_rows(
                new_tuples,
                self._bootstrap_reference_rows(
                    relation,
                    values_by_id,
                    locked_columns=provided_columns,
                ),
            )
            self._merge_created_rows(
                new_tuples,
                self._resolve_composite_reference_conflicts(
                    relation,
                    values_by_id,
                    locked_columns=provided_columns,
                ),
            )
            self._complete_sparse_composite_keys(
                relation,
                [values_by_id],
                freshen_duplicates=False,
            )
            try:
                main_pos = self._create_row(relation, values_by_id)
            except UniqueConflictError:
                created = self._bootstrap_reference_rows(
                    relation,
                    values_by_id,
                    prefer_new_for_unique=True,
                    locked_columns=provided_columns,
                )
                if not created:
                    raise
                self._merge_created_rows(new_tuples, created)
                self._merge_created_rows(
                    new_tuples,
                    self._resolve_composite_reference_conflicts(
                        relation,
                        values_by_id,
                        locked_columns=provided_columns,
                    ),
                )
                main_pos = self._create_row(relation, values_by_id)
        finally:
            self._bootstrapping.discard(relation)
            if previous_bootstrap_values is None:
                self._bootstrapping_values.pop(relation, None)
            else:
                self._bootstrapping_values[relation] = previous_bootstrap_values
            if previous_locked_columns is None:
                self._bootstrapping_locked_columns.pop(relation, None)
            else:
                self._bootstrapping_locked_columns[relation] = previous_locked_columns
        new_tuples[relation].append(self.get_row(relation, main_pos))
        positions[relation] = main_pos
        return RowCreationResult(
            created={table: tuple(rows) for table, rows in new_tuples.items()},
            positions=positions,
        )

    def _create_row(
        self,
        relation: RelationId,
        concretes: Dict[ColumnId, Any],
    ):
        table_key = self._table_key_for_storage(relation)
        if table_key not in (self._ddl_columns or self.tables):
            return None
        tuple_index = len(self.get_rows(relation))

        existing_index = self._find_existing_row(relation, concretes)
        if existing_index is not None:
            return existing_index
        conflict_index = self._find_conflicting_unique_row(relation, concretes)
        if conflict_index is not None:
            return conflict_index
        if self._has_bootstrapping_foreign_key(relation):
            return self._create_row_circular_fk(relation, concretes, tuple_index)

        for _ in range(10):
            try:
                completed = self.builder.complete_row(
                    table_key,
                    preset_values=self._builder_values(concretes),
                    persist=False,
                )
            except UniqueConflictError:
                raise
            except ForeignKeyResolutionError:
                if relation in self._bootstrapping:
                    return self._create_row_circular_fk(relation, concretes, tuple_index)
                raise
            new_values = {}
            rowid = f"{table_key}_rowid_{tuple_index}"
            table_columns = self._table_columns_for_storage(relation)
            for column, datatype in table_columns.items():
                col_id = self._stored_column_id(relation, column)
                z_name = f"{table_key}_{column}_{datatype}_{tuple_index}"
                concrete = completed.get(
                    column,
                    completed.get(self._normalize_name(column, dialect=self.dialect)),
                )
                z_value = Variable(
                    this=z_name,
                    _type=datatype,
                    concrete=concrete,
                    relation_id=relation,
                    column_id=col_id,
                    rowid=rowid,
                )
                z_value.type = datatype
                new_values[col_id] = z_value
                self.symbols.register(z_value)
            if self._row_violates_check_constraints(relation, new_values):
                continue
            if self._row_violates_unique_constraints(relation, new_values):
                continue
            self.add_row(relation, Row(this=rowid, columns=new_values))
            self.builder.runtime.remember_row(
                table_key,
                self._builder_values(
                    {column: value.concrete for column, value in new_values.items()}
                ),
            )
            return tuple_index
        raise_exception(f"Failed to create row for table {table_key} after 10 attempts")

    def _create_row_circular_fk(
        self,
        relation: RelationId,
        concretes: Dict[ColumnId, Any],
        tuple_index: int,
    ) -> int:
        """Create a row bypassing FK validation for circular dependencies.

        Uses place_row with preset values and defaults for FK columns that
        reference tables currently being bootstrapped.
        """
        row_values: Dict[ColumnId, Any] = {}
        table_columns = self._table_columns_for_storage(relation)
        for column, datatype in table_columns.items():
            col_id = self._stored_column_id(relation, column)
            if col_id in concretes:
                row_values[col_id] = concretes[col_id]
            else:
                row_values[col_id] = self._default_for_type(datatype)

        for fk in self.get_foreign_keys_by_relation_id(relation):
            if fk.target_table_id not in self._bootstrapping:
                continue
            nullable_unset = [
                column
                for column in fk.source_column_ids
                if self.nullable(relation, column) and column not in concretes
            ]
            if nullable_unset:
                for column in nullable_unset:
                    row_values[column] = None
                continue
            if any(row_values.get(column) is None for column in fk.source_column_ids):
                continue
            for local_col, target_col in zip(
                fk.source_column_ids,
                fk.target_column_ids,
            ):
                preferred = (
                    row_values[local_col]
                    if local_col in concretes and row_values[local_col] is not None
                    else _BOOTSTRAP_MISSING
                )
                target_value = self._ensure_bootstrapping_value(
                    fk.target_table_id,
                    target_col,
                    preferred=preferred,
                )
                row_values[local_col] = target_value
                if fk.target_table_id == relation:
                    row_values[target_col] = target_value

        locked_columns = set(concretes)
        self._freshen_single_unique_defaults(
            relation,
            row_values,
            locked_columns=locked_columns,
        )
        self._freshen_composite_defaults(
            relation,
            row_values,
            locked_columns=locked_columns,
        )
        if self._row_violates_check_constraints(relation, row_values):
            raise ConstraintViolationError(
                f"check_constraint_failed:{relation.display}"
            )
        row = self.place_row(relation, row_values)
        self.builder.runtime.remember_row(
            self._table_key_for_storage(relation),
            self._row_value_dict(row),
        )
        return tuple_index

    def _freshen_single_unique_defaults(
        self,
        relation: RelationId,
        row_values: Dict[ColumnId, Any],
        locked_columns: Set[ColumnId],
    ) -> None:
        for column_name in self._table_columns_for_storage(relation):
            column = self._stored_column_id(relation, column_name)
            if not self.is_unique(relation, column):
                continue
            value = row_values.get(column)
            if value is None:
                continue
            used_values = {
                symbol.concrete
                for symbol in self.get_column_data(relation, column)
                if symbol.concrete is not None
            }
            if value not in used_values:
                continue
            if column in locked_columns:
                raise UniqueConflictError(
                    f"Duplicate value {value!r} for unique column {column.display}"
                )
            attempt = 1
            while value in used_values:
                value = self._next_default_value(value, attempt)
                attempt += 1
            row_values[column] = value

    def _freshen_composite_defaults(
        self,
        relation: RelationId,
        row_values: Dict[ColumnId, Any],
        locked_columns: Set[ColumnId],
    ) -> None:
        for columns in self._constraint_groups(relation):
            candidates = [column for column in columns if column not in locked_columns]
            if not candidates:
                continue
            attempt = 1
            while self._composite_tuple_exists(relation, columns, row_values):
                column = candidates[-1]
                row_values[column] = self._next_default_value(
                    row_values.get(column),
                    attempt,
                )
                attempt += 1

    def _composite_tuple_exists(
        self,
        relation: RelationId,
        columns: Tuple[ColumnId, ...],
        values: Mapping[ColumnId, Any],
    ) -> bool:
        target = tuple(values.get(column) for column in columns)
        if any(value is None for value in target):
            return False
        for existing_row in self.get_rows(relation):
            existing = tuple(existing_row[column].concrete for column in columns)
            if existing == target:
                return True
        return False

    def _ensure_bootstrapping_value(
        self,
        relation: RelationId,
        column: ColumnId,
        *,
        preferred: Any = _BOOTSTRAP_MISSING,
    ) -> Any:
        active_values = self._bootstrapping_values.get(relation)
        if active_values is None:
            return (
                preferred
                if preferred is not _BOOTSTRAP_MISSING
                else self._fresh_default_for_column(relation, column)
            )
        current = active_values.get(column, _BOOTSTRAP_MISSING)
        if current is not _BOOTSTRAP_MISSING and current is not None:
            if (
                preferred is not _BOOTSTRAP_MISSING
                and preferred is not None
                and current != preferred
            ):
                raise ForeignKeyResolutionError(
                    f"Circular foreign key requires {column.display}={preferred!r}, "
                    f"but the bootstrapped row already has {current!r}"
                )
            return current
        if column in self._bootstrapping_locked_columns.get(relation, set()):
            raise ForeignKeyResolutionError(
                f"Circular foreign key cannot bind explicit NULL for {column.display}"
            )
        value = (
            preferred
            if preferred is not _BOOTSTRAP_MISSING and preferred is not None
            else self._fresh_default_for_column(relation, column)
        )
        active_values[column] = value
        return value

    def _fresh_default_for_column(self, relation: RelationId, column: ColumnId) -> Any:
        column_key = self._column_key_for_storage(relation, column)
        datatype = self._table_columns_for_storage(relation).get(column_key, "TEXT")
        candidate = self._default_for_type(datatype)
        if not self.is_unique(relation, column):
            return candidate
        used_values = {
            symbol.concrete
            for symbol in self.get_column_data(relation, column)
            if symbol.concrete is not None
        }
        attempt = 1
        while candidate in used_values:
            candidate = self._next_default_value(candidate, attempt)
            attempt += 1
        return candidate

    @staticmethod
    def _next_default_value(value: Any, attempt: int) -> Any:
        if isinstance(value, bool):
            return attempt
        if isinstance(value, int):
            return value + 1
        if isinstance(value, float):
            return value + 1.0
        if isinstance(value, datetime):
            return value + timedelta(days=attempt)
        if isinstance(value, date):
            return value + timedelta(days=attempt)
        if isinstance(value, time):
            seconds = (value.hour * 3600 + value.minute * 60 + value.second + attempt) % 86400
            hour, rem = divmod(seconds, 3600)
            minute, second = divmod(rem, 60)
            return time(hour, minute, second)
        return f"{value}_{attempt}"

    @staticmethod
    def _default_for_type(datatype: str) -> Any:
        """Return a sensible default value for a SQL type string."""
        family = type_family(DataType.build(datatype))
        if family == TypeFamily.INTEGER:
            return 1
        if family == TypeFamily.DECIMAL:
            return 1.0
        if family == TypeFamily.BOOLEAN:
            return 0
        if family == TypeFamily.DATE:
            return date(2024, 6, 15)
        if family == TypeFamily.DATETIME:
            return datetime(2024, 6, 15, 0, 0, 0)
        if family == TypeFamily.TIME:
            return time(0, 0, 0)
        return "value"

    def _bootstrap_reference_rows(
        self,
        relation: RelationId,
        values: Dict[ColumnId, Any],
        prefer_new_for_unique: bool = False,
        locked_columns: Optional[set[ColumnId]] = None,
    ) -> dict[RelationId, list[Row]]:
        created_rows: dict[RelationId, list[Row]] = defaultdict(list)
        locked_columns = locked_columns or set()

        for fk in self.get_foreign_keys_by_relation_id(relation):
            if len(fk.source_column_ids) != 1 or len(fk.target_column_ids) != 1:
                self._bootstrap_composite_reference_row(
                    created_rows,
                    fk,
                    values,
                )
                continue
            local_col = fk.source_column_ids[0]
            ref_table = fk.target_table_id
            ref_col = fk.target_column_ids[0]

            if ref_table in self._bootstrapping:
                continue

            explicit_value = values.get(local_col)
            existing_parent_values = [
                symbol.concrete for symbol in self.get_column_data(ref_table, ref_col)
            ]
            used_child_values = {
                symbol.concrete for symbol in self.get_column_data(relation, local_col)
            }
            local_unique = self.is_unique(relation, local_col)
            used_child_storage_values = (
                {
                    self._column_storage_value(relation, local_col, used_value)
                    for used_value in used_child_values
                }
                if local_unique
                else set()
            )

            if explicit_value is not None:
                if (
                    prefer_new_for_unique
                    and local_col not in locked_columns
                    and local_unique
                    and self._column_storage_value(
                        relation, local_col, explicit_value,
                    )
                    in used_child_storage_values
                ):
                    created = self.create_row(ref_table, {})
                    self._merge_created_rows(created_rows, created.created)
                    ref_position = created.positions[ref_table]
                    ref_value = self.get_column_data(ref_table, ref_col)[ref_position]
                    values[local_col] = ref_value.concrete
                    continue
                if not any(
                    self._column_values_equivalent(
                        ref_table, ref_col, explicit_value, parent_value,
                    )
                    for parent_value in existing_parent_values
                ):
                    created = self.create_row(
                        ref_table,
                        {ref_col: explicit_value},
                    )
                    self._merge_created_rows(created_rows, created.created)
                continue

            should_force_new_parent = (
                prefer_new_for_unique
                and local_unique
                and bool(existing_parent_values)
            )
            if not should_force_new_parent:
                available_values = [
                    value
                    for value in existing_parent_values
                    if not (
                        local_unique
                        and self._column_storage_value(relation, local_col, value)
                        in used_child_storage_values
                    )
                ]
                if available_values:
                    values[local_col] = self.builder.runtime.rng.choice(available_values)
                    continue

            created = self.create_row(ref_table, {})
            self._merge_created_rows(created_rows, created.created)
            ref_position = created.positions[ref_table]
            ref_value = self.get_column_data(ref_table, ref_col)[ref_position]
            values[local_col] = ref_value.concrete

        return created_rows

    def _bootstrap_composite_reference_row(
        self,
        created_rows: dict[RelationId, list[Row]],
        fk,
        values: Dict[ColumnId, Any],
    ) -> None:
        ref_table = fk.target_table_id
        if ref_table in self._bootstrapping:
            return

        explicit_values = tuple(values.get(column) for column in fk.source_column_ids)
        existing_parent_rows = self.get_rows(ref_table)
        if all(value is not None for value in explicit_values):
            if any(
                tuple(row[column].concrete for column in fk.target_column_ids)
                == explicit_values
                for row in existing_parent_rows
            ):
                return
            created = self.create_row(
                ref_table,
                dict(zip(fk.target_column_ids, explicit_values)),
            )
            self._merge_created_rows(created_rows, created.created)
            return

        candidates = []
        for row in existing_parent_rows:
            target_values = tuple(row[column].concrete for column in fk.target_column_ids)
            if all(
                explicit is None or explicit == target
                for explicit, target in zip(explicit_values, target_values)
            ):
                candidates.append(target_values)
        if candidates:
            target_values = self.builder.runtime.rng.choice(candidates)
            for source_column, target_value in zip(fk.source_column_ids, target_values):
                values.setdefault(source_column, target_value)
            return

        parent_values = {
            target_column: source_value
            for source_column, target_column, source_value in zip(
                fk.source_column_ids,
                fk.target_column_ids,
                explicit_values,
            )
            if source_value is not None
        }
        created = self.create_row(ref_table, parent_values)
        self._merge_created_rows(created_rows, created.created)
        ref_position = created.positions[ref_table]
        ref_row = self.get_row(ref_table, ref_position)
        for source_column, target_column in zip(
            fk.source_column_ids,
            fk.target_column_ids,
        ):
            values[source_column] = ref_row[target_column].concrete

    def _has_bootstrapping_foreign_key(self, relation: RelationId) -> bool:
        return any(
            fk.target_table_id in self._bootstrapping
            for fk in self.get_foreign_keys_by_relation_id(relation)
        )

    def _merge_created_rows(
        self,
        target: dict[RelationId, list[Row]],
        created: Mapping[RelationId, Sequence[Row]],
    ) -> None:
        for relation, rows in created.items():
            target[relation].extend(rows)

    def _column_storage_value(
        self,
        relation: RelationId,
        column: ColumnId,
        value: Any,
    ) -> Any:
        if isinstance(value, Symbol):
            value = value.concrete
        if value is None:
            return None
        table_key = self._table_key_for_storage(relation)
        column_name = column.name.normalized
        try:
            cache_key = (table_key, column_name, value)
            cached = self._column_storage_value_cache.get(cache_key)
            if cached is not None or cache_key in self._column_storage_value_cache:
                return cached
        except TypeError:
            cache_key = None
        try:
            spec = self.schema_spec.get_table(table_key).get_column(column_name)
            coerced = storage_key(value, spec.datatype, dialect=self.dialect)
        except KeyError:
            coerced = value
        if cache_key is not None:
            self._column_storage_value_cache[cache_key] = coerced
        return coerced

    def _column_value_index(
        self,
        relation: RelationId,
        column: ColumnId,
    ) -> Dict[Any, Set[int]]:
        table_key = self._table_key_for_storage(relation)
        cache_key = (table_key, column.name.normalized)
        if cache_key not in self._column_value_index_cache:
            index: Dict[Any, Set[int]] = {}
            for row_index, row in enumerate(self.get_rows(relation)):
                try:
                    storage_value = self._column_storage_value(
                        relation,
                        column,
                        row[column].concrete,
                    )
                except (KeyError, TypeError):
                    continue
                index.setdefault(storage_value, set()).add(row_index)
            self._column_value_index_cache[cache_key] = index
        return self._column_value_index_cache[cache_key]

    def _column_values_equivalent(
        self,
        relation: RelationId,
        column: ColumnId,
        left: Any,
        right: Any,
    ) -> bool:
        return self._column_storage_value(
            relation, column, left,
        ) == self._column_storage_value(relation, column, right)

    def _constraint_key(
        self,
        relation: RelationId,
        columns: Sequence[ColumnId],
        values: Mapping[ColumnId, Any],
    ) -> tuple[Any, ...]:
        return tuple(
            self._column_storage_value(relation, column, values[column])
            for column in columns
        )

    def _unique_column_ids(self, relation: RelationId) -> Tuple[ColumnId, ...]:
        if relation not in self._unique_column_ids_cache:
            self._unique_column_ids_cache[relation] = tuple(
                self._stored_column_id(relation, column_name)
                for column_name in self._table_columns_for_storage(relation)
                if self.is_unique(relation, self._stored_column_id(relation, column_name))
            )
        return self._unique_column_ids_cache[relation]

    def _find_conflicting_unique_row(
        self, relation: RelationId, concretes: Dict[ColumnId, Any]
    ) -> Optional[int]:
        for column, concrete in concretes.items():
            if concrete is None or not self.is_unique(relation, column):
                continue
            for idx, symbol in enumerate(self.get_column_data(relation, column)):
                if self._column_values_equivalent(
                    relation, column, symbol.concrete, concrete,
                ):
                    return idx
        return None

    def _find_existing_row(
        self, relation: RelationId, concretes: Dict[ColumnId, Any]
    ) -> Optional[int]:
        grouped_index = self._find_existing_row_for_constraint_groups(relation, concretes)
        if grouped_index is not None:
            return grouped_index
        unique_column_set = set(self._unique_column_ids(relation))
        unique_columns = [column for column in concretes if column in unique_column_set]
        if not unique_columns:
            return None
        candidate_indexes = None
        for column in unique_columns:
            storage_value = self._column_storage_value(
                relation,
                column,
                concretes[column],
            )
            matching_indexes = set(
                self._column_value_index(relation, column).get(storage_value, set())
            )
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
            row = self.get_row(relation, idx)
            if all(
                self._column_values_equivalent(
                    relation, column, row[column].concrete, concrete,
                )
                for column, concrete in concretes.items()
            ):
                return idx
        return None

    def _find_existing_row_for_constraint_groups(
        self,
        relation: RelationId,
        concretes: Dict[ColumnId, Any],
    ) -> Optional[int]:
        for column_ids in self._constraint_groups(relation):
            if not all(column in concretes for column in column_ids):
                continue
            target = self._constraint_key(relation, column_ids, concretes)
            for idx, row in enumerate(self.get_rows(relation)):
                candidate = self._constraint_key(
                    relation,
                    column_ids,
                    {column_id: row[column_id].concrete for column_id in column_ids},
                )
                if candidate == target:
                    return idx
        return None

    def _row_violates_unique_constraints(
        self, relation: RelationId, row_values: Dict[ColumnId, Variable]
    ) -> bool:
        for columns in self._constraint_groups(relation):
            concretes = self._constraint_key(relation, columns, row_values)
            if any(value is None for value in concretes):
                continue
            for existing_row in self.get_rows(relation):
                existing = self._constraint_key(
                    relation,
                    columns,
                    {column: existing_row[column].concrete for column in columns},
                )
                if existing == concretes:
                    return True
        for column in self._unique_column_ids(relation):
            concrete = row_values[column].concrete
            if concrete is None:
                continue
            storage_value = self._column_storage_value(relation, column, concrete)
            if self._column_value_index(relation, column).get(storage_value):
                return True
        return False

    def _row_violates_check_constraints(
        self,
        relation: RelationId,
        row_values: Mapping[ColumnId, Any],
        *,
        require_complete: bool = False,
    ) -> bool:
        constraints = self.database_constraints(relation)
        values_by_name = {
            column.name.normalized: (
                value.concrete
                if isinstance(value, Symbol)
                else value
            )
            for column, value in row_values.items()
        }
        for check in constraints.checks:
            if not check.supported:
                raise ConstraintViolationError(
                    f"unsupported_check_constraint:{check.reason or 'unknown'}"
                )
            required_names = {
                column.name.normalized
                for column in check.referenced_columns
            }
            if require_complete and not required_names <= set(values_by_name):
                continue
            if not required_names <= set(values_by_name):
                continue
            expression = check.expression.copy()
            for col in expression.find_all(exp.Column):
                column_name = identifier_name(
                    col.name,
                    dialect=self.dialect,
                ).normalized
                if column_name in values_by_name:
                    col.set("concrete", values_by_name[column_name])
            result = concrete(expression, Environment())
            if result is False:
                return True
        return False

    def _constraint_groups(self, relation: RelationId) -> list[tuple[ColumnId, ...]]:
        table = self.schema_spec.get_table(self._table_key_for_storage(relation))
        groups: list[tuple[ColumnId, ...]] = []
        if len(table.primary_key_ids) > 1:
            groups.append(table.primary_key_ids)
        for columns in table.unique_constraint_ids:
            if len(columns) > 1:
                groups.append(columns)
        return groups

    def _resolve_composite_reference_conflicts(
        self,
        relation: RelationId,
        values: Dict[ColumnId, Any],
        locked_columns: Optional[set[ColumnId]] = None,
    ) -> dict[RelationId, list[Row]]:
        created_rows: dict[RelationId, list[Row]] = defaultdict(list)
        locked_columns = locked_columns or set()
        fk_map = self._foreign_key_map(relation)

        for _ in range(20):
            duplicate_group = None
            for column_ids in self._constraint_groups(relation):
                if not all(column in values for column in column_ids):
                    continue
                target = self._constraint_key(relation, column_ids, values)
                if any(value is None for value in target):
                    continue
                if any(
                    self._constraint_key(
                        relation,
                        column_ids,
                        {column: row[column].concrete for column in column_ids},
                    ) == target
                    for row in self.get_rows(relation)
                ):
                    duplicate_group = column_ids
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
                if ref_table in self._bootstrapping:
                    continue
                created = self.create_row(ref_table, {})
                self._merge_created_rows(created_rows, created.created)
                ref_position = created.positions[ref_table]
                ref_value = self.get_column_data(ref_table, ref_col)[ref_position].concrete
                values[column] = ref_value
                progress = True
                break
            if not progress:
                return created_rows

        return created_rows

    def _foreign_key_map(
        self,
        relation: RelationId,
    ) -> dict[ColumnId, tuple[RelationId, ColumnId]]:
        mapping: dict[ColumnId, tuple[RelationId, ColumnId]] = {}
        for fk in self.get_foreign_keys_by_relation_id(relation):
            if len(fk.source_column_ids) != 1 or len(fk.target_column_ids) != 1:
                continue
            mapping[fk.source_column_ids[0]] = (
                fk.target_table_id,
                fk.target_column_ids[0],
            )
        return mapping

    def reset(self):
        self.data.clear()
        self.symbols.clear()
        self.builder = DatabaseBuilder(self.schema_spec)
        # Re-parse so the catalog state (tables / PK / FK / constraints) is
        # reconstructed from scratch; schema_spec cache is invalidated too.
        self.mapping = {}
        self.constraints.clear()
        self.primary_keys.clear()
        self.foreign_keys.clear()
        self.unique_constraints.clear()
        self._relation_keys_by_id.clear()
        self._column_keys_by_id.clear()
        self._constraints_by_column_id.clear()
        self._primary_key_ids_by_relation_id.clear()
        self._unique_constraint_ids_by_relation_id.clear()
        self._foreign_keys_by_relation_id.clear()
        self.__dict__.pop("schema_spec", None)  # clear cached_property
        self._ingest_ddls(self.ddls, self.dialect)

    # ------------------------------------------------------------------
    # Transactional scoping
    # ------------------------------------------------------------------

    def checkpoint(self) -> Dict[str, Any]:
        """Capture a lightweight checkpoint of the current row state.

        Returns an opaque token that can be passed to :meth:`rollback` to
        restore the Instance to this point. Only row data and symbol
        registrations are captured; schema / catalog state is immutable
        and doesn't need checkpointing.
        """
        return {
            "data": {
                table: list(rows) for table, rows in self.data.items()
            },
            "symbols": list(self.symbols.names()),
        }

    def rollback(self, checkpoint: Dict[str, Any]) -> None:
        """Restore row state to a previously captured :meth:`checkpoint`.

        Rows added after the checkpoint are removed; symbols registered
        for those rows are unregistered. The builder's runtime memory is
        rebuilt from the surviving rows.
        """
        saved_data = checkpoint["data"]
        saved_symbol_names = set(checkpoint["symbols"])

        # Restore row data.
        self.data.clear()
        for table, rows in saved_data.items():
            self.data[table] = rows

        # Unregister symbols that were added after the checkpoint.
        current_names = list(self.symbols.names())
        for name in current_names:
            if name not in saved_symbol_names:
                self.symbols.unregister(name)

        self._column_data_cache.clear()
        self._column_value_index_cache.clear()
        self._column_storage_value_cache.clear()

        # Rebuild the builder's runtime memory from surviving rows.
        self.builder = DatabaseBuilder(self.schema_spec)
        for table_name in self.tables:
            for row in self.get_rows(table_name):
                self.builder.runtime.remember_row(
                    table_name,
                    self._row_value_dict(row),
                )

    def snapshot(self) -> InstanceSnapshot:
        tables: list[TableBatch] = []
        table_order = self._fk_safe_table_order()
        for table_name in table_order:
            rows = self._row_dicts(table_name)
            # Derive column names from the Row's ColumnId objects so that
            # quoted identifiers preserve their original casing (matching
            # _row_value_dict keys).  Fall back to column_names() when the
            # table has no rows yet.
            instance_rows = self.get_rows(table_name)
            if instance_rows:
                columns = tuple(
                    cid.name.normalized if hasattr(cid, "name") else str(cid)
                    for cid in instance_rows[0].columns
                )
            else:
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
            schema_ddl=self._fk_safe_schema_ddl(table_order),
            dialect=self.dialect,
            tables=tuple(tables),
        )

    def _fk_safe_table_order(self) -> tuple[str, ...]:
        original_order = tuple((self._ddl_columns or self.tables).keys())
        position = {table: index for index, table in enumerate(original_order)}
        children_by_parent: dict[str, list[str]] = {
            table: [] for table in original_order
        }
        indegree = {table: 0 for table in original_order}

        for table in self.schema_spec.tables:
            source = table.name
            if source not in indegree:
                continue
            for fk in table.foreign_keys:
                target = fk.target_table
                if target not in indegree or target == source:
                    continue
                children_by_parent.setdefault(target, []).append(source)
                indegree[source] += 1

        ready = sorted(
            (table for table, count in indegree.items() if count == 0),
            key=position.__getitem__,
        )
        ordered: list[str] = []
        while ready:
            table = ready.pop(0)
            ordered.append(table)
            for child in sorted(
                children_by_parent.get(table, ()),
                key=position.__getitem__,
            ):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort(key=position.__getitem__)

        if len(ordered) < len(original_order):
            ordered_set = set(ordered)
            ordered.extend(table for table in original_order if table not in ordered_set)
        return tuple(ordered)

    def _fk_safe_schema_ddl(self, table_order: tuple[str, ...]) -> str:
        statements = [
            statement.strip() for statement in self.ddls.split(";") if statement.strip()
        ]
        if len(statements) <= 1:
            return self.ddls

        by_table: dict[str, str] = {}
        for statement in statements:
            try:
                parsed = parse(statement, dialect=self.dialect)
            except Exception:
                return self.ddls
            if len(parsed) != 1 or not isinstance(parsed[0], exp.Create):
                return self.ddls
            create = parsed[0]
            schema = create.this if isinstance(create.this, exp.Schema) else None
            table_node = schema.this if schema is not None else create.this
            table_key = self._resolve_declared_table_key(table_node)
            by_table[table_key] = statement

        if set(by_table) != set(table_order):
            return self.ddls

        return "; ".join(by_table[table] for table in table_order) + ";"

    def _row_dicts(self, table_name: str) -> list[dict[str, Any]]:
        rows = []
        for row in self.get_rows(table_name):
            rows.append(self._row_value_dict(row))
        return rows

    def to_db(
        self,
        connection_string: str,
        dialect: str = None,
        truncate_first: bool = True,
        return_inserted: bool = False,
    ):
        """Write this instance to a live database.

        Thin delegation to :func:`parseval.instance.io.to_db`.
        """
        from .io import to_db as _to_db

        return _to_db(
            self,
            connection_string=connection_string,
            dialect=dialect,
            truncate_first=truncate_first,
            return_inserted=return_inserted,
        )
