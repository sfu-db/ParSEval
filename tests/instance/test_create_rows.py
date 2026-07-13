"""create_rows: domain-backed full rows, Instance/Domain boundary."""

from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.instance import Instance
from parseval.instance.core import Instance as InstanceCore
from parseval.instance.schema import table_key


DDL = """
CREATE TABLE parent (
    id INT PRIMARY KEY,
    name TEXT NOT NULL
);
CREATE TABLE child (
    id INT PRIMARY KEY,
    parent_id INT NOT NULL,
    label TEXT,
    FOREIGN KEY (parent_id) REFERENCES parent(id)
);
CREATE TABLE item (
    a_id INT NOT NULL,
    b_id INT NOT NULL,
    seq INT NOT NULL,
    PRIMARY KEY (a_id, b_id, seq)
);
"""


class TestCreateRowsEmptyPayload(unittest.TestCase):
    def test_empty_mapping_payload_generates_one_full_row(self):
        inst = Instance(ddls=DDL, name="empty_map", dialect="sqlite")
        parent = inst.resolve_table("parent")
        results = inst.create_rows({"parent": {}})
        self.assertEqual([table_key(t) for t in results], ["parent"])
        self.assertEqual(len(results[parent]), 1)
        row = inst.get_rows("parent")[0]
        self.assertIsNotNone(row["id"].concrete)
        self.assertIsNotNone(row["name"].concrete)
        self.assertEqual(len(inst.get_rows("child")), 0)

    def test_empty_sequence_payload_generates_one_full_row(self):
        inst = Instance(ddls=DDL, name="empty_seq", dialect="sqlite")
        parent = inst.resolve_table("parent")
        results = inst.create_rows({"parent": []})
        self.assertEqual(len(results[parent]), 1)
        self.assertEqual(len(inst.get_rows("parent")), 1)

    def test_omitted_concretes_creates_nothing(self):
        inst = Instance(ddls=DDL, name="none", dialect="sqlite")
        self.assertEqual(inst.create_rows(), {})
        self.assertEqual(inst.create_rows({}), {})
        self.assertEqual(len(inst.get_rows("parent")), 0)


