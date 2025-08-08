from __future__ import annotations
from .constant import BranchType, PathConstraintType
from .helper import get_ref, get_datatype
from src.expression.symbol import Expr, get_all_variables, Variable, and_, or_
from src.expression.visitors import substitute, extend_summation, extend_distinct

import random, logging
from typing import TYPE_CHECKING
# if TYPE_CHECKING:
from .constraint import Constraint, PlausibleChild

logger = logging.getLogger('src.parseval')

BRANCH_HIT = 2  ## number of hit for each path
MAX_RETRY = 2
MINIMIAL_GROUP_COUNT = 3
MINIMIAL_GROUP_SIZE = 3

def get_reference_predicate(plausible: PlausibleChild):
    """
        Given a plausible node in the constraint tree, return a reference predicate, its constraint type, and involved tables.
        This predicate can be used to generate new data to cover unexplored branches.
    """
    branch_type = plausible.branch_type
    if branch_type == BranchType.POSITIVE:
        return _get_reference_predicate_from_positive(plausible)
    elif branch_type == BranchType.PLAUSIBLE:
        return _get_reference_predicate_from_plausible(plausible)
    
    elif branch_type == BranchType.NULLABLE:
        return _get_reference_predicate_from_nullable(plausible)
    elif branch_type == BranchType.SIZE:
        return _get_reference_predicate_from_plausible_size(plausible)
    else:
        logger.error(f"Unsupported branch type: {branch_type}")
        raise ValueError(f"Unsupported branch type: {branch_type}")

def _get_reference_predicate_from_plausible(plausible: PlausibleChild):
    """
    For PLAUSIBLE branch, return the negation of a sibling's predicate.
    """
    sibling = _find_sibling_constraint(plausible)
    if not sibling or not sibling.delta:
        raise ValueError("No sibling constraint or empty delta found for PLAUSIBLE branch.")
    # Pick a random predicate from sibling's delta and negate it
    pred = random.choice(sibling.delta).not_()
    constraint_type = sibling.constraint_type
    tables = sibling.get_tables()    
    return pred, constraint_type, tables

def _get_reference_predicate_from_positive(plausible: PlausibleChild):
    """
        For POSITIVE branch, use parent's predicate or size logic.
    """
    parent_node: Constraint = plausible.parent
    constraint_type = parent_node.constraint_type
    tables = parent_node.get_tables()
    if constraint_type in {PathConstraintType.VALUE, PathConstraintType.PATH}:
        pred = _get_reference_predicate_from_filter(parent_node)
        logger.debug(f"POSITIVE: Using filter predicate {pred}")
        return pred, constraint_type, tables
    elif constraint_type == PathConstraintType.SIZE:
        pred, _, _ = _get_reference_predicate_from_plausible_size(plausible)
        logger.debug(f"POSITIVE: Using size predicate {pred}")
        return pred, constraint_type, tables
    else:
        logger.error(f"Cannot get reference predicate from POSITIVE node: {plausible}")
        raise ValueError(f"Cannot get reference predicate from POSITIVE node: {plausible}")

def _get_reference_predicate_from_nullable(plausible: PlausibleChild):
    """
        For NULLABLE branch, generate a predicate that makes a variable NULL.
    """
    parent = plausible.parent
    if not parent.delta:
        logger.error("Parent delta is empty for NULLABLE branch.")
        raise ValueError("Parent delta is empty for NULLABLE branch.")
    pred = parent.delta[-1]
    variables = list(get_all_variables(pred))
    if not variables:
        logger.error("No variables found in parent predicate for NULLABLE branch.")
        raise ValueError("No variables found in parent predicate for NULLABLE branch.")
    v = random.choice(variables)
    predicate = v.is_null()
    constraint_type = parent.constraint_type
    tables = parent.get_tables()
    logger.debug(f"NULLABLE: Using is_null predicate {predicate}")
    return predicate, constraint_type, tables

def _get_reference_predicate_from_plausible_size(plausible: PlausibleChild):
    """
    For SIZE branch, handle project, sort, and aggregate operators.
    Returns (predicate, constraint_type, tables)
    """
    parent_node: Constraint = plausible.parent
    constraint_type = parent_node.constraint_type
    tables = parent_node.get_tables()
    if parent_node.operator_key in {'project', 'sort'}:
        pred = _get_reference_predicate_from_project(parent_node)
        logger.debug(f"SIZE: Using project/sort predicate {pred}")
        return pred, constraint_type, tables
    elif parent_node.operator_key == 'aggregate':
        pred = _get_reference_predicate_from_agg_func(parent_node)
        logger.debug(f"SIZE: Using aggregate predicate {pred}")
        return pred, constraint_type, tables
    else:
        logger.error(f"Unsupported operator_key for SIZE branch: {parent_node.operator_key}")
        raise ValueError(f"Unsupported operator_key for SIZE branch: {parent_node.operator_key}")

