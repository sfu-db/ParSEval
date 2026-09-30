from dataclasses import replace

import pytest
from sqlglot import exp

from parseval.catalog import Catalog, ColumnDecl
from parseval.errors import CatalogError, DDLImportError, IRValidationError
from parseval.terms import Context, IRBuilder, Schema, TermArena, verify_uexpr
from parseval.terms.constraints import (
    CatalogExpression,
    CheckDecl,
    ConstraintMetadata,
    ForeignKeyDecl,
    ForeignKeyMatch,
    GeneratedColumnDecl,
    NotNullDecl,
    NullConflictPolicy,
    PrimaryKeyDecl,
    UniqueDecl,
    UnsupportedConstraintDecl,
)
from parseval.terms.context import (
    AggregateKind,
    AggregateSpec,
    ScalarFunctionSpec,
    Volatility,
)
from parseval.terms.names import (
    CallSiteId,
    ColumnId,
    ConstraintId,
    Identifier,
    RelationId,
)
from parseval.terms.schema import ColumnSpec, RelationSpec
from parseval.terms.sorts import PREDICATE, BagSort, RowSort, ScalarSort
from parseval.terms.types import INTEGER, STRING, TypeKind


def constraints(table, kind):
    return [item for item in table.constraints if isinstance(item, kind)]


def test_nullable_columns_and_structural_schemas():
    catalog = Catalog.from_ddl("CREATE TABLE a(x INT); CREATE TABLE b(y INT)")
    a, b = catalog.tables()
    assert a.schema == b.schema
    assert a.relation != b.relation
    assert a.columns[0].id != b.columns[0].id
    assert a.column_spec(a.columns[0].id).sort.nullable
    assert not constraints(a, NotNullDecl)
    assert not hasattr(a.spec.columns[0], "name")


def test_declared_types_defaults_and_named_not_null_are_preserved():
    catalog = Catalog.from_ddl("""CREATE TABLE t(
        id BIGINT CONSTRAINT id_required NOT NULL,
        amount NUMERIC(12, 2) DEFAULT 1.25,
        title VARCHAR(42), created TIMESTAMP WITH TIME ZONE
    )""")
    table = catalog.resolve_table("t")
    assert [c.declared_type for c in table.columns] == [
        "BIGINT",
        "NUMERIC(12, 2)",
        "VARCHAR(42)",
        "TIMESTAMP WITH TIME ZONE",
    ]
    assert table.columns[1].default_sql == "1.25"
    amount = table.column_spec(table.columns[1].id).sort.sql_type
    assert (amount.kind, amount.precision, amount.scale) == (TypeKind.DECIMAL, 12, 2)
    assert constraints(table, NotNullDecl)[0].metadata.name.text == "id_required"


def test_primary_keys_finalize_nullability_before_registration():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT, b INT, PRIMARY KEY(a,b))")
    table = catalog.resolve_table("t")
    assert all(not c.sort.nullable for c in table.spec.columns)
    assert len(constraints(table, PrimaryKeyDecl)[0].columns) == 2
    assert len(constraints(table, NotNullDecl)) == 2
    with pytest.raises(ValueError, match="only update constraints"):
        catalog.context.replace_relation(
            table.relation, replace(table.spec, columns=())
        )


@pytest.mark.parametrize(
    "declaration, nullable",
    [
        ("id INTEGER PRIMARY KEY", False),
        ("id INT PRIMARY KEY", True),
        ("id INTEGER PRIMARY KEY DESC", True),
        ("id TEXT PRIMARY KEY", True),
        ("id TEXT PRIMARY KEY NOT NULL", False),
        ("id INTEGER, PRIMARY KEY(id)", False),
        ("id INT, PRIMARY KEY(id)", True),
    ],
)
def test_sqlite_primary_key_nullability(declaration, nullable):
    catalog = Catalog.from_ddl(f"CREATE TABLE t({declaration})", dialect="sqlite")
    assert catalog.resolve_table("t").spec.columns[0].sort.nullable is nullable


