"""SQLite disprove experiment — load dataset, parallel run, summarize.

Usage::

    python scripts/exp_sqlite_disprover.py --workers 16
    python scripts/exp_sqlite_disprover.py --limit 100 --workers 8
    python scripts/exp_sqlite_disprover.py --workers 16 --inmemory
"""

import json
import os
import argparse
import datetime
import sqlite3
import shutil
import tempfile
import time
from concurrent.futures import as_completed, TimeoutError as FuturesTimeoutError
from pebble import ProcessPool, ProcessExpired
import sys
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


def load_preds(preds_fp: str):
    lines = []
    with open(preds_fp) as f:
        for line in f:
            if line.strip():
                lines.append(line.strip())
    return lines


def _process_disprove_case(index, gold_sql, pred_sql, db_id, ddls, connection_string, timeout, memory_uri=None):
    t0 = time.time()
    record = {
        "index": index,
        "db_id": db_id,
        "gold_sql": gold_sql,
        "pred_sql": pred_sql,
        "verdict": "unknown",
        "error_msg": "",
        "elapsed_time": 0.0,
    }
    keeper_conn = None
    try:
        from parseval.main import disprove

        # Keep a shared in-memory SQLite DB alive across disprove's internal
        # connection open/close cycles (to_db -> execute_query).
        if memory_uri:
            keeper_conn = sqlite3.connect(memory_uri, uri=True)

        result = disprove(
            gold_sql, pred_sql, ddls, connection_string, "sqlite",
            semantics="bag",
            max_iterations=15,
            timeout=timeout,
        )
        record["verdict"] = result.verdict.value
        record["error_msg"] = result.error_msg or ""
    except Exception as exc:
        record["verdict"] = "syntax_error"
        record["error_msg"] = str(exc)[:200]
    finally:
        if keeper_conn is not None:
            keeper_conn.close()
        record["elapsed_time"] = round(time.time() - t0, 4)
    return record


def _process_disprove_task(task):
    index, gold_sql, pred_sql, db_id, ddls, connection_string, timeout, memory_uri = task
    return _process_disprove_case(
        index, gold_sql, pred_sql, db_id, ddls, connection_string, timeout, memory_uri,
    )


def run_disprove_experiment(
    schema_fp: str,
    gold_fp: str,
    preds_fp: str,
    output_dir: str = "results",
    limit: int | None = None,
    start: int = 0,
    workers: int = 1,
    timeout: int = 60,
    inmemory: bool = False,
):
    schemas = load_schema(schema_fp)
    gold = load_gold(gold_fp)
    preds = load_preds(preds_fp)

    selected = gold[start:]
    if limit is not None:
        selected = selected[:limit]

    os.makedirs(output_dir, exist_ok=True)
    tmp_dir = None if inmemory else tempfile.mkdtemp(prefix="parseval_")

    tasks = []
    for offset, row in enumerate(selected):
        index = start + offset
        gold_sql = row.get("SQL") or ""
        pred_sql = preds[index] if index < len(preds) else ""
        db_id = row.get("db_id")
        ddls = ";".join(schemas.get(db_id, []))
        if inmemory:
            memory_name = f"parseval_{db_id}_{index}"
            memory_uri = f"file:{memory_name}?mode=memory&cache=shared"
            connection_string = f"sqlite:///{memory_uri}&uri=true"
        else:
            db_path = os.path.abspath(os.path.join(tmp_dir, f"{db_id}_{index}.db"))
            connection_string = f"sqlite:///{db_path}"
            memory_uri = None
        tasks.append((index, gold_sql, pred_sql, db_id, ddls, connection_string, timeout, memory_uri))

    if workers <= 1:
        records = [
            _process_disprove_task(task)
            for task in tqdm(tasks, desc="Disproving BIRD pairs", disable=not sys.stdout.isatty())
        ]
    else:
        records_by_index = {}
        with ProcessPool(max_workers=workers) as pool:
            future_to_task = {
                pool.schedule(_process_disprove_task, args=(task,), timeout=timeout): task
                for task in tasks
            }
            for future in tqdm(
                as_completed(future_to_task),
                total=len(future_to_task),
                desc="Disproving BIRD pairs",
                disable=not sys.stdout.isatty()
            ):
                task = future_to_task[future]
                index = task[0]
                gold_sql = task[1]
                pred_sql = task[2]
                try:
                    record = future.result()
                    records_by_index[record["index"]] = record
                except FuturesTimeoutError:
                    records_by_index[index] = {
                        "index": index,
                        "db_id": task[3],
                        "gold_sql": gold_sql,
                        "pred_sql": pred_sql,
                        "verdict": "timeout",
                        "error_msg": f"Task exceeded {timeout} seconds",
                        "elapsed_time": float(timeout),
                    }
                except ProcessExpired as e:
                    records_by_index[index] = {
                        "index": index,
                        "db_id": task[3],
                        "gold_sql": gold_sql,
                        "pred_sql": pred_sql,
                        "verdict": "execution_error",
                        "error_msg": f"Worker process crashed: {e}",
                        "elapsed_time": 0.0,
                    }
                except Exception as e:
                    records_by_index[index] = {
                        "index": index,
                        "db_id": task[3],
                        "gold_sql": gold_sql,
                        "pred_sql": pred_sql,
                        "verdict": "execution_error",
                        "error_msg": f"Python Exception: {str(e)[:200]}",
                        "elapsed_time": 0.0,
                    }
        records = [records_by_index[index] for index, *_rest in tasks]

    if tmp_dir is not None:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    metrics = compute_metrics(records)

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_fp = os.path.join(output_dir, f"sqlite_results_{ts}.json")
    metrics_fp = os.path.join(output_dir, f"sqlite_metrics_{ts}.json")

    with open(results_fp, "w") as f:
        json.dump(records, f, indent=2)
    print(f"Wrote {len(records)} results to {results_fp}")

    with open(metrics_fp, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"Wrote metrics to {metrics_fp}")

    print_summary(metrics)
    return metrics


