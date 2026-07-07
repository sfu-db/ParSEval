"""Core types for the symbolic branch-coverage engine.

This module defines the vocabulary shared across the evaluator, constraint
generator, infeasibility detector, and engine. Every type is a plain
dataclass — no behavior beyond property accessors — so the module stays
dependency-free and testable in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Literal, Optional, Set, Tuple

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


@dataclass(frozen=True)
class RowSetObligation:
    """Logical upstream rows required to satisfy one operator target."""

    anchor_step_id: str
    required_rows: int
    generation_rows: int
    row_scopes: Tuple[str, ...]
    relations: Tuple[RelationId, ...] = ()
    join_facts: Tuple[JoinFact, ...] = ()
    path_predicates: Tuple[exp.Expression, ...] = ()
    group_keys: Tuple[ColumnId, ...] = ()
    counted_expression: Optional[exp.Expression] = None
    distinct_expression: Optional[exp.Expression] = None
    duplicate_expressions: Tuple[exp.Expression, ...] = ()
    ordering: Tuple[exp.Expression, ...] = ()
    ordering_mode: str = ""
    join_scope_outcomes: Tuple[Tuple[str, BranchType], ...] = ()


@dataclass(frozen=True, eq=False)
class PathPredicate:
    """A predicate required for a row to survive along a branch path."""

    node: "BranchNode"
    expression: exp.Expression
    outcome: BranchType
    obligation: Optional["CoverageObligation"] = None


@dataclass(frozen=True, eq=False)
class SubqueryPath:
    """Nested path for a subquery participating in an outer predicate."""

    node: "BranchNode"
    inner_root: Any
    outer_columns: Tuple[ColumnId, ...] = ()
    inner_columns: Tuple[ColumnId, ...] = ()
    predicate: Optional[exp.Expression] = None


@dataclass(frozen=True)
class OperatorObligation:
    """An operator-level requirement for a generated witness row."""

    kind: str
    step_id: str
    site: str
    relation: Optional[RelationId] = None
    storage_relation: Optional[RelationId] = None
    columns: Tuple[ColumnId, ...] = ()
    row_scope: Optional[str] = None
    row_count: int = 1
    expression: Optional[exp.Expression] = None
    row_set: Optional[RowSetObligation] = None


@dataclass(frozen=True)
class CoverageObligation:
    """One planner/runtime coverage requirement attached to a BranchNode."""

    metric: str
    atom_id: int
    expression: exp.Expression
    outcomes: Tuple[BranchType, ...]


@dataclass(frozen=True, eq=False)
class BranchPath:
    """Complete constraints needed for one target to survive toward the query root."""

    target: "CoverageTarget"
    predicates: Tuple[PathPredicate, ...] = ()
    join_facts: Tuple[JoinFact, ...] = ()
    subqueries: Tuple[SubqueryPath, ...] = ()
    obligations: Tuple[OperatorObligation, ...] = ()
    coverage_obligations: Tuple[CoverageObligation, ...] = ()
    relations: Tuple[RelationId, ...] = ()


@dataclass(frozen=True)
class PathObservationKey:
    """Stable dedupe key for one row's outcome at one branch atom."""

    node_key: Tuple[str, str, str]
    atom_id: int
    row_ids: Tuple[Any, ...]


@dataclass(frozen=True)
class RowLineage:
    """Evaluator-derived row flow through one plan operator."""

    step_id: str
    site: str
    output_row_ids: Tuple[Any, ...]
    source_row_ids: Tuple[Tuple[Any, ...], ...] = ()
    relations: Tuple[RelationId, ...] = ()


@dataclass(frozen=True)
class OperatorTrace:
    """One operator decision bound to concrete input and output row ids."""

    node_key: Tuple[str, str, str]
    outcome: BranchType
    input_row_ids: Tuple[Tuple[Any, ...], ...] = ()
    output_row_ids: Tuple[Tuple[Any, ...], ...] = ()
    concrete_values: Tuple[Tuple[ColumnId, Any], ...] = ()


