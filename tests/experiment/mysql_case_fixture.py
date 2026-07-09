from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

from parseval.instance import Instance
from parseval.plan import Plan
from parseval.query import preprocess_sql
from parseval.symbolic.engine import SymbolicEngine
from parseval.symbolic.evaluator import PlanEvaluator
from parseval.symbolic.types import CoverageThresholds

DEFAULT_DATA_FP = Path(__file__).resolve().parents[2] / "data/mysql/leetcode.jsonlines"
EXPERIMENT_DIR = Path(__file__).resolve().parent
if str(EXPERIMENT_DIR) not in sys.path:
    sys.path.insert(0, str(EXPERIMENT_DIR))

from test_mysql import _prepare_mysql_query, build_ddl


@dataclass(frozen=True)
class LeetCodeCase:
    index: int
    schema: dict[str, dict[str, str]]
    constraints: list[dict[str, Any]]
    sql1: str
    sql2: str
    ddl: str

    @property
    def pair(self) -> tuple[str, str]:
        return self.sql1, self.sql2


def load_leetcode_case(index: int, data_fp: str | Path = DEFAULT_DATA_FP) -> LeetCodeCase:
    with Path(data_fp).open() as handle:
        for offset, line in enumerate(handle):
            if offset != index:
                continue
            entry = json.loads(line)
            sql1, sql2 = (
                _prepare_mysql_query(sql, entry["schema"])
                for sql in entry["pair"]
            )
            constraints = entry.get("constraint") or []
            return LeetCodeCase(
                index=index,
                schema=entry["schema"],
                constraints=constraints,
                sql1=sql1,
                sql2=sql2,
                ddl=build_ddl(entry["schema"], constraints),
            )
    raise IndexError(f"leetcode_case_not_found:{index}")


def evaluate_case_query(
    case: LeetCodeCase,
    query_number: int,
    *,
    max_iterations: int = 10,
    dialect: str = "mysql",
):
    sql = case.pair[query_number]
    instance = Instance(ddls=case.ddl, name=f"leetcode_{case.index}", dialect=dialect)
    SymbolicEngine(
        instance,
        sql,
        dialect=dialect,
        max_iterations=max_iterations,
        max_rows_per_table=max_iterations,
    ).generate(thresholds=CoverageThresholds(atom_null=0), speculate_first=False)
    plan = Plan(preprocess_sql(sql, instance, dialect=dialect), instance)
    output = PlanEvaluator(plan, instance, dialect).evaluate_context()
    return instance, output


def execute_generated_sqlite(case: LeetCodeCase, instance: Instance, sql: str):
    connection = sqlite3.connect(":memory:")
    try:
        for statement in _sqlite_ddl(case.ddl).split(";"):
            statement = statement.strip()
            if statement:
                connection.execute(statement)
        for table_name in instance.tables:
            rows = _instance_table_rows(instance, table_name)
            if not rows:
                continue
            columns = list(rows[0])
            quoted = ", ".join(f'"{column}"' for column in columns)
            placeholders = ", ".join("?" for _column in columns)
            insert = f'INSERT INTO "{table_name}" ({quoted}) VALUES ({placeholders})'
            for row in rows:
                connection.execute(insert, [_sqlite_value(row[column]) for column in columns])
        connection.commit()
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _instance_table_rows(instance: Instance, table_name: str) -> list[dict[str, Any]]:
    return [
        {column.name.normalized: symbol.concrete for column, symbol in row.items()}
        for row in instance.get_rows(table_name)
    ]


def _sqlite_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _sqlite_ddl(ddl: str) -> str:
    ddl = re.sub(r"\bENUM\s*\([^)]*\)", "TEXT", ddl, flags=re.IGNORECASE)
    ddl = re.sub(r"\bVARCHAR\s*\([^)]*\)", "TEXT", ddl, flags=re.IGNORECASE)
    ddl = re.sub(r",\s*INDEX\s*\([^)]*\)", "", ddl, flags=re.IGNORECASE)
    return ddl
