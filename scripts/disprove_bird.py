"""Check predicted SQL against the BIRD-dev gold queries with ``disprove`` on SQLite.

Each line of the predictions file (data/sqlite/dail.txt: DAIL-SQL's
predictions) is the prediction for the question at the same position of
data/sqlite/dev.json. Each record logs the verdict, its reason and
end_to_end_s in wall-clock seconds; the summary counts verdicts (ERROR when
a check crashed) and is written next to the output as
``<output stem>.summary.json``.

Example:
    python scripts/disprove_bird.py --workers 32 --limit 0
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path
from time import monotonic

from parseval import GenerationConfig, Verdict, disprove

ROOT = Path(__file__).resolve().parents[1]
DEV = ROOT / "data" / "sqlite" / "dev.json"
SCHEMA = ROOT / "data" / "sqlite" / "schema.json"
PREDICTIONS = ROOT / "data" / "sqlite" / "dail.txt"


def run_case(row: dict, config: GenerationConfig, query_timeout_s: float) -> dict:
    started = monotonic()
    record = {"dbid": row["dbid"], "index": row["index"], "difficulty": row["difficulty"]}
    # One failing pair must not end the run.
    try:
        result = disprove(
            row["gold"], row["predicted"], row["schema"], "sqlite:///:memory:", "sqlite",
            config=config, timeout_s=query_timeout_s,
        )
    except Exception as error:
        record.update(verdict="ERROR", reason=f"{type(error).__name__}: {str(error)[:500]}", results=None)
    else:
        record.update(
            verdict=result.verdict.value, reason=result.reason,
            results=result.results and [str(item)[:300] for item in result.results],
        )
    return {**record, "end_to_end_s": round(monotonic() - started, 3), "gold": row["gold"], "predicted": row["predicted"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dev", type=Path, default=DEV)
    parser.add_argument("--schema", type=Path, default=SCHEMA)
    parser.add_argument("--predictions", type=Path, default=PREDICTIONS)
    parser.add_argument("--output", type=Path, default=ROOT / "results" / "disprove-bird.jsonl")
    parser.add_argument("--dbid", action="append", help="Restrict to database IDs")
    parser.add_argument("--index", action="append", help="Restrict to question_id values")
    parser.add_argument("--start", type=int, default=0, help="Pairs to skip first")
    parser.add_argument("--limit", type=int, default=10, help="Maximum pairs; 0 runs all (default: 10)")
    parser.add_argument("--solver-timeout-ms", type=int, default=5_000)
    parser.add_argument("--case-timeout-s", type=float, default=360, help="Generation time budget per query")
    parser.add_argument("--query-timeout-s", type=float, default=15, help="SQLite time budget per query run")
    parser.add_argument("--workers", type=int, default=1, help="Pairs checked in parallel")
    args = parser.parse_args(argv)
    started = monotonic()
    schemas = {dbid: ";\n".join(ddl) for dbid, ddl in json.loads(args.schema.read_text(encoding="utf-8")).items()}
    predictions = args.predictions.read_text(encoding="utf-8").split("\n")
    rows = [
        {
            "dbid": item["db_id"], "index": str(item["question_id"]), "difficulty": item.get("difficulty", ""),
            "schema": schemas[item["db_id"]], "gold": item["SQL"], "predicted": predicted.strip(),
        }
        for item, predicted in zip(json.loads(args.dev.read_text(encoding="utf-8")), predictions, strict=True)
        if (not args.dbid or item["db_id"] in args.dbid)
        and (not args.index or str(item["question_id"]) in args.index)
    ]
    rows = rows[args.start:][: args.limit or None]
    config = GenerationConfig(timeout_ms=args.solver_timeout_ms, time_limit_s=args.case_timeout_s)
    case = partial(run_case, config=config, query_timeout_s=args.query_timeout_s)
    verdicts: Counter[str] = Counter({verdict.value: 0 for verdict in Verdict} | {"ERROR": 0})
    times = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output, ProcessPoolExecutor(args.workers) as pool:
        for record in pool.map(case, rows):
            verdicts[record["verdict"]] += 1
            times.append(record["end_to_end_s"])
            print(json.dumps(record, default=str), file=output, flush=True)
            print(f'{record["dbid"]}/{record["index"]}: {record["verdict"]} ({record["end_to_end_s"]:.3f}s)',
                  file=sys.stderr, flush=True)
    report = {
        "pairs": len(times),
        **verdicts,
        "median_s": statistics.median(times) if times else None,
        "max_s": max(times, default=None),
        "end_to_end_s": round(monotonic() - started, 3),
    }
    args.output.with_name(f"{args.output.stem}.summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
