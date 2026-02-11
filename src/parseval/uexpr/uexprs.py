from __future__ import annotations
from ..constants import PlausibleBit, PlausibleType, PBit, BranchType
from typing import Optional, Dict, Any, Set, Union, List, TYPE_CHECKING, Tuple
from collections import defaultdict
from ordered_set import OrderedSet
from src.parseval.plan.rex import Symbol, Const, ColumnRef
from src.parseval.helper import group_by_concrete
from src.parseval.configuration import Config
from src.parseval.uexpr.checks import Check
if TYPE_CHECKING:
    from src.parseval.plan.rex import Expression

from sqlglot.expressions import AggFunc, Predicate



import logging
logger = logging.getLogger("parseval.uexpr")

class _Constraint:
    __slots__ = (
        "tree",
        "parent",
        "path",
        "_pattern",
        "_hash",
    )

    def __init__(self, tree, parent: Optional[_Constraint] = None):
        self.tree = tree
        self.parent = parent
        self.path = None
        self._pattern = None
        self._hash = None

    def bit(self) -> PlausibleBit:
        if self.parent is not None:
            for k, v in self.parent.children.items():
                if v is self:
                    return k

    def hit(self):
        if self.parent is not None and self.parent.step_type != "ROOT":
            bit = self.bit()
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

    LABEL_STRATEGIES = {
    }

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
        vaild_bits = (PBit.FALSE, PBit.TRUE, PBit.JOIN_TRUE, PBit.GROUP_SIZE)
        bit = self.bit()
        if self.parent.coverage.get(bit, []):
            return self._branch
        return BranchType.UNDECIDED
    
    @branch.setter
    def branch(self, value: bool):
        self._branch = value 

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

    def update_mark(self):
        if self.parent is None:
            return
        bit = self.bit()
        # Prefer strategy-based check if available for (operator_type, bit)
        operator_type = None
        if getattr(self.parent, "operator", None) is not None:
            operator_type = getattr(self.parent.operator, "operator_type", None)

        strategy = resolve_check(operator_type, bit)
        # Honor per-tracer configuration: skip strategy when disabled
        if strategy is not None:
            enabled = True
            tracer = getattr(self, "tree", None)
            if (
                tracer is not None
                and getattr(tracer, "strategy_config", None) is not None
            ):
                cfg = tracer.strategy_config
                key = (operator_type, bit)
                if key in cfg:
                    enabled = bool(cfg[key])
                elif bit in cfg:
                    enabled = bool(cfg[bit])
            if enabled:
                try:
                    self.plausible_type = strategy.check(self)
                    return
                except Exception:
                    # Fall back to legacy label strategies on error
                    pass

        # Legacy fallback: use the LABEL_STRATEGIES map if present
        if bit in self.LABEL_STRATEGIES:
            legacy = self.LABEL_STRATEGIES[bit]
            self.plausible_type = legacy(self)

        # for bit, strategy in self.LABEL_STRATEGIES.items():
        #     if bit == self.bit():
        #         # logging.info(f"starting to check {bit}")
        #         self.plausible_type = strategy(self)


    def accept(self, visitor):
        return visitor.visit_plausible_branch(self)

    def __str__(self):
        return (
            f"PlausibleNode( "
            f"branch={self.branch}, "
            f"type={self.plausible_type.value}, "
            f"feasible={self.is_feasible})"
        )


