from sqlglot import exp
import json
from pathlib import Path

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


def test_mixed_case_table_level_primary_key_populates_catalog_metadata():
    inst = Instance(
        "CREATE TABLE Users (ID INT, Name TEXT, PRIMARY KEY(ID));",
        name="db",
        dialect="sqlite",
    )
    info = inst.catalog_column("users", "id")
    assert info.primary_key is True
    assert info.unique is True
    assert info.nullable is False
    assert inst.nullable("users", "id") is False
    assert inst.is_unique("users", "id") is True


def test_named_composite_primary_key_populates_database_constraints():
    inst = Instance(
        """
        CREATE TABLE FOLLOW (
          FOLLOWEE VARCHAR(30) NOT NULL,
          FOLLOWER VARCHAR(30) NOT NULL,
          CONSTRAINT PK_FOLLOW PRIMARY KEY (FOLLOWEE, FOLLOWER),
          CONSTRAINT CHECK_FOLLOW CHECK (FOLLOWEE <> FOLLOWER)
        );
        """,
        name="db",
        dialect="sqlite",
    )

    constraints = inst.database_constraints(inst.table_id("follow"))

    assert constraints.primary_key == (
        inst.column_id("follow", "followee"),
        inst.column_id("follow", "follower"),
    )


def test_default_normalization_resolves_unquoted_mixed_case_table_lookup():
    inst = Instance(
        "CREATE TABLE Team (ID INT PRIMARY KEY, Name TEXT);",
        name="db",
        dialect="sqlite",
    )

    team_id = inst.table_id("team")
    assert team_id == inst.table_id("Team")
    assert inst.column_id("team", "id").relation == team_id


def test_default_normalization_resolves_bird_mixed_case_table_lookup():
    schemas = json.loads(Path("data/sqlite/schema.json").read_text())
    inst = Instance(
        ";".join(schemas["european_football_2"]),
        name="db",
        dialect="sqlite",
    )

    assert inst.table_id("team") == inst.table_id("Team")
    assert inst.column_id("team", "id").relation == inst.table_id("team")


def test_normalize_false_mixed_case_inline_primary_key_identity_metadata():
    inst = Instance(
        "CREATE TABLE Users (ID INT PRIMARY KEY, Name TEXT);",
        name="db",
        dialect="sqlite",
        normalize=False,
    )
    table = inst.schema_spec.get_table("Users")
    column = table.get_column("ID")
    assert table.id == inst.table_id("Users")
    assert column.id == inst.column_id("Users", "ID")
    assert column.table_id == table.id
    assert table.primary_key_ids == (inst.column_id("Users", "ID"),)
    assert column.primary_key is True
    assert inst.catalog_column("Users", "ID").primary_key is True
    assert inst.catalog_column("Users", "ID").nullable is False
    assert inst.catalog_column("Users", "ID").unique is True


def test_normalize_false_mixed_case_table_primary_key_identity_metadata():
    inst = Instance(
        "CREATE TABLE Users (ID INT, Name TEXT, PRIMARY KEY(ID));",
        name="db",
        dialect="sqlite",
        normalize=False,
    )
    table = inst.schema_spec.get_table("Users")
    assert table.id == inst.table_id("Users")
    assert table.primary_key_ids == (inst.column_id("Users", "ID"),)
    assert table.get_column("ID").primary_key is True
    assert inst.catalog_column("Users", "ID").primary_key is True
    assert inst.catalog_column("Users", "ID").nullable is False
    assert inst.catalog_column("Users", "ID").unique is True


def test_normalize_false_mixed_case_table_unique_identity_metadata():
    inst = Instance(
        "CREATE TABLE Users (ID INT, Email TEXT, UNIQUE(Email));",
        name="db",
        dialect="sqlite",
        normalize=False,
    )
    table = inst.schema_spec.get_table("Users")
    assert table.unique_constraint_ids == ((inst.column_id("Users", "Email"),),)
    assert inst.catalog_column("Users", "Email").unique is True
    assert table.get_column("Email").unique is True


