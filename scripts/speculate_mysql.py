#!/usr/bin/env python3
"""Run speculative seeding against MySQL schemas with DB validation.

Loads query records from a JSONLines file (schema + SQL pairs), runs
``speculate()`` on each query, persists to a MySQL database, executes
the original query, and reports results.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
from concurrent.futures import TimeoutError, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlparse, urlunparse

import sqlglot
from sqlglot import exp

from parseval.db_manager import get_connection
from parseval.generator.bounds import BmcBounds
from parseval.generator.speculate import speculate
from parseval.instance import Instance

from pebble import ProcessPool, ProcessExpired

DEFAULT_MYSQL_CONNECTION = "mysql+pymysql://root:rootpass@localhost:3306/mydb"
DEFAULT_MYSQL_DATA_FP = Path(__file__).resolve().parents[1] / "data/mysql/leetcode-new.jsonlines"


@dataclass
class SpecRecord:
    index: int
    db_id: str
    sql: str
    seed_status: str          # "sat" | "unsat" | "error" | "timeout"
    seed_reason: str
    elapsed_seconds: float
    instance_row_total: int = field(default=0)
    validation_rows: int = field(default=0)
    column_count: int = field(default=0)
    column_types: list[str] = field(default_factory=list)
    shape_tags: tuple[str, ...] = field(default=())


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
    db_name = f"parseval_s_{index}"
    new_parsed = parsed._replace(path=f"/{db_name}")
    return urlunparse(new_parsed)


def _canonical_identifier(identifier: str) -> str:
    return identifier.lower()


def _prepare_mysql_query(sql: str, schema: str) -> str:
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


def _quote_mysql_enum_literals(sql: str, schema: str) -> str:
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


# ---------------------------------------------------------------------------
# Speculate experiment  (single-query seed + validate)
# ---------------------------------------------------------------------------


def process_speculate_case(
    index: int,
    sql: str,
    ddl: str,
    connection_string: str,
    timeout: int,
    dialect: str = "mysql",
    bounds: BmcBounds | None = None,
) -> SpecRecord:
    t0 = time.time()
    tags = sql_shape_tags(sql)
    inst: Instance | None = None
    nrows = 0

    try:
        inst = speculate(ddl, sql, dialect=dialect, bounds=bounds)
        nrows = sum(
            len(inst.get_rows(table_node))
            for table_node in inst.schema.fk_safe_table_order()
        )
    except Exception as exc:
        elapsed = time.time() - t0
        return SpecRecord(
            index=index, db_id="mysql",
            sql=sql, seed_status="error",
            seed_reason=f"speculate_error:{type(exc).__name__}:{exc}",
            elapsed_seconds=elapsed, instance_row_total=0,
            shape_tags=tags,
        )

    elapsed = time.time() - t0

    if inst is None:
        return SpecRecord(
            index=index, db_id="mysql",
            sql=sql, seed_status="error",
            seed_reason="returned_none",
            elapsed_seconds=elapsed, instance_row_total=0,
            shape_tags=tags,
        )

    if nrows == 0:
        return SpecRecord(
            index=index, db_id="mysql",
            sql=sql, seed_status="unsat",
            seed_reason="all_relaxations_exhausted",
            elapsed_seconds=elapsed, instance_row_total=0,
            shape_tags=tags,
        )

    # -- Persist to MySQL --
    try:
        from parseval.instance import to_db
        to_db(inst, connection_string, dialect=dialect, truncate_first=True)
    except Exception as exc:
        return SpecRecord(
            index=index, db_id="mysql",
            sql=sql, seed_status="sat",
            seed_reason=f"to_db_error:{type(exc).__name__}:{exc}",
            elapsed_seconds=elapsed, instance_row_total=nrows,
            shape_tags=tags,
        )

    # -- Execute original query --
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
    except Exception as exc:
        return SpecRecord(
            index=index, db_id="mysql",
            sql=sql, seed_status="sat",
            seed_reason=f"validation_error:{type(exc).__name__}:{exc}",
            elapsed_seconds=elapsed, instance_row_total=nrows,
            shape_tags=tags,
        )

    seed_status = "sat" if validation_rows > 0 else "empty_result"
    seed_reason = "" if validation_rows > 0 else "empty_query_result"

    return SpecRecord(
        index=index, db_id="mysql",
        sql=sql, seed_status=seed_status,
        seed_reason=seed_reason,
        elapsed_seconds=elapsed,
        instance_row_total=nrows,
        validation_rows=validation_rows,
        column_count=column_count,
        column_types=column_types,
        shape_tags=tags,
    )


def _print_spec_record(record: SpecRecord) -> None:
    seed_mark = (
        "OK" if record.seed_status == "sat"
        else "EM" if record.seed_status == "empty_result"
        else "TO" if record.seed_status == "timeout"
        else "UN"
    )
    print(
        f"[{seed_mark}] index={record.index} "
        f"elapsed={record.elapsed_seconds:.3f}s "
        f"seed_rows={record.instance_row_total} "
        f"db_rows={record.validation_rows} "
        f"cols={record.column_count} "
        f"reason={record.seed_reason}",
        file=sys.stderr,
        flush=True,
    )


def compute_speculate_metrics(records: list[SpecRecord]) -> dict[str, Any]:
    from collections import Counter

    seed_status_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()

    for record in records:
        seed_status_counts[record.seed_status] += 1
        for tag in record.shape_tags:
            tag_counts[tag] += 1

    sat_records = [r for r in records if r.seed_status == "sat"]
    return {
        "total": len(records),
        "sat_via_db": sum(1 for r in records if r.validation_rows > 0),
        "empty_result": sum(1 for r in records if r.seed_status == "empty_result"),
        "timeout": sum(1 for r in records if r.seed_status == "timeout"),
        "error": sum(1 for r in records if r.seed_status == "error"),
        "unsat": sum(1 for r in records if r.seed_status == "unsat"),
        "seed_status_counts": dict(seed_status_counts),
        "elapsed_total_seconds": sum(r.elapsed_seconds for r in records),
        "elapsed_avg_seconds": sum(r.elapsed_seconds for r in records) / len(records) if records else 0.0,
        "elapsed_sat_avg_seconds": sum(r.elapsed_seconds for r in sat_records) / len(sat_records) if sat_records else 0.0,
        "instance_rows_avg": sum(r.instance_row_total for r in sat_records) / len(sat_records) if sat_records else 0.0,
        "validation_rows_avg": sum(r.validation_rows for r in sat_records) / len(sat_records) if sat_records else 0.0,
        "column_count_avg": sum(r.column_count for r in sat_records) / len(sat_records) if sat_records else 0.0,
        "shape_tag_counts": dict(tag_counts.most_common()),
        "records": [asdict(record) for record in records],
    }


def _cleanup_databases(base_connection_string: str, tasks: list) -> None:
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
                task_conn_str = task[3]
                db_name = urlparse(task_conn_str).path.lstrip("/")
                if db_name:
                    conn.execute(text(f"DROP DATABASE IF EXISTS `{db_name}`"))
    finally:
        engine.dispose()


def run_speculate_experiment(
    data_fp: str,
    connection_string: str = DEFAULT_MYSQL_CONNECTION,
    output_dir: str = "results",
    limit: int | None = None,
    start: int = 0,
    workers: int = 1,
    timeout: int = 60,
    table_rows: int = 3,
    join_width: int = 1,
    groups: int = 2,
    rows_per_group: int = 3,
    subquery_rows: int = 3,
    order_competitors: int = 1,
    max_iterations: int = 0,
    max_table_rows: int = 512,
    use_first_of_pair: bool = True,
) -> list[SpecRecord]:
    entries = load_jsonlines(data_fp)
    selected = entries[start:]

    if limit is not None:
        selected = selected[:limit]

    os.makedirs(output_dir, exist_ok=True)

    print(
        f"Preparing {len(selected)} speculate tasks (start={start}, limit={limit})",
        flush=True,
    )

    bounds = BmcBounds(
        table_rows=table_rows,
        join_width=join_width,
        groups=groups,
        rows_per_group=rows_per_group,
        subquery_rows=subquery_rows,
        order_competitors=order_competitors,
        max_iterations=max_iterations,
        max_table_rows=max_table_rows,
    )

    tasks = []
    skipped = 0

    for offset, entry in enumerate(selected):
        index = start + offset
        ddl = entry["schema"]
        sql = entry["pair"][0] if use_first_of_pair else entry["pair"][1]
        prepared_sql = _prepare_mysql_query(sql, ddl)

        if not _is_select_query(prepared_sql):
            skipped += 1
            continue

        task_conn = _make_task_connection_string(connection_string, index)
        tasks.append((index, prepared_sql, ddl, task_conn, timeout, bounds))

    if skipped:
        print(f"Skipped {skipped} entries with non-SELECT statements", flush=True)

    print(
        f"Running {len(tasks)} speculate tasks with workers={workers}, timeout={timeout}s",
        flush=True,
    )

    records: list[SpecRecord] = []

    try:
        if workers <= 1:
            for task in tasks:
                index, sql, ddl, task_conn, t, b = task
                record = process_speculate_case(index, sql, ddl, task_conn, t, "mysql", b)
                _print_spec_record(record)
                records.append(record)
        else:
            records_by_index: dict[int, SpecRecord] = {}
            with ProcessPool(max_workers=workers) as pool:
                future_to_task = {
                    pool.schedule(
                        _process_speculate_task, args=(task,), timeout=timeout
                    ): task
                    for task in tasks
                }

                for future in as_completed(future_to_task):
                    task = future_to_task[future]
                    index = task[0]

                    try:
                        record = future.result()
                        records_by_index[index] = record
                        _print_spec_record(record)
                    except TimeoutError:
                        elapsed = float(timeout)
                        record = SpecRecord(
                            index=index, db_id="mysql",
                            sql=task[1], seed_status="timeout",
                            seed_reason=f"exceeded_task_timeout_{timeout}s",
                            elapsed_seconds=elapsed,
                            shape_tags=sql_shape_tags(task[1]),
                        )
                        records_by_index[index] = record
                        _print_spec_record(record)
                    except ProcessExpired as e:
                        record = SpecRecord(
                            index=index, db_id="mysql",
                            sql=task[1], seed_status="error",
                            seed_reason=f"worker_crashed:{e}",
                            elapsed_seconds=0.0,
                            shape_tags=sql_shape_tags(task[1]),
                        )
                        records_by_index[index] = record
                        _print_spec_record(record)
                    except Exception as e:
                        record = SpecRecord(
                            index=index, db_id="mysql",
                            sql=task[1], seed_status="error",
                            seed_reason=f"exception:{type(e).__name__}:{e}",
                            elapsed_seconds=0.0,
                            shape_tags=sql_shape_tags(task[1]),
                        )
                        records_by_index[index] = record
                        _print_spec_record(record)

            records = [records_by_index[index] for index, *_rest in tasks]
    finally:
        _cleanup_databases(connection_string, tasks)

    metrics = compute_speculate_metrics(records)

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_fp = os.path.join(output_dir, f"mysql_speculate_results_{ts}.json")
    metrics_fp = os.path.join(output_dir, f"mysql_speculate_metrics_{ts}.json")

    with open(results_fp, "w") as f:
        json.dump(metrics["records"], f, indent=2)
    print(f"Wrote {len(records)} records to {results_fp}", flush=True)

    with open(metrics_fp, "w") as f:
        json.dump({k: v for k, v in metrics.items() if k != "records"}, f, indent=2)
    print(f"Wrote metrics to {metrics_fp}", flush=True)

    _print_speculate_summary(metrics)
    return records


def _print_speculate_summary(metrics: dict[str, Any]) -> None:
    print("\n=== Speculate Experiment Summary ===", flush=True)
    print(f"Total queries: {metrics['total']}", flush=True)
    print(f"  sat_via_db:     {metrics['sat_via_db']}", flush=True)
    print(f"  empty_result:   {metrics['empty_result']}", flush=True)
    print(f"  unsat:          {metrics['unsat']}", flush=True)
    print(f"  error:          {metrics['error']}", flush=True)
    print(f"  timeout:        {metrics['timeout']}", flush=True)
    print(f"  Elapsed total:  {metrics['elapsed_total_seconds']:.3f}s", flush=True)
    print(f"  Elapsed avg:    {metrics['elapsed_avg_seconds']:.3f}s", flush=True)
    print(f"  Elapsed sat avg: {metrics['elapsed_sat_avg_seconds']:.3f}s", flush=True)
    print(f"  Instance rows avg: {metrics['instance_rows_avg']:.1f}", flush=True)
    print(f"  Validation rows avg: {metrics['validation_rows_avg']:.1f}", flush=True)
    print(f"  Shape tags: {metrics['shape_tag_counts']}", flush=True)


def _process_speculate_task(task: tuple) -> SpecRecord:
    index, sql, ddl, task_conn, timeout, bounds = task
    return process_speculate_case(index, sql, ddl, task_conn, timeout, "mysql", bounds)





# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run MySQL speculative seeding with DB validation."
    )
    parser.add_argument("--data_fp", default=str(DEFAULT_MYSQL_DATA_FP))
    parser.add_argument("--connection_string", default=DEFAULT_MYSQL_CONNECTION)
    parser.add_argument("--output_dir", default="results")
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--timeout", type=int, default=60,
                        help="Query execution timeout per task in seconds")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)

    # BmcBounds (speculate mode only)
    parser.add_argument("--table-rows", type=int, default=3)
    parser.add_argument("--join-width", type=int, default=1)
    parser.add_argument("--groups", type=int, default=2)
    parser.add_argument("--rows-per-group", type=int, default=3)
    parser.add_argument("--subquery-rows", type=int, default=3)
    parser.add_argument("--order-competitors", type=int, default=1)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--max-table-rows", type=int, default=512)
    parser.add_argument("--use-first-of-pair", action="store_true", default=True,
                        help="Use the first SQL in each pair for speculate mode")
    parser.add_argument("--no-use-first-of-pair", action="store_false",
                        dest="use_first_of_pair")

    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    run_speculate_experiment(
        data_fp=args.data_fp,
        connection_string=args.connection_string,
        output_dir=args.output_dir,
        limit=args.limit,
        start=args.start,
        workers=args.workers,
        timeout=args.timeout,
        table_rows=args.table_rows,
        join_width=args.join_width,
        groups=args.groups,
        rows_per_group=args.rows_per_group,
        subquery_rows=args.subquery_rows,
        order_competitors=args.order_competitors,
        max_iterations=args.max_iterations,
        max_table_rows=args.max_table_rows,
        use_first_of_pair=args.use_first_of_pair,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
