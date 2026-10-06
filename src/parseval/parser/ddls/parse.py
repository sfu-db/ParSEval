"""Phased import of CREATE TABLE schema descriptions into a fresh catalog."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from sqlglot import exp
from sqlglot.errors import ParseError

from parseval.catalog import Catalog, ColumnDecl, TableDecl
from parseval.errors import (
    CatalogError,
    DDLImportError,
    ErrorKind,
    IRValidationError,
    ScalarTypeError,
    SubEqError,
)
from parseval.parser.context import LoweringSession
from parseval.parser.expression import ExpressionCompiler, SCHEMA_EXPRESSIONS
from parseval.parser.scope import (
    ColumnBinding,
    EmitEnvironment,
    Relation,
)
from parseval.terms.builder import IRBuilder, TermRef
from parseval.terms.constraints import (
    CatalogExpression,
    ForeignKeyMatch,
    NullConflictPolicy,
)
from parseval.identifiers import Identifier, QualifiedName, name_key
from parseval.terms.sorts import ScalarSort


@dataclass
class _Constraint:
    node: exp.Expression
    name: Identifier | None
    columns: tuple[Identifier, ...] = ()


@dataclass
class _Table:
    name: QualifiedName
    source: exp.Create
    columns: list[ColumnDecl] = field(default_factory=list)
    constraints: list[_Constraint] = field(default_factory=list)
    declaration: TableDecl | None = None


class UnsupportedConstraintExpression(DDLImportError):
    """A schema expression that cannot be represented faithfully."""


def _row_environment(table: TableDecl, row: TermRef) -> EmitEnvironment:
    qualifiers = frozenset(
        {
            (table.name.parts[-1].text,),
            name_key(table.name),
        }
    )
    relation = Relation(
        row,
        table.schema,
        tuple(
            ColumnBinding(
                declaration.name,
                specification.sort,
                qualifiers,
                specification.collation,
            )
            for declaration, specification in zip(
                table.columns,
                table.spec.columns,
                strict=True,
            )
        ),
    )
    return EmitEnvironment(relation, row, {})


def _populate_catalog(catalog: Catalog, ddl: str) -> None:
    """Build a schema before publication; callers must supply a fresh catalog."""
    if catalog.tables() or tuple(catalog.context.relations()):
        raise CatalogError("DDL import requires a fresh catalog")
    try:
        statements = catalog.dialect.parse_ddl(ddl)
        tables: list[_Table] = []
        by_name: dict[object, _Table] = {}
        alters = []
        for node in statements:
            if node is None:
                continue
            if isinstance(node, exp.AlterTable):
                alters.append(node)
                continue
            table = _collect(catalog, node)
            key = name_key(table.name)
            if key in by_name:
                raise DDLImportError(
                    f"Duplicate table declaration: {table.name.qualified_name}"
                )
            tables.append(table)
            by_name[key] = table
        for alter in alters:
            _collect_alter(catalog, by_name, alter)
        # All final row shapes exist before any cross-table reference is bound.
        for table in tables:
            table.declaration = catalog.register_table(
                table.name,
                table.columns,
                source_sql=table.source.sql(dialect=catalog.dialect.name),
            )
        for table in tables:
            _register_keys(catalog, table)
        for table in tables:
            _register_foreign_keys(catalog, table)
        for table in tables:
            _register_expressions(catalog, table)
    except (ParseError, IRValidationError, ScalarTypeError) as exc:
        raise DDLImportError(str(exc)) from exc
    except CatalogError as exc:
        if isinstance(exc, DDLImportError):
            raise
        raise DDLImportError(str(exc)) from exc


def _collect(catalog: Catalog, node: exp.Expression) -> _Table:
    if (
        not isinstance(node, exp.Create)
        or node.kind != "TABLE"
        or not isinstance(node.this, exp.Schema)
    ):
        raise DDLImportError(
            f"Expected CREATE TABLE with explicit columns: {node.sql()}"
        )
    unsupported_options = any(
        value
        for key, value in node.args.items()
        if key not in ("this", "kind", "exists")
    )
    if unsupported_options or node.args.get("exists") not in (None, True):
        raise DDLImportError("CREATE TABLE options and AS queries are unsupported")
    dialect = catalog.dialect
    table = _Table(dialect.qualified_name(node.this.this), node)
    for item in node.this.expressions:
        if isinstance(item, exp.ColumnDef):
            if not isinstance(item.kind, exp.DataType):
                raise DDLImportError("Columns require an explicit supported SQL type")
            name = dialect.identifier(item.this, column=True)
            declared_type = item.kind.meta["declared_sql"]
            nullable = True
            collation = None
            default_sql = None
            for wrapper in item.constraints:
                kind = wrapper.kind
                constraint_name = (
                    dialect.identifier(wrapper.this)
                    if wrapper.this is not None
                    else None
                )
                if isinstance(kind, exp.NotNullColumnConstraint):
                    nullable = bool(kind.args.get("allow_null", False))
                elif isinstance(kind, exp.DefaultColumnConstraint):
                    default_sql = kind.this.sql(dialect=dialect.name)
                    continue
                elif isinstance(kind, exp.CollateColumnConstraint):
                    collation = catalog.register_collation(kind.this.name)
                    continue
                table.constraints.append(_Constraint(kind, constraint_name, (name,)))
            if item.kind.this is exp.DataType.Type.ENUM:
                # An ENUM column holds strings from its list: a CHECK membership.
                member = exp.In(this=exp.column(item.this.copy()), expressions=[value.copy() for value in item.kind.expressions])
                table.constraints.append(_Constraint(exp.CheckColumnConstraint(this=member), None, (name,)))
            table.columns.append(
                ColumnDecl(
                    name,
                    ScalarSort(dialect.scalar_type(item.kind), nullable),
                    declared_type,
                    collation,
                    default_sql,
                )
            )
        elif isinstance(item, exp.Constraint):
            name = dialect.identifier(item.this, column=True)
            for kind in item.expressions:
                table.constraints.append(
                    _Constraint(kind, name, _key_columns(catalog, kind))
                )
        else:
            table.constraints.append(
                _Constraint(item, None, _key_columns(catalog, item))
            )
    _apply_primary_key_nullability(catalog, table)
    return table


def _apply_primary_key_nullability(catalog: Catalog, table: _Table) -> None:
    keys = [
        c
        for c in table.constraints
        if isinstance(c.node, (exp.PrimaryKey, exp.PrimaryKeyColumnConstraint))
    ]
    if len(keys) > 1:
        raise DDLImportError("A table may have only one primary key")
    if keys:
        # Primary keys are NOT NULL. SQLite accepts NULL in a primary key that
        # is not an INTEGER rowid alias, a bug it keeps for compatibility;
        # generated data never relies on it, so it loads into any backend.
        key_names = {c.text for c in keys[0].columns}
        table.columns = [
            replace(c, sort=replace(c.sort, nullable=False)) if c.name.text in key_names else c
            for c in table.columns
        ]


def _collect_alter(
    catalog: Catalog,
    tables: dict[object, _Table],
    node: exp.AlterTable,
) -> None:
    if (
        node.args.get("exists")
        or node.args.get("options")
        or not isinstance(node.this, exp.Table)
    ):
        raise DDLImportError(f"Unsupported ALTER TABLE form: {node.sql()}")
    name = catalog.dialect.qualified_name(node.this)
    table = tables.get(name_key(name))
    if table is None:
        raise DDLImportError(
            f"ALTER TABLE references an unknown table: {name.qualified_name}"
        )
    for action in node.args.get("actions") or ():
        if not isinstance(action, exp.AddConstraint):
            raise DDLImportError(f"Unsupported ALTER TABLE action: {action.sql()}")
        for item in action.expressions:
            name = None
            constraints = item.expressions if isinstance(item, exp.Constraint) else (item,)
            if isinstance(item, exp.Constraint):
                name = catalog.dialect.identifier(item.this) if item.this else None
            for constraint in constraints:
                table.constraints.append(
                    _Constraint(
                        constraint,
                        name,
                        _key_columns(catalog, constraint),
                    )
                )
    _apply_primary_key_nullability(catalog, table)


def _key_columns(catalog: Catalog, node: exp.Expression) -> tuple[Identifier, ...]:
    if isinstance(node, (exp.PrimaryKey, exp.ForeignKey)):
        columns = node.expressions
    elif isinstance(node, exp.UniqueColumnConstraint) and isinstance(
        node.this, exp.Schema
    ):
        columns = node.this.expressions
    else:
        return ()
    result = []
    for column in columns:
        if not isinstance(column, exp.Identifier):
            raise DDLImportError("Expression and ordered keys are unsupported")
        result.append(catalog.dialect.identifier(column, column=True))
    return tuple(result)


def _register_keys(catalog: Catalog, table: _Table) -> None:
    declaration = table.declaration
    for item in table.constraints:
        node = item.node
        source_sql = node.sql(dialect=catalog.dialect.name)
        kwargs = {"name": item.name, "source_sql": source_sql}
        if isinstance(node, (exp.PrimaryKey, exp.PrimaryKeyColumnConstraint)):
            options = node.args.get("options", ())
            inactive = "Primary-key timing/options are unsupported" if options else None
            catalog.register_primary_key(
                declaration, item.columns, inactive_reason=inactive, **kwargs
            )
        elif isinstance(node, exp.UniqueColumnConstraint):
            if node.args.get("index_type") or node.args.get("on_conflict"):
                kwargs["inactive_reason"] = "UNIQUE options are unsupported"
            catalog.register_unique(
                declaration,
                item.columns,
                null_policy=NullConflictPolicy.NULLS_DISTINCT,
                **kwargs,
            )
        elif isinstance(node, exp.NotNullColumnConstraint):
            if not node.args.get("allow_null"):
                catalog.register_not_null(declaration, item.columns[0], **kwargs)


def _register_foreign_keys(catalog: Catalog, table: _Table) -> None:
    for item in table.constraints:
        node = item.node
        if not isinstance(node, (exp.ForeignKey, exp.Reference)):
            continue
        source_sql = node.sql(dialect=catalog.dialect.name)
        try:
            reference = (
                node.args["reference"]
                if isinstance(node, exp.ForeignKey)
                else node
            )
            target = reference.this
            target_columns = None
            if isinstance(target, exp.Schema):
                target_columns = tuple(
                    catalog.dialect.identifier(c, column=True)
                    for c in target.expressions
                )
                target = target.this
            if not isinstance(target, exp.Table):
                raise CatalogError("Foreign key requires a named target table")
            target_name = catalog.dialect.qualified_name(target)
            # Schema descriptions use the declaring table's namespace for local FKs.
            if len(target_name.parts) == 1 and len(table.name.parts) > 1:
                target_name = QualifiedName((*table.name.parts[:-1], *target_name.parts))
            options = [
                str(option).upper() for option in reference.args.get("options", ())
            ]
            match = ForeignKeyMatch.SIMPLE
            inactive_reason = None
            for option in options:
                if option.startswith("MATCH "):
                    match = ForeignKeyMatch(option.split()[-1].lower())
                elif option in ("DEFERRABLE", "INITIALLY DEFERRED"):
                    inactive_reason = (
                        "Deferred foreign keys are not unconditional query-time assumptions"
                    )
                elif option not in (
                    "NOT DEFERRABLE",
                    "INITIALLY IMMEDIATE",
                ) and not option.startswith(("ON DELETE ", "ON UPDATE ")):
                    raise CatalogError(f"Unsupported foreign-key option: {option}")
            if (
                catalog.dialect.name in ("sqlite", "mysql")
                and match is not ForeignKeyMatch.SIMPLE
            ):
                inactive_reason = (
                    "Non-default FK matching requires dialect-specific semantics"
                )
            catalog.register_foreign_key(
                table.declaration,
                item.columns,
                catalog.resolve_table(target_name),
                target_columns,
                match=match,
                name=item.name,
                source_sql=source_sql,
                inactive_reason=inactive_reason,
            )
        except (CatalogError, ValueError) as error:
            catalog.register_unsupported_constraint(
                table.declaration,
                "FOREIGN KEY",
                str(error),
                name=item.name,
                source_sql=source_sql,
            )


def _register_expressions(catalog: Catalog, table: _Table) -> None:
    declaration = table.declaration
    builder = IRBuilder(catalog.constraint_arena)
    compiler = ExpressionCompiler(
        LoweringSession(catalog, builder.arena, builder=builder),
        capabilities=SCHEMA_EXPRESSIONS,
    )
    structural = (
        exp.PrimaryKey,
        exp.PrimaryKeyColumnConstraint,
        exp.UniqueColumnConstraint,
        exp.ForeignKey,
        exp.Reference,
        exp.NotNullColumnConstraint,
        exp.AutoIncrementColumnConstraint,
        exp.IndexColumnConstraint,
    )
    for item in table.constraints:
        node = item.node
        if isinstance(node, structural):
            continue
        source_sql = node.sql(dialect=catalog.dialect.name)
        generated = isinstance(
            node,
            (exp.GeneratedAsIdentityColumnConstraint, exp.ComputedColumnConstraint),
        )
        expression = (
            node.args.get("expression")
            if isinstance(node, exp.GeneratedAsIdentityColumnConstraint)
            else node.this
            if isinstance(
                node, (exp.CheckColumnConstraint, exp.ComputedColumnConstraint)
            )
            else None
        )
        if expression is None or not (
            isinstance(node, exp.CheckColumnConstraint) or generated
        ):
            catalog.register_unsupported_constraint(
                declaration,
                node.key,
                "Constraint semantics are unsupported",
                name=item.name,
                source_sql=source_sql,
            )
            continue
        target = (
            catalog.resolve_column(declaration, item.columns[0]) if generated else None
        )
        expected = declaration.column_spec(target.id).sort if generated else None

        def lower(row):
            environment = _row_environment(declaration, row)
            try:
                if generated:
                    return compiler.lower_value(expression, environment, expected)
                return compiler.lower_condition(expression, environment)
            except SubEqError as error:
                if error.diagnostic.kind is ErrorKind.UNSUPPORTED:
                    raise UnsupportedConstraintExpression(
                        error.diagnostic.message
                    ) from error
                raise DDLImportError(error.diagnostic.message) from error

        try:
            term = builder.finish(builder.row_lambda(declaration.schema, lower))
        except UnsupportedConstraintExpression as exc:
            catalog.register_unsupported_constraint(
                declaration, node.key, str(exc), name=item.name, source_sql=source_sql
            )
            continue
        checked = CatalogExpression(term)
        if generated:
            catalog.register_generated_column(
                declaration, target.id, checked, name=item.name, source_sql=source_sql
            )
        else:
            catalog.register_check(
                declaration, checked, name=item.name, source_sql=source_sql
            )
