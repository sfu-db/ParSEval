from sqlglot import exp, parse_one

from parseval.identity import (
    PARSEVAL_COLUMN_ID,
    PARSEVAL_SEMANTIC_DATATYPE,
    ColumnId,
    ColumnKind,
    IdentifierName,
    RelationId,
    RelationKind,
    column_id,
    identifier_name,
    relation_id,
)


def test_identifier_name_normalizes_unquoted_names():
    node = exp.Identifier(this="User", quoted=False)
    ident = identifier_name(node, dialect="sqlite")
    assert ident.raw == "User"
    assert ident.normalized == "user"
    assert ident.quoted is False
    assert ident.dialect == "sqlite"


def test_identifier_name_preserves_quoted_names():
    node = exp.Identifier(this="User", quoted=True)
    ident = identifier_name(node, dialect="sqlite")
    assert ident.raw == "User"
    assert ident.normalized == "User"
    assert ident.quoted is True


def test_relation_id_distinguishes_self_join_aliases():
    table = identifier_name("users")
    rel_a = relation_id(RelationKind.TABLE, table, alias=identifier_name("a"), scope_id="s0")
    rel_b = relation_id(RelationKind.TABLE, table, alias=identifier_name("b"), scope_id="s0")
    assert rel_a != rel_b
    assert rel_a.name == rel_b.name


def test_relation_binding_display_distinguishes_reused_visible_names():
    table = identifier_name("races")
    outer = relation_id(RelationKind.TABLE, table, scope_id="outer")
    inner = relation_id(RelationKind.TABLE, table, scope_id="inner")

    assert outer.display == inner.display == "races"
    assert outer.binding_display == "races@outer"
    assert inner.binding_display == "races@inner"
    assert outer.binding_display != inner.binding_display


def test_column_id_links_query_column_to_physical_source():
    physical_table = relation_id(RelationKind.TABLE, identifier_name("users"))
    physical = column_id(ColumnKind.PHYSICAL, identifier_name("id"), physical_table)
    alias_table = relation_id(
        RelationKind.TABLE,
        identifier_name("users"),
        alias=identifier_name("u"),
        scope_id="s0",
    )
    scoped = column_id(
        ColumnKind.PHYSICAL,
        identifier_name("id"),
        alias_table,
        scope_id="s0",
        source_column_id=physical,
    )
    assert scoped != physical
    assert scoped.source_column_id == physical


def test_column_id_is_safe_when_sqlglot_ast_mutates():
    expr = parse_one("SELECT u.id FROM users AS u")
    col = next(expr.find_all(exp.Column))
    rel = relation_id(RelationKind.TABLE, identifier_name("users"), alias=identifier_name("u"), scope_id="s0")
    cid = column_id(ColumnKind.PHYSICAL, identifier_name(col.this), rel, scope_id="s0")
    lookup = {cid: "value"}
    col.set("this", exp.Identifier(this="name"))
    assert lookup[cid] == "value"


def test_parseval_column_id_constant_is_stable():
    assert PARSEVAL_COLUMN_ID == "parseval_column_id"


def test_parseval_semantic_datatype_constant_is_stable():
    assert PARSEVAL_SEMANTIC_DATATYPE == "parseval_semantic_datatype"
