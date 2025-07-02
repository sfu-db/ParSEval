from dataclasses import dataclass, field
import logging
from collections import OrderedDict, deque, defaultdict
from typing import List, Tuple, Dict, Any, TYPE_CHECKING, Optional, Set, Union, TypeVar
from .constant import BranchType, Action, PathConstraintType, OperatorKey, OperatorId, ConstraintId
from .constraint import Constraint, PlausibleChild
from .helper import get_ref, get_datatype
from .reference import get_reference_predicate
from .adapt import adapt_constraint
from src.expression.symbol import Expr, get_all_variables, Variable, and_, or_
from src.expression.visitors import substitute, extend_summation, extend_distinct
import enum, random
if TYPE_CHECKING:
    from src.instance.instance import Instance
logger = logging.getLogger('src.parseval.uexpr')

T = TypeVar('T')

BRANCH_HIT = 2
MAX_RETRY = 2
MINIMIAL_GROUP_COUNT = 3
MINIMIAL_GROUP_SIZE = 3
class UExprToConstraint:
    def __init__(self, add):
        self.constraints: List[Constraint] = []
        self.nodes: Dict[str, Constraint] = {}
        self.leaves: Dict[str, Constraint] = {}
        self.root_constraint = Constraint(self, None, 'ROOT', None)
        self.positive_path = defaultdict(list)
        self.positive_path['ROOT'].append(self.root_constraint) ## we use this to cache all positive paths' operators.
        self.no_bit, self.yes_bit = '0', '1'

        self.prev_operator = 'ROOT'
        self.add = add
    
    def add_constraint(self, constraint: Constraint):
        self.constraints.append(constraint)
        self.nodes[constraint.identifier] = constraint
        if constraint.is_leaf():
            self.leaves[constraint.identifier] = constraint
    
    def which_branch(self, operator_key: OperatorKey, operator_i: OperatorId, predicates: List[Expr], sql_conditions: List, takens: List[bool], branch, infos: List[Dict[str, Any]], tuples, **kwargs):
        """
            For each operator, we should track all possible predicates, since we are using concrete values, we could know which path is covered.
        """
        assert len(infos) == len(sql_conditions)
        for node in self.positive_path[self.prev_operator]: #positive_nodes
            if node.operator_key != 'ROOT' and not node.tuples.intersection(tuples):
                continue
            for smt_expr, condition, taken, info in zip(predicates, sql_conditions, takens, infos):
                node = node.add_child(operator_key, operator_i, condition, smt_expr, branch = branch, info = info, taken = taken, tuples = tuples,**kwargs)

            if branch and node not in self.positive_path[f'{operator_key}_{operator_i}']:
                self.positive_path[f'{operator_key}_{operator_i}'].append(node)
        return node

    def _determine_action(self, plausible: PlausibleChild) -> Action:
        parent = plausible.parent
        # if plausible.branch_type == BranchType.POSITIVE and len(parent.delta) > 2:
        #     return Action.DONE
        if parent.constraint_type in {PathConstraintType.SIZE, PathConstraintType.PATH}:
            return Action.APPEND
        # if plausible.branch_type == BranchType.PLAUSIBLE and len(plausible.sibling().delta) > 1:
        #     return Action.UPDATE
        return Action.APPEND
    def _is_covered_aggregate(self, node: Constraint):
        assert node.operator_key in {'aggregate'}
        """we consider group count and group size here"""
        ### processing group COUNT
        if node.taken:
            return len(node.info['group_size']) > MINIMIAL_GROUP_COUNT
        group_sizes = [size for _, size in node.info['group_size']]

        if max(group_sizes) < MINIMIAL_GROUP_SIZE:
            return False
        
        nullable = False
        unique = False

        for md in node.info['table']:
            for depend in md.depends_on:
                if depend.nullable:
                    nullable = True
                if depend.unique:
                    unique = True
        ## is there NULL in each group?
        ## is there duplicate values in each group?
        if nullable and not all([has_null for _, has_null, _ in node.info['group_stats']]):
            return False
        if not unique and not all([has_duplicate for _, _, has_duplicate in node.info['group_stats']]):
            return False
        return True
    
    def _is_covered(self, plausible: PlausibleChild):
        if plausible.branch_type in {BranchType.POSITIVE, BranchType.STRAIGHT}:
            parent_node: Constraint = plausible.parent
            if parent_node.constraint_type in {PathConstraintType.PATH, PathConstraintType.VALUE}:
                return len(parent_node.delta) >= BRANCH_HIT
            elif parent_node.constraint_type in {PathConstraintType.SIZE}:
                if parent_node.operator_key in {'aggregate', 'aggfunc'}:
                    """we consider group count and group size here"""
                    return self._is_covered_aggregate(parent_node)
                        # return len(set(sizes)) != len(sizes)
                if len(parent_node.delta) < BRANCH_HIT:
                    return False
                null_values = [variable.is_null() for variable in parent_node.delta]
                # ref = parent_node.sql_condition.args.get('ref')
                if parent_node.info['table'][0].nullable and not any(null_values):
                    return False
                if not parent_node.info['table'][0].unique:
                    duplicates = [v.value for v in parent_node.delta]
                    return len(set(duplicates)) != len(parent_node.delta)
                return True
        
        raise RuntimeError("encounter unsupported positive branches")
        
    def _get_affected_tables(self, path: List[Constraint]) -> Dict[str, set]:
        '''
            get all tables that are involved in the path.
            Args:
                path: path from node to root
            Returns:
                a dictionary that maps table name to a set of columns
        '''
        affected_tables = defaultdict(set)        
        for n in path:
            for inputref in n.metadata['table']:
                affected_tables[inputref.table].add(inputref.name)
        return affected_tables

    def next_branch(self, instance: 'Instance', skips: Optional[Set] = None, solved: Optional[List] = None):
        '''
            if we find a node has not been covered(i.e. no constraints in the delta), we should either flip a constraint from sibiling or append a new tuple to the instance to cover this path.
        '''
        skips = skips if skips is not None else set()
        solved = solved if solved is not None else []
        branches = {
            'positive': [],
            'negative': [],
            'uncovered': [],
            'unreachable': []
        }
        for pattern, plausible in self.leaves.items():
            if plausible.branch_type in {BranchType.POSITIVE, BranchType.STRAIGHT}:
                branches['positive'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.NEGATIVE:
                branches['negative'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.PLAUSIBLE and pattern not in skips:
                branches['uncovered'].append((plausible, pattern))
            else:
                branches['unreachable'].append((plausible, pattern))
        plausible, pattern = None, None
        
        if branches['positive']:
            '''
                For all positive branches, we should consider NULL values, Duplicate, SIZE
            '''
            positives = sorted(branches['positive'], key = lambda node: len(node[0].parent.delta))
            for plausible, pattern in positives:
                is_covered = self._is_covered(plausible)
                if not is_covered and pattern not in skips:
                    self._handle_tuple_append(instance, plausible, pattern)
                    return pattern

        uncovered = branches['uncovered']
        uncovered = sorted(filter(lambda x: x[1] not in skips and solved.count(x[1]) < MAX_RETRY, uncovered), key = lambda node: len(node[1]), reverse= True)

        # logger.info(uncovered)
        if uncovered:
            plausible, pattern = uncovered[0]
            self._handle_tuple_append(instance, plausible, pattern)
            return pattern
        return None
       
    def _get_involved_tables_path(self, plausible: PlausibleChild):
        '''
            get all tables that are involved in the path that from plausible to root.
            Args:
                path: path from node to root
            Returns:
                a dictionary that maps table name to a set of columns
        '''
        node = None
        if plausible.branch_type == BranchType.PLAUSIBLE:
            node = plausible.sibling()
        else:
            node = plausible.parent
        path = node.get_path_to_root()[1:]
        affected_tables = set()
        for n in path:
            affected_tables.update(n.get_tables())
        return affected_tables
    def _get_involved_nodes(self, plausible : PlausibleChild):
        ''' get all nodes involved in the path to root
        '''
        
        path = plausible.parent.get_path_to_root()[1:]
        if plausible.branch_type != BranchType.PLAUSIBLE:
            path = path[:-1]        
        return path
        
    def _derive_constraint_from_project(self, instance, node: Constraint, new_symbols: Dict[str, List],primary_table, primary_tuple_id):
        assert node.constraint_type == PathConstraintType.SIZE
        if node.operator_key in {'project'}:
            return None
        if node.operator_key in {'aggregate'}:
            new_constraint = self._derive_constraint_from_aggregate(instance, node, new_symbols, primary_table, primary_tuple_id)
            return new_constraint
        return None

    def _derive_constraint_from_aggregate(self, instance, node: Constraint, new_symbols: Dict[str, List], primary_table, primary_tuple_id):
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
            logger.info(f'primary tbale: {primary_table} , {node.get_tables()}')
            if primary_table in node.get_tables():
                for predicate in node.delta:
                    source_vars = get_all_variables(predicate)
                    tuples_ = set(instance.symbol_to_tuple_id[v.this] for v in source_vars)
                    logger.info(f'delta: {predicate}, tuples: {tuples_}, primary tuple: {primary_tuple_id}')
                    if primary_tuple_id not in tuples_:
                        continue
                    new_constraint = predicate == predicate.value
                    new_constraint = self._derive_constraints(new_constraint, instance, source_vars, new_symbols, orders= new_symbols.keys(), extend= False)
            else:
                predicate = node.delta[-1]
                source_vars = get_all_variables(predicate)
                tuples_ = set(instance.symbol_to_tuple_id[v.this] for v in source_vars)
                new_constraint = predicate == predicate.value
                logger.info(new_constraint)
                new_constraint = self._derive_constraints(new_constraint, instance, source_vars, new_symbols, orders= new_symbols.keys(), extend= False)
        return new_constraint
    
    def _derive_constraint_from_aggfunc(self, instance, node: Constraint, new_symbols: Dict[str, List], primary_table, primary_tuple_id):
        if primary_table in node.get_tables():
            for predicate in node.delta:
                source_vars = get_all_variables(predicate)
                tuples_ = set(instance.symbol_to_tuple_id[v.this] for v in source_vars)
                if primary_tuple_id not in tuples_:
                    continue
                new_constraint = predicate == predicate.value
                new_constraint = self._derive_constraints(new_constraint, instance, source_vars, new_symbols, orders= new_symbols.keys(), extend= False)
        else:
            predicate = node.delta[-1]
            source_vars = get_all_variables(predicate)
            tuples_ = set(instance.symbol_to_tuple_id[v.this] for v in source_vars)
            new_constraint = predicate == predicate.value
            new_constraint = self._derive_constraints(new_constraint, instance, source_vars, new_symbols, orders= new_symbols.keys(), extend= False)

        return new_constraint

    def _handle_tuple_append(self, instance, plausible, pattern):
        involved_tables = self._get_involved_tables_path(plausible)

        primary_predicate, primary_constraint_type, tables = get_reference_predicate(plausible)

        primary_vars = get_all_variables(primary_predicate)
        primary_var = random.choice(list(primary_vars))
        primary_tuple_id = instance.symbol_to_tuple_id[primary_var.this]
        primary_table, _, _ = instance.symbol_to_table[primary_var.this]

        new_symbols = self._declare_new_symbols(instance, involved_tables)
        for node in self._get_involved_nodes(plausible):
            constraint = adapt_constraint(instance, node, new_symbols, primary_table, primary_tuple_id)            
            if constraint is not None:
                self.add(constraint, node.operator_key)
            constraint = None
        
        if primary_constraint_type in {PathConstraintType.PATH, PathConstraintType.VALUE}:
            new_constraint = self._derive_constraints(primary_predicate, instance, primary_vars, new_symbols, tables, extend= primary_constraint_type == PathConstraintType.PATH)
        elif primary_constraint_type in {PathConstraintType.SIZE}:
            logger.info(f'primary constraint : {primary_predicate}, primary vars: {primary_vars}, new_symbols: ')
            new_constraint = self._derive_constraints(primary_predicate, instance, primary_vars, new_symbols, tables, extend= False)
        else:
            raise RuntimeError(f'cannot handle constraint type: {primary_constraint_type}')
            

        if new_constraint is not None:
            logger.info(f'new constraint for primary requirement: {new_constraint}')
            self.add(new_constraint, 'primary')


    def _derive_constraints(self, predicate, instance, source_vars, target_vars: Dict[str, List], orders, extend = False):
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
        

    def _declare_new_symbols(self, instance, tables):
        '''
            Declare new symbols.
            we do not need to change all existing concrete values, hence, we just need to call a solver to solve constraints to derive concrete values for new symbols.
        '''
        new_symbols = defaultdict(list)
        for tbl in tables:
            for atbl_name, rows in instance.create_row(tbl, {}).items():
                new_symbols[atbl_name].extend(rows)
                for row in rows:
                    self.add(list(row),  "variable")
        return new_symbols

    def reset(self):
        self.prev_operator = 'ROOT'
        c = [self.root_constraint]
        while c:
            op = c.pop()
            for k, child in op.children.items():
                child.delta.clear()
                child.tuples.clear()
                c.append(child)

    def advance(self, operator_key, operator_i):
        '''
            move the current path forward by one step
        '''
        curr_operator = f"{operator_key}_{operator_i}"
        self.prev_operator = curr_operator

    def __str__(self):
        return f"Trace(Constraint = {len(self.nodes)}, current = {self.prev_operator})"

    def __repr__(self):
        return self.pprint()
    
    def pprint(self):
        lines = []
        q = deque([self.root_constraint])
        while  q:
            node = q.popleft()
            lines.append(str(node))
            logger.info(node)
            if hasattr(node, 'children'):
                for bit, child in node.children.items():
                    q.append(child)
        return '\n'.join(lines)