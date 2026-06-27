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


def _write_and_execute_speculate(instance, sql, db_path):
    """Write instance rows via to_db and execute the query via DBManager."""
    from parseval.instance.io import to_db
    from parseval.db_manager import DBManager

    connection_string = f"sqlite:///{db_path}"
    to_db(instance, connection_string, dialect="sqlite")
    with DBManager().get_connection(connection_string, "sqlite") as conn:
        rows = conn.execute(sql, fetch="all", timeout=30)
        return rows or []


def _write_and_execute_speculate_branch(ddls, rows_per_table, sql, db_path):
    """Execute one speculate branch in a fresh instance."""
    from parseval.instance import Instance
    from parseval.symbolic.speculate import _materialize_rows

    instance = Instance(ddls=ddls, name="speculate_branch", dialect="sqlite")
    _materialize_rows(instance, rows_per_table)
    return _write_and_execute_speculate(instance, sql, db_path)


def _cleanup_db(db_path):
    for suffix in ("", "-wal", "-shm"):
        path = f"{db_path}{suffix}"
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# Speculate mode
# ---------------------------------------------------------------------------

def _run_speculate_case(index, row, schemas):
    """Run speculate() on a single BIRD query and return a result record."""
    from parseval.instance import Instance
    from parseval.plan import Plan
    from parseval.query import preprocess_sql
    from parseval.symbolic.speculate import speculate, SpeculateConfig

    db_id = row.get("db_id")
    sql = row.get("SQL") or ""
    raw_ddls = schemas.get(db_id, [])
    ddls = raw_ddls if isinstance(raw_ddls, str) else ";".join(raw_ddls)

    record = {
        "index": index,
        "db_id": db_id,
        "difficulty": row.get("difficulty"),
        "sql": sql[:200],
        "status": "unknown",
        "branches": 0,
        "rows_generated": 0,
        "non_empty": False,
        "non_empty_branch": "",
        "error_msg": "",
        "elapsed_propagate": 0.0,
        "elapsed_total": 0.0,
    }

    t0 = time.time()
    try:
        instance = Instance(ddls=ddls, name=f"spec_{db_id}_{index}", dialect="sqlite")
        expr = preprocess_sql(sql, instance, dialect="sqlite")
        plan = Plan(expr, instance)

        t_prop = time.time()
        config = SpeculateConfig.gold_non_empty()
        results = speculate(plan, instance, dialect="sqlite", config=config)
        record["elapsed_propagate"] = round(time.time() - t_prop, 4)

        record["branches"] = len(results)
        total_rows = 0
        for _branch_name, rows_per_table in results:
            for _table, table_rows in rows_per_table.items():
                total_rows += len(table_rows)
        record["rows_generated"] = total_rows

        if total_rows > 0:
            db_dir = os.path.abspath("tmp")
            os.makedirs(db_dir, exist_ok=True)
            for branch_offset, (branch_name, rows_per_table) in enumerate(results):
                db_path = os.path.join(
                    db_dir,
                    f"speculate_{db_id}_{index}_{branch_offset}_{branch_name}.db",
                )
                _cleanup_db(db_path)
                try:
                    query_results = _write_and_execute_speculate_branch(
                        ddls, rows_per_table, sql, db_path,
                    )
                    if query_results:
                        record["non_empty"] = True
                        record["non_empty_branch"] = branch_name
                        break
                finally:
                    _cleanup_db(db_path)
            record["status"] = "non_empty" if record["non_empty"] else "empty_result"
        else:
            record["status"] = "empty_generation"

    except Exception as exc:
        record["status"] = "error"
        record["error_msg"] = str(exc)[:300]
    finally:
        record["elapsed_total"] = round(time.time() - t0, 4)

    return record


