"""SQLite experiment runner — generate test databases for BIRD dataset queries."""

import json
import os
import argparse
import datetime
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

try:
    from tqdm import tqdm
except Exception:
    def tqdm(x, **kwargs):
        return x


def load_schema(schema_fp: str):
    with open(schema_fp) as f:
        return json.load(f)


def load_gold(gold_fp: str):
    with open(gold_fp) as f:
        return json.load(f)


def _process_bird_datagen_case(index, row, schemas, *, execute_sqlite=True):
    t0 = time.time()
    db_id = row.get("db_id")
    sql = row.get("SQL") or ""
    raw_ddls = schemas.get(db_id, [])
    ddls = raw_ddls if isinstance(raw_ddls, str) else ";".join(raw_ddls)
    record = {
        "index": index,
        "db_id": db_id,
        "difficulty": row.get("difficulty"),
        "sql": sql,
        "status": "unknown",
        "rows_generated": 0,
        "coverage": 0.0,
        "non_empty": False,
        "error_msg": "",
        "elapsed_time": 0.0,
    }
    try:
        from parseval.main import instantiate_db

        db_path = os.path.abspath(f"tmp/worker_{db_id}_{index}.db")
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        for suffix in ("", "-wal", "-shm"):
            path = f"{db_path}{suffix}"
            if os.path.exists(path):
                os.remove(path)
        connection_string = f"sqlite:///{db_path}"

        result = instantiate_db(
            sql, ddls, connection_string, "sqlite",
            db_id=f"{db_id}_{index}",
            max_iterations=10,
            atom_null=0,
            atom_dup=1,
        )

        record["rows_generated"] = result.generation.rows_generated
        record["coverage"] = result.generation.coverage
        if result.q_result is not None:
            record["non_empty"] = bool(result.q_result.rows)
            if result.q_result.error_msg:
                record["error_msg"] = result.q_result.error_msg
        if not result.success:
            record["status"] = "generation_error"
            record["error_msg"] = result.error_msg or record["error_msg"]
        elif record["non_empty"]:
            record["status"] = "non_empty"
        elif result.q_result and result.q_result.error_msg:
            record["status"] = "execution_error"
        elif record["rows_generated"] == 0:
            record["status"] = "empty_generation"
        else:
            record["status"] = "empty_result"
    except Exception as exc:
        record["status"] = "generation_error"
        record["error_msg"] = str(exc)[:300]
    finally:
        record["elapsed_time"] = round(time.time() - t0, 4)
    return record


def _process_bird_datagen_task(task):
    index, row, schemas, execute_sqlite = task
    return _process_bird_datagen_case(
        index,
        row,
        schemas,
        execute_sqlite=execute_sqlite,
    )


def summarize_datagen_results(records):
    total = len(records)
    status_counts = {}
    total_rows = 0
    coverages = []
    for record in records:
        status = record.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
        total_rows += int(record.get("rows_generated") or 0)
        cov = record.get("coverage")
        if cov is not None:
            coverages.append(cov)
    non_empty = status_counts.get("non_empty", 0)
    empty_generation = status_counts.get("empty_generation", 0)
    generation_errors = status_counts.get("generation_error", 0)
    witness_queries = total - empty_generation - generation_errors
    generated = total - generation_errors
    avg_coverage = round(sum(coverages) / len(coverages), 4) if coverages else 0.0
    return {
        "total_queries": total,
        "generated_queries": generated,
        "witness_queries": witness_queries,
        "non_empty_queries": non_empty,
        "generated_rows": total_rows,
        "average_coverage": avg_coverage,
        "status_counts": status_counts,
        "status_ratio": {
            key: round(value / total, 4) if total else 0
            for key, value in status_counts.items()
        },
        "witness_ratio": round(witness_queries / total, 4) if total else 0,
        "non_empty_ratio": round(non_empty / total, 4) if total else 0,
    }