class Constraint(_Constraint):
    PLAUSIBLE_CONFIGS = {
        "scan": (PBit.TRUE, PBit.NULL, PBit.DUPLICATE),
        "filter": (PBit.FALSE, PBit.TRUE),
        "join": (PBit.JOIN_TRUE, PBit.JOIN_LEFT, PBit.JOIN_RIGHT),
        "project": (PBit.TRUE, PBit.NULL, PBit.DUPLICATE),
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
        step_type: Optional[str] = None,
        step_name: Optional[str] = None,
        sql_condition: Optional[Expression] = None,
        children: Optional[Dict[str, Constraint]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(tree, parent)
        self.step_type = step_type
        self.step_name = step_name or step_type
        self.sql_condition = sql_condition
        self.children = children or {}
        self.coverage = defaultdict(list) # Symbolic expressions coverage indexed by bit
        self.scopes = defaultdict(list)  # rowids indexed by (bit, name)
        self.rowid_index: Set[str] = set() # tables this node references        
        self.metadata = metadata if metadata is not None else {}

    @property
    def plausible_bits(self) -> Tuple[PBit, ...]:
        if self.step_type == "ROOT":
            return (PBit.TRUE,)
        
        return self.PLAUSIBLE_CONFIGS.get(self.step_type.lower(), (PBit.TRUE,))
        
        if self.step == "Aggregate":
            if isinstance(self.sql_condition, AggFunc):
                if isinstance(self.sql_condition, Predicate):
                    return self.PLAUSIBLE_CONFIGS["having"]
                return self.PLAUSIBLE_CONFIGS["Having"]
            return self.PLAUSIBLE_CONFIGS["groupby"]
        elif self.step.startswith("Join"):
            return self.PLAUSIBLE_CONFIGS["join"]
        elif isinstance(self.sql_condition, Predicate):
            return self.PLAUSIBLE_CONFIGS["predicate"]
        elif self.step == "Sort":
            return self.PLAUSIBLE_CONFIGS["sort"]
        elif isinstance(self.sql_condition, ColumnRef):
            return self.PLAUSIBLE_CONFIGS["project"]
        return self.PLAUSIBLE_CONFIGS[self.step.lower()]

    def __str__(self):
        return f"{self.step_type}({self.sql_condition})"
    
    def __getitem__(self, key):
        if hasattr(self, key):
            return getattr(self, key)
        elif key in self.metadata:
            return self.metadata[key]
        raise KeyError(f"Key {key} not found in {self}.")

    def update_coverage(self, bit: PlausibleBit, smt_expr: Union[List[Symbol], Symbol], rowids, branch: bool, name: str = None, **kwargs):
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
        
        key = (bit, name)
        self.scopes[key].append(rowids)
        for ridx in rowids:
            self.tree._index_row(self.step_type, self.step_name, ridx, self, bit, branch)
        self.metadata.update(kwargs)
        
    
    def update_branchtype(self, bit: PlausibleBit, branch: bool):
        if isinstance(self.children.get(bit, None), PlausibleBranch):
            branch_type = BranchType(int(branch))
            self.children[bit].branch = branch_type
            if branch_type:
                self.tree.node_index[(self.step_type, self.step_name)].add((self, bit))

    def has_rowids_for_scope(self, bit: PlausibleBit, rowids, name) -> bool:
        key = (bit, name)
        if rowids in self.scopes.get(key, []):
            return True
        
        for rowid in self.scopes.get(key, []):
            if set(rowids).intersection(set(rowid)):
                return True
        return False
        

    def add_child_if_not_exists(
        self,
        step_type: str,
        step_name: str,
        sql_condition: Expression,
        bit: PlausibleBit,
        branch: bool,
        **kwargs,
    ):
        child_node = self.children.get(bit, None)
        if child_node is None or isinstance(child_node, PlausibleBranch):
            child_node = Constraint(
                tree=self.tree,
                parent=self,
                step_type=step_type,
                step_name=step_name,
                sql_condition=sql_condition,
                metadata={**kwargs},
            )
            self.children[bit] = child_node
        return child_node
   
    def _branchtype(self, bit: PlausibleBit, branch: bool):
        b = int(branch)
        vaild_bits = (PBit.FALSE, PBit.TRUE, PBit.JOIN_TRUE, PBit.GROUP_SIZE)
        if self.coverage.get(bit, []) and bit in vaild_bits:
            return BranchType(b)
        else:
            return BranchType.UNDECIDED
   
    def upsert_plausible_nodes(self, tbit, branch):
        # 
        
        for bit in self.plausible_bits:
            if bit in self.children:
                # self.update_branchtype(bit, branch)
                continue
            branch = self._branchtype(bit, branch) if bit == tbit else BranchType.UNDECIDED
            plausible = PlausibleBranch(self.tree, self, branch)
            self.children[bit] = plausible
            child_pattern = self.pattern()
            if child_pattern in self.tree.leaves:
                del self.tree.leaves[child_pattern]
            child_pattern = plausible.pattern()
            self.tree.leaves[child_pattern] = plausible
        
        self.update_branchtype(tbit, branch)







class UExprToConstraint:
    """
    Traces the execution of a UExpr over data rows, building a constraint tree
    that captures the paths taken and plausible branches not yet explored.
    """

    def __init__(self):
        self.root = Constraint(tree=self, step_type="ROOT", step_name="ROOT")
        self.prev_step = "ROOT"
        self.node_index: Dict[Tuple[str, ...], Set[Constraint]] = defaultdict(OrderedSet) ## index nodes by { (step type, step name) : (Constraint, bit)}
         # Index of leaf patterns to their corresponding Constraint nodes
        self.leaves: Dict[Tuple[PBit, ...], PlausibleBranch] = {}
        self.row_index: Dict[str, Set[Tuple[_Constraint, PlausibleBit]]] = defaultdict(OrderedSet)
        self.node_index[("ROOT", "ROOT")].add((self.root, PBit.TRUE))

    def on_scope_enter(self, step: str):
        self.prev_step = step

    def on_scope_exit(self, step: str):
        self.prev_step = step
        
    def _index_row(self, step_type: str, step_name: str, rowid: Any, node: _Constraint, bit: PlausibleBit, branch: bool):
        """Internal helper to index a rowid -> (node, bit) mapping.

        This accelerates lookup of which UExpr nodes correspond to a runtime
        row (or tuple of rowids for composed rows like joins). The index is
        updated from Constraint.update_delta when symbolic rows are recorded.
        """
        self.row_index[rowid].add((node, bit))
        # if branch:
        #     try:
                
        #         self.row_index[rowid][step_id].add((node, bit))
        #     except Exception:
        #         return
    
    def _find_child(self, step_type: str, step_name: str, sql_condition: Expression) -> List[Constraint]:
        """Find existing child nodes matching step name and condition."""
        matching_nodes = []
        for node in self.node_index.get(step_name, []):
            if node.sql_condition == sql_condition:
                matching_nodes.append(node)
        return matching_nodes
    def _validate_nodes_by_rowids(self, candidates: Set[Tuple[Constraint, PBit]], rowids: Tuple, branch: bool, name) -> Set[Tuple[Constraint, PBit]]:
        """Filter nodes to those that have recorded the given rowids for the step and branch."""
        validated = set()
        
        for node, bit in candidates:
            if not isinstance(node, Constraint):
                continue
            if node.parent is None:
                validated.add((node, bit))
                continue
            
            if node.has_rowids_for_scope(bit, rowids, name= name):
                validated.add((node, bit))
        return validated
    
    def update_nodes(self, step_name: str, sql_condition: Expression, symbolic_expr: Symbol, taken: int, rowids: Tuple, branch: bool) -> Set[Constraint]:
        """Update existing nodes with new symbolic expressions and rowids.

        Args:
            step_name: Unique name/ID of the UExpr step.
            sql_condition: SQL condition evaluated at this step.
            symbolic_expr: Symbolic expression corresponding to the condition.
            taken: Integer indicating which condition was taken.
            rowids: Tuple of row IDs being processed.
            branch: Whether this is a branching operation.

        Returns:
            Set of updated Constraint nodes.
        """
        tbit = PBit.from_int(taken)
        existing_nodes = set()
        
        for node in self._find_child(step_name, step_name, sql_condition):
            existing_nodes.add((node, tbit))
        
        if not existing_nodes:
            return set()
        
        validated = self._validate_nodes_by_rowids(existing_nodes, rowids, branch, name=step_name)
        
        for node, bit in validated:
            logger.info(f"Updating existing node: {node}, bit: {bit}, rowids: {rowids in node.parent.delta[bit]}, branch: {branch}")
            node.update_coverage(bit, symbolic_expr, rowids, branch, name=step_name)
        return validated
        
        # existing_nodes = self._find_child(step_name, step_name, sql_condition)
        # validate_nodes = self._validate_nodes_by_rowids(set((node, node.bit()) for node in existing_nodes), rowids, branch, name=step_name)
        
        # for node, tbit in validate_nodes:
        #     logger.info(f"Updating existing node: {node}, bit: {tbit}, rowids: {rowids in node.parent.delta[bit]}, branch: {branch}")
        #     node.update_coverage(tbit, symbolic_expr, rowids, branch, name=step_name)
        
        # # logger.info(f'Updated nodes: {len(validate_nodes)} for step: {step}, condition: {sql_condition}, taken: {taken}, rowids: {rowids}, branch: {branch}')
        # return set(node for node, bit in validate_nodes)
    
    
    def _find_starting_nodes(self, attach_to: Optional[Tuple[str, ...]], rowids) -> List[Tuple[Constraint, PBit]]:
        
        if attach_to is None:
            return [(self.root, PBit.TRUE)]
        starting_nodes = []
        positive_nodes = []
        for node, bit in self.node_index[attach_to]:
            # logger.info(f"considering node: {node} for attach_to: {attach_to}")
            ## if node is a Constraint but step_name does not match attach_to, skip
            if isinstance(node, Constraint) and (node.step_type, node.step_name) != attach_to:
                continue
            
            child = node.children.get(bit, None)
            if isinstance(child, Constraint) and (child.step_type, child.step_name) == attach_to:
                continue
            if node.has_rowids_for_scope(bit, rowids, name = node.step_name):
                starting_nodes.append((node, bit))
            positive_nodes.append((node, bit))
        if not starting_nodes:
            starting_nodes = positive_nodes
            
            # if isinstance(child, PlausibleBranch):
            #     logger.info(f"found plausible child: {child} for bit: {bit}, branch: {child.branch}")
            # if isinstance(child, PlausibleBranch) and child.branch is BranchType.POSITIVE:
            # starting_nodes.append((node, bit))
            
            # for bit, child in node.children.items():
            #     if isinstance(child, Constraint) and (child.step_type, child.step_name) == attach_to:
            #         continue
            #     if isinstance(child, PlausibleBranch):
            #         logger.info(f"found plausible child: {child} for bit: {bit}, branch: {child.branch}")
            #     if isinstance(child, PlausibleBranch) and child.branch is True:
            #         starting_nodes.append((node, bit))
            #     elif node.has_rowids_for_scope(bit, rowids, name = node.step_name):
            #         starting_nodes.append((node, bit))
            #     else:
            #         logger.info(f"skipping child node: {child} for bit: {bit}, rowids: {rowids}, name: {node.step_name}")
        return starting_nodes
        
        
    def which_path(self, step_type: str, step_name: str, sql_conditions: List[Expression], smt_exprs: List[Symbol], takens: List[int], rowids: Tuple, branch: bool, attach_to: Optional[Tuple[str, ...]] = None, **kwargs):
        """
        Record which path(s) were taken at a given step in the UExpr execution.

        Args:
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
        # attach_to = self.root if attach_to is None else attach_to
        starts = self._find_starting_nodes(attach_to, rowids)
        
        for start, bit in starts:
            # logger.info(f"which_path called for step_type: {step_type}, step_name: {step_name}, start: {start}, bit: {bit}, rowids: {rowids}, branch: {branch}, attach_to: {attach_to}, {rowids in start.coverage.get(bit, [])}")
            # logger.info(f"which path for {step_type}, {step_name} from start node: {start}, bit: {bit}, sql_conditions: {sql_conditions}, takens: {takens}, rowids: {rowids}, branch: {branch}")
            node, b = start, bit
            for index, (sql_condition, taken) in enumerate(zip(sql_conditions, takens)):
                node = node.add_child_if_not_exists(
                    step_type=step_type,
                    step_name=step_name,
                    sql_condition=sql_condition,
                    bit = b,
                    branch=branch,
                    **kwargs,
                )
                b = PBit.from_int(taken)
                smt_expr = smt_exprs[index] if index < len(smt_exprs) else []
                node.update_coverage(b, smt_expr, rowids, branch, name=step_name, **kwargs)
                node.upsert_plausible_nodes(b, branch=branch)
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
    
    
    def next_path(self, config: Config, skips = None) -> Optional[_Constraint]:
        """
        Given a pattern of plausible bits, find the next unexplored constraint node.

        Args:
            pattern: Tuple of plausible bits representing the path taken so far.
        """
        skips = skips or set()
        ## First, run checks to update the plausible types of all leaves based on the current configuration and coverage
        check = Check(**config.to_dict())
        
        for pattern, leaf in self.leaves.items():
            leaf.accept(check)
        
        for pattern, leaf in self.leaves.items():
            if leaf.attempts > config.max_tries:
                if leaf.branch == BranchType.UNDECIDED:
                    leaf.mark_infeasible()
                continue
            if leaf.plausible_type == PlausibleType.INFEASIBLE:
                continue
                
            if pattern in skips:
                continue
            
            leaf.mark_pending()
            if leaf.branch == BranchType.NEGATIVE \
                and leaf.plausible_type not in {PlausibleType.COVERED, PlausibleType.INFEASIBLE}:
                return pattern, leaf
            
            if leaf.branch in [BranchType.UNDECIDED, BranchType.POSITIVE]:
                return pattern, leaf
        return None, None
        