def run_speculate_experiment(schema_fp, gold_fp, *, limit=None, start=0, workers=1):
    """Run speculate() on BIRD queries and return metrics."""
    schemas = load_schema(schema_fp)
    gold = load_gold(gold_fp)
    selected = gold[start:]
    if limit is not None:
        selected = selected[:limit]

    tasks = [(start + offset, row, schemas) for offset, row in enumerate(selected)]
    if workers <= 1:
        records = [
            _run_speculate_case(*task)
            for task in tqdm(tasks, desc="Testing speculate()")
        ]
    else:
        records_by_index = {}
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_run_speculate_case, *t) for t in tasks]
            for future in tqdm(as_completed(futures), total=len(futures), desc="Testing speculate()"):
                record = future.result()
                records_by_index[record["index"]] = record
        records = [records_by_index[index] for index, *_rest in tasks]

    total = len(records)
    non_empty = errors = empty_gen = 0
    total_rows = total_branches = 0
    sum_propagate = sum_total = 0.0
    for r in records:
        if r["non_empty"]:
            non_empty += 1
        status = r["status"]
        if status == "error":
            errors += 1
        elif status == "empty_generation":
            empty_gen += 1
        total_rows += r["rows_generated"]
        total_branches += r["branches"]
        sum_propagate += r["elapsed_propagate"]
        sum_total += r["elapsed_total"]

    return {
        "total_queries": total,
        "non_empty": non_empty,
        "non_empty_ratio": round(non_empty / total, 4) if total else 0,
        "errors": errors,
        "empty_generation": empty_gen,
        "total_rows_generated": total_rows,
        "total_branches": total_branches,
        "avg_branches": round(total_branches / total, 2) if total else 0,
        "avg_rows_per_query": round(total_rows / total, 2) if total else 0,
        "avg_propagate_time": round(sum_propagate / total, 4) if total else 0,
        "avg_total_time": round(sum_total / total, 4) if total else 0,
        "records": records,
    }


# ---------------------------------------------------------------------------
# Engine / Combined mode (shared implementation)
# ---------------------------------------------------------------------------

def _run_engine_case(
    index,
    row,
    schemas,
    *,
    execute_sqlite=True,
    max_iterations=50,
    speculate_first=True,
):
    """Run SymbolicEngine on a single BIRD query and return a result record."""
    from parseval.instance import Instance
    from parseval.symbolic import CoverageThresholds, SymbolicEngine

    db_id = row.get("db_id")
    sql = row.get("SQL") or ""
    raw_ddls = schemas.get(db_id, [])
    ddls = raw_ddls if isinstance(raw_ddls, str) else ";".join(raw_ddls)

    record = {
        "index": index,
        "db_id": db_id,
        "difficulty": row.get("difficulty"),
        "sql": sql[:200],
        "status": "unknown",
        "rows_generated": 0,
        "coverage": 0.0,
        "iterations": 0,
        "non_empty": False,
        "error_msg": "",
        "elapsed_generate": 0.0,
        "elapsed_total": 0.0,
    }

    t0 = time.time()
    try:
        instance = Instance(ddls=ddls, name=f"engine_{db_id}_{index}", dialect="sqlite")
        t_generate = time.time()
        engine = SymbolicEngine(
            instance, sql, dialect="sqlite", max_iterations=max_iterations,
        )
        result = engine.generate(
            thresholds=CoverageThresholds(atom_null=0),
            speculate_first=speculate_first,
        )
        record["elapsed_generate"] = round(time.time() - t_generate, 4)
        record["rows_generated"] = result.rows_generated
        record["coverage"] = result.coverage
        record["iterations"] = result.iterations

        if record["rows_generated"] <= 0:
            record["status"] = "empty_generation"
        elif not execute_sqlite:
            record["status"] = "generated"
        else:
            db_path = os.path.abspath(f"tmp/engine_{db_id}_{index}.db")
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            _cleanup_db(db_path)
            try:
                query_results = _write_and_execute_speculate(instance, sql, db_path)
                record["non_empty"] = bool(query_results)
            finally:
                _cleanup_db(db_path)
            record["status"] = "non_empty" if record["non_empty"] else "empty_result"
    except Exception as exc:
        record["status"] = "error"
        record["error_msg"] = str(exc)[:300]
    finally:
        record["elapsed_total"] = round(time.time() - t0, 4)
    return record


def _run_engine_task(task):
    index, row, schemas, execute_sqlite, max_iterations, speculate_first = task
    return _run_engine_case(
        index, row, schemas,
        execute_sqlite=execute_sqlite,
        max_iterations=max_iterations,
        speculate_first=speculate_first,
    )


