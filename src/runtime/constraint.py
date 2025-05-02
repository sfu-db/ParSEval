from __future__ import annotations
from collections import defaultdict
from typing import List, Optional, Dict, Union, Set
from .constant import *
from sqlglot import exp

from src.expression.symbol import Expr
import logging
logger = logging.getLogger('src.parseval.constraitn')


class PlausibleChild:
    def __init__(self, parent, branch_type: BranchType, cond, tree):
        self.parent = parent
        self.branch_type = branch_type
        self.cond = cond
        self.tree = tree

    def __repr__(self):
        return 'PlausibleChild[%s]' % (self.parent.pattern() + ':' + self.cond)

    @property
    def identifier(self):
        return f"{self.parent.identifier + self.cond}"

class Constraint:
    cnt = 0
    def __init__(self, 
                 tree, 
                 parent: Optional[Constraint], 
                 operator_key: OperatorKey,
                 operator_i: OperatorId,
                 delta: List = None,
                 sql_condition: exp.Condition = None, 
                 branch_type: BranchType = BranchType.ROOT, 
                 constraint_type: PathConstraintType = PathConstraintType.UNKNOWN, metadata = None):
        self.tree = tree
        self.parent: Optional[Constraint] = parent
        self.children: Dict[str, Constraint] = {}
        self.operator_key = operator_key
        self.operator_i = operator_i
        self.delta:List[Expr] = delta
        self.sql_condition = sql_condition
        self.branch_type = branch_type
        self.constraint_type = constraint_type
        self.metadata = metadata

        self.unique_id = f"{self.identifier}{self.__class__.cnt}"
        self.__class__.cnt += 1
        
        self.processed = False
        self._pattern = None
        self.path = None

    @property
    def identifier(self) -> str:
        identifier = self.operator_key
        if self.operator_i:
            identifier += self.operator_i
        if self.sql_condition:
            identifier += f"({str(self.sql_condition)})"
        return identifier
       
        # for k,v in kwargs.items():
        #     setattr(self, k, v)

    def no(self):
        return self.children.get(self.tree.no_bit, None)
    def yes(self):
        return self.children.get(self.tree.yes_bit, None)

    def has_sibiling(self):
        return self.no() is not None and self.yes() is not None
    
    def sibiling(self) -> Constraint:
        if self.bit() == self.tree.no_bit :
            return self.parent.yes()
        return self.parent.no()
    
    def bit(self):
        if self.parent and self.parent.yes() == self:
            return self.tree.yes_bit
        return self.tree.no_bit
    
    def __repr__(self):
        return str(self)
    def __str__(self):
        return f"Constraint({self.identifier}, predicates = {self.delta})"
    
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

    def _analyze_branch_info(self, operator_key, is_positive) -> BranchType:
        '''
            Determine branch type of child node based on the operator key and current branch type.
            if operator is project or aggregate, return STRAIGHT
            if operator is filter, return CURRENT_BRANCH_TYPE & is_positive
        '''
        if operator_key in {'project', 'aggregate'}:
            return BranchType.STRAIGHT
        return self.branch_type & is_positive

    def _analyze_constraint_type(self, condition: exp.Condition) -> PathConstraintType:
        '''
            Analyze the type of constraint based on the condition.
            Return
                SIZE, VALUE, PATH
        '''
        if isinstance(condition, (exp.Count, exp.Exists)):
            return PathConstraintType.SIZE
        if isinstance(condition, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
            if isinstance(condition.expression, exp.Literal):
                return PathConstraintType.VALUE
            if isinstance(condition.this, exp.Column) and isinstance(condition.expression, exp.Column):
                return PathConstraintType.PATH
        elif isinstance(condition, exp.Unary):
            return self._analyze_constraint_type(condition.this)
        return PathConstraintType.VALUE
    
    def _analyze_constraint_info(self, operator_key: str, sql_condition: exp.Condition, smt_expr) -> Dict:
        if not smt_expr:
            sql_condition = exp.Not(this = sql_condition) if sql_condition.key != 'not' else sql_condition.this
        ## Determine path constraint type
        c_type = PathConstraintType.PATH  # by default
        if operator_key not in {'join', 'aggregate'}:
            c_type = self._analyze_constraint_type(sql_condition)
        ## find out all related tables and columns
        # tables = defaultdict(set)
        # for smt_var in get_all_symbols(smt_expr):
        #     table_name, column_name, column_index = self.tree.context.get('symbol_to_table', str(smt_var))
        #     tables[table_name].add(column_name)
        # identifier = clean_str(f"{operator_key}{operator_i}({str(sql_condition)})")
        return {
            # 'identifier': identifier,
            'constraint_type' : c_type,
            'sql_condition': sql_condition,
            # 'tables': tables
        }
    
    def add_child(self, operator_key, operator_i, sql_condition: exp.Condition, symbolic_expr, branch, metadata,**kwargs):        
        branch_type = self._analyze_branch_info(operator_key, branch)
        constraint_info = self._analyze_constraint_info(operator_key, sql_condition, symbolic_expr)        
        bit = self.tree.yes_bit if symbolic_expr else self.tree.no_bit
        identifier = f"{operator_key}{operator_i}({str(constraint_info['sql_condition'])})"
        child_node = self.find_child(identifier)

        if child_node is None:
            child_node = Constraint(tree= self.tree, 
                                    parent = self, 
                                    operator_key = operator_key, 
                                    operator_i = operator_i,
                                    delta= [], branch_type = branch_type, **constraint_info, metadata = metadata)
            self.children[bit] = child_node
            self.tree.leaves.pop(self.pattern(), None)
            self.tree.leaves[self.pattern() + bit] = child_node

        if branch_type in {BranchType.POSITIVE, BranchType.NEGATIVE}:
            '''add sibiling node to current node'''
            sibiling_constraint_info = self._analyze_constraint_info(operator_key, sql_condition, symbolic_expr.not_())
            sibiling_constraint_identifier = f"{operator_key}{operator_i}({str(sibiling_constraint_info['sql_condition'])})"
            sibiling_branch_type = self._analyze_branch_info(operator_key, not branch)
            if self.find_child(sibiling_constraint_identifier) is None:
                sibiling_node = Constraint(tree = self.tree,
                                           parent = self, 
                                           operator_key= operator_key,
                                           operator_i= operator_i, delta= [],
                                           branch_type = sibiling_branch_type,  **sibiling_constraint_info, metadata = metadata)
                sibiling_bit = self.tree.no_bit if symbolic_expr else self.tree.yes_bit
                self.children[sibiling_bit] = sibiling_node
                self.tree.leaves.pop(self.pattern(), None)
                self.tree.leaves[self.pattern() + sibiling_bit] = sibiling_node
                # sibiling_node.add_plausiblechild()
        
        p = symbolic_expr if symbolic_expr else symbolic_expr.not_()
        child_node.branch_type = child_node.branch_type  & branch_type
        child_node.delta.append(p)
        return child_node
    
    def add_plausiblechild(self):
        if self.branch_type not in {BranchType.POSITIVE, BranchType.NEGATIVE}:
            return
        for bit in [self.tree.yes_bit, self.tree.no_bit]:
            self.children[bit] = PlausibleChild(self, bit, self.tree)


    
    def find_child(self, identifier):
        for bit, child in self.children.items():
            if isinstance(child, Constraint) and child.identifier == identifier:
                return child
        return None