class TestCreateRowsConstraints(unittest.TestCase):
    def test_child_only_creates_parent_and_binds_fk(self):
        inst = Instance(ddls=DDL, name="fk", dialect="sqlite")
        inst.create_rows({"child": {}})
        self.assertEqual(len(inst.get_rows("parent")), 1)
        self.assertEqual(len(inst.get_rows("child")), 1)
        parent_id = inst.get_rows("parent")[0]["id"].concrete
        child = inst.get_rows("child")[0]
        self.assertEqual(child["parent_id"].concrete, parent_id)
        self.assertIsNotNone(child["id"].concrete)

    def test_child_fk_preset_creates_missing_parent(self):
        """No matching parent → Instance adds a parent row with the FK key."""
        inst = Instance(ddls=DDL, name="fk_preset", dialect="sqlite")
        inst.create_rows({"child": [{"id": 1, "parent_id": 99}]})
        self.assertEqual(len(inst.get_rows("parent")), 1)
        self.assertEqual(inst.get_rows("parent")[0]["id"].concrete, 99)
        self.assertEqual(inst.get_rows("child")[0]["parent_id"].concrete, 99)

    def test_child_fk_preset_creates_parent_when_existing_do_not_match(self):
        inst = Instance(ddls=DDL, name="fk_mismatch", dialect="sqlite")
        inst.create_rows({"parent": [{"id": 1, "name": "a"}]})
        inst.create_rows({"child": [{"id": 2, "parent_id": 50}]})
        parent_ids = {r["id"].concrete for r in inst.get_rows("parent")}
        self.assertEqual(parent_ids, {1, 50})
        self.assertEqual(inst.get_rows("child")[0]["parent_id"].concrete, 50)

    def test_two_empty_parents_have_distinct_pks(self):
        inst = Instance(ddls=DDL, name="uniq", dialect="sqlite")
        inst.create_rows({"parent": {}})
        inst.create_rows({"parent": {}})
        ids = [r["id"].concrete for r in inst.get_rows("parent")]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_partial_presets_fill_missing_columns(self):
        inst = Instance(ddls=DDL, name="partial", dialect="sqlite")
        inst.create_rows({"parent": [{"id": 7}]})
        row = inst.get_rows("parent")[0]
        self.assertEqual(row["id"].concrete, 7)
        self.assertIsNotNone(row["name"].concrete)

    def test_composite_pk_avoids_collision_across_batch(self):
        inst = Instance(ddls=DDL, name="comp", dialect="sqlite")
        inst.create_rows(
            {
                "item": [
                    {"a_id": 1, "b_id": 1, "seq": 1},
                    {"a_id": 1, "b_id": 1},
                ]
            }
        )
        rows = inst.get_rows("item")
        self.assertEqual(len(rows), 2)
        keys = {
            (r["a_id"].concrete, r["b_id"].concrete, r["seq"].concrete)
            for r in rows
        }
        self.assertEqual(len(keys), 2)
        self.assertEqual(rows[0]["seq"].concrete, 1)
        self.assertNotEqual(rows[1]["seq"].concrete, 1)

    def test_create_rows_check_rejection(self):
        ddl = """
        CREATE TABLE follow (
            followee TEXT NOT NULL,
            follower TEXT NOT NULL,
            CONSTRAINT check_follow CHECK (followee <> follower)
        );
        """
        inst = Instance(ddls=ddl, name="chk", dialect="sqlite")
        with self.assertRaises(Exception):
            inst.create_rows(
                {"follow": [{"followee": "A", "follower": "A"}]}
            )

    def test_create_rows_column_oriented_batch(self):
        inst = Instance(ddls=DDL, name="cols", dialect="sqlite")
        inst.create_rows({"parent": {"id": [1, 2], "name": ["a", "b"]}})
        rows = inst.get_rows("parent")
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["id"].concrete for r in rows}, {1, 2})


class TestInstanceDomainBoundary(unittest.TestCase):
    def test_instance_has_no_value_invention_helpers(self):
        forbidden = {
            "_default_for_type",
            "_next_default_value",
            "_freshen_uniques",
        }
        methods = {name for name in dir(InstanceCore) if not name.startswith("__")}
        self.assertTrue(forbidden.isdisjoint(methods), methods & forbidden)


class TestCreateRowsDialectKeys(unittest.TestCase):
    def test_sqlite_folded_case_same_table_key(self):
        ddl = 'CREATE TABLE "Users" ("ID" INT PRIMARY KEY, name TEXT NOT NULL);'
        inst = Instance(ddls=ddl, name="fold", dialect="sqlite")
        users = inst.resolve_table("users")
        r1 = inst.create_rows({"Users": {}})
        r2 = inst.create_rows({"users": {}})
        self.assertIn(users, r1)
        self.assertIn(users, r2)
        self.assertEqual(len(inst.get_rows(users)), 2)

    def test_postgres_quoted_column_keys_preserved(self):
        from parseval.domain import DomainGenerator

        ddl = 'CREATE TABLE t ("ID" INT PRIMARY KEY, id INT NOT NULL);'
        inst = Instance(ddls=ddl, name="pg_quote", dialect="postgres")
        quoted = inst.resolve_column("t", exp.Identifier(this="ID", quoted=True))
        folded = inst.resolve_column("t", "id")
        self.assertNotEqual(quoted.name, folded.name)

        gen = DomainGenerator(inst.schema, seed=5)
        row = gen.complete_row(
            "t",
            presets={quoted: 1, folded: 2},
            locked={quoted, folded},
        )
        self.assertEqual(row[quoted], 1)
        self.assertEqual(row[folded], 2)
        self.assertEqual(set(row), {quoted, folded})

        results = inst.create_rows({"t": [{quoted: 10, folded: 20}]})
        table = inst.resolve_table("t")
        self.assertIn(table, results)
        placed = inst.get_rows(table)[0]
        self.assertEqual(placed[quoted].concrete, 10)
        self.assertEqual(placed[folded].concrete, 20)


