"""Parse every BIRD SQLite schema with InstanceSchema."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from parseval.instance.schema import InstanceSchema, table_key

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FILES = (
    ROOT / "data" / "sqlite" / "schema.json",
    ROOT / "data" / "sqlite" / "train_schema.json",
)


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


class TestBirdSchemaParse(unittest.TestCase):
    def test_parse_all_bird_sqlite_schemas(self):
        missing = [path for path in SCHEMA_FILES if not path.is_file()]
        if missing:
            self.skipTest(f"missing schema files: {missing}")

        parsed = 0
        for path in SCHEMA_FILES:
            for db_id, ddl in _load_databases(path).items():
                with self.subTest(file=path.name, db=db_id):
                    schema = InstanceSchema.from_ddl(ddl, dialect="sqlite")
                    self.assertGreater(
                        len(schema.tables),
                        0,
                        f"{path.name}:{db_id} produced no tables",
                    )
                    for table, table_schema in schema.tables.items():
                        self.assertEqual(table_key(table), table_schema.name)
                        # Composite PK is an ordered tuple; members are not
                        # individually unique unless the PK is single-column.
                        pk = table_schema.primary_key
                        if len(pk) > 1:
                            for col in pk:
                                self.assertFalse(
                                    schema.is_unique(table, col),
                                    f"composite PK member marked unique: "
                                    f"{db_id}.{table_schema.name}.{col.name}",
                                )
                        for fk in table_schema.foreign_keys:
                            self.assertIn(fk.target_table, schema.tables)
                            self.assertEqual(
                                len(fk.source_columns),
                                len(fk.target_columns),
                                f"FK arity mismatch in {db_id}.{table_schema.name}",
                            )
                    parsed += 1

        self.assertEqual(parsed, 80)


if __name__ == "__main__":
    unittest.main()