def compute_metrics(records):
    total = len(records)
    verdict_counts = {}
    elapsed_times = []
    for record in records:
        v = record.get("verdict", "unknown")
        verdict_counts[v] = verdict_counts.get(v, 0) + 1
        elapsed_times.append(record.get("elapsed_time", 0.0))

    elapsed_times.sort()
    time_stats = {}
    if elapsed_times:
        time_stats = {
            "min": round(min(elapsed_times), 3),
            "max": round(max(elapsed_times), 3),
            "mean": round(sum(elapsed_times) / len(elapsed_times), 3),
            "median": round(elapsed_times[len(elapsed_times) // 2], 3),
            "p90": round(elapsed_times[int(len(elapsed_times) * 0.9)], 3),
            "p95": round(elapsed_times[int(len(elapsed_times) * 0.95)], 3),
            "p99": round(elapsed_times[int(len(elapsed_times) * 0.99)], 3),
            "total": round(sum(elapsed_times), 3),
        }

    return {
        "total_pairs": total,
        "verdict_counts": verdict_counts,
        "verdict_ratio": {k: round(v / total, 4) for k, v in verdict_counts.items()},
        "elapsed_time": time_stats,
    }


def print_summary(metrics):
    print("\n=== Experiment Summary ===")
    print(f"Total pairs: {metrics['total_pairs']}")
    for k, cnt in sorted(metrics["verdict_counts"].items()):
        ratio = metrics["verdict_ratio"][k]
        print(f"  {k}: {cnt} ({ratio:.1%})")

    time_stats = metrics.get("elapsed_time", {})
    if time_stats:
        print("\n=== Elapsed Time Distribution ===")
        print(f"  Min: {time_stats['min']:.3f}s")
        print(f"  Max: {time_stats['max']:.3f}s")
        print(f"  Mean: {time_stats['mean']:.3f}s")
        print(f"  Median: {time_stats['median']:.3f}s")
        print(f"  P90: {time_stats['p90']:.3f}s")
        print(f"  P95: {time_stats['p95']:.3f}s")
        print(f"  P99: {time_stats['p99']:.3f}s")
        print(f"  Total: {time_stats['total']:.3f}s")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SQLite disprove experiment")
    parser.add_argument("--schema_fp", default="data/sqlite/schema.json")
    parser.add_argument("--gold_fp", default="data/sqlite/dev.json")
    parser.add_argument("--preds_fp", default="data/sqlite/dail.txt")
    parser.add_argument("--output_dir", default="results")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=360, help="Query execution timeout per task in seconds")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--inmemory", action="store_true", help="Use SQLite in-memory database per task")
    args = parser.parse_args()
    run_disprove_experiment(
        schema_fp=args.schema_fp,
        gold_fp=args.gold_fp,
        preds_fp=args.preds_fp,
        output_dir=args.output_dir,
        limit=args.limit,
        start=args.start,
        workers=args.workers,
        timeout=args.timeout,
        inmemory=args.inmemory,
    )
