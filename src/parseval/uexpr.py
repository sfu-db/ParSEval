from __future__ import annotations
from __future__ import annotations

from typing import List, TYPE_CHECKING, Union, Optional, Any, Dict

if TYPE_CHECKING:
    from .plan.expression import ExpOrStr
    from .plan.step import LogicalOperator

import src.parseval.symbol as sym

import src.parseval.plan.expression as sql_exp

from enum import Enum, IntEnum, auto
from collections import defaultdict

from dataclasses import dataclass
from typing import List, Optional, Dict, Union, Set, Any, Tuple


class ConstraintBehavior(Enum):
    """Defines different constraint behaviors."""

    PREDICATE = "predicate"  # Standard if/else branching
    JOIN = "join"  # 3-way branching (true/false/outer)
    PROJECTION = "projection"  # No negation needed
    AGGREGATE = "aggregate"  # Similar to predicate but tracks aggregate info
    GROUP = "group"  # Similar to predicate but tracks grouping keys


class PlausibleType(Enum):
    """Types of plausible (unexplored) branches."""

    POSITIVE = "positive"  # Branch
    UNEXPLORED = "unexplored"  # Branch exists but never taken
    INFEASIBLE = "infeasible"  # Branch proven impossible (via constraint solving)
    PENDING = "pending"  # Branch queued for exploration
    TIMEOUT = "timeout"  # Branch exists but solver timed out
    ERROR = "error"  # Branch caused an error during exploration


@dataclass(frozen=True)
class ConstraintConfig:
    """Configuration for constraints.
    The bits tuple defines which branch paths are possible from this constraint.
    """

    behavior: ConstraintBehavior
    should_negate: bool = True
    # Possible bits for branches (e.g., 0, 1 for if/else, 2 for join, 3 for null, 4 for duplicate)
    plausible_bits: Tuple[int, ...] = (0, 1)

    @classmethod
    def for_predicate(cls) -> ConstraintConfig:
        """
        Standard if/else predicate (WHERE, HAVING, filter).
        Bits: 0=false, 1=true
        """
        return cls(
            behavior=ConstraintBehavior.PREDICATE,
            plausible_bits=(0, 1),
        )

    @classmethod
    def for_join(cls) -> ConstraintConfig:
        return cls(behavior=ConstraintBehavior.JOIN, plausible_bits=(0, 1, 2))

    @classmethod
    def for_project(cls) -> ConstraintConfig:
        return cls(
            behavior=ConstraintBehavior.PROJECTION,
            plausible_bits=(1, 3, 4),
            should_negate=False,
        )

    @classmethod
    def for_groupby(cls) -> ConstraintConfig:
        return cls(
            behavior=ConstraintBehavior.GROUP,
            plausible_bits=(1, 3, 4),
            should_negate=False,
        )

    @classmethod
    def for_aggregate(cls) -> ConstraintConfig:
        return cls(
            behavior=ConstraintBehavior.AGGREGATE,
            plausible_bits=(1, 3, 4),
            should_negate=False,
        )


class _Constraint:
    __slots__ = (
        "tree",
        "parent",
        "path",
        "_pattern",
        "_hash",
    )

    def __init__(self, tree, parent: Optional[Constraint] = None):
        self.tree = tree
        self.parent = parent
        self.path = None
        self._pattern = None
        self._hash = None

    def bit(self):
        if self.parent:
            for k, v in self.parent.children.items():
                if v is self:
                    return k
        return ""

    def get_path_to_root(self) -> List[Constraint]:
        if self.path is not None:
            return self.path
        parent_path = []
        if self.parent is not None:
            parent_path = self.parent.get_path_to_root()
        self.path = parent_path + [self]
        return self.path

    def pattern(self):
        if self._pattern is not None:
            return self._pattern
        path = self.get_path_to_root()
        self._pattern = "".join(p.bit() for p in path[1:])
        return self._pattern

    def __repr__(self):
        return str(self)


class PlausibleBranch(_Constraint):
    """Represents an unexplored but potentially reachable branch in the constraint tree.

    A PlausibleNode is a placeholder that says "this branch could be taken,
    but we haven't explored it yet." It helps track coverage gaps.

    """

    def __init__(
        self,
        tree,
        parent,
        branch: bool,
        plausible_type: Optional[PlausibleType] = None,
        metadata: Dict[str, Any] = None,
    ):
        super().__init__(tree, parent)
        self.plausible_type = plausible_type
        self.branch = branch
        self.attempts = 0
        self.metadata = metadata or {}
        self.is_feasible: Optional[bool] = None
        self.plausible_type = plausible_type or PlausibleType.UNEXPLORED

    def mark_pending(self):
        self.plausible_type = PlausibleType.PENDING

    def mark_infeasible(self):
        self.plausible_type = PlausibleType.INFEASIBLE

    def mark_timeout(self):
        self.plausible_type = PlausibleType.TIMEOUT

    def mark_error(self):
        self.plausible_type = PlausibleType.ERROR

    def __repr__(self):
        return (
            f"PlausibleNode(pattern={self.pattern()}, "
            f"branch={self.branch}, "
            f"type={self.plausible_type.value}, "
            f"feasible={self.is_feasible})"
        )


