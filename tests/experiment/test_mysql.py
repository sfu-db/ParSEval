"""MySQL experiment runner — disprove equivalence on LeetCode dataset pairs.

Usage::

    python tests/experiment/test_mysql.py --workers 16
    python tests/experiment/test_mysql.py --limit 100 --workers 8
    python tests/experiment/test_mysql.py --workers 16 --case_timeout 30
"""

import argparse
import datetime
import json
import os
import signal
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from urllib.parse import urlparse, urlunparse

try:
    from tqdm import tqdm
except Exception:
    def tqdm(x, **kwargs):
        return x


DEFAULT_MYSQL_CONNECTION = "mysql+pymysql://root:rootpass@localhost:3306/mydb"


class CaseTimeoutError(TimeoutError):
    """Raised when a single disprove case exceeds its timeout."""


def _timeout_handler(signum, frame):
    raise CaseTimeoutError("case timed out")


def test_normalize_dtype_converts_comma_enum_to_mysql_enum():
    assert _normalize_dtype("ENUM,DESKTOP,MOBILE") == "ENUM('DESKTOP','MOBILE')"


def test_build_ddl_emits_mysql_enum_columns_for_dataset_schemas():
    schema = {
        "SPENDING": {
            "USER_ID": "INT",
            "SPEND_DATE": "DATE",
            "PLATFORM": "ENUM,DESKTOP,MOBILE",
        },
        "ACTIVITY": {
            "PLAYER_ID": "INT",
            "EVENT_DATE": "DATE",
            "DEVICE_TYPE": "ENUM,WEB,APP",
        },
    }

    ddl = build_ddl(schema, constraints=[])

    assert "PLATFORM ENUM('DESKTOP','MOBILE')" in ddl
    assert "DEVICE_TYPE ENUM('WEB','APP')" in ddl


def _make_task_connection_string(base_connection_string: str, index: int) -> str:
    """Build a per-task connection string with a unique database name."""
    parsed = urlparse(base_connection_string)
    db_name = f"parseval_{index}"
    new_parsed = parsed._replace(path=f"/{db_name}")
    return urlunparse(new_parsed)


def load_jsonlines(fp: str) -> list[dict]:
    rows = []
    with open(fp) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _is_select_query(sql: str) -> bool:
    """Return True if sql is a top-level SELECT or WITH statement."""
    s = sql.strip().upper()
    return s.startswith("SELECT") or s.startswith("WITH")


def _parse_ref(column_ref: str) -> tuple[str, str]:
    """Split 'TABLE__COLUMN' into (table, column)."""
    table, col = column_ref.split("__", 1)
    return table, col


def _normalize_dtype(dtype: str) -> str:
    """Normalize dataset type strings into valid MySQL type strings."""
    d = dtype.strip().upper()

    if d.startswith("ENUM,"):
        values = [part.strip() for part in dtype.strip().split(",")[1:]]
        quoted_values = []
        for value in values:
            escaped = value.replace("'", "''")
            quoted_values.append(f"'{escaped}'")
        return f"ENUM({','.join(quoted_values)})"

    if d == "VARCHAR":
        return "VARCHAR(255)"

    if d == "CHAR":
        return "CHAR(255)"

    return dtype


def build_ddl(schema: dict, constraints: list[dict] | None) -> str:
    """Convert a schema dict and constraints into MySQL DDL.

    Args:
        schema: {"TABLE": {"COL": "TYPE", ...}, ...}
        constraints: list of primary/foreign key constraint dicts.

    Returns:
        Semicolon-separated CREATE TABLE statements.
    """
    pk_map: dict[str, list[str]] = {}
    fks: list[tuple[str, str, str, str]] = []

    for c in constraints or []:
        if "primary" in c:
            for entry in c["primary"]:
                tbl, col = _parse_ref(entry["value"])
                pk_map.setdefault(tbl, []).append(col)

        elif "foreign" in c:
            entries = c["foreign"]
            fk_tbl, fk_col = _parse_ref(entries[0]["value"])
            ref_tbl, ref_col = _parse_ref(entries[1]["value"])
            fks.append((fk_tbl, fk_col, ref_tbl, ref_col))

    stmts = []
    for table, columns in schema.items():
        col_defs = [
            f"{col} {_normalize_dtype(dtype)}"
            for col, dtype in columns.items()
        ]

        if table in pk_map:
            col_defs.append(f"PRIMARY KEY ({', '.join(pk_map[table])})")

        for fk_tbl, fk_col, ref_tbl, ref_col in fks:
            if fk_tbl == table:
                col_defs.append(
                    f"FOREIGN KEY ({fk_col}) REFERENCES {ref_tbl}({ref_col})"
                )

        stmts.append(f"CREATE TABLE {table} ({', '.join(col_defs)})")

    return "; ".join(stmts)


