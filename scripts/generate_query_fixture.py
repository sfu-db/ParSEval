#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import deque
import multiprocessing as mp
from queue import Empty

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlglot
from sqlglot import exp

from parseval.db_manager import DBManager
from parseval.generator import (
    BmcBounds,
    generate_query_database,
)
from parseval.instance import Instance
from parseval.plan.explain import PlanError


DEFAULT_DEV = Path("data/sqlite/dev.json")
DEFAULT_SCHEMA = Path("data/sqlite/schema.json")

@dataclass(frozen=True)
class PipelineTask:
    dataset_index: int
    query: dict[str, Any]
    ddl: str
    connection_string: str
    db_path: str
    bounds: BmcBounds
    dialect: str
    timeout: int
    write_db: bool
    
def run_pipeline_task(task: PipelineTask) -> PipelineRecord:
    """
    Execute one query-generation pipeline inside a worker process.

    This function must remain at module scope so multiprocessing can pickle it.
    """
    return run_query_pipeline(
        task.dataset_index,
        task.query,
        ddl=task.ddl,
        connection_string=task.connection_string,
        db_path=Path(task.db_path),
        bounds=task.bounds,
        dialect=task.dialect,
        timeout=task.timeout,
        write_db=task.write_db,
    )
    
def print_pipeline_record(record: PipelineRecord) -> None:
    print(
        (
            f"[{record.status}] index={record.dataset_index} "
            f"qid={record.question_id} db={record.db_id} "
            f"elapsed={record.elapsed_seconds:.3f}s "
            f"rows={record.validation_rows} "
            f"instance_rows={record.instance_row_total} "
            f"single_row_violation={record.single_row_violation} "
            f"reason={record.reason}"
        ),
        file=sys.stderr,
        flush=True,
    )
def _expects_single_row(sql: str, dialect: str, ddl: str | None = None) -> bool:
    """
    Checks if a query is structurally expected to return exactly 1 row 
    (via LIMIT 1, global aggregation without a GROUP BY, or a schema-derived
    unique-key path from a literal predicate to every projected table).
    """
    try:
        parsed = sqlglot.parse_one(sql, read=dialect)
        
        # 1. Check for LIMIT 1
        limit = parsed.args.get("limit")
        if limit and isinstance(limit.expression, exp.Literal) and limit.expression.this == "1":
            return True
            
        # 2. Check for Global Aggregation
        if isinstance(parsed, exp.Select):
            # True if any selected expression contains an aggregate function
            has_agg = any(select.find(exp.AggFunc) is not None for select in parsed.selects)
            # True if there is a GROUP BY clause
            has_group_by = parsed.args.get("group") is not None
            
            if has_agg and not has_group_by:
                return True

            if ddl and _unique_key_single_row_shape(parsed, ddl, dialect):
                return True

        return False
    except Exception:
        # If parsing fails, default to False so we still flag it strictly
        return False


def _unique_key_single_row_shape(select: exp.Select, ddl: str, dialect: str) -> bool:
    aliases = _table_aliases(select)
    if not aliases:
        return False

    instance = Instance(ddl, name="single_row_shape", dialect=dialect)
    fixed_aliases: set[str] = set()
    unique_edges: dict[str, set[str]] = {alias: set() for alias in aliases}

    for predicate in _and_terms(select.args.get("where")):
        if not isinstance(predicate, exp.EQ):
            continue
        left = predicate.this
        right = predicate.expression
        if isinstance(left, exp.Column) and isinstance(right, exp.Literal):
            alias = _column_alias(left, aliases, instance)
            if alias and _is_unique_column(instance, aliases[alias], left.name):
                fixed_aliases.add(alias)
        elif isinstance(right, exp.Column) and isinstance(left, exp.Literal):
            alias = _column_alias(right, aliases, instance)
            if alias and _is_unique_column(instance, aliases[alias], right.name):
                fixed_aliases.add(alias)

    for predicate in select.find_all(exp.EQ):
        left = predicate.this
        right = predicate.expression
        if not isinstance(left, exp.Column) or not isinstance(right, exp.Column):
            continue
        left_alias = _column_alias(left, aliases, instance)
        right_alias = _column_alias(right, aliases, instance)
        if not left_alias or not right_alias or left_alias == right_alias:
            continue
        if _is_unique_column(instance, aliases[right_alias], right.name):
            unique_edges[left_alias].add(right_alias)
        if _is_unique_column(instance, aliases[left_alias], left.name):
            unique_edges[right_alias].add(left_alias)

    selected_aliases = {
        alias
        for projection in select.selects
        for column in projection.find_all(exp.Column)
        if (alias := _column_alias(column, aliases, instance))
    }
    if not fixed_aliases or not selected_aliases:
        return False

    reachable = set(fixed_aliases)
    queue = deque(fixed_aliases)
    while queue:
        alias = queue.popleft()
        for next_alias in unique_edges.get(alias, ()):
            if next_alias not in reachable:
                reachable.add(next_alias)
                queue.append(next_alias)

    return selected_aliases <= reachable


