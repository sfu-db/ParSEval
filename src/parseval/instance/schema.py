from __future__ import annotations

from sqlglot import exp, parse

from parseval.domain import ColumnSpec, ForeignKeySpec, SchemaSpec, TableSpec


def build_schema_spec(ddls: str, dialect: str) -> SchemaSpec:
    creates = [statement for statement in parse(ddls, dialect=dialect) if isinstance(statement, exp.Create)]
    tables: list[TableSpec] = []

    for create in creates:
        schema = create.this
        table = schema.this
        table_name = table.name
        column_defs = [node for node in schema.expressions if isinstance(node, exp.ColumnDef)]

        pk_columns = _table_primary_keys(schema)
        unique_constraints = _table_unique_constraints(schema)
        foreign_keys = _table_foreign_keys(table_name, schema)
        single_column_fk_map = _single_column_fk_map(foreign_keys)

        columns: list[ColumnSpec] = []
        for column_def in column_defs:
            column_name = column_def.name
            column_constraints = list(column_def.constraints)
            column_pk = any(
                isinstance(constraint.kind, exp.PrimaryKeyColumnConstraint)
                for constraint in column_constraints
            )
            column_unique = any(
                isinstance(constraint.kind, exp.UniqueColumnConstraint)
                for constraint in column_constraints
            )
            nullable = not any(
                isinstance(constraint.kind, exp.NotNullColumnConstraint)
                for constraint in column_constraints
            )
            columns.append(
                ColumnSpec(
                    table=table_name,
                    column=column_name,
                    datatype=column_def.kind.copy(),
                    nullable=nullable and column_name.lower() not in pk_columns,
                    unique=column_unique
                    or any(
                        len(constraint) == 1 and column_name.lower() in constraint
                        for constraint in unique_constraints
                    ),
                    primary_key=column_pk or column_name.lower() in pk_columns,
                    foreign_key=single_column_fk_map.get(column_name.lower()),
                    default=None,
                    native_type=column_def.kind.sql(dialect=dialect),
                    dialect=dialect,
                    length=getattr(column_def.kind, "length", None),
                    precision=getattr(column_def.kind, "precision", None),
                    scale=getattr(column_def.kind, "scale", None),
                )
            )

        tables.append(
            TableSpec(
                name=table_name,
                columns=tuple(columns),
                primary_key=tuple(pk_columns),
                unique_constraints=tuple(unique_constraints),
                foreign_keys=tuple(foreign_keys),
            )
        )

    return SchemaSpec(tables=tuple(tables), dialect=dialect)


def _table_primary_keys(schema: exp.Schema) -> tuple[str, ...]:
    primary_keys: list[str] = []
    for node in schema.expressions:
        if isinstance(node, exp.PrimaryKey):
            primary_keys.extend(identifier.name.lower() for identifier in node.expressions)
    return tuple(primary_keys)


def _table_unique_constraints(schema: exp.Schema) -> tuple[tuple[str, ...], ...]:
    constraints: list[tuple[str, ...]] = []
    for node in schema.expressions:
        if isinstance(node, exp.UniqueColumnConstraint):
            columns = tuple(identifier.name.lower() for identifier in node.this.expressions)
            if columns:
                constraints.append(columns)
    return tuple(constraints)


def _table_foreign_keys(table_name: str, schema: exp.Schema) -> tuple[ForeignKeySpec, ...]:
    foreign_keys: list[ForeignKeySpec] = []
    for node in schema.expressions:
        if not isinstance(node, exp.ForeignKey):
            continue
        reference = node.args.get("reference")
        target_table = reference.find(exp.Table).name
        target_columns = tuple(identifier.name.lower() for identifier in reference.this.expressions)
        source_columns = tuple(identifier.name.lower() for identifier in node.expressions)
        foreign_keys.append(
            ForeignKeySpec(
                source_table=table_name,
                source_columns=source_columns,
                target_table=target_table,
                target_columns=target_columns,
            )
        )
    return tuple(foreign_keys)


def _single_column_fk_map(
    foreign_keys: tuple[ForeignKeySpec, ...]
) -> dict[str, ForeignKeySpec]:
    mapping: dict[str, ForeignKeySpec] = {}
    for foreign_key in foreign_keys:
        if len(foreign_key.source_columns) == 1:
            mapping[foreign_key.source_columns[0]] = foreign_key
    return mapping