def test_qualified_resolution_and_export_do_not_drop_namespaces():
    catalog = Catalog.from_ddl("CREATE TABLE a.t(x INT); CREATE TABLE b.t(y TEXT)")
    with pytest.raises(CatalogError, match="Ambiguous"):
        catalog.resolve_table("t")
    with pytest.raises(CatalogError, match="Unknown"):
        catalog.resolve_table("missing.t")
    assert catalog.resolve_table("a.t").columns[0].name.text == "x"
    mapping = catalog.to_schema_mapping()
    assert mapping.column_names(exp.to_table("a.t")) == ["x"]
    assert mapping.column_names(exp.to_table("b.t")) == ["y"]


def test_mixed_qualification_export_is_explicitly_rejected():
    catalog = Catalog.from_ddl("CREATE TABLE t(x INT); CREATE TABLE s.u(y INT)")
    with pytest.raises(CatalogError, match="equally qualified"):
        catalog.to_schema_mapping()


def test_postgres_quoted_names_and_dots():
    catalog = Catalog.from_ddl('CREATE TABLE "S"."T.X"("ID" INT, id TEXT)')
    table = catalog.resolve_table('"S"."T.X"')
    assert (
        catalog.resolve_column(table, Identifier("ID", True)).id
        != catalog.resolve_column(table, "ID").id
    )
    with pytest.raises(CatalogError):
        catalog.resolve_table('s."T.X"')
    assert catalog.to_schema_mapping().column_names(exp.to_table('"S"."T.X"')) == [
        "ID",
        "id",
    ]


@pytest.mark.parametrize("dialect", ["sqlite", "mysql"])
def test_dialect_identifier_normalization(dialect):
    catalog = Catalog.from_ddl("CREATE TABLE Users(ID INT)", dialect=dialect)
    assert (
        catalog.resolve_column(catalog.resolve_table("Users"), "id").name.text.lower()
        == "id"
    )


def test_cyclic_foreign_keys_and_omitted_target_columns():
    catalog = Catalog.from_ddl("""
        CREATE TABLE s.child(id INT PRIMARY KEY, parent INT REFERENCES parent);
        CREATE TABLE s.parent(id INT PRIMARY KEY, child INT REFERENCES child);
    """)
    child, parent = catalog.tables()
    forward = constraints(child, ForeignKeyDecl)[0]
    reverse = constraints(parent, ForeignKeyDecl)[0]
    assert forward.target_relation == parent.relation
    assert reverse.target_relation == child.relation
    assert forward.target == (parent.columns[0].id,)


def test_composite_foreign_key_preserves_pairing_and_match():
    catalog = Catalog.from_ddl("""
        CREATE TABLE c(x INT, y INT, FOREIGN KEY(x,y) REFERENCES p(b,a) MATCH FULL);
        CREATE TABLE p(a INT, b INT, UNIQUE(a,b));
    """)
    child, parent = catalog.tables()
    fk = constraints(child, ForeignKeyDecl)[0]
    assert fk.match is ForeignKeyMatch.FULL
    assert fk.target == (parent.columns[1].id, parent.columns[0].id)
    assert fk.metadata.proof_active


@pytest.mark.parametrize("option", ["DEFERRABLE INITIALLY DEFERRED"])
def test_unsupported_foreign_key_semantics_are_not_proof_assumptions(option):
    catalog = Catalog.from_ddl(
        f"CREATE TABLE t(id INT PRIMARY KEY, p INT REFERENCES t {option})"
    )
    fk = constraints(catalog.resolve_table("t"), ForeignKeyDecl)[0]
    assert not fk.metadata.proof_active
    assert fk.metadata.inactive_reason


def test_deferred_primary_key_is_not_an_unconditional_key():
    catalog = Catalog.from_ddl("CREATE TABLE t(id INT, PRIMARY KEY(id) DEFERRABLE)")
    table = catalog.resolve_table("t")
    assert not table.spec.has_unconditional_key((table.columns[0].id,))
    assert not table.spec.columns[0].sort.nullable


def test_time_and_blob_types_are_retained_in_catalog():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(occurred_at TIME, payload BLOB)", dialect="postgres"
    )
    table = catalog.resolve_table("t")
    assert table.column_spec(table.columns[0].id).sort.sql_type.kind is TypeKind.TIME
    assert table.column_spec(table.columns[1].id).sort.sql_type.kind is TypeKind.OPAQUE


