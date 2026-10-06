"""Check that a generated database makes each BIRD-dev gold query return rows.

For every gold SQL in data/sqlite/dev.json, generate databases from the
db_id's schema, save them as SQLite files, and re-run the gold query with
SQLite itself to see whether any generated database yields a non-empty result.
Each query record and the final summary log end_to_end_s in wall-clock seconds.

Example:
    python scripts/benchmark_bird_dev.py --limit 20 --output results/bird-dev.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit_postgres_dataset import replay_sqlite  # noqa: E402
from parseval import GenerationConfig, instantiate_db  # noqa: E402

DEV = ROOT / "data" / "sqlite" / "dev.json"
SCHEMA = ROOT / "data" / "sqlite" / "schema.json"


def load_rows(dev: Path, schema: Path) -> list[dict[str, str]]:
    schemas = {dbid: ";\n".join(ddl) for dbid, ddl in json.loads(schema.read_text(encoding="utf-8")).items()}
    rows = []
    for item in json.loads(dev.read_text(encoding="utf-8")):
        rows.append({
            "dbid": item["db_id"],
            "index": str(item["question_id"]),
            "dialect": "sqlite",
            "schema_ddl": schemas[item["db_id"]],
            "q1": item["SQL"],
            "difficulty": item.get("difficulty", ""),
        })
    return rows


def status(record: dict) -> str:
    if "error" in record:
        return "generation_error"
    if record.get("unsupported_generation"):
        return "unsupported"
    return {
        "nonempty": "nonempty",
        "empty": "empty",
        "error": "replay_error",
        "timeout": "replay_timeout",
        "not_run": "no_instances",
    }[record["sqlite_replay_status"]]


def run_case(row: dict[str, str], sqlite_dir: Path, config: GenerationConfig, sqlite_wall_s: float) -> dict:
    """Instantiate one BIRD database and replay its gold query with SQLite."""
    started = monotonic()
    relative = Path(row["dbid"]) / row["index"] / "0.sqlite"
    path = sqlite_dir / relative
    record = {
        "dbid": row["dbid"], "index": row["index"], "query": "q1",
        "sqlite_dir": str(sqlite_dir), "sqlite": [], "sqlite_checks": [],
        "sqlite_nonempty": None, "sqlite_replay_status": "not_run",
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        generated = instantiate_db(
            row["q1"], row["schema_ddl"], f"sqlite:///{path.resolve()}", "sqlite",
            config=config,
        )
        record.update({
            "elapsed_s": round(monotonic() - started, 3),
            "instances": int(generated.instance is not None),
            "populated": bool(generated.instance and generated.instance.row_count),
            "productive": generated.nonempty,
            "unsupported_generation": generated.unsupported,
            "coverage": {
                "reached": generated.coverage.reached,
                "covered": generated.coverage.covered,
                "failed": generated.coverage.failed,
                "ratio": generated.coverage.ratio,
            },
            "attempts": len(generated.attempts),
        })
    except Exception as error:
        record["error"] = {"type": type(error).__name__, "message": str(error)[:500]}
    else:
        if generated.instance is not None:
            check = replay_sqlite(path, row["q1"], "sqlite", sqlite_wall_s)
            record["sqlite"] = [relative.as_posix()]
            record["sqlite_checks"] = [{"path": relative.as_posix(), **check}]
            record["sqlite_nonempty"] = check["nonempty"]
            record["sqlite_replay_status"] = (
                "nonempty" if check["nonempty"] is True
                else "empty" if check["nonempty"] is False
                else check["status"]
            )
    record["end_to_end_s"] = round(monotonic() - started, 3)
    record["difficulty"] = row["difficulty"]
    record["sql"] = row["q1"]
    record["outcome"] = status(record)
    return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev", type=Path, default=DEV)
    parser.add_argument("--schema", type=Path, default=SCHEMA)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "bird-dev.jsonl")
    parser.add_argument("--sqlite-dir", type=Path, default=ROOT / "tmp" / "bird-dev", help="Where generated instances are saved (default: tmp/bird-dev)")
    parser.add_argument("--dbid", action="append", help="Restrict to database IDs")
    parser.add_argument("--index", action="append", help="Restrict to question_id values")
    parser.add_argument("--difficulty", action="append", help="simple, moderate or challenging")
    parser.add_argument("--limit", type=int, default=10, help="Maximum queries; 0 runs all (default: 10)")
    parser.add_argument("--solver-timeout-ms", type=int, default=5_000)
    parser.add_argument("--case-timeout-s", type=float, default=120, help="Generation time budget; returns the latest accepted database")
    parser.add_argument("--sqlite-wall-s", type=float, default=5, help="Replay budget per saved database")
    parser.add_argument("--workers", type=int, default=1, help="Queries generated in parallel")
    parser.add_argument("--no-speculation", action="store_true", help="Start generation from an empty database")
    args = parser.parse_args(argv)
    started = monotonic()
    args.sqlite_dir.mkdir(parents=True, exist_ok=True)
    sqlite_dir = Path(tempfile.mkdtemp(prefix="run-", dir=args.sqlite_dir)).resolve()
    config = GenerationConfig(
        timeout_ms=args.solver_timeout_ms,
        time_limit_s=args.case_timeout_s,
        speculate=not args.no_speculation,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    summary: Counter[str] = Counter()
    by_difficulty: dict[str, Counter[str]] = {}
    selected = []
    for row in load_rows(args.dev, args.schema):
        if args.dbid and row["dbid"] not in args.dbid:
            continue
        if args.index and row["index"] not in args.index:
            continue
        if args.difficulty and row["difficulty"] not in args.difficulty:
            continue
        if args.limit and len(selected) >= args.limit:
            break
        selected.append(row)

    def run(row):
        record = run_case(row, sqlite_dir, config, args.sqlite_wall_s)
        return row, record

    with args.output.open("w", encoding="utf-8") as output, ThreadPoolExecutor(args.workers) as pool:
        for row, record in pool.map(run, selected):
            summary["queries"] += 1
            summary[record["outcome"]] += 1
            by_difficulty.setdefault(row["difficulty"], Counter())[record["outcome"]] += 1
            print(json.dumps(record, default=str), file=output, flush=True)
            print(f'{row["dbid"]}/{row["index"]}: {record["outcome"]} '
                  f'({record["end_to_end_s"]:.3f}s end to end)', file=sys.stderr, flush=True)

    total = summary["queries"]
    report = {
        "summary": dict(summary),
        "nonempty_rate": round(summary["nonempty"] / total, 4) if total else None,
        "by_difficulty": {key: dict(value) for key, value in by_difficulty.items()},
        "end_to_end_s": round(monotonic() - started, 3),
    }
    print(json.dumps(report), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
