from __future__ import annotations

from typing import List, TYPE_CHECKING, Union, Optional, Any, Dict
from src.parseval.symbol import Variable
from .helper import group_by_concrete

if TYPE_CHECKING:
    from .plan.rex import Expression as Expression
    from .plan.rex import LogicalOperator
    from src.parseval.instance import Instance
    from src.parseval.symbol import Symbol as Symbol

import logging, random

# import src.parseval.symbol as sym
import src.parseval.plan.rex as sql_exp

from enum import Enum, IntEnum, auto
from collections import defaultdict

from dataclasses import dataclass
from typing import List, Optional, Dict, Union, Set, Any, Tuple


class PlausibleBit(IntEnum):
    """Bits representing different plausible branches."""

    FALSE = 0  # e.g., if condition is false
    TRUE = 1  # e.g., if condition is true
    JOIN = 2  # e.g., threr exist tuple in right table cannot join with left table
    NULL = 3  # e.g., column is null
    DUPLICATE = 4  # e.g., duplicate values exist
    MAX = 5  # e.g., number of max value
    MIN = 6  # e.g., number of  min value
    GROUP_COUNT = 7  # e.g., number of groups
    GROUP_SIZE = 8  # e.g., size of groups(count of rows in each group)

    @classmethod
    def from_int(cls, value: Union[int, str, bool, PlausibleBit]) -> "PlausibleBit":
        if isinstance(value, PlausibleBit):
            return value
        return cls(int(value))

    def __str__(self):
        return str(self.value)


PBit = PlausibleBit


