"""Import every checked-in real-world schema into a query Catalog."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from sqlglot import exp

from leetcode_schema import mysql_schema_to_ddl
from parseval.catalog import Catalog
from parseval.parser.dialect import SQLDialect
from parseval.terms.constraints import UnsupportedConstraintDecl

ROOT = Path(__file__).resolve().parents[2]


def _schema_ddls():
    for filename in ("schema.json", "train_schema.json"):
        path = ROOT / "data" / "sqlite" / filename
        for database, ddl in json.loads(path.read_text()).items():
            if isinstance(ddl, list):
                ddl = ";\n".join(
                    str(statement).strip().rstrip(";")
                    for statement in ddl
                    if str(statement).strip()
                ) + ";"
            yield f"sqlite/{filename}:{database}", "sqlite", ddl

    path = ROOT / "data" / "mysql" / "leetcode.jsonlines"
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            entry = json.loads(line)
            yield (
                f"mysql/leetcode.jsonlines:{line_number}",
                "mysql",
                mysql_schema_to_ddl(entry["schema"], entry.get("constraint")),
            )

    path = ROOT / "data" / "mysql" / "leetcode-new.jsonlines"
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            entry = json.loads(line)
            yield (
                f"mysql/leetcode-new.jsonlines:{line_number}",
                "mysql",
                entry["schema"],
            )

    path = ROOT / "data" / "postgres.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        for line_number, row in enumerate(csv.DictReader(handle), 2):
            yield f"postgres.csv:{line_number}", row["dialect"], row["schema_ddl"]


def _expected_tables(dialect: SQLDialect, statements):
    expected = []
    for statement in statements:
        if not isinstance(statement, exp.Create) or statement.kind != "TABLE":
            continue
        expected.append(
            (
                dialect.qualified_name(statement.this.this),
                tuple(
                    (
                        dialect.identifier(column.this, column=True),
                        dialect.scalar_type(column.kind),
                    )
                    for column in statement.this.expressions
                    if isinstance(column, exp.ColumnDef)
                ),
            )
        )
    return expected


def test_all_dataset_schema_ddls_build_catalogs_with_declared_columns():
    unique_schemas = {}
    for label, dialect_name, ddl in _schema_ddls():
        unique_schemas.setdefault((dialect_name, ddl), label)

    assert unique_schemas
    for (dialect_name, ddl), label in unique_schemas.items():
        dialect = SQLDialect(dialect_name)
        try:
            statements = [
                statement
                for statement in dialect.parse_ddl(ddl)
                if statement is not None
            ]
            expected = _expected_tables(dialect, statements)
            catalog = Catalog.from_ddl(ddl, dialect=dialect_name)
        except Exception as error:
            raise AssertionError(f"{label}: {error}") from error

        actual = [
            (
                table.name,
                tuple(
                    (
                        column.name,
                        table.column_spec(column.id).sort.sql_type,
                    )
                    for column in table.columns
                ),
            )
            for table in catalog.tables()
        ]
        assert actual == expected, f"{label}: imported table/column definitions differ"
        unsupported = [
            constraint
            for table in catalog.tables()
            for constraint in table.constraints
            if isinstance(constraint, UnsupportedConstraintDecl)
        ]
        assert not unsupported, f"{label}: unsupported constraints remain"