def test_enum_columns_use_string_semantics_and_retain_the_declaration():
    catalog = Catalog.from_ddl("CREATE TABLE t(state ENUM('open', 'closed'))", dialect="mysql")
    table = catalog.resolve_table("t")
    assert table.column_spec(table.columns[0].id).sort.sql_type.kind is TypeKind.STRING
    assert table.columns[0].declared_type == "ENUM('open', 'closed')"


def test_alter_table_primary_key_is_collected_before_table_registration():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(id INT, value TEXT); "
        "ALTER TABLE t ADD CONSTRAINT t_pk PRIMARY KEY(id)",
        dialect="postgres",
    )
    table = catalog.resolve_table("t")
    assert not table.column_spec(table.columns[0].id).sort.nullable
    primary_key = constraints(table, PrimaryKeyDecl)[0]
    assert primary_key.columns == (table.columns[0].id,)
    assert primary_key.metadata.name.text == "t_pk"


def test_create_table_if_not_exists_is_a_supported_schema_declaration():
    catalog = Catalog.from_ddl(
        "CREATE TABLE IF NOT EXISTS t(id INTEGER PRIMARY KEY)", dialect="sqlite"
    )
    assert catalog.resolve_table("t").columns[0].name.text == "id"


def test_unresolvable_foreign_key_is_preserved_as_unsupported():
    catalog = Catalog.from_ddl(
        "CREATE TABLE child(id INT, parent_id INT REFERENCES missing(id))"
    )
    unsupported = [
        constraint
        for table in catalog.tables()
        for constraint in table.constraints
        if isinstance(constraint, UnsupportedConstraintDecl)
        and constraint.kind == "FOREIGN KEY"
    ]
    assert len(unsupported) == 1
    assert not unsupported[0].metadata.proof_active
    assert unsupported[0].metadata.source_sql


def test_duplicate_foreign_key_target_is_structured_but_proof_inactive():
    catalog = Catalog.from_ddl(
        "CREATE TABLE parent(a INT PRIMARY KEY); "
        "CREATE TABLE child(a INT, b INT, FOREIGN KEY(a, b) REFERENCES parent(a, a))"
    )
    parent = catalog.resolve_table("parent")
    child = catalog.resolve_table("child")
    fk = constraints(child, ForeignKeyDecl)[0]
    assert fk.target == (parent.columns[0].id, parent.columns[0].id)
    assert not fk.metadata.proof_active
    assert not constraints(child, UnsupportedConstraintDecl)


@pytest.mark.parametrize("dialect", ["sqlite", "mysql"])
def test_non_unique_foreign_key_target_is_structured_but_proof_inactive(dialect):
    catalog = Catalog.from_ddl(
        "CREATE TABLE parent(a INT, b INT, PRIMARY KEY(a, b)); "
        "CREATE TABLE child(a INT, FOREIGN KEY(a) REFERENCES parent(a))",
        dialect=dialect,
    )
    child = catalog.resolve_table("child")
    fk = constraints(child, ForeignKeyDecl)[0]
    assert not fk.metadata.proof_active
    assert fk.metadata.inactive_reason == (
        "Referenced key is not an active proof assumption"
    )
    assert not constraints(child, UnsupportedConstraintDecl)


@pytest.mark.parametrize(
    ("dialect", "sql"),
    [
        ("sqlite", "CREATE TABLE t(id INTEGER PRIMARY KEY AUTOINCREMENT)"),
        ("mysql", "CREATE TABLE t(id INT, INDEX(id))"),
    ],
)
def test_non_semantic_column_features_are_ignored(dialect, sql):
    table = Catalog.from_ddl(sql, dialect=dialect).resolve_table("t")
    assert not constraints(table, UnsupportedConstraintDecl)


