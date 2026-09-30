"""Exercise coverage generation on the paired queries in data/postgres.csv.

Examples:
    python scripts/benchmark_postgres_coverage.py --limit 5
    python scripts/benchmark_postgres_coverage.py --mode inventory --limit 5
    python scripts/benchmark_postgres_coverage.py --dbid tpch --limit 0 --output results.jsonl
    python scripts/benchmark_postgres_coverage.py --index 596

The comparison is of SQL bags. A difference is a concrete witness for NEQ;
failure to find one does not prove EQ. Each CSV row runs in a separate process
so the wall-clock timeout includes parsing, generation, and concrete replay.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import sqlite3
import sys
import tempfile
import time
from collections import Counter
from datetime import date, datetime, time as time_value
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "postgres.csv"


def _error(error: Exception) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)[:500]}


def _target_key(identity: str) -> str:
    """Return a compact stable key for an internal coverage identity."""

    return hashlib.blake2s(identity.encode(), digest_size=6).hexdigest()


def _compile(sql: str, catalog: Any) -> tuple[Any, Any]:
    from parseval.parser.query import lower_query
    from parseval.terms.arena import TermArena
    from parseval.uexpr import UExprCompiler

    query = lower_query(sql, catalog)
    arena = TermArena(query.arena.context)
    root = UExprCompiler(query.arena, arena).compile(query.root).simplified_root
    return arena, root


def _rows(value: Any) -> tuple[tuple[object, ...], ...]:
    from parseval.coverage.evaluate import BagValue

    rows = value.expanded_rows() if isinstance(value, BagValue) else value.rows
    return tuple(row.values for row in rows)


def _same_bag(left: tuple[tuple[object, ...], ...], right: tuple[tuple[object, ...], ...]) -> bool:
    from parseval.coverage.evaluate import row_identity_equal

    if len(left) != len(right):
        return False
    unmatched = [tuple(_sqlite_scalar(value) for value in row) for row in right]
    for values in left:
        row = tuple(_sqlite_scalar(value) for value in values)
        match = next(
            (index for index, other in enumerate(unmatched) if row_identity_equal(row, other)),
            None,
        )
        if match is None:
            return False
        unmatched.pop(match)
    return True


def _sqlite_scalar(value: object) -> object:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime, time_value)):
        return value.isoformat()
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    return str(value)


def _sqlite_query(sql: str, dialect: str) -> str:
    """Transpile a corpus query and flatten PostgreSQL schema qualifiers."""

    from sqlglot import exp, parse_one

    expression = parse_one(sql, read=dialect)

    def rewrite(node: exp.Expression) -> exp.Expression:
        if isinstance(node, (exp.Add, exp.Sub)) and isinstance(
            node.expression, exp.Interval
        ):
            interval = node.expression
            amount = interval.this.name
            unit = interval.args["unit"].name.lower()
            sign = "+" if isinstance(node, exp.Add) else "-"
            return exp.Anonymous(
                this="DATE",
                expressions=(
                    node.this.copy(),
                    exp.Literal.string(f"{sign}{amount} {unit}"),
                ),
            )
        return node

    expression = expression.transform(rewrite)
    for table in expression.find_all(exp.Table):
        table.set("catalog", None)
        table.set("db", None)
    return expression.sql(dialect="sqlite")


def _sqlite_type(kind: object) -> str:
    name = getattr(kind, "value", str(kind))
    if name in {"boolean", "integer"}:
        return "INTEGER"
    if name in {"float", "decimal"}:
        return "REAL"
    if name == "opaque":
        return "BLOB"
    return "TEXT"


def _sqlite_replay(
    catalog: Any,
    instance: Any,
    queries: dict[str, str],
) -> dict[str, tuple[tuple[object, ...], ...]]:
    """Materialize one generated instance in an isolated SQLite database."""

    names: set[str] = set()
    with tempfile.NamedTemporaryFile(suffix=".sqlite") as database:
        connection = sqlite3.connect(database.name)
        try:
            for table in catalog.tables():
                table_name = table.name.parts[-1].text
                if table_name.casefold() in names:
                    raise ValueError(
                        f"SQLite replay cannot flatten duplicate table name {table_name!r}"
                    )
                names.add(table_name.casefold())
                columns = []
                for binding in table.columns:
                    specification = table.column_spec(binding.id)
                    column_name = binding.name.text.replace('"', '""')
                    columns.append(
                        f'"{column_name}" {_sqlite_type(specification.sort.sql_type.kind)}'
                    )
                quoted_table = table_name.replace('"', '""')
                connection.execute(
                    f'CREATE TABLE "{quoted_table}" ({", ".join(columns)})'
                )
                rows = instance.rows(table.relation)
                if rows:
                    placeholders = ", ".join("?" for _ in table.columns)
                    connection.executemany(
                        f'INSERT INTO "{quoted_table}" VALUES ({placeholders})',
                        tuple(
                            tuple(_sqlite_scalar(value) for value in row.values)
                            for row in rows
                        ),
                    )
            connection.commit()
            return {
                name: tuple(connection.execute(sql).fetchall())
                for name, sql in queries.items()
            }
        finally:
            connection.close()


def run_case(
    row: dict[str, str],
    solver_timeout_ms: int,
    max_attempts: int | None = None,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
    mode: str = "generate",
) -> dict[str, Any]:
    from parseval.catalog import Catalog
    from parseval.coverage import explore_paths, unsupported_scopes
    from parseval.generator import GenerationConfig, generate
    from parseval.instance import Instance
    from parseval.coverage.evaluate import UExprEvaluator

    result: dict[str, Any] = {
        "index": row["index"],
        "dbid": row["dbid"],
        "ground_truth": row["ground_truth"],
        "queries": {},
        "comparison": {
            "status": "not_run",
            "tested_instances": 0,
            "bag_difference_found": False,
        },
        "sqlite_replay": {
            "status": "not_run",
            "tested_instances": 0,
            "query_executions": 0,
            "semantic_mismatches": 0,
            "bag_difference_found": False,
        },
    }
    started = time.monotonic()
    try:
        catalog = Catalog.from_ddl(row["schema_ddl"], dialect=row["dialect"])
    except Exception as error:
        result["catalog_error"] = _error(error)
        result["elapsed_s"] = round(time.monotonic() - started, 3)
        return result
    if progress:
        progress("catalog", {})

    compiled: dict[str, tuple[Any, Any]] = {}
    instances = [Instance.empty(catalog)]
    for name in ("q1", "q2"):
        query_result: dict[str, Any] = {}
        result["queries"][name] = query_result
        query_started = time.monotonic()
        try:
            if progress:
                progress(f"{name}.compile", {"query": name})
            compiled[name] = _compile(row[name], catalog)
            if progress:
                progress(f"{name}.initial_explore", {"query": name})
            arena, root = compiled[name]
            initial_observed, initial_neighbors = explore_paths(
                arena, root, Instance.empty(catalog)
            )
            query_result["initial_observed"] = len(initial_observed)
            query_result["initial_neighbors"] = len(initial_neighbors)
            query_result["unsupported_scopes"] = list(unsupported_scopes(arena, root))
            if mode == "inventory":
                query_result["status"] = "inventory_only"
            else:
                if progress:
                    progress(f"{name}.generate", {"query": name})

                def on_generation_progress(
                    phase: str, target: Any, attempt: int
                ) -> None:
                    details = {
                        "query": name,
                        "attempt": attempt,
                        "target": _target_key(target.id),
                        "label": target.label,
                    }
                    if progress:
                        progress(f"{name}.{phase}", details)

                generated = generate(
                    row[name],
                    catalog,
                    config=GenerationConfig(
                        timeout_ms=solver_timeout_ms,
                        max_attempts=max_attempts,
                    ),
                    on_progress=on_generation_progress if progress else None,
                )
                counts = Counter(item.solve.status.value for item in generated.results)
                query_result.update(
                    targets=len(generated.coverage.targets),
                    covered=len(generated.coverage.covered),
                    coverage_ratio=round(generated.coverage.ratio, 4),
                    fully_covered=generated.coverage.fully_covered,
                    bounded_unsat=len(generated.coverage.bounded_unsat),
                    unknown=len(generated.coverage.unknown),
                    unsupported=len(generated.coverage.unsupported),
                    not_attempted=len(generated.coverage.not_attempted),
                    unsupported_scopes=list(generated.coverage.unsupported_scopes),
                    solver_statuses=dict(counts),
                    generated_instances=len(generated.counterexamples),
                )
                instances.extend(case.instance for case in generated.counterexamples)
        except Exception as error:
            query_result["error"] = _error(error)
        query_result["elapsed_s"] = round(time.monotonic() - query_started, 3)
        if progress:
            progress(f"{name}.done", {"query": name})

    if len(compiled) == 2 and mode == "generate":
        if progress:
            progress("comparison", {})
        result["comparison"]["status"] = "inconclusive"
        for instance in instances:
            try:
                left_arena, left_root = compiled["q1"]
                right_arena, right_root = compiled["q2"]
                left = _rows(UExprEvaluator(left_arena, instance).evaluate_query(left_root))
                right = _rows(UExprEvaluator(right_arena, instance).evaluate_query(right_root))
            except Exception as error:
                result["comparison"].setdefault("evaluation_error", _error(error))
                continue
            result["comparison"]["tested_instances"] += 1
            if not _same_bag(left, right):
                result["comparison"].update(
                    status="difference_found",
                    bag_difference_found=True,
                    witness_rows={
                        relation.value: len(instance.rows(relation))
                        for relation, _ in catalog.context.relations()
                        if instance.rows(relation)
                    },
                    q1_result_rows=len(left),
                    q2_result_rows=len(right),
                )
                break
        if (
            not result["comparison"]["bag_difference_found"]
            and result["comparison"]["tested_instances"]
            and "evaluation_error" not in result["comparison"]
            and all("error" not in query for query in result["queries"].values())
        ):
            result["comparison"]["status"] = "no_difference_found"

        sqlite_result = result["sqlite_replay"]
        if progress:
            progress("sqlite_replay", {})
        try:
            translated = {
                name: _sqlite_query(row[name], row["dialect"])
                for name in ("q1", "q2")
            }
        except Exception as error:
            sqlite_result.update(status="transpile_error", error=_error(error))
        else:
            sqlite_result["status"] = "completed"
            for instance in instances:
                try:
                    concrete = _sqlite_replay(catalog, instance, translated)
                except Exception as error:
                    sqlite_result.update(status="execution_error", error=_error(error))
                    break
                sqlite_result["tested_instances"] += 1
                sqlite_result["query_executions"] += 2
                replay_failed = False
                for name in ("q1", "q2"):
                    try:
                        arena, root = compiled[name]
                        expected = _rows(
                            UExprEvaluator(arena, instance).evaluate_query(root)
                        )
                    except Exception as error:
                        sqlite_result.update(
                            status="semantic_replay_error",
                            error=_error(error),
                        )
                        replay_failed = True
                        break
                    if not _same_bag(expected, concrete[name]):
                        sqlite_result["semantic_mismatches"] += 1
                if replay_failed:
                    break
                if not _same_bag(concrete["q1"], concrete["q2"]):
                    sqlite_result["bag_difference_found"] = True
                    break

    result["elapsed_s"] = round(time.monotonic() - started, 3)
    return result


def _worker(
    connection: Any, row: dict[str, str], solver_timeout_ms: int,
    max_attempts: int | None, mode: str,
) -> None:
    try:
        def progress(stage: str, details: dict[str, Any]) -> None:
            connection.send({"phase": stage, "details": details})

        connection.send({
            "result": run_case(
                row, solver_timeout_ms, max_attempts, progress, mode
            )
        })
    except BaseException as error:
        connection.send({"result": {
            "index": row.get("index"),
            "dbid": row.get("dbid"),
            "ground_truth": row.get("ground_truth"),
            "worker_error": {"type": type(error).__name__, "message": str(error)[:500]},
        }})
    finally:
        connection.close()


def run_with_timeout(
    row: dict[str, str], solver_timeout_ms: int, case_timeout_s: float,
    max_attempts: int | None = None, mode: str = "generate",
) -> dict[str, Any]:
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker,
        args=(sender, row, solver_timeout_ms, max_attempts, mode),
    )
    process.start()
    sender.close()
    deadline = time.monotonic() + case_timeout_s
    phase = "startup"
    details: dict[str, Any] = {}
    try:
        while receiver.poll(max(0, deadline - time.monotonic())):
            try:
                message = receiver.recv()
            except EOFError:
                break
            if "result" in message:
                return message["result"]
            phase = message["phase"]
            details = message["details"]
        if process.is_alive():
            return {
                "index": row["index"], "dbid": row["dbid"],
                "ground_truth": row["ground_truth"],
                "status": "timeout",
                "phase": phase,
                "elapsed_s": round(case_timeout_s, 3),
                **details,
            }
        return {
            "index": row["index"], "dbid": row["dbid"],
            "ground_truth": row["ground_truth"], "worker_exit_code": process.exitcode,
        }
    finally:
        if process.is_alive():
            process.terminate()
        process.join()
        receiver.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, help="Write per-case JSON Lines here (default: stdout)")
    parser.add_argument(
        "--mode", choices=("generate", "inventory"), default="generate",
        help="Generate instances or only parse, compile, and inspect initial coverage",
    )
    parser.add_argument("--dbid", action="append", help="Select one or more database IDs")
    parser.add_argument("--index", action="append", help="Select one or more CSV index values")
    parser.add_argument("--limit", type=int, default=10, help="Maximum selected cases; 0 runs all (default: 10)")
    parser.add_argument("--solver-timeout-ms", type=int, default=3000)
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=12,
        help="Maximum coverage targets solved per query; 0 means unlimited",
    )
    parser.add_argument("--case-timeout-s", type=float, default=60)
    args = parser.parse_args(argv)
    if (
        args.limit < 0
        or args.max_attempts < 0
        or args.solver_timeout_ms <= 0
        or args.case_timeout_s <= 0
    ):
        parser.error("limit must be nonnegative; timeouts must be positive")
    max_attempts = args.max_attempts or None

    output = sys.stdout
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output = args.output.open("w", encoding="utf-8")
    summary: Counter[str] = Counter()
    try:
        with args.input.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if args.dbid and row["dbid"] not in args.dbid:
                    continue
                if args.index and row["index"] not in args.index:
                    continue
                if args.limit and summary["cases"] >= args.limit:
                    break
                case = run_with_timeout(
                    row,
                    args.solver_timeout_ms,
                    args.case_timeout_s,
                    max_attempts,
                    args.mode,
                )
                case["settings"] = {
                    "mode": args.mode,
                    "solver_timeout_ms": args.solver_timeout_ms,
                    "case_timeout_s": args.case_timeout_s,
                    "max_attempts": max_attempts,
                }
                summary["cases"] += 1
                summary[f"{case['ground_truth'].lower()}_cases"] += 1
                if case.get("status") == "timeout":
                    summary["timeouts"] += 1
                elif "catalog_error" in case or "worker_error" in case or "worker_exit_code" in case:
                    summary["case_errors"] += 1
                else:
                    for query in case["queries"].values():
                        if "error" in query:
                            summary["query_errors"] += 1
                        elif query.get("status") == "inventory_only":
                            summary["inventoried_queries"] += 1
                            summary["initial_neighbors"] += query["initial_neighbors"]
                        else:
                            summary["generated_queries"] += 1
                            summary["targets"] += query["targets"]
                            summary["covered"] += query["covered"]
                            summary["bounded_unsat"] += query["bounded_unsat"]
                            summary["unknown"] += query["unknown"]
                            summary["unsupported"] += query["unsupported"]
                            summary["not_attempted"] += query["not_attempted"]
                    if case["comparison"]["status"] in {
                        "no_difference_found", "difference_found"
                    }:
                        summary["compared_cases"] += 1
                    elif case["comparison"]["status"] == "inconclusive":
                        summary["inconclusive_comparisons"] += 1
                    if case["comparison"]["bag_difference_found"]:
                        summary["bag_differences"] += 1
                        if case["ground_truth"] == "EQ":
                            summary["eq_disagreements"] += 1
                        elif case["ground_truth"] == "NEQ":
                            summary["neq_witnesses"] += 1
                    replay = case.get("sqlite_replay", {})
                    summary[f"sqlite_{replay.get('status', 'missing')}"] += 1
                    summary["sqlite_instances"] += replay.get("tested_instances", 0)
                    summary["sqlite_query_executions"] += replay.get("query_executions", 0)
                    summary["sqlite_semantic_mismatches"] += replay.get(
                        "semantic_mismatches", 0
                    )
                print(json.dumps(case, default=str), file=output, flush=True)
    finally:
        if args.output:
            output.close()
    print(json.dumps({"summary": dict(summary)}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
