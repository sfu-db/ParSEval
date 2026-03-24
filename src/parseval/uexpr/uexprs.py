from __future__ import annotations
from ..constants import (
    PlausibleBit,
    PlausibleType,
    PBit,
    BranchType,
    StepType,
    is_valid_path_bit,
)
from typing import Optional, Dict, Any, Set, Union, List, TYPE_CHECKING, Tuple
from collections import defaultdict, deque
from ordered_set import OrderedSet
from parseval.plan.rex import Symbol, Const, ColumnRef
from parseval.helper import group_by_concrete
from parseval.configuration import Config
from parseval.uexpr.coverage import CoverageCalculator

if TYPE_CHECKING:
    from parseval.plan.rex import Expression

from sqlglot.expressions import AggFunc, Predicate


import logging

logger = logging.getLogger("parseval.coverage")


class _Constraint:
    __slots__ = ("tree", "parent", "path", "depth", "_pattern", "hits")

    def __init__(self, tree, parent: Optional[_Constraint] = None):
        self.tree = tree
        self.parent = parent
        self.path = None
        self._pattern = None
        self.depth: int = 0
        self.hits: Dict[PlausibleBit, int] = {}

    def bit(self) -> PlausibleBit:
        if self.parent is not None:
            for k, v in self.parent.children.items():
                if v is self:
                    return k

    def hit(self):
        if self.parent is not None and self.parent.step_type != StepType.ROOT:
            bit = self.bit()
            if bit in self.parent.hits:
                return self.parent.hits[bit]
            return len(self.parent.coverage[bit])
        return 0

    def mark_pending(self):
        self.attempts += 1
        self.plausible_type = PlausibleType.PENDING

    def mark_infeasible(self):
        self.plausible_type = PlausibleType.INFEASIBLE

    def mark_timeout(self):
        self.plausible_type = PlausibleType.TIMEOUT

    def mark_error(self):
        self.plausible_type = PlausibleType.ERROR

    def mark_covered(self):
        self.plausible_type = PlausibleType.COVERED

    def get_path_to_root(self) -> List[_Constraint]:
        if self.path is not None:
            return self.path
        parent_path = []
        if self.parent is not None:
            parent_path = self.parent.get_path_to_root()
        self.path = parent_path + [self]
        return self.path

    def pattern(self):
        path = self.get_path_to_root()
        if self._pattern is not None:
            return self._pattern
        bits = [node.bit() for node in path[2:]]
        self._pattern = tuple(bits)
        return self._pattern


class PlausibleBranch(_Constraint):
    """Represents an unexplored but potentially reachable branch in the constraint tree.

    A PlausibleNode is a placeholder that says "this branch could be taken,
    but we haven't explored it yet." It helps track coverage gaps.

    """

    LABEL_STRATEGIES = {}

    def __init__(
        self,
        tree,
        parent,
        branch: BranchType,
        plausible_type: Optional[PlausibleType] = None,
        metadata: Dict[str, Any] = None,
    ):
        super().__init__(tree, parent)
        self._branch = branch
        self.attempts = 0
        self.metadata = metadata or {}
        self.is_feasible: Optional[bool] = None
        self.plausible_type = plausible_type or PlausibleType.UNEXPLORED

    @property
    def branch(self) -> BranchType:
        bit = self.bit()
        if self._branch in {
            BranchType.POSITIVE,
            BranchType.NEGATIVE,
        } or self.parent.coverage.get(bit, []):
            return self._branch
        return BranchType.UNDECIDED

    @branch.setter
    def branch(self, value: bool):
        self._branch = value

    def mark_infeasible(self):
        self.plausible_type = PlausibleType.INFEASIBLE

    def mark_covered(self):
        self.plausible_type = PlausibleType.COVERED

    def accept(self, visitor):
        return visitor.visit_plausible_branch(self)

    def __str__(self):
        return (
            f"PlausibleNode( "
            f"branch={self.branch}, "
            f"type={self.plausible_type.value})"
        )

    def __repr__(self):
        return str(self)