class TestCreateRowsDualFkSameParent(unittest.TestCase):
    """Two FKs to the same parent sharing a column must stay jointly valid."""

    DDL = """
    CREATE TABLE teams (
        year INTEGER NOT NULL,
        tmid TEXT NOT NULL,
        PRIMARY KEY (year, tmid)
    );
    CREATE TABLE teamvsteam (
        year INTEGER NOT NULL,
        tmid TEXT NOT NULL,
        oppid TEXT NOT NULL,
        PRIMARY KEY (year, tmid, oppid),
        FOREIGN KEY (year, tmid) REFERENCES teams(year, tmid),
        FOREIGN KEY (oppid, year) REFERENCES teams(tmid, year)
    );
    """

    def test_shared_year_dual_fk_stays_referential(self):
        inst = Instance(ddls=self.DDL, name="dual_fk", dialect="sqlite")
        inst.create_rows(
            {
                "teams": [
                    {"year": 1, "tmid": "A"},
                    {"year": 1, "tmid": "B"},
                    {"year": 2, "tmid": "C"},
                ]
            }
        )
        for _ in range(5):
            inst.create_rows({"teamvsteam": {}})
        team_keys = {
            (r["year"].concrete, r["tmid"].concrete) for r in inst.get_rows("teams")
        }
        for row in inst.get_rows("teamvsteam"):
            self.assertIn((row["year"].concrete, row["tmid"].concrete), team_keys)
            self.assertIn((row["year"].concrete, row["oppid"].concrete), team_keys)


class TestCreateRowsCompositeFkUniqueGroup(unittest.TestCase):
    """PK over overlapping FKs must not cartesian-product composite keys."""

    DDL = """
    CREATE TABLE country (code TEXT PRIMARY KEY);
    CREATE TABLE province (
        name TEXT NOT NULL,
        country TEXT NOT NULL,
        PRIMARY KEY (name, country),
        FOREIGN KEY (country) REFERENCES country(code)
    );
    CREATE TABLE island (name TEXT PRIMARY KEY);
    CREATE TABLE geo_island (
        island TEXT NOT NULL,
        country TEXT NOT NULL,
        province TEXT NOT NULL,
        PRIMARY KEY (province, country, island),
        FOREIGN KEY (province, country) REFERENCES province(name, country),
        FOREIGN KEY (country) REFERENCES country(code),
        FOREIGN KEY (island) REFERENCES island(name)
    );
    """

    def test_overlapping_country_fk_does_not_dangle_composite(self):
        inst = Instance(ddls=self.DDL, name="composite_fk_uq", dialect="sqlite")
        inst.create_rows(
            {
                "country": [{"code": "A"}, {"code": "B"}],
                "province": [{"name": "P", "country": "A"}],
                "island": [{"name": "I"}],
            }
        )
        # Unique-group expansion must not pin province=P with country=B.
        inst.create_rows({"geo_island": {}})
        row = inst.get_rows("geo_island")[0]
        self.assertEqual(row["province"].concrete, "P")
        self.assertEqual(row["country"].concrete, "A")
        self.assertEqual(row["island"].concrete, "I")

    def test_second_row_spawns_matching_province_composite(self):
        inst = Instance(ddls=self.DDL, name="composite_fk_uq2", dialect="sqlite")
        inst.create_rows(
            {
                "country": [{"code": "A"}, {"code": "B"}],
                "province": [{"name": "P", "country": "A"}],
                "island": [{"name": "I"}],
                "geo_island": {},
            }
        )
        inst.create_rows({"geo_island": {}})
        self.assertEqual(len(inst.get_rows("geo_island")), 2)
        for row in inst.get_rows("geo_island"):
            parent_keys = {
                (p["name"].concrete, p["country"].concrete)
                for p in inst.get_rows("province")
            }
            self.assertIn(
                (row["province"].concrete, row["country"].concrete),
                parent_keys,
            )


