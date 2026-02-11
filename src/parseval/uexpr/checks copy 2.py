from __future__ import annotations

from abc import ABC, abstractmethod
from ..constants import PBit, PlausibleType
from typing import Optional, TYPE_CHECKING, Dict, Tuple, List, Any
from src.parseval.plan import (
    ColumnRef,
    Is_Null,
    Expression,
    negate_predicate,
)
from sqlglot import expressions as sqlglot_exp
from src.parseval.symbol import Variable, Const, Symbol

from src.parseval.helper import group_by_concrete, convert_to_literal

if TYPE_CHECKING:
    from src.parseval.uexpr import PlausibleBranch
    from src.parseval.uexpr.ptree import UExprToConstraint, Constraint
from functools import reduce
import logging

logger = logging.getLogger("parseval.coverage")

DUPLICATE_THRESHOLD = 1
NULL_THRESHOLD = 1
POSITIVE_THRESHOLD = 1
NEGATIVE_THRESHOLD = 1
GROUP_SIZE_THRESHOLD = 2


class Strategy(ABC):
    """Base strategy that implements both declaration and checking.

    Subclasses should implement `declare(tracer, node, context)` to emit
    constraints via `tracer.declare(...)`, and `check(plausible)` to
    determine the `PlausibleType` for a given `PlausibleBranch`.
    """

    def __init__(self, **kwargs):
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, key, value)

    def select_group(self, node: "Constraint", context: Dict):
        if isinstance(node.sql_condition, sqlglot_exp.AggFunc):
            groups = node.symbolic_exprs[PBit.GROUP_SIZE]
            for group in groups:
                if group.group_key and all(
                    v.concrete is not None for v in group.group_key.values()
                ):
                    context["groupid"] = group.rowids
                    return

    @abstractmethod
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> List[Expression]:
        pass

    @abstractmethod
    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        pass