def _table_aliases(select: exp.Select) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for table in select.find_all(exp.Table):
        if table.find_ancestor(exp.Subquery):
            continue
        aliases[_alias_key(table.alias_or_name)] = table.name
    return aliases


def _and_terms(node: exp.Expression | None):
    if node is None:
        return
    expression = node.this if isinstance(node, exp.Where) else node
    if isinstance(expression, exp.And):
        yield from _and_terms(expression.this)
        yield from _and_terms(expression.expression)
    else:
        yield expression


def _column_alias(
    column: exp.Column,
    aliases: Mapping[str, str],
    instance: Instance,
) -> str | None:
    if column.table:
        alias = _alias_key(column.table)
        return alias if alias in aliases else None

    matches = [
        alias
        for alias, table_name in aliases.items()
        if _column_exists(instance, table_name, column.name)
    ]
    return matches[0] if len(matches) == 1 else None


def _column_exists(instance: Instance, table_name: str, column_name: str) -> bool:
    try:
        instance.resolve_column(table_name, column_name)
    except Exception:
        return False
    return True


def _is_unique_column(instance: Instance, table_name: str, column_name: str) -> bool:
    try:
        return instance.is_unique(table_name, column_name)
    except Exception:
        return False


def _alias_key(value: str) -> str:
    return value.casefold()


@dataclass
class PipelineRecord:
    dataset_index: int
    question_id: Any
    db_id: str
    difficulty: str
    status: str
    reason: str
    elapsed_seconds: float
    connection_string: str
    db_path: str
    validation_rows: int
    inserted_rows: int
    sql: str
    instance_row_total: int = field(default=0)
    coverage_ratio: float = field(default=0.0)
    obligations_total: int = field(default=0)
    obligations_covered: int = field(default=0)
    obligations_unsupported: int = field(default=0)
    obligations_infeasible: int = field(default=0)
    single_row_violation: bool = field(default=False)


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


def database_path_for_query(out_dir: Path, *, db_id: str, question_id: Any) -> Path:
    safe_db_id = _safe_filename(db_id)
    safe_question_id = _safe_filename(str(question_id))
    return out_dir / f"{safe_db_id}_{safe_question_id}.sqlite"


def connection_for_query(path: Path, *, dialect: str) -> str:
    if dialect == "sqlite":
        return f"sqlite:///{path}"
    return str(path)


def materialize_instance(
    ddl: str,
    *,
    name: str,
    rows: Mapping[exp.Table, Sequence[Mapping[exp.Identifier, object]]],
    dialect: str,
) -> Instance:
    instance = Instance(ddl, name=name, dialect=dialect)
    instance.create_rows(rows)
    return instance


def validate_query(
    *,
    connection_string: str,
    dialect: str,
    sql: str,
    timeout: int,
) -> int:
    with DBManager().get_connection(connection_string, dialect) as conn:
        rows = conn.execute(sql, fetch="all", timeout=timeout)
    return len(rows or [])


def instance_row_total(instance: Instance) -> int:
    return sum(
        len(instance.get_rows(table_node))
        for table_node in instance.schema.fk_safe_table_order()
    )


