from __future__ import annotations
from typing import List, Optional, Dict, Union, Set
from .constant import *
from sqlglot import exp

from .helper import negate_sql_condition
from src.expression.symbol import Expr
import logging
logger = logging.getLogger('src.parseval.constraitn')

class PlausibleChild:
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
    
    def sibling(self):
        for bit, child in self.parent.children.items():
            if child != self:
                return child
        raise ValueError(f'No sibling found for {self}')
    
    def bit(self):
        if self.parent and self.parent.yes() == self:
            return self.tree.yes_bit
        return self.tree.no_bit
    
class Constraint:
    def __init__(self, 
                 tree, 
                 parent: Optional[Constraint], 
                 operator_key: OperatorKey,
                 operator_i: OperatorId,
                 delta: List = None,
                 sql_condition: exp.Condition = None, 
                 taken: Optional[bool] = None, 
                 constraint_type: PathConstraintType = PathConstraintType.UNKNOWN,
                 info = None, **kwargs):
        self.tree = tree
        self.parent: Optional[Constraint] = parent
        self.children: Dict[str, Constraint] = {}
        self.operator_key = operator_key
        self.operator_i = operator_i
        self.delta:List[Expr] = delta
        self.sql_condition = sql_condition
        self.taken = taken
        self.constraint_type = constraint_type
        self.info = info
        self.tuples: List[List] = []
        self._pattern = None
        self.path = None
        self.tables = None

    def no(self):
        return self.children.get(self.tree.no_bit, None)
    
    def yes(self):
        return self.children.get(self.tree.yes_bit, None)

    def has_sibling(self):
        return self.no() is not None and self.yes() is not None
    
    def sibling(self) -> Constraint:
        if self.bit() == self.tree.no_bit :
            return self.parent.yes()
        return self.parent.no()
    
    def bit(self):
        if self.parent and self.parent.yes() == self:
            return self.tree.yes_bit
        return self.tree.no_bit

    def get_tables(self) -> List:
        if self.tables:
            return self.tables
        tables = set()
        for inputref in self.info['table']:
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
        # logger.info(f'branch_type: {branch_type}')
        branch_type = BranchType.from_value(branch_type)
        # logger.info(f"{self.constraint_type} --> {self.operator_key}, {self.sql_condition}, {branch_type}")

        if self.constraint_type in {PathConstraintType.VALUE, PathConstraintType.PATH}:
            bit =self.tree.yes_bit if branch_type else self.tree.no_bit
            if bit not in self.children:
                plausible_child = PlausibleChild(self, branch_type, self.tree)
                self.children[bit] = plausible_child
                self.tree.leaves.pop(self.pattern(), None)
                self.tree.leaves[self.pattern()] = plausible_child
            elif isinstance(self.children[bit], Constraint):
                self.children[bit].branch_type = branch_type
        
            if not self.sibling() or isinstance(self.sibling(), PlausibleChild) :
                bit = self.tree.yes_bit if self.bit() == self.tree.no_bit else self.tree.no_bit
                plausible_child = PlausibleChild(self.parent, BranchType.PLAUSIBLE, self.tree)
                self.parent.children[bit] = plausible_child
                self.tree.leaves.pop(self.parent.pattern(), None)
                self.tree.leaves[self.parent.pattern() + bit] = plausible_child
        elif self.constraint_type == PathConstraintType.SIZE:
            bit = self.tree.yes_bit if self.taken else self.tree.no_bit
            if bit not in self.children:
                plausible_child = PlausibleChild(self, branch_type, self.tree)
                self.children[bit] = plausible_child
                self.tree.leaves.pop(self.parent.pattern(), None)
                self.tree.leaves[self.pattern()] = plausible_child

            null_bit = self.tree.no_bit if self.taken else self.tree.yes_bit
            if  self.sql_condition.key in {'count', 'sum', 'max', 'min', 'avg'} and null_bit not in self.children:
                plausible_child = PlausibleChild(self, BranchType.NULLABLE, self.tree)
                self.children[null_bit] = plausible_child
                self.tree.leaves.pop(self.parent.pattern(), None)
                self.tree.leaves[self.pattern() + null_bit] = plausible_child


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
    def _analyze_sql_condition(self, operator_key: str, sql_condition: exp.Condition, taken) -> Dict:
        if not taken:
            sql_condition = negate_sql_condition(sql_condition)
        return sql_condition
    
    def add_child(self, operator_key, operator_i, sql_condition: exp.Condition, symbolic_expr, branch, info, taken: bool, tuples, **kwargs):        
        child_node = self.find_child(operator_key, operator_i, taken)
        if child_node is None:
            constraint_type = self._analyze_constraint_type(operator_key, sql_condition)
            sql_condition = self._analyze_sql_condition(operator_key, sql_condition, taken)
            bit = self.tree.yes_bit if taken else self.tree.no_bit            
            child_node = Constraint(
                tree = self.tree,
                parent = self, 
                operator_key = operator_key, 
                operator_i = operator_i,
                delta= [],
                taken = taken, 
                constraint_type= constraint_type,
                sql_condition= sql_condition,
                info = info
            )
            self.children[bit] = child_node
            child_node.upsert_plausible_node(branch)
        p = symbolic_expr if isinstance(symbolic_expr, list) else [symbolic_expr]
        if child_node.constraint_type in {PathConstraintType.VALUE, PathConstraintType.PATH}:
            p = [symbolic_expr if taken else symbolic_expr.not_()]
        
        child_node.delta.extend(p)
        child_node.tuples.append(tuples)
        child_node.info.update(info)
        return child_node
    
    def find_child(self, operator_key, operator_i, taken):
        bit = self.tree.yes_bit if taken else self.tree.no_bit
        child = self.children.get(bit, None)
        if isinstance(child, Constraint) and \
            child.operator_key == operator_key and \
            child.operator_i == operator_i:
            return child
        return None
