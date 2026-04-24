from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from sqlglot import exp


SUPPORTED_ANONYMOUS_FUNCTIONS = {
    "ABS",
    "INSTR",
    "LENGTH",
    "STRFTIME",
    "SUBSTR",
    "SUBSTRING",
}


SUPPORTED_AGGREGATES = (
    exp.Count,
    exp.Sum,
    exp.Avg,
    exp.Min,
    exp.Max,
)


@dataclass(frozen=True)
class GenerationCapability:
    can_use_smt: bool
    reasons: List[str] = field(default_factory=list)


def analyze_smt_generation_support(expression: exp.Expression) -> GenerationCapability:
    reasons: List[str] = []

    for node in expression.walk():
        if isinstance(node, exp.Anonymous):
            name = (node.name or "").upper()
            if name not in SUPPORTED_ANONYMOUS_FUNCTIONS:
                reasons.append(f"unsupported function {name or '<anonymous>'}")
        elif isinstance(node, exp.AggFunc) and not isinstance(node, SUPPORTED_AGGREGATES):
            reasons.append(f"unsupported aggregate {type(node).__name__}")
        elif isinstance(node, exp.Window):
            reasons.append("window functions are not modeled by the SMT generator")
        elif isinstance(node, exp.Unnest):
            reasons.append("unnest is not modeled by the SMT generator")
        elif isinstance(node, exp.Lateral):
            reasons.append("lateral joins are not modeled by the SMT generator")

    deduped: List[str] = []
    seen = set()
    for reason in reasons:
        if reason in seen:
            continue
        seen.add(reason)
        deduped.append(reason)
    return GenerationCapability(can_use_smt=not deduped, reasons=deduped)
