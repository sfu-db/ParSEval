from __future__ import annotations

from collections import OrderedDict, defaultdict
from functools import cached_property
from typing import Any, Dict, List, Optional, Set, Tuple, TYPE_CHECKING
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
from parseval.identity import (
    CatalogColumn,
    ColumnId,
    ColumnKind,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)
# from parseval.helper import normalize_name
from parseval.plan.rex import Row, Symbol, Variable
from parseval.states import raise_exception

from .exporter import InstanceExporter
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
        self._relation_ids: Dict[str, Any] = {}
        self._column_ids: Dict[Tuple[str, str], Any] = {}
        self._catalog_columns: Dict[Any, CatalogColumn] = {}
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

    def _remember_table_identity(self, table: exp.Table | str) -> None:
        key = self._identity_key(table, is_table=True)
        if key not in self._relation_ids:
            self._relation_ids[key] = self._relation_identity_from_source(table)

    def _remember_column_identity(
        self,
        table: exp.Table | str,
        column: exp.Identifier | str,
        datatype_sql: str,
    ) -> None:
        table_key = self._identity_key(table, is_table=True)
        column_key = self._identity_key(column)
        self._remember_table_identity(table)
        rel_id = self._relation_ids[table_key]
        col_id = column_id(
            ColumnKind.PHYSICAL,
            identifier_name(column, dialect=self.dialect),
            rel_id,
        )
        self._column_ids[(table_key, column_key)] = col_id
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
        self._catalog_columns.clear()
        table_columns = self._ddl_columns or self.tables
        for table_key, columns in table_columns.items():
            table_source = self._table_sources.get(table_key, table_key)
            self._remember_table_identity(table_source)
            for column_key, datatype_sql in columns.items():
                column_source = self._column_sources.get(
                    (table_key, column_key),
                    column_key,
                )
                self._remember_column_identity(
                    table_source,
                    column_source,
                    datatype_sql,
                )

    def table_id(self, table: exp.Table | str):
        key = self._identity_key(table, is_table=True)
        return self._relation_ids[key]

    def column_id(
        self,
        table: exp.Table | str,
        column: exp.Column | exp.Identifier | str,
    ):
        table_key = self._identity_key(table, is_table=True)
        column_key = self._identity_key(column)
        return self._column_ids[(table_key, column_key)]

    def catalog_column(
        self,
        table: exp.Table | str,
        column: exp.Column | exp.Identifier | str,
    ) -> CatalogColumn:
        return self._catalog_columns[self.column_id(table, column)]

    def add_primary_key(
        self, table: exp.Table | str, columns: List[exp.Identifier] | exp.Identifier
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

    def get_primary_key(self, table: exp.Table | str):
        table = self._identity_key(table, is_table=True)
        return tuple(self.primary_keys.get(table, ()))

    def _primary_key_names(self, table: exp.Table | str) -> Tuple[str, ...]:
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
        self, table: exp.Table | str, foreign_key: List[exp.ForeignKey] | exp.ForeignKey
    ):
        table = self._identity_key(table, is_table=True)
        fk_list = self.foreign_keys.setdefault(table, [])
        fks = [foreign_key] if isinstance(foreign_key, exp.ForeignKey) else foreign_key
        fk_list.extend(fks)

    def get_foreign_key(self, table: exp.Table | str):
        table = self._identity_key(table, is_table=True)
        return self.foreign_keys.get(table, [])

    def add_unique_constraint(
        self,
        table: exp.Table | str,
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

    def get_unique_constraints(self, table: exp.Table | str):
        table = self._identity_key(table, is_table=True)
        return tuple(self.unique_constraints.get(table, ()))

    def add_constraint(
        self,
        table: exp.Table | str,
        column: exp.Column | exp.Identifier | str,
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
        table: exp.Table | str,
        column: exp.Column | exp.Identifier | str,
    ):
        table = self._identity_key(table, is_table=True)
        column = self._identity_key(column)
        table_constraints = self.constraints.get(table, {})
        return table_constraints.get(column, set())

    def get_check_constraints(self, table: exp.Table | str) -> List[exp.Expression]:
        """Return parsed CHECK constraint expressions for a table."""
        table = self._identity_key(table, is_table=True)
        results = []
        for col_constraints in self.constraints.get(table, {}).values():
            for c in col_constraints:
                if isinstance(c.kind, exp.CheckColumnConstraint):
                    results.append(c.kind.this)
        return results

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
        column_name = self._identity_key(column)
        if column_name in self._primary_key_names(table):
            return False
        return True

    def is_unique(
        self,
        table: exp.Table | str,
        column: exp.Column | str,
        normalize: Optional[bool] = None,
    ):
        del normalize
        pk_columns = self._primary_key_names(table)
        for constraint in self.get_column_constraints(table, column):
            if isinstance(
                constraint.kind,
                (exp.UniqueColumnConstraint, exp.PrimaryKeyColumnConstraint),
            ):
                return True
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

        def _walk(
            ddl: exp.Create,
            maps: Dict[str, Dict[str, str]],
            deps: Dict[str, int],
            pks: Dict[str, list],
            fks: Dict[str, list],
            uniques: Dict[str, list],
            tbl_constraints: Dict[str, Dict[str, set]],
        ) -> None:
            table_node = ddl.this
            table_name = self._identity_key(table_node, is_table=True)
            self._table_sources.setdefault(table_name, table_node)
            if table_name not in deps:
                deps[table_name] = 0
            table_mapping = maps.setdefault(table_name, {})
            constraints = tbl_constraints.setdefault(table_name, {})
            for node in ddl.dfs():
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
            )
        self._ddl_columns = OrderedDict(
            (table_name, OrderedDict(columns))
            for table_name, columns in mappings.items()
        )
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
                table_name: mappings[table_name]
                for table_name in sorted(
                    mappings,
                    key=lambda key: dependency.get(key, 0),
                    reverse=True,
                )
            }
        )
        for table_name, table_columns in sorted_table.items():
            table_source = self._table_sources.get(table_name, table_name)
            self.add_table(table_name, table_columns, dialect=dialect)
            self.add_primary_key(table_source, primary_keys.get(table_name, []))
            self.add_foreign_key(table_source, foreign_keys.get(table_name, []))
            for unique_columns in unique_constraints.get(table_name, []):
                self.add_unique_constraint(table_source, list(unique_columns))
            for column in table_columns:
                if column in table_constraints.get(table_name, {}):
                    self.add_constraint(
                        table_source,
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
            pk_columns = list(self._primary_key_names(table_source))
            for column_name in column_types:
                column_source = self._column_sources.get(
                    (table_name, column_name),
                    column_name,
                )
                raw_constraints = self.get_column_constraints(
                    table_source,
                    column_source,
                )
                if any(
                    isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
                    for constraint in raw_constraints
                ) and column_name not in pk_columns:
                    pk_columns.append(column_name)
            unique_constraints = tuple(self.get_unique_constraints(table_source))
            fk_nodes = self.get_foreign_key(table_source)
            fk_specs: List[ForeignKeySpec] = []
            single_column_fk_map: Dict[str, ForeignKeySpec] = {}
            for fk_node in fk_nodes:
                reference = fk_node.args.get("reference")
                if reference is None:
                    continue
                target_table = reference.find(exp.Table)
                if target_table is None:
                    continue
                source_columns = tuple(
                    self._resolve_declared_column_key(table_name, identifier)
                    for identifier in fk_node.expressions
                )
                target_table_key = self._resolve_declared_table_key(target_table)
                target_columns = tuple(
                    self._resolve_declared_column_key(target_table_key, identifier)
                    for identifier in reference.this.expressions
                )
                # If target columns not specified, infer from parent PK.
                if not target_columns:
                    target_columns = self.resolve_fk_ref_columns(fk_node)
                if len(source_columns) != len(target_columns):
                    raise ValueError(
                        "Foreign key column count does not match referenced columns: "
                        f"{table_name}({', '.join(source_columns)}) -> "
                        f"{target_table.name}({', '.join(target_columns)})"
                    )
                source_column_ids = tuple(
                    self.column_id(
                        table_source,
                        self._column_sources.get((table_name, col), col),
                    )
                    for col in source_columns
                )
                target_table_source = self._table_sources.get(
                    target_table_key,
                    target_table,
                )
                target_table_id = self.table_id(target_table_source)
                target_column_ids = tuple(
                    self.column_id(
                        target_table_source,
                        self._column_sources.get((target_table_key, col), col),
                    )
                    for col in target_columns
                )
                fk_spec = ForeignKeySpec(
                    source_table=table_name,
                    source_columns=source_columns,
                    target_table=target_table_key,
                    target_columns=target_columns,
                    source_table_id=self.table_id(table_source),
                    source_column_ids=source_column_ids,
                    target_table_id=target_table_id,
                    target_column_ids=target_column_ids,
                )
                fk_specs.append(fk_spec)
                if len(source_columns) == 1:
                    single_column_fk_map[source_columns[0]] = fk_spec

            column_specs: List[ColumnSpec] = []
            for column_name, type_sql in column_types.items():
                column_source = self._column_sources.get(
                    (table_name, column_name),
                    column_name,
                )
                raw_constraints = self.get_column_constraints(
                    table_source,
                    column_source,
                )
                column_pk = any(
                    isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
                    for constraint in raw_constraints
                )
                column_unique = any(
                    isinstance(constraint.kind, exp.UniqueColumnConstraint)
                    for constraint in raw_constraints
                )
                single_column_unique = any(
                    len(columns) == 1 and columns[0] == column_name
                    for columns in unique_constraints
                )
                nullable = not any(
                    isinstance(constraint.kind, exp.NotNullColumnConstraint)
                    for constraint in raw_constraints
                )
                is_pk = column_pk or column_name in pk_columns
                datatype_node = self._datatype_node_for(column_name, type_sql)
                column_specs.append(
                    ColumnSpec(
                        table=table_name,
                        column=column_name,
                        datatype=datatype_node.copy(),
                        nullable=nullable and not is_pk,
                        unique=column_unique or single_column_unique,
                        primary_key=is_pk,
                        foreign_key=single_column_fk_map.get(column_name),
                        default=None,
                        native_type=type_sql,
                        dialect=self.dialect,
                        length=getattr(datatype_node, "length", None),
                        precision=getattr(datatype_node, "precision", None),
                        scale=getattr(datatype_node, "scale", None),
                        id=self.column_id(table_source, column_source),
                        table_id=self.table_id(table_source),
                    )
                )

            table_specs.append(
                TableSpec(
                    name=table_name,
                    columns=tuple(column_specs),
                    primary_key=tuple(pk_columns),
                    unique_constraints=tuple(unique_constraints),
                    foreign_keys=tuple(fk_specs),
                    id=self.table_id(table_source),
                    primary_key_ids=tuple(
                        self.column_id(
                            table_source,
                            self._column_sources.get((table_name, column), column),
                        )
                        for column in pk_columns
                    ),
                    unique_constraint_ids=tuple(
                        tuple(
                            self.column_id(
                                table_source,
                                self._column_sources.get((table_name, column), column),
                            )
                            for column in columns
                        )
                        for columns in unique_constraints
                    ),
                )
            )

        return SchemaSpec(tables=tuple(table_specs), dialect=self.dialect)

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
        self._bootstrapping: set[str] = set()

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

    def add_row(self, table_name: str, row: Row):
        table_name = self._normalize_table(table_name, dialect=self.dialect)
        self.data[table_name].append(row)

    def get_rows(self, table_name) -> List[Row]:
        table_name = self._normalize_table(table_name, dialect=self.dialect)
        return self.data[table_name]

    def get_row(self, table_name, index):
        return self.get_rows(table_name)[index]

    def get_column_data(self, table_name, column_name) -> List[Symbol]:
        table_name = self._normalize_table(table_name, dialect=self.dialect)
        col_id = self._stored_column_id(table_name, column_name)
        return [row[col_id] for row in self.get_rows(table_name)]

    @staticmethod
    def _row_value_dict(row: Row) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for column, symbol in row.items():
            key = column.name.normalized if isinstance(column, ColumnId) else str(column)
            values[key] = symbol.concrete
        return values

    def _table_key_for_storage(self, table_name: str) -> str:
        return self._resolve_declared_table_key(table_name)

    def _table_columns_for_storage(self, table_name: str):
        table_key = self._table_key_for_storage(table_name)
        return (self._ddl_columns or self.tables).get(table_key, self.tables[table_name])

    def _column_source_for_storage(self, table_name: str, column_name: str):
        table_key = self._table_key_for_storage(table_name)
        column_key = self._resolve_declared_column_key(table_key, column_name)
        return self._column_sources.get((table_key, column_key), column_key)

    def _stored_column_id(self, table_name: str, column_name: str):
        table_key = self._table_key_for_storage(table_name)
        table_source = self._table_sources.get(table_key, table_key)
        column_source = self._column_source_for_storage(table_name, column_name)
        return self.column_id(table_source, column_source)

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
        for normalized_table in self._creation_order(normalized_concretes):
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

    def _creation_order(self, concretes: Dict[str, Dict[str, List[Any]]]) -> List[str]:
        requested = list(concretes.keys())
        requested_set = set(requested)
        visited: Set[str] = set()
        ordered: List[str] = []

        def visit(table_name: str) -> None:
            if table_name in visited:
                return
            visited.add(table_name)
            for fk in self.get_foreign_key(table_name):
                reference = fk.args.get("reference")
                if reference is None:
                    continue
                ref_table_expr = reference.find(exp.Table)
                if ref_table_expr is None:
                    continue
                ref_table = self._normalize_table(ref_table_expr.name, dialect=self.dialect)
                if ref_table in requested_set:
                    visit(ref_table)
            ordered.append(table_name)

        for table_name in requested:
            visit(table_name)
        return ordered

    # ------------------------------------------------------------------
    # Row creation — Level 0 (primitive, unchecked)
    # ------------------------------------------------------------------

    def place_row(
        self,
        table_name: str,
        values: Dict[str, Any],
    ) -> Row:
        """Append a row with explicit values. No FK/unique validation.

        Creates a :class:`Variable` for each column, registers it in the
        :class:`SymbolIndex`, and appends the :class:`Row`. This is the
        foundation that :meth:`create_row` builds on; tests and the
        solver use it when they want full control without policy.

        ``values`` must contain an entry for every column in the table.
        Missing columns are filled with ``None`` (SQL NULL).
        """
        table_name = self._normalize_name(table_name, dialect=self.dialect, is_table=True)
        if table_name not in self.tables:
            raise KeyError(f"Unknown table: {table_name}")
        tuple_index = len(self.get_rows(table_name))
        rowid = f"{table_name}_rowid_{tuple_index}"
        normalized_values = {
            self._normalize_name(k, dialect=self.dialect): v for k, v in values.items()
        }
        table_columns = self._table_columns_for_storage(table_name)
        row_cells: Dict[Any, Variable] = {}
        table_key = self._table_key_for_storage(table_name)
        table_source = self._table_sources.get(table_key, table_key)
        relation_id = self.table_id(table_source)
        for column, datatype in table_columns.items():
            col_id = self._stored_column_id(table_name, column)
            z_name = f"{table_name}_{column}_{datatype}_{tuple_index}"
            concrete = normalized_values.get(
                self._normalize_name(column, dialect=self.dialect)
            )
            z_value = Variable(
                this=z_name,
                _type=datatype,
                concrete=concrete,
                table=table_name,
                column=column,
                relation_id=relation_id,
                column_id=col_id,
                rowid=rowid,
            )
            z_value.type = datatype
            row_cells[col_id] = z_value
            self.symbols.register(z_value)
        row = Row(this=rowid, columns=row_cells)
        self.add_row(table_name, row)
        return row

    # ------------------------------------------------------------------
    # Row creation — Level 1 (policy-driven, validated)
    # ------------------------------------------------------------------

    def create_row(
        self,
        table_name: str,
        values: Dict[str, Any] | None = None,
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
        self._bootstrapping.add(table_name)
        try:
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
                main_pos = self._create_row(table_name, values)
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
                main_pos = self._create_row(table_name, values)
        finally:
            self._bootstrapping.discard(table_name)
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
    ):
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

        for _ in range(10):
            try:
                completed = self.builder.complete_row(
                    table_name,
                    preset_values=concretes,
                    persist=False,
                )
            except UniqueConflictError:
                raise
            except Exception:
                if table_name in self._bootstrapping:
                    return self._create_row_circular_fk(table_name, concretes, tuple_index)
                raise
            new_values = {}
            rowid = f"{table_name}_rowid_{tuple_index}"
            table_key = self._table_key_for_storage(table_name)
            table_source = self._table_sources.get(table_key, table_key)
            relation_id = self.table_id(table_source)
            table_columns = self._table_columns_for_storage(table_name)
            for column, datatype in table_columns.items():
                col_id = self._stored_column_id(table_name, column)
                z_name = f"{table_name}_{column}_{datatype}_{tuple_index}"
                concrete = completed.get(
                    column,
                    completed.get(self._normalize_name(column, dialect=self.dialect)),
                )
                z_value = Variable(
                    this=z_name,
                    _type=datatype,
                    concrete=concrete,
                    table=table_name,
                    column=column,
                    relation_id=relation_id,
                    column_id=col_id,
                    rowid=rowid,
                )
                z_value.type = datatype
                new_values[col_id] = z_value
                self.symbols.register(z_value)
            if self._row_violates_unique_constraints(table_name, new_values):
                continue
            self.add_row(table_name, Row(this=rowid, columns=new_values))
            self.builder.runtime.remember_row(
                table_name,
                {
                    column.name.normalized: value.concrete
                    for column, value in new_values.items()
                },
            )
            return tuple_index
        raise_exception(f"Failed to create row for table {table_name} after 10 attempts")

    def _create_row_circular_fk(
        self,
        table_name: str,
        concretes: Dict[str, Any],
        tuple_index: int,
    ) -> int:
        """Create a row bypassing FK validation for circular dependencies.

        Uses place_row with preset values and defaults for FK columns that
        reference tables currently being bootstrapped.
        """
        row_values: Dict[str, Any] = {}
        fk_cols: list[str] = []
        for column, datatype in self.tables[table_name].items():
            if column in concretes:
                row_values[column] = concretes[column]
            else:
                row_values[column] = self._default_for_type(datatype)

        for fk in self.get_foreign_key(table_name):
            local_col = self._normalize_name(fk.expressions[0].name, dialect=self.dialect)
            ref = fk.args.get("reference")
            if ref is None:
                continue
            ref_table_node = ref.find(exp.Table)
            if ref_table_node is None:
                continue
            ref_table_str = self._normalize_name(ref_table_node.name, dialect=self.dialect, is_table=True)
            if ref_table_str in self._bootstrapping:
                if self.nullable(table_name, local_col):
                    row_values[local_col] = None
                else:
                    row_values[local_col] = self._default_for_type(
                        self.tables[table_name].get(local_col, "TEXT")
                    )
                fk_cols.append(local_col)

        row = self.place_row(table_name, row_values)
        self.builder.runtime.remember_row(
            table_name,
            self._row_value_dict(row),
        )
        return tuple_index

    @staticmethod
    def _default_for_type(datatype: str) -> Any:
        """Return a sensible default value for a SQL type string."""
        upper = datatype.upper()
        if any(t in upper for t in ("INT", "INTEGER", "BIGINT", "SMALLINT", "TINYINT")):
            return 1
        if any(t in upper for t in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL")):
            return 1.0
        if "BOOL" in upper:
            return 0
        return "value"

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
            ref = fk.args.get("reference")
            if ref is None:
                continue
            ref_table_node = ref.find(exp.Table)
            if ref_table_node is None:
                continue
            ref_table = self._normalize_table(ref_table_node.name, dialect=self.dialect)
            ref_table_str = self._normalize_name(ref_table_node.name, dialect=self.dialect, is_table=True)
            ref_col = self.resolve_fk_ref_column(fk)
            if ref_col is None:
                continue

            # Circular FK: ref_table is already being bootstrapped.
            # Skip entirely — _create_row_circular_fk will handle the value.
            if ref_table_str in self._bootstrapping:
                continue

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
                    created = self.create_row(ref_table, {})
                    self._merge_created_rows(created_rows, created.created)
                    ref_position = next(iter(created.positions.values()))
                    ref_value = self.get_column_data(ref_table, ref_col)[ref_position]
                    values[local_col] = ref_value.concrete
                    continue
                if explicit_value not in existing_parent_values:
                    created = self.create_row(
                        ref_table,
                        {ref_col: explicit_value},
                    )
                    self._merge_created_rows(created_rows, created.created)
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

            created = self.create_row(ref_table, {})
            self._merge_created_rows(created_rows, created.created)
            ref_position = next(iter(created.positions.values()))
            ref_value = self.get_column_data(ref_table, ref_col)[ref_position]
            values[local_col] = ref_value.concrete

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
            concretes = tuple(
                row_values[self._stored_column_id(table_name, column)].concrete
                for column in columns
            )
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
            concrete = row_values[self._stored_column_id(table_name, column)].concrete
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
                # Skip if ref_table is already being bootstrapped (circular FK).
                if ref_table in self._bootstrapping:
                    continue
                created = self.create_row(ref_table, {})
                self._merge_created_rows(created_rows, created.created)
                ref_position = next(iter(created.positions.values()))
                ref_value = self.get_column_data(ref_table, ref_col)[ref_position].concrete
                values[column] = ref_value
                progress = True
                break
            if not progress:
                return created_rows

        return created_rows

    def _foreign_key_map(self, table_name: str) -> dict[str, tuple[str, str]]:
        mapping: dict[str, tuple[str, str]] = {}
        for fk in self.get_foreign_key(table_name):
            local_col = self._normalize_name(fk.expressions[0].name, dialect=self.dialect)
            ref = fk.args.get("reference")
            if ref is None:
                continue
            ref_table_node = ref.find(exp.Table)
            if ref_table_node is None:
                continue
            ref_table = self._normalize_name(ref_table_node.name, dialect=self.dialect, is_table=True)
            ref_col = self.resolve_fk_ref_column(fk)
            if ref_col is None:
                continue
            mapping[local_col] = (ref_table, ref_col)
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
