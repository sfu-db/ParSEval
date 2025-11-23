from __future__ import annotations
from typing import List, Optional, Dict, Union, Set, Any, Tuple, TYPE_CHECKING, Optional
from src.parseval.symbol import Variable
from .helper import group_by_concrete
from src.parseval.plan import rex
from sqlglot.expressions import convert, AggFunc, Predicate

if TYPE_CHECKING:
    from .plan.rex import Expression as Expression
    from .plan.rex import LogicalOperator
    from src.parseval.instance import Instance
    from src.parseval.symbol import Symbol as Symbol

import logging, random

from enum import Enum, IntEnum, auto
from collections import defaultdict

from dataclasses import dataclass


NULL_THRESHOLD = 1
DUPLICATE_THRESHOLD = 1


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


class _ScopeManager:
    """
    Internal context manager class to handle saving and restoring the Trace state.
    This ensures state is always reset correctly, even upon exception.
    """

    def __init__(self, trace_instance: "UExprToConstraint", operator: LogicalOperator):
        self.trace = trace_instance
        self.operator = operator

    def __enter__(self) -> "UExprToConstraint":
        """
        Saves the current active node onto the internal state stack.
        """
        return self.trace

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Restores the active node from the stack, guaranteed to run.
        """
        for pattern in self.trace.leaves:
            leaf = self.trace.leaves[pattern]
            if not isinstance(leaf, PlausibleBranch):
                continue
            leaf.update_mark()
        if self.operator.operator_id in self.trace.positive_nodes:
            self.trace.prev_operator = self.operator.operator_id
        return False


def check_cover_duplicate(current_plausible) -> bool:
    """Check if the constraint covers duplicate values."""
    bit = current_plausible.bit()
    current_label = current_plausible.plausible_type
    constraint: Constraint = current_plausible.parent
    columnrefs = list(constraint.sql_condition.find_all(rex.ColumnRef))
    if not columnrefs or all(
        [columnref.args.get("unique", False) for columnref in columnrefs]
    ):
        return PlausibleType.INFEASIBLE

    variables = []
    for smt_expr in constraint.symbolic_exprs[PBit.TRUE]:
        variables.extend(smt_expr.find_all(Variable))

    constraint.symbolic_exprs[bit].clear()
    groups = group_by_concrete(variables)
    duplicates_found = False
    for key, items in groups.items():
        if len(items) > current_plausible.metadata.get(
            "DUPLICATE_THRESHOLD", DUPLICATE_THRESHOLD
        ):
            duplicates_found = True
            constraint.symbolic_exprs[bit].append(items[0])
    return PlausibleType.COVERED if duplicates_found else current_label


def check_cover_null(current_plausible) -> bool:
    """Check if the constraint covers null values."""
    bit = current_plausible.bit()
    label = current_plausible.plausible_type

    constraint: Constraint = current_plausible.parent
    columnrefs = list(constraint.sql_condition.find_all(rex.ColumnRef))
    if not columnrefs or all(
        [columnref.datatype.nullable is False for columnref in columnrefs]
    ):
        return PlausibleType.INFEASIBLE

    constraint.symbolic_exprs[bit].clear()
    for smt in constraint.symbolic_exprs[PBit.TRUE]:
        for var in smt.find_all(Variable):
            if var.concrete is None:
                constraint.symbolic_exprs[bit].append(smt)
                cover_null = True
    cover_null = False
    if len(constraint.symbolic_exprs[bit]) > NULL_THRESHOLD:
        cover_null = True
    return PlausibleType.COVERED if cover_null else label


def check_cardinality(current_plausible) -> bool:
    """Check if the constraint covers cardinality conditions."""
    bit = current_plausible.bit()
    label = current_plausible.plausible_type
    constraint: Constraint = current_plausible.parent
    if constraint.sql_condition.args.get("unique", False):
        return PlausibleType.COVERED
    values = [v.concrete for v in constraint.symbolic_exprs[PBit.TRUE]]
    filtered = list(filter(lambda x: x is not None, values))
    if not filtered:
        return PlausibleType.UNEXPLORED
    logging.info(f"check cardinality values: {filtered}")
    min_ = min(filtered)
    max_ = max(filtered)
    if max_ == min_:
        return PlausibleType.UNEXPLORED
    if bit == PBit.MAX and values.count(max_) > 1:
        constraint.symbolic_exprs[bit] = [
            v for v in constraint.symbolic_exprs[PBit.TRUE] if v.concrete == max_
        ]
        return PlausibleType.COVERED
    if bit == PBit.MIN and values.count(min_) > 1:
        constraint.symbolic_exprs[bit] = [
            v for v in constraint.symbolic_exprs[PBit.TRUE] if v.concrete == min_
        ]
        return PlausibleType.COVERED
    return label


def check_groupcount(current_plausible) -> bool:
    """Check if the constraint covers group count conditions."""
    bit = current_plausible.bit()
    label = current_plausible.plausible_type
    constraint: Constraint = current_plausible.parent

    groups = constraint.symbolic_exprs[PBit.TRUE]
    groups = group_by_concrete(groups)
    if len(groups) > 1:
        constraint.symbolic_exprs[bit].clear()
        constraint.symbolic_exprs[bit].extend(groups)
        return PlausibleType.COVERED
    return label


def check_groupsize(plausible):
    bit = plausible.bit()
    label = plausible.plausible_type
    constraint: Constraint = plausible.parent
    groups = constraint.metadata.get("group")

    if groups is None:
        return label

    constraint.symbolic_exprs[bit].clear()
    for group in groups:
        if len(group) > 1:
            constraint.symbolic_exprs[bit].append(group)
    return PlausibleType.COVERED if constraint.symbolic_exprs[bit] else label


class PlausibleBranch(_Constraint):
    """Represents an unexplored but potentially reachable branch in the constraint tree.

    A PlausibleNode is a placeholder that says "this branch could be taken,
    but we haven't explored it yet." It helps track coverage gaps.

    """

    LABEL_STRATEGIES = {
        PBit.DUPLICATE: check_cover_duplicate,
        PBit.NULL: check_cover_null,
        PBit.TRUE: lambda self: (
            PlausibleType.COVERED
            if self.parent.symbolic_exprs[PBit.TRUE]
            else self.plausible_type
        ),
        PBit.FALSE: lambda self: (
            PlausibleType.COVERED
            if self.parent.symbolic_exprs[PBit.FALSE]
            else self.plausible_type
        ),
        PBit.MAX: check_cardinality,
        PBit.MIN: check_cardinality,
        PBit.GROUP_COUNT: check_groupcount,
        PBit.GROUP_SIZE: check_groupsize,
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
        vaild_bits = (PBit.FALSE, PBit.TRUE, PBit.JOIN, PBit.GROUP_SIZE)
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
        if bit in self.LABEL_STRATEGIES:
            strategy = self.LABEL_STRATEGIES[bit]
            self.plausible_type = strategy(self)

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
        "join": (PBit.TRUE, PBit.FALSE, PBit.JOIN),
        "project": (PBit.TRUE, PBit.NULL, PBit.DUPLICATE),
        "groupby": (PBit.TRUE, PBit.GROUP_SIZE, PBit.GROUP_COUNT),
        "aggregate": (PBit.TRUE, PBit.NULL, PBit.DUPLICATE),
        "predicate": (PBit.FALSE, PBit.TRUE),
        "sort": (PBit.TRUE, PBit.MAX, PBit.MIN),
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
        self.metadata = metadata or {}

    @property
    def plausible_bits(self) -> Tuple[PBit, ...]:
        if self.operator is None or self.operator == "ROOT":
            return (PBit.TRUE,)
        if self.operator.operator_type == "Aggregate":
            if isinstance(self.sql_condition, AggFunc):
                return self.PLAUSIBLE_CONFIGS["aggregate"]
            return self.PLAUSIBLE_CONFIGS["groupby"]
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
            isinstance(self.sql_condition, rex.ColumnRef)
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
        # Ensure all attributes used here are immutable (like strings, numbers, tuples, or other hashable objects)
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

    def update_metadata(self, **kwargs):
        for k, v in kwargs.items():
            if k not in self.metadata:
                self.metadata[k] = v
            else:
                if isinstance(self.metadata[k], dict) and isinstance(v, dict):
                    self.metadata[k].update(v)
                elif isinstance(self.metadata[k], list) and isinstance(v, list):
                    self.metadata[k].extend(v)
                elif isinstance(self.metadata[k], set) and isinstance(v, set):
                    self.metadata[k].update(v)
                else:
                    self.metadata[k] = v

    def update_delta(
        self, bit: PlausibleBit, symbolic_expr: Union[List[Symbol], Symbol], rows
    ):
        p = symbolic_expr if isinstance(symbolic_expr, list) else [symbolic_expr]
        self.symbolic_exprs[bit].extend(p)
        self.delta[bit].append(rows)

    def _create_plausible_siblings(self, branch):
        for bit in self.plausible_bits:
            if bit in self.children:
                continue
            plausible = PlausibleBranch(self.tree, self, branch)
            self.children[bit] = plausible
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
        self.positive_nodes["ROOT"].add((self.root_constraint, PBit.TRUE))
        self.prev_operator: Optional[LogicalOperator] = "ROOT"
        self.declare = declare
        self.threshold = threshold

    def scope(self, operator: LogicalOperator) -> _ScopeManager:
        """context manager for scoping the trace to a specific operator"""
        return _ScopeManager(self, operator)

    def reset(self):
        self.prev_operator = "ROOT"
        c = [self.root_constraint]
        while c:
            op = c.pop()
            op.symbolic_exprs.clear()
            op.delta.clear()
            op.metadata.clear()

            for k, child in op.children.items():
                if isinstance(child, Constraint):
                    c.append(child)

    def which_path(
        self,
        operator: LogicalOperator,
        sql_conditions: List[Expression],
        symbolic_exprs: List[Union[List[Symbol], Symbol]],
        takens: List[bool],
        rows: Any,
        branch: Union[bool, str],
        **kwargs,
    ):
        assert len(sql_conditions) == len(takens) and len(sql_conditions) == len(
            symbolic_exprs
        ), "Conditions and takens length mismatch"
        operator_id = operator.operator_id
        for start, bit in self.positive_nodes[self.prev_operator]:
            node, b = start, bit
            for index, (sql_condition, taken) in enumerate(zip(sql_conditions, takens)):
                node = node.add_child(
                    operator=operator,
                    sql_condition=sql_condition,
                    bit=b,
                    branch=branch,
                    **kwargs,
                )
                smt_expr = symbolic_exprs[index] if index < len(symbolic_exprs) else []
                b = PlausibleBit.from_int(taken)
                node.update_delta(b, smt_expr, rows)
                node.update_metadata(**kwargs)
            if branch and (node, b) not in self.positive_nodes[operator.operator_id]:
                self.positive_nodes[operator_id].add((node, b))

    def next_path(self):
        leaves = dict(
            sorted(
                (
                    (pattern, leaf)
                    for pattern, leaf in self.leaves.items()
                    if leaf.plausible_type
                    not in {PlausibleType.INFEASIBLE, PlausibleType.COVERED}
                    and leaf.attempts <= self.threshold
                ),
                key=lambda item: len(item[0]),
                reverse=False,
            )
        )
        for pattern, leaf in leaves.items():
            if pattern[-1] == PBit.JOIN or leaf.attempts > self.threshold:
                continue

            if leaf.plausible_type in {PlausibleType.UNEXPLORED, PlausibleType.PENDING}:
                leaf.mark_pending()
                return leaf
        return None

    def _declare_smt_constraints(self, plausible: PlausibleBranch):
        """
        declare SMT constraints for the plausible branch
        """
        path = plausible.get_path_to_root()
        path = list(reversed(path[1:]))
        patterns = list(reversed(plausible.pattern()))
        # ### we first process constraints in the path to root

        context = {"has_having": False, "patterns": patterns}

        declarers = {
            PBit.DUPLICATE: self._declare_duplicate_constraints,
            PBit.NULL: self._declare_null_constraints,
            PBit.GROUP_COUNT: self._declare_group_count_constraints,
            PBit.GROUP_SIZE: self._declare_group_size_constraints,
            PBit.MAX: self._declare_sortmax_constraints,
            PBit.MIN: self._declare_sortmin_constraints,
        }

        for bit, node in zip(patterns, path[1:]):
            if context["has_having"]:
                break
            logging.info(f"Declaring constraint for bit: {bit}, node: {node}")
            if bit in declarers:

                declarers[bit](node, context)
            if bit is PBit.FALSE:
                if node.operator.operator_type in {"Having"}:
                    continue
                elif isinstance(node.sql_condition, rex.sqlglot_exp.Predicate):
                    pos_constraint = rex.negate_predicate(node.sql_condition)
                    self.declare(node.operator.operator_type, pos_constraint)
            else:
                if node.operator.operator_type in {"Having"}:

                    self._declare_having_constraints(node, bit, context)
                else:
                    self.declare(node.operator.operator_type, node.sql_condition)

    def _declare_duplicate_constraints(self, node, context):
        if isinstance(node.sql_condition, rex.ColumnRef):
            if not node.sql_condition.args.get("unique", False) and node.symbolic_exprs:
                constraint = node.sql_condition
                value_counts = group_by_concrete(node.symbolic_exprs[PBit.TRUE])
                if value_counts:
                    values = sorted(value_counts.items(), key=lambda x: -len(x[1]))
                    value = values[0][1][0]
                    literal = convert(value.concrete)
                    literal.set("datatype", node.sql_condition.datatype)
                    constraint = rex.sqlglot_exp.EQ(
                        this=node.sql_condition, expression=literal
                    )
                self.declare(node.operator.operator_type, constraint)

    def _declare_null_constraints(self, node, context):
        columnrefs = list(node.sql_condition.find_all(rex.ColumnRef))
        for columnref in columnrefs:
            if columnref.datatype and columnref.datatype.nullable:
                null_constraint = rex.Is_Null(this=columnref)
                self.declare(node.operator.operator_type, null_constraint)
                return

    def _declare_smt_join_constraints(self, plausible: PlausibleBranch):
        """
        declare SMT constraints for the plausible branch
        """
        path = plausible.get_path_to_root()
        ### we first process constraints in the path to root
        for bit, node in zip(plausible.pattern(), path[1:]):
            if bit == PBit.FALSE:
                if isinstance(node.sql_condition, rex.sqlglot_exp.Predicate):
                    pos_constraint = rex.negate_predicate(node.sql_condition)
                    self.declare(node.operator.operator_type, pos_constraint)
            else:
                self.declare(node.operator.operator_type, node.sql_condition)

    def _declare_group_count_constraints(self, node, context):
        """declare SMT constraints for group count"""
        for value in node.symbolic_exprs[PBit.TRUE]:
            literal = convert(value.concrete)
            literal.set("datatype", node.sql_condition.datatype)
            constraint = rex.sqlglot_exp.NEQ(
                this=node.sql_condition, expression=literal
            )
            self.declare(node.operator.operator_type, constraint)

    def _declare_group_size_constraints(self, node, context):
        """declare SMT constraints for group size"""
        group_key = node.symbolic_exprs[PBit.TRUE][-1]
        literal = convert(group_key.concrete)
        literal.set("datatype", node.sql_condition.datatype)
        constraint = rex.sqlglot_exp.EQ(this=node.sql_condition, expression=literal)
        self.declare(node.operator.operator_type, constraint)

    def _declare_sortmax_constraints(self, node, context):
        if node.sql_condition.args.get("unique", False):
            return
        values = node.symbolic_exprs[PBit.TRUE]
        max_ = max([v.concrete for v in values if v.concrete is not None])
        min_ = min([v.concrete for v in values if v.concrete is not None])

        max_literal = convert(max_)
        max_literal.set("datatype", node.sql_condition.args.get("datatype"))
        if max_ == min_:
            klass = rex.sqlglot_exp.NEQ
        else:
            klass = rex.sqlglot_exp.EQ
        constraint = klass(
            this=node.sql_condition,
            expression=max_literal,
        )
        self.declare(node.operator.operator_type, constraint)

    def _declare_sortmin_constraints(self, node, context):
        if node.sql_condition.args.get("unique", False):
            return
        values = node.symbolic_exprs[PBit.TRUE]
        max_ = max([v.concrete for v in values if v.concrete is not None])
        min_ = min([v.concrete for v in values if v.concrete is not None])
        min_literal = convert(min_)
        min_literal.set("datatype", node.sql_condition.datatype)
        if max_ == min_:
            klass = rex.sqlglot_exp.NEQ
        else:
            klass = rex.sqlglot_exp.EQ
        constraint = klass(
            this=node.sql_condition,
            expression=min_literal,
        )
        self.declare(node.operator.operator_type, constraint)

    def _declare_having_constraints(self, node, bit, context):
        """declare SMT constraints for having clause"""
        patterns = context["patterns"]
        if bit is not patterns[-1]:
            context["has_having"] = True
            return

        # if not list(node.sql_condition.find_all(rex.ColumnRef)):
        #     node.children[bit].mark_infeasible()
        #     return

        b = PBit.FALSE if bit is PBit.TRUE else PBit.FALSE

        if not node.symbolic_exprs[b]:
            return

        # logging.info(f"procesing {bit}, {b} having: {node.sql_condition}")
        # node.symbolic_exprs[b][0]
        # if bit is PBit.TRUE:
        #     ...
        # else:
        #     ...

        # pattern = "".join([str(b) for b in context["patterns"]])
        # groups = []
        # for group in node.symbolic_exprs[PBit.TRUE]:
        #     group_name = group.name
        #     assigned_pattern = node.metadata.get("group", {}).get(group_name)
        #     if assigned_pattern is None:
        #         groups.append(group)
        # if groups:
        #     group = groups[-1]
        #     node.metadata.setdefault("group", {})[group.name] = pattern

        # logging.info(f"Declaring having constraint for group: for {node.sql_condition}")

        # if isinstance(node.sql_condition, rex.sqlglot_exp.Predicate):
        #     pos_constraint = rex.negate_predicate(node.sql_condition)
        #     self.declare(node.operator.operator_type, pos_constraint)
