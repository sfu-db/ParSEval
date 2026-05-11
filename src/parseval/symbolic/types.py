"""Core types for the symbolic branch-coverage engine.

This module defines the vocabulary shared across the evaluator, constraint
generator, infeasibility detector, and engine. Every type is a plain
dataclass — no behavior beyond property accessors — so the module stays
dependency-free and testable in isolation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from sqlglot import exp


# =============================================================================
# Branch types
# =============================================================================


class BranchType(enum.Enum):
    """Every kind of outcome a decision site can produce."""

    # Atom-level 3VL (the fundamental unit of coverage)
    ATOM_TRUE = "atom_true"
    ATOM_FALSE = "atom_false"
    ATOM_NULL = "atom_null"

    # Filter / WHERE (decision-level)
    FILTER_TRUE = "filter_true"
    FILTER_FALSE = "filter_false"
    FILTER_NULL = "filter_null"

    # JOIN ON
    JOIN_MATCH = "join_match"
    JOIN_NO_MATCH = "join_no_match"
    JOIN_NULL = "join_null"

    # HAVING
    HAVING_PASS = "having_pass"
    HAVING_FAIL = "having_fail"
    HAVING_NULL = "having_null"

    # CASE WHEN arms
    CASE_ARM_TAKEN = "case_arm_taken"
    CASE_ARM_SKIPPED = "case_arm_skipped"

    # Subquery
    EXISTS_TRUE = "exists_true"
    EXISTS_FALSE = "exists_false"
    IN_MATCH = "in_match"
    IN_NO_MATCH = "in_no_match"

    # Grouping cardinality
    GROUP_SINGLE = "group_single"
    GROUP_MULTI = "group_multi"

    # DISTINCT
    DISTINCT_UNIQUE = "distinct_unique"
    DISTINCT_DUPLICATE = "distinct_duplicate"


# =============================================================================
# Observations
# =============================================================================


@dataclass(frozen=True)
class AtomObservation:
    """One concrete evaluation of an atom under specific row values."""

    atom_id: int  # index into BranchNode.atoms
    outcome: BranchType
    row_ids: Tuple[Any, ...] = ()
    concrete_values: Tuple[Tuple[str, Any], ...] = ()


# =============================================================================
# Branch nodes (decision sites in the plan)
# =============================================================================


@dataclass
class BranchNode:
    """One decision site in the branch tree.

    A BranchNode corresponds to a single predicate (or join condition, or
    CASE arm, etc.) at a specific plan step. It tracks which atom-level
    outcomes have been observed so far and which are still missing.

    ``atoms`` holds the live :class:`exp.Expression` objects — never
    re-parsed from text. The constraint generator operates on these
    directly.
    """

    step_id: str
    step_type: str
    site: str  # "filter" / "join_on" / "having" / "case_arm" / "exists" / "in"
    predicate: exp.Expression  # the full predicate (live AST node)
    atoms: Tuple[exp.Expression, ...]  # decomposed atomic predicates (live AST)
    tables: Tuple[str, ...] = ()
    observations: List[AtomObservation] = field(default_factory=list)
    infeasible: Set[Tuple[int, BranchType]] = field(default_factory=set)

    @property
    def predicate_sql(self) -> str:
        return self.predicate.sql()

    def atom_sql(self, atom_id: int) -> str:
        return self.atoms[atom_id].sql()

    def observed_outcomes(self, atom_id: int) -> Set[BranchType]:
        """Which outcomes have been seen for this atom."""
        return {obs.outcome for obs in self.observations if obs.atom_id == atom_id}

    def observation_count(self, atom_id: int, outcome: BranchType) -> int:
        return sum(
            1 for obs in self.observations
            if obs.atom_id == atom_id and obs.outcome == outcome
        )

    def is_infeasible(self, atom_id: int, outcome: BranchType) -> bool:
        return (atom_id, outcome) in self.infeasible

    def mark_infeasible(self, atom_id: int, outcome: BranchType) -> None:
        self.infeasible.add((atom_id, outcome))


# =============================================================================
# Coverage thresholds (user-configurable)
# =============================================================================


@dataclass
class CoverageThresholds:
    """Minimum observation counts per branch type before "covered".

    Set a threshold to 0 to skip that branch type entirely.
    """

    atom_true: int = 1
    atom_false: int = 1
    atom_null: int = 1
    filter_true: int = 1
    filter_false: int = 1
    filter_null: int = 0  # often not targeted by default
    join_match: int = 1
    join_no_match: int = 1
    join_null: int = 0
    having_pass: int = 1
    having_fail: int = 1
    having_null: int = 0
    case_arm_taken: int = 1
    case_arm_skipped: int = 1
    exists_true: int = 1
    exists_false: int = 1
    in_match: int = 1
    in_no_match: int = 1
    group_single: int = 1
    group_multi: int = 1
    distinct_unique: int = 0
    distinct_duplicate: int = 0

    def threshold_for(self, branch_type: BranchType) -> int:
        return getattr(self, branch_type.value, 0)


# =============================================================================
# Branch tree (the full coverage state)
# =============================================================================


@dataclass
class CoverageTarget:
    """One specific gap: an atom at a node that needs a specific outcome."""

    node: BranchNode
    atom_id: int  # index into node.atoms
    target_outcome: BranchType

    @property
    def atom(self) -> exp.Expression:
        return self.node.atoms[self.atom_id]


@dataclass
class BranchTree:
    """Aggregated coverage state for a plan evaluation."""

    nodes: List[BranchNode] = field(default_factory=list)
    thresholds: CoverageThresholds = field(default_factory=CoverageThresholds)

    def get_or_create_node(
        self,
        step_id: str,
        step_type: str,
        site: str,
        predicate: exp.Expression,
        atoms: Tuple[exp.Expression, ...],
        tables: Tuple[str, ...] = (),
    ) -> BranchNode:
        """Find an existing node or create a new one."""
        pred_sql = predicate.sql()
        for node in self.nodes:
            if node.step_id == step_id and node.predicate_sql == pred_sql:
                return node
        node = BranchNode(
            step_id=step_id,
            step_type=step_type,
            site=site,
            predicate=predicate,
            atoms=atoms,
            tables=tables,
        )
        self.nodes.append(node)
        return node

    def record_observation(self, node: BranchNode, observation: AtomObservation) -> None:
        node.observations.append(observation)

    @property
    def uncovered_targets(self) -> List[CoverageTarget]:
        """All atom-outcome pairs that haven't met their threshold."""
        targets: List[CoverageTarget] = []
        for node in self.nodes:
            for atom_id in range(len(node.atoms)):
                for outcome in (BranchType.ATOM_TRUE, BranchType.ATOM_FALSE, BranchType.ATOM_NULL):
                    threshold = self.thresholds.threshold_for(outcome)
                    if threshold <= 0:
                        continue
                    if node.is_infeasible(atom_id, outcome):
                        continue
                    if node.observation_count(atom_id, outcome) >= threshold:
                        continue
                    targets.append(CoverageTarget(node=node, atom_id=atom_id, target_outcome=outcome))
        return targets

    @property
    def total_targets(self) -> int:
        count = 0
        for node in self.nodes:
            for atom_id in range(len(node.atoms)):
                for outcome in (BranchType.ATOM_TRUE, BranchType.ATOM_FALSE, BranchType.ATOM_NULL):
                    threshold = self.thresholds.threshold_for(outcome)
                    if threshold <= 0:
                        continue
                    if node.is_infeasible(atom_id, outcome):
                        continue
                    count += 1
        return count

    @property
    def covered_count(self) -> int:
        return self.total_targets - len(self.uncovered_targets)

    @property
    def coverage_ratio(self) -> float:
        total = self.total_targets
        if total == 0:
            return 1.0
        return self.covered_count / total

    @property
    def fully_covered(self) -> bool:
        return len(self.uncovered_targets) == 0

    def mark_infeasible(self, node: BranchNode, atom_id: int, outcome: BranchType) -> None:
        node.mark_infeasible(atom_id, outcome)


# =============================================================================
# Generation result
# =============================================================================


@dataclass
class GenerationResult:
    """Output of :meth:`SymbolicEngine.generate`."""

    tree: BranchTree
    iterations: int = 0
    rows_generated: int = 0

    @property
    def coverage(self) -> float:
        return self.tree.coverage_ratio

    @property
    def fully_covered(self) -> bool:
        return self.tree.fully_covered

    @property
    def uncovered(self) -> List[CoverageTarget]:
        return self.tree.uncovered_targets


__all__ = [
    "AtomObservation",
    "BranchNode",
    "BranchTree",
    "BranchType",
    "CoverageTarget",
    "CoverageThresholds",
    "GenerationResult",
]