class Constraint(_Constraint):
    PLAUSIBLE_CONFIGS = {
        "scan": (PBit.TRUE,),
        "filter": (PBit.FALSE, PBit.TRUE),
        "join": (PBit.JOIN_TRUE, PBit.JOIN_LEFT, PBit.JOIN_RIGHT),
        "project": (PBit.PROJECT, PBit.NULL, PBit.DUPLICATE),
        "groupby": (PBit.GROUP_SIZE, PBit.GROUP_COUNT),
        "aggregate": (PBit.AGGREGATE_SIZE, PBit.GROUP_NULL, PBit.GROUP_DUPLICATE),
        "predicate": (PBit.FALSE, PBit.TRUE),
        "sort": (PBit.TRUE, PBit.MAX, PBit.MIN),
        "having": (PBit.HAVING_TRUE, PBit.HAVING_FALSE),
    }

    def __init__(
        self,
        tree,
        parent=None,
        scope_id: Optional[int] = None,
        step_type: StepType = StepType.ROOT,
        step_name: Optional[str] = None,
        sql_condition: Optional[Expression] = None,
        children: Optional[Dict[str, Constraint]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(tree, parent)
        self.scope_id = scope_id
        self.step_type = step_type if step_type else None
        self.step_name = step_name or step_type.value
        self.sql_condition = sql_condition
        self.children = children or {}
        self.coverage = defaultdict(
            list
        )  # Symbolic expressions coverage indexed by bit
        self.rowid_index: Dict[PBit, List[Tuple[str, ...]]] = defaultdict(
            list
        )  # # rowids indexed by bit
        self.table_refs: Set[str] = set()  # tables this node references
        self.metadata = metadata if metadata is not None else {}

    @property
    def plausible_bits(self) -> Tuple[PBit, ...]:
        if self.step_type == StepType.ROOT:
            return (PBit.TRUE,)
        if self.step_type == StepType.PROJECT and isinstance(
            self.sql_condition, Predicate
        ):
            return self.PLAUSIBLE_CONFIGS["filter"]
        return self.PLAUSIBLE_CONFIGS.get(self.step_type.value, (PBit.TRUE,))

    def __repr__(self):
        return str(self)

    def __str__(self):
        return f"{self.step_type}({self.sql_condition})"

    def __getitem__(self, key):
        if hasattr(self, key):
            return getattr(self, key)
        elif key in self.metadata:
            return self.metadata[key]
        raise KeyError(f"Key {key} not found in {self}.")

    def add_child_if_not_exists(
        self,
        scope_id: int,
        step_type: StepType,
        step_name: str,
        sql_condition: Expression,
        bit: PlausibleBit,
        **kwargs,
    ):
        child_node = self.children.get(bit, None)
        if child_node is None or isinstance(child_node, PlausibleBranch):
            child_node = Constraint(
                tree=self.tree,
                parent=self,
                scope_id=scope_id,
                step_type=step_type,
                step_name=step_name,
                sql_condition=sql_condition,
                metadata={**kwargs},
            )
            child_node.depth = self.depth + 1
            self.children[bit] = child_node
        return child_node

    def update_coverage(
        self,
        bit: PlausibleBit,
        smt_expr: Union[List[Symbol], Symbol],
        rowids,
        branch: bool,
        name: str = None,
        **kwargs,
    ):
        """
        Update delta with table-aware tracking.

        Args:
            bit: The plausible bit for this update
            symbolic_expr: Symbolic expression(s) to add
            rowids: Row IDs being tracked
            branch: Whether this branch was taken
            table_name: Optional table name for rowid scoping
        """
        name = name or self.step_name
        bit = PBit.from_int(bit)
        smt_exprs = smt_expr if isinstance(smt_expr, list) else [smt_expr]
        self.coverage[bit].extend(smt_exprs)
        self.rowid_index[bit].append(rowids)
        self.tree._index_row(rowids, self, bit)
        self.metadata.update(kwargs)

    def update_branchtype(self, bit: PlausibleBit, branch: bool):
        if isinstance(self.children.get(bit, None), PlausibleBranch):
            branch_type = BranchType(int(branch))
            self.children[bit].branch = branch_type
            if branch_type:
                self.tree._index_pnode(self, bit)

    def has_rowids_for_bit(self, bit: PlausibleBit, rowids) -> bool:
        if rowids in self.rowid_index.get(bit, []):
            return True
        # for rowid in self.rowid_index.get(bit, []):
        #     if set(rowids).intersection(set(rowid)):
        #         return True
        return False

    def _branchtype(self, bit: PlausibleBit, branch: bool):
        b = int(branch)
        bit = PBit.from_int(bit)
        if self.parent.step_type == StepType.SCAN:
            return BranchType(b)
        if is_valid_path_bit(bit) and self.coverage.get(bit, []):
            return BranchType(b)
        return BranchType.UNDECIDED

    def upsert_plausible_nodes(self, tbit: PBit, branch):
        # #
        if self.step_type in {
            StepType.SCAN,
            StepType.PROJECT,
            StepType.SORT,
            StepType.GROUPBY,
            StepType.AGGREGATE,
        }:
            branch = 1

        for bit in self.plausible_bits:
            if bit in self.children:
                continue
            branch = (
                self._branchtype(bit, branch) if bit == tbit else BranchType.UNDECIDED
            )
            plausible = PlausibleBranch(self.tree, self, branch)
            self.children[bit] = plausible
            child_pattern = self.pattern()
            if child_pattern in self.tree.leaves:
                del self.tree.leaves[child_pattern]
            child_pattern = plausible.pattern()
            self.tree.leaves[child_pattern] = plausible


class UExprToConstraint:
    """
    Traces the execution of a UExpr over data rows, building a constraint tree
    that captures the paths taken and plausible branches not yet explored.
    """

    def __init__(self):
        self.leaves: Dict[Tuple[PBit, ...], PlausibleBranch] = {}
        self.root = Constraint(tree=self, step_type=StepType.ROOT, step_name="ROOT")
        # Index of leaf patterns to their corresponding Constraint nodes
        self.prev_steps = deque(["ROOT"])
        self.pnode_index: Dict[Tuple[str, ...], Set[Tuple[Constraint, PBit]]] = (
            defaultdict(OrderedSet)
        )  ## index nodes by { (scope_id, step type, step name) : ( Constraint, bit)}
        self._current_step = (None, self.root, PBit.TRUE)
        self._prev_step = None
        self.row_index: Dict[Tuple[Any, ...], Set[Tuple[_Constraint, PlausibleBit]]] = (
            defaultdict(set)
        )  ## index rowids to nodes and bits that cover them
        self.attempt_index: Dict[Tuple[Any, ...], int] = defaultdict(int)

    def get_prev_step(self, scope_id, step_type, step_name):
        if (scope_id, step_type, step_name) != self._current_step:
            self._prev_step = self._current_step
            self._current_step = (scope_id, step_type, step_name)
        return self._prev_step

    def reset(self):
        self._prev_step = None
        self._current_step = (None, self.root, PBit.TRUE)
        q = deque([self.root])
        while q:
            node = q.popleft()
            if isinstance(node, PlausibleBranch):
                continue
            node.hits.clear()
            node.coverage.clear()
            node.rowid_index.clear()
            node.metadata.clear()
            for child in node.children.values():
                q.append(child)

    def _index_row(self, rowids: Tuple[Any, ...], node: _Constraint, bit: PlausibleBit):
        """
        Internal helper to index a rowid -> (node, bit) mapping.
        This accelerates lookup of which UExpr nodes correspond to a runtime
        row (or tuple of rowids for composed rows like joins). The index is
        updated from Constraint.update_delta when symbolic rows are recorded.
        """
        self.row_index[rowids].add((node, bit))

    def _index_pnode(self, node: Constraint, bit: PlausibleBit):
        if is_valid_path_bit(bit):
            key = (node.scope_id, node.step_type, node.step_name)
            self.pnode_index[key].add((node, bit))

    def leaf_key(self, leaf: PlausibleBranch) -> Tuple[Any, ...]:
        node = leaf.parent
        condition = (
            node.sql_condition.sql() if getattr(node, "sql_condition", None) else "ROOT"
        )
        return (node.scope_id, node.step_type, node.step_name, condition, leaf.bit())

    def _find_positive_branch(self) -> List[Tuple[Constraint, PBit]]:
        candidates = []
        for leaf in self.leaves.values():
            if leaf.branch == BranchType.POSITIVE and is_valid_path_bit(leaf.bit()):
                candidates.append((leaf.parent, leaf.bit()))
        if not candidates:
            candidates.append((self.root, PBit.TRUE))
        return candidates

    def find_attach_to(
        self,
        prev_step,
        scope_id,
        step_type: StepType,
        step_name: str,
        rowids: Tuple[Any, ...],
    ) -> List[Tuple[Constraint, PBit]]:
        assert (
            rowids is not None
        ), f"Row IDs must be provided for non-scan steps to find attachment point. get{step_type}"
        starting_nodes = []
        positive_nodes = []
        for node, bit in self.pnode_index[prev_step]:
            if isinstance(node, Constraint):
                if node.has_rowids_for_bit(bit, rowids):
                    starting_nodes.append((node, bit))
                positive_nodes.append((node, bit))

        if not starting_nodes:
            starting_nodes = set()
            q = deque(positive_nodes)
            while q:
                node, bit = q.popleft()
                plausible_child = node.children.get(bit, None)
                if (
                    isinstance(node, Constraint)
                    and node.scope_id == scope_id
                    and node.step_type == step_type
                    and node.step_name == step_name
                ):
                    starting_nodes.add((node.parent, node.bit()))
                elif (
                    isinstance(plausible_child, PlausibleBranch)
                    and plausible_child.branch == BranchType.POSITIVE
                    and is_valid_path_bit(bit)
                ):
                    starting_nodes.add((node, bit))

                for child_bit, child in node.children.items():
                    if isinstance(child, Constraint):
                        key = (child.scope_id, child.step_type, child.step_name)
                        if key in self.pnode_index:
                            q.append((child, child_bit))
        assert (
            starting_nodes
        ), f"No attachment point found for step {step_type} {step_name} with rowids {rowids}. Positive nodes: {positive_nodes}, prev step: {prev_step}"
        return starting_nodes

    def which_path(
        self,
        scope_id: int,
        step_type: str,
        step_name: str,
        sql_conditions: List[Expression],
        takens: List[int],
        smt_exprs: Optional[List[Symbol]] = None,
        rowids: Optional[Tuple[Any, ...]] = None,
        branch: bool = BranchType.UNDECIDED,
        **kwargs,
    ):
        """
        Record which path(s) were taken at a given step in the UExpr execution.

        Args:
            scope_id: Unique identifier for the current query scope.
            step_type: Type of the UExpr step (e.g., "Filter", "Join").
            step_name: Unique name/ID of the UExpr step.
            sql_conditions: List of SQL conditions evaluated at this step.
            smt_exprs: List of symbolic expressions corresponding to each condition.
            takens: List of integers indicating which conditions were taken.
            rowids: Tuple of row IDs being processed.
            branch: Whether this is a branching operation.
            attach_to: Optional step name to attach new nodes to instead of prev_step.
            kwargs: Additional metadata to attach to new nodes.
        """
        step_type = StepType(step_type.lower())
        prev_step = self.get_prev_step(scope_id, step_type, step_name)
        if step_type == StepType.SCAN:
            for index, (sql_condition, taken) in enumerate(zip(sql_conditions, takens)):
                key = (scope_id, step_type, step_name)
                candidates = []
                if key in self.pnode_index:
                    for node, bit in self.pnode_index.get(key, []):
                        if node.sql_condition.sql() == sql_condition.sql():
                            candidates.append((node.parent, node.bit()))
                if not candidates:
                    candidates = self._find_positive_branch()

                for node, bit in candidates:
                    node = node.add_child_if_not_exists(
                        scope_id=scope_id,
                        step_type=step_type,
                        step_name=step_name,
                        sql_condition=sql_condition,
                        bit=bit,
                        **kwargs,
                    )
                    b = PBit.from_int(taken)
                    node.upsert_plausible_nodes(b, branch=branch)
                    if smt_exprs:
                        smt_expr = smt_exprs[index] if index < len(smt_exprs) else []
                        node.update_coverage(
                            b, smt_expr, rowids, branch, name=step_name, **kwargs
                        )
                    node.update_branchtype(b, branch)
        else:

            for start, bit in self.find_attach_to(
                prev_step=prev_step,
                scope_id=scope_id,
                step_type=step_type,
                step_name=step_name,
                rowids=rowids,
            ):
                node, b = start, bit
                for index, (sql_condition, taken) in enumerate(
                    zip(sql_conditions, takens)
                ):
                    node = node.add_child_if_not_exists(
                        scope_id=scope_id,
                        step_type=step_type,
                        step_name=step_name,
                        sql_condition=sql_condition,
                        bit=b,
                        **kwargs,
                    )
                    b = PBit.from_int(taken)
                    node.upsert_plausible_nodes(b, branch=branch)
                    if smt_exprs:
                        smt_expr = smt_exprs[index] if index < len(smt_exprs) else []
                        node.update_coverage(
                            b, smt_expr, rowids, branch, name=step_name, **kwargs
                        )
                    node.update_branchtype(b, branch)

    def get_positive_patterns(self) -> List[Tuple[PBit, ...]]:
        """
        Retrieve all patterns of bits that have been marked as positive branches.

        Returns:
            List of tuples representing positive bit patterns.
        """
        positive_patterns = []
        for pattern, leaf in self.leaves.items():
            if leaf.branch is BranchType.POSITIVE:
                positive_patterns.append(pattern)
        return positive_patterns

    def get_unexplored_patterns(self) -> List[Tuple[PBit, ...]]:
        """
        Retrieve all patterns of bits that are unexplored.

        Returns:
            List of tuples representing unexplored bit patterns.
        """
        unexplored_patterns = []
        for pattern, leaf in self.leaves.items():
            if leaf.plausible_type in {PlausibleType.UNEXPLORED, PlausibleType.PENDING}:
                unexplored_patterns.append(pattern)
        return unexplored_patterns

    def update_stats(self, config: Config):
        """
        Update statistics for all leaves based on the current configuration and coverage.

        Args:
            config: Configuration object containing parameters for checks.
        """
        calculator = CoverageCalculator(**config.to_dict())
        for pattern, leaf in self.leaves.items():
            # leaf.attempts = self.attempt_index[self.leaf_key(leaf)]
            if leaf.attempts >= config.max_tries:
                leaf.mark_covered()
                continue
            coverage = calculator.evaluate_leaf(leaf)
            leaf.parent.hits[leaf.bit()] = coverage.hits
            if coverage.forced_branch is not None:
                leaf.branch = coverage.forced_branch
            if coverage.infeasible:
                leaf.mark_infeasible()
            elif coverage.covered:
                leaf.mark_covered()
            elif leaf.plausible_type in {
                PlausibleType.COVERED,
                PlausibleType.PENDING,
            }:
                leaf.plausible_type = PlausibleType.UNEXPLORED

    def next_path(self, config: Config, skips=None) -> Optional[_Constraint]:
        """
        Given a pattern of plausible bits, find the next unexplored constraint node.

        Args:
            pattern: Tuple of plausible bits representing the path taken so far.
        """
        skips = skips or set()
        ## First, run checks to update the plausible types of all leaves based on the current configuration and coverage
        self.update_stats(config)

        candidates = []
        for pattern, leaf in self.leaves.items():
            logger.info(f'pattern: {"/".join(str(p) for p in pattern)}')
            if self.attempt_index[self.leaf_key(leaf)] >= config.max_tries:
                continue

            if leaf.plausible_type in {
                PlausibleType.INFEASIBLE,
                PlausibleType.ERROR,
                PlausibleType.TIMEOUT,
            }:
                continue

            if pattern in skips:
                continue

            candidates.append((pattern, leaf))

        def _priority(item):
            pattern, leaf = item
            branch_priority = {
                BranchType.POSITIVE: 0,
                BranchType.UNDECIDED: 1,
                BranchType.NEGATIVE: 2,
            }.get(leaf.branch, 3)
            covered_penalty = 1 if leaf.plausible_type == PlausibleType.COVERED else 0
            return (covered_penalty, branch_priority, -len(pattern), leaf.attempts)

        for pattern, leaf in sorted(candidates, key=_priority):
            key = self.leaf_key(leaf)
            self.attempt_index[key] += 1
            leaf.mark_pending()
            # leaf.attempts = self.attempt_index[key]
            leaf.plausible_type = PlausibleType.PENDING
            return pattern, leaf
        return None, None
