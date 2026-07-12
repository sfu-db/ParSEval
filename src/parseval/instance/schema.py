"""DDL-backed schema keyed by sqlglot ``exp.Table`` / ``exp.Identifier``.

No ``parseval.identity`` types — dialect equality comes from
``normalize_identifier`` at ingest.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, Optional, Tuple

from sqlglot import exp, parse
from sqlglot.dialects.dialect import Dialect


def normalize_identifier(
    ident: exp.Identifier | str,
    dialect: str,
    *,
    quoted: bool | None = None,
) -> exp.Identifier:
    if isinstance(ident, exp.Identifier):
        node = ident.copy()
    else:
        node = exp.to_identifier(str(ident), quoted=bool(quoted))
    return Dialect.get_or_raise(dialect).normalize_identifier(node)


def normalize_table(table: exp.Table | str, dialect: str) -> exp.Table:
    if isinstance(table, str):
        node = exp.to_table(table)
    else:
        node = table.copy()
    if node.this is not None:
        node.set("this", normalize_identifier(node.this, dialect))
    if node.args.get("db") is not None:
        node.set("db", normalize_identifier(node.args["db"], dialect))
    if node.args.get("catalog") is not None:
        node.set("catalog", normalize_identifier(node.args["catalog"], dialect))
    return node


def table_key(table: exp.Table) -> str:
    parts = [
        part.name
        for part in (table.args.get("catalog"), table.args.get("db"), table.this)
        if part is not None
    ]
    return ".".join(parts)


@dataclass(frozen=True)
class DatabaseCheckConstraint:
    table: exp.Table
    expression: exp.Expression
    referenced_columns: Tuple[exp.Identifier, ...]
    origin: str
    supported: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class ForeignKeyConstraint:
    source_table: exp.Table
    source_columns: Tuple[exp.Identifier, ...]
    target_table: exp.Table
    target_columns: Tuple[exp.Identifier, ...]


@dataclass
class ColumnSchema:
    identifier: exp.Identifier
    datatype: exp.DataType
    column_constraints: Tuple[exp.ColumnConstraint, ...] = ()
    nullable: bool = True
    primary_key: bool = False
    # True only for single-column UNIQUE / single-column PK — never for
    # composite PK membership alone.
    unique: bool = False


@dataclass
class TableSchema:
    table: exp.Table
    columns: OrderedDict[exp.Identifier, ColumnSchema] = field(default_factory=OrderedDict)
    # Ordered composite primary key (empty if none).
    primary_key: Tuple[exp.Identifier, ...] = ()
    # Additional UNIQUE groups; never includes ``primary_key`` itself.
    unique_constraints: Tuple[Tuple[exp.Identifier, ...], ...] = ()
    foreign_keys: Tuple[ForeignKeyConstraint, ...] = ()
    # Raw CHECK exprs during ingest; cleared once ``checks`` is built.
    table_checks: Tuple[exp.Expression, ...] = ()
    checks: Tuple[DatabaseCheckConstraint, ...] = ()
    create_sql: str = ""

    @property
    def name(self) -> str:
        return table_key(self.table)

    @property
    def not_null_columns(self) -> Tuple[exp.Identifier, ...]:
        return tuple(
            col.identifier for col in self.columns.values() if not col.nullable
        )

    def uniqueness_groups(self) -> Tuple[Tuple[exp.Identifier, ...], ...]:
        """All uniqueness groups, composite-aware.

        Returns the primary key (as one group, possibly multi-column) plus
        additional UNIQUE constraints. Callers must treat each group as a
        whole — composite PK members are not individually unique.
        """
        groups: list[Tuple[exp.Identifier, ...]] = []
        if self.primary_key:
            groups.append(self.primary_key)
        groups.extend(self.unique_constraints)
        return tuple(groups)


@dataclass
class InstanceSchema:
    dialect: str
    ddls: str
    tables: OrderedDict[exp.Table, TableSchema] = field(default_factory=OrderedDict)
    _by_name: Dict[str, exp.Table] = field(default_factory=dict, repr=False)

    @classmethod
    def from_ddl(cls, ddls: str, dialect: str) -> "InstanceSchema":
        schema = cls(dialect=dialect, ddls=ddls)
        schema._ingest(ddls)
        schema._build_constraints()
        return schema

    def _ingest(self, ddls: str) -> None:
        parsed = parse(ddls, dialect=self.dialect)
        pending: list[tuple[exp.Table, TableSchema, list[ForeignKeyConstraint]]] = []

        for stmt in parsed:
            create = stmt if isinstance(stmt, exp.Create) else getattr(stmt, "this", None)
            if not isinstance(create, exp.Create):
                continue
            table_schema, fks = self._parse_create(create)
            pending.append((table_schema.table, table_schema, fks))

        indegree: dict[str, int] = {table_key(t): 0 for t, _, _ in pending}
        children: dict[str, list[str]] = {k: [] for k in indegree}
        name_to_entry = {table_key(t): (t, ts, fks) for t, ts, fks in pending}
        for t, ts, fks in pending:
            child = table_key(t)
            for fk in fks:
                parent = table_key(fk.target_table)
                if parent not in indegree:
                    indegree[parent] = 0
                    children.setdefault(parent, [])
                children[parent].append(child)
                indegree[child] = indegree.get(child, 0) + 1

        ready = sorted(k for k, d in indegree.items() if d == 0 and k in name_to_entry)
        ordered: list[str] = []
        while ready:
            name = ready.pop(0)
            ordered.append(name)
            for child in children.get(name, ()):
                indegree[child] -= 1
                if indegree[child] == 0 and child in name_to_entry:
                    ready.append(child)
                    ready.sort()
        for name, _, _ in pending:
            key = table_key(name)
            if key not in ordered:
                ordered.append(key)

        for name in ordered:
            if name not in name_to_entry:
                continue
            table_node, table_schema, fks = name_to_entry[name]
            table_schema.foreign_keys = tuple(fks)
            self.tables[table_node] = table_schema
            self._by_name[table_key(table_node)] = table_node

    def _parse_create(
        self, create: exp.Create
    ) -> tuple[TableSchema, list[ForeignKeyConstraint]]:
        schema_node = create.this if isinstance(create.this, exp.Schema) else None
        table_node = schema_node.this if schema_node is not None else create.this
        table = normalize_table(table_node, self.dialect)
        expressions = (
            schema_node.expressions if schema_node is not None else create.expressions
        ) or ()

        columns: OrderedDict[exp.Identifier, ColumnSchema] = OrderedDict()
        pk_idents: list[exp.Identifier] = []
        unique_groups: list[tuple[exp.Identifier, ...]] = []
        table_checks: list[exp.Expression] = []
        fks: list[ForeignKeyConstraint] = []

        for node in expressions:
            if isinstance(node, exp.ColumnDef):
                col_ident = normalize_identifier(node.this, self.dialect)
                datatype = (
                    node.kind
                    if isinstance(node.kind, exp.DataType)
                    else exp.DataType.build(
                        node.kind.sql(dialect=self.dialect) if node.kind else "TEXT"
                    )
                )
                constraints = tuple(node.constraints or ())
                nullable = True
                is_pk = False
                is_unique = False
                for constraint in constraints:
                    kind = constraint.kind
                    if isinstance(kind, exp.NotNullColumnConstraint):
                        nullable = kind.args.get("allow_null", False)
                    elif isinstance(kind, exp.PrimaryKeyColumnConstraint):
                        is_pk = True
                        nullable = False
                        pk_idents.append(col_ident)
                    elif isinstance(kind, exp.UniqueColumnConstraint):
                        is_unique = True
                        unique_groups.append((col_ident,))
                    elif isinstance(kind, exp.Reference):
                        fks.append(self._parse_inline_reference(table, col_ident, kind))
                columns[col_ident] = ColumnSchema(
                    identifier=col_ident,
                    datatype=datatype,
                    column_constraints=constraints,
                    nullable=nullable and not is_pk,
                    primary_key=is_pk,
                    unique=is_unique or is_pk,
                )
            elif isinstance(node, exp.PrimaryKey):
                pk_idents.extend(
                    normalize_identifier(c, self.dialect) for c in node.expressions or ()
                )
            elif isinstance(node, exp.ForeignKey):
                fks.append(self._parse_foreign_key(table, node))
            elif isinstance(node, exp.UniqueColumnConstraint) and node.this is not None:
                unique_groups.append(
                    tuple(
                        normalize_identifier(c, self.dialect)
                        for c in node.this.expressions or ()
                    )
                )
            elif isinstance(node, exp.CheckColumnConstraint):
                table_checks.append(node.this)
            elif isinstance(node, exp.Constraint):
                for constraint_expr in node.expressions or ():
                    if isinstance(constraint_expr, exp.PrimaryKey):
                        pk_idents.extend(
                            normalize_identifier(c, self.dialect)
                            for c in constraint_expr.expressions or ()
                        )
                    elif (
                        isinstance(constraint_expr, exp.UniqueColumnConstraint)
                        and constraint_expr.this is not None
                    ):
                        unique_groups.append(
                            tuple(
                                normalize_identifier(c, self.dialect)
                                for c in constraint_expr.this.expressions or ()
                            )
                        )
                    elif isinstance(constraint_expr, exp.CheckColumnConstraint):
                        table_checks.append(constraint_expr.this)
                    elif isinstance(constraint_expr, exp.ForeignKey):
                        fks.append(self._parse_foreign_key(table, constraint_expr))

        pk_tuple = tuple(dict.fromkeys(pk_idents))
        single_pk = len(pk_tuple) == 1
        for ident in pk_tuple:
            if ident in columns:
                col = columns[ident]
                columns[ident] = ColumnSchema(
                    identifier=col.identifier,
                    datatype=col.datatype,
                    column_constraints=col.column_constraints,
                    nullable=False,
                    primary_key=True,
                    # Composite PK members are not individually unique.
                    unique=single_pk or col.unique,
                )
        for group in unique_groups:
            if len(group) == 1 and group[0] in columns:
                col = columns[group[0]]
                if not col.unique:
                    columns[group[0]] = ColumnSchema(
                        identifier=col.identifier,
                        datatype=col.datatype,
                        column_constraints=col.column_constraints,
                        nullable=col.nullable,
                        primary_key=col.primary_key,
                        unique=True,
                    )

        return (
            TableSchema(
                table=table,
                columns=columns,
                primary_key=pk_tuple,
                unique_constraints=tuple(
                    group for group in unique_groups if group and group != pk_tuple
                ),
                table_checks=tuple(table_checks),
                create_sql=create.sql(dialect=self.dialect),
            ),
            fks,
        )

    def _parse_inline_reference(
        self,
        source_table: exp.Table,
        col_ident: exp.Identifier,
        kind: exp.Reference,
    ) -> ForeignKeyConstraint:
        ref_table = normalize_table(kind.find(exp.Table), self.dialect)
        ref_cols = self._reference_columns(kind)
        return ForeignKeyConstraint(
            source_table=source_table,
            source_columns=(col_ident,),
            target_table=ref_table,
            target_columns=ref_cols,
        )

    def _reference_columns(self, reference: exp.Expression) -> Tuple[exp.Identifier, ...]:
        schema_ref = reference.find(exp.Schema) if reference is not None else None
        if schema_ref is not None and schema_ref.expressions:
            return tuple(
                normalize_identifier(c, self.dialect) for c in schema_ref.expressions
            )
        idents = [
            normalize_identifier(i, self.dialect)
            for i in (reference.find_all(exp.Identifier) if reference else [])
        ]
        table = reference.find(exp.Table) if reference is not None else None
        table_name = (
            normalize_identifier(table.this, self.dialect).name
            if table is not None and table.this is not None
            else None
        )
        return tuple(i for i in idents if i.name != table_name)

    def _parse_foreign_key(
        self, source_table: exp.Table, node: exp.ForeignKey
    ) -> ForeignKeyConstraint:
        source_cols = tuple(
            normalize_identifier(c, self.dialect) for c in node.expressions or ()
        )
        reference = node.args.get("reference")
        target_table = normalize_table(reference.find(exp.Table), self.dialect)
        target_cols = self._reference_columns(reference) if reference else ()
        return ForeignKeyConstraint(
            source_table=source_table,
            source_columns=source_cols,
            target_table=target_table,
            target_columns=target_cols,
        )

    def _build_constraints(self) -> None:
        for table, table_schema in self.tables.items():
            fk_specs = []
            for fk in table_schema.foreign_keys:
                target = self.resolve_table(fk.target_table)
                # Composite FK: empty target_columns means "use parent PK order".
                target_cols = fk.target_columns or self.tables[target].primary_key
                if len(fk.source_columns) != len(target_cols):
                    raise ValueError(
                        "composite_foreign_key_arity_mismatch:"
                        f"{table_key(table)}->{table_key(target)}:"
                        f"{len(fk.source_columns)}!={len(target_cols)}"
                    )
                fk_specs.append(
                    ForeignKeyConstraint(
                        source_table=table,
                        source_columns=tuple(
                            self.resolve_column(table, c) for c in fk.source_columns
                        ),
                        target_table=target,
                        target_columns=tuple(
                            self.resolve_column(target, c) for c in target_cols
                        ),
                    )
                )
            table_schema.foreign_keys = tuple(fk_specs)

            checks: list[DatabaseCheckConstraint] = []
            for check_expr in table_schema.table_checks:
                checks.append(self._check_constraint(table, check_expr, origin="table"))
            for col in table_schema.columns.values():
                for constraint in col.column_constraints:
                    if isinstance(constraint.kind, exp.CheckColumnConstraint):
                        checks.append(
                            self._check_constraint(
                                table, constraint.kind.this, origin="inline"
                            )
                        )
            table_schema.checks = tuple(checks)
            table_schema.table_checks = ()

    def _check_constraint(
        self,
        table: exp.Table,
        expression: exp.Expression,
        *,
        origin: str,
    ) -> DatabaseCheckConstraint:
        referenced: list[exp.Identifier] = []
        supported = True
        reason = None
        if expression.find(exp.Subquery):
            supported = False
            reason = "subquery"
        for col in expression.find_all(exp.Column):
            if col.table:
                try:
                    other = self.resolve_table(col.table)
                except KeyError:
                    supported = False
                    reason = "cross_relation"
                    continue
                if table_key(other) != table_key(table):
                    supported = False
                    reason = "cross_relation"
                    continue
            try:
                referenced.append(self.resolve_column(table, col.this))
            except KeyError:
                continue
        return DatabaseCheckConstraint(
            table=table,
            expression=expression,
            referenced_columns=tuple(dict.fromkeys(referenced)),
            origin=origin,
            supported=supported,
            reason=reason,
        )

    def resolve_table(self, table: exp.Table | str) -> exp.Table:
        if isinstance(table, str):
            if table in self._by_name:
                return self._by_name[table]
            matches = [k for k in self._by_name if k.casefold() == table.casefold()]
            if len(matches) == 1:
                return self._by_name[matches[0]]
            normalized = normalize_table(table, self.dialect)
            key = table_key(normalized)
            if key in self._by_name:
                return self._by_name[key]
            matches = [k for k in self._by_name if k.casefold() == key.casefold()]
            if len(matches) == 1:
                return self._by_name[matches[0]]
            raise KeyError(table)
        normalized = normalize_table(table, self.dialect)
        key = table_key(normalized)
        if key in self._by_name:
            return self._by_name[key]
        if normalized in self.tables:
            return normalized
        matches = [k for k in self._by_name if k.casefold() == key.casefold()]
        if len(matches) == 1:
            return self._by_name[matches[0]]
        raise KeyError(table)

    def resolve_column(
        self,
        table: exp.Table | str,
        column: exp.Identifier | str | exp.Column,
    ) -> exp.Identifier:
        table_node = self.resolve_table(table)
        table_schema = self.tables[table_node]
        if isinstance(column, exp.Column):
            column = column.this
        ident = normalize_identifier(column, self.dialect)
        if ident in table_schema.columns:
            return ident
        for stored in table_schema.columns:
            if stored.name == ident.name or stored.name.casefold() == ident.name.casefold():
                return stored
        raise KeyError(column)

    def get_table(self, table: exp.Table | str) -> TableSchema:
        return self.tables[self.resolve_table(table)]

    def database_constraints(self, table: exp.Table | str) -> TableSchema:
        """Return the table schema (constraints live on :class:`TableSchema`)."""
        return self.get_table(table)

    def nullable(self, table: exp.Table | str, column: exp.Identifier | str | exp.Column) -> bool:
        table_node = self.resolve_table(table)
        col = self.resolve_column(table_node, column)
        return self.tables[table_node].columns[col].nullable

    def is_unique(self, table: exp.Table | str, column: exp.Identifier | str | exp.Column) -> bool:
        """True only for single-column uniqueness (not composite PK members)."""
        table_schema = self.get_table(table)
        col_ident = self.resolve_column(table, column)
        if table_schema.primary_key == (col_ident,):
            return True
        if table_schema.columns[col_ident].unique:
            return True
        return (col_ident,) in table_schema.unique_constraints

    def column_names(self, table: exp.Table | str) -> Tuple[str, ...]:
        return tuple(ident.name for ident in self.get_table(table).columns)

    def name_mapping(self) -> OrderedDict[str, OrderedDict[str, str]]:
        result: OrderedDict[str, OrderedDict[str, str]] = OrderedDict()
        for table, table_schema in self.tables.items():
            cols: OrderedDict[str, str] = OrderedDict()
            for ident, col in table_schema.columns.items():
                cols[ident.name] = col.datatype.sql(dialect=self.dialect)
            result[table_key(table)] = cols
        return result

    def fk_safe_table_order(self) -> Tuple[exp.Table, ...]:
        original = list(self.tables.keys())
        position = {table_key(t): i for i, t in enumerate(original)}
        indegree = {table_key(t): 0 for t in original}
        children: dict[str, list[str]] = {table_key(t): [] for t in original}
        for table, table_schema in self.tables.items():
            source = table_key(table)
            for fk in table_schema.foreign_keys:
                target = table_key(fk.target_table)
                if target not in indegree or target == source:
                    continue
                children[target].append(source)
                indegree[source] += 1
        ready = sorted(
            (k for k, d in indegree.items() if d == 0),
            key=position.__getitem__,
        )
        ordered_names: list[str] = []
        while ready:
            name = ready.pop(0)
            ordered_names.append(name)
            for child in sorted(children.get(name, ()), key=position.__getitem__):
                indegree[child] -= 1
                if indegree[child] == 0:
                    ready.append(child)
                    ready.sort(key=position.__getitem__)
        if len(ordered_names) < len(original):
            seen = set(ordered_names)
            ordered_names.extend(
                table_key(t) for t in original if table_key(t) not in seen
            )
        return tuple(self._by_name[name] for name in ordered_names)


__all__ = [
    "ColumnSchema",
    "DatabaseCheckConstraint",
    "ForeignKeyConstraint",
    "InstanceSchema",
    "TableSchema",
    "normalize_identifier",
    "normalize_table",
    "table_key",
]
