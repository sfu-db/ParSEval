"""Check LeetCode query pairs with ``disprove`` on a MySQL server.

Each line of data/mysql/leetcode.jsonlines holds a problem's tables as JSON
(``schema``: table -> column -> type), its ``constraint`` list and a ``pair``
of queries: the reference solution and a submission. The MySQL DDL is
recovered from the JSON (see ``ddl``). Every check runs in scratch databases
created on the server the connection string names. Pairs with a query that
is not a SELECT are skipped. Each record logs the
verdict, its reason and end_to_end_s in wall-clock seconds; the summary
counts verdicts (ERROR when a check crashed) and is written next to the
output as ``<output stem>.summary.json``.

Example:
    python scripts/disprove_leetcode.py --workers 8 --limit 0 \\
        --connection-string mysql+pymysql://root:rootpass@127.0.0.1:3306/mydb
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from time import monotonic

from parseval import GenerationConfig, Verdict, disprove

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "mysql" / "leetcode.jsonlines"
CONNECTION = os.environ.get("MYSQL_CONNECTION_STRING", "mysql+pymysql://root:rootpass@127.0.0.1:3306/mydb")

TYPES = {"INT": "INT", "VARCHAR": "VARCHAR(255)", "DATE": "DATE", "TIME": "TIME", "NUMERIC": "DECIMAL(10, 2)",
         "BOOL": "BOOLEAN"}
# A SELECT query, possibly parenthesized or with a WITH clause. The dataset
# also holds UPDATE statements and fragments that lost their head ("FROM ...").
SELECT = re.compile(r"\s*\(*\s*(SELECT|WITH)\b", re.IGNORECASE)
COMPARISONS = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def quote(name: str) -> str:
    return f"`{name}`"


def literal(value) -> str:
    """SQL for a constraint operand: a ``{"value": "TABLE__COLUMN"}`` column, a
    ``{"literal": ...}`` constant, a ``{"date": ...}`` typed literal, a string or a number."""
    if isinstance(value, dict):
        (kind, text), = value.items()
        if kind == "value":
            return quote(text.split("__", 1)[1])
        return literal(text) if kind == "literal" else f"{kind.upper()} '{text}'"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return str(value)


def tables(item) -> set[str]:
    """Tables whose columns a constraint mentions."""
    if isinstance(item, dict):
        if isinstance(item.get("value"), str):
            return {item["value"].split("__", 1)[0]}
        return set().union(*map(tables, item.values()))
    if isinstance(item, list):
        return set().union(*map(tables, item))
    return set()


def predicate(item: dict) -> str | None:
    """A CHECK condition, or None for a constraint DDL cannot state (``inc``, ``consec``)."""
    (kind, args), = item.items()
    if kind in COMPARISONS:
        return f"{literal(args[0])} {COMPARISONS[kind]} {literal(args[1])}"
    if kind == "between":
        return f"{literal(args[0])} BETWEEN {literal(args[1])} AND {literal(args[2])}"
    if kind == "in":
        return f"{literal(args[0])} IN ({', '.join(map(literal, args[1]))})"
    if kind == "imply":
        premise, conclusion = map(predicate, args)
        return None if premise is None or conclusion is None else f"NOT ({premise}) OR ({conclusion})"
    return None


def column_type(declared: str) -> str:
    """``ENUM,A,B`` lists the values; its ``NULL`` value only allows NULL."""
    kind, *values = declared.split(",")
    if kind == "ENUM":
        return f"ENUM({', '.join(literal(value) for value in values if value != 'NULL')})"
    return TYPES[kind]


def ddl(schema: dict[str, dict[str, str]], constraints: list[dict] | None) -> str:
    """CREATE TABLE statements for one problem, parents before the tables referencing them.

    A table's first ``primary`` entry is its primary key and later ones are
    unique keys. A referenced column that leads no key gets an index, which
    MySQL requires of a foreign key's parent. CHECK constraints over one
    table are kept; those spanning tables, ``inc`` and ``consec`` are dropped.
    """
    keys: dict[str, list[list[str]]] = {table: [] for table in schema}
    foreign: dict[str, list[tuple[str, str, str]]] = {table: [] for table in schema}
    checks: dict[str, list[str]] = {table: [] for table in schema}
    for item in constraints or ():
        (kind, args), = item.items()
        if kind == "primary":
            table = args[0]["value"].split("__", 1)[0]
            keys[table].append([value["value"].split("__", 1)[1] for value in args])
        elif kind == "foreign":
            (child, column), (parent, target) = (value["value"].split("__", 1) for value in args)
            foreign[child].append((column, parent, target))
        elif len(scope := tables(item)) == 1 and (condition := predicate(item)) is not None:
            checks[scope.pop()].append(condition)
    indexes = {table: [] for table in schema}
    for references in foreign.values():
        for _, parent, target in references:
            if not any(key[0] == target for key in keys[parent] + indexes[parent]):
                indexes[parent].append([target])

    order: list[str] = []
    def visit(table: str, path: tuple[str, ...] = ()) -> None:
        if table in order or table in path:
            return
        for _, parent, _ in foreign[table]:
            visit(parent, (*path, table))
        order.append(table)
    for table in schema:
        visit(table)

    statements = []
    for table in order:
        items = [f"{quote(name)} {column_type(declared)}" for name, declared in schema[table].items()]
        for index, key in enumerate(keys[table]):
            items.append(f"{'PRIMARY KEY' if index == 0 else 'UNIQUE'} ({', '.join(map(quote, key))})")
        items += [f"KEY ({quote(column)})" for column, in indexes[table]]
        items += [
            f"FOREIGN KEY ({quote(column)}) REFERENCES {quote(parent)} ({quote(target)})"
            for column, parent, target in foreign[table]
        ]
        items += [f"CHECK ({condition})" for condition in checks[table]]
        statements.append(f"CREATE TABLE {quote(table)} ({', '.join(items)})")
    return ";\n".join(statements)


def run_case(row: dict, url: str, config: GenerationConfig, query_timeout_s: float) -> dict:
    started = monotonic()
    record = {"problem": row["problem"], "index": row["index"]}
    # One failing pair must not end the run.
    try:
        result = disprove(row["q1"], row["q2"], row["schema"], url, "mysql", config=config, timeout_s=query_timeout_s)
    except Exception as error:
        record.update(verdict="ERROR", reason=f"{type(error).__name__}: {str(error)[:500]}", results=None)
    else:
        record.update(
            verdict=result.verdict.value, reason=result.reason,
            results=result.results and [str(item)[:300] for item in result.results],
        )
    return {**record, "end_to_end_s": round(monotonic() - started, 3), "q1": row["q1"], "q2": row["q2"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument(
        "--connection-string", default=CONNECTION, help="SQLAlchemy MySQL URL (default: $MYSQL_CONNECTION_STRING)"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "disprove-leetcode.jsonl")
    parser.add_argument("--problem", action="append", help="Restrict to problem numbers, such as 175")
    parser.add_argument("--start", type=int, default=0, help="Pairs to skip first")
    parser.add_argument("--limit", type=int, default=10, help="Maximum pairs; 0 runs all (default: 10)")
    parser.add_argument("--solver-timeout-ms", type=int, default=5_000)
    parser.add_argument("--case-timeout-s", type=float, default=360, help="Generation time budget per query")
    parser.add_argument("--query-timeout-s", type=float, default=15, help="MySQL time budget per query run")
    parser.add_argument("--workers", type=int, default=1, help="Pairs checked in parallel")
    args = parser.parse_args(argv)
    started = monotonic()
    rows = []
    skipped = 0
    with args.data.open(encoding="utf-8") as lines:
        for line in lines:
            item = json.loads(line)
            problem = Path(item["file"]).stem
            if args.problem and problem not in args.problem:
                continue
            q1, q2 = item["pair"]
            if not (SELECT.match(q1) and SELECT.match(q2)):
                skipped += 1
                continue
            rows.append({
                "problem": problem, "index": item["index"], "q1": q1, "q2": q2,
                "schema": ddl(item["schema"], item["constraint"]),
            })
    print(f"skipped {skipped} pairs with a non-SELECT query", file=sys.stderr)
    rows = rows[args.start:][: args.limit or None]
    config = GenerationConfig(timeout_ms=args.solver_timeout_ms, time_limit_s=args.case_timeout_s)
    case = partial(run_case, url=args.connection_string, config=config, query_timeout_s=args.query_timeout_s)
    verdicts: Counter[str] = Counter({verdict.value: 0 for verdict in Verdict} | {"ERROR": 0})
    times = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output, ProcessPoolExecutor(args.workers) as pool:
        for record in pool.map(case, rows):
            verdicts[record["verdict"]] += 1
            times.append(record["end_to_end_s"])
            print(json.dumps(record, default=str), file=output, flush=True)
            print(f'{record["problem"]}/{record["index"]}: {record["verdict"]} ({record["end_to_end_s"]:.3f}s)',
                  file=sys.stderr, flush=True)
    report = {
        "pairs": len(times),
        **verdicts,
        "median_s": statistics.median(times) if times else None,
        "max_s": max(times, default=None),
        "end_to_end_s": round(monotonic() - started, 3),
    }
    args.output.with_name(f"{args.output.stem}.summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