def run_bird_datagen(
    schema_fp: str,
    gold_fp: str,
    *,
    limit: int | None = None,
    start: int = 0,
    execute_sqlite: bool = True,
    workers: int = 1,
):
    schemas = load_schema(schema_fp)
    gold = load_gold(gold_fp)
    selected = gold[start:]
    if limit is not None:
        selected = selected[:limit]

    tasks = [
        (start + offset, row, schemas, execute_sqlite)
        for offset, row in enumerate(selected)
    ]
    if workers <= 1:
        records = [
            _process_bird_datagen_task(task)
            for task in tqdm(tasks, desc="Generating BIRD test databases")
        ]
    else:
        records_by_index = {}
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_process_bird_datagen_task, task) for task in tasks]
            for future in tqdm(
                as_completed(futures),
                total=len(futures),
                desc="Generating BIRD test databases",
            ):
                record = future.result()
                records_by_index[record["index"]] = record
        records = [records_by_index[index] for index, *_rest in tasks]
    metrics = summarize_datagen_results(records)
    metrics["records"] = records
    return metrics


def run_bird_datagen_experiment(args):
    os.makedirs(args.output_dir, exist_ok=True)
    metrics = run_bird_datagen(
        schema_fp=args.schema_fp,
        gold_fp=args.gold_fp,
        limit=args.limit,
        start=args.start,
        execute_sqlite=not args.no_execute_sqlite,
        workers=args.workers,
    )

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_fp = os.path.join(args.output_dir, f"sqlite_datagen_{ts}.json")
    with open(out_fp, "w") as f:
        json.dump(metrics, f, indent=2)

    printable = {key: value for key, value in metrics.items() if key != "records"}
    print(json.dumps(printable, indent=2))
    print(f"Wrote {len(metrics['records'])} datagen records to {out_fp}")
    return metrics


def test_bird_datagen_smoke():
    try:
        import pytest
    except Exception:
        pytest = None
    if not os.path.exists("data/sqlite/schema.json") or not os.path.exists("data/sqlite/dev.json"):
        if pytest is None:
            return
        pytest.skip("BIRD SQLite fixtures are not available")

    limit = int(os.environ.get("BIRD_DATAGEN_LIMIT", "25"))
    min_non_empty_ratio = float(os.environ.get("BIRD_DATAGEN_MIN_NON_EMPTY_RATIO", "0.8"))
    metrics = run_bird_datagen(
        schema_fp="data/sqlite/schema.json",
        gold_fp="data/sqlite/dev.json",
        limit=limit,
        execute_sqlite=os.environ.get("BIRD_DATAGEN_NO_EXECUTE", "0") != "1",
    )

    assert metrics["total_queries"] == limit
    assert metrics["generated_rows"] > 0
    assert metrics["witness_ratio"] >= min_non_empty_ratio
    if os.environ.get("BIRD_DATAGEN_NO_EXECUTE", "0") != "1":
        assert metrics["non_empty_ratio"] >= min_non_empty_ratio


def test_bird_datagen_parallel_smoke():
    try:
        import pytest
    except Exception:
        pytest = None
    if not os.path.exists("data/sqlite/schema.json") or not os.path.exists("data/sqlite/dev.json"):
        if pytest is None:
            return
        pytest.skip("BIRD SQLite fixtures are not available")

    metrics = run_bird_datagen(
        schema_fp="data/sqlite/schema.json",
        gold_fp="data/sqlite/dev.json",
        limit=3,
        workers=2,
        execute_sqlite=False,
    )

    assert metrics["total_queries"] == 3
    assert [record["index"] for record in metrics["records"]] == [0, 1, 2]
    assert metrics["generated_rows"] > 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run ParSEval Data Generation experiment")
    parser.add_argument("--schema_fp", default="data/sqlite/schema.json")
    parser.add_argument("--gold_fp", default="data/sqlite/dev.json")
    parser.add_argument("--output_dir", default="results")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--no_execute_sqlite", action="store_true")
    args = parser.parse_args()
    run_bird_datagen_experiment(args)
