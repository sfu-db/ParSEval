from __future__ import annotations
from typing import Dict, Any, TYPE_CHECKING
from src.parseval.plan import rex
from .constants import PBit, PlausibleType
from src.parseval.symbol import Variable
from src.parseval.helper import group_by_concrete
import logging

if TYPE_CHECKING:
    from .node import Constraint

DUPLICATE_THRESHOLD = 2
NULL_THRESHOLD = 2


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
