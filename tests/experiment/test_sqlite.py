"""SQLite experiment runner — disprove equivalence on Bird dataset pairs."""

import json
import os
import argparse
from datetime import datetime

from parseval.main import disprove
from parseval.states import Semantics

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


def main(args):
    schemas = load_schema(args.schema_fp)
    gold = load_gold(args.gold_fp)
    preds = load_preds(args.preds_fp)

    results = []
    os.makedirs(args.output_dir, exist_ok=True)
    tmp_dir = os.path.join("tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    for index in range(len(gold)):
        if index % 100 == 0:
            print(f"Processing pair {index}/{len(gold)}...")
        row = gold[index]
        gold_sql = row.get("SQL")
        pred_sql = preds[index] if index < len(preds) else ""
        db_id = row.get("db_id")
        ddls = ";".join(schemas.get(db_id, []))

        db_path = os.path.abspath(os.path.join(tmp_dir, f"{db_id}_{index}.db"))
        connection_string = f"sqlite:///{db_path}"

        try:
            result = disprove(
                gold_sql,
                pred_sql,
                ddls,
                connection_string,
                dialect="sqlite",
                max_iterations=5,
                semantics=Semantics.BAG,
            )
            entry = {
                "index": index,
                "db_id": db_id,
                "gold_sql": gold_sql,
                "pred_sql": pred_sql,
                "result": result.to_dict(),
            }
        except Exception as e:
            entry = {
                "index": index,
                "db_id": db_id,
                "gold_sql": gold_sql,
                "pred_sql": pred_sql,
                "result": {"verdict": "error", "error_msg": str(e)[:200]},
            }

        results.append(entry)

        # Clean up temp DB
        try:
            os.remove(db_path)
        except OSError:
            pass

    # Write results
    ts = datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results_fname = f"sqlite_results_{ts}.json"
    metrics_fname = f"sqlite_metrics_{ts}.json"

    out_fp = os.path.join(args.output_dir, results_fname)
    with open(out_fp, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Wrote {len(results)} results to {out_fp}")

    # Compute and write metrics
    metrics = compute_metrics(results)
    metrics_fp = os.path.join(args.output_dir, metrics_fname)
    with open(metrics_fp, "w") as f:
        json.dump(metrics, f, indent=2)

    print_summary(metrics)


def compute_metrics(results):
    total = len(results)
    verdict_counts = {}
    for entry in results:
        v = entry.get("result", {}).get("verdict", "unknown")
        verdict_counts[v] = verdict_counts.get(v, 0) + 1

    return {
        "total_pairs": total,
        "verdict_counts": verdict_counts,
        "verdict_ratio": {k: round(v / total, 4) for k, v in verdict_counts.items()},
    }


def print_summary(metrics):
    print("\n=== Experiment Summary ===")
    print(f"Total pairs: {metrics['total_pairs']}")
    for k, cnt in sorted(metrics["verdict_counts"].items()):
        ratio = metrics["verdict_ratio"][k]
        print(f"  {k}: {cnt} ({ratio:.1%})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SQLite equivalence experiment")
    parser.add_argument("--schema_fp", default="data/sqlite/schema.json")
    parser.add_argument("--gold_fp", default="data/sqlite/dev.json")
    parser.add_argument("--preds_fp", default="data/sqlite/dail.txt")
    parser.add_argument("--output_dir", default="results")
    args = parser.parse_args()
    main(args)
