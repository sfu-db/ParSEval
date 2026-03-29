from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

from sqlglot import expressions as sqlglot_exp
from ..constants import PBit, PlausibleType, VALID_PATH_BITS, BranchType
from parseval.plan.rex import Variable
from parseval.helper import group_by_concrete

if TYPE_CHECKING:
    from parseval.uexpr.uexprs import PlausibleBranch


@dataclass
class LeafCoverage:
    bit: PBit
    hits: int
    threshold: int
    covered: bool
    infeasible: bool = False
    forced_branch: BranchType | None = None
    decision: str = "pending"
    positive_branch: bool = False


class CoverageCalculator:
    def __init__(self, **kwargs):
        self.duplicate_threshold = kwargs.get("duplicate_threshold", 1)
        self.null_threshold = kwargs.get("null_threshold", 1)
        self.group_count_threshold = kwargs.get("group_count_threshold", 3)
        self.group_size_threshold = kwargs.get("group_size_threshold", 2)
        self.positive_threshold = kwargs.get("positive_threshold", 1)
        self.negative_threshold = kwargs.get("negative_threshold", 1)

    def evaluate_leaf(self, leaf: PlausibleBranch) -> LeafCoverage:
        bit = leaf.bit()
        node = leaf.parent
        threshold = self._threshold_for(leaf)
        hits = self._global_hits(node, bit)
        infeasible = self._is_structurally_infeasible(node, bit)
        positive_branch = self._is_positive_branch(bit)
        covered = hits >= threshold
        decision = "pending"
        if infeasible:
            decision = "infeasible"
        elif covered:
            decision = "covered" if positive_branch else "skip"
        return LeafCoverage(
            bit=bit,
            hits=hits,
            threshold=threshold,
            covered=hits >= threshold,
            infeasible=infeasible,
            forced_branch=self._forced_branch(bit, infeasible),
            decision=decision,
            positive_branch=positive_branch,
        )

    def to_dict(self, snapshot: List[NodeCoverage]) -> List[Dict[str, Any]]:
        return [asdict(nc) for nc in snapshot]

    def _threshold_for(self, leaf) -> int:
        return self._threshold_for_bit(leaf.bit(), leaf)

    def _threshold_for_bit(self, bit: PBit, leaf=None) -> int:
        if bit in {
            PBit.TRUE,
            PBit.JOIN_TRUE,
            PBit.HAVING_TRUE,
            PBit.PROJECT,
            PBit.AGGREGATE_SIZE,
        }:
            return self.positive_threshold
        if bit in {PBit.FALSE, PBit.JOIN_LEFT, PBit.JOIN_RIGHT, PBit.HAVING_FALSE}:
            return self.negative_threshold
        if bit == PBit.NULL:
            return self.null_threshold
        if bit == PBit.DUPLICATE:
            return self.duplicate_threshold
        if bit == PBit.GROUP_COUNT:
            return self.group_count_threshold
        if bit == PBit.GROUP_SIZE:
            return self.group_size_threshold
        if bit in {PBit.GROUP_NULL, PBit.GROUP_DUPLICATE, PBit.MAX, PBit.MIN}:
            return 1
        return 1

    def _is_positive_branch(self, bit: PBit) -> bool:
        return bit in {
            PBit.TRUE,
            PBit.JOIN_TRUE,
            PBit.HAVING_TRUE,
            PBit.PROJECT,
            PBit.AGGREGATE_SIZE,
        }

    def _node_key(self, node, bit: PBit) -> Tuple[Any, ...]:
        condition = self._condition_key(getattr(node, "sql_condition", None))
        return (node.scope_id, node.step_type, node.step_name, condition, bit)

    def _condition_key(self, condition: Any) -> str:
        if condition is None:
            return "ROOT"
        alias = getattr(condition, "alias_or_name", None)
        if alias:
            return str(alias)
        try:
            return condition.sql()
        except Exception:
            try:
                return repr(condition)
            except Exception:
                return type(condition).__name__

    def _iter_constraints(self, root) -> Iterable[Any]:
        stack = [root]
        while stack:
            node = stack.pop()
            if hasattr(node, "children"):
                yield node
                for child in node.children.values():
                    if hasattr(child, "children"):
                        stack.append(child)

    def _global_hits(self, target_node, bit: PBit) -> int:
        matching = [
            node
            for node in self._iter_constraints(target_node.tree.root)
            if self._node_key(node, bit) == self._node_key(target_node, bit)
        ]
        if bit in {
            PBit.TRUE,
            PBit.FALSE,
            PBit.JOIN_TRUE,
            PBit.JOIN_LEFT,
            PBit.JOIN_RIGHT,
            PBit.HAVING_TRUE,
            PBit.HAVING_FALSE,
            PBit.PROJECT,
        }:
            rowids = set()
            for node in matching:
                rowids.update(self._normalize_rowids(node.rowid_index.get(bit, [])))
            return len(rowids)

        if bit == PBit.NULL:
            return self._null_hits(target_node, bit)
        if bit == PBit.DUPLICATE:
            return max(
                (self._duplicate_hits(node, bit) for node in matching), default=0
            )
        if bit == PBit.GROUP_COUNT:
            return sum(self._group_count_hits(node) for node in matching)
        if bit == PBit.GROUP_SIZE:
            return sum(self._group_size_hits(node, bit) for node in matching)
        if bit == PBit.GROUP_NULL:
            return sum(self._group_null_hits(node) for node in matching)
        if bit == PBit.GROUP_DUPLICATE:
            return sum(self._group_duplicate_hits(node) for node in matching)

        return sum(
            node.hits.get(bit, len(node.coverage.get(bit, []))) for node in matching
        )

    def _rowid_hits(self, node, bit: PBit) -> int:
        return len(set(self._normalize_rowids(node.rowid_index.get(bit, []))))

    def _normalize_rowids(self, items) -> List[Tuple[Any, ...]]:
        normalized = []
        for rowids in items:
            if isinstance(rowids, tuple):
                normalized.append(rowids)
            elif isinstance(rowids, list):
                normalized.append(tuple(rowids))
            else:
                normalized.append((rowids,))
        return normalized

    def _null_hits(self, node, bit: PBit) -> int:
        hit = 0
        positive_bit = self._positive_bit(node, bit)
        for smt_expr in node.coverage.get(positive_bit, []):
            for var in smt_expr.find_all(Variable):
                if var.concrete is None:
                    hit += 1
                    break
        return hit

    def _duplicate_hits(self, node, bit: PBit) -> int:
        variables = []
        positive_bit = self._positive_bit(node, bit)
        for smt_expr in node.coverage.get(positive_bit, []):
            variables.extend(smt_expr.find_all(Variable))
        groups = group_by_concrete(variables)
        return max((len(items) for items in groups.values()), default=0)

    def _group_count_hits(self, node) -> int:
        hit = 0
        for g in node.coverage.get(PBit.GROUP_SIZE, []):
            if any(v.concrete is None for v in g.group_key):
                continue
            hit += 1
        return hit

    def _group_size_hits(self, node, bit: PBit) -> int:
        return sum(
            1
            for g in node.coverage.get(bit, [])
            if len(g.group_values) >= self.group_size_threshold
        )

    def _group_null_hits(self, node) -> int:
        columnrefs = list(node.sql_condition.find_all(sqlglot_exp.Column))
        hit = 0
        for g in node.coverage.get(PBit.AGGREGATE_SIZE, []):
            if g.group_key and any(key.concrete is None for key in g.group_key):
                continue
            for row in g.group_values:
                if any(
                    row[columnref.name].concrete is None for columnref in columnrefs
                ):
                    hit += 1
                    break
        return hit

    def _group_duplicate_hits(self, node) -> int:
        columnrefs = list(node.sql_condition.find_all(sqlglot_exp.Column))
        hit = 0
        for g in node.coverage.get(PBit.AGGREGATE_SIZE, []):
            if g.group_key and any(key.concrete is None for key in g.group_key):
                continue
            values = {}
            for row in g.group_values:
                for columnref in columnrefs:
                    v = row[columnref.name]
                    if v.concrete is not None:
                        values.setdefault(columnref.name, []).append(v.concrete)
            if any(len(items) != len(set(items)) for items in values.values()):
                hit += 1
        return hit

    def _positive_bit(self, node, bit: PBit) -> PBit:
        if bit in VALID_PATH_BITS:
            return bit
        for candidate in node.coverage:
            if candidate in VALID_PATH_BITS:
                return candidate
        return bit

    def _is_structurally_infeasible(self, node, bit: PBit) -> bool:
        columnrefs = list(node.sql_condition.find_all(sqlglot_exp.Column))
        if bit == PBit.NULL and columnrefs:
            return all(
                columnref.args.get("nullable") is False for columnref in columnrefs
            )
        if bit == PBit.DUPLICATE and columnrefs:
            return all(
                columnref.args.get("is_unique", False) for columnref in columnrefs
            )
        return False

    def _forced_branch(self, bit: PBit, infeasible: bool) -> BranchType | None:
        if not infeasible:
            return None
        if bit in {PBit.DUPLICATE, PBit.NULL}:
            return BranchType.NEGATIVE
        return None


CoverageTracker = CoverageCalculator

__all__ = ["CoverageCalculator", "CoverageTracker", "LeafCoverage"]