class DuplicateStrategy(Strategy):
    def declare_agg_funcs(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ):
        logger.info(
            f"Declaring duplicate constraints for agg func: {node.sql_condition}"
        )
        constraints = []
        if isinstance(node.sql_condition, sqlglot_exp.AggFunc):
            for group in node.symbolic_exprs[PBit.GROUP_SIZE]:
                value_counts = group_by_concrete(group)
                logger.info(f"Group value counts: {value_counts}")
                if value_counts:
                    values = sorted(value_counts.items(), key=lambda x: len(x[1]))
                    value = values[0][1][0]
                    datatype = node.sql_condition.args.get("datatype")
                    if datatype is None:
                        datatype = value.datatype
                    literal = convert_to_literal(value.concrete, datatype)
                    constraint = sqlglot_exp.EQ(
                        this=node.sql_condition.this, expression=literal
                    )
                    logger.info(f"Declared duplicate constraint: {constraint}")
                    constraints.append(constraint)
                    context["groupid"] = group.rowids
                    break
        return constraints

    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> List[Expression]:
        if isinstance(node.sql_condition, sqlglot_exp.AggFunc):
            return self.declare_agg_funcs(tracer, node, context)
        constraints = []
        if isinstance(node.sql_condition, ColumnRef):
            if not node.sql_condition.args.get("unique", False) and node.symbolic_exprs:
                constraint = node.sql_condition
                value_counts = group_by_concrete(node.symbolic_exprs[PBit.TRUE])
                if value_counts:
                    values = sorted(value_counts.items(), key=lambda x: len(x[1]))
                    value = values[0][1][0]
                    literal = convert_to_literal(
                        value.concrete, node.sql_condition.datatype
                    )
                    constraint = sqlglot_exp.EQ(
                        this=node.sql_condition, expression=literal
                    )
                constraints.append(constraint)
                self.select_group(node, context)

        return constraints
        # tracer.declare(node.operator.operator_type, constraint)

    def _check_project(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        constraint: Constraint = plausible.parent
        columnrefs = list(constraint.sql_condition.find_all(ColumnRef))
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
            if len(items) > plausible.metadata.get(
                "DUPLICATE_THRESHOLD", DUPLICATE_THRESHOLD
            ):
                duplicates_found = True
                constraint.symbolic_exprs[bit].append(items[0])
        return PlausibleType.COVERED if duplicates_found else plausible.plausible_type

    def _check_aggregate(self, plausible: "PlausibleBranch") -> PlausibleType:

        bit = plausible.bit()
        node: Constraint = plausible.parent
        if isinstance(node.sql_condition, sqlglot_exp.AggFunc):
            columnrefs = list(node.sql_condition.find_all(ColumnRef))
            if not columnrefs or all(
                [columnref.args.get("unique", False) for columnref in columnrefs]
            ):
                return PlausibleType.INFEASIBLE
            duplicates_found = False
            for group in node.symbolic_exprs[PBit.GROUP_SIZE]:
                groups = group_by_concrete(group)
                for key, items in groups.items():
                    if len(items) > plausible.metadata.get(
                        "DUPLICATE_THRESHOLD", DUPLICATE_THRESHOLD
                    ):
                        duplicates_found = True
            return (
                PlausibleType.COVERED if duplicates_found else plausible.plausible_type
            )
        return plausible.plausible_type

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        """Check if the constraint covers duplicate values."""
        if plausible.plausible_type in {
            PlausibleType.COVERED,
            PlausibleType.INFEASIBLE,
        }:
            return plausible.plausible_type
        if plausible.parent.operator.operator_type == "Aggregate":
            return self._check_aggregate(plausible)
        return self._check_project(plausible)


class NullStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        constraints = []
        columnrefs = list(node.sql_condition.find_all(ColumnRef))
        for columnref in columnrefs:
            if columnref.datatype and columnref.datatype.nullable:
                null_constraint = Is_Null(this=columnref)
                constraints.append(null_constraint)
                self.select_group(node, context)
                break
                # tracer.declare(node.operator.operator_type, null_constraint)
                # return
        return constraints

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        label = plausible.plausible_type
        if label in {
            PlausibleType.COVERED,
            PlausibleType.INFEASIBLE,
            PlausibleType.TIMEOUT,
        }:
            return label

        constraint: Constraint = plausible.parent
        true_branche = (
            PBit.TRUE
            if constraint.operator.operator_type == "Project"
            else PBit.GROUP_SIZE
        )
        columnrefs = list(constraint.sql_condition.find_all(ColumnRef))
        if not columnrefs or all(
            [columnref.datatype.nullable is False for columnref in columnrefs]
        ):
            return PlausibleType.INFEASIBLE

        constraint.symbolic_exprs[bit].clear()
        for smt in constraint.symbolic_exprs[true_branche]:
            for var in smt.find_all(Variable):
                if var.concrete is None:
                    constraint.symbolic_exprs[bit].append(smt)
        cover_null = False
        if len(constraint.symbolic_exprs[bit]) > NULL_THRESHOLD:
            cover_null = True
        return PlausibleType.COVERED if cover_null else label
        # elif constraint.operator.operator_type == "Aggregate":
        #     for smt in constraint.symbolic_exprs[PBit.GROUP_SIZE]:
        #         for variable in smt.find_all(Variable):
        #             ...
        #         ...
        #     ...


class GroupCountStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> List[Expression]:
        sql_condition = node.sql_condition
        if not isinstance(sql_condition, ColumnRef):
            return []
        values = []

        for group in node.symbolic_exprs[PBit.GROUP_SIZE]:
            group_key = group.group_key[sql_condition]
            if group_key.concrete is not None:
                values.append(group_key)
        #     for kid, key_value in group.group_key.items():
        #         if key_value.concrete is None:
        #             continue

        #         if kid == sql_condition and key_value.concrete is not None:
        #             values.append(key_value)
        # for k, v in node.symbolic_exprs[PBit.GROUP_SIZE].items():
        #     if v.concrete is None:
        #         continue
        #     values.append(v)
        #     if v.concrete > 1:
        #         literal = convert_to_literal(
        #             v.concrete, node.sql_condition.args.get("datatype")
        #         )
        #         constraint = sqlglot_exp.EQ(this=node.sql_condition, expression=literal)
        #         return [constraint]

        # values = [
        #     v for v in node.symbolic_exprs[PBit.GROUP_SIZE] if v.concrete is not None
        # ]

        constraints = []

        for v in values:
            literal = convert_to_literal(v.concrete, v.datatype)
            constraints.append(sqlglot_exp.NEQ(this=sql_condition, expression=literal))
        return [reduce(lambda x, y: sqlglot_exp.AND(this=x, expression=y), constraints)]

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        """Check if the constraint covers group count conditions."""

        bit = plausible.bit()
        label = plausible.plausible_type
        constraint: Constraint = plausible.parent

        groups = constraint.symbolic_exprs[PBit.GROUP_SIZE]
        # groups = group_by_concrete(groups)
        if len(groups) > 1:
            constraint.symbolic_exprs[bit].clear()
            constraint.symbolic_exprs[bit].extend(groups)
            return PlausibleType.COVERED
        return label


class GroupSizeStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        sql_condition = node.sql_condition
        ### if this is group key part,
        if isinstance(sql_condition, ColumnRef):
            ## if we want to extend one group only, (i.e., context has groupid)
            if "groupid" in context:
                rowid = context["groupid"]
                for group in node.symbolic_exprs[PBit.GROUP_SIZE]:
                    if rowid == group.rowids:
                        group_key = group.group_key[sql_condition]

                        literal = convert_to_literal(
                            group_key.concrete, node.sql_condition.datatype
                        )
                        constraint = sqlglot_exp.EQ(
                            this=node.sql_condition, expression=literal
                        )
                        return [constraint]
            else:
                ## if there is no groupid, we pick one group with concrete key
                groups = node.symbolic_exprs[PBit.GROUP_SIZE]
                for group in node.symbolic_exprs[PBit.GROUP_SIZE]:
                    ## we only pick group with concrete value
                    if group.group_key and any(
                        v.concrete is None for v in group.group_key.values()
                    ):
                        continue

                    group_key = group.group_key[sql_condition]
                    literal = convert_to_literal(
                        group_key.concrete, node.sql_condition.datatype
                    )
                    constraint = sqlglot_exp.EQ(
                        this=node.sql_condition, expression=literal
                    )
                    context["groupid"] = group.rowids
                    return [constraint]
        elif isinstance(sql_condition, sqlglot_exp.AggFunc):
            self.select_group(node, context)
        return []

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        label = plausible.plausible_type
        constraint: Constraint = plausible.parent
        flag = True
        for group in constraint.symbolic_exprs[PBit.GROUP_SIZE]:
            if len(group) > GROUP_SIZE_THRESHOLD:
                constraint.symbolic_exprs[bit].append(group)
                return PlausibleType.COVERED
            else:
                flag = False

        return PlausibleType.COVERED if flag else label


class MinMaxStrategy(Strategy):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.is_max = kwargs.get("is_max", True)

    def declare(self, tracer, node, context):

        if node.sql_condition.args.get("unique", False):
            return []
        values = node.symbolic_exprs[PBit.TRUE]
        concretes = [v.concrete for v in values if v.concrete is not None]
        if not concretes:
            # tracer.declare(node.operator.operator_type, node.sql_condition)
            return [node.sql_condition]
        max_ = max(concretes)
        min_ = min(concretes)
        reference = max_ if self.is_max else min_
        ref_literal = convert_to_literal(
            reference, node.sql_condition.args.get("datatype")
        )
        if max_ == min_:
            klass = sqlglot_exp.NEQ
        else:
            klass = sqlglot_exp.EQ
        constraint = klass(
            this=node.sql_condition,
            expression=ref_literal,
        )
        return [constraint]
        tracer.declare(node.operator.operator_type, constraint)

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        """Check if the constraint covers cardinality conditions."""

        bit = plausible.bit()
        label = plausible.plausible_type
        constraint: Constraint = plausible.parent

        if constraint.sql_condition.args.get("unique", False):
            return PlausibleType.COVERED
        if not isinstance(constraint.sql_condition, ColumnRef):
            if constraint.operator.operator_type == "Sort":
                limit = constraint.operator.limit
                if limit is not None and limit <= len(
                    constraint.symbolic_exprs[PBit.TRUE]
                ):
                    return PlausibleType.COVERED
            return label
        values = [v.concrete for v in constraint.symbolic_exprs[PBit.TRUE]]
        filtered = list(filter(lambda x: x is not None, values))
        if not filtered:
            return PlausibleType.UNEXPLORED
        if constraint.operator.offset + constraint.operator.limit < len(values):
            return PlausibleType.UNEXPLORED
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


class SortMaxStrategy(MinMaxStrategy):
    def __init__(self, **kwargs):
        super().__init__(is_max=True, **kwargs)


class SortMinStrategy(MinMaxStrategy):
    def __init__(self, **kwargs):
        super().__init__(is_max=False, **kwargs)


class JoinTrueStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        """
        declare SMT constraints for join true
        """
        return [node.sql_condition]
        tracer.declare(node.operator.operator_type, node.sql_condition)

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        # No special check implemented for join true; leave unchanged
        return plausible.plausible_type


class JoinLeftStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        column_refs = list(node.sql_condition.find_all(ColumnRef))
        left_table = column_refs[0].table
        # tracer.declare(node.operator.operator_type, column_refs[0])
        return [column_refs[0]]

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        label = plausible.plausible_type
        constraint: Constraint = plausible.parent

        column_refs = list(constraint.sql_condition.find_all(ColumnRef))
        left_table = column_refs[0].table
        # tracer.declare(constraint.operator.operator_type, column_refs[0])
        if constraint.delta[bit]:
            return PlausibleType.COVERED

        return plausible.plausible_type


class JoinRightStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        column_refs = list(node.sql_condition.find_all(ColumnRef))
        left_table = column_refs[0].table
        # tracer.declare(node.operator.operator_type, column_refs[1])
        return [column_refs[1]]

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        bit = plausible.bit()
        label = plausible.plausible_type
        constraint: Constraint = plausible.parent

        column_refs = list(constraint.sql_condition.find_all(ColumnRef))
        left_table = column_refs[0].table
        # tracer.declare(constraint.operator.operator_type, column_refs[0])
        if constraint.delta[bit]:
            return PlausibleType.COVERED
        return plausible.plausible_type


class PredicateStrategy(Strategy):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.bit = kwargs.get("bit", PBit.TRUE)

    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> List[Expression]:
        if self.bit == PBit.TRUE:
            return [node.sql_condition]
        else:
            constraint = negate_predicate(node.sql_condition)

            return [constraint]

        # return tracer._declare_predicate_constraints(node, context)

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        threshold = POSITIVE_THRESHOLD if self.bit == PBit.TRUE else NEGATIVE_THRESHOLD
        if len(plausible.parent.symbolic_exprs[self.bit]) >= threshold:
            return PlausibleType.COVERED
        return plausible.plausible_type


class HavingCountStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        return tracer._declare_having_count_constraints(node, context)

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        return plausible.plausible_type


class HavingSumStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        return tracer._declare_having_sum_constraints(node, context)

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        return plausible.plausible_type


class HavingAvgStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        return tracer._declare_having_avg_constraints(node, context)

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        return plausible.plausible_type


class HavingMaxStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        return tracer._declare_having_max_constraints(node, context)

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        return plausible.plausible_type


class HavingMinStrategy(Strategy):
    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        return tracer._declare_having_min_constraints(node, context)

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        return plausible.plausible_type


class HavingStrategy(Strategy):
    def is_having_count(self, smt_expr: Symbol) -> bool:

        if not smt_expr.find_all(Variable):
            return True
        return False

    def is_size_one_group(self, smt_expr: Symbol) -> bool:
        variables = smt_expr.find_all(Variable)
        if len(variables) == 1:
            return True
        return False

    def declare_having_count(self, tracer, node, context):
        rbit = PBit.HAVING_FALSE if self.bit == PBit.HAVING_TRUE else PBit.HAVING_TRUE

        for rowids, smt_expr in zip(node.delta[rbit], node.symbolic_exprs[rbit]):
            if self.is_size_one_group(smt_expr):
                """Extend group size to >1"""
                continue
            context["groupid"] = rowids
            return []

    def declare(
        self, tracer: "UExprToConstraint", node: "Constraint", context: dict
    ) -> None:
        rbit = PBit.HAVING_FALSE if self.bit == PBit.HAVING_TRUE else PBit.HAVING_TRUE

        for rowids, smt_expr in zip(node.delta[rbit], node.symbolic_exprs[rbit]):
            if self.is_size_one_group(smt_expr) or self.is_having_count(smt_expr):
                """Extend group size to >1"""
                context["groupid"] = rowids
                return []
            context["has_having"] = True
            return [smt_expr]
        # if self.bit == PBit.HAVING_TRUE:
        #     for smt_expr in node.symbolic_exprs[PBit.HAVING_FALSE]:
        #         if not smt_expr.find_all(Variable):
        #             continue
        #         context["has_having"] = True
        #         return [smt_expr]
        # else:
        #     for smt_expr in node.symbolic_exprs[PBit.HAVING_TRUE]:
        #         if not smt_expr.find_all(Variable):
        #             continue
        #         context["has_having"] = True
        #         return [smt_expr]
        return []

    def check(self, plausible: "PlausibleBranch") -> PlausibleType:
        threshold = (
            POSITIVE_THRESHOLD if self.bit == PBit.HAVING_TRUE else NEGATIVE_THRESHOLD
        )
        if len(plausible.parent.symbolic_exprs[self.bit]) >= threshold:
            return PlausibleType.COVERED
        return plausible.plausible_type


REGISTRY: Dict[object, Strategy] = {
    PBit.DUPLICATE: DuplicateStrategy(),
    PBit.NULL: NullStrategy(),
    PBit.GROUP_COUNT: GroupCountStrategy(),
    PBit.GROUP_SIZE: GroupSizeStrategy(),
    PBit.MAX: SortMaxStrategy(),
    PBit.MIN: SortMinStrategy(),
    PBit.JOIN_TRUE: JoinTrueStrategy(),
    PBit.JOIN_LEFT: JoinLeftStrategy(),
    PBit.JOIN_RIGHT: JoinRightStrategy(),
    PBit.TRUE: PredicateStrategy(bit=PBit.TRUE),
    PBit.FALSE: PredicateStrategy(bit=PBit.FALSE),
    PBit.HAVING_TRUE: HavingStrategy(bit=PBit.HAVING_TRUE),
    PBit.HAVING_FALSE: HavingStrategy(bit=PBit.HAVING_FALSE),
}


def resolve_check(operator_type: Optional[str], bit: PBit) -> Optional[Strategy]:
    """Resolve a strategy given an operator type and a PlausibleBit.

    Try operator-specific key first, then fall back to bit-only strategies.
    """
    if operator_type is not None:
        key: Tuple[str, PBit] = (operator_type, bit)
        if key in REGISTRY:
            return REGISTRY[key]
    return REGISTRY.get(bit)
