"""MySQL experiment runner — disprove equivalence on LeetCode dataset pairs.

Usage::

    python tests/experiment/test_mysql.py --workers 16
    python tests/experiment/test_mysql.py --limit 100 --workers 8
    python tests/experiment/test_mysql.py --workers 16 --case_timeout 30
"""

import argparse
import datetime
import json
import multiprocessing
import os
import queue
import re
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


def _constraint_refs(node) -> set[tuple[str, str]]:
    if isinstance(node, dict):
        if set(node) == {"value"}:
            return {_parse_ref(node["value"])}
        refs: set[tuple[str, str]] = set()
        for value in node.values():
            refs.update(_constraint_refs(value))
        return refs
    if isinstance(node, list):
        refs: set[tuple[str, str]] = set()
        for value in node:
            refs.update(_constraint_refs(value))
        return refs
    return set()


def _sql_literal(value) -> str:
    if isinstance(value, dict) and set(value) == {"date"}:
        return _sql_literal(value["date"])
    if isinstance(value, dict) and set(value) == {"literal"}:
        return _sql_literal(value["literal"])
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if value is None:
        return "NULL"
    return str(value)


def _sql_operand(value, table: str) -> str:
    if isinstance(value, dict) and set(value) == {"value"}:
        ref_table, col = _parse_ref(value["value"])
        if ref_table != table:
            raise ValueError("cross-table operand")
        return col
    return _sql_literal(value)


def _row_local_check(c: dict) -> tuple[str, str] | None:
    if len(c) != 1:
        return None
    op, values = next(iter(c.items()))
    if op in {"primary", "foreign", "inc", "consec"}:
        return None
    refs = _constraint_refs(values)
    tables = {table for table, _column in refs}
    if len(tables) != 1:
        return None
    table = next(iter(tables))
    try:
        expression = _render_check_expression(op, values, table)
    except ValueError:
        return None
    if expression is None:
        return None
    return table, expression


def _render_check_expression(op: str, values, table: str) -> str | None:
    comparisons = {
        "eq": "=",
        "neq": "<>",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
    }
    if op in comparisons:
        lhs, rhs = values
        return f"{_sql_operand(lhs, table)} {comparisons[op]} {_sql_operand(rhs, table)}"
    if op == "between":
        column, low, high = values
        return (
            f"{_sql_operand(column, table)} BETWEEN "
            f"{_sql_operand(low, table)} AND {_sql_operand(high, table)}"
        )
    if op == "in":
        column = values[0]
        raw_choices = values[1:]
        if len(raw_choices) == 1 and isinstance(raw_choices[0], list):
            raw_choices = raw_choices[0]
        choices = ", ".join(_sql_operand(value, table) for value in raw_choices)
        return f"{_sql_operand(column, table)} IN ({choices})"
    if op == "imply":
        antecedent, consequent = values
        lhs = _render_nested_check(antecedent, table)
        rhs = _render_nested_check(consequent, table)
        if lhs is None or rhs is None:
            return None
        return f"(NOT ({lhs}) OR ({rhs}))"
    return None


def _render_nested_check(node, table: str) -> str | None:
    if not isinstance(node, dict) or len(node) != 1:
        return None
    op, values = next(iter(node.items()))
    return _render_check_expression(op, values, table)


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


def _prepare_mysql_query(sql: str, schema: dict) -> str:
    """Normalize known LeetCode MySQL dataset quirks before running cases."""
    sql = _quote_mysql_enum_literals(sql, schema)
    sql = _quote_mysql_reserved_rank_alias(sql)
    return sql


def _quote_mysql_enum_literals(sql: str, schema: dict) -> str:
    enum_values = sorted(
        {
            value.strip()
            for columns in schema.values()
            for dtype in columns.values()
            if dtype.strip().upper().startswith("ENUM,")
            for value in dtype.strip().split(",")[1:]
            if value.strip()
        },
        key=len,
        reverse=True,
    )
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


def _topological_sort(deps: dict[str, set[str]]) -> list[str]:
    """Kahn's algorithm. Parents before children. Cycles append remaining."""
    in_degree: dict[str, int] = {t: 0 for t in deps}
    children: dict[str, list[str]] = {t: [] for t in deps}

    for node, parents in deps.items():
        for parent in parents:
            if parent != node:
                children[parent].append(node)
                in_degree[node] += 1

    queue = [t for t in deps if in_degree[t] == 0]
    sorted_tables: list[str] = []
    while queue:
        node = queue.pop(0)
        sorted_tables.append(node)
        for child in children[node]:
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    sorted_tables.extend(t for t in deps if t not in sorted_tables)
    return sorted_tables


