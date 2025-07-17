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

NO_BIT = 0
NO_BIT2 = -1
YES_BIT = 1
NULL_BIT = 2
SIZE_BIT = 3

class UExprToConstraint:
    def __init__(self, add):
        self.constraints: List[Constraint] = []
        self.nodes: Dict[str, Constraint] = {}
        self.leaves: Dict[str, PlausibleChild] = {}
        self.root_constraint = Constraint(self, None, 'ROOT', None)
        self.positive_path = defaultdict(list)
        self.positive_path['ROOT'].append(self.root_constraint) ## we use this to cache all positive paths' operators.
        
        self.prev_operator = 'ROOT'
        self.add = add
    
    def add_constraint(self, constraint: Constraint):
        self.constraints.append(constraint)
        self.nodes[constraint.identifier] = constraint
        if constraint.is_leaf():
            self.leaves[constraint.identifier] = constraint
    

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
        '''move the current path forward by one step'''
        self.prev_operator =  f"{operator_key}_{operator_i}"


    def which_branch(self, operator_key: OperatorKey, operator_i: OperatorId, predicates: List[Expr], sql_conditions: List, takens: List[bool], branch, tbl_exprs: List, tuples, **kwargs):
        """
        Enhanced which_branch function that captures all predicates in each operator.
        Args:
            operator_key: The type of operator (filter, join, project, etc.)
            operator_i: The operator identifier
            predicates: List of symbolic expressions representing predicates
            sql_conditions: List of SQL conditions corresponding to predicates
            takens: List of boolean values indicating which predicates were taken
            branch: Overall branch result (True/False)
            tbl_exprs: List of metadata information for each predicate
            tuples: List of tuple data that satisfies each predicate
        """
        # current_nodes = [leave.parent for leave in self.leaves if isinstance(leave.branch_type, BranchType.POSITIVE)]
        current_nodes = self.positive_path.get(self.prev_operator, [])
        assert current_nodes, 'There should be at least one positive node for operator %s, current is %s'  % (self.prev_operator, self.positive_path)
        for node in current_nodes:
            # Skip nodes that don't have relevant tuples (for non-root nodes)
            if node.operator_key != 'ROOT' and not node.get_all_tuples().intersection(tuples):
                continue
            for smt_expr, sql_condition, taken in zip(predicates, sql_conditions, takens):
                node = node.add_child(operator_key, operator_i, sql_condition, smt_expr, branch = branch, tbl_exprs = tbl_exprs, taken = taken, tuples = tuples, **kwargs)

            if branch and node not in self.positive_path[f'{operator_key}_{operator_i}']:
                self.positive_path[f'{operator_key}_{operator_i}'].append(node)
        
  

    # def which_branch(self, operator_key: OperatorKey, operator_i: OperatorId, predicates: List[Expr], sql_conditions: List, takens: List[bool], branch, infos: List[Dict[str, Any]], tuples, **kwargs):
    #     """
    #     Enhanced which_branch function that captures all predicates in each operator.
        
    #     Args:
    #         operator_key: The type of operator (filter, join, project, etc.)
    #         operator_i: The operator identifier
    #         predicates: List of symbolic expressions representing predicates
    #         sql_conditions: List of SQL conditions corresponding to predicates
    #         takens: List of boolean values indicating which predicates were taken
    #         branch: Overall branch result (True/False)
    #         infos: List of metadata information for each predicate
    #         tuples: List of tuple data that satisfies each predicate
    #     """
    #     assert len(infos) == len(sql_conditions), f"infos: {len(infos)}, sql_conditions: {len(sql_conditions)}"
    #     assert len(predicates) == len(sql_conditions), f"predicates: {len(predicates)}, sql_conditions: {len(sql_conditions)}"
    #     assert len(takens) == len(predicates), f"takens: {len(takens)}, predicates: {len(predicates)}"
        
    #     # Get current positive nodes to process
    #     current_nodes = self.positive_path.get(self.prev_operator, [])
    #     if not current_nodes:
    #         current_nodes = [self.root_constraint]
        
    #     # Process each predicate for each current node
    #     for node in current_nodes:
    #         # Skip nodes that don't have relevant tuples (for non-root nodes)
    #         if node.operator_key != 'ROOT' and not node.get_all_tuples().intersection(tuples):
    #             continue
            
    #         # Process each predicate individually for detailed tracking
    #         for i, (smt_expr, condition, taken, info) in enumerate(zip(predicates, sql_conditions, takens, infos)):
    #             # Create child node for this specific predicate
    #             child_node = node.add_child(
    #                 operator_key, 
    #                 operator_i, 
    #                 condition, 
    #                 smt_expr, 
    #                 branch=branch, 
    #                 info=info, 
    #                 taken=taken, 
    #                 tuples=tuples[i] if isinstance(tuples, list) and i < len(tuples) else tuples,
    #                 **kwargs
    #             )
                
    #             # Add to positive path if this is a positive branch
    #             if branch and child_node not in self.positive_path.get(f'{operator_key}_{operator_i}', []):
    #                 if f'{operator_key}_{operator_i}' not in self.positive_path:
    #                     self.positive_path[f'{operator_key}_{operator_i}'] = []
    #                 self.positive_path[f'{operator_key}_{operator_i}'].append(child_node)
            
    #         # Also create aggregate constraint for the entire operator if multiple predicates
    #         if len(predicates) > 1:
    #             self._create_aggregate_constraint(node, operator_key, operator_i, predicates, sql_conditions, takens, branch, infos, tuples, **kwargs)
    
    def _update_coverage_stats(self, node: 'Constraint', smt_expr: Expr, taken: bool, info: Dict[str, Any]):
        """Update coverage statistics for a constraint node."""
        # Calculate complexity score based on predicate characteristics
        complexity_factors = []
        
        # Factor 1: Operator type complexity
        operator_complexity = {
            'filter': 1.0,
            'join': 2.0,
            'project': 0.5,
            'aggregate': 1.5,
            'sort': 0.8,
            'union': 1.2,
            'intersect': 1.2,
            'minus': 1.2
        }
        complexity_factors.append(operator_complexity.get(node.operator_key, 1.0))
        
        # Factor 2: Constraint type complexity
        constraint_complexity = {
            PathConstraintType.VALUE: 1.0,
            PathConstraintType.PATH: 1.5,
            PathConstraintType.SIZE: 2.0
        }
        complexity_factors.append(constraint_complexity.get(node.constraint_type, 1.0))
        
        # Factor 3: Number of tables involved
        table_count = len(node.get_tables()) if node.get_tables() else 1
        complexity_factors.append(min(table_count / 2.0, 3.0))
        
        # Factor 4: Predicate complexity (based on expression structure)
        expr_complexity = self._calculate_expression_complexity(smt_expr)
        complexity_factors.append(expr_complexity)
        
        # Update node complexity score
        node.complexity_score = sum(complexity_factors) / len(complexity_factors)
        
        # Log coverage information
        logger.debug(f"Updated coverage for {node}: taken={taken}, complexity={node.complexity_score:.2f}")
    
    def _calculate_expression_complexity(self, expr: Expr) -> float:
        """Calculate complexity of a symbolic expression."""
        if expr is None:
            return 1.0
        
        # Simple complexity calculation based on expression structure
        complexity = 1.0
        
        # Check for nested operations
        if hasattr(expr, 'operands') and expr.operands:
            complexity += len(expr.operands) * 0.5
        
        # Check for special operators
        if hasattr(expr, 'key'):
            if expr.key in ['and', 'or', 'not']:
                complexity += 0.5
            elif expr.key in ['gt', 'lt', 'gte', 'lte', 'eq', 'neq']:
                complexity += 0.3
            elif expr.key in ['like', 'in', 'between']:
                complexity += 0.8
        
        return min(complexity, 5.0)  # Cap at 5.0
    
    def _create_aggregate_constraint(self, parent_node: 'Constraint', operator_key: str, operator_i: str, 
                                   predicates: List[Expr], sql_conditions: List, takens: List[bool], 
                                   branch: bool, infos: List[Dict[str, Any]], tuples, **kwargs):
        """Create an aggregate constraint that represents the overall operator behavior."""
        # Create a combined predicate that represents the entire operator
        if len(predicates) == 1:
            combined_predicate = predicates[0]
        else:
            # For multiple predicates, create a logical combination
            if operator_key in ['filter']:
                # For filters, use AND combination
                combined_predicate = predicates[0]
                for pred in predicates[1:]:
                    combined_predicate = combined_predicate.and_(pred)
            elif operator_key in ['join']:
                # For joins, use OR combination of successful matches
                combined_predicate = predicates[0]
                for pred in predicates[1:]:
                    combined_predicate = combined_predicate.or_(pred)
            else:
                # Default to first predicate
                combined_predicate = predicates[0]
        
        # Create aggregate condition
        combined_condition = sql_conditions[0] if sql_conditions else None
        
        # Create aggregate info
        combined_info = infos[0] if infos else {}
        
        # Create aggregate constraint node
        aggregate_node = parent_node.add_child(
            operator_key,
            f"{operator_i}_aggregate",
            combined_condition,
            combined_predicate,
            branch=branch,
            info=combined_info,
            taken=branch,
            tuples=tuples,
            **kwargs
        )
        
        # Mark as aggregate constraint
        aggregate_node.constraint_type = PathConstraintType.SIZE
        aggregate_node.complexity_score = parent_node.complexity_score * 1.2  # Slightly more complex
        
        logger.debug(f"Created aggregate constraint for {operator_key}_{operator_i}")
    
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
        logger.info(f'testing plausible: {plausible.branch_type} is covered')
        if plausible.branch_type in {BranchType.POSITIVE, BranchType.STRAIGHT}:
            parent_node: Constraint = plausible.parent

            # if parent_node.constraint_type in {PathConstraintType.PATH, PathConstraintType.VALUE}:
            return len(parent_node.delta) >= BRANCH_HIT
            # return True
            # elif parent_node.constraint_type in {PathConstraintType.SIZE}:
            #     if parent_node.operator_key in {'aggregate', 'aggfunc'}:
            #         """we consider group count and group size here"""
            #         return self._is_covered_aggregate(parent_node)
            #     if len(parent_node.delta) < BRANCH_HIT:
            #         return False
            #     null_values = [variable.is_null() for variable in parent_node.delta]
            #     # ref = parent_node.sql_condition.args.get('ref')
            #     if parent_node.info['table'][0].nullable and not any(null_values):
            #         return False
            #     if not parent_node.info['table'][0].unique:
            #         duplicates = [v.value for v in parent_node.delta]
            #         return len(set(duplicates)) != len(parent_node.delta)
            #     return True
        
        if plausible.branch_type in {BranchType.UNIQUE}:
            ...
        if plausible.branch_type in {BranchType.NULLABLE}:
            parent_node = plausible.parent
            null_values = [variable.is_null() for variable in parent_node.delta]

            logger.info(f'plausible branch type : {plausible.branch_type} --> NULL : {any(null_values)}')
            if parent_node.info['table'][0].nullable and not any(null_values):
                return False
            
            return True
            
        
        raise RuntimeError(f"encounter unsupported positive branches: {plausible.branch_type}")
        
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
            for inputref in n.tbl_exprs:
                affected_tables[inputref.table].add(inputref.name)
        return affected_tables


    def next_branch(self, instance: 'Instance', skips: Optional[Set] = None, solved: Optional[List] = None):
        '''
            Traverse all leaves 
        '''
        skips = skips or set()
        solved = solved or []
        branches = {
            'positive': [],
            'plausible': [],
            'rplausible': [],
            'nullable': [],
            'size': [],
            'negative': [],
            'unreachable': []
        }
        for pattern, plausible in self.leaves.items():
            if pattern in skips:
                continue
            if plausible.branch_type in {BranchType.POSITIVE}:
                branches['positive'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.NEGATIVE:
                branches['negative'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.PLAUSIBLE:
                branches['plausible'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.RPLAUSIBLE:
                branches['rplausible'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.NULLABLE:
                branches['nullable'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.SIZE:
                branches['size'].append((plausible, pattern))
            else:
                branches['unreachable'].append((plausible, pattern))

        if branches['positive']:
            '''
                For all positive branches, we should consider number of hits
            '''
            positives = sorted(branches['positive'], key = lambda node: len(node[0].parent.delta))
            for plausible, pattern in positives:
                if not plausible.is_covered():
                    self._handle_tuple_append(instance, plausible, pattern)
                    return pattern
        if branches['plausible']:
            plausibles = sorted(filter(lambda x: x[1] not in skips and solved.count(x[1]) < MAX_RETRY,  branches['plausible']), key = lambda node: len(node[1]), reverse= True)
            if plausibles:
                plausible, pattern = plausibles[0]
                self._handle_tuple_append(instance, plausible, pattern)
                return pattern
        return None


    def next_branch2(self, instance: 'Instance', skips: Optional[Set] = None, solved: Optional[List] = None):
        '''
            if we find a node has not been covered(i.e. no constraints in the delta), we should either flip a constraint from sibiling or append a new tuple to the instance to cover this path.
        '''
        skips = skips if skips is not None else set()
        solved = solved if solved is not None else []
        branches = {
            'positive': [],
            'negative': [],
            'uncovered': [],
            'nullable': [],
            'unreachable': []
        }
        for pattern, plausible in self.leaves.items():
            if plausible.branch_type in {BranchType.POSITIVE, BranchType.STRAIGHT}:
                branches['positive'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.NEGATIVE:
                branches['negative'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.PLAUSIBLE and pattern not in skips:
                branches['uncovered'].append((plausible, pattern))
            elif plausible.branch_type == BranchType.NULLABLE and pattern not in skips:
                branches['nullable'].append((plausible, pattern))
            else:
                branches['unreachable'].append((plausible, pattern))
        plausible, pattern = None, None
        
        if branches['positive']:
            '''
                For all positive branches, we should consider number of hits
            '''
            positives = sorted(branches['positive'], key = lambda node: len(node[0].parent.delta))
            for plausible, pattern in positives:
                is_covered = self._is_covered(plausible)
                if not is_covered and pattern not in skips:
                    self._handle_tuple_append(instance, plausible, pattern)
                    return pattern
        #  NULL values, Duplicate, SIZE
        uncovered = branches['uncovered']
        uncovered = sorted(filter(lambda x: x[1] not in skips and solved.count(x[1]) < MAX_RETRY, uncovered), key = lambda node: len(node[1]), reverse= True)
        if uncovered:
            plausible, pattern = uncovered[0]
            self._handle_tuple_append(instance, plausible, pattern)
            return pattern
        if branches['nullable']:
            logger.info('thhħe branch is nullable')
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
        if plausible.branch_type in {BranchType.PLAUSIBLE, BranchType.RPLAUSIBLE}:
            for bit, child in plausible.parent.children.items():
                
                if isinstance(child, Constraint):
                    node = child
                    break
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

    def _handle_tuple_append(self, instance, plausible, pattern):
        '''
            Increasing the relation size
        '''
        involved_tables = self._get_involved_tables_path(plausible)

        ref_predicate, ref_constraint_type, tables = get_reference_predicate(plausible)
        ref_vars = get_all_variables(ref_predicate)
        ref_var = random.choice(list(ref_vars))
        ref_tuple_id = instance.symbol_to_tuple_id[ref_var.this]
        ref_table, _, _ = instance.symbol_to_table[ref_var.this]

        new_symbols = self._declare_new_symbols(instance, involved_tables)

        for node in self._get_involved_nodes(plausible):
            constraint = adapt_constraint(instance, node, new_symbols, ref_table, ref_tuple_id)
            if constraint is not None:
                self.add(constraint, node.operator_key)
            constraint = None

        # primary_predicate, primary_constraint_type, tables = get_reference_predicate(plausible)

        # primary_vars = get_all_variables(primary_predicate)
        # primary_var = random.choice(list(primary_vars))
        # primary_tuple_id = instance.symbol_to_tuple_id[primary_var.this]
        # primary_table, _, _ = instance.symbol_to_table[primary_var.this]

        # new_symbols = self._declare_new_symbols(instance, involved_tables)
        # for node in self._get_involved_nodes(plausible):
            # constraint = adapt_constraint(instance, node, new_symbols, primary_table, primary_tuple_id)            
        #     if constraint is not None:
        #         self.add(constraint, node.operator_key)
        #     constraint = None
        
        # if primary_constraint_type in {PathConstraintType.PATH, PathConstraintType.VALUE}:
        #     new_constraint = self._derive_constraints(primary_predicate, instance, primary_vars, new_symbols, tables, extend= primary_constraint_type == PathConstraintType.PATH)
        # elif primary_constraint_type in {PathConstraintType.SIZE}:
        #     logger.info(f'primary constraint : {primary_predicate}, primary vars: {primary_vars}, new_symbols: ')
        #     new_constraint = self._derive_constraints(primary_predicate, instance, primary_vars, new_symbols, tables, extend= False)
        # else:
        #     raise RuntimeError(f'cannot handle constraint type: {primary_constraint_type}')

        # if new_constraint is not None:
        #     logger.info(f'new constraint for primary requirement: {new_constraint}')
        #     self.add(new_constraint, 'primary')

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

    def get_coverage_report(self) -> Dict[str, Any]:
        """Generate a comprehensive coverage report for the constraint tree."""
        report = {
            'total_constraints': 0,
            'covered_constraints': 0,
            'coverage_ratio': 0.0,
            'operator_coverage': {},
            'uncovered_paths': [],
            'complexity_distribution': {},
            'recommendations': []
        }
        
        # Collect all constraint nodes
        all_nodes = self._collect_all_nodes(self.root_constraint)
        
        for node in all_nodes:
            if isinstance(node, Constraint):
                report['total_constraints'] += 1
                
                # Update operator coverage
                op_key = f"{node.operator_key}_{node.operator_i}"
                if op_key not in report['operator_coverage']:
                    report['operator_coverage'][op_key] = {
                        'total': 0,
                        'covered': 0,
                        'coverage_ratio': 0.0
                    }
                
                report['operator_coverage'][op_key]['total'] += 1
                
                if node.is_covered():
                    report['covered_constraints'] += 1
                    report['operator_coverage'][op_key]['covered'] += 1
                
                # Track complexity distribution
                complexity_bucket = int(node.complexity_score)
                report['complexity_distribution'][complexity_bucket] = report['complexity_distribution'].get(complexity_bucket, 0) + 1
                
                # Find uncovered paths
                if not node.is_covered():
                    report['uncovered_paths'].append({
                        'pattern': node.pattern(),
                        'operator': op_key,
                        'constraint_type': node.constraint_type,
                        'complexity': node.complexity_score,
                        'sql_condition': str(node.sql_condition) if node.sql_condition else None
                    })
        
        # Calculate overall coverage ratio
        if report['total_constraints'] > 0:
            report['coverage_ratio'] = report['covered_constraints'] / report['total_constraints']
        
        # Calculate operator-specific coverage ratios
        for op_key, stats in report['operator_coverage'].items():
            if stats['total'] > 0:
                stats['coverage_ratio'] = stats['covered'] / stats['total']
        
        # Generate recommendations
        report['recommendations'] = self._generate_coverage_recommendations(report)
        
        return report
    
    def _collect_all_nodes(self, node: 'Constraint') -> List['Constraint']:
        """Recursively collect all constraint nodes in the tree."""
        nodes = [node]
        for child in node.children.values():
            if isinstance(child, Constraint):
                nodes.extend(self._collect_all_nodes(child))
        return nodes
    
    def _generate_coverage_recommendations(self, report: Dict[str, Any]) -> List[str]:
        """Generate recommendations for improving coverage."""
        recommendations = []
        
        # Overall coverage recommendations
        if report['coverage_ratio'] < 0.5:
            recommendations.append("Overall coverage is low. Focus on high-complexity operators first.")
        
        # Operator-specific recommendations
        for op_key, stats in report['operator_coverage'].items():
            if stats['coverage_ratio'] < 0.3:
                recommendations.append(f"Low coverage for {op_key}: {stats['coverage_ratio']:.2%}. Consider adding test cases.")
        
        # Complexity-based recommendations
        high_complexity_count = sum(count for bucket, count in report['complexity_distribution'].items() if bucket >= 3)
        if high_complexity_count > len(report['complexity_distribution']) * 0.5:
            recommendations.append("Many high-complexity constraints detected. Consider simplifying query structure.")
        
        # Uncovered paths recommendations
        if len(report['uncovered_paths']) > 0:
            recommendations.append(f"Found {len(report['uncovered_paths'])} uncovered paths. Prioritize exploration of these paths.")
        
        return recommendations