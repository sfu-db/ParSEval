
import argparse
import datetime
import json
import os
import sys
import time
from concurrent.futures import TimeoutError, as_completed
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import sqlglot
from sqlglot import exp

# Import pebble for robust process management
from pebble import ProcessPool, ProcessExpired

try:
    from tqdm import tqdm
    HAS_TQDM = True
except Exception:
    HAS_TQDM = False
    def tqdm(x, **kwargs):
        return x

# docker run --name mysql-db   -e MYSQL_ROOT_PASSWORD=rootpass   -e MYSQL_DATABASE=mydb   -p 3306:3306   --mount type=tmpfs,destination=/var/lib/mysql,tmpfs-size=4G   -d mysql:8.0
DEFAULT_MYSQL_CONNECTION = "mysql+pymysql://root:rootpass@localhost:3306/mydb"
DEFAULT_MYSQL_DATA_FP = Path(__file__).resolve().parents[1] / "data/mysql/leetcode-new.jsonlines"


def _progress_enabled() -> bool:
    # Allow forcing progress bars in non-interactive environments.
    if os.environ.get("PARSEVAL_FORCE_TQDM") == "1":
        return True
    return sys.stdout.isatty() or sys.stderr.isatty()


def load_jsonlines(fp: str) -> list[dict]:
    rows = []
    with open(fp) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _is_select_query(sql: str) -> bool:
    s = sql.strip().upper()
    return s.startswith("SELECT") or s.startswith("WITH")


def _make_task_connection_string(base_connection_string: str, index: int) -> str:
    parsed = urlparse(base_connection_string)
    db_name = f"parseval_n_{index}"
    new_parsed = parsed._replace(path=f"/{db_name}")
    return urlunparse(new_parsed)

def _canonical_identifier(identifier: str) -> str:
    return identifier.lower()


def _prepare_mysql_query(sql: str, schema: dict) -> str:
    sql = _quote_mysql_enum_literals(sql, schema)
    sql = _quote_mysql_reserved_rank_alias(sql)
    sql = _canonicalize_unquoted_identifiers(sql)
    return sql


def _canonicalize_unquoted_identifiers(sql: str) -> str:
    try:
        tree = sqlglot.parse_one(sql, read="mysql")
    except Exception:
        return sql

    def lower_identifier(node):
        if isinstance(node, exp.Identifier) and not node.args.get("quoted"):
            node.set("this", _canonical_identifier(node.name))
        return node

    return tree.transform(lower_identifier).sql(dialect="mysql")


def _quote_mysql_enum_literals(sql: str, schema: dict) -> str:
    import re

    def _enum_values_from_type(dtype: str) -> set[str]:
        dtype = dtype.strip()
        upper_dtype = dtype.upper()
        if not upper_dtype.startswith("ENUM(") or not dtype.endswith(")"):
            return set()
        inner = dtype[dtype.find("(") + 1 : -1]
        values = set()
        for match in re.finditer(r"'((?:''|[^'])*)'", inner):
            values.add(match.group(1).replace("''", "'"))
        return values

    enum_values = set()
    if isinstance(schema, dict):
        for columns in schema.values():
            for dtype in columns.values():
                enum_values.update(_enum_values_from_type(dtype))
    elif isinstance(schema, str):
        for match in re.finditer(r"ENUM\s*\((.*?)\)", schema, flags=re.IGNORECASE | re.DOTALL):
            enum_values.update(_enum_values_from_type(f"ENUM({match.group(1)})"))

    enum_values = sorted(enum_values, key=len, reverse=True)
    operator_tokens = {"<", ">", "=", "<=", ">=", "<>", "!=", "LIKE"}
    for value in enum_values:
        if value.upper() in operator_tokens:
            continue
        escaped = value.replace("'", "''")
        sql = re.sub(
            rf"(?<!['\"`.\w]){re.escape(value)}(?!['\"`.\w])",
            f"'{escaped}'",
            sql,
            flags=re.IGNORECASE,
        )
    return sql


def _quote_mysql_reserved_rank_alias(sql: str) -> str:
    import re
    if not re.search(r"\bAS\s+RANK\b", sql, flags=re.IGNORECASE):
        return sql
    sql = re.sub(r"\bAS\s+RANK\b", "AS `RANK`", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"(?<![`.\w])RANK(?!\s*\(|[`.\w])",
        "`RANK`",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"(\b[A-Za-z_][A-Za-z0-9_]*\.)RANK\b",
        r"\1`RANK`",
        sql,
        flags=re.IGNORECASE,
    )
    return sql