def build_ddl(schema: dict, constraints: list[dict] | None) -> str:
    """Convert a schema dict and constraints into MySQL DDL.

    Args:
        schema: {"TABLE": {"COL": "TYPE", ...}, ...}
        constraints: list of primary/foreign key constraint dicts.

    Returns:
        Semicolon-separated CREATE TABLE statements.
        Tables are ordered so referenced tables appear before tables
        that reference them (topological sort by FK dependencies).
    """
    primary_keys: dict[str, list[list[str]]] = {}
    fks: list[tuple[str, str, str, str]] = []
    checks_by_table: dict[str, list[str]] = {}

    for c in constraints or []:
        if "primary" in c:
            cols = []
            for entry in c["primary"]:
                tbl, col = _parse_ref(entry["value"])
                cols.append(col)
            if cols:
                primary_keys.setdefault(tbl, []).append(cols)

        elif "foreign" in c:
            entries = c["foreign"]
            fk_tbl, fk_col = _parse_ref(entries[0]["value"])
            ref_tbl, ref_col = _parse_ref(entries[1]["value"])
            fks.append((fk_tbl, fk_col, ref_tbl, ref_col))
        else:
            check = _row_local_check(c)
            if check is not None:
                table, expression = check
                checks_by_table.setdefault(table, []).append(expression)

    deps: dict[str, set[str]] = {t: set() for t in schema}
    for fk_tbl, _fk_col, ref_tbl, _ref_col in fks:
        if fk_tbl != ref_tbl and fk_tbl in deps and ref_tbl in deps:
            deps[fk_tbl].add(ref_tbl)

    referenced_cols: dict[str, set[str]] = {}
    for _fk_tbl, _fk_col, ref_tbl, ref_col in fks:
        referenced_cols.setdefault(ref_tbl, set()).add(ref_col)

    stmts: list[str] = []
    created_tables: set[str] = set()

    for table in _topological_sort(deps):
        columns = schema[table]
        col_defs = [
            f"{col} {_normalize_dtype(dtype)}"
            for col, dtype in columns.items()
        ]

        table_primary_keys = primary_keys.get(table, [])
        pk_cols = set(table_primary_keys[0]) if table_primary_keys else set()
        if pk_cols:
            col_defs.append(f"PRIMARY KEY ({', '.join(table_primary_keys[0])})")

        seen_keys = {tuple(table_primary_keys[0])} if table_primary_keys else set()
        for unique_cols in table_primary_keys[1:]:
            key = tuple(unique_cols)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            col_defs.append(f"UNIQUE ({', '.join(unique_cols)})")

        for ref_col in referenced_cols.get(table, set()):
            if ref_col not in pk_cols and [ref_col] not in table_primary_keys:
                col_defs.append(f"INDEX ({ref_col})")

        for fk_tbl, fk_col, ref_tbl, ref_col in fks:
            if fk_tbl == table:
                if fk_tbl == ref_tbl or ref_tbl in created_tables:
                    col_defs.append(
                        f"FOREIGN KEY ({fk_col}) REFERENCES {ref_tbl}({ref_col})"
                    )

        for check_expr in checks_by_table.get(table, ()):
            col_defs.append(f"CHECK ({check_expr})")

        stmts.append(f"CREATE TABLE {table} ({', '.join(col_defs)})")
        created_tables.add(table)

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

        _apply_result_metadata(record, result)

    except CaseTimeoutError:
        record["verdict"] = "timeout"
        record["error_msg"] = f"Timed out after {case_timeout} seconds"
        record["debug_category"] = "timeout"
        record["rows_generated"] = None
        record["generation_coverage"] = None

    except Exception as exc:
        record.update(
            _record_for_exception(
                index=index,
                sql1=sql1,
                sql2=sql2,
                exc=exc,
                elapsed_time=record["elapsed_time"],
            )
        )

    finally:
        if case_timeout and case_timeout > 0:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)

        record["elapsed_time"] = round(time.time() - t0, 4)

    return record


def _apply_result_metadata(record: dict, result) -> None:
    record["verdict"] = result.verdict.value
    record["error_msg"] = result.error_msg or ""
    record["rows_generated"] = result.generation.rows_generated
    record["generation_coverage"] = result.generation.coverage
    record["debug_category"] = _debug_category_for_result(result)


def _record_for_exception(
    *,
    index: int,
    sql1: str,
    sql2: str,
    exc: Exception,
    elapsed_time: float,
) -> dict:
    return {
        "index": index,
        "sql1": sql1,
        "sql2": sql2,
        "verdict": _classify_mysql_exception(exc),
        "error_msg": str(exc)[:500],
        "elapsed_time": elapsed_time,
        "debug_category": _debug_category_for_exception(exc),
        "rows_generated": None,
        "generation_coverage": None,
    }