def test_normalize_false_mixed_case_foreign_key_identity_metadata():
    ddl = """
    CREATE TABLE Users (ID INT PRIMARY KEY);
    CREATE TABLE Orders (UserID INT REFERENCES Users(ID));
    """
    inst = Instance(ddl, name="db", dialect="sqlite", normalize=False)
    orders = inst.schema_spec.get_table("Orders")
    fk = orders.foreign_keys[0]
    assert fk.source_table_id == inst.table_id("Orders")
    assert fk.target_table_id == inst.table_id("Users")
    assert fk.source_column_ids == (inst.column_id("Orders", "UserID"),)
    assert fk.target_column_ids == (inst.column_id("Users", "ID"),)
    assert orders.get_column("UserID").foreign_key == fk


def test_default_normalization_resolves_mixed_case_inline_foreign_key_reference():
    ddl = """
    CREATE TABLE Users (ID INT PRIMARY KEY);
    CREATE TABLE Orders (UserID INT REFERENCES users(id));
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    orders = inst.schema_spec.get_table("orders")
    fk = orders.foreign_keys[0]
    assert fk.source_table_id == inst.table_id("orders")
    assert fk.target_table_id == inst.table_id("users")
    assert fk.source_column_ids == (inst.column_id("orders", "userid"),)
    assert fk.target_column_ids == (inst.column_id("users", "id"),)


def test_default_normalization_resolves_mixed_case_composite_foreign_key_reference():
    ddl = """
    CREATE TABLE Parent (A INT, B INT, PRIMARY KEY(A, B));
    CREATE TABLE Child (A INT, B INT, FOREIGN KEY (A, B) REFERENCES parent);
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    child = inst.schema_spec.get_table("child")
    fk = child.foreign_keys[0]
    assert fk.source_table_id == inst.table_id("child")
    assert fk.target_table_id == inst.table_id("parent")
    assert fk.source_column_ids == (
        inst.column_id("child", "a"),
        inst.column_id("child", "b"),
    )
    assert fk.target_column_ids == (
        inst.column_id("parent", "a"),
        inst.column_id("parent", "b"),
    )


def test_quoted_mixed_case_catalog_identities_preserve_identifier_metadata():
    inst = Instance(
        'CREATE TABLE "Users" ("ID" INT PRIMARY KEY, "Email" TEXT UNIQUE);',
        name="db",
        dialect="sqlite",
    )
    table = exp.to_table('"Users"')
    id_name = exp.Identifier(this="ID", quoted=True)
    email_name = exp.Identifier(this="Email", quoted=True)

    users_id = inst.table_id(table)
    id_col = inst.column_id(table, id_name)
    email_col = inst.column_id(table, email_name)

    assert users_id.name.raw == "Users"
    # SQLite normalizes identifiers to lowercase even when quoted.
    assert users_id.name.normalized == "users"
    assert users_id.name.quoted is True
    assert id_col.name.raw == "ID"
    assert id_col.name.normalized == "id"
    assert id_col.name.quoted is True
    assert email_col.name.raw == "Email"
    assert email_col.name.normalized == "email"
    assert email_col.name.quoted is True
    assert inst.catalog_column(table, id_name).primary_key is True
    assert inst.catalog_column(table, email_name).unique is True


def test_unquoted_lookup_resolves_quoted_identifier_in_case_insensitive_dialect():
    """SQLite is case-insensitive, so unquoted 'id' resolves to quoted '\"ID\"'."""
    inst = Instance(
        'CREATE TABLE "Users" ("ID" INT PRIMARY KEY);',
        name="db",
        dialect="sqlite",
    )

    assert inst.table_id("users") == inst.table_id(exp.to_table('"Users"'))

    # In SQLite, identifiers are case-insensitive, so 'id' resolves to '"ID"'.
    col = inst.column_id(exp.to_table('"Users"'), "id")
    assert col is not None