def _cleanup_database(connection_string: str) -> None:
    from sqlalchemy import create_engine, text

    parsed = urlparse(connection_string)
    db_name = parsed.path.lstrip("/")
    if not db_name:
        return

    admin_url = (
        f"{parsed.scheme}://"
        f"{parsed.username}:{parsed.password}"
        f"@{parsed.hostname}:{parsed.port}/"
    )

    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
    finally:
        engine.dispose()


def _process_disprove_case(index, sql1, sql2, ddls, connection_string, timeout):
    t0 = time.time()
    record = {
        "index": index,
        "sql1": sql1,
        "sql2": sql2,
        "verdict": "unknown",
        "error_msg": "",
        "elapsed_time": 0.0,
        "generation_status": "",
        "generation_coverage": 0.0,
        "generation_stage_timings": {},
        "generation_budgets_consumed": {},
        "generation_unresolved_reasons": [],
    }
    try:
        from parseval.db_manager import DBManager
        from parseval.main import disprove

        with DBManager().get_connection(connection_string, "mysql"):
            pass

        result = disprove(
            sql1, sql2, ddls, connection_string, "mysql",
            semantics="bag",
            timeout=timeout,
        )
        record["verdict"] = result.verdict.value
        record["error_msg"] = result.error_msg or ""
        record["generation_status"] = result.generation.status
        record["generation_coverage"] = result.generation.coverage
        record["generation_stage_timings"] = result.generation.stage_timings
        record["generation_budgets_consumed"] = result.generation.budgets_consumed
        record["generation_unresolved_reasons"] = list(
            result.generation.unresolved_reasons
        )
    except Exception as exc:
        record["verdict"] = "execution_error"
        record["error_msg"] = str(exc)[:500]
    finally:
        record["elapsed_time"] = round(time.time() - t0, 4)
        _cleanup_database(connection_string)
    return record


def _process_disprove_task(task):
    index, sql1, sql2, ddls, connection_string, timeout = task
    return _process_disprove_case(index, sql1, sql2, ddls, connection_string, timeout)


def run_disprove_experiment(
    data_fp: str,
    connection_string: str = DEFAULT_MYSQL_CONNECTION,
    output_dir: str = "results",
    limit: int | None = None,
    start: int = 0,
    workers: int = 1,
    timeout: int = 60,
):
    entries = load_jsonlines(data_fp)
    selected = entries[start:]

    if limit is not None:
        selected = selected[:limit]

    os.makedirs(output_dir, exist_ok=True)

    if not HAS_TQDM:
        print(
            "tqdm is not installed; progress bars are disabled. "
            "Install with: pip install tqdm",
            flush=True,
        )

    progress_enabled = _progress_enabled()
    print(
        f"Preparing tasks from {len(selected)} entries (start={start}, limit={limit})",
        flush=True,
    )

    tasks = []
    skipped = 0

    for offset, entry in enumerate(
        tqdm(
            selected,
            desc="Preparing MySQL tasks",
            disable=not progress_enabled,
        )
    ):
        index = start + offset
        sql1, sql2 = (
            _prepare_mysql_query(sql, entry["schema"])
            for sql in entry["pair"]
        )

        if not (_is_select_query(sql1) and _is_select_query(sql2)):
            skipped += 1
            continue
        ddls = entry["schema"]
        task_conn = _make_task_connection_string(connection_string, index)

        tasks.append((index, sql1, sql2, ddls, task_conn, timeout))

    if skipped:
        print(f"Skipped {skipped} entries with non-SELECT statements", flush=True)

    print(
        f"Running {len(tasks)} MySQL disprove tasks "
        f"with workers={workers}, timeout={timeout}",
        flush=True,
    )

    records = []

    if workers <= 1:
        records = [
            _process_disprove_task(task)
            for task in tqdm(
                tasks,
                desc="Disproving LeetCode pairs",
                disable=not progress_enabled,
            )
        ]
    else:
        records_by_index = {}
        with ProcessPool(max_workers=workers) as pool:
            # Map futures to tasks so we can recover the payload if a worker crashes
            future_to_task = {
                pool.schedule(_process_disprove_task, args=(task,), timeout=timeout): task
                for task in tasks
            }

            for future in tqdm(
                as_completed(future_to_task),
                total=len(future_to_task),
                desc="Disproving LeetCode pairs",
                disable=not progress_enabled,
            ):
                task = future_to_task[future]
                index = task[0]
                sql1 = task[1]
                sql2 = task[2]

                try:
                    record = future.result()
                    records_by_index[record["index"]] = record
                except TimeoutError:
                    records_by_index[index] = {
                        "index": index,
                        "sql1": sql1,
                        "sql2": sql2,
                        "verdict": "timeout",
                        "error_msg": f"Task exceeded {timeout} seconds",
                        "elapsed_time": timeout,
                    }
                except ProcessExpired as e:
                    records_by_index[index] = {
                        "index": index,
                        "sql1": sql1,
                        "sql2": sql2,
                        "verdict": "execution_error",
                        "error_msg": f"Worker process crashed (Rust panic): {e}",
                        "elapsed_time": 0.0,
                    }
                except Exception as e:
                    records_by_index[index] = {
                        "index": index,
                        "sql1": sql1,
                        "sql2": sql2,
                        "verdict": "execution_error",
                        "error_msg": f"Python Exception: {str(e)[:500]}",
                        "elapsed_time": 0.0,
                    }

        records = [records_by_index[index] for index, *_rest in tasks]

    metrics = compute_metrics(records)

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_fp = os.path.join(output_dir, f"mysql_results_{ts}.json")
    metrics_fp = os.path.join(output_dir, f"mysql_metrics_{ts}.json")

    with open(results_fp, "w") as f:
        json.dump(records, f, indent=2)
    print(f"Wrote {len(records)} results to {results_fp}", flush=True)

    with open(metrics_fp, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"Wrote metrics to {metrics_fp}", flush=True)

    print_summary(metrics)
    return metrics


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    idx = int(len(sorted_values) * q)
    idx = min(idx, len(sorted_values) - 1)
    return sorted_values[idx]