def test_collated_keys_and_expressions_are_inactive():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(x TEXT COLLATE nocase, UNIQUE(x), CHECK(x = 'a'))",
        dialect="sqlite",
    )
    table = catalog.resolve_table("t")
    assert table.spec.columns[0].collation is not None
    assert not constraints(table, UniqueDecl)[0].metadata.proof_active
    assert constraints(table, UnsupportedConstraintDecl)
    assert not constraints(table, CheckDecl)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t(x INT); CREATE TABLE t(y TEXT)",
        "CREATE TABLE t(x INT, X TEXT)",
        "CREATE TABLE t(x INT, UNIQUE(missing))",
        "CREATE TABLE t(x INT, UNIQUE(x,x))",
        "CREATE TABLE t(x INT PRIMARY KEY, y INT PRIMARY KEY)",
        "CREATE TABLE t(x INT, CONSTRAINT u UNIQUE(x), CONSTRAINT u CHECK(x > 0))",
        "CREATE TABLE t(x INT); DROP TABLE t",
        "CREATE VIEW t AS SELECT 1",
        "CREATE TABLE t AS SELECT 1",
        "CREATE TABLE t(x JSONB)",
        "CREATE TABLE t(x INT CHECK(other.x > 0))",
        "CREATE TABLE t(x INT CHECK(missing > 0))",
        "CREATE TABLE t(",
    ],
)
def test_invalid_or_unsupported_schema_input_is_rejected(sql):
    with pytest.raises(DDLImportError):
        Catalog.from_ddl(sql)


def test_failed_programmatic_registration_does_not_publish_a_table():
    catalog = Catalog()
    with pytest.raises(CatalogError):
        catalog.register_table(
            "t", [ColumnDecl(Identifier("x"), ScalarSort(INTEGER))] * 2
        )
    assert not catalog.tables()
    assert not tuple(catalog.context.relations())
    table = catalog.register_table(
        "t", [ColumnDecl(Identifier("x"), ScalarSort(INTEGER, True))]
    )
    assert not table.constraints


def test_context_allocates_after_explicit_id_registration():
    context = Context()
    field = ScalarSort(INTEGER)
    schema = context.intern_schema(Schema((field,)))
    context.register_relation(
        RelationId(20), RelationSpec(schema, (ColumnSpec(ColumnId(50), field),))
    )
    catalog = Catalog(context)
    table = catalog.register_table("t", [ColumnDecl(Identifier("x"), field)])
    assert table.relation.value > 20
    assert table.columns[0].id.value > 50


def test_foreign_catalog_handles_are_rejected_even_when_numeric_ids_match():
    a, b = (Catalog.from_ddl("CREATE TABLE t(x INT)") for _ in range(2))
    assert a.tables()[0].relation == b.tables()[0].relation
    with pytest.raises(CatalogError, match="another catalog"):
        a.register_unique(b.tables()[0], ["x"])


def test_context_validates_constraint_ownership_before_replacement():
    catalog = Catalog.from_ddl("CREATE TABLE t(x INT); CREATE TABLE u(y INT)")
    table, other = catalog.tables()
    item = UniqueDecl(
        ConstraintMetadata(ConstraintId(90)),
        other.relation,
        (table.columns[0].id,),
        NullConflictPolicy.NULLS_DISTINCT,
    )
    with pytest.raises(ValueError, match="another relation"):
        catalog.context.replace_relation(
            table.relation, replace(table.spec, constraints=(item,))
        )
    assert not table.constraints


def test_context_rejects_removing_referenced_key():
    catalog = Catalog.from_ddl(
        "CREATE TABLE p(x INT PRIMARY KEY); CREATE TABLE c(x INT REFERENCES p)"
    )
    parent = catalog.tables()[0]
    with pytest.raises(ValueError, match="referenced"):
        catalog.context.replace_relation(
            parent.relation, replace(parent.spec, constraints=())
        )


def test_function_and_aggregate_names_bind_context_signatures():
    catalog = Catalog()
    scalar = ScalarSort(INTEGER)
    spec = ScalarFunctionSpec((scalar,), scalar)
    a = catalog.register_scalar_function("s.f", spec)
    assert catalog.register_scalar_function("s.f", spec) == a
    assert catalog.resolve_scalar_function("s.f", (scalar,)) == a
    with pytest.raises(CatalogError):
        catalog.resolve_scalar_function("other.f", (scalar,))
    with pytest.raises(CatalogError, match="Conflicting"):
        catalog.register_scalar_function(
            "s.f", replace(spec, volatility=Volatility.VOLATILE)
        )
    agg = catalog.register_aggregate(
        "s.count", AggregateSpec(None, scalar, kind=AggregateKind.COUNT)
    )
    assert catalog.resolve_aggregate("s.count", None) == agg


def test_ddl_catalog_does_not_mix_builtins_with_sql_namespace_declarations():
    catalog = Catalog.from_ddl("CREATE TABLE t(n DECIMAL(8, 2), s TEXT NOT NULL)")

    with pytest.raises(CatalogError):
        catalog.resolve_scalar_function(
            "abs", (catalog.resolve_table("t").spec.columns[0].sort,)
        )
    assert tuple(catalog.context.functions()) == ()


