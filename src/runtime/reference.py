from __future__ import annotations
from .constant import BranchType, PathConstraintType
from .helper import get_ref, get_datatype
from src.expression.symbol import Expr, get_all_variables, Variable, and_, or_
from src.expression.visitors import substitute, extend_summation, extend_distinct

import random, logging
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .constraint import Constraint, PlausibleChild

logger = logging.getLogger('src.parseval')

BRANCH_HIT = 2  ## number of hit for each path
MAX_RETRY = 2
MINIMIAL_GROUP_COUNT = 3
MINIMIAL_GROUP_SIZE = 3

def get_reference_predicate(plausible: PlausibleChild):
    '''
        get reference predicate related to plausible node
        if branch type is PLAUSIBLE, return sibling.delta.not_()
        if branch type is positive:
            if constraint type in {Path, VALUE}, return delta
            if constraint type in {SIZE}, return is_null, or duplicate
    '''
    constraint_type = None
    predicate = None
    tables = None
    if plausible.branch_type == BranchType.PLAUSIBLE:
        node = plausible.sibling()
        predicate = random.choice(node.delta).not_()
        constraint_type = node.constraint_type
        tables = node.get_tables()

    elif plausible.branch_type == BranchType.POSITIVE:
        parent_node: Constraint = plausible.parent
        constraint_type = parent_node.constraint_type
        tables = parent_node.get_tables()
        if parent_node.constraint_type in {PathConstraintType.PATH, PathConstraintType.VALUE}:
            predicate = _get_reference_predicate_from_filter(parent_node)
        elif constraint_type in {PathConstraintType.SIZE}:
            if parent_node.operator_key in {'aggregate'}:
                if parent_node.taken:
                    '''We should extend group count'''
                    predicate = parent_node.delta[0]
                else:
                    '''we should increase group size '''
                    predicate = _get_reference_predicate_from_agg_func(parent_node)
            if parent_node.operator_key in {'project', 'sort'}:
                predicate = _get_reference_predicate_from_project(parent_node)    
    assert predicate is not None, f"should handle {plausible.branch_type} "
    return predicate, constraint_type, tables

def _get_reference_predicate_from_agg_func(node):
    '''we do not need to consider group count here. Instead
        aggregate($0)
        / 
    SUM($4)
        /
    COUNT($5)'''
    assert node.taken is False, f"Aggregate Func node should only in bit 0, current is {node.taken}"
    assert node.sql_condition.key in {'count', 'sum', 'max', 'min', 'avg'}, f"Aggregate Func node should be one of count, sum, max, min, avg, current is {node.sql_condition.key}"
    nullable, unique = False, False
    for md in node.info['table']:
        for depend in md.depends_on:
            if depend.nullable:
                nullable = True
            if depend.unique:
                unique = True
    if not unique:
        for group_index, _, has_duplicate in node.info['group_stats']:
            if not has_duplicate:
                delta = node.delta[group_index]
                variables = list(get_all_variables(delta))
                variable = random.choice(variables)
                # logger.info(f'group {group_index} has no unique values, select data: {variable == variable.value}')
                return  variable == variable.value
    if nullable: ## is there NULL in each group?
        for group_index, has_null, _ in node.info['group_stats']:
            if not has_null:
                delta = node.delta[group_index]
                variables = list(get_all_variables(delta))
                return random.choice(variables).is_null() #random.choice(variables).is_null()
    
    group_sizes = sorted(node.info['group_size'], key = lambda node: node[1])
    '''if max size < thres, tehn max, if all groups have the same size, then random one '''
    if group_sizes[-1][1] < MINIMIAL_GROUP_SIZE:
        variable = node.delta[group_sizes[-1][0]]
    else:
        variable = random.choices(node.delta, weights= [-d[1] for d in node.info['group_size']])[0]
    predicate = variable.is_null().not_()
    return predicate


def _get_reference_predicate_from_project(node: Constraint):
    if node.sql_condition.key == 'column':
        inputref = node.info['table'][0]
        null_constraints = [variable.is_null() for variable in node.delta]
        if inputref.nullable and not any(null_constraints):
            predicate = null_constraints.pop()
        elif not inputref.unique:
            if node.operator_key in {'sort'}:
                variable = max(node.delta)
            else:
                variable = random.choice([d for d in node.delta if not d.is_null()])
            predicate = variable == variable.value #variables[0] == variables[1]
        else:
            logger.info(node.sql_condition)
            raise ValueError(f'refered column should be either nullable or unique')
    return predicate

def _get_reference_predicate_from_filter(node):
    assert node.constraint_type in {PathConstraintType.PATH, PathConstraintType.VALUE}
    predicate = node.delta[-1]
    return predicate

def _get_reference_predicate_from_having(node: Constraint):

    ...