def _process_disprove_case(
    index: int,
    sql1: str,
    sql2: str,
    ddls: str,
    connection_string: str,
    table_names: list[str],
    case_timeout: int | None,
) -> dict:
    """Process a single disprove case.

    The timeout is enforced inside the worker process using SIGALRM.
    This works on Linux, including GitHub Actions ubuntu-latest runners.
    """
    t0 = time.time()
    record = {
        "index": index,
        "sql1": sql1,
        "sql2": sql2,
        "verdict": "unknown",
        "error_msg": "",
        "elapsed_time": 0.0,
    }

    old_handler = None

    try:
        if case_timeout and case_timeout > 0:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(case_timeout)

        from parseval.db_manager import DBManager
        from parseval.main import disprove

        # DBManager is expected to create/prepare the database if needed.
        with DBManager().get_connection(connection_string, "mysql"):
            pass

        result = disprove(
            sql1,
            sql2,
            ddls,
            connection_string,
            "mysql",
            semantics="bag",
            max_iterations=25,
            atom_null=1,
            atom_false=1,
            atom_dup=1,
            project_null=1,
            distinct_duplicate=1,
            distinct_unique=1,
        )

        record["verdict"] = result.verdict.value
        record["error_msg"] = result.error_msg or ""

    except CaseTimeoutError:
        record["verdict"] = "timeout"
        record["error_msg"] = f"Timed out after {case_timeout} seconds"

    except Exception as exc:
        record["verdict"] = "syntax_error"
        record["error_msg"] = str(exc)[:500]

    finally:
        if case_timeout and case_timeout > 0:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)

        record["elapsed_time"] = round(time.time() - t0, 4)

    return record


def _process_disprove_task(task: tuple) -> dict:
    """Wrapper for parallel execution."""
    (
        index,
        sql1,
        sql2,
        ddls,
        connection_string,
        table_names,
        case_timeout,
    ) = task

    return _process_disprove_case(
        index=index,
        sql1=sql1,
        sql2=sql2,
        ddls=ddls,
        connection_string=connection_string,
        table_names=table_names,
        case_timeout=case_timeout,
    )


def _cleanup_databases(base_connection_string: str, tasks: list[tuple]) -> None:
    """Drop temporary databases created for each task."""
    from sqlalchemy import create_engine, text

    parsed = urlparse(base_connection_string)

    admin_url = (
        f"{parsed.scheme}://"
        f"{parsed.username}:{parsed.password}"
        f"@{parsed.hostname}:{parsed.port}/"
    )

    engine = create_engine(admin_url)

    try:
        with engine.begin() as conn:
            for task in tasks:
                task_conn_str = task[4]
                db_name = urlparse(task_conn_str).path.lstrip("/")
                if db_name:
                    conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
    finally:
        engine.dispose()


def run_disprove_experiment(
    data_fp: str,
    connection_string: str = DEFAULT_MYSQL_CONNECTION,
    output_dir: str = "results",
    limit: int | None = None,
    start: int = 0,
    workers: int = 1,
    case_timeout: int | None = 60,
):
    """Run the disprove experiment on LeetCode MySQL dataset pairs."""
    entries = load_jsonlines(data_fp)
    selected = entries[start:]

    if limit is not None:
        selected = selected[:limit]

    os.makedirs(output_dir, exist_ok=True)

    tasks = []
    skipped = 0

    for offset, entry in enumerate(selected):
        index = start + offset
        sql1, sql2 = entry["pair"]

        if not (_is_select_query(sql1) and _is_select_query(sql2)):
            skipped += 1
            continue

        ddls = build_ddl(entry["schema"], entry.get("constraint") or [])
        table_names = list(entry["schema"].keys())
        task_conn = _make_task_connection_string(connection_string, index)

        tasks.append(
            (
                index,
                sql1,
                sql2,
                ddls,
                task_conn,
                table_names,
                case_timeout,
            )
        )

    if skipped:
        print(f"Skipped {skipped} entries with non-SELECT statements", flush=True)

    print(
        f"Running {len(tasks)} MySQL disprove tasks "
        f"with workers={workers}, case_timeout={case_timeout}",
        flush=True,
    )

    records = []

    try:
        if workers <= 1:
            records = [
                _process_disprove_task(task)
                for task in tqdm(
                    tasks,
                    desc="Disproving LeetCode pairs",
                    disable=not sys.stdout.isatty(),
                )
            ]
        else:
            records_by_index = {}

            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(_process_disprove_task, task)
                    for task in tasks
                ]

                for future in tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc="Disproving LeetCode pairs",
                    disable=not sys.stdout.isatty(),
                ):
                    record = future.result()
                    records_by_index[record["index"]] = record

            records = [records_by_index[index] for index, *_rest in tasks]

    finally:
        # Always try to clean up temporary per-task databases, even if the run fails.
        _cleanup_databases(connection_string, tasks)

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
    """Return nearest-rank style percentile from an already sorted list."""
    if not sorted_values:
        return 0.0

    idx = int(len(sorted_values) * q)
    idx = min(idx, len(sorted_values) - 1)
    return sorted_values[idx]


def compute_metrics(records: list[dict]) -> dict:
    """Compute summary metrics from experiment records."""
    total = len(records)
    verdict_counts: dict[str, int] = {}
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


def print_summary(metrics: dict) -> None:
    """Print experiment summary to stdout."""
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
    parser.add_argument("--data_fp", default="data/mysql/leetcode.jsonlines")
    parser.add_argument("--connection_string", default=DEFAULT_MYSQL_CONNECTION)
    parser.add_argument("--output_dir", default="results")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument(
        "--case_timeout",
        type=int,
        default=60,
        help="Timeout in seconds for each disprove case. Use <=0 to disable.",
    )

    args = parser.parse_args()

    run_disprove_experiment(
        data_fp=args.data_fp,
        connection_string=args.connection_string,
        output_dir=args.output_dir,
        limit=args.limit,
        start=args.start,
        workers=args.workers,
        case_timeout=args.case_timeout,
    )