def test_unquoted_foreign_key_reference_links_to_quoted_declared_table():
    ddl = """
    CREATE TABLE "Team" ("id" INT PRIMARY KEY);
    CREATE TABLE "Match" (
        "team_id" INT,
        FOREIGN KEY ("team_id") REFERENCES Team(id)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    team = exp.to_table('"Team"')
    match = exp.to_table('"Match"')
    team_id = exp.Identifier(this="id", quoted=True)
    match_team_id = exp.Identifier(this="team_id", quoted=True)
    fk = inst.schema_spec.get_table("Match").foreign_keys[0]

    assert fk.source_table_id == inst.table_id(match)
    assert fk.target_table_id == inst.table_id(team)
    assert fk.source_column_ids == (inst.column_id(match, match_team_id),)
    assert fk.target_column_ids == (inst.column_id(team, team_id),)

    assert inst.table_id("team") == inst.table_id(team)


def test_qualified_tables_with_same_leaf_name_have_distinct_relation_ids():
    inst = Instance(
        "CREATE TABLE main.users (id INT); CREATE TABLE aux.users (id INT);",
        name="db",
        dialect="sqlite",
    )
    main_users = exp.to_table("main.users")
    aux_users = exp.to_table("aux.users")

    main_id = inst.table_id(main_users)
    aux_id = inst.table_id(aux_users)

    assert main_id != aux_id
    assert main_id.name.raw == "users"
    assert main_id.db.raw == "main"
    assert aux_id.name.raw == "users"
    assert aux_id.db.raw == "aux"
    assert inst.column_id(main_users, "id").relation == main_id
    assert inst.column_id(aux_users, "id").relation == aux_id


def test_inline_primary_key_and_unique_constraints_populate_catalog_indexes():
    inst = Instance(
        "CREATE TABLE users (id INT PRIMARY KEY, email TEXT UNIQUE);",
        name="db",
        dialect="sqlite",
    )
    assert tuple(pk.name for pk in inst.get_primary_key("users")) == ("id",)
    table = inst.schema_spec.get_table("users")
    assert table.primary_key_ids == (inst.column_id("users", "id"),)
    assert table.unique_constraint_ids == ((inst.column_id("users", "email"),),)


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


def test_implicit_composite_foreign_key_uses_parent_primary_key_order():
    ddl = """
    CREATE TABLE parent (a INT, b INT, PRIMARY KEY(a, b));
    CREATE TABLE child (a INT, b INT, FOREIGN KEY (a, b) REFERENCES parent);
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    child = inst.schema_spec.get_table("child")
    fk = child.foreign_keys[0]
    assert fk.source_column_ids == (
        inst.column_id("child", "a"),
        inst.column_id("child", "b"),
    )
    assert fk.target_column_ids == (
        inst.column_id("parent", "a"),
        inst.column_id("parent", "b"),
    )