def summarize_engine_results(records):
    total = len(records)
    status_counts = {}
    non_empty = 0
    total_rows = 0
    sum_coverage = sum_iterations = 0.0
    sum_generate = sum_total = 0.0
    for r in records:
        status_counts[r.get("status", "unknown")] = status_counts.get(r.get("status", "unknown"), 0) + 1
        if r.get("non_empty"):
            non_empty += 1
        total_rows += int(r.get("rows_generated") or 0)
        sum_coverage += r.get("coverage") or 0.0
        sum_iterations += r.get("iterations") or 0
        sum_generate += r.get("elapsed_generate") or 0.0
        sum_total += r.get("elapsed_total") or 0.0

    return {
        "total_queries": total,
        "non_empty": non_empty,
        "non_empty_ratio": round(non_empty / total, 4) if total else 0,
        "errors": status_counts.get("error", 0),
        "empty_generation": status_counts.get("empty_generation", 0),
        "total_rows_generated": total_rows,
        "avg_rows_per_query": round(total_rows / total, 2) if total else 0,
        "avg_coverage": round(sum_coverage / total, 4) if total else 0,
        "avg_iterations": round(sum_iterations / total, 2) if total else 0,
        "avg_generate_time": round(sum_generate / total, 4) if total else 0,
        "avg_total_time": round(sum_total / total, 4) if total else 0,
        "status_counts": status_counts,
        "records": records,
    }


def run_engine_experiment(
    schema_fp,
    gold_fp,
    *,
    limit=None,
    start=0,
    workers=1,
    execute_sqlite=True,
    max_iterations=50,
    speculate_first=True,
):
    """Run SymbolicEngine on BIRD queries and return metrics."""
    schemas = load_schema(schema_fp)
    gold = load_gold(gold_fp)
    selected = gold[start:]
    if limit is not None:
        selected = selected[:limit]

    tasks = [
        (start + offset, row, schemas, execute_sqlite, max_iterations, speculate_first)
        for offset, row in enumerate(selected)
    ]
    if workers <= 1:
        records = [
            _run_engine_task(task)
            for task in tqdm(tasks, desc="Testing SymbolicEngine")
        ]
    else:
        records_by_index = {}
        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(_run_engine_task, t) for t in tasks]
            for future in tqdm(as_completed(futures), total=len(futures), desc="Testing SymbolicEngine"):
                record = future.result()
                records_by_index[record["index"]] = record
        records = [records_by_index[index] for index, *_rest in tasks]

    return summarize_engine_results(records)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _save_and_print(metrics, output_dir, prefix):
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_fp = os.path.join(output_dir, f"{prefix}_{ts}.json")
    with open(out_fp, "w") as f:
        json.dump(metrics, f, indent=2)

    printable = {k: v for k, v in metrics.items() if k != "records"}
    print(json.dumps(printable, indent=2))

    failures = [r for r in metrics["records"] if r["status"] != "non_empty"]
    if failures:
        print(f"\nNon-successes ({len(failures)}):")
        for r in failures[:20]:
            print(f"  [{r['index']}] {r['db_id']} [{r['status']}] rows={r['rows_generated']} err={r['error_msg'][:80]}")

    print(f"\nWrote {len(metrics['records'])} records to {out_fp}")
    return metrics


def dispatch_experiment(args):
    os.makedirs(args.output_dir, exist_ok=True)

    if args.mode == "speculate":
        metrics = run_speculate_experiment(
            schema_fp=args.schema_fp,
            gold_fp=args.gold_fp,
            limit=args.limit,
            start=args.start,
            workers=args.workers,
        )
        return _save_and_print(metrics, args.output_dir, "speculate_bird")

    # engine and combined share the same implementation;
    # combined enables speculate_first (the default).
    speculate_first = args.mode == "combined"
    metrics = run_engine_experiment(
        schema_fp=args.schema_fp,
        gold_fp=args.gold_fp,
        limit=args.limit,
        start=args.start,
        workers=args.workers,
        execute_sqlite=not args.no_execute_sqlite,
        max_iterations=args.max_iterations,
        speculate_first=speculate_first,
    )
    prefix = "combined_bird" if speculate_first else "symbolic_engine_bird"
    return _save_and_print(metrics, args.output_dir, prefix)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run ParSEval BIRD dataset experiment"
    )
    parser.add_argument(
        "--mode",
        choices=["engine", "speculate", "combined"],
        default="combined",
        help="Generation mode: 'combined' (speculate+engine), 'engine' (SymbolicEngine), or 'speculate' (Propagator+Resolver)",
    )
    parser.add_argument("--schema_fp", default="data/sqlite/schema.json")
    parser.add_argument("--gold_fp", default="data/sqlite/dev.json")
    parser.add_argument("--output_dir", default="results")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--no_execute_sqlite", action="store_true")
    parser.add_argument("--max_iterations", type=int, default=50)
    args = parser.parse_args()

    dispatch_experiment(args)
