"""DomainGenerator unit tests over InstanceSchema."""

from __future__ import annotations

import unittest

from parseval.domain import DomainGenerator, compile_column
from parseval.instance.schema import InstanceSchema


class TestCompileColumn(unittest.TestCase):
    def test_enum_allowed_values(self):
        schema = InstanceSchema.from_ddl(
            "CREATE TABLE t (status VARCHAR(10));",
            dialect="sqlite",
        )
        # SQLite has no native ENUM; use mysql-style via datatype expressions if present.
        schema = InstanceSchema.from_ddl(
            "CREATE TABLE t (id INT PRIMARY KEY, kind TEXT);",
            dialect="sqlite",
        )
        col = schema.get_table("t").columns[schema.resolve_column("t", "id")]
        plan = compile_column(col, dialect="sqlite", unique=True)
        space = plan.to_value_space()
        self.assertTrue(space.not_null)
        self.assertIsNotNone(space.pick())


class TestDomainGenerator(unittest.TestCase):
    def test_complete_row_respects_presets_and_uniques(self):
        schema = InstanceSchema.from_ddl(
            "CREATE TABLE t (id INT PRIMARY KEY, name TEXT);",
            dialect="sqlite",
        )
        gen = DomainGenerator(schema, seed=1)
        id_col = schema.resolve_column("t", "id")
        name_col = schema.resolve_column("t", "name")
        row1 = gen.complete_row("t", presets={"id": 1, "name": "a"})
        row2 = gen.complete_row(
            "t",
            presets={"name": "b"},
            existing_rows=[row1],
            locked={"name"},
        )
        self.assertEqual(row1[id_col], 1)
        self.assertNotEqual(row2[id_col], 1)
        self.assertIn(id_col, row1)
        self.assertIn(name_col, row1)

    def test_locked_unique_preset_raises(self):
        """Only locked colliding presets are UniqueConflictError — generation otherwise invents."""
        from parseval.domain.exceptions import UniqueConflictError

        schema = InstanceSchema.from_ddl(
            "CREATE TABLE t (id INT PRIMARY KEY, name TEXT);",
            dialect="sqlite",
        )
        gen = DomainGenerator(schema, seed=1)
        row1 = gen.complete_row("t", presets={"id": 1, "name": "a"})
        with self.assertRaises(UniqueConflictError):
            gen.complete_row(
                "t",
                presets={"id": 1, "name": "b"},
                existing_rows=[row1],
                locked={"id", "name"},
            )

    def test_composite_pk_avoids_collision(self):
        schema = InstanceSchema.from_ddl(
            """
            CREATE TABLE child (
                a_id INT NOT NULL,
                b_id INT NOT NULL,
                seq INT NOT NULL,
                PRIMARY KEY (a_id, b_id, seq)
            );
            """,
            dialect="sqlite",
        )
        gen = DomainGenerator(schema, seed=2)
        a = schema.resolve_column("child", "a_id")
        b = schema.resolve_column("child", "b_id")
        seq = schema.resolve_column("child", "seq")
        row1 = gen.complete_row("child", presets={"a_id": 1, "b_id": 1, "seq": 1})
        row2 = gen.complete_row(
            "child",
            presets={"a_id": 1, "b_id": 1},
            existing_rows=[row1],
            locked={"a_id", "b_id"},
        )
        self.assertNotEqual(
            (row1[a], row1[b], row1[seq]),
            (row2[a], row2[b], row2[seq]),
        )
        self.assertEqual(row2[a], 1)
        self.assertEqual(row2[b], 1)

    def test_fk_bind_from_parents(self):
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
        gen = DomainGenerator(schema, seed=3)
        parent_table = schema.resolve_table("parent")
        parent_id = schema.resolve_column("child", "parent_id")
        parent = gen.complete_row("parent", presets={"id": 10})
        child = gen.complete_row(
            "child",
            presets={"id": 1},
            parent_rows={parent_table: [parent]},
            locked={"id"},
        )
        self.assertEqual(child[parent_id], 10)

    def test_fk_requires_parent_rows(self):
        from parseval.domain.exceptions import ForeignKeyResolutionError

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
        gen = DomainGenerator(schema, seed=3)
        with self.assertRaises(ForeignKeyResolutionError):
            gen.complete_row("child", presets={"id": 1}, locked={"id"})

    def test_fk_rejects_dangling_preset(self):
        from parseval.domain.exceptions import ForeignKeyResolutionError

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
        gen = DomainGenerator(schema, seed=3)
        parent_table = schema.resolve_table("parent")
        parent = gen.complete_row("parent", presets={"id": 10})
        with self.assertRaises(ForeignKeyResolutionError):
            gen.complete_row(
                "child",
                presets={"id": 1, "parent_id": 999},
                parent_rows={parent_table: [parent]},
                locked={"id", "parent_id"},
            )

    def test_fk_rejects_dangling_when_parent_rows_empty(self):
        """Fail closed: preset FK with no parent maps is dangling."""
        from parseval.domain.exceptions import ForeignKeyResolutionError

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
        gen = DomainGenerator(schema, seed=3)
        with self.assertRaises(ForeignKeyResolutionError):
            gen.complete_row(
                "child",
                presets={"id": 1, "parent_id": 999},
                parent_rows={},
                locked={"id", "parent_id"},
            )

    def test_compile_table_descriptors(self):
        from parseval.domain import compile_table

        schema = InstanceSchema.from_ddl(
            """
            CREATE TABLE parent (id INT PRIMARY KEY);
            CREATE TABLE child (
                id INT PRIMARY KEY,
                parent_id INT NOT NULL,
                FOREIGN KEY (parent_id) REFERENCES parent(id),
                CHECK (id > 0)
            );
            """,
            dialect="sqlite",
        )
        desc = compile_table(schema.get_table("child"))
        self.assertEqual(desc.table, "child")
        self.assertIn(("id",), desc.uniqueness_groups)
        self.assertEqual(len(desc.foreign_keys), 1)
        self.assertEqual(desc.foreign_keys[0].target_table, "parent")
        self.assertEqual(desc.foreign_keys[0].source_columns, ("parent_id",))
        self.assertTrue(any(c.supported for c in desc.checks))

    def test_check_constraint(self):
        schema = InstanceSchema.from_ddl(
            """
            CREATE TABLE follow (
                followee TEXT NOT NULL,
                follower TEXT NOT NULL,
                CONSTRAINT check_follow CHECK (followee <> follower)
            );
            """,
            dialect="sqlite",
        )
        gen = DomainGenerator(schema, seed=4)
        with self.assertRaises(Exception):
            gen.complete_row(
                "follow",
                presets={"followee": "A", "follower": "A"},
                locked={"followee", "follower"},
            )

    def test_empty_domain_raises_not_fallback(self):
        """ValueSpace.pick() failure must not invent out-of-domain values."""
        from parseval.domain.exceptions import ConstraintConflict

        schema = InstanceSchema.from_ddl(
            "CREATE TABLE t (flag BOOLEAN NOT NULL);",
            dialect="sqlite",
        )
        gen = DomainGenerator(schema, seed=1)
        with self.assertRaises(ConstraintConflict):
            gen.next_value("t", "flag", avoid=(True, False, 0, 1))

    def test_text_defaults_differ_by_column_name(self):
        """Unconstrained TEXT must not collapse to a global 'value' default."""
        schema = InstanceSchema.from_ddl(
            """
            CREATE TABLE t (
                bioguide_id TEXT NOT NULL,
                tmid TEXT NOT NULL
            );
            """,
            dialect="sqlite",
        )
        gen = DomainGenerator(schema, seed=1)
        row = gen.complete_row("t")
        bio = row[schema.resolve_column("t", "bioguide_id")]
        tmid = row[schema.resolve_column("t", "tmid")]
        self.assertNotEqual(bio, "value")
        self.assertNotEqual(tmid, "value")
        self.assertNotEqual(bio, tmid)
        self.assertNotEqual(
            gen.next_value("t", "bioguide_id"),
            gen.next_value("t", "tmid"),
        )

    def test_composite_pk_with_small_enum_does_not_exhaust(self):
        """Saturated enum suffix must freshen earlier PK columns, not empty_value_space."""
        schema = InstanceSchema.from_ddl(
            """
            CREATE TABLE activity (
                machine_id INT NOT NULL,
                process_id INT NOT NULL,
                activity_type ENUM('start', 'end') NOT NULL,
                PRIMARY KEY (machine_id, process_id, activity_type)
            );
            """,
            dialect="mysql",
        )
        gen = DomainGenerator(schema, seed=1)
        rows = []
        for _ in range(5):
            row = gen.complete_row("activity", existing_rows=rows)
            rows.append(row)
        keys = {
            (
                r[schema.resolve_column("activity", "machine_id")],
                r[schema.resolve_column("activity", "process_id")],
                r[schema.resolve_column("activity", "activity_type")],
            )
            for r in rows
        }
        self.assertEqual(len(keys), 5)

    def test_fk_composite_pk_with_enum_picks_unsaturated_parent(self):
        """FK-bound PK columns must not lock a prefix that exhausts a finite enum."""
        schema = InstanceSchema.from_ddl(
            """
            CREATE TABLE variables (
                name VARCHAR(255) PRIMARY KEY,
                value INT
            );
            CREATE TABLE expressions (
                left_operand VARCHAR(255),
                operator ENUM('<', '>', '=') NOT NULL,
                right_operand VARCHAR(255),
                PRIMARY KEY (left_operand, operator, right_operand),
                FOREIGN KEY (left_operand) REFERENCES variables(name),
                FOREIGN KEY (right_operand) REFERENCES variables(name)
            );
            """,
            dialect="mysql",
        )
        gen = DomainGenerator(schema, seed=1)
        parents = [
            gen.complete_row("variables", presets={"name": "a", "value": 1}),
            gen.complete_row("variables", presets={"name": "b", "value": 2}),
        ]
        parent_table = schema.resolve_table("variables")
        rows = []
        for _ in range(5):
            row = gen.complete_row(
                "expressions",
                existing_rows=rows,
                parent_rows={parent_table: parents},
            )
            rows.append(row)
        self.assertEqual(len(rows), 5)


if __name__ == "__main__":
    unittest.main()
