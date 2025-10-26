from __future__ import annotations

from typing import List, TYPE_CHECKING, Union, Optional, Any, Dict

if TYPE_CHECKING:
    from .plan.expression import ExpOrStr
    from .plan.step import LogicalOperator
    from src.parseval.instance import Instance

import logging, random
import src.parseval.symbol as sym
import src.parseval.plan.rex as sql_exp

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
    NEGATIVE = "negative"  # Branch
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
        if self.parent is not None:
            for k, v in self.parent.children.items():
                if v is self:
                    return k
        return ""

    def hit(self):
        if self.parent is not None and self.parent.operator != "ROOT":
            return len(self.parent.symbolic_exprs[self.bit()])
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
        self._pattern = "".join(bits)
        return self._pattern

        # self._pattern = self.parent.pattern()
        return self._pattern

    # def __str__(self):
    #     pass

    # def __repr__(self):
    #     return str(self)


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

    def mark_negative(self):
        self.plausible_type = PlausibleType.NEGATIVE

    def mark_pending(self):
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
        if self.parent.delta[str(bit)]:
            self.plausible_type = (
                PlausibleType.POSITIVE
                if self.branch and bit == "1"
                else PlausibleType.NEGATIVE
            )

    def update_mark(self):
        if self.parent is None:
            return
        bit = self.bit()
        if bit == "4":
            if isinstance(self.parent.sql_condition, sql_exp.ColumnRef):
                if self.parent.sql_condition.metadata.get("unique", False):
                    self.plausible_type = PlausibleType.INFEASIBLE
                    return
                else:
                    values = [v.concrete for v in self.parent.symbolic_exprs["1"]]
                    if len(values) != len(set(values)):
                        self.plausible_type = PlausibleType.COVERED
                        self.parent.symbolic_exprs["4"].extend(values)
        if bit == "3":
            if (
                self.parent.sql_condition.datatype
                and self.parent.sql_condition.datatype.nullable is False
            ):
                self.plausible_type = PlausibleType.INFEASIBLE
            else:
                values = [
                    v for v in self.parent.symbolic_exprs["1"] if v.concrete is None
                ]
                if values:
                    self.plausible_type = PlausibleType.COVERED
                    self.parent.symbolic_exprs["3"].extend(values)

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
        ref_condition: Optional[ExpOrStr] = None,
        sql_condition: Optional[ExpOrStr] = None,
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
                behavior=ConstraintBehavior.PROJECTION,
                should_negate=False,
                plausible_bits=(1,),
            )
        if operator.operator_type == "Join":
            return self.CONSTRAINT_CONFIGS["join"]

        if isinstance(sql_condition, sql_exp.Predicate):
            return self.CONSTRAINT_CONFIGS["predicate"]
        elif isinstance(sql_condition, sql_exp.ColumnRef):
            return self.CONSTRAINT_CONFIGS["project"]

        if operator.operator_type == "Filter":
            return self.CONSTRAINT_CONFIGS["predicate"]
        if operator.operator_type == "Project":
            return self.CONSTRAINT_CONFIGS["project"]
        if operator.operator_type == "Aggregate":
            return self.CONSTRAINT_CONFIGS["aggregate"]
        if operator.operator_type == "Sort":
            return ConstraintConfig(
                ConstraintBehavior.PROJECTION, should_negate=False, plausible_bits=(1,)
            )

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
        ref_condition: ExpOrStr,
        sql_condition: ExpOrStr,
        bit: str,
        branch: bool,
        **kwargs,
    ):
        child_node = self.children.get(str(bit), None)
        if child_node is None or isinstance(child_node, PlausibleBranch):
            child_node = Constraint(
                tree=self.tree,
                parent=self,
                ref_condition=ref_condition,
                sql_condition=sql_condition,
                operator=operator,
                branch=branch,
                **kwargs,
            )
            self.children[str(bit)] = child_node
            child_node._create_plausible_siblings()

        return child_node

    def update_delta(
        self, bit, symbolic_expr: Union[List[sym.Symbol], sym.Symbol], rows
    ):

        p = symbolic_expr if isinstance(symbolic_expr, list) else [symbolic_expr]

        self.symbolic_exprs[str(bit)].extend(p)
        self.delta[str(bit)].append(rows)
        if isinstance(self.children[str(bit)], PlausibleBranch):
            self.children[str(bit)].mark_positive_negative()

    def _create_plausible_siblings(self):
        for bit in self.config.plausible_bits:
            logging.info(f"Creating plausible sibling for {self} bit: {bit}")
            bit_str = str(bit)
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
            # parent_pattern = self.pattern()
            # if parent_pattern in self.tree.leaves:
            #     del self.tree.leaves[parent_pattern]
            child_pattern = self.pattern()
            if child_pattern in self.tree.leaves:
                del self.tree.leaves[child_pattern]
            child_pattern = plausible.pattern()  # cached pattern for child
            logging.info(
                f"child pattern to add: {child_pattern}, bit: {bit_str}, parent pattern: {self.pattern()}"
            )

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

        self.positive_nodes["ROOT"].add((self.root_constraint, "1"))

        self.prev_operator: Optional[LogicalOperator] = "ROOT"
        self.declare = declare
        self.threshold = threshold

    def advance(self, operator: LogicalOperator):
        """move the current path forward by one step"""
        if operator.id in self.positive_nodes:
            self.prev_operator = operator.id
        for pattern in self.leaves:
            leaf = self.leaves[pattern]
            if not isinstance(leaf, PlausibleBranch):
                continue

            if leaf.hit() >= self.threshold:
                if not leaf.branch:
                    leaf.mark_negative()

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
        ref_conditions: List[ExpOrStr],
        sql_conditions: List[ExpOrStr],
        symbolic_exprs: List[sym.Symbol],
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
            b = str(bit)
            for index, (ref_condition, sql_condition) in enumerate(
                zip(ref_conditions, sql_conditions)
            ):
                node = node.add_child(
                    operator=operator,
                    ref_condition=ref_condition,
                    sql_condition=sql_condition,
                    bit=b,
                    branch=branch,
                    **kwargs,
                )

                taken = takens[index]
                smt_expr = symbolic_exprs[index] if index < len(symbolic_exprs) else []
                b = str(int(taken))
                node.update_delta(b, smt_expr, rows)

            if branch and (node, b) not in self.positive_nodes[operator.id]:
                self.positive_nodes[operator.id].add((node, b))

    def next_path(self):
        logging.info(f"number of leavs: {len(self.leaves)}")
        for pattern in self.leaves:
            leaf = self.leaves[pattern]
            if not isinstance(leaf, PlausibleBranch):
                raise ValueError("Expected PlausibleBranch in leaves")
                continue
            leaf.update_mark()
        for pattern, leaf in self.leaves.items():
            if leaf.branch and leaf.plausible_type == PlausibleType.UNEXPLORED:
                logging.info(
                    f"Selecting unexplored positive leaf: ========================= {pattern}"
                )
                return leaf

        leaves = dict(
            sorted(self.leaves.items(), key=lambda item: len(item[0]), reverse=True)
        )
        for pattern, leaf in leaves.items():
            if not isinstance(leaf, PlausibleBranch):
                continue
            if leaf.plausible_type in {
                PlausibleType.INFEASIBLE,
                PlausibleType.NEGATIVE,
            }:
                continue
            if pattern.endswith("2"):
                continue

            if leaf.plausible_type == PlausibleType.UNEXPLORED:
                leaf.mark_pending()
                assert pattern == leaf.pattern(), f"{pattern} vs {leaf.pattern()}"
                return leaf
        return None

    def _append_tuple(self, instance: Instance, plausible: PlausibleBranch):
        """
        insert a new tuple into the instance
        Declare new symbols.
        we do not need to change all existing concrete values, hence, we just need to call a solver to solve constraints to derive concrete values for new symbols.
        """
        # involved_tables = self._get_involved_tables_path(plausible)

        path = plausible.get_path_to_root()

        if plausible.pattern().endswith("4"):
            logging.info(plausible.pattern())

        ### we first process constraints in the path to root
        for bit, node in zip(plausible.pattern(), path[1:]):
            if plausible.pattern().endswith("4"):
                logging.info("==== Processing constraint node ====")
                logging.info(f"bit: {bit}, node: {node.sql_condition}")
            if str(bit) == "4":
                if isinstance(node.sql_condition, sql_exp.ColumnRef):
                    if (
                        not node.sql_condition.metadata.get("unique", False)
                        and node.symbolic_exprs
                    ):
                        value = random.choice(node.symbolic_exprs["1"])
                        dup_constraint = sql_exp.Predicate(
                            node.sql_condition,
                            "=",
                            sql_exp.Literal(
                                value=value.concrete,
                                datatype=node.sql_condition.datatype,
                            ),
                        )
                        logging.info(
                            f"Handling duplicate constraint: {node.sql_condition}, {str(dup_constraint)}"
                        )
                        self.declare(node.operator.operator_type, dup_constraint)
            elif str(bit) == "3":
                null_constraint = node.sql_condition.is_null()
                logging.info(
                    f"Handling null constraint: {node.sql_condition}, {type(node.sql_condition)}, {null_constraint}"
                )
                self.declare(node.operator.operator_type, null_constraint)
            elif str(bit) == "0":
                if isinstance(node.sql_condition, sql_exp.Predicate):
                    pos_constraint = node.sql_condition.negate()
                    self.declare(node.operator.operator_type, pos_constraint)
            else:
                self.declare(node.operator.operator_type, node.sql_condition)

        ## Then process the constraint node itself
