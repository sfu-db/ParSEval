from __future__ import annotations
from .constant import BranchType, PathConstraintType
from collections import defaultdict
from .helper import get_ref, get_datatype
from src.expression.symbol import Expr, get_all_variables, Variable, and_, or_
from src.expression.visitors import substitute, extend_summation, extend_distinct

import random, logging
from typing import TYPE_CHECKING, Dict, List
if TYPE_CHECKING:
    from .constraint import Constraint, PlausibleChild

logger = logging.getLogger('src.parseval')

if TYPE_CHECKING:
    from .constraint import Constraint

def adapt_constraint(instance, node: Constraint, new_symbols: Dict[str, List], primary_table, primary_tuple_id):
    new_constraint = None
    if node.constraint_type in { PathConstraintType.VALUE, PathConstraintType.PATH}:
        new_constraint = adapt_constraint_from_filter(instance, node, new_symbols= new_symbols, primary_table= primary_table, primary_tuple_id= primary_tuple_id)
    if node.constraint_type in {PathConstraintType.SIZE}:
        if node.operator_key == 'aggregate':
            new_constraint = adapt_constraint_from_aggregate(instance, node, new_symbols= new_symbols, primary_table= primary_table, primary_tuple_id= primary_tuple_id)
        else:
            new_constraint = adapt_constraint_from_project(instance, node, new_symbols= new_symbols, primary_table= primary_table, primary_tuple_id= primary_tuple_id)
    return new_constraint

def adapt_constraint_from_filter(instance, node: Constraint, new_symbols: Dict[str, List], primary_table, primary_tuple_id):
    '''
        derive constraint from filter and join operators
    '''
    assert node.constraint_type in {PathConstraintType.VALUE, PathConstraintType.PATH}

    tables = node.get_tables()
    new_constraint, source_vars = None, None
    if primary_table in node.get_tables():
        for predicate in node.delta:
            source_vars = get_all_variables(predicate)
            tuples_ = set(instance.symbol_to_tuple_id[v.this] for v in source_vars)
            # logger.info(f"primary tuple id {primary_tuple_id} in {tuples_} : {primary_tuple_id in tuples_}")
            if primary_tuple_id in tuples_:
                new_constraint = predicate
                break
            
    if new_constraint is None:
        new_constraint = node.delta[-1]
        source_vars = get_all_variables(new_constraint)
    new_constraint = replace_or_extend_constraints(new_constraint, instance, source_vars, new_symbols, tables, node.constraint_type == PathConstraintType.PATH)
    return new_constraint


def replace_or_extend_constraints(predicate, instance, source_vars, target_vars: Dict[str, List], orders, extend = False):
    '''
        Either substitute or extend a given predicate with target vars(i.e. new symbols)
    '''
    substitutions = defaultdict(dict)
    for v in source_vars:
        tbl, _, col_index = instance.symbol_to_table[v.this]
        for row in target_vars[tbl]:
            new_symbol = row[col_index]
            if new_symbol not in substitutions[tbl].values():
                substitutions[tbl][v] = new_symbol
    new_constraint = predicate
    for idx, tbl in enumerate(orders):
        mapping = substitutions[tbl]
        if extend:
            new_constraint = extend_summation(new_constraint, mapping, extend = idx > 0)
        else:                
            new_constraint = substitute(new_constraint, mapping)
    return new_constraint

def adapt_constraint_from_aggfunc(instance, node: Constraint, new_symbols: Dict[str, List], primary_table, primary_tuple_id):

    ...


def adapt_constraint_from_aggregate(instance, node: Constraint, new_symbols: Dict[str, List], primary_table, primary_tuple_id):
    assert node.operator_key in {'aggregate'}
    new_constraint, source_vars = None, None
    substitutions = defaultdict(dict)
    if node.taken:
        """we should increase group count, i.e. extend Operands in Distinct Expression"""
        predicate = node.delta[0]
        source_vars = get_all_variables(predicate)
        for v in source_vars:
            tbl, _, col_index = instance.symbol_to_table[v.this]
            for row in new_symbols[tbl]:
                new_symbol = row[col_index]
                if new_symbol not in substitutions[tbl].values():
                    substitutions[tbl][v] = new_symbol
        new_constraint = predicate
        for tbl, mapping in substitutions.items():
            new_constraint = extend_distinct(new_constraint, mapping)
    else:
        """we should increase group size"""
        for predicate, tup in zip(node.delta, node.tuples):
            source_vars = get_all_variables(predicate)
            tuples_ =set( [var for t in tup for var in get_all_variables(t)])

            # logger.info(f'primary tuple id: {primary_tuple_id}, ref {predicate}, source vars: {tuples_}')

            # logger.info(tuples_)
            # logger.info(f"{primary_tuple_id in tuples_}, {type(primary_tuple_id)} {primary_tuple_id.value}")
            
            if primary_tuple_id not in tuples_:
                continue
            new_constraint = predicate == predicate.value
            # logger.info(f'before extend: {new_constraint}')
            new_constraint = replace_or_extend_constraints(new_constraint, instance, source_vars, new_symbols, orders= new_symbols.keys(), extend= False)
            break
        if new_constraint is None:
            predicate = node.delta[-1]
            source_vars = get_all_variables(predicate)
            tuples_ = set(instance.symbol_to_tuple_id[v.this] for v in source_vars)
            new_constraint = predicate == predicate.value
            logger.info(new_constraint)
            new_constraint = replace_or_extend_constraints(new_constraint, instance, source_vars, new_symbols, orders= new_symbols.keys(), extend= False)
    return new_constraint

def adapt_constraint_from_project(instance, node: Constraint, new_symbols: Dict[str, List], primary_table, primary_tuple_id):
    return None
