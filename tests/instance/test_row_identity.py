import sqlglot

from parseval.identity import PARSEVAL_COLUMN_ID
from parseval.instance import Instance


def test_create_row_stores_cells_by_column_id():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);", name="db", dialect="sqlite")
    users_id = inst.table_id("users")
    id_col = inst.column_id("users", "id")
    name_col = inst.column_id("users", "name")
    result = inst.create_row(users_id, {id_col: 1, name_col: "Ada"})
    row = result.created[users_id][0]

    assert row[id_col].concrete == 1
    assert row["id"].concrete == 1


def test_exp_column_lookup_uses_resolved_column_id():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    users_id = inst.table_id("users")
    id_col = inst.column_id("users", "id")
    row = inst.create_row(users_id, {id_col: 7}).created[users_id][0]
    col = sqlglot.parse_one("SELECT id FROM users").expressions[0]
    col.meta[PARSEVAL_COLUMN_ID] = id_col

    assert row[col].concrete == 7


def test_variable_carries_relation_and_column_ids():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    users_id = inst.table_id("users")
    column_id = inst.column_id("users", "id")
    row = inst.create_row(users_id, {column_id: 1}).created[users_id][0]
    var = row[column_id]

    assert var.args["relation_id"] == users_id
    assert var.args["column_id"] == column_id


def test_symbol_index_lookup_by_identity():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY);", name="db", dialect="sqlite")
    users_id = inst.table_id("users")
    id_col = inst.column_id("users", "id")
    inst.create_row(users_id, {id_col: 1})
    cells = inst.symbols.by_column(id_col)

    assert len(cells) == 1
    assert cells[0].concrete == 1