def _debug_category_for_result(result) -> str:
    if result.verdict.value == "timeout":
        return "timeout"
    if result.verdict.value == "eq":
        if result.generation.coverage < 1.0:
            return "matched_partial_coverage"
        return "matched_generated_instance"
    message = " ".join(
        value
        for value in (
            result.error_msg,
            result.q1_result.error_msg,
            result.q2_result.error_msg,
            result.generation.error_msg,
        )
        if value
    )
    if message:
        return _debug_category_for_message(message)
    return result.verdict.value


def _debug_category_for_exception(exc: Exception) -> str:
    return _debug_category_for_message(str(exc))


def _debug_category_for_message(message: str) -> str:
    lower = message.lower()
    if "timed out" in lower or "timeout" in lower:
        return "timeout"
    if (
        "failed to open the referenced table" in lower
        or "cannot add foreign key constraint" in lower
        or "foreign key constraint fails" in lower
    ):
        return "db_write_fk_order"
    if "only_full_group_by" in lower or "not in group by clause" in lower:
        return "mysql_strict_group_by"
    if "unknown column" in lower or "unresolved column" in lower:
        return "unknown_column_or_literal"
    if "sql syntax" in lower or "syntax error" in lower:
        return "mysql_invalid_query"
    return "db_write_error" if _looks_like_db_write_error(lower) else "execution_error"


def _looks_like_db_write_error(message: str) -> bool:
    return any(
        needle in message
        for needle in (
            "duplicate entry",
            "data truncated",
            "truncated for column",
        )
    )


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

    if case_timeout and case_timeout > 0:
        return _process_disprove_task_with_process_timeout(task)

    return _process_disprove_case(
        index=index,
        sql1=sql1,
        sql2=sql2,
        ddls=ddls,
        connection_string=connection_string,
        table_names=table_names,
        case_timeout=None,
    )


def _process_disprove_task_with_process_timeout(task: tuple) -> dict:
    (
        index,
        sql1,
        sql2,
        _ddls,
        _connection_string,
        _table_names,
        case_timeout,
    ) = task

    t0 = time.time()
    result_queue = multiprocessing.Queue(maxsize=1)
    process = multiprocessing.Process(
        target=_process_disprove_task_child,
        args=(task, result_queue),
    )
    process.start()
    process.join(case_timeout)

    try:
        if process.is_alive():
            process.terminate()
            process.join(timeout=1)
            if process.is_alive():
                process.kill()
                process.join()

            return {
                "index": index,
                "sql1": sql1,
                "sql2": sql2,
                "verdict": "timeout",
                "error_msg": f"Timed out after {case_timeout} seconds",
                "elapsed_time": round(time.time() - t0, 4),
                "debug_category": "timeout",
                "rows_generated": None,
                "generation_coverage": None,
            }

        try:
            return result_queue.get_nowait()
        except queue.Empty:
            return {
                "index": index,
                "sql1": sql1,
                "sql2": sql2,
                "verdict": "execution_error",
                "error_msg": (
                    f"Case process exited without result "
                    f"(exitcode={process.exitcode})"
                ),
                "elapsed_time": round(time.time() - t0, 4),
                "debug_category": "execution_error",
                "rows_generated": None,
                "generation_coverage": None,
            }
    finally:
        result_queue.close()
        result_queue.join_thread()


def _process_disprove_task_child(task: tuple, result_queue) -> None:
    (
        index,
        sql1,
        sql2,
        ddls,
        connection_string,
        table_names,
        _case_timeout,
    ) = task

    record = _process_disprove_case(
        index=index,
        sql1=sql1,
        sql2=sql2,
        ddls=ddls,
        connection_string=connection_string,
        table_names=table_names,
        case_timeout=None,
    )
    result_queue.put(record)


def _classify_mysql_exception(exc: Exception) -> str:
    message = str(exc).lower()
    code = _mysql_error_code(exc)
    if code == 1064 or "sql syntax" in message or "syntax error" in message:
        return "syntax_error"
    if (
        code in {1062, 1265}
        or "duplicate entry" in message
        or "data truncated" in message
        or "truncated for column" in message
        or "failed to open the referenced table" in message
        or "cannot add foreign key constraint" in message
        or "foreign key constraint fails" in message
    ):
        return "db_write_error"
    if "only_full_group_by" in message or "not in group by clause" in message:
        return "execution_error"
    return "execution_error"


def _mysql_error_code(exc: Exception) -> int | None:
    for value in getattr(exc, "args", ()):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


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
        sql1, sql2 = (
            _prepare_mysql_query(sql, entry["schema"])
            for sql in entry["pair"]
        )

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