def test_constraints_are_resolved_by_column_and_relation_ids():
    ddl = """
    CREATE TABLE main.users (
        id INT PRIMARY KEY,
        email TEXT UNIQUE,
        score INT NOT NULL CHECK (score > 0)
    );
    CREATE TABLE aux.users (
        id INT PRIMARY KEY,
        email TEXT UNIQUE,
        score INT CHECK (score > 10)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    main_users = exp.to_table("main.users")
    aux_users = exp.to_table("aux.users")
    main_score_id = inst.column_id(main_users, "score")
    aux_score_id = inst.column_id(aux_users, "score")

    assert inst.get_primary_key_ids(main_users) == (inst.column_id(main_users, "id"),)
    assert inst.get_primary_key_ids(aux_users) == (inst.column_id(aux_users, "id"),)
    assert inst.get_unique_constraint_ids(main_users) == (
        (inst.column_id(main_users, "email"),),
    )
    assert inst.get_unique_constraint_ids(aux_users) == (
        (inst.column_id(aux_users, "email"),),
    )

    main_constraints = inst.get_column_constraints_by_id(main_score_id)
    aux_constraints = inst.get_column_constraints_by_id(aux_score_id)
    assert any(
        isinstance(constraint.kind, exp.NotNullColumnConstraint)
        for constraint in main_constraints
    )
    assert not any(
        isinstance(constraint.kind, exp.NotNullColumnConstraint)
        for constraint in aux_constraints
    )
    main_checks = [
        constraint.kind.this.sql()
        for constraint in main_constraints
        if isinstance(constraint.kind, exp.CheckColumnConstraint)
    ]
    aux_checks = [
        constraint.kind.this.sql()
        for constraint in aux_constraints
        if isinstance(constraint.kind, exp.CheckColumnConstraint)
    ]
    assert main_checks == ["score > 0"]
    assert aux_checks == ["score > 10"]


def test_table_level_check_constraints_are_resolved_by_relation_id():
    ddl = """
    CREATE TABLE follow (
        followee INT,
        follower INT,
        CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")

    checks = inst.get_check_constraints(inst.table_id("follow"))
    db_constraints = inst.database_constraints(inst.table_id("follow"))

    assert [check.sql(dialect="sqlite") for check in checks] == [
        "followee <> follower"
    ]
    assert [column.name.normalized for column in db_constraints.checks[0].referenced_columns] == [
        "followee",
        "follower",
    ]
    assert db_constraints.checks[0].supported is True


def test_database_constraints_exposes_normalized_relation_constraints():
    ddl = """
    CREATE TABLE parent (
        a INT,
        b INT,
        PRIMARY KEY (a, b),
        UNIQUE (b)
    );
    CREATE TABLE child (
        a INT NOT NULL CHECK (a > 0),
        b INT,
        CONSTRAINT child_pair CHECK (a <> b),
        FOREIGN KEY (a, b) REFERENCES parent(a, b)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")

    parent = inst.database_constraints(inst.table_id("parent"))
    child = inst.database_constraints(inst.table_id("child"))

    assert parent.primary_key == (
        inst.column_id("parent", "a"),
        inst.column_id("parent", "b"),
    )
    assert parent.unique_constraints == ((inst.column_id("parent", "b"),),)
    assert child.not_null_columns == (inst.column_id("child", "a"),)
    assert child.foreign_keys[0].source_column_ids == (
        inst.column_id("child", "a"),
        inst.column_id("child", "b"),
    )
    assert [check.expression.sql(dialect="sqlite") for check in child.checks] == [
        "a <> b",
        "a > 0",
    ]
    assert all(check.supported for check in child.checks)


def test_foreign_keys_are_resolved_by_relation_ids():
    ddl = """
    CREATE TABLE main.users (id INT PRIMARY KEY);
    CREATE TABLE main.orders (
        id INT PRIMARY KEY,
        user_id INT,
        FOREIGN KEY (user_id) REFERENCES main.users(id)
    );
    CREATE TABLE aux.users (id INT PRIMARY KEY);
    CREATE TABLE aux.orders (
        id INT PRIMARY KEY,
        user_id INT,
        FOREIGN KEY (user_id) REFERENCES aux.users(id)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    main_orders = exp.to_table("main.orders")
    aux_orders = exp.to_table("aux.orders")
    main_fk = inst.get_foreign_keys_by_relation_id(inst.table_id(main_orders))[0]
    aux_fk = inst.get_foreign_keys_by_relation_id(inst.table_id(aux_orders))[0]

    assert main_fk.source_table_id == inst.table_id(main_orders)
    assert main_fk.target_table_id == inst.table_id(exp.to_table("main.users"))
    assert main_fk.source_column_ids == (inst.column_id(main_orders, "user_id"),)
    assert main_fk.target_column_ids == (
        inst.column_id(exp.to_table("main.users"), "id"),
    )
    assert aux_fk.source_table_id == inst.table_id(aux_orders)
    assert aux_fk.target_table_id == inst.table_id(exp.to_table("aux.users"))
    assert aux_fk.source_column_ids == (inst.column_id(aux_orders, "user_id"),)
    assert aux_fk.target_column_ids == (
        inst.column_id(exp.to_table("aux.users"), "id"),
    )