@dataclass(frozen=True)
class GroupTrace:
    """Aggregate output row bound to the source rows that formed the group."""

    step_id: str
    output_row_ids: Tuple[Any, ...]
    source_row_ids: Tuple[Tuple[Any, ...], ...]
    group_key: Tuple[Any, ...] = ()
    aggregate_values: Tuple[Tuple[Any, Any], ...] = ()


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
    generated_strategies: Set[Tuple[int, BranchType]] = field(default_factory=set)
    infeasible_atom_outcomes: Set[Tuple[Tuple[int, BranchType], ...]] = field(
        default_factory=set
    )
    discovery: Literal["planned", "runtime"] = "planned"
    origin: Optional[str] = None

    # Cached planner metadata (set during Phase 1 tree construction)
    annotation_metadata: Dict[str, Any] = field(default_factory=dict)
    _predicate_sql: Optional[str] = field(default=None, init=False, repr=False, compare=False)
    _node_key: Optional[Tuple[str, str, str]] = field(default=None, init=False, repr=False, compare=False)

    # Plan hierarchy and runtime parentage
    step: Optional[Any] = None  # direct reference to plan Step object
    parent: Optional[BranchNode] = None
    children: List[BranchNode] = field(default_factory=list)

    # Cached upstream constraints (computed once at creation)
    path_predicates: Tuple[exp.Expression, ...] = ()
    join_equalities: Tuple[Tuple[ColumnId, ColumnId], ...] = ()
    join_facts: Tuple[JoinFact, ...] = ()
    subqueries: Tuple[SubqueryPath, ...] = ()
    obligations: Tuple[OperatorObligation, ...] = ()
    coverage_obligations: Tuple[CoverageObligation, ...] = ()

    # Dict-based observation storage: atom_id -> {row_ids -> outcome}
    observations: Dict[int, Dict[Tuple[Any, ...], BranchType]] = field(
        default_factory=dict
    )

    @property
    def predicate_sql(self) -> str:
        if self._predicate_sql is None:
            self._predicate_sql = self.predicate.sql()
        return self._predicate_sql

    @property
    def node_key(self) -> Tuple[str, str, str]:
        if self._node_key is None:
            self._node_key = (self.step_id, self.site, self.predicate_sql)
        return self._node_key

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

    def is_strategy_generated(self, atom_id: int, outcome: BranchType) -> bool:
        return (atom_id, outcome) in self.generated_strategies

    def mark_strategy_generated(self, atom_id: int, outcome: BranchType) -> None:
        self.generated_strategies.add((atom_id, outcome))

    def is_atom_outcomes_infeasible(
        self,
        atom_outcomes: Tuple[Tuple[int, BranchType], ...],
    ) -> bool:
        return tuple(atom_outcomes) in self.infeasible_atom_outcomes

    def mark_atom_outcomes_infeasible(
        self,
        atom_outcomes: Tuple[Tuple[int, BranchType], ...],
    ) -> None:
        self.infeasible_atom_outcomes.add(tuple(atom_outcomes))

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
    project_null: int = 1
    project_non_null: int = 1
    aggregate_null: int = 1
    aggregate_non_null: int = 1
    aggregate_distinct_null_ignored: int = 1
    aggregate_distinct_duplicate_eliminated: int = 1
    aggregate_distinct_multiple_retained: int = 1
    aggregate_duplicate: int = 1
    atom_dup: int = 1

    def threshold_for(self, branch_type: BranchType) -> int:
        thresholds = {
            BranchType.ATOM_TRUE: self.atom_true,
            BranchType.ATOM_FALSE: self.atom_false,
            BranchType.ATOM_NULL: self.atom_null,
            BranchType.JOIN_MATCH: self.join_match,
            BranchType.JOIN_NO_MATCH: self.join_no_match,
            BranchType.JOIN_LEFT: self.join_no_match,
            BranchType.JOIN_RIGHT: self.join_no_match,
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
            BranchType.PROJECT_NULL: self.project_null,
            BranchType.PROJECT_NON_NULL: self.project_non_null,
            BranchType.AGGREGATE_NULL: self.aggregate_null,
            BranchType.AGGREGATE_NON_NULL: self.aggregate_non_null,
            BranchType.AGG_DISTINCT_NULL_IGNORED: self.aggregate_distinct_null_ignored,
            BranchType.AGG_DISTINCT_DUPLICATE_ELIMINATED: self.aggregate_distinct_duplicate_eliminated,
            BranchType.AGG_DISTINCT_MULTIPLE_RETAINED: self.aggregate_distinct_multiple_retained,
            BranchType.DUPLICATE: self.aggregate_duplicate,
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
    atom_outcomes: Tuple[Tuple[int, BranchType], ...] = ()
    obligation: Optional[CoverageObligation] = None

    @property
    def atom(self) -> exp.Expression:
        if self.obligation is not None:
            return self.obligation.expression
        if self.atom_outcomes:
            return self.node.predicate
        if self.atom_id < 0:
            return self.node.predicate
        return self.node.atoms[self.atom_id]


@dataclass
class BranchTree:
    """Aggregated coverage state for a plan evaluation."""

    nodes: List[BranchNode] = field(default_factory=list)
    thresholds: CoverageThresholds = field(default_factory=CoverageThresholds)
    step_map: Dict[str, BranchNode] = field(default_factory=dict)
    node_map: Dict[Tuple[str, str, str], BranchNode] = field(default_factory=dict)
    row_index: Dict[Tuple[Any, ...], Set[Tuple[str, str, str]]] = field(default_factory=dict)
    observation_keys: Set[PathObservationKey] = field(default_factory=set)
    row_lineage: Dict[Tuple[Any, ...], RowLineage] = field(default_factory=dict)
    operator_traces: List[OperatorTrace] = field(default_factory=list)
    group_lineage: Dict[Tuple[Any, ...], GroupTrace] = field(default_factory=dict)
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
        subqueries: Tuple[SubqueryPath, ...] = (),
        obligations: Tuple[OperatorObligation, ...] = (),
        coverage_obligations: Tuple[CoverageObligation, ...] = (),
        annotation_metadata: Optional[Dict[str, Any]] = None,
        discovery: Literal["planned", "runtime"] = "planned",
        origin: Optional[str] = None,
    ) -> BranchNode:
        """Find an existing node or create a new one."""
        node_key = (step_id, site, predicate.sql())
        existing = self.node_map.get(node_key)
        if existing is not None:
            return existing
        if not coverage_obligations:
            from .branch_tree import coverage_obligations_for_site

            coverage_obligations = coverage_obligations_for_site(site, atoms)

        node = BranchNode(
            step_id=step_id,
            step_type=step_type,
            site=site,
            predicate=predicate,
            atoms=atoms,
            tables=tables,
            discovery=discovery,
            origin=origin,
            annotation_metadata=annotation_metadata or {},
            step=step_obj,
            parent=parent,
            path_predicates=path_predicates,
            join_equalities=join_equalities,
            join_facts=join_facts,
            subqueries=subqueries,
            obligations=obligations,
            coverage_obligations=coverage_obligations,
        )
        if parent is not None:
            parent.children.append(node)
        node._predicate_sql = node_key[2]
        node._node_key = node_key

        self.nodes.append(node)
        self.node_map[node_key] = node
        self.step_map.setdefault(step_id, node)
        return node

    def record_observation(self, node: BranchNode, observation: AtomObservation) -> None:
        node_key = node.node_key
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

    def record_row_lineage(
        self,
        *,
        step_id: str,
        site: str,
        output_row_ids: Tuple[Any, ...],
        source_row_ids: Tuple[Tuple[Any, ...], ...] = (),
        relations: Tuple[RelationId, ...] = (),
    ) -> None:
        self.row_lineage[output_row_ids] = RowLineage(
            step_id=step_id,
            site=site,
            output_row_ids=output_row_ids,
            source_row_ids=source_row_ids,
            relations=relations,
        )

    def record_operator_trace(
        self,
        node: BranchNode,
        *,
        outcome: BranchType,
        input_row_ids: Tuple[Tuple[Any, ...], ...] = (),
        output_row_ids: Tuple[Tuple[Any, ...], ...] = (),
        concrete_values: Tuple[Tuple[ColumnId, Any], ...] = (),
    ) -> None:
        self.operator_traces.append(
            OperatorTrace(
                node_key=node.node_key,
                outcome=outcome,
                input_row_ids=input_row_ids,
                output_row_ids=output_row_ids,
                concrete_values=concrete_values,
            )
        )

    def record_group_lineage(
        self,
        *,
        step_id: str,
        output_row_ids: Tuple[Any, ...],
        source_row_ids: Tuple[Tuple[Any, ...], ...],
        group_key: Tuple[Any, ...] = (),
        aggregate_values: Tuple[Tuple[Any, Any], ...] = (),
    ) -> None:
        self.group_lineage[output_row_ids] = GroupTrace(
            step_id=step_id,
            output_row_ids=output_row_ids,
            source_row_ids=source_row_ids,
            group_key=group_key,
            aggregate_values=aggregate_values,
        )

    def traces_for_node(self, node: BranchNode) -> List[OperatorTrace]:
        node_key = node.node_key
        return [trace for trace in self.operator_traces if trace.node_key == node_key]

    def operator_trace_count(
        self,
        node: BranchNode,
        outcome: BranchType,
        *,
        require_output: bool = False,
    ) -> int:
        return sum(
            1
            for trace in self.traces_for_node(node)
            if trace.outcome == outcome
            and (not require_output or bool(trace.output_row_ids))
        )

    def root_output_lineages(self) -> List[RowLineage]:
        return [
            lineage
            for lineage in self.row_lineage.values()
            if lineage.site == "root_result"
        ]

    def _target_specs_for_node(self, node: BranchNode) -> List[Tuple[int, BranchType, int]]:
        """Coverage targets for a node, including operator-level outcomes."""

        specs: List[Tuple[int, BranchType, int]] = []

        def root_result_row_count() -> int:
            counts = [
                obligation.row_count
                for obligation in node.obligations
                if obligation.kind == "root_result"
            ]
            return max(counts or [1])

        def add(atom_id: int, outcome: BranchType, threshold: int) -> None:
            if threshold > 0:
                specs.append((atom_id, outcome, threshold))

        if node.coverage_obligations:
            for obligation in node.coverage_obligations:
                for outcome in obligation.outcomes:
                    if (
                        node.site == "root_result"
                        and outcome == BranchType.DUPLICATE
                        and obligation.metric == "project_duplicate"
                        and not any(
                            row_set.duplicate_expressions
                            for row_set in (
                                candidate.row_set
                                for candidate in node.obligations
                                if candidate.row_set is not None
                            )
                        )
                    ):
                        continue
                    threshold = self.thresholds.threshold_for(outcome)
                    if node.site == "root_result" and outcome == BranchType.ATOM_TRUE:
                        threshold = max(threshold, root_result_row_count())
                    add(
                        obligation.atom_id,
                        outcome,
                        threshold,
                    )
            return specs

        if node.site == "group":
            add(0, BranchType.GROUP_SINGLE, self.thresholds.group_single)
            add(0, BranchType.GROUP_MULTI, self.thresholds.group_multi)
            return specs

        if node.site == "distinct":
            add(0, BranchType.DISTINCT_UNIQUE, self.thresholds.distinct_unique)
            add(0, BranchType.DISTINCT_DUPLICATE, self.thresholds.distinct_duplicate)
            return specs

        if node.site == "project_output":
            for atom_id in range(len(node.atoms)):
                add(atom_id, BranchType.PROJECT_NULL, self.thresholds.project_null)
                add(atom_id, BranchType.PROJECT_NON_NULL, self.thresholds.project_non_null)
            return specs

        if node.site == "aggregate_output":
            for atom_id, atom in enumerate(node.atoms):
                expression = atom.this if isinstance(atom, exp.Alias) else atom
                if not isinstance(expression, exp.Count):
                    add(atom_id, BranchType.AGGREGATE_NULL, self.thresholds.aggregate_null)
                add(atom_id, BranchType.AGGREGATE_NON_NULL, self.thresholds.aggregate_non_null)
            return specs

        if node.site == "aggregate_distinct_input":
            for atom_id in range(len(node.atoms)):
                add(
                    atom_id,
                    BranchType.AGG_DISTINCT_NULL_IGNORED,
                    self.thresholds.aggregate_distinct_null_ignored,
                )
                add(
                    atom_id,
                    BranchType.AGG_DISTINCT_DUPLICATE_ELIMINATED,
                    self.thresholds.aggregate_distinct_duplicate_eliminated,
                )
                add(
                    atom_id,
                    BranchType.AGG_DISTINCT_MULTIPLE_RETAINED,
                    self.thresholds.aggregate_distinct_multiple_retained,
                )
            return specs

        if node.site == "root_result":
            add(0, BranchType.ATOM_TRUE, root_result_row_count())
            return specs

        if node.site == "exists":
            add(0, BranchType.EXISTS_TRUE, self.thresholds.exists_true)
            add(0, BranchType.EXISTS_FALSE, self.thresholds.exists_false)
            return specs

        if node.site == "in":
            add(0, BranchType.IN_MATCH, self.thresholds.in_match)
            add(0, BranchType.IN_NO_MATCH, self.thresholds.in_no_match)
            return specs

        for atom_id in range(len(node.atoms)):
            add(atom_id, BranchType.ATOM_TRUE, self.thresholds.atom_true)
            add(atom_id, BranchType.ATOM_FALSE, self.thresholds.atom_false)
            add(atom_id, BranchType.ATOM_NULL, self.thresholds.atom_null)

        return specs

    @property
    def uncovered_targets(self) -> List[CoverageTarget]:
        """All atom-outcome pairs that haven't met their threshold."""
        from .branch_tree import CoverageAnalyzer

        return CoverageAnalyzer(self).uncovered_targets

    @property
    def total_targets(self) -> int:
        from .branch_tree import CoverageAnalyzer

        return CoverageAnalyzer(self).total_targets

    @property
    def covered_count(self) -> int:
        from .branch_tree import CoverageAnalyzer

        return CoverageAnalyzer(self).covered_count

    @property
    def coverage_ratio(self) -> float:
        total = self.total_targets
        if total == 0:
            return 1.0
        return self.covered_count / total

    @property
    def fully_covered(self) -> bool:
        from .branch_tree import CoverageAnalyzer

        return CoverageAnalyzer(self).fully_covered

    def mark_infeasible(self, node: BranchNode, atom_id: int, outcome: BranchType) -> None:
        node.mark_infeasible(atom_id, outcome)
        self._cache_dirty = True

    def mark_target_infeasible(self, target: CoverageTarget) -> None:
        if target.atom_outcomes:
            target.node.mark_atom_outcomes_infeasible(target.atom_outcomes)
        else:
            target.node.mark_infeasible(target.atom_id, target.target_outcome)
        self._cache_dirty = True

    def mark_strategy_generated(self, target: CoverageTarget) -> None:
        target.node.mark_strategy_generated(target.atom_id, target.target_outcome)
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

    def path_for_target(self, target: CoverageTarget) -> BranchPath:
        from .branch_tree import BranchPathBuilder

        return BranchPathBuilder().path_for_target(target)

    @property
    def root_witness_targets(self) -> List[CoverageTarget]:
        """Targets that exercise the full row path from root to a branch node.

        These are preferred over ordinary atom-coverage targets so the engine
        first generates rows that survive the entire plan path, then fills in
        remaining edge-case atoms.  Only returns targets that are not yet
        covered (observation count below threshold).
        """
        from .branch_tree import CoverageAnalyzer

        return CoverageAnalyzer(self).root_witness_targets


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
    "CoverageObligation",
    "CoverageThresholds",
    "GenerationResult",
    "GroupTrace",
    "JoinFact",
    "OperatorObligation",
    "OperatorTrace",
    "PathObservationKey",
    "PathPredicate",
    "RowLineage",
    "RowSetObligation",
    "SubqueryPath",
]