def test_checks_and_generated_expressions_have_arena_derived_types():
    catalog = Catalog.from_ddl("""CREATE TABLE t(
        x INT, CHECK(x > 0 AND x < 10),
        y INT GENERATED ALWAYS AS (x + 1)
    )""")
    table = catalog.tables()[0]
    check = constraints(table, CheckDecl)[0]
    generated = constraints(table, GeneratedColumnDecl)[0]
    assert catalog.constraint_arena[check.predicate.term].sort.result == PREDICATE
    assert catalog.constraint_arena[
        generated.expression.term
    ].sort.result == ScalarSort(INTEGER, True)
    assert not hasattr(check.predicate, "result_sort")


@pytest.mark.parametrize(
    "expression",
    ["NULL", "TRUE", "FALSE", "x IS NULL", "x IN (1, 2)", "0 < x", "x BETWEEN 1 AND 5"],
)
def test_check_expression_forms(expression):
    catalog = Catalog.from_ddl(f"CREATE TABLE t(x INT CHECK({expression}))")
    assert len(constraints(catalog.tables()[0], CheckDecl)) == 1


def test_mysql_boolean_check_accepts_zero_and_one_literals():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(free BOOL CHECK(free IN (0, 1)))", dialect="mysql"
    )
    assert len(constraints(catalog.tables()[0], CheckDecl)) == 1


def test_temporal_literal_cast_in_check_is_lowered():
    catalog = Catalog.from_ddl(
        "CREATE TABLE t(action_date DATE, "
        "CHECK(action_date <= CAST('2019-07-05' AS DATE)))",
        dialect="mysql",
    )
    assert len(constraints(catalog.tables()[0], CheckDecl)) == 1


def test_query_builtin_elaboration_is_shared_by_ddl_expressions():
    catalog = Catalog.from_ddl("CREATE TABLE t(x INT CONSTRAINT ck CHECK(ABS(x) > 0))")
    assert len(constraints(catalog.tables()[0], CheckDecl)) == 1


def test_unsupported_expression_is_preserved_with_inactive_provenance():
    catalog = Catalog.from_ddl("CREATE TABLE t(x INT CONSTRAINT ck CHECK(x ^ 2 > 0))")
    item = constraints(catalog.tables()[0], UnsupportedConstraintDecl)[0]
    assert item.metadata.name.text == "ck"
    assert "^" in item.metadata.source_sql
    assert not item.metadata.proof_active


@pytest.mark.parametrize(
    ("expression", "reason"),
    [
        ("EXISTS (SELECT 1)", "Subqueries are unavailable"),
        (
            "CURRENT_TIMESTAMP IS NOT NULL",
            "Non-immutable functions are unavailable",
        ),
    ],
)
def test_ddl_expression_capabilities_are_preserved_as_inactive(expression, reason):
    catalog = Catalog.from_ddl(f"CREATE TABLE t(x INT CHECK({expression}))")
    item = constraints(catalog.tables()[0], UnsupportedConstraintDecl)[0]
    assert item.metadata.inactive_reason is not None
    assert reason in item.metadata.inactive_reason
    assert not item.metadata.proof_active


def test_check_registration_rejects_wrong_type_arena_schema_and_open_terms():
    catalog = Catalog.from_ddl("CREATE TABLE t(x INT); CREATE TABLE u(x INT, y INT)")
    table, other = catalog.tables()
    b = IRBuilder(catalog.constraint_arena)
    wrong_type = b.finish(b.row_lambda(table.schema, lambda r: b.literal(1, INTEGER)))
    with pytest.raises(CatalogError, match="result sort"):
        catalog.register_check(table, CatalogExpression(wrong_type))
    wrong_schema = b.finish(b.row_lambda(other.schema, lambda r: b.true3()))
    with pytest.raises(CatalogError, match="over its table"):
        catalog.register_check(table, CatalogExpression(wrong_schema))
    foreign = IRBuilder(TermArena(catalog.context))
    foreign_term = foreign.finish(
        foreign.row_lambda(table.schema, lambda r: foreign.true3())
    )
    with pytest.raises(IRValidationError, match="another arena"):
        catalog.register_check(table, CatalogExpression(foreign_term))
    open_term = catalog.constraint_arena.row_var(0, RowSort(table.schema))
    with pytest.raises(IRValidationError, match="Free row"):
        catalog.register_check(table, CatalogExpression(open_term))
    assert not constraints(table, CheckDecl)