def run_query_pipeline(
    dataset_index: int,
    query: Mapping[str, Any],
    *,
    ddl: str,
    connection_string: str,
    db_path: Path,
    bounds: BmcBounds,
    dialect: str,
    timeout: int,
    write_db: bool,
) -> PipelineRecord:
    question_id = query.get("question_id")
    db_id = str(query["db_id"])
    sql = str(query["SQL"])
    started = time.perf_counter()

    try:
        result = generate_query_database(ddl, sql, dialect=dialect, bounds=bounds)
    except PlanError as e:
        return PipelineRecord(
            dataset_index=dataset_index,
            question_id=question_id,
            db_id=db_id,
            difficulty=str(query.get("difficulty", "?")),
            status="plan_error",
            reason=str(e),
            elapsed_seconds=time.perf_counter() - started,
            connection_string=connection_string,
            db_path=str(db_path),
            validation_rows=0,
            inserted_rows=0,
            instance_row_total=0,
            sql=sql,
            coverage_ratio=0.0,
            obligations_total=0,
            obligations_covered=0,
            obligations_unsupported=0,
            obligations_infeasible=0,
        )

    status = result.generation.status
    reason = result.generation.reason
    validation_rows = 0
    inserted_rows = 0
    n_instance_rows = 0
    single_row_violation = False
    coverage = _coverage_counts(result)

    if result.generation.status == "sat":
        instance = result
        n_instance_rows = instance_row_total(instance)
        validation_rows = (
            len(result.generation.root_schema.rows)
            if result.generation.root_schema is not None
            else 0
        )
        if write_db:
            from parseval.instance import to_db
            to_db(
                instance,
                connection_string,
                dialect=dialect,
                truncate_first=True,
            )

            inserted_rows = sum(
                len(instance.get_rows(table_node))
                for table_node in instance.tables
            )
            # getattr(write_result, "inserted_rows", 0)
            validation_rows = validate_query(
                connection_string=connection_string,
                dialect=dialect,
                sql=sql,
                timeout=timeout,
            )
            if validation_rows == 0:
                status = "empty_result"
                reason = "empty_query_result"
            elif validation_rows == 1:
                if not _expects_single_row(sql, dialect, ddl):
                    single_row_violation = True
                    status = "single_row_violation"
                    reason = "unexpected_single_row_result"
            if validation_rows == 0:
                status = "empty_result"
                reason = "empty_query_result"

    return PipelineRecord(
        dataset_index=dataset_index,
        question_id=question_id,
        db_id=db_id,
        difficulty=str(query.get("difficulty", "?")),
        status=status,
        reason=reason,
        elapsed_seconds=time.perf_counter() - started,
        connection_string=connection_string,
        db_path=str(db_path),
        validation_rows=validation_rows,
        inserted_rows=inserted_rows,
        instance_row_total=n_instance_rows,
        sql=sql,
        coverage_ratio=result.generation.coverage_ratio,
        obligations_total=coverage["total"],
        obligations_covered=coverage["covered"],
        obligations_unsupported=coverage["unsupported"],
        obligations_infeasible=coverage["infeasible"],
        single_row_violation=single_row_violation
    )


def summarize(records: Sequence[PipelineRecord]) -> dict[str, Any]:
    obligations_total = sum(record.obligations_total for record in records)
    obligations_covered = sum(record.obligations_covered for record in records)
    return {
        "total": len(records),
        "sat": sum(1 for r in records if r.status == "sat"),
        "empty_result": sum(1 for r in records if r.status == "empty_result"),
        "validation_error": sum(1 for r in records if r.status == "validation_error"),
        "plan_error": sum(1 for r in records if r.status == "plan_error"),
        "bounded_unknown": sum(1 for r in records if r.status == "bounded_unknown"),
        "unknown": sum(1 for r in records if r.status == "unknown"),
        "unsat": sum(1 for r in records if r.status == "unsat"),
        "coverage_ratio_avg": (
            sum(record.coverage_ratio for record in records) / len(records)
            if records
            else 0.0
        ),
        "obligations_total": obligations_total,
        "obligations_covered": obligations_covered,
        "obligations_unsupported": sum(
            record.obligations_unsupported for record in records
        ),
        "obligations_infeasible": sum(
            record.obligations_infeasible for record in records
        ),
        "coverage_ratio_obligations": (
            obligations_covered / obligations_total if obligations_total else 1.0
        ),
        "single_row_violation": sum(1 for r in records if r.single_row_violation), # NEW SUMMARY KEY
        "worker_error": sum(1 for r in records if r.status == "worker_error"),
        "timeout": sum(1 for r in records if r.status == "timeout"),
        "results": [asdict(record) for record in records],
    }


