import pytest

from parseval.catalog import Catalog
from parseval.instance import Instance, Row
from parseval.terms.names import RelationId


def test_instance_preserves_duplicate_rows_and_schema_identity():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT, b TEXT)")
    relation, specification = next(iter(catalog.context.relations()))

    instance = Instance.from_rows(
        catalog,
        {relation: ((1, "x"), (1, "x"), (None, "y"))},
    )

    assert instance.rows(relation) == (
        Row(specification.schema, (1, "x")),
        Row(specification.schema, (1, "x")),
        Row(specification.schema, (None, "y")),
    )


def test_with_rows_returns_a_new_instance():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, _ = next(iter(catalog.context.relations()))
    empty = Instance.empty(catalog)

    populated = empty.with_rows(relation, ((4,),))

    assert empty.rows(relation) == ()
    assert tuple(row.values for row in populated.rows(relation)) == ((4,),)


def test_direct_construction_normalizes_and_validates_rows():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, specification = next(iter(catalog.context.relations()))

    instance = Instance(catalog, {relation: ([1],)})

    assert instance.rows(relation) == (Row(specification.schema, (1,)),)

    with pytest.raises(ValueError, match="do not match"):
        Instance(catalog, {relation: ((1, 2),)})


def test_instance_rejects_unknown_relations_instead_of_treating_them_as_empty():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    instance = Instance.empty(catalog)

    with pytest.raises(KeyError, match="Unknown relation"):
        instance.rows(RelationId(999))


def test_instance_rejects_rows_with_the_wrong_schema():
    catalog = Catalog.from_ddl("CREATE TABLE a(x INT); CREATE TABLE b(y TEXT)")
    (_, specification_a), (relation_b, _) = catalog.context.relations()

    with pytest.raises(ValueError, match="do not match"):
        Instance.from_rows(
            catalog,
            {relation_b: (Row(specification_a.schema, (1,)),)},
        )
