
import logging
from sqlglot import exp
from collections import OrderedDict, deque, defaultdict
from typing import List, Tuple, Dict, Any, TYPE_CHECKING
from .constant import BranchType, Action, PathConstraintType
from .constraint import  Constraint
from .coverage import Coverage
from src.expr import SymbolOrName, get_all_symbols, substitute
import enum, random
if TYPE_CHECKING:
    from src.context import Context

logger = logging.getLogger('src.parseval.uexpr')

class UExprToConstraint:
    def __init__(self, context: 'Context', add):
        self.context: Context = context
        self.constraints: List[Constraint] = []
        self.nodes: Dict[str, Constraint] = {}
        self.leaves: Dict[str, Constraint] = {}
        self.root_constraint = Constraint(self, None, 'ROOT')
        self.positive_path = defaultdict(list)
        self.positive_path['ROOT'].append(self.root_constraint) ## we use this to cache all positive paths' operators.
        self.no_bit, self.yes_bit = '0', '1'
        self.current_operator = 'ROOT'
        self.add = add
        self.coverage = Coverage()

    def add_constraint(self, constraint: Constraint):
        self.constraints.append(constraint)
        self.nodes[constraint.identifier] = constraint
        if constraint.is_leaf():
            self.leaves[constraint.identifier] = constraint
    
    def which_branch(self, operator_key, operator_i, symbolic_exprs: List[SymbolOrName], conditions: List[exp.Condition], branch):
        assert len(symbolic_exprs) == len(conditions), f'the length of symbolic expressions should be equal to conditions'
        if len( symbolic_exprs) < 1:
            return
        for pc_idx in range(len(self.positive_path[self.current_operator])):
            node = self.positive_path[self.current_operator][pc_idx]
            for smt_expr, condition in zip(symbolic_exprs, conditions):
                node = node.add_child(operator_key, operator_i, condition, smt_expr, is_positive = branch, process_neg= True)
            if node.branch_type and f'{operator_key}_{operator_i}' not in self.positive_path:
                self.positive_path[f'{operator_key}_{operator_i}'].append(node)

    def _next_branch(self, target = 'positive'):
        '''
            if we find a node has not been covered(i.e. no constraints in the delta), we should either flip a constraint from sibiling or append a new tuple to the instance to cover this path.
        '''
        uncovered = []
        positive = []
        for _, node in self.leaves.items():
            if node.branch_type in {BranchType.POSITIVE, BranchType.STRAIGHT}:
                positive.append((node, node.get_path_to_root()[1:]))
            if not node.delta:
                path = node.get_path_to_root()
                uncovered.append((node, path[1:]))
        uncovered.sort(key = lambda x: len(x[1]), reverse= True)
        return uncovered[0] if uncovered else positive[0]
    
    def _determine_action(self, node: Constraint) -> Action:
        if node.constraint_type in {PathConstraintType.SIZE, PathConstraintType.PATH}:
            return Action.APPEND
        sibiling = node.sibiling()
        
        if len(sibiling.delta) > 1:
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

    def _create_smt_constraint_for_predicate(self, predicate, new_symbols: Dict[str, List], primary_tuple, constraint_type: PathConstraintType):
        '''
            handle the constraint of a node.
            For path constraint, we need to find a tuple that satisfies all predicates in the path(i.e. summation).
            we can use the affected tables to find all constraints. 
            no need to worry about whether the new symbols are useful or not. as the multipliticy will be 0 if it is not used.
        '''

        vars_ = get_all_symbols(predicate)
        tuples_ = set(self.context.get('symbol_to_tuple_id', str(v)) for v in vars_)
        if primary_tuple not in tuples_:
            return None
        new_constraint = predicate        
        for v in vars_:
            tbl, _, col_index = self.context.get('symbol_to_table', str(v))
            for row in new_symbols[tbl]:
                new_symbol = row[col_index]
                if constraint_type == PathConstraintType.PATH:                    
                    logger.info(f'replace: {v} --> {new_symbol}')
                    # new_constraint = extend_smt_clause(new_constraint, (v, new_symbol.expr))
                elif constraint_type == PathConstraintType.VALUE:
                    new_constraint = substitute(new_constraint, src = v, tar = new_symbol)
                    break
                else:
                    raise ValueError(f'Unknown constraint type: {constraint_type}')
        if constraint_type == PathConstraintType.PATH:
            logger.info(f'predicate: {predicate.expr}')
            logger.info(f'new constraint: {new_constraint}')
        return new_constraint
    
    def _handle_tuple_append(self, node: Constraint, path: List[Constraint]):
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
        primary_vars = get_all_symbols(primary_predicate)
        logger.info(f'primary predicate: {primary_predicate}')
        logger.info(f'primary vars: {primary_vars}')
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
        # constraints.extend(self._create_equality_assignment_constraint(reference_predicates))

        logger.info(f'constraints: {constraints}')
        for c in constraints:
            logger.info(f'c: {c}')
        # if node.branch_type:
        #     self.add(z3.And(*constraints), node.identifier, 'positive')
        # else:
        #     self.add(z3.And(*constraints), node.identifier, 'negative')
        return constraints
    
    def encode_constraint(self):
        '''
            encode all constraints to SMT
        '''
        uncovered = self._next_branch()
        if not uncovered:
            return Action.DONE
        
        node, path = uncovered
        action = self._determine_action(node)        
        if action == Action.UPDATE:
            self._handle_value_update(node, path)
        elif action == Action.APPEND:
            self._handle_tuple_append(node, path)
            

    
    
    def reset(self):
        ...

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
            for bit, child in node.children.items():
                q.append(child)
        return '\n'.join(lines)