def _coverage_counts(result: Instance) -> dict[str, int]:
    obligations = result.generation.obligations
    return {
        "total": len(obligations),
        "covered": sum(1 for item in obligations if item.status == "covered"),
        "unsupported": sum(
            1 for item in obligations if item.status == "unsupported"
        ),
        "infeasible": sum(
            1 for item in obligations if item.status == "infeasible"
        ),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate query fixtures, persist them through Instance.to_db(), "
            "and require the original query to return at least one row."
        )
    )
    parser.add_argument("--dev", type=Path, default=DEFAULT_DEV)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--question-id")
    parser.add_argument("--db-id")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out-dir", type=Path, default=Path("tmp/query_fixtures"))
    parser.add_argument("--connection-string")
    parser.add_argument(
        "--dialect",
        default="sqlite",
        choices=("sqlite", "mysql", "postgres"),
    )
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--timeout", type=int, default=15)
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="Persist each generated instance and validate by executing SQL against the DB.",
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--table-rows", type=int, default=1)
    parser.add_argument("--join-width", type=int, default=1)
    parser.add_argument("--result-rows", type=int, default=3)
    parser.add_argument("--groups", type=int, default=3)
    parser.add_argument("--rows-per-group", type=int, default=3)
    parser.add_argument("--subquery-rows", type=int, default=1)
    parser.add_argument("--order-competitors", type=int, default=1)
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=4,
        help="Unsat search expansions only; structural LIMIT floors still apply at 4.",
    )
    parser.add_argument(
        "--max-table-rows",
        type=int,
        default=512,
        help="Hard cap on plan-derived and expanded table_rows floors.",
    )
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
            "Wall-clock seconds allowed per query's full generation pipeline "
            "(covers generate_query_database, to_db, and validation). A task "
            "exceeding this is terminated and recorded with status='timeout'. "
            "Set to 0 to disable (wait indefinitely, old behavior)."
        ),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.2,
        help="How often (seconds) the scheduler checks running tasks for completion/timeout.",
    )
    return parser.parse_args(argv)


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
    if args.dialect != "sqlite" and args.connection_string is None:
        raise SystemExit("connection_string_required_for_non_sqlite")
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
        result_rows=args.result_rows,
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
        connection_template=args.connection_string,
        bounds=bounds,
        dialect=args.dialect,
        timeout=args.timeout,
        write_db=args.write_db,
        workers=args.workers,
        mp_start_method=args.mp_start_method,
        task_timeout=args.task_timeout,
        poll_interval=args.poll_interval,
    )

    summary = summarize(records)
    payload = json.dumps(summary, indent=2, sort_keys=True)
    if args.summary_json is not None:
        args.summary_json.write_text(payload + "\n", encoding="utf-8")
    # print(payload)
    if summary["empty_result"] or summary["validation_error"] or summary["worker_error"]:
        return 1
    return 0


def _timeout_record(task: PipelineTask, elapsed: float, task_timeout: float) -> PipelineRecord:
    return PipelineRecord(
        dataset_index=task.dataset_index,
        question_id=task.query.get("question_id"),
        db_id=str(task.query["db_id"]),
        difficulty=str(task.query.get("difficulty", "?")),
        status="timeout",
        reason=f"exceeded_task_timeout_{task_timeout:.1f}s",
        elapsed_seconds=elapsed,
        connection_string=task.connection_string,
        db_path=task.db_path,
        validation_rows=0,
        inserted_rows=0,
        instance_row_total=0,
        sql=str(task.query["SQL"]),
    )


