# from __future__ import annotations

# from abc import ABC, abstractmethod
# from .constants import PBit, PlausibleType
# from typing import Optional, TYPE_CHECKING, Dict, Tuple
# from src.parseval.plan import ColumnRef, sqlglot_exp, Is_Null
# from src.parseval.symbol import Variable, Const

# from src.parseval.helper import group_by_concrete, convert_to_literal

# # from .checks import (
# #     check_cover_duplicate,
# #     check_cover_null,
# #     check_cardinality,
# #     check_groupcount,
# #     check_groupsize,
# # )

# if TYPE_CHECKING:
#     from src.parseval.uexpr import PlausibleBranch
#     from src.parseval.uexpr.ptree import UExprToConstraint, Constraint


# DUPLICATE_THRESHOLD = 2
# NULL_THRESHOLD = 2


# class Strategy(ABC):
#     """Base strategy that implements both declaration and checking.

#     Subclasses should implement `declare(tracer, node, context)` to emit
#     constraints via `tracer.declare(...)`, and `check(plausible)` to
#     determine the `PlausibleType` for a given `PlausibleBranch`.
#     """

#     def __init__(self, **kwargs):
#         super().__init__()
#         for key, value in kwargs.items():
#             setattr(self, key, value)

#     @abstractmethod
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         pass

#     @abstractmethod
#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         pass


# class DuplicateStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         if isinstance(node.sql_condition, ColumnRef):
#             if not node.sql_condition.args.get("unique", False) and node.symbolic_exprs:
#                 constraint = node.sql_condition
#                 value_counts = group_by_concrete(node.symbolic_exprs[PBit.TRUE])
#                 if value_counts:
#                     values = sorted(value_counts.items(), key=lambda x: -len(x[1]))
#                     value = values[0][1][0]
#                     literal = convert_to_literal(
#                         value.concrete, node.sql_condition.datatype
#                     )
#                     constraint = sqlglot_exp.EQ(
#                         this=node.sql_condition, expression=literal
#                     )
#                 tracer.declare(node.operator.operator_type, constraint)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         """Check if the constraint covers duplicate values."""
#         bit = plausible.bit()
#         current_label = plausible.plausible_type
#         if current_label in {
#             PlausibleType.INFEASIBLE,
#             PlausibleType.COVERED,
#             PlausibleType.TIMEOUT,
#         }:
#             return current_label
#         constraint: Constraint = plausible.parent
#         columnrefs = list(constraint.sql_condition.find_all(ColumnRef))
#         if not columnrefs or all(
#             [columnref.args.get("unique", False) for columnref in columnrefs]
#         ):
#             return PlausibleType.INFEASIBLE
#         variables = []
#         for smt_expr in constraint.symbolic_exprs[PBit.TRUE]:
#             variables.extend(smt_expr.find_all(Variable))

#         constraint.symbolic_exprs[bit].clear()
#         groups = group_by_concrete(variables)
#         duplicates_found = False
#         for key, items in groups.items():
#             if len(items) > plausible.metadata.get(
#                 "DUPLICATE_THRESHOLD", DUPLICATE_THRESHOLD
#             ):
#                 duplicates_found = True
#                 constraint.symbolic_exprs[bit].append(items[0])
#         return PlausibleType.COVERED if duplicates_found else current_label


# class NullStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         columnrefs = list(node.sql_condition.find_all(ColumnRef))
#         for columnref in columnrefs:
#             if columnref.datatype and columnref.datatype.nullable:
#                 null_constraint = Is_Null(this=columnref)
#                 tracer.declare(node.operator.operator_type, null_constraint)
#                 return

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         bit = plausible.bit()
#         label = plausible.plausible_type
#         if label in {
#             PlausibleType.INFEASIBLE,
#             PlausibleType.COVERED,
#             PlausibleType.TIMEOUT,
#         }:
#             return label

#         constraint: Constraint = plausible.parent
#         columnrefs = list(constraint.sql_condition.find_all(ColumnRef))
#         if not columnrefs or all(
#             [columnref.datatype.nullable is False for columnref in columnrefs]
#         ):
#             return PlausibleType.INFEASIBLE

#         constraint.symbolic_exprs[bit].clear()
#         for smt in constraint.symbolic_exprs[PBit.TRUE]:
#             for var in smt.find_all(Variable):
#                 if var.concrete is None:
#                     constraint.symbolic_exprs[bit].append(smt)
#                     cover_null = True
#         cover_null = False
#         if len(constraint.symbolic_exprs[bit]) > NULL_THRESHOLD:
#             cover_null = True
#         return PlausibleType.COVERED if cover_null else label


# class GroupCountStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         return tracer._declare_group_count_constraints(node, context)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return check_groupcount(plausible)


# class GroupSizeStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         return tracer._declare_group_size_constraints(node, context)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return check_groupsize(plausible)


# class MinMaxStrategy(Strategy):
#     def __init__(self, **kwargs):
#         super().__init__(**kwargs)
#         self.is_max = kwargs.get("is_max", True)

#     def declare(self, tracer, node, context):
#         if node.sql_condition.args.get("unique", False):
#             return
#         values = node.symbolic_exprs[PBit.TRUE]
#         max_ = max([v.concrete for v in values if v.concrete is not None])
#         min_ = min([v.concrete for v in values if v.concrete is not None])
#         reference = max_ if self.is_max else min_
#         ref_literal = convert_to_literal(
#             reference, node.sql_condition.args.get("datatype")
#         )
#         if max_ == min_:
#             klass = sqlglot_exp.NEQ
#         else:
#             klass = sqlglot_exp.EQ
#         constraint = klass(
#             this=node.sql_condition,
#             expression=ref_literal,
#         )
#         tracer.declare(node.operator.operator_type, constraint)

