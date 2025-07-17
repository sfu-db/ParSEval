from __future__ import annotations
from typing import List, Optional, Dict, Union, Set, Any, Tuple
from .constant import *
from sqlglot import exp

from .helper import negate_sql_condition
from src.expression.symbol import Expr
import logging

logger = logging.getLogger('src.parseval.constraint')

BRANCH_HIT = 1
MAX_RETRY = 2
MINIMIAL_GROUP_COUNT = 3
MINIMIAL_GROUP_SIZE = 3

class PlausibleChild:
    """
    Represents possible behaviors of an operator in the constraint tree.

    Attributes:
        parent: The parent Constraint Node
        branch_type: The type of branch(POSITIVE, NEGATIVE, NULLABLE, etc.)
        tree: The constraint tree
    """
    def __init__(self, parent, branch_type: BranchType, tree):
        self.parent = parent
        self.branch_type = branch_type
        self.tree = tree

    def __str__(self):
        return 'PlausibleChild[%s]' % (self.parent.pattern() + ':' + str(self.branch_type))
    def __repr__(self):
        return 'PlausibleChild[%s]' % (self.parent.pattern() + ':' + str(self.branch_type))

    @property
    def covered(self):
        return len(self.parent.delta) or 0
    
    def bit(self):
        if self.parent:
            for bit, node in self.parent.children.items():
                if node == self:
                    return bit
        return "0"

    def is_covered(self):
        if self.branch_type in {BranchType.POSITIVE}:
            return len(self.parent.delta) >= BRANCH_HIT
        
        if self.branch_type in {BranchType.NULLABLE}:
            null_values = [variable.is_null() for variable in self.parent.delta]
            if self.parent.tbl_exprs[0].nullable and not any(null_values):
                return False
            return True
        
        if self.branch_type in {BranchType.SIZE}:
            ...

        # if plausible.branch_type in {BranchType.NULLABLE}:
    #         parent_node = plausible.parent
    #         null_values = [variable.is_null() for variable in parent_node.delta]
    #         logger.info(f'plausible branch type : {plausible.branch_type} --> NULL : {any(null_values)}')
    #         if parent_node.info['table'][0].nullable and not any(null_values):
    #             return False
    #         return True

 
