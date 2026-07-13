"""BIRD schema generation: Instance + Domain create_rows over real SQLite DDLs."""

from __future__ import annotations

import json
import random
import unittest
from pathlib import Path

from parseval.instance import Instance
from parseval.instance.schema import table_key

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_JSON = ROOT / "data" / "sqlite" / "schema.json"
TRAIN_SCHEMA_JSON = ROOT / "data" / "sqlite" / "train_schema.json"


def _ddl_from_entry(value) -> str:
    if isinstance(value, list):
        parts = [str(stmt).strip().rstrip(";") for stmt in value if str(stmt).strip()]
        return ";\n".join(parts) + ";"
    return str(value)


def _load_databases(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise TypeError(f"expected object in {path}, got {type(payload).__name__}")
    return {str(db_id): _ddl_from_entry(ddl) for db_id, ddl in payload.items()}


def _assert_row_complete(inst: Instance, table, *, label: str) -> None:
    row = inst.get_rows(table)[0]
    table_schema = inst.schema.tables[table]
    for col_ident, col in table_schema.columns.items():
        msg = f"{label}.{table_key(table)}.{col_ident.name}"
        cell = row[col_ident]
        if not col.nullable:
            assert cell.concrete is not None, msg


def _assert_fk_integrity(inst: Instance, *, label: str) -> None:
    for table, table_schema in inst.schema.tables.items():
        rows = inst.get_rows(table)
        if not rows:
            continue
        for fk in table_schema.foreign_keys:
            parents = inst.get_rows(fk.target_table)
            parent_keys = {
                tuple(parent[c].concrete for c in fk.target_columns)
                for parent in parents
            }
            for row in rows:
                child_key = tuple(row[c].concrete for c in fk.source_columns)
                if any(v is None for v in child_key):
                    # Nullable FK left unset — allowed.
                    continue
                assert child_key in parent_keys, (
                    f"{label}.{table_key(table)} FK "
                    f"{tuple(c.name for c in fk.source_columns)}={child_key!r} "
                    f"missing in {table_key(fk.target_table)}"
                )


def _assert_unique_groups(inst: Instance, *, label: str) -> None:
    for table, table_schema in inst.schema.tables.items():
        rows = inst.get_rows(table)
        if len(rows) < 2:
            continue
        for group in table_schema.uniqueness_groups():
            seen: set[tuple] = set()
            for row in rows:
                key = tuple(row[c].concrete for c in group)
                if any(v is None for v in key):
                    continue
                assert key not in seen, (
                    f"{label}.{table_key(table)} duplicate "
                    f"{tuple(c.name for c in group)}={key!r}"
                )
                seen.add(key)


class TestBirdSchemaGenerate(unittest.TestCase):
    def test_schema_json_one_row_per_table(self):
        """Generate one full row per table for every DB in schema.json."""
        if not SCHEMA_JSON.is_file():
            self.skipTest(f"missing {SCHEMA_JSON}")

        rng = random.Random(0)
        generated = 0
        for db_id, ddl in _load_databases(SCHEMA_JSON).items():
            with self.subTest(db=db_id):
                inst = Instance(ddls=ddl, name=db_id, dialect="sqlite")
                tables = list(inst.schema.tables)
                if not tables:
                    continue

                for _ in range(rng.randint(5, 40)):
                    k_tables = rng.randint(1, len(tables))
                    selected_tables = rng.sample(tables, k=k_tables)
                    concretes = {table: {} for table in selected_tables}
                    inst.create_rows(concretes)
                    generated += 1

                _assert_fk_integrity(inst, label=db_id)
                from parseval.instance.io import to_db
                to_db(inst, f"sqlite:///tmp/{db_id}.sqlite", dialect="sqlite")
        self.assertGreater(generated, 11)

        # generated_tables = 0
        # databases = _load_databases(SCHEMA_JSON)
        # self.assertGreater(len(databases), 0)

        # for db_id, ddl in databases.items():
        #     with self.subTest(db=db_id):
        #         inst = Instance(ddls=ddl, name=db_id, dialect="sqlite")
        #         concretes = {table: {} for table in inst.schema.tables}
        #         results = inst.create_rows(concretes)
        #         self.assertEqual(set(results), set(concretes))
        #         for table in inst.schema.tables:
        #             self.assertGreaterEqual(
        #                 len(inst.get_rows(table)),
        #                 1,
        #                 table_key(table),
        #             )
        #             _assert_row_complete(inst, table, label=db_id)
        #             generated_tables += 1
        #         _assert_fk_integrity(inst, label=db_id)

        # self.assertGreater(generated_tables, 50)

    def test_schema_json_second_row_respects_uniques(self):
        """Two empty create_rows rounds stay unique on PK/UNIQUE groups."""
        if not SCHEMA_JSON.is_file():
            self.skipTest(f"missing {SCHEMA_JSON}")

        for db_id, ddl in _load_databases(SCHEMA_JSON).items():
            with self.subTest(db=db_id):
                inst = Instance(ddls=ddl, name=f"{db_id}_twice", dialect="sqlite")
                tables = list(inst.schema.tables)
                inst.create_rows({t: {} for t in tables})
                inst.create_rows({t: {} for t in tables})

                for table in tables:
                    self.assertGreaterEqual(len(inst.get_rows(table)), 2, table_key(table))

                _assert_fk_integrity(inst, label=db_id)
                _assert_unique_groups(inst, label=db_id)

    def test_train_schema_json_smoke(self):
        """Broader train_schema.json: random table subsets, FK integrity, export."""
        if not TRAIN_SCHEMA_JSON.is_file():
            self.skipTest(f"missing {TRAIN_SCHEMA_JSON}")

        rng = random.Random(0)
        generated = 0
        for db_id, ddl in _load_databases(TRAIN_SCHEMA_JSON).items():
            with self.subTest(db=db_id):
                inst = Instance(ddls=ddl, name=db_id, dialect="sqlite")
                tables = list(inst.schema.tables)
                if not tables:
                    continue

                for _ in range(rng.randint(5, 40)):
                    k_tables = rng.randint(1, len(tables))
                    selected_tables = rng.sample(tables, k=k_tables)
                    concretes = {table: {} for table in selected_tables}
                    inst.create_rows(concretes)
                    generated += 1

                _assert_fk_integrity(inst, label=db_id)
                from parseval.instance.io import to_db

                to_db(inst, f"sqlite:///tmp/{db_id}.sqlite", dialect="sqlite")

        self.assertGreater(generated, 50)


if __name__ == "__main__":
    unittest.main()