def compute_metrics(records):
    total = len(records)
    verdict_counts = {}
    elapsed_times = []

    for record in records:
        verdict = record.get("verdict", "unknown")
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
        elapsed_times.append(record.get("elapsed_time", 0.0))

    elapsed_times.sort()

    time_stats = {}
    if elapsed_times:
        time_stats = {
            "min": round(min(elapsed_times), 3),
            "max": round(max(elapsed_times), 3),
            "mean": round(sum(elapsed_times) / len(elapsed_times), 3),
            "median": round(_percentile(elapsed_times, 0.50), 3),
            "p90": round(_percentile(elapsed_times, 0.90), 3),
            "p95": round(_percentile(elapsed_times, 0.95), 3),
            "p99": round(_percentile(elapsed_times, 0.99), 3),
            "total": round(sum(elapsed_times), 3),
        }

    verdict_ratio = {}
    if total > 0:
        verdict_ratio = {
            k: round(v / total, 4)
            for k, v in verdict_counts.items()
        }

    return {
        "total_pairs": total,
        "verdict_counts": verdict_counts,
        "verdict_ratio": verdict_ratio,
        "elapsed_time": time_stats,
    }


def print_summary(metrics):
    print("\n=== Experiment Summary ===", flush=True)
    print(f"Total pairs: {metrics['total_pairs']}", flush=True)

    for verdict, count in sorted(metrics["verdict_counts"].items()):
        ratio = metrics["verdict_ratio"].get(verdict, 0.0)
        print(f"  {verdict}: {count} ({ratio:.1%})", flush=True)

    time_stats = metrics.get("elapsed_time", {})
    if time_stats:
        print("\n=== Elapsed Time Distribution ===", flush=True)
        print(f"  Min: {time_stats['min']:.3f}s", flush=True)
        print(f"  Max: {time_stats['max']:.3f}s", flush=True)
        print(f"  Mean: {time_stats['mean']:.3f}s", flush=True)
        print(f"  Median: {time_stats['median']:.3f}s", flush=True)
        print(f"  P90: {time_stats['p90']:.3f}s", flush=True)
        print(f"  P95: {time_stats['p95']:.3f}s", flush=True)
        print(f"  P99: {time_stats['p99']:.3f}s", flush=True)
        print(f"  Total: {time_stats['total']:.3f}s", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run MySQL disprove experiment")
    parser.add_argument("--data_fp", default=str(DEFAULT_MYSQL_DATA_FP))
    parser.add_argument("--connection_string", default=DEFAULT_MYSQL_CONNECTION)
    parser.add_argument("--output_dir", default="results")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=60, help="Query execution timeout per task in seconds")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)

    args = parser.parse_args()

    run_disprove_experiment(
        data_fp=args.data_fp,
        connection_string=args.connection_string,
        output_dir=args.output_dir,
        limit=args.limit,
        start=args.start,
        workers=args.workers,
        timeout=args.timeout,
    )