#     def check(self, plausible):
#         bit = plausible.bit()
#         label = plausible.plausible_type
#         constraint: Constraint = plausible.parent
#         if constraint.sql_condition.args.get("unique", False) or label in {
#             PlausibleType.INFEASIBLE,
#             PlausibleType.TIMEOUT,
#         }:
#             return PlausibleType.INFEASIBLE
#         values = [v.concrete for v in constraint.symbolic_exprs[PBit.TRUE]]
#         filtered = list(filter(lambda x: x is not None, values))
#         if not filtered:
#             return PlausibleType.UNEXPLORED

#         min_ = min(filtered)
#         max_ = max(filtered)
#         if max_ == min_:
#             return PlausibleType.UNEXPLORED
#         if bit == PBit.MAX and values.count(max_) > 1:
#             constraint.symbolic_exprs[bit] = [
#                 v for v in constraint.symbolic_exprs[PBit.TRUE] if v.concrete == max_
#             ]
#             return PlausibleType.COVERED
#         if bit == PBit.MIN and values.count(min_) > 1:
#             constraint.symbolic_exprs[bit] = [
#                 v for v in constraint.symbolic_exprs[PBit.TRUE] if v.concrete == min_
#             ]
#             return PlausibleType.COVERED
#         return label


# class SortMaxStrategy(MinMaxStrategy):
#     def __init__(self, **kwargs):
#         super().__init__(is_max=True, **kwargs)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return check_cardinality(plausible)


# class SortMinStrategy(MinMaxStrategy):
#     def __init__(self, **kwargs):
#         super().__init__(is_max=False, **kwargs)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return check_cardinality(plausible)


# class JoinTrueStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         """
#         declare SMT constraints for join true
#         """
#         tracer.declare(node.operator.operator_type, node.sql_condition)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         # No special check implemented for join true; leave unchanged
#         return plausible.plausible_type


# class JoinLeftStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         column_refs = list(node.sql_condition.find_all(ColumnRef))
#         left_table = column_refs[0].table
#         tracer.declare(node.operator.operator_type, column_refs[0])

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         bit = plausible.bit()
#         label = plausible.plausible_type
#         constraint: Constraint = plausible.parent

#         column_refs = list(constraint.sql_condition.find_all(ColumnRef))
#         left_table = column_refs[0].table
#         # tracer.declare(constraint.operator.operator_type, column_refs[0])
#         if constraint.delta[bit]:
#             return PlausibleType.COVERED

#         return plausible.plausible_type


# class JoinRightStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         column_refs = list(node.sql_condition.find_all(ColumnRef))
#         left_table = column_refs[0].table
#         tracer.declare(node.operator.operator_type, column_refs[1])

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         bit = plausible.bit()
#         label = plausible.plausible_type
#         constraint: Constraint = plausible.parent

#         column_refs = list(constraint.sql_condition.find_all(ColumnRef))
#         left_table = column_refs[0].table
#         # tracer.declare(constraint.operator.operator_type, column_refs[0])
#         if constraint.delta[bit]:
#             return PlausibleType.COVERED
#         return plausible.plausible_type


# class PredicateStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         return tracer._declare_predicate_constraints(node, context)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return plausible.plausible_type


# class HavingCountStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         return tracer._declare_having_count_constraints(node, context)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return plausible.plausible_type


# class HavingSumStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         return tracer._declare_having_sum_constraints(node, context)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return plausible.plausible_type


# class HavingAvgStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         return tracer._declare_having_avg_constraints(node, context)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return plausible.plausible_type


# class HavingMaxStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         return tracer._declare_having_max_constraints(node, context)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return plausible.plausible_type


# class HavingMinStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         return tracer._declare_having_min_constraints(node, context)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return plausible.plausible_type


# class HavingStrategy(Strategy):
#     def declare(
#         self, tracer: "UExprToConstraint", node: "Constraint", context: dict
#     ) -> None:
#         return tracer._declare_having_constraints(node, context)

#     def check(self, plausible: "PlausibleBranch") -> PlausibleType:
#         return plausible.plausible_type


# REGISTRY: Dict[object, Strategy] = {
#     PBit.DUPLICATE: DuplicateStrategy(),
#     PBit.NULL: NullStrategy(),
#     PBit.GROUP_COUNT: GroupCountStrategy(),
#     PBit.GROUP_SIZE: GroupSizeStrategy(),
#     PBit.MAX: SortMaxStrategy(),
#     PBit.MIN: SortMinStrategy(),
#     PBit.JOIN_TRUE: JoinTrueStrategy(),
#     PBit.JOIN_LEFT: JoinLeftStrategy(),
#     PBit.JOIN_RIGHT: JoinRightStrategy(),
# }


# def resolve_strategy(operator_type: Optional[str], bit: PBit) -> Optional[Strategy]:
#     """Resolve a strategy given an operator type and a PlausibleBit.

#     Try operator-specific key first, then fall back to bit-only strategies.
#     """
#     if operator_type is not None:
#         key: Tuple[str, PBit] = (operator_type, bit)
#         if key in REGISTRY:
#             return REGISTRY[key]
#     return REGISTRY.get(bit)
