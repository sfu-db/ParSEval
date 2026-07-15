#!/usr/bin/env python3
"""Benchmark the symbolic pipeline on a set of SQL queries.

Measures per-query: plan explanation, public generation, and final row count.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from parseval.plan.explain import explain

from parseval.generator.symbolic.generate import generate


DEFAULT_DEV = Path("data/sqlite/dev.json")
DEFAULT_SCHEMA = Path("data/sqlite/schema.json")


@dataclass
class BenchmarkRecord:
    dataset_index: int
    question_id: Any
    db_id: str
    sql: str
    status: str
    reason: str = ""
    elapsed_total: float = 0.0
    elapsed_explain: float = 0.0
    elapsed_generate: float = 0.0
    n_results: int = 0
    n_instance_rows: int = 0


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


def run_benchmark(
    dataset_index: int,
    query: Mapping[str, Any],
    *,
    ddl: str,
    dialect: str,
) -> BenchmarkRecord:
    sql = str(query["SQL"])
    question_id = query.get("question_id")
    db_id = str(query["db_id"])
    started = time.perf_counter()

    # --- explain ---
    t0 = time.perf_counter()
    try:
        plan = explain(ddl, sql, dialect=dialect)
    except Exception as exc:
        return BenchmarkRecord(
            dataset_index=dataset_index,
            question_id=question_id,
            db_id=db_id,
            sql=sql,
            status="explain_error",
            reason=f"{type(exc).__name__}:{exc}",
            elapsed_total=time.perf_counter() - started,
        )
    elapsed_explain = time.perf_counter() - t0

    # --- generate (speculate + explain + encode + solve + materialize) ---
    t0 = time.perf_counter()
    try:
        rows = generate(ddl, sql, dialect=dialect)
    except Exception as exc:
        return BenchmarkRecord(
            dataset_index=dataset_index,
            question_id=question_id,
            db_id=db_id,
            sql=sql,
            status="generate_error",
            reason=f"{type(exc).__name__}:{exc}",
            elapsed_total=time.perf_counter() - started,
            elapsed_explain=elapsed_explain,
        )
    elapsed_generate = time.perf_counter() - t0

    n_instance_rows = sum(len(instance.get_rows(t)) for t in instance.schema.fk_safe_table_order())

    return BenchmarkRecord(
        dataset_index=dataset_index,
        question_id=question_id,
        db_id=db_id,
        sql=sql,
        status="ok",
        elapsed_total=time.perf_counter() - started,
        elapsed_explain=elapsed_explain,
        elapsed_generate=elapsed_generate,
        n_results=len(rows),
        n_instance_rows=n_instance_rows,
    )


def run_batch(
    selected: Sequence[tuple[int, Mapping[str, Any]]],
    *,
    ddl_by_db: Mapping[str, str],
    dialect: str,
    workers: int,
) -> list[BenchmarkRecord]:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    max_workers = max(1, workers)

    def run_one(item: tuple[int, Mapping[str, Any]]) -> BenchmarkRecord:
        dataset_index, query = item
        db_id = str(query["db_id"])
        record = run_benchmark(
            dataset_index,
            query,
            ddl=ddl_by_db[db_id],
            dialect=dialect,
        )
        print(
            (
                f"[{record.status}] index={record.dataset_index} "
                f"qid={record.question_id} db={record.db_id} "
                f"total={record.elapsed_total:.3f}s "
                f"results={record.n_results} "
                f"instance_rows={record.n_instance_rows} "
                f"reason={record.reason}"
            ),
            file=sys.stderr,
            flush=True,
        )
        return record

    if max_workers == 1:
        return [run_one(item) for item in selected]

    records: list[BenchmarkRecord] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(run_one, item) for item in selected]
        for future in as_completed(futures):
            records.append(future.result())
    return sorted(records, key=lambda r: r.dataset_index)


def summarize(records: Sequence[BenchmarkRecord]) -> dict[str, Any]:
    total = len(records)
    counts: dict[str, int] = {}
    for r in records:
        counts[r.status] = counts.get(r.status, 0) + 1

    timed = [r for r in records if r.elapsed_total > 0]
    avg_total = sum(r.elapsed_total for r in timed) / len(timed) if timed else 0.0
    avg_explain = sum(r.elapsed_explain for r in timed) / len(timed) if timed else 0.0
    avg_generate = sum(r.elapsed_generate for r in timed) / len(timed) if timed else 0.0

    return {
        "total": total,
        "status_counts": counts,
        "avg_elapsed_total": round(avg_total, 4),
        "avg_elapsed_explain": round(avg_explain, 4),
        "avg_elapsed_generate": round(avg_generate, 4),
        "results": [asdict(r) for r in records],
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark the symbolic pipeline on SQL queries."
    )
    parser.add_argument("--dev", type=Path, default=DEFAULT_DEV)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--question-id")
    parser.add_argument("--db-id")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--summary-json", type=Path)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--dialect", default="sqlite")
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
    if args.summary_json is not None:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)

    ddl_by_db = {db_id: schema_entry_to_ddl(entry) for db_id, entry in schemas.items()}
    missing = sorted({str(query["db_id"]) for _, query in selected} - set(ddl_by_db))
    if missing:
        raise SystemExit(f"missing_schema_dbs:{missing}")

    records = run_batch(
        selected,
        ddl_by_db=ddl_by_db,
        dialect=args.dialect,
        workers=args.workers,
    )

    summary = summarize(records)
    payload = json.dumps(summary, indent=2, sort_keys=True)
    if args.summary_json is not None:
        args.summary_json.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if summary["status_counts"].get("ok", 0) > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