def test_create_rows_accepts_relation_and_column_ids():
    ddl = """
    CREATE TABLE main.users (id INT PRIMARY KEY, name TEXT);
    CREATE TABLE aux.users (id INT PRIMARY KEY, name TEXT);
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    main_users = inst.table_id(sqlglot.exp.to_table("main.users"))
    aux_users = inst.table_id(sqlglot.exp.to_table("aux.users"))
    main_id = inst.column_id(sqlglot.exp.to_table("main.users"), "id")
    main_name = inst.column_id(sqlglot.exp.to_table("main.users"), "name")
    aux_id = inst.column_id(sqlglot.exp.to_table("aux.users"), "id")
    aux_name = inst.column_id(sqlglot.exp.to_table("aux.users"), "name")

    result = inst.create_rows(
        {
            main_users: {main_id: [1], main_name: ["Ada"]},
            aux_users: {aux_id: [1], aux_name: ["Grace"]},
        }
    )

    assert set(result) == {main_users, aux_users}
    assert result[main_users][0].created[main_users][0][main_name].concrete == "Ada"
    assert result[aux_users][0].created[aux_users][0][aux_name].concrete == "Grace"


def test_create_rows_empty_batch_creates_one_row_per_relation():
    inst = Instance("CREATE TABLE users (id INT PRIMARY KEY, name TEXT);", name="db", dialect="sqlite")
    users_id = inst.table_id("users")
    id_col = inst.column_id("users", "id")

    result = inst.create_rows({users_id: {}})

    assert list(result) == [users_id]
    assert len(result[users_id]) == 1
    assert len(inst.get_rows(users_id)) == 1
    assert inst.get_rows(users_id)[0][id_col].concrete is not None


def test_create_rows_accepts_sparse_row_batches_and_returns_completed_rows():
    ddl = """
    CREATE TABLE parent (
        id INT PRIMARY KEY,
        label TEXT NOT NULL
    );
    CREATE TABLE child (
        id INT PRIMARY KEY,
        parent_id INT NOT NULL,
        note TEXT NOT NULL,
        FOREIGN KEY (parent_id) REFERENCES parent(id)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")

    result = inst.create_rows(
        {
            "parent": [{"id": 7}],
            "child": [{"id": 11, "parent_id": 7}],
        }
    )

    parent_id = inst.table_id("parent")
    child_id = inst.table_id("child")
    parent_label = inst.column_id("parent", "label")
    child_note = inst.column_id("child", "note")

    assert set(result) == {parent_id, child_id}
    assert result[parent_id][0].created[parent_id][0][parent_label].concrete is not None
    assert result[child_id][0].created[child_id][0][child_note].concrete is not None


def test_create_row_completion_respects_table_level_check():
    ddl = """
    CREATE TABLE follow (
        followee TEXT NOT NULL,
        follower TEXT NOT NULL,
        CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    follow_id = inst.table_id("follow")
    followee = inst.column_id(follow_id, "followee")
    follower = inst.column_id(follow_id, "follower")

    row = inst.create_row(follow_id, {follower: "A"}).created[follow_id][0]

    assert row[followee].concrete != "A"
    assert row[follower].concrete == "A"


def test_create_row_rejects_explicit_table_level_check_violation():
    ddl = """
    CREATE TABLE follow (
        followee TEXT NOT NULL,
        follower TEXT NOT NULL,
        CONSTRAINT check_follow CHECK (followee <> follower)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    follow_id = inst.table_id("follow")
    followee = inst.column_id(follow_id, "followee")
    follower = inst.column_id(follow_id, "follower")

    try:
        inst.create_row(follow_id, {followee: "A", follower: "A"})
    except Exception as exc:
        assert "check" in str(exc).lower()
    else:
        raise AssertionError("CHECK-violating row was accepted")


def test_create_row_preserves_non_nullable_cyclic_foreign_keys():
    ddl = """
    CREATE TABLE a (
        id INT PRIMARY KEY,
        b_id INT NOT NULL,
        FOREIGN KEY (b_id) REFERENCES b(id)
    );
    CREATE TABLE b (
        id INT PRIMARY KEY,
        a_id INT NOT NULL,
        FOREIGN KEY (a_id) REFERENCES a(id)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    a_id = inst.table_id("a")
    b_id = inst.table_id("b")
    a_pk = inst.column_id(a_id, "id")
    a_b_id = inst.column_id(a_id, "b_id")
    b_pk = inst.column_id(b_id, "id")
    b_a_id = inst.column_id(b_id, "a_id")

    inst.create_row(a_id, {})
    a_row = inst.get_rows(a_id)[0]
    b_row = inst.get_rows(b_id)[0]

    assert a_row[a_b_id].concrete == b_row[b_pk].concrete
    assert b_row[b_a_id].concrete == a_row[a_pk].concrete


def test_create_row_preserves_composite_cyclic_foreign_keys():
    ddl = """
    CREATE TABLE a (
        id1 INT NOT NULL,
        id2 INT NOT NULL,
        b1 INT NOT NULL,
        b2 INT NOT NULL,
        PRIMARY KEY (id1, id2),
        FOREIGN KEY (b1, b2) REFERENCES b(id1, id2)
    );
    CREATE TABLE b (
        id1 INT NOT NULL,
        id2 INT NOT NULL,
        a1 INT NOT NULL,
        a2 INT NOT NULL,
        PRIMARY KEY (id1, id2),
        FOREIGN KEY (a1, a2) REFERENCES a(id1, id2)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    a_id = inst.table_id("a")
    b_id = inst.table_id("b")
    a_id1 = inst.column_id(a_id, "id1")
    a_id2 = inst.column_id(a_id, "id2")
    a_b1 = inst.column_id(a_id, "b1")
    a_b2 = inst.column_id(a_id, "b2")
    b_id1 = inst.column_id(b_id, "id1")
    b_id2 = inst.column_id(b_id, "id2")
    b_a1 = inst.column_id(b_id, "a1")
    b_a2 = inst.column_id(b_id, "a2")

    inst.create_row(a_id, {})
    a_row = inst.get_rows(a_id)[0]
    b_row = inst.get_rows(b_id)[0]

    assert (a_row[a_b1].concrete, a_row[a_b2].concrete) == (
        b_row[b_id1].concrete,
        b_row[b_id2].concrete,
    )
    assert (b_row[b_a1].concrete, b_row[b_a2].concrete) == (
        a_row[a_id1].concrete,
        a_row[a_id2].concrete,
    )


def test_duplicate_foreign_key_bootstrap_uses_storage_equivalent_parent_value():
    ddl = """
    CREATE TABLE parent (
        id TEXT PRIMARY KEY
    );
    CREATE TABLE child (
        id INT PRIMARY KEY,
        parent_id INT,
        FOREIGN KEY (parent_id) REFERENCES parent(id),
        FOREIGN KEY (parent_id) REFERENCES parent(id)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    child_id = inst.table_id("child")
    parent_id = inst.column_id("child", "parent_id")

    inst.create_row(child_id, {parent_id: 1})

    parent_rows = inst.get_rows(inst.table_id("parent"))
    assert len(parent_rows) == 1
    assert parent_rows[0][inst.column_id("parent", "id")].concrete == "1"


def test_composite_primary_key_lookup_uses_storage_equivalent_values():
    ddl = """
    CREATE TABLE yearmonth (
        CustomerID INTEGER NOT NULL,
        Date TEXT NOT NULL,
        Consumption REAL,
        PRIMARY KEY (Date, CustomerID)
    );
    """
    inst = Instance(ddl, name="db", dialect="sqlite")
    relation = inst.table_id("yearmonth")
    customer_id = inst.column_id(relation, "CustomerID")
    date = inst.column_id(relation, "Date")

    first = inst.create_row(relation, {customer_id: 38508, date: 1})
    second = inst.create_row(relation, {customer_id: 38508, date: 1})

    assert first.positions[relation] == second.positions[relation]
    assert len(inst.get_rows(relation)) == 1
    assert inst.get_rows(relation)[0][date].concrete == "1"


def test_mysql_text_primary_key_lookup_is_case_insensitive():
    inst = Instance("CREATE TABLE variables (name VARCHAR(10) PRIMARY KEY);", name="db", dialect="mysql")
    relation = inst.table_id("variables")
    name = inst.column_id(relation, "name")

    first = inst.create_row(relation, {name: "C"})
    second = inst.create_row(relation, {name: "c"})

    assert first.positions[relation] == second.positions[relation]
    assert len(inst.get_rows(relation)) == 1


def test_create_rows_rejects_invalid_mysql_enum_before_materialization():
    inst = Instance(
        "CREATE TABLE expressions (operator ENUM('<','>','=') NOT NULL);",
        name="db",
        dialect="mysql",
    )
    relation = inst.table_id("expressions")

    try:
        inst.create_rows({relation: [{"operator": "x"}]})
    except Exception as exc:
        assert "allowed" in str(exc).lower() or "enum" in str(exc).lower()
    else:
        raise AssertionError("invalid enum value was accepted")


def test_mysql_enum_storage_value_rejects_invalid_value():
    inst = Instance(
        "CREATE TABLE expressions (operator ENUM('<','>','=') NOT NULL);",
        name="db",
        dialect="mysql",
    )
    relation = inst.table_id("expressions")
    operator = inst.column_id(relation, "operator")

    try:
        inst._column_storage_value(relation, operator, "x")
    except ValueError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("invalid enum storage value was accepted")


def test_composite_primary_key_containing_enum_validates_and_uses_storage_key():
    inst = Instance(
        """
        CREATE TABLE expressions (
            name VARCHAR(10) NOT NULL,
            operator ENUM('<','>','=') NOT NULL,
            PRIMARY KEY (name, operator)
        );
        """,
        name="db",
        dialect="mysql",
    )
    relation = inst.table_id("expressions")

    first = inst.create_row(relation, {"name": "C", "operator": "<"})
    second = inst.create_row(relation, {"name": "c", "operator": "<"})

    assert first.positions[relation] == second.positions[relation]
    assert len(inst.get_rows(relation)) == 1
