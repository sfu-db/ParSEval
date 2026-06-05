from parseval.identity import ColumnId, ColumnKind, RelationId, RelationKind
from parseval.instance import Instance


def test_instance_builds_physical_table_and_column_ids():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);", name="db", dialect="sqlite")
    users_id = inst.table_id("users")
    id_col = inst.column_id("users", "id")
    assert isinstance(users_id, RelationId)
    assert users_id.kind is RelationKind.TABLE
    assert users_id.name.normalized == "users"
    assert isinstance(id_col, ColumnId)
    assert id_col.kind is ColumnKind.PHYSICAL
    assert id_col.relation == users_id
    assert id_col.name.normalized == "id"


def test_catalog_column_preserves_type_and_constraints():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT NOT NULL);", name="db", dialect="sqlite")
    id_info = inst.catalog_column("users", "id")
    name_info = inst.catalog_column("users", "name")
    assert id_info.primary_key is True
    assert id_info.nullable is False
    assert name_info.nullable is False
    assert name_info.datatype.sql().upper() == "TEXT"


def test_schema_spec_carries_identity_fields():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);", name="db", dialect="sqlite")
    table = inst.schema_spec.get_table("users")
    column = table.get_column("id")
    assert table.id == inst.table_id("users")
    assert column.id == inst.column_id("users", "id")
    assert column.table_id == table.id


def test_inline_primary_key_populates_table_primary_key_ids():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);", name="db", dialect="sqlite")
    table = inst.schema_spec.get_table("users")
    assert table.primary_key == ("id",)
    assert table.primary_key_ids == (inst.column_id("users", "id"),)


def test_catalog_column_preserves_table_level_single_column_unique():
    inst = Instance("CREATE TABLE users (id INT, email TEXT, UNIQUE(email));", name="db", dialect="sqlite")
    assert inst.catalog_column("users", "email").unique is True
    assert inst.schema_spec.get_table("users").get_column("email").unique is True


def test_table_level_multi_column_unique_populates_identity_fields():
    inst = Instance(
        "CREATE TABLE users (id INT, email TEXT, org TEXT, UNIQUE(email, org));",
        name="db",
        dialect="sqlite",
    )
    table = inst.schema_spec.get_table("users")
    assert table.unique_constraint_ids == (
        (inst.column_id("users", "email"), inst.column_id("users", "org")),
    )


def test_foreign_key_spec_carries_column_ids():
    ddl = '''
    CREATE TABLE users (id INT PRIMARY KEY);
    CREATE TABLE orders (
        id INT PRIMARY KEY,
        user_id INT REFERENCES users(id)
    );
    '''
    inst = Instance(ddl, name="db", dialect="sqlite")
    orders = inst.schema_spec.get_table("orders")
    fk = orders.foreign_keys[0]
    assert fk.source_table_id == inst.table_id("orders")
    assert fk.target_table_id == inst.table_id("users")
    assert fk.source_column_ids == (inst.column_id("orders", "user_id"),)
    assert fk.target_column_ids == (inst.column_id("users", "id"),)