class PlausibleType(Enum):
    """Types of plausible (unexplored) branches."""

    POSITIVE = "positive"  # Branch
    COVERED = "covered"  # Branch already covered
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

    should_negate: bool = True
    # Possible bits for branches (e.g., 0, 1 for if/else, 2 for join, 3 for null, 4 for duplicate, 5 for max, 6 for min)
    plausible_bits: Tuple[int, ...] = (PBit.FALSE, PBit.TRUE)

    @classmethod
    def for_predicate(cls) -> ConstraintConfig:
        """
        Standard if/else predicate (WHERE, HAVING, filter).
        Bits: 0=false, 1=true
        """
        return cls(
            plausible_bits=(PBit.FALSE, PBit.TRUE),
        )

    @classmethod
    def for_join(cls) -> ConstraintConfig:
        return cls(plausible_bits=(PBit.FALSE, PBit.TRUE, PBit.JOIN))

    @classmethod
    def for_project(cls) -> ConstraintConfig:
        return cls(
            plausible_bits=(
                PBit.TRUE,
                PBit.NULL,
                PBit.DUPLICATE,
            ),
            should_negate=False,
        )

    @classmethod
    def for_sort(cls) -> ConstraintConfig:
        return cls(
            plausible_bits=(PBit.TRUE, PBit.MAX, PBit.MIN),
            should_negate=False,
        )

    @classmethod
    def for_groupby(cls) -> ConstraintConfig:
        return cls(
            plausible_bits=(
                PBit.TRUE,
                PBit.GROUP_COUNT,
                PBit.GROUP_SIZE,
            ),
            should_negate=False,
        )

    @classmethod
    def for_aggregate(cls) -> ConstraintConfig:
        return cls(
            plausible_bits=(
                PBit.TRUE,
                PBit.NULL,
                PBit.DUPLICATE,
            ),
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

    def bit(self) -> PlausibleBit:
        if self.parent is not None:
            for k, v in self.parent.children.items():
                if v is self:
                    return k

    def hit(self):
        if self.parent is not None and self.parent.operator != "ROOT":
            bit = self.bit()
            return len(self.parent.symbolic_exprs[bit])
        return 0

    def get_path_to_root(self) -> List[Constraint]:
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


def check_cover_duplicate(current_plausible) -> bool:
    """Check if the constraint covers duplicate values."""
    bit = current_plausible.bit()
    current_label = current_plausible.plausible_type
    constraint: Constraint = current_plausible.parent

    if isinstance(constraint.sql_condition, sql_exp.ColumnRef):
        if constraint.sql_condition.args.get("unique", False):
            return PlausibleType.INFEASIBLE
        elif constraint.symbolic_exprs[bit]:
            return PlausibleType.COVERED
        else:
            indices = defaultdict(list)
            for i, v in enumerate(constraint.symbolic_exprs[PBit.TRUE]):
                if v.concrete is not None:
                    indices[v.concrete].append(i)
            duplicates = {v: idxs for v, idxs in indices.items() if len(idxs) > 1}
            if duplicates:
                for v, idxs in duplicates.items():
                    for idx in idxs:
                        smt = constraint.symbolic_exprs[PBit.TRUE][idx]
                        constraint.symbolic_exprs[bit].append(smt)
                return PlausibleType.COVERED
    return current_label


def check_cover_null(current_plausible) -> bool:
    """Check if the constraint covers null values."""
    bit = current_plausible.bit()
    label = current_plausible.plausible_type

    constraint: Constraint = current_plausible.parent
    columnrefs = list(constraint.sql_condition.find_all(sql_exp.ColumnRef))

    if all([columnref.datatype.nullable is False for columnref in columnrefs]):
        return PlausibleType.INFEASIBLE

    for columnref in columnrefs:
        if columnref.datatype.nullable is False:
            continue
        elif constraint.symbolic_exprs[bit]:
            return PlausibleType.COVERED
        else:
            for index, smt in enumerate(constraint.symbolic_exprs[PBit.TRUE]):
                for var in smt.find_all(Variable):
                    if var.concrete is None:
                        constraint.symbolic_exprs[bit].append(smt)
                        label = PlausibleType.COVERED
                        break

    return label


def check_cardinality(current_plausible) -> bool:
    """Check if the constraint covers cardinality conditions."""
    bit = current_plausible.bit()
    label = current_plausible.plausible_type
    constraint: Constraint = current_plausible.parent
    values = group_by_concrete(constraint.symbolic_exprs[PBit.TRUE])
    if len(values) > 1:
        return PlausibleType.COVERED
    return label


class PlausibleBranch(_Constraint):
    """Represents an unexplored but potentially reachable branch in the constraint tree.

    A PlausibleNode is a placeholder that says "this branch could be taken,
    but we haven't explored it yet." It helps track coverage gaps.

    """

    LABEL_STRATEGIES = {
        PBit.DUPLICATE: check_cover_duplicate,
        PBit.NULL: check_cover_null,
        PBit.FALSE: lambda self: (
            PlausibleType.COVERED
            if self.parent.symbolic_exprs[PBit.FALSE]
            else self.plausible_type
        ),
        PBit.MAX: check_cardinality,
        PBit.MIN: check_cardinality,
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
        self.branch = branch
        self.attempts = 0
        self.metadata = metadata or {}
        self.is_feasible: Optional[bool] = None
        self.plausible_type = plausible_type or PlausibleType.UNEXPLORED

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

    def mark_positive_negative(self):
        bit = self.bit()
        if self.parent.delta[bit]:
            self.plausible_type = (
                PlausibleType.POSITIVE
                if self.branch and bit == PBit.TRUE
                else PlausibleType.COVERED
            )

    def update_mark(self):
        if self.parent is None:
            return
        bit = self.bit()
        for bit, strategy in self.LABEL_STRATEGIES.items():
            if bit == self.bit():
                self.plausible_type = strategy(self)

    def __str__(self):
        return (
            f"PlausibleNode( "
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
        ref_condition: Optional[Expression] = None,
        sql_condition: Optional[Expression] = None,
        branch: Optional[bool] = True,
        subquery=None,
        metadata: Optional[Dict[str, Any]] = None,
        config: Optional[ConstraintConfig] = None,
    ):
        super().__init__(tree, parent)
        self.operator = operator
        self.children = children or {}

        self.ref_condition = ref_condition
        self.sql_condition = sql_condition
        self.symbolic_exprs = defaultdict(list)
        self.delta = defaultdict(list)
        self.branch = branch
        self.subquery = subquery
        self.metadata = metadata or {}
        self.config = config or self._infer_config(sql_condition, operator)

    def _infer_config(
        self, sql_condition, operator: Optional[LogicalOperator]
    ) -> ConstraintConfig:
        if operator is None or operator == "ROOT":
            return ConstraintConfig(
                should_negate=False,
                plausible_bits=(1,),
            )
        if operator.operator_type == "Join":
            return self.CONSTRAINT_CONFIGS["join"]

        if isinstance(sql_condition, sql_exp.sqlglot_exp.Predicate):
            return self.CONSTRAINT_CONFIGS["predicate"]
        elif operator.operator_type == "Sort":
            return ConstraintConfig.for_sort()
        elif isinstance(sql_condition, sql_exp.ColumnRef):
            return self.CONSTRAINT_CONFIGS["project"]

        if operator.operator_type == "Filter":
            return self.CONSTRAINT_CONFIGS["predicate"]
        if operator.operator_type == "Project":
            return self.CONSTRAINT_CONFIGS["project"]
        if operator.operator_type == "Aggregate":
            if isinstance(sql_condition, tuple(sql_exp.AGG_FUNCS.values())):
                return self.CONSTRAINT_CONFIGS["aggregate"]

            return self.CONSTRAINT_CONFIGS["groupby"]

        raise ValueError(
            f"Cannot infer config for operator type: {operator.operator_type}"
        )

    def __str__(self):
        return f"Constraint({self.operator.operator_type if self.operator else 'ROOT'}, {self.ref_condition})"

    # def __hash__(self):
    #     if self._hash is None:
    #         self._hash = hash(
    #             (
    #                 self.operator.id if self.operator else None,
    #                 str(self.ref_condition) if self.ref_condition else None,
    #             )
    #         )
    #     return self._hash

    # def __eq__(self, value):
    #     if not isinstance(value, Constraint):
    #         return False

    #     return (
    #         self.operator.operator_type == value.operator.operator_type
    #         and self.operator.id == value.operator.id
    #         and str(self.ref_condition) == str(value.ref_condition)
    #     )

    # def __ne__(self, value):
    #     return not self.__eq__(value)

    def add_child(
        self,
        operator: LogicalOperator,
        ref_condition: Expression,
        sql_condition: Expression,
        bit: PlausibleBit,
        branch: bool,
    ):

        child_node = self.children.get(bit, None)
        if child_node is None or isinstance(child_node, PlausibleBranch):
            child_node = Constraint(
                tree=self.tree,
                parent=self,
                ref_condition=ref_condition,
                sql_condition=sql_condition,
                operator=operator,
                branch=branch,
            )
            self.children[bit] = child_node
            child_node._create_plausible_siblings()
        return child_node

    def update_delta(
        self, bit: PlausibleBit, symbolic_expr: Union[List[Symbol], Symbol], rows
    ):
        p = symbolic_expr if isinstance(symbolic_expr, list) else [symbolic_expr]
        self.symbolic_exprs[bit].extend(p)
        self.delta[bit].append(rows)

    def _create_plausible_siblings(self):
        for bit in self.config.plausible_bits:
            bit_str = bit
            if bit_str in self.children:
                continue
            plausible_type = None
            plausible = PlausibleBranch(
                tree=self.tree,
                parent=self,
                branch=self.branch,
                plausible_type=plausible_type,
            )
            self.children[bit_str] = plausible
            child_pattern = self.pattern()
            if child_pattern in self.tree.leaves:
                del self.tree.leaves[child_pattern]
            child_pattern = plausible.pattern()  # cached pattern for child
            self.tree.leaves[child_pattern] = plausible


class UExprToConstraint:

    def __init__(self, declare, threshold=1):
        """
        positive_nodes: a mapping from operator id to all positive path constraint nodes
        prev_operator: the last SQL operator we have seen
        """
        self.constraints = []
        self.leaves: Dict[str, Constraint] = {}
        self.root_constraint = Constraint(self, None, None)
        ## we use this positive_path to cache all positive paths' constraints.
        self.positive_nodes: Dict[str, Set[Constraint]] = defaultdict(set)
        self.positive_nodes["ROOT"].add(
            (self.root_constraint, PBit.TRUE)
        )  # (Node, bit)
        self.prev_operator: Optional[LogicalOperator] = "ROOT"
        self.declare = declare
        self.threshold = threshold

    def advance(self, operator: LogicalOperator):
        """move the current path forward by one step"""
        if operator.operator_id in self.positive_nodes:
            self.prev_operator = operator.operator_id

    def reset(self):
        self.prev_operator = "ROOT"
        c = [self.root_constraint]
        while c:
            op = c.pop()
            for k, child in op.children.items():
                if isinstance(child, Constraint):
                    child.symbolic_exprs.clear()
                    child.delta.clear()
                    c.append(child)

    def which_path(
        self,
        operator: LogicalOperator,
        ref_conditions: List[Expression],
        sql_conditions: List[Expression],
        symbolic_exprs: List[Union[List[Symbol], Symbol]],
        takens: List[bool],
        rows: Any,
        branch: Union[bool, str],
        **kwargs,
    ):
        # assert len(ref_conditions) == len(
        #     sql_conditions
        # ), "Conditions length mismatch in uexpr to constraint"
        for positive_node, bit in self.positive_nodes[self.prev_operator]:
            # Skip nodes that don't have relevant tuples (for non-root nodes)
            # if node.operator_key != "ROOT" and not node.get_all_tuples().intersection(
            #     tuples
            # ):
            #     continue
            node = positive_node
            b = bit
            for index, (ref_condition, sql_condition) in enumerate(
                zip(ref_conditions, sql_conditions)
            ):
                node = node.add_child(
                    operator=operator,
                    ref_condition=ref_condition,
                    sql_condition=sql_condition,
                    bit=b,
                    branch=branch,
                )
                taken = takens[index]
                smt_expr = symbolic_exprs[index] if index < len(symbolic_exprs) else []
                b = PlausibleBit.from_int(taken)
                node.update_delta(b, smt_expr, rows)

            if branch and (node, b) not in self.positive_nodes[operator.operator_id]:
                self.positive_nodes[operator.operator_id].add((node, b))
        for pattern in self.leaves:
            leaf = self.leaves[pattern]
            if not isinstance(leaf, PlausibleBranch):
                continue
            leaf.update_mark()

    def next_path(self):
        for pattern, leaf in self.leaves.items():
            if leaf.branch and leaf.plausible_type == PlausibleType.UNEXPLORED:
                if leaf.parent.operator.operator_type == "Join":
                    continue
                return leaf

        leaves = dict(
            sorted(self.leaves.items(), key=lambda item: len(item[0]), reverse=True)
        )
        for pattern, leaf in leaves.items():
            if not isinstance(leaf, PlausibleBranch):
                continue
            if leaf.plausible_type in {
                PlausibleType.INFEASIBLE,
                PlausibleType.COVERED,
            }:
                continue
            if pattern[-1] == PBit.JOIN:
                continue

            if leaf.plausible_type == PlausibleType.UNEXPLORED:
                leaf.mark_pending()
                return leaf
        return None

    def _declare_smt_constraints(self, plausible: PlausibleBranch):
        """
        declare SMT constraints for the plausible branch
        """
        path = plausible.get_path_to_root()
        # path = reversed(path[1:])

        # ### we first process constraints in the path to root
        # for bit, node in zip(plausible.pattern(), path):
        #     ...

        for bit, node in zip(plausible.pattern(), path[1:]):
            logging.info(f"Declaring SMT for bit {bit} at node {node.sql_condition}")
            if bit is PBit.DUPLICATE:
                self._declare_duplicate_constraints(node)
            elif bit is PBit.NULL:
                self._declare_null_constraints(node)
            elif bit is PBit.FALSE:
                if node.operator.operator_type == "aggregate":
                    self._declare_smt_group_constraints(plausible, node)
                elif isinstance(node.sql_condition, sql_exp.sqlglot_exp.Predicate):
                    pos_constraint = sql_exp.negate_predicate(node.sql_condition)
                    self.declare(node.operator.operator_type, pos_constraint)
            else:
                if node.operator.operator_type == "aggregate":
                    self._declare_smt_group_constraints(plausible, node)
                else:
                    self.declare(node.operator.operator_type, node.sql_condition)

    def _declare_duplicate_constraints(self, node):
        if isinstance(node.sql_condition, sql_exp.ColumnRef):
            if not node.sql_condition.args.get("unique", False) and node.symbolic_exprs:
                constraint = node.sql_condition
                value_counts = group_by_concrete(node.symbolic_exprs[PBit.TRUE])
                if value_counts:
                    from sqlglot.expressions import convert

                    values = sorted(value_counts.items(), key=lambda x: -len(x[1]))
                    value = values[0][1][0]
                    literal = convert(value.concrete)
                    literal.set("datatype", node.sql_condition.datatype)
                    constraint = sql_exp.sqlglot_exp.EQ(
                        this=node.sql_condition, expression=literal
                    )
                self.declare(node.operator.operator_type, constraint)

    def _declare_null_constraints(self, node):
        if isinstance(node.sql_condition, sql_exp.ColumnRef):
            if (
                node.sql_condition.datatype
                and node.sql_condition.datatype.nullable is False
            ):
                return
            null_constraint = sql_exp.Is_Null(this=node.sql_condition)
            self.declare(node.operator.operator_type, null_constraint)

    def _declare_smt_join_constraints(self, plausible: PlausibleBranch):
        """
        declare SMT constraints for the plausible branch
        """
        path = plausible.get_path_to_root()

        ### we first process constraints in the path to root
        for bit, node in zip(plausible.pattern(), path[1:]):
            if bit == PBit.FALSE:
                if isinstance(node.sql_condition, sql_exp.sqlglot_exp.Predicate):
                    pos_constraint = sql_exp.negate_predicate(node.sql_condition)
                    self.declare(node.operator.operator_type, pos_constraint)
            else:
                self.declare(node.operator.operator_type, node.sql_condition)

    def _declare_smt_group_constraints(self, plausible: PlausibleBranch, node):
        bit = str(plausible.bit())
        if bit == PBit.GROUP_COUNT:
            """declare SMT constraints for group count"""
            if node.symbolic_exprs.get(PBit.TRUE):
                for value in node.symbolic_exprs[PBit.TRUE]:
                    from sqlglot.expressions import convert

                    literal = convert(value.concrete)
                    literal.set("datatype", node.sql_condition.datatype)
                    constraint = sql_exp.sqlglot_exp.NE(
                        this=node.sql_condition, expression=literal
                    )
                    self.declare(node.operator.operator_type, constraint)
        elif bit == PBit.TRUE:
            """declare SMT constraints for group size"""
            if node.symbolic_exprs.get(PBit.TRUE):
                for value in node.symbolic_exprs[PBit.TRUE]:
                    from sqlglot.expressions import convert

                    literal = convert(value.concrete)
                    literal.set("datatype", node.sql_condition.datatype)
                    constraint = sql_exp.sqlglot_exp.EQ(
                        this=node.sql_condition, expression=literal
                    )
                    self.declare(node.operator.operator_type, constraint)
        else:
            raise NotImplementedError(
                f"Group by plausible branch bit {bit} not implement yet."
            )