def _worker_error_record(task: PipelineTask, elapsed: float, exc: BaseException | str) -> PipelineRecord:
    reason = f"{type(exc).__name__}: {exc}" if isinstance(exc, BaseException) else str(exc)
    return PipelineRecord(
        dataset_index=task.dataset_index,
        question_id=task.query.get("question_id"),
        db_id=str(task.query["db_id"]),
        difficulty=str(task.query.get("difficulty", "?")),
        status="worker_error",
        reason=reason,
        elapsed_seconds=elapsed,
        connection_string=task.connection_string,
        db_path=task.db_path,
        validation_rows=0,
        inserted_rows=0,
        instance_row_total=0,
        sql=str(task.query["SQL"]),
    )


def _run_pipeline_task_to_queue(task: PipelineTask, result_queue: "mp.Queue") -> None:
    """
    Entry point run inside each spawned worker process. Must stay at module
    scope so it can be pickled by multiprocessing. Always attempts to put
    *something* on the queue, even on failure, so the parent never blocks
    waiting for a result that will never arrive.
    """
    try:
        record = run_pipeline_task(task)
    except BaseException as exc:  # noqa: BLE001 - we want to report *any* failure
        record = _worker_error_record(task, 0.0, exc)
    try:
        result_queue.put(record)
    except Exception:
        # If the record itself can't be pickled/sent, at least don't hang the
        # worker forever; the parent's join(timeout) will time it out.
        pass


def run_batch(
    selected: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    ddl_by_db: Mapping[str, str],
    out_dir: Path,
    connection_template: Optional[str],
    bounds: BmcBounds,
    dialect: str,
    timeout: int,
    write_db: bool,
    workers: int,
    mp_start_method: str,
    task_timeout: float = 0.0,
    poll_interval: float = 0.2,
) -> list[PipelineRecord]:
    """
    Runs every task to completion, enforcing task_timeout (wall-clock seconds)
    per task when task_timeout > 0. Unlike ProcessPoolExecutor, this manages
    individual multiprocessing.Process objects directly so a single stuck task
    can be terminated without blocking on, or tearing down, the whole pool.
    """
    max_workers = max(1, workers)

    tasks: list[PipelineTask] = []

    for dataset_index, query in selected:
        db_id = str(query["db_id"])
        question_id = query.get("question_id")

        db_path = database_path_for_query(
            out_dir,
            db_id=db_id,
            question_id=question_id,
        )

        connection_string = (
            connection_template.format(
                db_id=db_id,
                question_id=question_id,
                dataset_index=dataset_index,
            )
            if connection_template is not None
            else connection_for_query(db_path, dialect=dialect)
        )

        tasks.append(
            PipelineTask(
                dataset_index=dataset_index,
                query=dict(query),
                ddl=ddl_by_db[db_id],
                connection_string=connection_string,
                db_path=str(db_path),
                bounds=bounds,
                dialect=dialect,
                timeout=timeout,
                write_db=write_db,
            )
        )

    context = mp.get_context(mp_start_method)
    pending: deque[PipelineTask] = deque(tasks)
    # process -> (task, result_queue, started_at)
    running: dict[mp.process.BaseProcess, tuple[PipelineTask, "mp.Queue", float]] = {}
    records: list[PipelineRecord] = []

    def launch(task: PipelineTask) -> None:
        result_queue = context.Queue(maxsize=1)
        process = context.Process(
            target=_run_pipeline_task_to_queue,
            args=(task, result_queue),
            daemon=True,
        )
        process.start()
        running[process] = (task, result_queue, time.monotonic())

    def finish(process: mp.process.BaseProcess, record: PipelineRecord) -> None:
        running.pop(process, None)
        print_pipeline_record(record)
        records.append(record)
        if pending:
            launch(pending.popleft())

    while pending and len(running) < max_workers:
        launch(pending.popleft())

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
                        # terminate() didn't land (rare) - escalate.
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


def _safe_filename(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)


if __name__ == "__main__":
    raise SystemExit(main())