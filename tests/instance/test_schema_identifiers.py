"""Tests for InstanceSchema dialect-aware sqlglot identifier keys."""

from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.instance import Instance, InstanceSchema, normalize_identifier


class TestDialectIdentifierEquality(unittest.TestCase):
    def test_sqlite_folds_unquoted_mixed_case(self):
        schema = InstanceSchema.from_ddl(
            "CREATE TABLE Users (ID INT PRIMARY KEY, Name TEXT);",
            dialect="sqlite",
        )
        self.assertEqual(schema.resolve_table("users"), schema.resolve_table("Users"))
        self.assertEqual(
            schema.resolve_column("users", "id"),
            schema.resolve_column("Users", "ID"),
        )

    def test_sqlite_folds_quoted_identifiers(self):
        schema = InstanceSchema.from_ddl(
            'CREATE TABLE "Users" ("ID" INT PRIMARY KEY);',
            dialect="sqlite",
        )
        table = schema.resolve_table('"Users"')
        self.assertEqual(table_key_name(table), "users")
        col = schema.resolve_column(table, exp.Identifier(this="ID", quoted=True))
        self.assertEqual(col.name, "id")

    def test_postgres_preserves_quoted_case(self):
        schema = InstanceSchema.from_ddl(
            'CREATE TABLE "Users" ("ID" INT PRIMARY KEY);',
            dialect="postgres",
        )
        # Unquoted lookup uses postgres folding (lower); quoted table stays distinct.
        users = schema.resolve_table(exp.to_table('"Users"'))
        self.assertEqual(users.this.name, "Users")
        self.assertTrue(users.this.quoted)
        col = schema.resolve_column(users, exp.Identifier(this="ID", quoted=True))
        self.assertEqual(col.name, "ID")

    def test_mysql_unquoted_lookup(self):
        schema = InstanceSchema.from_ddl(
            "CREATE TABLE Users (ID INT PRIMARY KEY);",
            dialect="mysql",
        )
        self.assertEqual(schema.resolve_table("users"), schema.resolve_table("Users"))

    def test_constraints_keyed_by_sqlglot_not_strings(self):
        schema = InstanceSchema.from_ddl(
            """
            CREATE TABLE parent (id INT PRIMARY KEY);
            CREATE TABLE child (
                id INT PRIMARY KEY,
                parent_id INT,
                FOREIGN KEY (parent_id) REFERENCES parent(id)
            );
            """,
            dialect="sqlite",
        )
        child = schema.resolve_table("child")
        self.assertIsInstance(child, exp.Table)
        constraints = schema.database_constraints(child)
        self.assertEqual(len(constraints.foreign_keys), 1)
        self.assertEqual(
            constraints.foreign_keys[0].target_table,
            schema.resolve_table("parent"),
        )

    def test_normalize_identifier_helper(self):
        ident = normalize_identifier("Foo", "sqlite")
        self.assertEqual(ident.name, "foo")
        quoted = normalize_identifier(
            exp.Identifier(this="Foo", quoted=True), "sqlite"
        )
        self.assertEqual(quoted.name, "foo")


class TestInstanceStableApi(unittest.TestCase):
    def test_no_catalog_base(self):
        from parseval.instance.core import Instance as Inst

        self.assertFalse(any(base.__name__ == "Catalog" for base in Inst.__mro__))

    def test_create_row_checkpoint_snapshot(self):
        inst = Instance(
            "CREATE TABLE t (id INT PRIMARY KEY, name TEXT);",
            name="stable",
            dialect="sqlite",
        )
        inst.create_row("t", {"id": 1, "name": "a"})
        cp = inst.checkpoint()
        inst.create_row("t", {"id": 2, "name": "b"})
        inst.rollback(cp)
        self.assertEqual(len(inst.get_rows("t")), 1)
        snap = inst.snapshot()
        self.assertEqual(snap.tables[0].table_name, "t")
        self.assertEqual(snap.tables[0].rows[0]["id"], 1)
        self.assertIsInstance(inst.schema.resolve_table("t"), exp.Table)


def table_key_name(table: exp.Table) -> str:
    return table.this.name


if __name__ == "__main__":
    unittest.main()
