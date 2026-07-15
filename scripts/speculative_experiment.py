#!/usr/bin/env python3
"""Run the speculative seeder against real-world datasets with DB validation.

Loads query records and schema DDL from the data/ directory, runs
``speculate()`` on each selected query, persists the seeded instance to a
SQLite database, executes the original query, and reports whether the query
returned non-empty results along with the result shape.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from queue import Empty
from typing import Any, Mapping, Optional, Sequence

from sqlglot import exp

from parseval.db_manager import get_connection, execute_query
from parseval.generator.bounds import BmcBounds
from parseval.generator.speculate import speculate
from parseval.instance import Instance


DEFAULT_DEV = Path("data/sqlite/dev.json")
DEFAULT_SCHEMA = Path("data/sqlite/schema.json")


@dataclass
class SpecRecord:
    dataset_index: int
    question_id: Any
    db_id: str
    difficulty: str
    # Speculative seeder outcome
    seed_status: str          # "sat" | "unsat" | "error" | "timeout"
    seed_reason: str
    relaxation_needed: int
    elapsed_seconds: float
    sql: str
    instance_row_total: int = field(default=0)
    # Database validation outcome
    validation_rows: int = field(default=0)
    column_count: int = field(default=0)
    column_types: list[str] = field(default_factory=list)
    db_path: str = field(default="")
    shape_tags: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class SpecTask:
    dataset_index: int
    query: dict[str, Any]
    ddl: str
    dialect: str
    bounds: BmcBounds
    out_dir: str
    timeout: int


def sql_shape_tags(sql: str) -> tuple[str, ...]:
    text = sql.lower()
    tags: list[str] = []
    if re.search(r"\(\s*select\b", text):
        tags.append("subquery")
    if re.search(r"\bunion(?:\s+all)?\b", text):
        tags.append("union")
    if re.search(r"\bintersect\b", text):
        tags.append("intersect")
    if re.search(r"\bexcept\b", text):
        tags.append("except")
    if "case when" in text:
        tags.append("case_when")
    if "group by" in text:
        tags.append("group_by")
    if "having" in text:
        tags.append("having")
    if re.search(r"\bjoin\b", text):
        tags.append("join")
    if re.search(r"\border\s+by\b", text):
        tags.append("order_by")
    if re.search(r"\blimit\b", text):
        tags.append("limit")
    if re.search(r"\boffset\b", text):
        tags.append("offset")
    if re.search(r"\blike\b", text):
        tags.append("like")
    if re.search(r"\bin\s*\(", text):
        tags.append("in")
    if re.search(r"\bbetween\b", text):
        tags.append("between")
    if re.search(r"\bnot\s+null\b", text):
        tags.append("not_null")
    if re.search(r"\bis\s+null\b", text):
        tags.append("is_null")
    if re.search(r"\bwindow\b", text):
        tags.append("window")
    return tuple(tags or ("simple",))


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def schema_entry_to_ddl(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Sequence):
        parts = [str(stmt).strip().rstrip(";") for stmt in entry if str(stmt).strip()]
        return ";\n".join(parts) + (";" if parts else "")
    raise TypeError(f"unsupported_schema_entry:{type(entry)!r}")


def select_queries(
    queries: Sequence[Mapping[str, Any]],
    *,
    question_id: Optional[str],
    db_id: Optional[str],
    start: int,
    limit: Optional[int],
) -> list[tuple[int, Mapping[str, Any]]]:
    selected = list(enumerate(queries))
    if question_id is not None:
        selected = [
            (index, query)
            for index, query in selected
            if str(query.get("question_id")) == question_id
        ]
    if db_id is not None:
        selected = [
            (index, query)
            for index, query in selected
            if str(query.get("db_id")) == db_id
        ]
    if question_id is None and start:
        selected = [(index, query) for index, query in selected if index >= start]
    if limit is not None:
        selected = selected[:limit]
    return selected


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


def database_path_for_query(out_dir: Path, *, db_id: str, question_id: Any) -> Path:
    safe_db_id = _safe_filename(db_id)
    safe_question_id = _safe_filename(str(question_id))
    return out_dir / f"{safe_db_id}_{safe_question_id}.sqlite"


def run_speculative(
    dataset_index: int,
    query: Mapping[str, Any],
    *,
    ddl: str,
    dialect: str,
    bounds: BmcBounds,
    out_dir: Path,
    timeout: int,
) -> SpecRecord:
    question_id = query.get("question_id")
    db_id = str(query["db_id"])
    sql = str(query["SQL"])
    tags = sql_shape_tags(sql)
    db_path = database_path_for_query(out_dir, db_id=db_id, question_id=question_id)
    connection_string = f"sqlite:///{db_path}"

    started = time.perf_counter()
    relaxation_used = -1
    nrows = 0
    inst: Instance | None = None

    for attempt in range(4):
        inst = speculate(ddl, sql, dialect=dialect, bounds=bounds)
        if inst is None:
            relaxation_used = attempt
            break
        nrows = sum(
            len(inst.get_rows(table_node))
            for table_node in inst.schema.fk_safe_table_order()
        )
        relaxation_used = attempt
        if nrows > 0:
            break

    elapsed = time.perf_counter() - started

    if inst is None:
        return SpecRecord(
            dataset_index=dataset_index, question_id=question_id,
            db_id=db_id, difficulty=str(query.get("difficulty", "?")),
            seed_status="error", seed_reason="returned_none",
            relaxation_needed=relaxation_used, elapsed_seconds=elapsed,
            sql=sql, instance_row_total=0, shape_tags=tags, db_path=str(db_path),
        )

    if nrows == 0:
        return SpecRecord(
            dataset_index=dataset_index, question_id=question_id,
            db_id=db_id, difficulty=str(query.get("difficulty", "?")),
            seed_status="unsat", seed_reason="all_relaxations_exhausted",
            relaxation_needed=relaxation_used, elapsed_seconds=elapsed,
            sql=sql, instance_row_total=0, shape_tags=tags, db_path=str(db_path),
        )

    # ── Persist and validate ──────────────────────────────────────
    db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from parseval.instance import to_db
        to_db(inst, connection_string, dialect=dialect, truncate_first=True)
    except Exception as e:
        return SpecRecord(
            dataset_index=dataset_index, question_id=question_id,
            db_id=db_id, difficulty=str(query.get("difficulty", "?")),
            seed_status="sat", seed_reason="",
            relaxation_needed=relaxation_used, elapsed_seconds=elapsed,
            sql=sql, instance_row_total=nrows,
            validation_rows=0, column_count=0,
            db_path=str(db_path), shape_tags=tags,
        )

    # ── Execute the original query ────────────────────────────────
    validation_rows = 0
    column_count = 0
    column_types: list[str] = []
    try:
        with get_connection(connection_string, dialect) as conn:
            rows = conn.execute(sql, fetch="all", timeout=timeout)
            if rows:
                validation_rows = len(rows)
                first = rows[0]
                column_count = len(first)
                column_types = [_infer_column_type(v) for v in first]
    except Exception as e:
        return SpecRecord(
            dataset_index=dataset_index, question_id=question_id,
            db_id=db_id, difficulty=str(query.get("difficulty", "?")),
            seed_status="sat", seed_reason=f"validation_error:{e}",
            relaxation_needed=relaxation_used, elapsed_seconds=elapsed,
            sql=sql, instance_row_total=nrows,
            validation_rows=0, column_count=0,
            db_path=str(db_path), shape_tags=tags,
        )

    seed_status = "sat" if validation_rows > 0 else "empty_result"
    seed_reason = "" if validation_rows > 0 else "empty_query_result"

    return SpecRecord(
        dataset_index=dataset_index, question_id=question_id,
        db_id=db_id, difficulty=str(query.get("difficulty", "?")),
        seed_status=seed_status, seed_reason=seed_reason,
        relaxation_needed=relaxation_used, elapsed_seconds=elapsed,
        sql=sql, instance_row_total=nrows,
        validation_rows=validation_rows,
        column_count=column_count,
        column_types=column_types,
        db_path=str(db_path),
        shape_tags=tags,
    )


def run_spec_task(task: SpecTask) -> SpecRecord:
    """
    Execute one speculative-seeding pipeline inside a worker process.

    Must remain at module scope so multiprocessing can pickle it.
    """
    return run_speculative(
        task.dataset_index,
        task.query,
        ddl=task.ddl,
        dialect=task.dialect,
        bounds=task.bounds,
        out_dir=Path(task.out_dir),
        timeout=task.timeout,
    )


def _infer_column_type(value: Any) -> str:
    if value is None:
        return "null"
    t = type(value)
    if t is int:
        return "int"
    if t is float:
        return "float"
    if t is bool:
        return "bool"
    if t is str:
        return "text"
    if isinstance(value, bytes):
        return "blob"
    return t.__name__


def print_spec_record(record: SpecRecord) -> None:
    seed_mark = (
        "OK" if record.seed_status == "sat"
        else "EM" if record.seed_status == "empty_result"
        else "TO" if record.seed_status == "timeout"
        else "UN"
    )
    print(
        f"[{seed_mark}] index={record.dataset_index} "
        f"qid={record.question_id} db={record.db_id} "
        f"elapsed={record.elapsed_seconds:.3f}s "
        f"seed_rows={record.instance_row_total} "
        f"db_rows={record.validation_rows} "
        f"cols={record.column_count} "
        f"relax={record.relaxation_needed} "
        f"reason={record.seed_reason}",
        file=sys.stderr,
        flush=True,
    )


def _timeout_record(task: SpecTask, elapsed: float, task_timeout: float) -> SpecRecord:
    query = task.query
    db_id = str(query["db_id"])
    question_id = query.get("question_id")
    sql = str(query["SQL"])
    db_path = database_path_for_query(Path(task.out_dir), db_id=db_id, question_id=question_id)
    return SpecRecord(
        dataset_index=task.dataset_index,
        question_id=question_id,
        db_id=db_id,
        difficulty=str(query.get("difficulty", "?")),
        seed_status="timeout",
        seed_reason=f"exceeded_task_timeout_{task_timeout:.1f}s",
        relaxation_needed=-1,
        elapsed_seconds=elapsed,
        sql=sql,
        instance_row_total=0,
        db_path=str(db_path),
        shape_tags=sql_shape_tags(sql),
    )


def _worker_error_record(task: SpecTask, elapsed: float, exc: BaseException | str) -> SpecRecord:
    reason = f"{type(exc).__name__}: {exc}" if isinstance(exc, BaseException) else str(exc)
    query = task.query
    db_id = str(query["db_id"])
    question_id = query.get("question_id")
    sql = str(query["SQL"])
    db_path = database_path_for_query(Path(task.out_dir), db_id=db_id, question_id=question_id)
    return SpecRecord(
        dataset_index=task.dataset_index,
        question_id=question_id,
        db_id=db_id,
        difficulty=str(query.get("difficulty", "?")),
        seed_status="error",
        seed_reason=reason,
        relaxation_needed=-1,
        elapsed_seconds=elapsed,
        sql=sql,
        instance_row_total=0,
        db_path=str(db_path),
        shape_tags=sql_shape_tags(sql),
    )


def _run_spec_task_to_queue(task: SpecTask, result_queue: "mp.Queue") -> None:
    """
    Entry point run inside each spawned worker process. Must stay at module
    scope so it can be pickled by multiprocessing. Always attempts to put
    *something* on the queue, even on failure, so the parent never blocks
    waiting on a result that will never arrive.
    """
    try:
        record = run_spec_task(task)
    except BaseException as exc:  # noqa: BLE001 - report any failure, not just Exception
        record = _worker_error_record(task, 0.0, exc)
    try:
        result_queue.put(record)
    except Exception:
        # If the record can't be pickled/sent, don't hang the worker forever;
        # the parent's timeout/liveness check will still recover.
        pass


def summarize(records: Sequence[SpecRecord]) -> dict[str, Any]:
    from collections import Counter

    seed_status_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    relaxation_counts: Counter[int] = Counter()

    for record in records:
        seed_status_counts[record.seed_status] += 1
        for tag in record.shape_tags:
            tag_counts[tag] += 1
        relaxation_counts[record.relaxation_needed] += 1

    sat_records = [r for r in records if r.seed_status == "sat"]
    return {
        "total": len(records),
        "sat_via_db": sum(1 for r in records if r.validation_rows > 0),
        "empty_result": sum(1 for r in records if r.seed_status == "empty_result"),
        "timeout": sum(1 for r in records if r.seed_status == "timeout"),
        "validation_error": sum(1 for r in records if r.seed_status not in ("sat", "unsat", "empty_result")),
        "seed_status_counts": dict(seed_status_counts),
        "elapsed_total_seconds": sum(r.elapsed_seconds for r in records),
        "elapsed_avg_seconds": sum(r.elapsed_seconds for r in records) / len(records) if records else 0.0,
        "elapsed_sat_avg_seconds": sum(r.elapsed_seconds for r in sat_records) / len(sat_records) if sat_records else 0.0,
        "instance_rows_avg": sum(r.instance_row_total for r in sat_records) / len(sat_records) if sat_records else 0.0,
        "validation_rows_avg": sum(r.validation_rows for r in sat_records) / len(sat_records) if sat_records else 0.0,
        "column_count_avg": sum(r.column_count for r in sat_records) / len(sat_records) if sat_records else 0.0,
        "relaxation_distribution": {
            str(k): v for k, v in sorted(relaxation_counts.items())
        },
        "shape_tag_counts": dict(tag_counts.most_common()),
        "records": [asdict(record) for record in records],
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the speculative seeder against real-world query datasets, "
            "persist to SQLite, execute the query, and report result shape."
        )
    )
    parser.add_argument("--dev", type=Path, default=DEFAULT_DEV)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--question-id")
    parser.add_argument("--db-id")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out-dir", type=Path, default=Path("tmp/experiment_dbs"))
    parser.add_argument("--dialect", default="sqlite", choices=("sqlite", "mysql", "postgres"))
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--table-rows", type=int, default=1)
    parser.add_argument("--join-width", type=int, default=1)
    parser.add_argument("--groups", type=int, default=1)
    parser.add_argument("--rows-per-group", type=int, default=1)
    parser.add_argument("--subquery-rows", type=int, default=1)
    parser.add_argument("--order-competitors", type=int, default=1)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--max-table-rows", type=int, default=512)
    parser.add_argument(
        "--mp-start-method",
        choices=("spawn", "forkserver", "fork"),
        default="spawn",
        help=(
            "Multiprocessing start method. 'spawn' is safest and works on macOS; "
            "'forkserver' may be faster on Linux; 'fork' should only be used when "
            "all native libraries involved are fork-safe."
        ),
    )
    parser.add_argument(
        "--task-timeout",
        type=float,
        default=120.0,
        help=(
            "Wall-clock seconds allowed per query's full speculative-seed "
            "pipeline (covers speculate(), to_db, and validation). A task "
            "exceeding this is terminated and recorded with "
            "seed_status='timeout'. Set to 0 to disable (wait indefinitely)."
        ),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.2,
        help="How often (seconds) the scheduler checks running tasks for completion/timeout.",
    )
    return parser.parse_args(argv)


def run_batch(
    selected: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    ddl_by_db: Mapping[str, str],
    out_dir: Path,
    dialect: str,
    bounds: BmcBounds,
    timeout: int,
    workers: int,
    mp_start_method: str,
    task_timeout: float = 0.0,
    poll_interval: float = 0.2,
) -> list[SpecRecord]:
    """
    Runs every task to completion, enforcing task_timeout (wall-clock seconds)
    per task when task_timeout > 0. Manages individual multiprocessing.Process
    objects directly (rather than ProcessPoolExecutor) so a single stuck task
    can be terminated without blocking on, or tearing down, the whole pool.
    """
    max_workers = max(1, workers)

    tasks: list[SpecTask] = []
    for dataset_index, query in selected:
        db_id = str(query["db_id"])
        tasks.append(
            SpecTask(
                dataset_index=dataset_index,
                query=dict(query),
                ddl=ddl_by_db[db_id],
                dialect=dialect,
                bounds=bounds,
                out_dir=str(out_dir),
                timeout=timeout,
            )
        )

    context = mp.get_context(mp_start_method)
    pending: "list[SpecTask]" = list(tasks)
    running: dict[mp.process.BaseProcess, tuple[SpecTask, "mp.Queue", float]] = {}
    records: list[SpecRecord] = []

    def launch(task: SpecTask) -> None:
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_run_spec_task_to_queue,
            args=(task, result_queue),
            daemon=True,
        )
        process.start()
        running[process] = (task, result_queue, time.monotonic())

    def finish(process: mp.process.BaseProcess, record: SpecRecord) -> None:
        running.pop(process, None)
        print_spec_record(record)
        records.append(record)
        if pending:
            launch(pending.pop(0))

    while pending and len(running) < max_workers:
        launch(pending.pop(0))

    try:
        while running:
            for process in list(running.keys()):
                task, result_queue, started_at = running[process]
                elapsed = time.monotonic() - started_at

                if not process.is_alive():
                    try:
                        record = result_queue.get_nowait()
                    except Empty:
                        exit_code = process.exitcode
                        record = _worker_error_record(
                            task,
                            elapsed,
                            f"worker exited (code={exit_code}) without a result",
                        )
                    process.join()
                    finish(process, record)
                    continue

                if task_timeout and elapsed > task_timeout:
                    process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                    record = _timeout_record(task, elapsed, task_timeout)
                    finish(process, record)
                    continue

            if running:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        # Best-effort cleanup so Ctrl-C doesn't leave orphaned children behind.
        for process, (task, _queue, started_at) in list(running.items()):
            elapsed = time.monotonic() - started_at
            process.terminate()
            process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            records.append(_worker_error_record(task, elapsed, "interrupted_by_user"))
        raise

    return sorted(records, key=lambda record: record.dataset_index)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    queries = load_json(args.dev)
    schemas = load_json(args.schema)
    selected = select_queries(
        queries,
        question_id=args.question_id,
        db_id=args.db_id,
        start=args.start,
        limit=args.limit,
    )
    if not selected:
        raise SystemExit("no_queries_selected")
    if args.summary_json is not None:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_dir.mkdir(parents=True, exist_ok=True)

    ddl_by_db = {db_id: schema_entry_to_ddl(entry) for db_id, entry in schemas.items()}
    missing = sorted({str(query["db_id"]) for _, query in selected} - set(ddl_by_db))
    if missing:
        raise SystemExit(f"missing_schema_dbs:{missing}")

    bounds = BmcBounds(
        table_rows=args.table_rows,
        join_width=args.join_width,
        groups=args.groups,
        rows_per_group=args.rows_per_group,
        subquery_rows=args.subquery_rows,
        order_competitors=args.order_competitors,
        max_iterations=args.max_iterations,
        max_table_rows=args.max_table_rows,
    )

    records = run_batch(
        selected,
        ddl_by_db=ddl_by_db,
        out_dir=args.out_dir,
        dialect=args.dialect,
        bounds=bounds,
        timeout=args.timeout,
        workers=args.workers,
        mp_start_method=args.mp_start_method,
        task_timeout=args.task_timeout,
        poll_interval=args.poll_interval,
    )

    summary = summarize(records)
    payload = json.dumps(summary, indent=2, sort_keys=True)
    if args.summary_json is not None:
        args.summary_json.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    empty = sum(1 for r in records if r.seed_status == "empty_result")
    errors = sum(1 for r in records if r.seed_status not in ("sat", "unsat", "empty_result"))
    return 1 if empty or errors else 0


if __name__ == "__main__":
    raise SystemExit(main())