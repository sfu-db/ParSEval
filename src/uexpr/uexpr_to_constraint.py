
import logging
from sqlglot import exp
from collections import OrderedDict, deque, defaultdict
from typing import List, Tuple, Dict, Any
from src.symbols import create_symbol, Symbols, logical_all, logical_any
from src.uexpr.constant import *
from .constraint import  Constraint
from .coverage import Coverage
from .helper import extend_smt_clause
import z3, enum, random

from .helper import get_all_vars

logger = logging.getLogger(__name__)



def is_the_same_tuple(context, expr1, expr2):
    '''
        Check if two expressions are working on the same tuple.
    '''
              


    
class UExprToConstraint:
    def __init__(self, add, context):
        self.context = context
        self.constraints = []
        self.nodes = {}
        self.leaves: Dict[str, Constraint] = {}
        self.root_constraint = Constraint(self, None, 'ROOT')
        self.nodes['ROOT'] = self.root_constraint

        self.instance = None

        self.no_bit, self.yes_bit = '0', '1'
        self.positive_path = defaultdict(list)
        self.positive_path['ROOT'].append(self.root_constraint) ## we use this to cache all positive paths' operators.
        self.current_operator = 'ROOT'
        self.add = add
        self.coverage = Coverage()

    def which_branch(self, operator_key, operator_i, symbolic_exprs: List[Symbols], conditions: List[exp.Condition], branch):
        assert len(symbolic_exprs) == len(conditions), f'the length of symbolic expressions should be equal to conditions'
        if len( symbolic_exprs) < 1:
            return
        for pc_idx in range(len(self.positive_path[self.current_operator])):
            node = self.positive_path[self.current_operator][pc_idx]
            for smt_expr, condition in zip(symbolic_exprs, conditions):
                node = node.add_child(operator_key, operator_i, condition, smt_expr, is_positive = branch, process_neg= True)
            if node.branch_type and f'{operator_key}_{operator_i}' not in self.positive_path:
                self.positive_path[f'{operator_key}_{operator_i}'].append(node)


    def count_dependents(self, pattern : str) -> int:
        leaves = [leaf for leaf in self.leaves if leaf.startswith(pattern)]
        return len(leaves)
            
    def _determine_action(self, node: Constraint) -> Action:
        if node.constraint_type in {PathConstraintType.SIZE, PathConstraintType.PATH}:
            return Action.APPEND
        sibiling = node.sibiling()
        
        if len(sibiling.delta) > 1:
            return Action.UPDATE
        return Action.APPEND

    

    def _next_branch(self, target = 'positive'):
        '''
            if we find a node has not been covered(i.e. no constraints in the delta), we should either flip a constraint from sibiling or append a new tuple to the instance to cover this path.
        '''
        uncovered = []
        positive = []
        for leaf, node in self.leaves.items():
            if node.branch_type in {BranchType.POSITIVE, BranchType.STRAIGHT}:
                positive.append((node, node.get_path_to_root()[1:]))
            if not node.delta:
                path = node.get_path_to_root()
                uncovered.append((node, path[1:]))
        uncovered.sort(key = lambda x: len(x[1]), reverse= True)
        return uncovered[0] if uncovered else None


    def encode_constraint(self):
        '''
            encode all constraints to SMT
        '''
        visited = set()
        uncovered = self._next_branch()
        if not uncovered:
            return Action.DONE
        
        node, path = uncovered
        action = self._determine_action(node)
        # logger.info(f'node: {node}, {node.constraint_type},  Action: {action}')
        if action == Action.UPDATE:
            self._handle_value_update(node, path)
        elif action == Action.APPEND:
            self._handle_size_append(node, path)

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
            for tbl in n.tables:
                affected_tables[tbl].update(n.tables[tbl])
        return affected_tables
    def _create_symbols_for_affected_tables(self, affected_tables: Dict[str, set]) -> Dict[str, List]:
        '''
            create symbols for each affected table
            Args:
                affected_tables: a dictionary that maps table name to a set of columns
            Returns:
                a dictionary that maps table name to a list of new tuples.  
        '''
        new_symbols = defaultdict(list)
        for tbl in affected_tables:
            for atbl_name, rows in self.instance.add_tuple(tbl, {}).items():
                new_symbols[atbl_name].extend(rows)
        return new_symbols
    

    def _create_smt_constraint_for_predicate(self, predicate, new_symbols: Dict[str, List], primary_tuple, constraint_type: PathConstraintType) -> z3.ExprRef:
        '''
            handle the constraint of a node.
            For path constraint, we need to find a tuple that satisfies all the predicates in the path(i.e. summation).
            we can use the affected tables to find all constraints. 
            no need to worry about whether the new symbols are useful or not. as the multipliticy will be 0 if it is not used.
        '''
        vars_ = get_all_vars(predicate.expr)
        tuples_ = set(self.context.get('symbol_to_tuple_id', str(v)) for v in vars_)
        if primary_tuple not in tuples_:
            return None
        new_constraint = predicate.expr
        logger.info(f'all vars: {vars_}')
        for v in vars_:
            tbl, _, col_index = self.context.get('symbol_to_table', str(v))
            for row in new_symbols[tbl]:
                new_symbol = row[col_index]
                if constraint_type == PathConstraintType.PATH:
                    logger.info(f'replace: {v} --> {new_symbol}')
                    new_constraint = extend_smt_clause(new_constraint, (v, new_symbol.expr))
                elif constraint_type == PathConstraintType.VALUE:
                    new_constraint = z3.substitute(new_constraint, (v, new_symbol.expr))
                    break
                else:
                    raise ValueError(f'Unknown constraint type: {constraint_type}')
        if constraint_type == PathConstraintType.PATH:
            logger.info(f'predicate: {predicate.expr}')
            logger.info(f'new constraint: {new_constraint}')
        return new_constraint


    def _create_equality_assignment_constraint(self, predicates: List[Symbols]) -> List[z3.ExprRef]:
        '''
            since we donot need to update the value of existing tuples, we could create equality constraints for existing tuples(i.e. symbol = concrete)
            Args:
                predicates: a list of predicates
            Returns:
                a list of equality constraints
        '''
        all_vars = set()
        for predicate in predicates:
            all_vars.update(get_all_vars(predicate.expr))
        constraints = []
        for v in all_vars:
            symbol =self.context.get('symbols', str(v))
            constraints.append(symbol.expr == symbol.value)
            if symbol.dtype == 'string':
                constraints.append(z3.Length(symbol.expr) > 0)
        return constraints




    def _handle_size_append(self, node: Constraint, path):
        '''
            Handle constraints that require new tuples
            Args:
                node: Constraint node(i.e. leaf node)
                path: Path from node to root            
        '''

        primary_predicate = None
        if node.delta:
            primary_predicate = random.choice(node.delta)
        elif node.sibiling().delta:
            primary_predicate = random.choice(node.sibiling().delta).__not__()
        else:
            return
        ### determine which tables, columns and tuples are directly involved in the reference predicate              
        primary_tables = defaultdict(set)
        primary_vars = get_all_vars(primary_predicate.expr)
        for vari in primary_vars:
            tbl, col, col_index = self.context.get('symbol_to_table', str(vari))
            primary_tables[tbl].update((col, col_index))
        primary_var = random.choice(list(primary_vars))
        primary_tuple = self.context.get('symbol_to_tuple_id', str(primary_var))
        
        ### determine which tables and columns are involved in the path
        affected_tables = self._get_affected_tables(path)
        ### create symbols for each affected table
        new_symbols = self._create_symbols_for_affected_tables(affected_tables)
        constraints = []
        reference_predicates = []
        for n in node.get_path_to_root()[1: -1]:
            for predicate in n.delta:
                constraint = self._create_smt_constraint_for_predicate(predicate, new_symbols, primary_tuple, n.constraint_type)
                if constraint is not None:
                    reference_predicates.append(predicate)
                    constraints.append(constraint)
                    break
        constraints.append(self._create_smt_constraint_for_predicate(primary_predicate, new_symbols, primary_tuple, node.constraint_type))
        constraints.extend(self._create_equality_assignment_constraint(reference_predicates))

        logger.info(f'constraints: {constraints}')
        if node.branch_type:
            self.add(z3.And(*constraints), node.identifier, 'positive')
        else:
            self.add(z3.And(*constraints), node.identifier, 'negative')
        return constraints


    def _handle_value_update(self, node: Constraint, path = None):
        '''
            flip a constraint from sibiling to cover this node
        '''
        sibiling = node.sibiling()
        if not sibiling or len(sibiling.delta) < 1:
            return
        reference_predicate = random.choice(sibiling.delta)
        if sibiling.constraint_type == PathConstraintType.VALUE:
            self.add(reference_predicate.__not__(), node.identifier, 'positive')
    

    

    def advance(self, operator_key, operator_i):
        '''
            move the current path forward by one step
        '''
        curr_operator = f"{operator_key}_{operator_i}"
        self.current_operator = curr_operator

    def get_longest_path(self):
        p = ''
        for pattern in self.leaves:
            if len(pattern) > len(p) and self.leaves[pattern].delta:
                p = pattern
        return self.leaves[p]
    
    def reset(self):
        self.current_cluster = self.root_constraint
        q = deque([self.root_constraint])
        while  q:
            node = q.popleft()
            if node.delta:
                node.delta.clear()
            for bit, child in node.children.items():
                q.append(child)

        self.current_operator = 'ROOT'
        
    def __str__(self):
        return f"Trace(Constraint = {len(self.nodes)}, current = {self.current_operator})"

    def pprint(self):
        q = deque([self.root_constraint])
        while  q:
            node = q.popleft()
            logger.info(node)
            for bit, child in node.children.items():
                q.append(child)

               

    def render_graph_graphviz(self):

        import pydot

        """
        Render the graphviz graph structure.

        Example to create a png:

        .. code-block::

            with open('somefile.png', 'wb') as file:
                file.write(session.render_graph_graphviz().create_png())

        :returns: Pydot object representing entire graph
        :rtype: pydot.Dot
        """
        dot_graph = pydot.Dot()
        q = deque([self.root_constraint])
        edges = []
        while  q:
            node = q.popleft()
            dot_node = node.render_node_graphviz()
            dot_graph.add_node(dot_node)
            for bit, child in node.children.items():
                q.append(child)
                edges.append((node.unique_id, child.unique_id, len(child.delta)))

        for edge in edges:
            src = edge[0]
            dst = edge[1]
            label = edge[2]
            color = 'blue' if label > 0 else 'red'
            dot_edge = pydot.Edge(src, dst, label = label, color = color)            
            dot_graph.add_edge(dot_edge)

        return dot_graph
    

