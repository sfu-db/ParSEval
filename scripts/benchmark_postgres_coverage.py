"""Time data generation for each query in data/postgres.csv.

Each SQL statement is one input. A row records how long generation took and,
when requested, where the SQLite database was written.

Example:
    python scripts/benchmark_postgres_coverage.py --limit 0 --sqlite-dir results/postgres-sqlite
"""

from __future__ import annotations

import argparse
import csv
import json
import multiprocessing as mp
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "postgres.csv"
QUERIES = ("q1", "q2")


def _error(error: Exception) -> dict[str, str]:
    return {"type": type(error).__name__, "message": str(error)[:500]}


def run_query(
    row: dict[str, str],
    query_name: str,
    solver_timeout_ms: int,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
    sqlite_dir: Path | None = None,
    postgres_dsn: str | None = None,
    time_limit_s: float | None = None,
) -> dict[str, Any]:
    """Generate and replay one database for an original dataset SQL statement."""

    from experiments.sqlite import write_sqlite
    from parseval.catalog import Catalog
    from parseval.generator import GenerationConfig, generate

    started = time.monotonic()
    result: dict[str, Any] = {
        "dbid": row["dbid"],
        "index": row["index"],
        "query": query_name,
    }
    try:
        catalog = Catalog.from_ddl(row["schema_ddl"], dialect=row["dialect"])
        if progress:
            progress("generate", {"query": query_name})

        def on_attempt(attempt: Any) -> None:
            if progress:
                progress("solve", {"query": query_name, "target": attempt.label})

        def save(instance: Any) -> None:
            # Each accepted version replaces the previous one, so a run that
            # hits the case timeout still leaves its latest database.
            if sqlite_dir is None:
                return
            relative = Path(row["dbid"]) / row["index"] / query_name / "0.sqlite"
            write_sqlite(instance, sqlite_dir / relative, overwrite=True)
            result["sqlite"] = [relative.as_posix()]

        generated = generate(
            row[query_name],
            catalog,
            config=GenerationConfig(
                timeout_ms=solver_timeout_ms,
                time_limit_s=time_limit_s,
                # Benchmarks set PARSEVAL_NO_SPECULATION to measure generation without it.
                speculate=not os.environ.get("PARSEVAL_NO_SPECULATION"),
            ),
            on_attempt=on_attempt if progress else None,
            on_instance=save,
        )
        instances = () if generated.instance is None else (generated.instance,)
        result["elapsed_s"] = round(time.monotonic() - started, 3)
        result["instances"] = int(generated.instance is not None)
        result["populated"] = bool(generated.instance and generated.instance.row_count)
        from experiments.outcomes import corpus_outcomes
        result["query_outcomes"] = (
            corpus_outcomes(row[query_name], catalog, instances)
            if instances
            else []
        )
        result["productive"] = any(outcome["output_rows"] > 0
                                   for outcome in result["query_outcomes"])
        result["coverage"] = {
            "reached": len(generated.coverage.reached),
            "covered": len(generated.coverage.covered),
            "failed": generated.coverage.failed,
        }
        result["solves"] = [
            {"target": attempt.label, "status": attempt.status.value,
             "accepted": attempt.accepted, "reason": attempt.reason}
            for attempt in generated.attempts
        ]
        result["unsupported_generation"] = generated.unsupported is not None
        if generated.unsupported is not None:
            result["unsupported"] = generated.unsupported
        result["validation"] = "concrete_ir"
        if postgres_dsn is not None:
            from experiments.postgres import validate_corpus
            result["postgres"] = validate_corpus(row[query_name], row["schema_ddl"],
                                                 catalog, instances, postgres_dsn)
            result["validation"] = "postgres"
    except Exception as error:
        result["elapsed_s"] = round(time.monotonic() - started, 3)
        result["instances"] = 0
        result["populated"] = False
        result["productive"] = False
        result["error"] = _error(error)
    return result


def _worker(
    connection: Any,
    row: dict[str, str],
    query_name: str,
    solver_timeout_ms: int,
    sqlite_dir: str | None,
    postgres_dsn: str | None,
    time_limit_s: float,
) -> None:
    try:
        def progress(stage: str, details: dict[str, Any]) -> None:
            connection.send({"phase": stage, "details": details})

        connection.send({
            "result": run_query(
                row,
                query_name,
                solver_timeout_ms,
                progress,
                None if sqlite_dir is None else Path(sqlite_dir),
                postgres_dsn,
                time_limit_s,
            )
        })
    except BaseException as error:
        connection.send({"result": {
            "dbid": row.get("dbid"),
            "index": row.get("index"),
            "query": query_name,
            "instances": 0,
            "error": {"type": type(error).__name__, "message": str(error)[:500]},
        }})
    finally:
        connection.close()


