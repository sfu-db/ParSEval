from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..constants import PBit, PlausibleType


@dataclass
class NodeCoverage:
    """Coverage information for a single constraint node.

    - `pattern`: the constraint pattern (tuple of bits from root to this node)
    - `operator_type`: the SQL operator type that created this constraint (e.g., 'Filter')
    - `condition`: a short string representation of the SQL condition
    - `per_bit`: mapping from PBit -> dict with status/hits/symbolic_count
    - `metadata`: any metadata attached to the constraint
    """

    pattern: Tuple[Any, ...]
    operator_type: Optional[str]
    condition: str
    per_bit: Dict[PBit, Dict[str, Any]]
    metadata: Dict[str, Any]


class CoverageTracker:
    """Compute and maintain coverage metrics for a UExpr constraint tree.

    Usage:
        tracker = CoverageTracker()
        snapshot = tracker.snapshot(root_constraint)
        summary = tracker.summary(snapshot)
    """

    def snapshot(self, root_constraint) -> List[NodeCoverage]:
        """Walk the UExpr tree and collect coverage info per constraint node.

        Args:
            root_constraint: The `Constraint` instance at the tree root.

        Returns:
            A list of `NodeCoverage` objects for each constraint node encountered.
        """
        out: List[NodeCoverage] = []

        stack = [root_constraint]
        while stack:
            node = stack.pop()
            # For root, operator may be None
            op_type = None
            try:
                op_type = node.operator.operator_type if node.operator else None
            except Exception:
                op_type = None

            condition = (
                str(node.sql_condition)
                if getattr(node, "sql_condition", None) is not None
                else "ROOT"
            )

            per_bit: Dict[PBit, Dict[str, Any]] = {}
            try:
                bits = node.plausible_bits
            except Exception:
                bits = []

            for bit in bits:
                child = node.children.get(bit)
                status = None
                hits = 0
                symbolic_count = 0
                sample = None
                if child is None:
                    status = None
                else:
                    # If child is a PlausibleBranch it has plausible_type
                    try:
                        status = getattr(child, "plausible_type", None)
                    except Exception:
                        status = None
                    # hit count: how many symbolic expressions recorded for this bit
                    try:
                        hits = len(node.symbolic_exprs.get(bit, []))
                    except Exception:
                        hits = 0
                    try:
                        symbolic_count = len(node.symbolic_exprs.get(bit, []))
                        if symbolic_count:
                            sample = node.symbolic_exprs.get(bit)[0]
                    except Exception:
                        symbolic_count = 0

                per_bit[bit] = {
                    "status": (
                        status.name
                        if isinstance(status, PlausibleType)
                        else str(status)
                    ),
                    "hits": hits,
                    "symbolic_count": symbolic_count,
                    "sample": str(sample) if sample is not None else None,
                }

            nc = NodeCoverage(
                pattern=node.pattern(),
                operator_type=op_type,
                condition=condition,
                per_bit=per_bit,
                metadata=getattr(node, "metadata", {}) or {},
            )
            out.append(nc)

            # push children constraints for traversal
            for child in node.children.values():
                if hasattr(child, "children"):
                    stack.append(child)

        return out

    def summary(self, snapshot: List[NodeCoverage]) -> Dict[str, Any]:
        """Compute aggregate coverage metrics from a snapshot.

        Returns a dict with totals and percentages.
        """
        total_branches = 0
        covered = 0
        infeasible = 0
        unexplored = 0
        pending = 0

        for nc in snapshot:
            for bit, info in nc.per_bit.items():
                total_branches += 1
                status = info.get("status")
                if status == PlausibleType.COVERED.name:
                    covered += 1
                elif status == PlausibleType.INFEASIBLE.name:
                    infeasible += 1
                elif status == PlausibleType.UNEXPLORED.name:
                    unexplored += 1
                elif status == PlausibleType.PENDING.name:
                    pending += 1

        pct = (covered / total_branches * 100) if total_branches else 0.0
        return {
            "total_branches": total_branches,
            "covered": covered,
            "infeasible": infeasible,
            "unexplored": unexplored,
            "pending": pending,
            "coverage_percent": pct,
        }

    def to_dict(self, snapshot: List[NodeCoverage]) -> List[Dict[str, Any]]:
        return [asdict(nc) for nc in snapshot]


__all__ = ["CoverageTracker", "NodeCoverage"]
