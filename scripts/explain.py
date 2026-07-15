#!/usr/bin/env python3
"""Bird / LeetCode coverage for DataFusion planning and Step-IR lowering.

Modes::

    # Step-IR lowering via ``parseval.plan.explain.explain`` (default)
    uv run python scripts/explain.py
    uv run python scripts/explain.py --dev data/sqlite/train.json \\
        --schema data/sqlite/train_schema.json --limit 50

    # DataFusion logical / optimized / physical probe (former fusion_test)
    uv run python scripts/explain.py --mode probe
    uv run python scripts/explain.py --mode probe --format leetcode --limit 100
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from parseval.plan.explain import Plan, PlanError, explain
from parseval.plan.session import DEFAULT_SOURCE_DIALECT, DataFusionSessionManager

DEFAULT_DEV = Path("data/sqlite/dev.json")
DEFAULT_SCHEMA = Path("data/sqlite/schema.json")
DEFAULT_LEETCODE = Path("data/mysql/leetcode.jsonlines")
DEFAULT_LEETCODE_DIALECT = "mysql"

_MYSQL_EXPERIMENT = (
    Path(__file__).resolve().parents[1] / "tests/experiment/test_mysql.py"
)


# ---------------------------------------------------------------------------
# Shared I/O / dataset helpers
# ---------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_jsonlines(path: Path) -> List[dict]:
    rows: List[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def schema_entry_to_ddl(entry: Any) -> str:
    """Flatten a Bird schema entry into a multi-statement DDL string."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Sequence):
        parts = [str(stmt).strip().rstrip(";") for stmt in entry if str(stmt).strip()]
        return ";\n".join(parts) + (";" if parts else "")
    raise TypeError(f"unsupported_schema_entry:{type(entry)!r}")