def test_checks_reject_relation_access_and_volatile_calls():
    catalog = Catalog.from_ddl("CREATE TABLE t(x INT)")
    table = catalog.tables()[0]
    b = IRBuilder(catalog.constraint_arena)
    term = b.finish(
        b.row_lambda(
            table.schema,
            lambda r: b.eq3(b.scalarize(b.base(table.relation)), b.literal(1, INTEGER)),
        )
    )
    with pytest.raises(CatalogError, match="row-local"):
        catalog.register_check(table, CatalogExpression(term))
    spec = ScalarFunctionSpec((), ScalarSort(INTEGER), Volatility.VOLATILE)
    function = catalog.register_scalar_function("random_value", spec)
    term = b.finish(
        b.row_lambda(
            table.schema,
            lambda r: b.eq3(
                b.scalar_call(
                    function.function,
                    (),
                    call_site=catalog.context.allocate_id(CallSiteId),
                ),
                b.literal(1, INTEGER),
            ),
        )
    )
    with pytest.raises(CatalogError, match="immutable"):
        catalog.register_check(table, CatalogExpression(term))


@pytest.mark.parametrize(
    "columns",
    [
        "x INT GENERATED ALWAYS AS(x + 1)",
        "x INT GENERATED ALWAYS AS(y + 1), y INT GENERATED ALWAYS AS(1)",
    ],
)
def test_generated_dependencies_are_rejected(columns):
    with pytest.raises(DDLImportError, match="generated columns"):
        Catalog.from_ddl(f"CREATE TABLE t({columns})")


def test_catalog_relation_can_be_used_in_a_checked_uexpression():
    catalog = Catalog.from_ddl("CREATE TABLE t(x INT NOT NULL)")
    table = catalog.tables()[0]
    arena = TermArena(catalog.context)
    b = IRBuilder(arena)
    root = b.finish(
        b.bag_lam(
            table.schema,
            lambda output: b.sum(
                RowSort(table.schema),
                lambda row: b.mul(
                    b.at(b.base(table.relation), row),
                    b.indicator(b.eq3(b.field(row, 0), b.literal(7, INTEGER))),
                    b.indicator(b.row_identity_eq(output, row)),
                ),
            ),
        )
    )
    assert verify_uexpr(arena, root) == BagSort(table.schema)


def test_partial_match_can_be_recorded_but_not_used_as_an_assumption():
    catalog = Catalog.from_ddl("CREATE TABLE t(id INT PRIMARY KEY, parent INT)")
    table = catalog.tables()[0]
    fk = catalog.register_foreign_key(
        table, ["parent"], table, match=ForeignKeyMatch.PARTIAL
    )
    assert not fk.metadata.proof_active


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t(x INT UNIQUE NULLS NOT DISTINCT)",
        "CREATE TABLE t(x INT, y INT GENERATED ALWAYS AS(x + 1) STORED)",
        "CREATE TABLE t(x INT PRIMARY KEY) WITHOUT ROWID",
    ],
)
def test_unsupported_ddl_syntax_is_rejected_at_boundary(sql):
    with pytest.raises(DDLImportError):
        Catalog.from_ddl(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "CREATE TABLE t(x DECIMAL(2, 5))",
        "CREATE TABLE t(x DECIMAL(2, 1, 1))",
        "CREATE TABLE t(x INT, y TEXT GENERATED ALWAYS AS(x))",
    ],
)
def test_type_errors_are_reported_at_the_ddl_boundary(sql):
    with pytest.raises(DDLImportError):
        Catalog.from_ddl(sql)


def test_not_null_registration_after_constraint_replacement():
    catalog = Catalog.from_ddl("CREATE TABLE t(x INT NOT NULL)")
    table = catalog.tables()[0]
    catalog.context.replace_relation(
        table.relation, replace(table.spec, constraints=())
    )
    constraint = catalog.register_not_null(table, "x", name=Identifier("required"))
    assert constraint.metadata.name.text == "required"
    assert constraint in table.constraints
