"""SQLite experiment runner — disprove equivalence on Bird dataset pairs."""

import json
import os
import argparse

import datetime
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


def _has_likely_syntax_error(sql: str) -> bool:
    """Quick heuristic to detect obviously broken SQL."""
    # Unbalanced single quotes (handles escaped '' by subtracting pairs)
    s = sql.replace("''", "")  # Remove escaped quotes
    if s.count("'") % 2 != 0:
        return True
    # Unbalanced parentheses
    if sql.count("(") != sql.count(")"):
        return True
    # Empty SQL
    if not sql.strip():
        return True
    return False


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

        # Skip pairs where either query likely has syntax issues
        # if _has_likely_syntax_error(gold_sql) or _has_likely_syntax_error(pred_sql):
        #     entry = {
        #         "index": index,
        #         "db_id": db_id,
        #         "gold_sql": gold_sql,
        #         "pred_sql": pred_sql,
        #         "result": {"verdict": "syntax_error", "error_msg": "Skipped: likely syntax error"},
        #     }
        #     results.append(entry)
        #     continue

        try:
            result = disprove(
                gold_sql,
                pred_sql,
                ddls,
                connection_string,
                dialect="sqlite",
                max_iterations=5,
                semantics=Semantics.BAG,
                atom_null=1,
                atom_dup=3
            )
            result_dict = result.to_dict()
            # Reclassify runtime errors as syntax errors — they indicate
            # invalid SQL for the target dialect (e.g., MySQL YEAR() in SQLite).
            if result_dict.get("verdict") == "runtime_error":
                result_dict["verdict"] = "syntax_error"
            entry = {
                "index": index,
                "db_id": db_id,
                "gold_sql": gold_sql,
                "pred_sql": pred_sql,
                "result": result_dict,
            }
        except Exception as e:
            entry = {
                "index": index,
                "db_id": db_id,
                "gold_sql": gold_sql,
                "pred_sql": pred_sql,
                "result": {"verdict": "syntax_error", "error_msg": str(e)[:200]},
            }

        results.append(entry)

        # Clean up temp DB
        try:
            os.remove(db_path)
        except OSError:
            pass

    # Write results
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