class Constraint:
    """
    Represents a node in the constraint tree for SQL logical plan execution path tracing.

    Each Constraint instance corresponds to a logical operator (e.g., filter, join, aggregate) in the plan and tracks the symbolic constraints (predicates), SQL conditions, and execution path information for that operator.

    The constraint tree is used to:
      - Trace all possible execution paths through the logical plan.
      - Track which branches (paths) have been covered by test cases.
      - Label and explore unexplored or interesting branches for further test generation.
      - Store metadata and statistics for coverage analysis and reporting.

    Key Attributes:
        tree: Reference to the constraint tree (UExprToConstraint) this node belongs to.
        parent: Parent Constraint node (None for root).
        children: Dictionary mapping branch bits (e.g., yes/no/null) to child Constraint or PlausibleChild nodes.
        operator_key: The logical operator type (e.g., 'filter', 'join').
        operator_i: Unique identifier for the operator instance.
        delta: List of symbolic expressions (Expr) representing the path constraints at this node.
        sql_condition: The SQL condition (predicate) associated with this node.
        taken: Boolean indicating if this branch was taken in execution.
        constraint_type: The type of constraint (VALUE, PATH, SIZE, etc.).
        info: Metadata dictionary (e.g., table/column info, group stats).
        tuples: List of tuples (row data) that satisfy this constraint.
        _pattern: Cached string representing the path from root to this node.
        path: Cached list of Constraint nodes from root to this node.
        tables: Cached list of involved tables for this constraint.

    Methods:
        - add_satisfying_tuple: Register a tuple that satisfies this constraint.
        - get_coverage_ratio / is_covered: Coverage statistics for this node.
        - get_tables / get_all_tuples: Table and tuple tracking utilities.
        - upsert_plausible_node: Add or update plausible (unexplored) child nodes.
        - pattern / get_path_to_root: Path tracing utilities.
        - Various helpers for sibling/branch navigation.

    Usage:
        The Constraint class is central to tracking and exploring execution paths in the logical plan. It enables systematic test case generation by identifying uncovered or interesting branches and providing the necessary context to synthesize new inputs.
    """
    
    def __init__(self, 
                 tree, 
                 parent: Optional[Constraint], 
                 operator_key: OperatorKey,
                 operator_i: OperatorId,
                 delta: List = None,
                 sql_condition: exp.Condition = None, 
                 taken: Optional[bool] = None, 
                 constraint_type: PathConstraintType = PathConstraintType.UNKNOWN,
                 tbl_exprs: Optional[List[Any]] = None,
                 info: Optional[Dict[str, Any]] = None):
        
        self.tree = tree
        self.parent: Optional[Constraint] = parent
        self.children: Dict[str, Constraint] = {}  #   List[Constraint] = []
        self.operator_key = operator_key
        self.operator_i = operator_i
        self.delta:List[Expr] = delta or []
        self.sql_condition = sql_condition
        self.taken = taken
        self.constraint_type = constraint_type
        self.tbl_exprs = tbl_exprs or []
        self.info = info or {}
        self.tuples: List[List] = []
        self._pattern = None
        self.path = None
        self.tables = None
        

    # def no(self):
    #     return self.children.get(self.tree.no_bit, None)
    
    # def yes(self):
    #     return self.children.get(self.tree.yes_bit, None)
    
    # def null_bit(self):
    #     return self.children.get(self.tree.null_bit, None)

    # def has_sibling(self):
    #     return self.no() is not None and self.yes() is not None
    
    # def sibling(self) -> Constraint:
    #     if self.bit() == self.tree.no_bit :
    #         return self.parent.yes()
    #     return self.parent.no()
    
    def bit(self):
        if self.parent:
            for bit, child in self.parent.children.items():
                if child == self:
                    return bit
        return self.tree.no_bit
    

    def get_tables(self) -> List:
        if self.tables:
            return self.tables
        tables = set()
        for inputref in self.tbl_exprs:
            tables.update(inputref.table)
        self.tables = list(tables)
        return self.tables

    def get_all_tuples(self)-> Set:
        all_tuples = set()
        for t in self.tuples:
            all_tuples.update(t)
        return all_tuples

    def upsert_plausible_node(self, branch_type):
        '''
            update plausible node accordingly.
            For filter and join, we should add two plausible nodes, one child, one sibling.
            1. If current node has no sibling or sibling is a PlausibleChild, add plausible node to current node's parent node
            2. if bit in current node's children, update branch type accordingly, else, add a PlausibleChild to current node
        '''
        branch_type = BranchType.from_value(branch_type)
        bit =  "1" if self.taken else "0" 
        if bit not in self.children:
            plausible_child = PlausibleChild(self, branch_type, self.tree)
            self.children[bit] = plausible_child
            self.tree.leaves.pop(self.pattern(), None)
            self.tree.leaves[self.pattern()] = plausible_child
        elif isinstance(self.children[bit], Constraint):
            self.children[bit].branch_type = branch_type

        if self.constraint_type in {PathConstraintType.VALUE}:            
            sibling_bit = "0" if self.taken else "1"
            if sibling_bit not in self.children or isinstance(self.children[sibling_bit], PlausibleChild):
                plausible_child = PlausibleChild(self.parent, BranchType.PLAUSIBLE, self.tree)
                self.parent.children[sibling_bit] = plausible_child
                self.tree.leaves.pop(self.parent.pattern(), None)
                self.tree.leaves[self.parent.pattern() + sibling_bit] = plausible_child

        elif self.constraint_type in {PathConstraintType.PATH}:
            sibling_bits = {"0", "1", "3"}
            sibling_bits.remove(bit)
            for sibling_bit in sibling_bits:
                if sibling_bit not in self.children or isinstance(self.children[sibling_bit], PlausibleChild):
                    sb_branch_type = BranchType.from_value(int(sibling_bit)) ^ BranchType.PLAUSIBLE
                    # logger.info(f'sb_branch_type: {sb_branch_type}')
                    plausible_child = PlausibleChild(self.parent, sb_branch_type, self.tree)
                    self.parent.children[sibling_bit] = plausible_child
                    self.tree.leaves.pop(self.parent.pattern(), None)
                    self.tree.leaves[self.parent.pattern() + sibling_bit] = plausible_child
        
        elif self.constraint_type in {PathConstraintType.SIZE}:
            sibling_bits = {"6", "7"}
            for sibling_bit in sibling_bits:
                if sibling_bit not in self.children:
                    sb_branch_type = BranchType.from_value(int(sibling_bit))
                    plausible_child = PlausibleChild(self, sb_branch_type, self.tree)
                    self.children[sibling_bit] = plausible_child
                    self.tree.leaves.pop(self.pattern(), None)
                    self.tree.leaves[self.pattern() + sibling_bit] = plausible_child



        # if self.constraint_type in {PathConstraintType.VALUE, PathConstraintType.PATH}:
        #     bit = "1" if branch_type else "0" #self.tree.yes_bit if branch_type else self.tree.no_bit
        #     if bit not in self.children:
        #         plausible_child = PlausibleChild(self, branch_type, self.tree)
        #         self.children[bit] = plausible_child
        #         self.tree.leaves.pop(self.pattern(), None)
        #         self.tree.leaves[self.pattern()] = plausible_child
        #     elif isinstance(self.children[bit], Constraint):
        #         self.children[bit].branch_type = branch_type
            
        #     if not self.sibling() or isinstance(self.sibling(), PlausibleChild):
        #         bit = "1" if branch_type else "0"
        #         # bit = self.tree.yes_bit if self.bit() == self.tree.no_bit else self.tree.no_bit
        #         plausible_child = PlausibleChild(self.parent, BranchType.PLAUSIBLE, self.tree)
        #         self.parent.children[bit] = plausible_child
        #         self.tree.leaves.pop(self.parent.pattern(), None)
        #         self.tree.leaves[self.parent.pattern() + bit] = plausible_child
        # elif self.constraint_type == PathConstraintType.SIZE:
        #     bit = self.tree.yes_bit if self.taken else self.tree.no_bit
        #     if bit not in self.children:
        #         plausible_child = PlausibleChild(self, branch_type, self.tree)
        #         self.children[bit] = plausible_child
        #         self.tree.leaves.pop(self.parent.pattern(), None)
        #         self.tree.leaves[self.pattern()] = plausible_child

        #     if self.tree.null_bit not in self.children:
        #         plausible_child = PlausibleChild(self, BranchType.NULLABLE, self.tree)
        #         self.children[self.tree.null_bit] = plausible_child
        #         self.tree.leaves.pop(self.parent.pattern(), None)
        #         self.tree.leaves[self.pattern() + self.tree.null_bit] = plausible_child
            
        #     if self.tree.distinct_bit not in self.children:
        #         plausible_child = PlausibleChild(self, BranchType.SIZE, self.tree)
        #         self.children[self.tree.distinct_bit] = plausible_child
        #         self.tree.leaves.pop(self.parent.pattern(), None)
        #         self.tree.leaves[self.pattern() + self.tree.distinct_bit] = plausible_child


        #     null_bit = self.tree.no_bit if self.taken else self.tree.yes_bit
        #     if  self.sql_condition.key in {'count', 'sum', 'max', 'min', 'avg'} and null_bit not in self.children:
        #         plausible_child = PlausibleChild(self, BranchType.NULLABLE, self.tree)
        #         self.children[null_bit] = plausible_child
        #         self.tree.leaves.pop(self.parent.pattern(), None)
        #         self.tree.leaves[self.pattern() + null_bit] = plausible_child

    def __repr__(self):
        return str(self)
    def __str__(self):
        return f"Constraint({self.operator_key}, {self.operator_i})" # , predicates = {self.delta}
    
    def __hash__(self):
        return hash(f"Constraint({self.operator_key}, {self.operator_i}, {self.sql_condition})")

    def get_path_to_root(self) -> List[Constraint]:
        if self.path is not None:
            return self.path
        parent_path = []
        if self.parent is not None:
            parent_path = self.parent.get_path_to_root()
        self.path = parent_path + [self]
        return self.path

    def pattern(self):
        if self._pattern is not None:
            return self._pattern
        path = self.get_path_to_root()
        self._pattern = ''.join(p.bit() for p in path[1:])
        return self._pattern

    def _analyze_constraint_type(self, operator_key, condition: exp.Condition) -> PathConstraintType:
        '''
            Analyze the type of constraint based on the condition.
            Return
                SIZE, VALUE, PATH
        '''
        if isinstance(condition, (exp.Count, exp.Exists, exp.Column, exp.Case, exp.AggFunc)):
            return PathConstraintType.SIZE
        if isinstance(condition, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ, exp.Like)):
            if isinstance(condition.expression, exp.Literal):
                return PathConstraintType.VALUE
            if isinstance(condition.this, exp.Column) and isinstance(condition.expression, exp.Column):
                return PathConstraintType.PATH
        elif condition.key == 'is_null':
            return PathConstraintType.VALUE
        elif isinstance(condition, (exp.Div, exp.Mul, exp.Add, exp.Sub)):
            return PathConstraintType.SIZE
        elif isinstance(condition, exp.Unary):            
            return self._analyze_constraint_type(operator_key, condition.this)
        else:
            raise ValueError(f'cannot parse constraint type {condition}')
        return PathConstraintType.VALUE
    
    def __to_bit(self, taken: bool):
        return {
            True : '1',
            False: '0'
        }.get(taken)
        
    def add_child(self, operator_key, operator_i, sql_condition: exp.Condition, symbolic_expr, branch, tbl_exprs: List[Any], taken: bool, tuples, **kwargs):
        # assert 
        if not taken:
            sql_condition = negate_sql_condition(sql_condition)
        
        child_node = self.find_child(operator_key, operator_i, sql_condition)

        if child_node is None:
            constraint_type = self._analyze_constraint_type(operator_key, sql_condition)
            child_node = Constraint(
                tree = self.tree,
                parent = self, 
                operator_key = operator_key, 
                operator_i = operator_i,
                delta= [],
                taken = taken, 
                constraint_type= constraint_type,
                sql_condition= sql_condition,
                tbl_exprs = tbl_exprs
            )
            self.children[self.__to_bit(taken)] = child_node
            child_node.upsert_plausible_node(branch)
        
        p = symbolic_expr if isinstance(symbolic_expr, list) else [symbolic_expr]
        if child_node.constraint_type in {PathConstraintType.VALUE, PathConstraintType.PATH}:
            p = [symbolic_expr if taken else symbolic_expr.not_()]
        child_node.delta.extend(p)
        child_node.tuples.append(tuples)

        return child_node




    def find_child(self, operator_key, operator_i, sql_condition):
        for bit, child in self.children.items():
            if isinstance(child, Constraint) and \
            child.operator_key == operator_key and \
            child.operator_i == operator_i and \
            child.sql_condition == sql_condition:
                return child
            
        return None


        # bit = self.tree.yes_bit if taken else self.tree.no_bit
        # child = self.children.get(bit, None)
        # if isinstance(child, Constraint) and \
        #     child.operator_key == operator_key and \
        #     child.operator_i == operator_i:
        #     return child
        # return None

    def get_constraint_summary(self) -> Dict[str, Any]:
        """Get a summary of this constraint node for analysis."""
        return {
            'operator_key': self.operator_key,
            'operator_i': self.operator_i,
            'constraint_type': self.constraint_type,
            'pattern': self.pattern(),
            'tables': self.get_tables(),
            'sql_condition': str(self.sql_condition) if self.sql_condition else None,
            'taken': self.taken
        }
