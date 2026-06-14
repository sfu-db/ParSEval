"""Core types for the symbolic branch-coverage engine.

This module defines the vocabulary shared across the evaluator, constraint
generator, infeasibility detector, and engine. Every type is a plain
dataclass — no behavior beyond property accessors — so the module stays
dependency-free and testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from sqlglot import exp

from parseval.constants import PlausibleBit
from parseval.identity import ColumnId, RelationId


# =============================================================================
# Branch types
# =============================================================================


BranchType = PlausibleBit


# =============================================================================
# Observations
# =============================================================================


@dataclass(frozen=True)
class AtomObservation:
    """One concrete evaluation of an atom under specific row values."""

    atom_id: int  # index into BranchNode.atoms
    outcome: BranchType
    row_ids: Tuple[Any, ...] = ()
    concrete_values: Tuple[Tuple[ColumnId, Any], ...] = ()


@dataclass(frozen=True)
class JoinFact:
    """Planner-derived join requirements for one join edge."""

    source_relation: RelationId
    target_relation: RelationId
    equalities: Tuple[Tuple[ColumnId, ColumnId], ...] = ()
    predicate: Optional[exp.Expression] = None
    side: str = ""


@dataclass(frozen=True, eq=False)
class PathPredicate:
    """A predicate required for a row to survive along a branch path."""

    node: "BranchNode"
    expression: exp.Expression
    outcome: BranchType


@dataclass(frozen=True, eq=False)
class SubqueryPath:
    """Nested path for a subquery participating in an outer predicate."""

    node: "BranchNode"
    inner_root: Any
    outer_columns: Tuple[ColumnId, ...] = ()
    inner_columns: Tuple[ColumnId, ...] = ()
    predicate: Optional[exp.Expression] = None


@dataclass(frozen=True, eq=False)
class BranchPath:
    """Complete constraints needed for one target to survive toward the query root."""

    target: "CoverageTarget"
    predicates: Tuple[PathPredicate, ...] = ()
    join_facts: Tuple[JoinFact, ...] = ()
    subqueries: Tuple[SubqueryPath, ...] = ()
    relations: Tuple[RelationId, ...] = ()


@dataclass(frozen=True)
class PathObservationKey:
    """Stable dedupe key for one row's outcome at one branch atom."""

    node_key: Tuple[str, str]
    atom_id: int
    row_ids: Tuple[Any, ...]


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
    tables: Tuple[RelationId, ...] = ()
    infeasible: Set[Tuple[int, BranchType]] = field(default_factory=set)

    # Plan hierarchy
    step: Optional[Any] = None  # direct reference to plan Step object
    parent: Optional[BranchNode] = None
    children: List[BranchNode] = field(default_factory=list)

    # Cached upstream constraints (computed once at creation)
    path_predicates: Tuple[exp.Expression, ...] = ()
    join_equalities: Tuple[Tuple[ColumnId, ColumnId], ...] = ()
    join_facts: Tuple[JoinFact, ...] = ()

    # Dict-based observation storage: atom_id -> {row_ids -> outcome}
    observations: Dict[int, Dict[Tuple[Any, ...], BranchType]] = field(
        default_factory=dict
    )

    @property
    def predicate_sql(self) -> str:
        return self.predicate.sql()

    def atom_sql(self, atom_id: int) -> str:
        return self.atoms[atom_id].sql()

    def record(self, observation: AtomObservation) -> None:
        """Insert an observation, deduplicating by (atom_id, row_ids)."""
        atom_bucket = self.observations.setdefault(observation.atom_id, {})
        key = observation.row_ids
        if not key:
            # Empty row_ids (aggregate-level): use a synthetic key to avoid
            # overwriting when multiple outcomes are recorded.
            key = (f"_auto_{len(atom_bucket)}",)
        atom_bucket[key] = observation.outcome

    def observed_outcomes(self, atom_id: int) -> Set[BranchType]:
        """Which outcomes have been seen for this atom."""
        bucket = self.observations.get(atom_id)
        if bucket is None:
            return set()
        return set(bucket.values())

    def observation_count(self, atom_id: int, outcome: BranchType) -> int:
        bucket = self.observations.get(atom_id)
        if bucket is None:
            return 0
        return sum(1 for v in bucket.values() if v == outcome)

    def is_infeasible(self, atom_id: int, outcome: BranchType) -> bool:
        return (atom_id, outcome) in self.infeasible

    def mark_infeasible(self, atom_id: int, outcome: BranchType) -> None:
        self.infeasible.add((atom_id, outcome))

    def row_outcomes(self, row_ids: Tuple[Any, ...]) -> Dict[int, BranchType]:
        """Outcomes for a specific row across all atoms."""
        result: Dict[int, BranchType] = {}
        for atom_id, bucket in self.observations.items():
            outcome = bucket.get(row_ids)
            if outcome is not None:
                result[atom_id] = outcome
        return result

    def all_row_ids(self) -> Set[Tuple[Any, ...]]:
        """All distinct row_ids observed at this node."""
        ids: Set[Tuple[Any, ...]] = set()
        for bucket in self.observations.values():
            ids.update(bucket.keys())
        return ids


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
    atom_dup: int = 1

    def threshold_for(self, branch_type: BranchType) -> int:
        thresholds = {
            BranchType.ATOM_TRUE: self.atom_true,
            BranchType.ATOM_FALSE: self.atom_false,
            BranchType.ATOM_NULL: self.atom_null,
            BranchType.JOIN_MATCH: self.join_match,
            BranchType.JOIN_NO_MATCH: self.join_no_match,
            BranchType.JOIN_NULL: self.join_null,
            BranchType.HAVING_PASS: self.having_pass,
            BranchType.HAVING_FAIL: self.having_fail,
            BranchType.HAVING_NULL: self.having_null,
            BranchType.CASE_ARM_TAKEN: self.case_arm_taken,
            BranchType.CASE_ARM_SKIPPED: self.case_arm_skipped,
            BranchType.EXISTS_TRUE: self.exists_true,
            BranchType.EXISTS_FALSE: self.exists_false,
            BranchType.IN_MATCH: self.in_match,
            BranchType.IN_NO_MATCH: self.in_no_match,
            BranchType.GROUP_SINGLE: self.group_single,
            BranchType.GROUP_MULTI: self.group_multi,
            BranchType.DISTINCT_UNIQUE: self.distinct_unique,
            BranchType.DISTINCT_DUPLICATE: self.distinct_duplicate,
        }
        return thresholds.get(branch_type, 0)


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
    step_map: Dict[str, BranchNode] = field(default_factory=dict)
    node_map: Dict[Tuple[str, str], BranchNode] = field(default_factory=dict)
    row_index: Dict[Tuple[Any, ...], Set[Tuple[str, str]]] = field(default_factory=dict)
    observation_keys: Set[PathObservationKey] = field(default_factory=set)
    _uncovered_cache: Optional[List[CoverageTarget]] = field(
        default=None, repr=False
    )
    _cache_dirty: bool = field(default=True, repr=False)

    def get_or_create_node(
        self,
        step_id: str,
        step_type: str,
        site: str,
        predicate: exp.Expression,
        atoms: Tuple[exp.Expression, ...],
        tables: Tuple[RelationId, ...] = (),
        step_obj: Any = None,
        parent: Optional[BranchNode] = None,
        path_predicates: Tuple[exp.Expression, ...] = (),
        join_equalities: Tuple[Tuple[ColumnId, ColumnId], ...] = (),
        join_facts: Tuple[JoinFact, ...] = (),
    ) -> BranchNode:
        """Find an existing node or create a new one."""
        node_key = (step_id, predicate.sql())
        existing = self.node_map.get(node_key)
        if existing is not None:
            return existing

        node = BranchNode(
            step_id=step_id,
            step_type=step_type,
            site=site,
            predicate=predicate,
            atoms=atoms,
            tables=tables,
            step=step_obj,
            parent=parent,
            path_predicates=path_predicates,
            join_equalities=join_equalities,
            join_facts=join_facts,
        )
        if parent is not None:
            parent.children.append(node)

        self.nodes.append(node)
        self.node_map[node_key] = node
        self.step_map.setdefault(step_id, node)
        return node

    def record_observation(self, node: BranchNode, observation: AtomObservation) -> None:
        node_key = (node.step_id, node.predicate_sql)
        key = PathObservationKey(
            node_key=node_key,
            atom_id=observation.atom_id,
            row_ids=observation.row_ids,
        )
        if observation.row_ids and key in self.observation_keys:
            return
        if observation.row_ids:
            self.observation_keys.add(key)
        node.record(observation)
        if observation.row_ids:
            entry = self.row_index.setdefault(observation.row_ids, set())
            entry.add(node_key)
        self._cache_dirty = True

    @property
    def uncovered_targets(self) -> List[CoverageTarget]:
        """All atom-outcome pairs that haven't met their threshold."""
        if not self._cache_dirty and self._uncovered_cache is not None:
            return self._uncovered_cache

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

        self._uncovered_cache = targets
        self._cache_dirty = False
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
        self._cache_dirty = True

    # -- Row-level trace queries --

    def rows_at_node(self, node: BranchNode) -> Set[Tuple[Any, ...]]:
        """All row_ids observed at a node."""
        return node.all_row_ids()

    def nodes_for_row(self, row_ids: Tuple[Any, ...]) -> List[BranchNode]:
        """All nodes that observed a given row."""
        node_keys = self.row_index.get(row_ids, set())
        return [self.node_map[key] for key in node_keys if key in self.node_map]

    def row_path(self, row_ids: Tuple[Any, ...]) -> List[Tuple[BranchNode, Dict[int, BranchType]]]:
        """Full path a row took through the decision tree."""
        nodes = self.nodes_for_row(row_ids)
        return [(node, node.row_outcomes(row_ids)) for node in nodes]


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
    "BranchPath",
    "BranchTree",
    "BranchType",
    "CoverageTarget",
    "CoverageThresholds",
    "GenerationResult",
    "JoinFact",
    "PathObservationKey",
    "PathPredicate",
    "SubqueryPath",
]
