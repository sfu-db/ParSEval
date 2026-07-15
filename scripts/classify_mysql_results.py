from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import glob
import json
import re
from pathlib import Path
from typing import Any


DEFAULT_RESULTS_GLOB = "results/mysql_results_*.json"
REPRESENTATIVE_LIMIT = 20


def latest_results_path(pattern: str = DEFAULT_RESULTS_GLOB) -> Path:
    matches = sorted(glob.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"no_mysql_results:{pattern}")
    return Path(matches[-1])


def sql_shape_tags(*sqls: str) -> tuple[str, ...]:
    text = "\n".join(sqls).lower()
    tags: list[str] = []
    if re.search(r"\([^)]*,[^)]*\)\s+(?:not\s+)?in\s*\(\s*select", text):
        tags.append("tuple_in_subquery")
    if re.search(r"\bnot\s+in\s*\(\s*select", text):
        tags.append("not_in_subquery")
    if re.search(r"\bexists\s*\(\s*select", text):
        tags.append("exists_subquery")
    if re.search(r"\bnot\s+exists\s*\(\s*select", text):
        tags.append("anti_subquery")
    if re.search(r"\bunion(?:\s+all)?\b", text):
        tags.append("union")
    if re.search(r"\bintersect\b", text):
        tags.append("intersect")
    if re.search(r"\bexcept\b", text):
        tags.append("except")
    if re.search(r"\(\s*select[^)]*\bunion(?:\s+all)?\b", text, re.DOTALL):
        tags.append("set_operation_subquery")
    if "case when" in text:
        tags.append("case_expression")
    if re.search(r"\bsum\s*\(\s*case\b", text):
        tags.append("sum_case")
    if "group by" in text:
        tags.append("group_by")
    if "having" in text:
        tags.append("having")
    if re.search(r"count\s*\(\s*distinct", text) and re.search(
        r"=\s*\(\s*select\s+count\s*\(\s*distinct", text, re.DOTALL
    ):
        tags.append("relational_division_count_distinct")
    if re.search(r"\b(date|datediff|timestampdiff|interval|str_to_date|date_format)\b", text):
        tags.append("temporal_expression")
    if re.search(r"\bover\s*\(", text):
        tags.append("window_function")
    return tuple(tags or ("unclassified",))


def classify_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    verdict_counts: Counter[str] = Counter()
    debug_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    tag_by_verdict: dict[str, Counter[str]] = defaultdict(Counter)
    representatives: dict[str, list[int]] = defaultdict(list)

    for record in records:
        verdict = record.get("verdict") or "unknown"
        debug_category = record.get("debug_category") or "uncategorized"
        tags = sql_shape_tags(record.get("sql1", ""), record.get("sql2", ""))
        verdict_counts[verdict] += 1
        debug_counts[debug_category] += 1
        for tag in tags:
            tag_counts[tag] += 1
            tag_by_verdict[verdict][tag] += 1
            if len(representatives[tag]) < REPRESENTATIVE_LIMIT:
                representatives[tag].append(record.get("index"))

    return {
        "total": len(records),
        "verdict_counts": dict(verdict_counts),
        "debug_category_counts": dict(debug_counts),
        "shape_tag_counts": dict(tag_counts),
        "shape_tags_by_verdict": {
            verdict: dict(counter)
            for verdict, counter in tag_by_verdict.items()
        },
        "representative_indices": {
            tag: [index for index in indices if index is not None]
            for tag, indices in representatives.items()
        },
    }


def load_records(path: str | Path) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return data
    return data.get("results", [])


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify MySQL experiment results")
    parser.add_argument("--input", default=None, help="Path to mysql_results_*.json")
    parser.add_argument("--output", default=None, help="Optional JSON summary path")
    args = parser.parse_args()

    input_path = Path(args.input) if args.input else latest_results_path()
    summary = classify_records(load_records(input_path))
    summary["input"] = str(input_path)
    rendered = json.dumps(summary, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