def _load_mysql_experiment():
    """Load ``tests/experiment/test_mysql.py`` for ``build_ddl`` / query prep."""
    path = _MYSQL_EXPERIMENT
    if not path.is_file():
        raise FileNotFoundError(f"mysql_experiment_missing:{path}")
    spec = importlib.util.spec_from_file_location("parseval_mysql_experiment", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"mysql_experiment_unimportable:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema_cache_key(schema: Mapping[str, Any], constraints: Any) -> str:
    return json.dumps({"s": schema, "c": constraints}, sort_keys=True)


def leetcode_entries_to_queries(
    entries: Sequence[Mapping[str, Any]],
    *,
    mysql_mod: Any = None,
) -> List[dict]:
    """Expand LeetCode pair rows into flat query records."""
    if mysql_mod is None:
        mysql_mod = _load_mysql_experiment()
    queries: List[dict] = []
    for entry in entries:
        schema = entry["schema"]
        prepared = [
            mysql_mod._prepare_mysql_query(sql, schema) for sql in entry["pair"]
        ]
        file_stem = Path(str(entry.get("file", "leetcode"))).stem
        base_index = entry.get("index", len(queries))
        for pair_idx, sql in enumerate(prepared):
            if not mysql_mod._is_select_query(sql):
                continue
            queries.append(
                {
                    "question_id": f"{base_index}:{pair_idx}",
                    "db_id": f"leetcode:{file_stem}",
                    "difficulty": "?",
                    "SQL": sql,
                    "schema": schema,
                    "constraint": entry.get("constraint") or [],
                    "file": entry.get("file"),
                    "pair_index": pair_idx,
                }
            )
    return queries


def classify_error(message: str) -> str:
    lower = message.lower()
    if "invalid function" in lower:
        return "invalid_function"
    if "group by" in lower:
        return "group_by"
    if "select distinct" in lower:
        return "distinct_order"
    if (
        "not found" in lower
        or "no field named" in lower
        or "unable to get field" in lower
    ):
        return "table_or_column_not_found"
    if "parsererror" in lower or "syntax" in lower:
        return "parser"
    if "type_coercion" in lower or "failed to coerce" in lower:
        return "type_coercion"
    if "failed to match any signature" in lower:
        return "type_coercion"
    return "other"


def _format_counter_map(mapping: Mapping[str, Mapping[str, int]]) -> str:
    lines = []
    for key, counts in mapping.items():
        parts = " ".join(f"{name}={value}" for name, value in counts.items())
        lines.append(f"  {key}: {parts}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mode: explain (Step-IR lowering)
# ---------------------------------------------------------------------------


@dataclass
class ExplainResult:
    question_id: Any
    db_id: str
    difficulty: str
    sql: str
    ok: bool = False
    stage: Optional[str] = None
    error: Optional[str] = None
    error_class: Optional[str] = None
    n_steps: Optional[int] = None
    root_kind: Optional[str] = None
    plan_repr: Optional[str] = None


def classify_explain_error(exc: BaseException) -> Tuple[str, str]:
    """Return ``(stage, error_class)`` for a failed ``explain`` call."""
    message = str(exc)
    if isinstance(exc, PlanError):
        return "lower", "plan_error"
    if isinstance(exc, NotImplementedError):
        return "lower", "not_implemented"
    return "plan", classify_error(message)


def run_explain(
    ddl: str,
    query: Mapping[str, Any],
    *,
    dialect: str,
    show_plans: bool = False,
) -> ExplainResult:
    result = ExplainResult(
        question_id=query.get("question_id"),
        db_id=str(query["db_id"]),
        difficulty=str(query.get("difficulty", "?")),
        sql=query["SQL"],
    )
    try:
        plan: Plan = explain(ddl, result.sql, dialect)
    except Exception as exc:  # noqa: BLE001 — batch measurement CLI
        result.stage, result.error_class = classify_explain_error(exc)
        result.error = str(exc)
        return result

    result.ok = True
    result.n_steps = len(plan.dag)
    result.root_kind = type(plan.root).__name__
    if show_plans:
        result.plan_repr = repr(plan)
    return result


def summarize_explain(results: Sequence[ExplainResult]) -> Dict[str, Any]:
    total = len(results)
    ok = sum(1 for r in results if r.ok)
    fail = total - ok
    by_difficulty: Dict[str, Counter] = defaultdict(Counter)
    by_db: Dict[str, Counter] = defaultdict(Counter)
    error_classes: Counter = Counter()
    stages: Counter = Counter()

    for r in results:
        by_difficulty[r.difficulty]["total"] += 1
        by_db[r.db_id]["total"] += 1
        if r.ok:
            by_difficulty[r.difficulty]["ok"] += 1
            by_db[r.db_id]["ok"] += 1
        else:
            by_difficulty[r.difficulty]["fail"] += 1
            by_db[r.db_id]["fail"] += 1
            if r.error_class:
                error_classes[r.error_class] += 1
            if r.stage:
                stages[r.stage] += 1

    return {
        "total": total,
        "ok": ok,
        "fail": fail,
        "ok_rate": (ok / total) if total else 0.0,
        "by_difficulty": {k: dict(v) for k, v in sorted(by_difficulty.items())},
        "by_db": {k: dict(v) for k, v in sorted(by_db.items())},
        "top_error_classes": dict(error_classes.most_common()),
        "fail_stages": dict(stages.most_common()),
    }


def print_explain_summary(summary: Mapping[str, Any]) -> None:
    print(
        f"total={summary['total']}\n"
        f"ok={summary['ok']}  fail={summary['fail']}\n"
        f"ok_rate={summary['ok_rate']:.4f}"
    )
    print("by_difficulty:")
    print(_format_counter_map(summary["by_difficulty"]))
    print("by_db:")
    print(_format_counter_map(summary["by_db"]))
    stages = summary["fail_stages"]
    if stages:
        joined = " ".join(f"{name}={count}" for name, count in stages.items())
        print(f"fail_stages: {joined}")
    classes = summary["top_error_classes"]
    if classes:
        joined = " ".join(f"{name}={count}" for name, count in classes.items())
        print(f"top_error_classes: {joined}")


def write_explain_failures(path: Path, results: Sequence[ExplainResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            if result.ok:
                continue
            row = {
                "question_id": result.question_id,
                "db_id": result.db_id,
                "difficulty": result.difficulty,
                "stage": result.stage,
                "error_class": result.error_class,
                "error": result.error,
                "sql": result.sql,
            }
            handle.write(json.dumps(row) + "\n")


def run_bird_explain(
    *,
    dev_path: Path,
    schema_path: Path,
    limit: Optional[int] = None,
    db_id: Optional[str] = None,
    failures_out: Optional[Path] = None,
    show_plans: bool = False,
    dialect: str = DEFAULT_SOURCE_DIALECT,
) -> Tuple[List[ExplainResult], Dict[str, Any]]:
    queries: List[Mapping[str, Any]] = load_json(dev_path)
    schema: Dict[str, Any] = load_json(schema_path)

    if db_id is not None:
        queries = [q for q in queries if q["db_id"] == db_id]
        schema = {db_id: schema[db_id]} if db_id in schema else {}

    if limit is not None:
        queries = queries[:limit]

    ddl_by_db = {
        name: schema_entry_to_ddl(entry) for name, entry in schema.items()
    }
    missing = sorted({q["db_id"] for q in queries} - set(ddl_by_db))
    if missing:
        raise RuntimeError(f"missing_schema_dbs:{missing}")

    print(f"bird_queries={len(queries)} dbs={len(ddl_by_db)} dialect={dialect}")

    results: List[ExplainResult] = []
    for query in queries:
        result = run_explain(
            ddl_by_db[query["db_id"]],
            query,
            dialect=dialect,
            show_plans=show_plans,
        )
        results.append(result)
        if show_plans and result.ok:
            print(
                f"[ok] qid={result.question_id} db={result.db_id} "
                f"root={result.root_kind} steps={result.n_steps}\n"
                f"{result.plan_repr}\n"
            )

    summary = summarize_explain(results)
    if failures_out is not None:
        write_explain_failures(failures_out, results)
        print(f"failures_out={failures_out}")
    return results, summary


def run_leetcode_explain(
    *,
    data_path: Path,
    limit: Optional[int] = None,
    failures_out: Optional[Path] = None,
    show_plans: bool = False,
    dialect: str = DEFAULT_LEETCODE_DIALECT,
) -> Tuple[List[ExplainResult], Dict[str, Any]]:
    mysql_mod = _load_mysql_experiment()
    entries = load_jsonlines(data_path)
    if limit is not None:
        entries = entries[:limit]

    queries = leetcode_entries_to_queries(entries, mysql_mod=mysql_mod)
    print(
        f"leetcode_entries={len(entries)} queries={len(queries)} "
        f"dialect={dialect}"
    )

    ddl_cache: Dict[str, str] = {}
    schema_errors: Dict[str, str] = {}
    results: List[ExplainResult] = []

    for query in queries:
        cache_key = _schema_cache_key(query["schema"], query["constraint"])
        if cache_key in schema_errors:
            results.append(
                ExplainResult(
                    question_id=query.get("question_id"),
                    db_id=query["db_id"],
                    difficulty=str(query.get("difficulty", "?")),
                    sql=query["SQL"],
                    stage="schema",
                    error=schema_errors[cache_key],
                    error_class=classify_error(schema_errors[cache_key]),
                )
            )
            continue
        if cache_key not in ddl_cache:
            try:
                ddl_cache[cache_key] = mysql_mod.build_ddl(
                    query["schema"], list(query["constraint"] or [])
                )
            except Exception as exc:  # noqa: BLE001
                schema_errors[cache_key] = str(exc)
                results.append(
                    ExplainResult(
                        question_id=query.get("question_id"),
                        db_id=query["db_id"],
                        difficulty=str(query.get("difficulty", "?")),
                        sql=query["SQL"],
                        stage="schema",
                        error=str(exc),
                        error_class=classify_error(str(exc)),
                    )
                )
                continue

        result = run_explain(
            ddl_cache[cache_key],
            query,
            dialect=dialect,
            show_plans=show_plans,
        )
        results.append(result)
        if show_plans and result.ok:
            print(
                f"[ok] qid={result.question_id} db={result.db_id} "
                f"root={result.root_kind} steps={result.n_steps}\n"
                f"{result.plan_repr}\n"
            )

    summary = summarize_explain(results)
    summary["unique_schemas"] = len(ddl_cache)
    summary["schema_failures"] = len(schema_errors)
    print(
        f"unique_schemas={len(ddl_cache)} "
        f"schema_failures={len(schema_errors)}"
    )
    if failures_out is not None:
        write_explain_failures(failures_out, results)
        print(f"failures_out={failures_out}")
    return results, summary


# ---------------------------------------------------------------------------
# Mode: probe (DataFusion logical / optimized / physical)
# ---------------------------------------------------------------------------


@dataclass
class ProbeResult:
    question_id: Any
    db_id: str
    difficulty: str
    sql: str
    normalized_sql: Optional[str] = None
    ok_logical: bool = False
    ok_optimized: bool = False
    ok_physical: bool = False
    stage: Optional[str] = None
    error: Optional[str] = None
    error_class: Optional[str] = None
    logical_plan: Optional[str] = None
    optimized_plan: Optional[str] = None
    physical_plan: Optional[str] = None


def build_contexts(
    schema: Mapping[str, Any],
    db_ids: Optional[Iterable[str]] = None,
    *,
    source_dialect: str = DEFAULT_SOURCE_DIALECT,
) -> Dict[str, DataFusionSessionManager]:
    """Register sanitized DDL for each database."""
    wanted = set(db_ids) if db_ids is not None else None
    sessions: Dict[str, DataFusionSessionManager] = {}
    for db_id, entry in schema.items():
        if wanted is not None and db_id not in wanted:
            continue
        session = DataFusionSessionManager(source_dialect)
        session.execute_ddl(entry)
        sessions[db_id] = session
    return sessions


def build_leetcode_context(
    schema: Mapping[str, Mapping[str, str]],
    constraints: Optional[Sequence[dict]],
    *,
    source_dialect: str = DEFAULT_LEETCODE_DIALECT,
    mysql_mod: Any = None,
) -> DataFusionSessionManager:
    """Build a DataFusion session from a LeetCode schema via ``build_ddl``."""
    if mysql_mod is None:
        mysql_mod = _load_mysql_experiment()
    ddl = mysql_mod.build_ddl(schema, list(constraints or []))
    session = DataFusionSessionManager(source_dialect)
    session.execute_ddl(ddl)
    return session


def probe_query(
    session: DataFusionSessionManager,
    query: Mapping[str, Any],
    *,
    show_plans: bool = False,
) -> ProbeResult:
    result = ProbeResult(
        question_id=query.get("question_id"),
        db_id=query["db_id"],
        difficulty=str(query.get("difficulty", "?")),
        sql=query["SQL"],
    )
    try:
        result.normalized_sql = session.prepare_query(result.sql)
    except Exception as exc:  # noqa: BLE001 — measurement CLI
        result.stage = "normalize"
        result.error = str(exc)
        result.error_class = classify_error(result.error)
        return result

    ctx = session.context
    try:
        df = ctx.sql(result.normalized_sql)
        logical = df.logical_plan()
        result.ok_logical = True
        if show_plans:
            result.logical_plan = str(logical)
    except Exception as exc:  # noqa: BLE001
        result.stage = "sql_logical"
        result.error = str(exc)
        result.error_class = classify_error(result.error)
        return result

    try:
        optimized = df.optimized_logical_plan()
        result.ok_optimized = True
        if show_plans:
            result.optimized_plan = str(optimized)
    except Exception as exc:  # noqa: BLE001
        result.stage = "optimized"
        result.error = str(exc)
        result.error_class = classify_error(result.error)
        return result

    try:
        physical = df.execution_plan()
        result.ok_physical = True
        if show_plans:
            result.physical_plan = str(physical)
    except Exception as exc:  # noqa: BLE001
        result.stage = "physical"
        result.error = str(exc)
        result.error_class = classify_error(result.error)
        return result

    return result


def summarize_probe(results: Sequence[ProbeResult]) -> Dict[str, Any]:
    total = len(results)
    ok_logical = sum(1 for r in results if r.ok_logical)
    ok_optimized = sum(1 for r in results if r.ok_optimized)
    ok_physical = sum(1 for r in results if r.ok_physical)
    fail = sum(1 for r in results if r.error is not None)

    by_difficulty: Dict[str, Counter] = defaultdict(Counter)
    by_db: Dict[str, Counter] = defaultdict(Counter)
    error_classes: Counter = Counter()

    for r in results:
        by_difficulty[r.difficulty]["total"] += 1
        by_db[r.db_id]["total"] += 1
        if r.ok_logical:
            by_difficulty[r.difficulty]["ok_logical"] += 1
            by_db[r.db_id]["ok_logical"] += 1
        if r.ok_optimized:
            by_difficulty[r.difficulty]["ok_optimized"] += 1
            by_db[r.db_id]["ok_optimized"] += 1
        if r.ok_physical:
            by_difficulty[r.difficulty]["ok_physical"] += 1
            by_db[r.db_id]["ok_physical"] += 1
        if r.error_class:
            by_difficulty[r.difficulty]["fail"] += 1
            by_db[r.db_id]["fail"] += 1
            error_classes[r.error_class] += 1

    return {
        "total": total,
        "ok_sql_logical": ok_logical,
        "ok_optimized": ok_optimized,
        "ok_physical": ok_physical,
        "fail": fail,
        "optimized_rate": (ok_optimized / total) if total else 0.0,
        "by_difficulty": {k: dict(v) for k, v in sorted(by_difficulty.items())},
        "by_db": {k: dict(v) for k, v in sorted(by_db.items())},
        "top_error_classes": dict(error_classes.most_common()),
    }


def print_probe_summary(summary: Mapping[str, Any]) -> None:
    print(
        f"total={summary['total']}\n"
        f"ok_sql_logical={summary['ok_sql_logical']}  "
        f"ok_optimized={summary['ok_optimized']}  "
        f"ok_physical={summary['ok_physical']}  "
        f"fail={summary['fail']}\n"
        f"optimized_rate={summary['optimized_rate']:.4f}"
    )
    print("by_difficulty:")
    print(_format_counter_map(summary["by_difficulty"]))
    print("by_db:")
    print(_format_counter_map(summary["by_db"]))
    classes = summary["top_error_classes"]
    if classes:
        joined = " ".join(f"{name}={count}" for name, count in classes.items())
        print(f"top_error_classes: {joined}")


def write_probe_failures(path: Path, results: Sequence[ProbeResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            if result.error is None:
                continue
            row = {
                "question_id": result.question_id,
                "db_id": result.db_id,
                "difficulty": result.difficulty,
                "stage": result.stage,
                "error_class": result.error_class,
                "error": result.error,
                "sql": result.sql,
                "normalized_sql": result.normalized_sql,
            }
            handle.write(json.dumps(row) + "\n")


def run_bird_probe(
    *,
    dev_path: Path,
    schema_path: Path,
    limit: Optional[int] = None,
    db_id: Optional[str] = None,
    failures_out: Optional[Path] = None,
    show_plans: bool = False,
    source_dialect: str = DEFAULT_SOURCE_DIALECT,
) -> Tuple[List[ProbeResult], Dict[str, Any]]:
    queries: List[Mapping[str, Any]] = load_json(dev_path)
    schema: Dict[str, Any] = load_json(schema_path)

    if db_id is not None:
        queries = [q for q in queries if q["db_id"] == db_id]
        schema = {db_id: schema[db_id]} if db_id in schema else {}

    if limit is not None:
        queries = queries[:limit]

    db_ids = sorted({q["db_id"] for q in queries})
    sessions = build_contexts(schema, db_ids=db_ids, source_dialect=source_dialect)
    missing = [name for name in db_ids if name not in sessions]
    if missing:
        raise RuntimeError(f"failed_to_register_dbs:{missing}")

    print(f"registered_dbs={len(sessions)}/{len(db_ids)}")

    results: List[ProbeResult] = []
    for query in queries:
        result = probe_query(
            sessions[query["db_id"]],
            query,
            show_plans=show_plans,
        )
        results.append(result)
        if show_plans and result.ok_optimized:
            print(
                f"[ok] qid={result.question_id} db={result.db_id}\n"
                f"{result.optimized_plan}\n"
            )

    summary = summarize_probe(results)
    if failures_out is not None:
        write_probe_failures(failures_out, results)
        print(f"failures_out={failures_out}")
    return results, summary


def run_leetcode_probe(
    *,
    data_path: Path,
    limit: Optional[int] = None,
    failures_out: Optional[Path] = None,
    show_plans: bool = False,
    source_dialect: str = DEFAULT_LEETCODE_DIALECT,
) -> Tuple[List[ProbeResult], Dict[str, Any]]:
    mysql_mod = _load_mysql_experiment()
    entries = load_jsonlines(data_path)
    if limit is not None:
        entries = entries[:limit]

    queries = leetcode_entries_to_queries(entries, mysql_mod=mysql_mod)
    print(
        f"leetcode_entries={len(entries)} queries={len(queries)} "
        f"dialect={source_dialect}"
    )

    session_cache: Dict[str, DataFusionSessionManager] = {}
    schema_errors: Dict[str, str] = {}
    results: List[ProbeResult] = []

    for query in queries:
        cache_key = _schema_cache_key(query["schema"], query["constraint"])
        if cache_key in schema_errors:
            results.append(
                ProbeResult(
                    question_id=query.get("question_id"),
                    db_id=query["db_id"],
                    difficulty=str(query.get("difficulty", "?")),
                    sql=query["SQL"],
                    stage="schema",
                    error=schema_errors[cache_key],
                    error_class=classify_error(schema_errors[cache_key]),
                )
            )
            continue
        if cache_key not in session_cache:
            try:
                session_cache[cache_key] = build_leetcode_context(
                    query["schema"],
                    query["constraint"],
                    source_dialect=source_dialect,
                    mysql_mod=mysql_mod,
                )
            except Exception as exc:  # noqa: BLE001
                schema_errors[cache_key] = str(exc)
                results.append(
                    ProbeResult(
                        question_id=query.get("question_id"),
                        db_id=query["db_id"],
                        difficulty=str(query.get("difficulty", "?")),
                        sql=query["SQL"],
                        stage="schema",
                        error=str(exc),
                        error_class=classify_error(str(exc)),
                    )
                )
                continue
        result = probe_query(
            session_cache[cache_key],
            query,
            show_plans=show_plans,
        )
        results.append(result)
        if show_plans and result.ok_optimized:
            print(
                f"[ok] qid={result.question_id} db={result.db_id}\n"
                f"{result.optimized_plan}\n"
            )

    summary = summarize_probe(results)
    summary["unique_schemas"] = len(session_cache)
    summary["schema_failures"] = len(schema_errors)
    print(
        f"unique_schemas={len(session_cache)} "
        f"schema_failures={len(schema_errors)}"
    )
    if failures_out is not None:
        write_probe_failures(failures_out, results)
        print(f"failures_out={failures_out}")
    return results, summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Bird / LeetCode coverage: Step-IR lowering (--mode explain) "
            "or DataFusion plan probe (--mode probe)."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("explain", "probe"),
        default="explain",
        help="explain=Step-IR lowering (default); probe=DF logical/optimized/physical.",
    )
    parser.add_argument(
        "--format",
        choices=("bird", "leetcode"),
        default="bird",
        help="Dataset format: bird (shared schema JSON) or leetcode (jsonlines).",
    )
    parser.add_argument("--dev", type=Path, default=None)
    parser.add_argument("--schema", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--db-id", type=str, default=None)
    parser.add_argument("--failures-out", type=Path, default=None)
    parser.add_argument(
        "--dialect",
        type=str,
        default=None,
        help="sqlglot source dialect (default: sqlite for bird, mysql for leetcode).",
    )
    parser.add_argument(
        "--show-plans",
        action="store_true",
        help="Print plan text for successful queries.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.mode == "probe":
        if args.format == "leetcode":
            _, summary = run_leetcode_probe(
                data_path=args.dev or DEFAULT_LEETCODE,
                limit=args.limit,
                failures_out=args.failures_out,
                show_plans=args.show_plans,
                source_dialect=args.dialect or DEFAULT_LEETCODE_DIALECT,
            )
        else:
            _, summary = run_bird_probe(
                dev_path=args.dev or DEFAULT_DEV,
                schema_path=args.schema or DEFAULT_SCHEMA,
                limit=args.limit,
                db_id=args.db_id,
                failures_out=args.failures_out,
                show_plans=args.show_plans,
                source_dialect=args.dialect or DEFAULT_SOURCE_DIALECT,
            )
        print_probe_summary(summary)
        return 0

    if args.format == "leetcode":
        _, summary = run_leetcode_explain(
            data_path=args.dev or DEFAULT_LEETCODE,
            limit=args.limit,
            failures_out=args.failures_out,
            show_plans=args.show_plans,
            dialect=args.dialect or DEFAULT_LEETCODE_DIALECT,
        )
    else:
        _, summary = run_bird_explain(
            dev_path=args.dev or DEFAULT_DEV,
            schema_path=args.schema or DEFAULT_SCHEMA,
            limit=args.limit,
            db_id=args.db_id,
            failures_out=args.failures_out,
            show_plans=args.show_plans,
            dialect=args.dialect or DEFAULT_SOURCE_DIALECT,
        )
    print_explain_summary(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
