import dataclasses
import json
import os
from typing import Any
from parseval.main import disprove

# Optional progress bar; fallback to passthrough if tqdm isn't installed
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
        for line in f.readlines():
            if line.strip():
                lines.append(line.strip())
    return lines


def analyze_results(results, output_dir: str, metrics_file: str = "sqlite_test_metrics.json"):
    """Compute simple metrics from the produced results and write them to a file.

    Metrics produced:
      - total_pairs: total number of query pairs
      - verdict_counts: counts per Verdict string (e.g., 'neq', 'eq')
      - verdict_ratio: same as counts but normalized by total
    """
    total = len(results)
    verdict_counts = {}
    for entry in results:
        res = entry.get("result") or {}
        # serialized DisproveResult has a 'verdict' field with string value
        v = None
        if isinstance(res, dict):
            v = res.get("verdict")
        if v is None:
            v = "unknown"
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    verdict_ratio = {k: (v / total if total > 0 else 0.0) for k, v in verdict_counts.items()}

    metrics = {
        "total_pairs": total,
        "verdict_counts": verdict_counts,
        "verdict_ratio": verdict_ratio,
    }

    os.makedirs(output_dir, exist_ok=True)
    metrics_fp = os.path.join(output_dir, metrics_file)
    with open(metrics_fp, "w") as f:
        json.dump(metrics, f, indent=2)

    # Print a concise summary
    print("--- Analysis Summary ---")
    print(f"Total pairs: {total}")
    for k, cnt in sorted(verdict_counts.items(), key=lambda x: x[0]):
        print(f"{k}: {cnt} ({verdict_ratio[k]:.2%})")
    print(f"Wrote metrics to {metrics_fp}")


def main(args):
    schemas = load_schema(args.schema_fp)
    gold = load_gold(args.gold_fp)
    preds = load_preds(args.preds_fp)

    results = []
    os.makedirs(args.output_dir, exist_ok=True)
    # ensure per-run temporary DB directory exists
    tmp_dir = os.path.join("tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    for index in tqdm(range(len(gold)), desc="Processing examples"):
        row = gold[index]
        gold_sql = row.get("SQL")
        pred_sql = preds[index] if index < len(preds) else ""
        db_id = row.get("db_id")
        ddls = ";".join(schemas.get(db_id, []))
        # Use SQLAlchemy-style sqlite URL with absolute path to avoid backend mismatch
        db_path = os.path.abspath(os.path.join(tmp_dir, f"{db_id}_{index}.db"))
        connection_string = f"sqlite:///{db_path}"

        result = disprove(
            gold_sql,
            pred_sql,
            ddls,
            connection_string,
            dialect="sqlite",
            atom_null=1,
            timeout=args.timeout,
        )

        entry = {
            "index": index,
            "db_id": db_id,
            "gold_sql": gold_sql,
            "pred_sql": pred_sql,
            "result": result.to_dict(),
        }
        results.append(entry)

    # create unique filenames to avoid overwriting previous runs
    from datetime import datetime
    import uuid

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    uid = uuid.uuid4().hex[:8]
    results_fname = f"sqlite_test_results_{ts}_{uid}.json"
    metrics_fname = f"sqlite_test_metrics_{ts}_{uid}.json"

    out_fp = os.path.join(args.output_dir, results_fname)
    with open(out_fp, "w") as f:
        json.dump(results, f, indent=2)

    print(f"Wrote results for {len(results)} examples to {out_fp}")
    # also run analysis and write metrics (unique filename)
    analyze_results(results, args.output_dir, metrics_file=metrics_fname)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--schema_fp",
        type=str,
        default="data/sqlite/schema.json",
        help="Path to the JSON file containing database schemas.",
    )
    parser.add_argument(
        "--gold_fp",
        type=str,
        default="data/sqlite/dev.json",
        help="Path to the JSON file containing gold SQL queries.",
    )
    parser.add_argument(
        "--preds_fp",
        type=str,
        default="data/sqlite/dail.txt",
        help="Path to the text file containing predicted SQL queries (one per line).",
    )
    parser.add_argument("--output_dir", type=str, default="results", help="Directory to write results to")
    parser.add_argument("--timeout", type=int, default=360, help="Execution timeout per query")

    args = parser.parse_args()
    main(args)


def analyze_results(results, output_dir: str, metrics_file: str = "sqlite_test_metrics.json"):
    """Compute simple metrics from the produced results and write them to a file.

    Metrics produced:
      - total_pairs: total number of query pairs
      - verdict_counts: counts per Verdict string (e.g., 'neq', 'eq')
      - verdict_ratio: same as counts but normalized by total
    """
    total = len(results)
    verdict_counts = {}
    for entry in results:
        res = entry.get("result") or {}
        # serialized DisproveResult has a 'verdict' field with string value
        v = None
        if isinstance(res, dict):
            v = res.get("verdict")
        if v is None:
            v = "unknown"
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    verdict_ratio = {k: (v / total if total > 0 else 0.0) for k, v in verdict_counts.items()}

    metrics = {
        "total_pairs": total,
        "verdict_counts": verdict_counts,
        "verdict_ratio": verdict_ratio,
    }

    os.makedirs(output_dir, exist_ok=True)
    metrics_fp = os.path.join(output_dir, metrics_file)
    with open(metrics_fp, "w") as f:
        json.dump(metrics, f, indent=2)

    # Print a concise summary
    print("--- Analysis Summary ---")
    print(f"Total pairs: {total}")
    for k, cnt in sorted(verdict_counts.items(), key=lambda x: x[0]):
        print(f"{k}: {cnt} ({verdict_ratio[k]:.2%})")
    print(f"Wrote metrics to {metrics_fp}")
        
        

 