from dataclasses import dataclass, field
import logging
from collections import OrderedDict, deque, defaultdict
from typing import List, Tuple, Dict, Any, TYPE_CHECKING, Optional, Set
from .constant import BranchType, Action, PathConstraintType, OperatorKey, OperatorId, ConstraintId
from .constraint import Constraint, PlausibleChild
from src.expression.symbol import Expr, get_all_variables, Variable
from src.expression.visitors import substitute, extend_summation
import enum, random
if TYPE_CHECKING:
    from src.instance.instance import Instance
logger = logging.getLogger('src.parseval.uexpr')


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
    


    def which_branch(self, operator_key: OperatorKey, operator_i: OperatorId, predicates: List[Expr], sql_conditions: List, takens: List[bool], branch, metadata, tuples, **kwargs):
        """
            For each operator, we should track all possible predicates, since we are using concrete values, we could know which path is covered.
        """
        for node in self.positive_path[self.prev_operator]: #positive_nodes
            if node.operator_key != 'ROOT' and not node.tuples.intersection(tuples):
                continue
            for smt_expr, condition, taken in zip(predicates, sql_conditions, takens):

                node = node.add_child(operator_key, operator_i, condition, smt_expr, branch = branch, metadata = metadata, taken = taken, tuples = tuples)
                
            
            # if operator_key in {'project'}:
            #     self.positive_path[f'{operator_key}_{operator_i}'].clear()
            #     self.positive_path[f'{operator_key}_{operator_i}'].append(node)
            # el
            if branch and node not in self.positive_path[f'{operator_key}_{operator_i}']:
                self.positive_path[f'{operator_key}_{operator_i}'].append(node)

            # if branch and node not in self.positive_path[f'{operator_key}_{operator_i}']:
            #     self.positive_path[f'{operator_key}_{operator_i}'].append(node)
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

    def next_branch(self, instance: 'Instance', skips: Optional[Set] = None):
        '''
            if we find a node has not been covered(i.e. no constraints in the delta), we should either flip a constraint from sibiling or append a new tuple to the instance to cover this path.
        '''
        skips = skips if skips is not None else set()
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
        if not branches['uncovered'] and branches['positive']:
            plausible, pattern = branches['positive'][-1] #random.choice()
        elif branches['uncovered']:
            plausible, pattern = branches['uncovered'][0]
        if plausible:
            action = self._determine_action(plausible)
            if action == Action.APPEND:
                self._handle_tuple_append(instance, plausible, pattern)
        return pattern
       
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
        elif plausible.branch_type == BranchType.POSITIVE:
            node = plausible.parent

        path = node.get_path_to_root()[1:]
        affected_tables = defaultdict(set)
        for n in path:
            for inputref in n.metadata['table']:
                affected_tables[inputref.table].add(inputref.name)
        return affected_tables

    def _get_reference_predicate(self, plausible: PlausibleChild):
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
                predicate = parent_node.delta[-1]
            elif parent_node.operator_key in {'project'}:
                if parent_node.sql_condition.key == 'column':
                    ref = parent_node.sql_condition.args.get('ref')
                    null_constraints = [variable.is_null() for variable in parent_node.delta]
                    if parent_node.metadata['table'][ref].nullable and not any(null_constraints):
                        predicate = null_constraints.pop()
                    elif not parent_node.metadata['table'][ref].unique:
                        variables = random.choices(parent_node.delta, k= 2)
                        predicate = variables[0] == variables[1]
                    else:
                        raise ValueError(f'refered column should be either nullable or unique')

        assert predicate is not None
        return predicate, constraint_type, tables
    

    def _derive_constraint_from_filter(self, instance, node: Constraint, new_symbols: Dict[str, List], primary_table, primary_tuple_id):
        '''
            derive constraint from filter and join operators
        '''
        assert node.constraint_type in {PathConstraintType.VALUE, PathConstraintType.PATH}
        tables = node.get_tables()
        if node.operator_key == 'filter':
            logger.info(f'source node {node}, delta : {len(node.delta)}')
        new_constraint, source_vars = None, None
        if primary_table in node.get_tables():
            for predicate in node.delta:
                source_vars = get_all_variables(predicate)
                tuples_ = set(instance.symbol_to_tuple_id[v.this] for v in source_vars)
                if primary_tuple_id in tuples_:
                    ## we should ensure the primary tuple is in the tuples_, so that we can use it to replace the variables to create a row level constraint.
                    ## i.e. new created constraint will be at the same row as the primary predicate.
                    new_constraint = predicate
                    break
        else:
            new_constraint = node.delta[-1]
            source_vars = get_all_variables(new_constraint)        
        new_constraint = self._derive_constraints(new_constraint, instance, source_vars, new_symbols, tables, node.constraint_type == PathConstraintType.PATH)
        return new_constraint
    
    def _derive_constraint_from_project(self, instance, node: Constraint, new_symbols: Dict[str, List],primary_table, primary_tuple_id):
        assert node.constraint_type == PathConstraintType.SIZE
        # if node.operator_key == 'project':
        #     if node.sql_condition.key == 'column':
        #         ref = node.sql_condition.args.get('ref')
        #         null_constraints = [variable.is_null() for variable in node.delta]
        #         if not node.metadata['table'][ref].nullable and not any(null_constraints):
        #             predicate = null_constraints.pop()
        #         elif not node.metadata['table'][ref].unique:
        #             variables = random.choices(node.delta, k= 2)
        #             predicate = variables[0] == variables[1]
        return None

    def _handle_tuple_append(self, instance, plausible, pattern):
        involved_tables = self._get_involved_tables_path(plausible)
        primary_predicate, primary_constraint_type, tables = self._get_reference_predicate(plausible)

        primary_vars = get_all_variables(primary_predicate)
        primary_var = random.choice(list(primary_vars))
        primary_tuple_id = instance.symbol_to_tuple_id[primary_var.this]
        primary_table = instance.symbol_to_table[primary_var.this]
        new_symbols = self._declare_new_symbols(instance, involved_tables)        
        path = plausible.parent.get_path_to_root()[1:]
        for node in path:            
            if node.constraint_type in { PathConstraintType.VALUE, PathConstraintType.PATH}:
                constraint = self._derive_constraint_from_filter(instance, node, new_symbols= new_symbols, primary_table= primary_table, primary_tuple_id= primary_tuple_id)
            if node.constraint_type in {PathConstraintType.SIZE}:
                constraint = self._derive_constraint_from_project(instance, node, new_symbols= new_symbols, primary_table= primary_table, primary_tuple_id= primary_tuple_id)
            
            if constraint is not None:
                self.add(constraint, node.operator_key)
            constraint = None
        new_constraint = self._derive_constraints(primary_predicate, instance, primary_vars, new_symbols, tables, extend= primary_constraint_type == PathConstraintType.PATH)
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