def _find_sibling_constraint(plausible: PlausibleChild) -> Constraint | None:
    """
    Helper to locate a sibling constraint node (not a PlausibleChild).
    """
    parent = plausible.parent
    for bit, child in parent.children.items():
        if isinstance(child, Constraint):
            return child
    return None

def _get_reference_predicate_from_filter(node: Constraint):
    """
    For VALUE or PATH constraints, return the last predicate in node.delta.
    """
    if not node.delta:
        logger.error("No predicates in node.delta for filter.")
        raise ValueError("No predicates in node.delta for filter.")
    return node.delta[-1]

def _get_reference_predicate_from_project(node: Constraint):
    """
    For project/sort operators, handle nullability and uniqueness.
    """
    if not node.delta:
        logger.error("No predicates in node.delta for project.")
        raise ValueError("No predicates in node.delta for project.")
    inputref = node.tbl_exprs[0] if node.tbl_exprs else None
    null_constraints = [variable.is_null() for variable in node.delta]
    if inputref and getattr(inputref, 'nullable', False) and not any(null_constraints):
        # Add a NULL value if possible
        predicate = random.choice([v for v in node.delta if not v.is_null()]).is_null()
        logger.debug(f"PROJECT: Adding NULL value predicate {predicate}")
        return predicate
    elif inputref and not getattr(inputref, 'unique', True):
        # Add a duplicate value if not unique
        non_nulls = [d for d in node.delta if not d.is_null()]
        if not non_nulls:
            logger.error("No non-null values to duplicate in project.")
            raise ValueError("No non-null values to duplicate in project.")
        variable = random.choice(non_nulls)
        predicate = variable == variable.value
        logger.debug(f"PROJECT: Adding duplicate value predicate {predicate}")
        return predicate
    else:
        logger.error(f"Referred column should be either nullable or not unique in project. inputref: {inputref}")
        raise ValueError(f"Referred column should be either nullable or not unique in project. inputref: {inputref}")

def _get_reference_predicate_from_agg_func(node: Constraint):
    """
    For aggregate functions, handle group size, nulls, and duplicates.
    """
    if not hasattr(node, 'info') or 'group_stats' not in node.info or 'group_size' not in node.info:
        logger.error("Aggregate node missing group_stats or group_size info.")
        raise ValueError("Aggregate node missing group_stats or group_size info.")
    nullable, unique = False, False
    for md in node.info.get('table', []):
        for depend in getattr(md, 'depends_on', []):
            if getattr(depend, 'nullable', False):
                nullable = True
            if getattr(depend, 'unique', False):
                unique = True
    # Try to add a duplicate if not unique
    if not unique:
        for group_index, _, has_duplicate in node.info['group_stats']:
            if not has_duplicate:
                delta = node.delta[group_index]
                variables = list(get_all_variables(delta))
                if not variables:
                    continue
                variable = random.choice(variables)
                logger.debug(f"AGGREGATE: Adding duplicate value predicate {variable == variable.value}")
                return variable == variable.value
    # Try to add a NULL if nullable
    if nullable:
        for group_index, has_null, _ in node.info['group_stats']:
            if not has_null:
                delta = node.delta[group_index]
                variables = list(get_all_variables(delta))
                if not variables:
                    continue
                predicate = random.choice(variables).is_null()
                logger.debug(f"AGGREGATE: Adding NULL value predicate {predicate}")
                return predicate
    # Try to increase group size
    group_sizes = sorted(node.info['group_size'], key=lambda x: x[1])
    if group_sizes and group_sizes[-1][1] < MINIMIAL_GROUP_SIZE:
        variable = node.delta[group_sizes[-1][0]]
        predicate = variable.is_null().not_()
        logger.debug(f"AGGREGATE: Increasing group size with predicate {predicate}")
        return predicate
    elif group_sizes:
        # Weighted random choice to increase a group
        weights = [-d[1] for d in node.info['group_size']]
        variable = random.choices(node.delta, weights=weights)[0]
        predicate = variable.is_null().not_()
        logger.debug(f"AGGREGATE: Increasing group size (weighted) with predicate {predicate}")
        return predicate
    logger.error("Could not determine aggregate reference predicate.")
    raise ValueError("Could not determine aggregate reference predicate.")