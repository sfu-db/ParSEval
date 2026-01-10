from __future__ import annotations
from collections import defaultdict
from typing import Optional, Tuple, List, Union, TYPE_CHECKING, Dict, Any

from .base import _Constraint, PlausibleBit
from ..constants import PBit, PlausibleType

if TYPE_CHECKING:
    from src.parseval.plan.rex import LogicalOperator, Expression
from src.parseval.plan import ColumnRef
from sqlglot.expressions import AggFunc, Predicate
from src.parseval.symbol import Symbol
from .checks import resolve_check


class PlausibleBranch(_Constraint):
    """Represents an unexplored but potentially reachable branch in the constraint tree.

    A PlausibleNode is a placeholder that says "this branch could be taken,
    but we haven't explored it yet." It helps track coverage gaps.

    """

    LABEL_STRATEGIES = {
        # PBit.DUPLICATE: check_cover_duplicate,
        # PBit.NULL: check_cover_null,
        # PBit.TRUE: lambda self: (
        #     PlausibleType.COVERED
        #     if len(self.parent.symbolic_exprs[PBit.TRUE]) > 2
        #     else self.plausible_type
        # ),
        # PBit.FALSE: lambda self: (
        #     PlausibleType.COVERED
        #     if len(self.parent.symbolic_exprs[PBit.FALSE]) > 2
        #     else self.plausible_type
        # ),
        # PBit.MAX: check_cardinality,
        # PBit.MIN: check_cardinality,
        # PBit.GROUP_COUNT: check_groupcount,
        # PBit.GROUP_SIZE: check_groupsize,
    }

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
        self._branch = branch
        self.attempts = 0
        self.metadata = metadata or {}
        self.is_feasible: Optional[bool] = None
        self.plausible_type = plausible_type or PlausibleType.UNEXPLORED

    @property
    def branch(self) -> bool:
        vaild_bits = (PBit.FALSE, PBit.TRUE, PBit.JOIN_TRUE, PBit.GROUP_SIZE)
        return self._branch and self.bit() in vaild_bits

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

    def __str__(self):
        return (
            f"PlausibleNode( "
            f"branch={self.branch}, "
            f"type={self.plausible_type.value}, "
            f"feasible={self.is_feasible})"
        )


class Constraint(_Constraint):
    PLAUSIBLE_CONFIGS = {
        "filter": (PBit.FALSE, PBit.TRUE),
        "join": (PBit.JOIN_TRUE, PBit.JOIN_LEFT, PBit.JOIN_RIGHT),
        "project": (PBit.TRUE, PBit.NULL, PBit.DUPLICATE),
        "groupby": (PBit.GROUP_SIZE, PBit.GROUP_COUNT),
        "aggregate": (PBit.GROUP_SIZE, PBit.NULL, PBit.DUPLICATE),
        "predicate": (PBit.FALSE, PBit.TRUE),
        "sort": (PBit.TRUE, PBit.MAX, PBit.MIN),
        "having": (PBit.HAVING_TRUE, PBit.HAVING_FALSE),
    }

    def __init__(
        self,
        tree,
        parent=None,
        operator: Optional[LogicalOperator] = None,
        children: Optional[Dict[str, Constraint]] = None,
        sql_condition: Optional[Expression] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(tree, parent)
        self.operator = operator
        self.children = children or {}
        self.sql_condition = sql_condition
        self.symbolic_exprs = defaultdict(list)
        self.delta = defaultdict(list)
        self.metadata = metadata if metadata is not None else {}

    @property
    def plausible_bits(self) -> Tuple[PBit, ...]:
        if self.operator is None or self.operator == "ROOT":
            return (PBit.TRUE,)
        if self.operator.operator_type == "Aggregate":
            if isinstance(self.sql_condition, AggFunc):
                return self.PLAUSIBLE_CONFIGS["aggregate"]
            return self.PLAUSIBLE_CONFIGS["groupby"]
        elif self.operator.operator_type == "Having":
            return self.PLAUSIBLE_CONFIGS["having"]
        elif self.operator.operator_type == "Join":
            return self.PLAUSIBLE_CONFIGS["join"]
        elif (
            isinstance(self.sql_condition, Predicate)
            or self.operator.operator_type == "Filter"
        ):
            return self.PLAUSIBLE_CONFIGS["predicate"]
        elif self.operator.operator_type == "Sort":
            return self.PLAUSIBLE_CONFIGS["sort"]
        elif (
            isinstance(self.sql_condition, ColumnRef)
            or self.operator.operator_type == "Project"
        ):
            return self.PLAUSIBLE_CONFIGS["project"]
        return self.PLAUSIBLE_CONFIGS[self.operator.operator_type.lower()]

    def __str__(self):
        return f"Constraint({self.operator.operator_type if self.operator else 'ROOT'}, {self.sql_condition})"

    def __hash__(self):
        """
        Calculates a hash based on the same attributes used in __eq__.
        Use a tuple of immutable attributes for hashing.
        """
        return hash((self.operator, self.sql_condition))

    def __eq__(self, value):
        if not isinstance(value, Constraint):
            return False
        return (
            self.operator.operator_type == value.operator.operator_type
            and self.operator.operator_id == value.operator.operator_id
            and str(self.sql_condition) == str(value.sql_condition)
        )

    def __ne__(self, value):
        return not self.__eq__(value)

    def __getitem__(self, key):
        if hasattr(self, key):
            return getattr(self, key)
        elif key in self.metadata:
            return self.metadata[key]
        raise KeyError(f"Key {key} not found in {self}.")

    def find_child(self, operator: LogicalOperator, sql_condition):

        c = [self]
        while c:
            op = c.pop()
            for k, child in op.children.items():
                if (
                    operator.operator_id == op.operator.operator_id
                    and op.sql_condition == sql_condition
                ):
                    return child

                if isinstance(child, Constraint):
                    c.append(child)
        return None

    def add_child(
        self,
        operator: LogicalOperator,
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
                sql_condition=sql_condition,
                operator=operator,
                metadata={**kwargs},
            )
            self.children[bit] = child_node
            child_node._create_plausible_siblings(branch=branch)
        return child_node

    def update_delta(
        self,
        bit: PlausibleBit,
        symbolic_expr: Union[List[Symbol], Symbol],
        rowids,
        branch,
    ):
        p = symbolic_expr if isinstance(symbolic_expr, list) else [symbolic_expr]
        self.symbolic_exprs[bit].extend(p)
        self.delta[bit].append(rowids)
        for ridx in rowids:
            op_id = self.operator.operator_id
            self.tree._index_row(op_id, ridx, self, bit, branch)

    def _create_plausible_siblings(self, branch):
        for bit in self.plausible_bits:
            if bit in self.children:
                continue
            plausible = PlausibleBranch(self.tree, self, branch)
            self.children[bit] = plausible
            child_pattern = self.pattern()
            if child_pattern in self.tree.leaves:
                del self.tree.leaves[child_pattern]
            child_pattern = plausible.pattern()
            self.tree.leaves[child_pattern] = plausible