def run_with_timeout(
    row: dict[str, str],
    query_name: str,
    solver_timeout_ms: int,
    case_timeout_s: float,
    sqlite_dir: Path | None = None,
    postgres_dsn: str | None = None,
) -> dict[str, Any]:
    context = mp.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(
        target=_worker,
        args=(
            sender,
            row,
            query_name,
            solver_timeout_ms,
            None if sqlite_dir is None else str(sqlite_dir),
            postgres_dsn,
            # Generation returns its best database in time for outcomes and replay.
            case_timeout_s * 0.75,
        ),
    )
    process.start()
    sender.close()
    deadline = time.monotonic() + case_timeout_s
    phase = "startup"
    try:
        while receiver.poll(max(0, deadline - time.monotonic())):
            try:
                message = receiver.recv()
            except EOFError:
                break
            if "result" in message:
                return message["result"]
            phase = message["phase"]
        process.join(timeout=0.05)
        if process.is_alive():
            record = {
                "dbid": row["dbid"],
                "index": row["index"],
                "query": query_name,
                "status": "timeout",
                "phase": phase,
                "elapsed_s": round(case_timeout_s, 3),
                "instances": 0,
            }
            if sqlite_dir is not None:
                relative = Path(row["dbid"]) / row["index"] / query_name
                saved = sorted((sqlite_dir / relative).glob("*.sqlite"))
                record["sqlite"] = [str(path.relative_to(sqlite_dir)) for path in saved]
                record["instances"] = len(saved)
            return record
        return {
            "dbid": row["dbid"],
            "index": row["index"],
            "query": query_name,
            "instances": 0,
            "error": {"type": "WorkerExit", "message": str(process.exitcode)},
        }
    finally:
        if process.is_alive():
            process.terminate()
        process.join()
        receiver.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DATA)
    parser.add_argument("--output", type=Path, help="Write one JSON object per query")
    parser.add_argument("--dbid", action="append", help="Select one or more database IDs")
    parser.add_argument("--index", action="append", help="Select one or more CSV index values")
    parser.add_argument("--selection", type=Path,
                        help="JSONL records selecting exact dbid/index/query triples")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum queries; 0 runs every statement in the CSV (default: 10)",
    )
    parser.add_argument(
        "--solver-timeout-ms",
        type=int,
        default=5_000,
        help="Z3 budget for one attempt (default: 5000)",
    )
    parser.add_argument(
        "--case-timeout-s",
        type=float,
        default=120,
        help="Wall-clock limit for one query (default: 120)",
    )
    parser.add_argument(
        "--sqlite-dir",
        type=Path,
        help="Write one SQLite database per generated instance under this directory",
    )
    parser.add_argument("--postgres-dsn", help="Replay the final database against this PostgreSQL server")
    args = parser.parse_args(argv)
    if (
        args.limit < 0
        or args.solver_timeout_ms <= 0
        or args.case_timeout_s <= 0
    ):
        parser.error("limit must be nonnegative; timeouts must be positive")
    selection = None
    if args.selection:
        with args.selection.open(encoding="utf-8") as handle:
            selection = {
                (record["dbid"], str(record["index"]), record["query"])
                for line in handle if line.strip()
                for record in (json.loads(line),)
                if all(key in record for key in ("dbid", "index", "query"))
            }

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
                for query_name in QUERIES:
                    if selection is not None and (row["dbid"], row["index"], query_name) not in selection:
                        continue
                    if args.limit and summary["queries"] >= args.limit:
                        break
                    record = run_with_timeout(
                        row,
                        query_name,
                        args.solver_timeout_ms,
                        args.case_timeout_s,
                        args.sqlite_dir,
                        args.postgres_dsn,
                    )
                    summary["queries"] += 1
                    summary["elapsed_s"] += record.get("elapsed_s", 0)
                    if record.get("status") == "timeout":
                        summary["timeouts"] += 1
                    elif "error" in record:
                        summary["errors"] += 1
                    elif record.get("unsupported_generation"):
                        summary["unsupported"] += 1
                    elif record.get("productive"):
                        summary["productive"] += 1
                    elif record.get("populated"):
                        summary["populated_no_output_change"] += 1
                    else:
                        summary["empty_only"] += 1
                    print(json.dumps(record, default=str), file=output, flush=True)
                else:
                    continue
                break
    finally:
        if args.output:
            output.close()
    summary["elapsed_s"] = round(summary["elapsed_s"], 3)
    print(json.dumps({"summary": dict(summary)}), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
