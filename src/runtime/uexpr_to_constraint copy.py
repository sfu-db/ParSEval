from dataclasses import dataclass, field
import logging
from collections import OrderedDict, deque, defaultdict
from typing import List, Tuple, Dict, Any, TYPE_CHECKING
from .constant import BranchType, Action, PathConstraintType, OperatorKey, OperatorId, ConstraintId
from .constraint import Constraint, PlausibleChild
from src.expression.symbol import Expr, get_all_variables
from src.expression.visitors import substitute
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

        self.current_operator = 'ROOT'
        self.add = add
    
    def add_constraint(self, constraint: Constraint):
        self.constraints.append(constraint)
        self.nodes[constraint.identifier] = constraint
        if constraint.is_leaf():
            self.leaves[constraint.identifier] = constraint
        

    def which_branch(self, operator_key: OperatorKey, operator_i: OperatorId, predicates: List[Expr], sql_conditions: List, branch, metadata):
        if len(predicates) < 1:
            return
        positive_nodes = [] if self.leaves else [self.root_constraint]
        for leaf in list(self.leaves.keys()):
            plausible_node = self.leaves[leaf]
            if plausible_node.branch_type:
                positive_nodes.append(plausible_node.parent)
        
        for node in positive_nodes: # self.positive_path[self.current_operator]
            for smt_expr, condition in zip(predicates, sql_conditions):
                node = node.add_child(operator_key, operator_i, condition, smt_expr, branch = branch, metadata = metadata)
            logger.info(f'node: {node}')
            if branch and f'{operator_key}_{operator_i}' not in self.positive_path:
                self.positive_path[f'{operator_key}_{operator_i}'].append(node)


    def _next_branch(self, target = 'positive'):
        '''
            if we find a node has not been covered(i.e. no constraints in the delta), we should either flip a constraint from sibiling or append a new tuple to the instance to cover this path.
        '''
        uncovered = []
        positive = []
        for pattern, plausible in self.leaves.items():
            parent = plausible.parent
            if plausible.branch_type in {BranchType.POSITIVE, BranchType.STRAIGHT}:
                positive.append((plausible, parent.get_path_to_root()[1:]))
            
            if not parent.delta:
                path = parent.get_path_to_root()
                uncovered.append((parent, path[1:]))
        uncovered.sort(key = lambda x: len(x[1]), reverse= True)
        logger.info(f'uncovered: {uncovered}')
        logger.info(f'positive: {positive}')
        return uncovered[0] if uncovered else positive[0]
    def _next_branch(self):
        positive = []
        negative = []
        uncovered = []
        for pattern, plausible in self.leaves.items():
            if plausible.branch_type in {BranchType.POSITIVE, BranchType.STRAIGHT}:
                positive.append(plausible)
            elif plausible.branch_type == BranchType.NEGATIVE:
                negative.append(plausible)
            elif plausible.branch_type == BranchType.PLAUSIBLE:
                uncovered.append(plausible)
        
        return uncovered[0] if uncovered else positive[0]



    def _determine_action(self, node: Constraint) -> Action:
        if node.constraint_type in {PathConstraintType.SIZE, PathConstraintType.PATH}:
            return Action.APPEND
        sibiling = node.sibiling()
        if isinstance(sibiling, Constraint) and len(sibiling.delta) > 1:
            return Action.UPDATE
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
            for item in n.metadata['table']:
                for table_name, columns in item.items():
                    affected_tables[table_name].update(columns)
        return affected_tables

    def next_branch(self, instance: 'Instance'):
        uncovered = self._next_branch()
        if not uncovered:
            return Action.DONE
        
        node, path = uncovered
        action = self._determine_action(node)
        if action == Action.UPDATE:
            self._handle_value_update(node, path)
        elif action == Action.APPEND:
            self._handle_tuple_append(instance, node, path)
        return action

    def _handle_value_update(self, node, path):
        '''
            flip a constraint from sibiling to cover this node
        '''
        sibiling = node.sibiling()
        if not sibiling or len(sibiling.delta) < 1:
            return
        reference_predicate = random.choice(sibiling.delta)
        if sibiling.constraint_type == PathConstraintType.VALUE:
            self.add(reference_predicate.not_(), node.identifier, node.branch_type)

    def _create_smt_constraint_for_predicate(self, instance, predicate, new_symbols: Dict[str, List], primary_tuple_id, constraint_type: PathConstraintType):
        '''
        Given an existing predicate, we need to replace variables with new ones.
            handle the constraint of a node.
            For path constraint, we need to find a tuple that satisfies all predicates in the path(i.e. summation).
            we can use the affected tables to find all constraints. 
            no need to worry about whether the new symbols are useful or not, as the multipliticy will be 0 if it is not used.
        '''
        vars_ = get_all_variables(predicate)
        tuples_ = set(instance.symbol_to_tuple_id[v.this] for v in vars_)
        ## we should ensure the primary tuple is in the tuples_, so that we can use it to replace the variables to create a row level constraint.
        ## i.e. new created constraint will be at the same row as the primary predicate.
        if primary_tuple_id not in tuples_:
            return None
        new_constraint = predicate
        mapping = {}
        for v in vars_:
            tbl, _, col_index = instance.symbol_to_table[v.this]
            for row in new_symbols[tbl]:
                new_symbol = row[col_index]
                mapping[v] = new_symbol

        if constraint_type == PathConstraintType.VALUE:
            new_constraint = substitute(new_constraint, mapping)
        elif constraint_type == PathConstraintType.PATH:
            ...
        else:
            raise ValueError(f'Unknown constraint type: {constraint_type}')
        if constraint_type == PathConstraintType.PATH:
            logger.info(f'predicate: {predicate.expr}')
            logger.info(f'new constraint: {new_constraint}')
        return new_constraint
    
    def _handle_tuple_append(self, instance, node, path):
        primary_predicate = None
        if node.delta:
            primary_predicate = random.choice(node.delta)
        elif node.sibiling().delta:
            primary_predicate = random.choice(node.sibiling().delta).not_()
        else:
            return
        ### determine which tables, columns and tuples are directly involved in the reference predicate 
        primary_tables = defaultdict(set)
        primary_vars = get_all_variables(primary_predicate)
        for vari in primary_vars:
            tbl, col, col_index = instance.symbol_to_table[vari.this]
            primary_tables[tbl].update((col, col_index))
        logger.info(f'primary predicate: {primary_predicate}')
        # logger.info(f'primary vars: {primary_vars}')
        primary_var = random.choice(list(primary_vars))
        primary_tuple_id = instance.symbol_to_tuple_id[primary_var.this]

        affected_tables = self._get_affected_tables(path)
        new_symbols = defaultdict(list)
        for tbl in affected_tables:
            for atbl_name, rows in instance.create_row(tbl, {}).items():
                new_symbols[atbl_name].extend(rows)
        constraints = []
        # reference_predicates = []
        for n in node.get_path_to_root()[1: -1]:
            for predicate in n.delta:
                constraint = self._create_smt_constraint_for_predicate(instance, predicate, new_symbols, primary_tuple_id, n.constraint_type)
                if constraint is not None:
                    # reference_predicates.append(predicate)
                    constraints.append(constraint)
                    break

        constraints.append(self._create_smt_constraint_for_predicate(instance, primary_predicate, new_symbols, primary_tuple_id, node.constraint_type))
        for c in constraints:
            self.add(c, node.identifier, node.branch_type)


    def reset(self):
        self.current_operator = 'ROOT'
        c = [self.root_constraint]
        while c:
            op = c.pop()
            for k, child in op.children.items():
                child.delta.clear()
                c.append(child)
        

    def advance(self, operator_key, operator_i):
        '''
            move the current path forward by one step
        '''
        curr_operator = f"{operator_key}_{operator_i}"
        self.current_operator = curr_operator

    def __str__(self):
        return f"Trace(Constraint = {len(self.nodes)}, current = {self.current_operator})"

    def __repr__(self):
        return self.pprint()
    
    def pprint(self):
        lines = []
        q = deque([self.root_constraint])
        while  q:
            node = q.popleft()
            lines.append(str(node))
            if hasattr(node, 'children'):
                for bit, child in node.children.items():
                    q.append(child)
        return '\n'.join(lines)