class Constraint(_Constraint):
    CONSTRAINT_CONFIGS = {
        "join": ConstraintConfig.for_join(),
        "project": ConstraintConfig.for_project(),
        "groupby": ConstraintConfig.for_groupby(),
        "aggregate": ConstraintConfig.for_aggregate(),
        "predicate": ConstraintConfig.for_predicate(),
    }

    def __init__(
        self,
        tree,
        parent=None,
        operator: Optional[LogicalOperator] = None,
        children: Optional[Dict[str, Constraint]] = None,
        sql_condition: Optional[ExpOrStr] = None,
        symbolic_exprs: Optional[List[sym.Symbol]] = None,
        delta: Optional[List[Any]] = None,
        branch: Optional[bool] = True,
        metadata: Optional[Dict[str, Any]] = None,
        config: Optional[ConstraintConfig] = None,
    ):
        super().__init__(tree, parent)
        self.operator = operator
        self.children = children or {}
        self.sql_condition = sql_condition
        self.symbolic_exprs = symbolic_exprs or []
        self.delta = delta or []
        self.branch = branch
        self.metadata = metadata or {}
        self.config = config or self._infer_config(operator)
        self._create_plausible_siblings()

    def _infer_config(self, operator: Optional[LogicalOperator]) -> ConstraintConfig:
        if operator is None or operator == "ROOT":
            return ConstraintConfig(
                behavior=ConstraintBehavior.PROJECTION,
                should_negate=False,
                plausible_bits=(1,),
            )

        if operator.operator_type == "Join":
            return self.CONSTRAINT_CONFIGS["join"]
        if operator.operator_type == "Filter":
            return self.CONSTRAINT_CONFIGS["predicate"]
        if operator.operator_type == "Project":
            return self.CONSTRAINT_CONFIGS["project"]
        if operator.operator_type == "Aggregate":
            return self.CONSTRAINT_CONFIGS["aggregate"]

        raise ValueError(
            f"Cannot infer config for operator type: {operator.operator_type}"
        )

    def __str__(self):
        return f"Constraint({self.operator}, {self.sql_condition})"

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(
                (
                    self.operator.id if self.operator else None,
                    str(self.sql_condition) if self.sql_condition else None,
                )
            )
        return self._hash

    def __eq__(self, value):
        if not isinstance(value, Constraint):
            return False
        return (
            self.operator.operator_type == value.operator.operator_type
            and self.operator.id == value.operator.id
            and str(self.sql_condition) == str(value.sql_condition)
        )

    def add_child(
        self,
        operator: LogicalOperator,
        sql_condition: ExpOrStr,
        symbolic_expr: sym.Symbol,
        taken: bool,
        branch: bool,
        rows,
        **kwargs,
    ):
        bit = int(taken)

        child_node = self.children.get(str(bit), None)
        if child_node is None or isinstance(child_node, PlausibleBranch):
            child_node = Constraint(
                tree=self.tree,
                parent=self,
                sql_condition=sql_condition,
                operator=operator,
                branch=branch,
                **kwargs,
            )
            self.children[str(bit)] = child_node

        self.update_delta(symbolic_expr, rows)

        return child_node[str[bit]]

    def update_delta(self, symbolic_expr: Union[List[sym.Symbol], sym.Symbol], rows):
        p = symbolic_expr if isinstance(symbolic_expr, list) else [symbolic_expr]
        self.symbolic_exprs.extend(p)
        self.delta.append(rows)

    def _create_plausible_siblings(self):
        for bit in self.config.plausible_bits:
            if bit in self.children:
                continue
            plausible_type = None
            if self.branch and bit == 1:
                plausible_type = PlausibleType.POSITIVE

            plausible = PlausibleBranch(
                tree=self.tree,
                parent=self,
                branch=self.branch,
                plausible_type=plausible_type,
            )
            self.children[str(bit)] = plausible
            self.tree.leaves.pop(self.pattern(), None)
            self.tree.leaves[plausible.pattern()] = plausible


class UExprToConstraint:

    def __init__(self, declare):
        """
        positive_nodes: a mapping from operator id to all positive path constraint nodes
        prev_operator: the last SQL operator we have seen
        """
        self.constraints = []
        self.leaves: Dict[str, Constraint] = {}
        self.root_constraint = Constraint(self, None, None)
        ## we use this positive_path to cache all positive paths' constraints.
        self.positive_nodes: Dict[str, List[Constraint]] = defaultdict(list)
        self.positive_nodes["ROOT"].append(self.root_constraint)

        self.prev_operator: Optional[LogicalOperator] = "ROOT"
        self.declare = declare

    def advance(self, operator: LogicalOperator):
        """move the current path forward by one step"""
        self.prev_operator = operator.id

    def which_path(
        self,
        operator: LogicalOperator,
        sql_conditions: List[ExpOrStr],
        symbolic_exprs: List[sym.Symbol],
        takens: List[bool],
        rows: Any,
        branch: Union[bool, str],
        **kwargs,
    ):
        current_nodes = self.positive_nodes[self.prev_operator]

        for node in current_nodes:
            # Skip nodes that don't have relevant tuples (for non-root nodes)
            # if node.operator_key != "ROOT" and not node.get_all_tuples().intersection(
            #     tuples
            # ):
            #     continue
            for smt_expr, sql_condition, taken in zip(
                symbolic_exprs, sql_conditions, takens
            ):
                node = node.add_child(
                    operator=operator,
                    sql_condition=sql_condition,
                    symbolic_expr=smt_expr,
                    taken=taken,
                    branch=branch,
                    rows=rows,
                    **kwargs,
                )

                if branch and node not in self.positive_nodes[operator.id]:
                    self.positive_nodes[operator.id].append(node)
