"""Validate LeetCode SQL pairs against MySQL syntax via PREPARE.

Creates a temporary database per entry, builds DDL, then validates both
SQL queries from the dataset using MySQL PREPARE and EXPLAIN.

Usage::

    uv run python tests/experiment/test_mysql_syntax.py \
      --connection-string mysql+pymysql://root:rootpass@localhost:3306/mydb

    MYSQL_CONNECTION=mysql+pymysql://root:rootpass@localhost:3306/mydb \
      uv run pytest tests/experiment/test_mysql_syntax.py -q --limit 50
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.experiment.test_mysql import (
    DEFAULT_MYSQL_CONNECTION,
    build_ddl,
    load_jsonlines,
)

DATA_FP = Path("data/mysql/leetcode.jsonlines")


@dataclass(frozen=True)
class QueryValidation:
    ok: bool
    error_code: int | None = None
    error_message: str = ""


@dataclass(frozen=True)
class SyntaxCaseValidation:
    index: int
    sql1: QueryValidation
    sql2: QueryValidation

    @property
    def mysql_rejected(self) -> bool:
        return not self.sql1.ok or not self.sql2.ok


def _connection_args(connection_string: str, database: str | None = None) -> dict:
    match = re.match(
        r"^mysql(?:\+pymysql)?://([^:]+):([^@]*)@([^:/]+)(?::(\d+))?(?:/([^?]+))?",
        connection_string,
    )
    if match is None:
        raise ValueError(
            "Expected mysql+pymysql://user:password@host:port/database "
            f"connection string, got {connection_string!r}"
        )
    user, password, host, port, parsed_database = match.groups()
    return {
        "host": host,
        "port": int(port or 3306),
        "user": unquote(user),
        "password": unquote(password),
        "database": database
        or (unquote(parsed_database) if parsed_database else None),
        "autocommit": True,
        "charset": "utf8mb4",
    }


def _quote_identifier(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _setup_case_database(
    admin_connection,
    db_name: str,
    ddl: str,
) -> None:
    with admin_connection.cursor() as cursor:
        cursor.execute(f"DROP DATABASE IF EXISTS {_quote_identifier(db_name)}")
        cursor.execute(f"CREATE DATABASE {_quote_identifier(db_name)}")
        cursor.execute(f"USE {_quote_identifier(db_name)}")
        for statement in ddl.split(";"):
            statement = statement.strip()
            if statement:
                cursor.execute(statement)


def _validate_query(cursor, sql: str) -> QueryValidation:
    try:
        cursor.execute("SET @parseval_syntax_sql = %s", (sql,))
        cursor.execute("PREPARE parseval_syntax_stmt FROM @parseval_syntax_sql")
        cursor.execute("DEALLOCATE PREPARE parseval_syntax_stmt")
        if sql.lstrip().upper().startswith(("SELECT", "WITH")):
            cursor.execute("EXPLAIN " + sql)
    except Exception as exc:
        code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
        return QueryValidation(
            ok=False,
            error_code=code,
            error_message=str(exc),
        )
    return QueryValidation(ok=True)


def validate_syntax(
    *,
    data_fp: Path,
    connection_string: str,
    limit: int | None = None,
) -> list[SyntaxCaseValidation]:
    import pymysql

    dataset = load_jsonlines(str(data_fp))
    if limit is not None:
        dataset = dataset[:limit]

    validations: list[SyntaxCaseValidation] = []
    admin_args = _connection_args(connection_string, database=None)
    admin_connection = pymysql.connect(**admin_args)
    try:
        for index, entry in enumerate(dataset):
            db_name = (
                f"parseval_syntax_{os.getpid()}_"
                f"{index}_{uuid.uuid4().hex[:8]}"
            )
            ddl = build_ddl(entry["schema"], entry.get("constraint") or [])
            try:
                _setup_case_database(admin_connection, db_name, ddl)
                with admin_connection.cursor() as cursor:
                    cursor.execute(f"USE {_quote_identifier(db_name)}")
                    sql1, sql2 = entry["pair"]
                    validations.append(
                        SyntaxCaseValidation(
                            index=index,
                            sql1=_validate_query(cursor, sql1),
                            sql2=_validate_query(cursor, sql2),
                        )
                    )
            finally:
                with admin_connection.cursor() as cursor:
                    cursor.execute(
                        f"DROP DATABASE IF EXISTS {_quote_identifier(db_name)}"
                    )
    finally:
        admin_connection.close()

    return validations


def _rejected_cases(
    validations: list[SyntaxCaseValidation],
) -> list[SyntaxCaseValidation]:
    return [case for case in validations if case.mysql_rejected]


def _format_validation(case: SyntaxCaseValidation) -> str:
    return (
        f"index={case.index} "
        f"sql1={'ok' if case.sql1.ok else f'ERR {case.sql1.error_code}'} "
        f"sql2={'ok' if case.sql2.ok else f'ERR {case.sql2.error_code}'} "
        f"msg={case.sql1.error_message or case.sql2.error_message}"
    )


def test_mysql_syntax():
    connection_string = os.environ.get("MYSQL_CONNECTION")
    if not connection_string:
        pytest.skip("Set MYSQL_CONNECTION")

    limit_value = os.environ.get("MYSQL_SYNTAX_LIMIT")
    limit = int(limit_value) if limit_value else None
    validations = validate_syntax(
        data_fp=DATA_FP,
        connection_string=connection_string,
        limit=limit,
    )

    rejected = _rejected_cases(validations)
    assert not rejected, "\n".join(
        _format_validation(case) for case in rejected
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate LeetCode SQL pairs against MySQL syntax"
    )
    parser.add_argument("--data-fp", type=Path, default=DATA_FP)
    parser.add_argument(
        "--connection-string",
        default=os.environ.get("MYSQL_CONNECTION", DEFAULT_MYSQL_CONNECTION),
    )
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    validations = validate_syntax(
        data_fp=args.data_fp,
        connection_string=args.connection_string,
        limit=args.limit,
    )
    rejected = _rejected_cases(validations)

    print(f"Tested {len(validations)} SQL pairs from {args.data_fp}")
    print(f"MySQL rejected: {len(rejected)}")
    print(f"MySQL accepted: {len(validations) - len(rejected)}")
    for case in rejected:
        print("REJECTED", _format_validation(case))

    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
