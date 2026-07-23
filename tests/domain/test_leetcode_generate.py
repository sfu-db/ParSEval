"""LeetCode schema generation: build DDL from dataset constraints, then create_rows.

Mirrors ``tests/experiment/test_mysql.py``: each jsonlines entry carries
``schema`` + ``constraint``; ``build_ddl`` recovers PRIMARY KEY / UNIQUE /
FOREIGN KEY / CHECK into MySQL DDL, which ``Instance`` must parse back.
"""

from __future__ import annotations

import importlib.util
import json
import random
import unittest
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from parseval.domain.exceptions import ConstraintViolationError
from parseval.instance import Instance
from parseval.instance.schema import table_key

ROOT = Path(__file__).resolve().parents[2]
LEETCODE_JSONL = ROOT / "data" / "mysql" / "leetcode.jsonlines"
DEFAULT_MYSQL_CONNECTION = "mysql+pymysql://root:rootpass@localhost:3306/mydb"
MYSQL_EXPERIMENT = ROOT / "scripts" / "exp_mysql_disprover.py"


def _load_mysql_experiment():
    spec = importlib.util.spec_from_file_location(
        "parseval_mysql_experiment", MYSQL_EXPERIMENT
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"mysql_experiment_unimportable:{MYSQL_EXPERIMENT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema_cache_key(schema: dict, constraints: Any) -> str:
    return json.dumps({"s": schema, "c": constraints}, sort_keys=True)


def _load_unique_entries(path: Path) -> list[dict[str, Any]]:
    """Deduplicate jsonlines rows by schema+constraints; attach built DDL."""
    mysql_mod = _load_mysql_experiment()
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            schema = entry["schema"]
            constraints = list(entry.get("constraint") or [])
            key = _schema_cache_key(schema, constraints)
            if key in seen:
                continue
            seen.add(key)
            stem = Path(str(entry.get("file", "leetcode"))).stem
            schema_id = f"leetcode_{stem}_{len(unique)}"
            ddl = mysql_mod.build_ddl(schema, constraints)
            unique.append(
                {
                    "schema_id": schema_id,
                    "file": entry.get("file"),
                    "schema": schema,
                    "constraint": constraints,
                    "ddl": ddl,
                }
            )
    return unique


def _expected_constraints(mysql_mod, schema: dict, constraints: list[dict]) -> dict[str, Any]:
    """Project dataset constraints the same way ``build_ddl`` emits them."""
    primary_keys: dict[str, list[tuple[str, ...]]] = {}
    foreign_keys: list[tuple[str, str, str, str]] = []
    checks: dict[str, list[str]] = {}

    for item in constraints:
        if "primary" in item:
            cols: list[str] = []
            table = None
            for entry in item["primary"]:
                table, col = mysql_mod._parse_ref(entry["value"])
                cols.append(col)
            if table is not None and cols:
                primary_keys.setdefault(table, []).append(tuple(cols))
        elif "foreign" in item:
            entries = item["foreign"]
            fk_tbl, fk_col = mysql_mod._parse_ref(entries[0]["value"])
            ref_tbl, ref_col = mysql_mod._parse_ref(entries[1]["value"])
            foreign_keys.append((fk_tbl, fk_col, ref_tbl, ref_col))
        else:
            check = mysql_mod._row_local_check(item)
            if check is not None:
                table, expression = check
                checks.setdefault(table, []).append(expression)

    # Mirror build_ddl: only emit FKs whose referenced table is already created
    # (or self-FK). Cyclic / reverse edges that lose the topo race are dropped.
    deps: dict[str, set[str]] = {table.casefold(): set() for table in schema}
    for fk_tbl, _fk_col, ref_tbl, _ref_col in foreign_keys:
        if fk_tbl != ref_tbl and fk_tbl in deps and ref_tbl in deps:
            deps[fk_tbl].add(ref_tbl)
    emitted_fks: list[tuple[str, str, str, str]] = []
    created: set[str] = set()
    for table in mysql_mod._topological_sort(deps):
        for fk_tbl, fk_col, ref_tbl, ref_col in foreign_keys:
            if fk_tbl == table and (fk_tbl == ref_tbl or ref_tbl in created):
                emitted_fks.append((fk_tbl, fk_col, ref_tbl, ref_col))
        created.add(table)

    return {
        "primary_keys": primary_keys,
        "foreign_keys": emitted_fks,
        "checks": checks,
    }


def _assert_constraints_recovered(
    inst: Instance,
    *,
    schema: dict[str, dict[str, str]],
    expected: dict[str, Any],
    label: str,
) -> None:
    """Assert InstanceSchema recovered PK / UNIQUE / FK / CHECK from built DDL."""
    def identifier(value: str) -> str:
        return value.casefold()

    # Tables
    recovered_tables = {table_key(t) for t in inst.schema.tables}
    self_tables = {identifier(table) for table in schema}
    assert recovered_tables == self_tables, (
        f"{label}: tables {recovered_tables!r} != {self_tables!r}"
    )

    # Primary key (first primary group) + extra primaries as UNIQUE
    for table_name, groups in expected["primary_keys"].items():
        table_schema = inst.schema.get_table(table_name)
        pk = tuple(identifier(c.name) for c in table_schema.primary_key)
        expected_pk = tuple(identifier(column) for column in groups[0])
        assert pk == expected_pk, f"{label}.{table_name} PK {pk!r} != {expected_pk!r}"
        extra = {
            tuple(identifier(c.name) for c in group)
            for group in table_schema.unique_constraints
        }
        for unique_group in groups[1:]:
            expected_unique = tuple(identifier(column) for column in unique_group)
            assert expected_unique in extra, (
                f"{label}.{table_name} missing UNIQUE {expected_unique!r} in {extra!r}"
            )

    # Foreign keys
    recovered_fks: set[tuple[str, str, str, str]] = set()
    for table in inst.schema.tables:
        for fk in inst.schema.tables[table].foreign_keys:
            assert len(fk.source_columns) == 1 and len(fk.target_columns) == 1
            recovered_fks.add(
                (
                    identifier(table_key(fk.source_table)),
                    identifier(fk.source_columns[0].name),
                    identifier(table_key(fk.target_table)),
                    identifier(fk.target_columns[0].name),
                )
            )
    expected_fks = {
        tuple(identifier(part) for part in foreign_key)
        for foreign_key in expected["foreign_keys"]
    }
    assert recovered_fks == expected_fks, (
        f"{label}: FKs {recovered_fks!r} != {expected_fks!r}"
    )

    # CHECKs recovered from DDL (row-local constraints rendered by build_ddl)
    for table_name, expressions in expected["checks"].items():
        table_schema = inst.schema.get_table(table_name)
        recovered_sql = {
            check.expression.sql(dialect="mysql") for check in table_schema.checks
        }
        assert len(table_schema.checks) >= len(expressions), (
            f"{label}.{table_name}: expected >= {len(expressions)} CHECKs, "
            f"got {len(table_schema.checks)} ({recovered_sql!r})"
        )


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


def _mysql_available(connection_string: str) -> bool:
    try:
        from parseval.db_manager import DBManager

        with DBManager().get_connection(connection_string, "mysql"):
            return True
    except Exception:
        return False


def _schema_connection_string(base: str, schema_id: str) -> str:
    parsed = urlparse(base)
    db_name = f"parseval_{schema_id}"[:64]
    return urlunparse(parsed._replace(path=f"/{db_name}"))


class TestLeetcodeSchemaGenerate(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not LEETCODE_JSONL.is_file():
            raise unittest.SkipTest(f"missing {LEETCODE_JSONL}")
        cls.mysql_mod = _load_mysql_experiment()
        cls.entries = _load_unique_entries(LEETCODE_JSONL)
        if not cls.entries:
            raise unittest.SkipTest("no unique leetcode schemas loaded")

    def test_build_ddl_recovers_all_constraints(self):
        """dataset constraints → build_ddl → InstanceSchema recovers PK/UQ/FK/CHECK."""
        recovered = 0
        for entry in self.entries:
            schema_id = entry["schema_id"]
            with self.subTest(schema=schema_id):
                expected = _expected_constraints(
                    self.mysql_mod, entry["schema"], entry["constraint"]
                )
                ddl_upper = entry["ddl"].upper()
                if expected["primary_keys"]:
                    self.assertIn("PRIMARY KEY", ddl_upper, schema_id)
                if any(len(groups) > 1 for groups in expected["primary_keys"].values()):
                    self.assertIn("UNIQUE", ddl_upper, schema_id)
                if expected["foreign_keys"]:
                    self.assertIn("FOREIGN KEY", ddl_upper, schema_id)
                if expected["checks"]:
                    self.assertIn("CHECK", ddl_upper, schema_id)

                inst = Instance(ddls=entry["ddl"], name=schema_id, dialect="mysql")
                _assert_constraints_recovered(
                    inst,
                    schema=entry["schema"],
                    expected=expected,
                    label=schema_id,
                )
                recovered += 1

        self.assertEqual(recovered, len(self.entries))
        self.assertGreater(recovered, 40)

    def test_unique_schemas_one_row_per_table(self):
        """Generate one full row per table for every unique LeetCode schema."""
        generated_tables = 0
        succeeded = 0
        skipped_checks = 0
        for entry in self.entries:
            schema_id = entry["schema_id"]
            with self.subTest(schema=schema_id):
                inst = Instance(ddls=entry["ddl"], name=schema_id, dialect="mysql")
                concretes = {table: {} for table in inst.schema.tables}
                try:
                    results = inst.create_rows(concretes)
                except ConstraintViolationError as exc:
                    if "check_constraint_failed" in str(exc):
                        skipped_checks += 1
                        continue
                    raise

                self.assertEqual(set(results), set(concretes))
                for table in inst.schema.tables:
                    self.assertGreaterEqual(
                        len(inst.get_rows(table)),
                        1,
                        table_key(table),
                    )
                    _assert_row_complete(inst, table, label=schema_id)
                    generated_tables += 1
                _assert_fk_integrity(inst, label=schema_id)
                succeeded += 1

        self.assertGreater(generated_tables, 30)
        self.assertGreater(succeeded, 30)
        self.assertLess(skipped_checks, len(self.entries))

    def test_unique_schemas_second_row_respects_uniques(self):
        """Two empty create_rows rounds stay unique on PK/UNIQUE groups."""
        succeeded = 0
        for entry in self.entries:
            schema_id = entry["schema_id"]
            with self.subTest(schema=schema_id):
                inst = Instance(
                    ddls=entry["ddl"], name=f"{schema_id}_twice", dialect="mysql"
                )
                tables = list(inst.schema.tables)
                try:
                    inst.create_rows({t: {} for t in tables})
                    inst.create_rows({t: {} for t in tables})
                except ConstraintViolationError as exc:
                    if "check_constraint_failed" in str(exc):
                        continue
                    raise

                for table in tables:
                    self.assertGreaterEqual(
                        len(inst.get_rows(table)), 2, table_key(table)
                    )
                _assert_fk_integrity(inst, label=schema_id)
                _assert_unique_groups(inst, label=schema_id)
                succeeded += 1

        self.assertGreater(succeeded, 30)

    def test_unique_schemas_smoke_export_mysql(self):
        """Random table subsets, FK integrity, export to MySQL."""
        if not _mysql_available(DEFAULT_MYSQL_CONNECTION):
            self.skipTest(f"mysql unavailable: {DEFAULT_MYSQL_CONNECTION}")

        from parseval.instance.io import to_db

        rng = random.Random(0)
        generated = 0
        succeeded = 0
        for entry in self.entries:
            schema_id = entry["schema_id"]
            with self.subTest(schema=schema_id):
                inst = Instance(ddls=entry["ddl"], name=schema_id, dialect="mysql")
                tables = list(inst.schema.tables)
                if not tables:
                    continue
                try:
                    for _ in range(rng.randint(3, 12)):
                        k_tables = rng.randint(1, len(tables))
                        selected = rng.sample(tables, k=k_tables)
                        inst.create_rows({table: {} for table in selected})
                        generated += 1
                except ConstraintViolationError as exc:
                    if "check_constraint_failed" in str(exc):
                        continue
                    raise

                _assert_fk_integrity(inst, label=schema_id)
                to_db(
                    inst,
                    _schema_connection_string(DEFAULT_MYSQL_CONNECTION, schema_id),
                    dialect="mysql",
                )
                succeeded += 1

        self.assertGreater(generated, 50)
        self.assertGreater(succeeded, 30)


if __name__ == "__main__":
    unittest.main()