class TestCreateRowsUniqueFkCycle(unittest.TestCase):
    """Junction unique(FK,FK) with a cyclic parent must still expand parents."""

    DDL = """
    CREATE TABLE lists (
        user_id INTEGER,
        list_id INTEGER PRIMARY KEY,
        FOREIGN KEY (user_id) REFERENCES lists_users(user_id)
    );
    CREATE TABLE lists_users (
        user_id INTEGER NOT NULL,
        list_id INTEGER NOT NULL,
        PRIMARY KEY (user_id, list_id),
        FOREIGN KEY (list_id) REFERENCES lists(list_id),
        FOREIGN KEY (user_id) REFERENCES lists(user_id)
    );
    """

    def test_batch_create_when_parent_deferred_still_gets_free_combo(self):
        inst = Instance(ddls=self.DDL, name="uq_fk_cycle", dialect="sqlite")
        # First pass fills every free (user_id, list_id) combo from existing lists.
        inst.create_rows({"lists": {}, "lists_users": {}})
        inst.create_rows({"lists": {}, "lists_users": {}})
        # Same batch order that DFS cycle ordering often yields: child before parent.
        inst.create_rows({"lists_users": {}, "lists": {}})
        self.assertGreaterEqual(len(inst.get_rows("lists_users")), 3)
        keys = {
            (r["user_id"].concrete, r["list_id"].concrete)
            for r in inst.get_rows("lists_users")
        }
        self.assertEqual(len(keys), len(inst.get_rows("lists_users")))


class TestCreateRowsSelfFk(unittest.TestCase):
    def test_second_self_fk_row_gets_new_pk(self):
        ddl = """
        CREATE TABLE employees (
            employeenumber INTEGER PRIMARY KEY,
            officecode TEXT NOT NULL,
            reportsto INTEGER,
            FOREIGN KEY (officecode) REFERENCES offices(officecode),
            FOREIGN KEY (reportsto) REFERENCES employees(employeenumber)
        );
        CREATE TABLE offices (
            officecode TEXT PRIMARY KEY
        );
        """
        inst = Instance(ddls=ddl, name="self_fk", dialect="sqlite")
        inst.create_rows({"offices": {}, "employees": {}})
        inst.create_rows({"offices": {}, "employees": {}})
        nums = [r["employeenumber"].concrete for r in inst.get_rows("employees")]
        self.assertEqual(len(nums), 2)
        self.assertEqual(len(set(nums)), 2)

    def test_second_self_fk_pk_row_gets_fresh_self_key(self):
        """PK that references itself (BIRD-style) must not reuse the first key."""
        ddl = """
        CREATE TABLE country (
            country_id INTEGER PRIMARY KEY,
            name TEXT,
            FOREIGN KEY (country_id) REFERENCES country(country_id)
        );
        """
        inst = Instance(ddls=ddl, name="self_pk", dialect="sqlite")
        inst.create_rows({"country": {}})
        inst.create_rows({"country": {}})
        ids = [r["country_id"].concrete for r in inst.get_rows("country")]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_cycle_bootstrap_keys_cleared_after_place(self):
        """Deferred cycle partner must not reuse bootstrap PK on the next row."""
        ddl = """
        CREATE TABLE characters (
            movie_title TEXT PRIMARY KEY,
            hero TEXT,
            FOREIGN KEY (hero) REFERENCES voice_actors(character)
        );
        CREATE TABLE voice_actors (
            character TEXT PRIMARY KEY,
            movie TEXT,
            FOREIGN KEY (movie) REFERENCES characters(movie_title)
        );
        """
        inst = Instance(ddls=ddl, name="cycle_clear", dialect="sqlite")
        inst.create_rows({"voice_actors": {}, "characters": {}})
        inst.create_rows({"voice_actors": {}, "characters": {}})
        titles = [r["movie_title"].concrete for r in inst.get_rows("characters")]
        self.assertEqual(len(titles), 2)
        self.assertEqual(len(set(titles)), 2)


if __name__ == "__main__":
    unittest.main()
