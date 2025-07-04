from __future__ import annotations
from collections import defaultdict
from typing import List, Optional, Dict, Union, Set

from .helper import get_all_vars, clean_str
from src.symbols import Symbols, SymbolicType
from src.uexpr.constant import *
from sqlglot import exp
import logging, enum, copy
logger = logging.getLogger('src.parseval.constraitn')


# class _Constraint:
#     cnt = 0
#     def __init__(self, parent:Optional[_Constraint], identifier: str, 
#                  sql_condition: exp.Condition = None, 
#                  branch_type: BranchType = BranchType.UNKNOWN, 
#                  constraint_type: PathConstraintType = PathConstraintType.UNKNOWN, tables = None, **kwargs):

#         self.parent: Optional[_Constraint] = parent
#         self.children: Dict[str, _Constraint] = {}
#         self.identifier = clean_identifier(identifier)
#         self.sql_condition = sql_condition
#         self.branch_type = branch_type
#         self.constraint_type = constraint_type
#         self.tables = tables

#         self.unique_id = f"{self.identifier}{self.__class__.cnt}"
#         self.__class__.cnt += 1
        
#         self.processed = False
#         self._pattern = None
#         self.path = None
#         self.tree = None

#         # general graph attributes
#         self.color = 0xEEF7FF
#         self.border_color = 0xEEEEEE
#         self.shape = "box"
#         self.label = ""


#         for k,v in kwargs.items():
#             setattr(self, k, v)

# class PlausibleChild(_Constraint):
#     def __init__(self, parent, identifier, sql_condition, tables, **kwargs):
#         super().__init__(parent, identifier, sql_condition, tables, **kwargs)

#     # def __init__(self, parent, cond, tree):
#     #     self.parent = parent
#     #     self.cond = cond
#     #     self.tree = tree
#     #     self._smt_val = None

#     def __repr__(self):
#         return 'PlausibleChild[%s]' % (self.parent.pattern() + ':' + self.sql_condition)



class Constraint:
    
    cnt = 0
    def __init__(self, tree, parent:Optional[Constraint],  identifier: str, 
                 delta: List = None,
                 sql_condition: exp.Condition = None, 
                 branch_type: BranchType = BranchType.ROOT, 
                 constraint_type: PathConstraintType = PathConstraintType.UNKNOWN, tables = None, **kwargs):
        self.tree = tree
        self.parent: Optional[Constraint] = parent
        self.children: Dict[str, Constraint] = {}
        self.identifier = identifier
        self.delta:List[Symbols] = delta
        self.sql_condition = sql_condition
        self.branch_type = branch_type
        self.constraint_type = constraint_type
        self.tables = tables

        self.unique_id = f"{self.identifier}{self.__class__.cnt}"
        self.__class__.cnt += 1
        
        self.processed = False
        self._pattern = None
        self.path = None
        
        

        # general graph attributes
        self.color = 0xEEF7FF
        self.border_color = 0xEEEEEE
        self.shape = "box"
        self.label = ""


        for k,v in kwargs.items():
            setattr(self, k, v)

    
        

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

    def _create_branch_info(self, operator_key, is_positive) -> BranchType:
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

    def _create_constraint_info(self, operator_key: str, operator_i, condition: exp.Condition, smt_expr: Symbols, is_positive) -> Dict:
        ## Assign identifier
        condition_str = str(condition)
        if not smt_expr.value:
            condition_str = f'Not({str(condition)})' if condition.key != 'not' else str(condition.this)
        identifier = clean_str(f"{operator_key}{operator_i}({condition_str})")
        ## Determine path constraint type
        c_type = PathConstraintType.PATH 
        if operator_key not in {'join', 'aggregate'}:
            c_type = self._analyze_constraint_type(condition)
        ## find out all related tables and columns
        tables = defaultdict(set)
        for smt_var in get_all_vars(smt_expr.expr):
            table_name, column_name, column_index = self.tree.context.get('symbol_to_table', str(smt_var))
            tables[table_name].add(column_name)
        
        return {
            'identifier': identifier,
            'branch_type': self._create_branch_info(operator_key, is_positive),
            'constraint_type' : c_type,
            'sql_condition': condition,
            'tables': tables
        }


    def add_child(self, operator_key, operator_i, condition: exp.Condition, symbolic_expr, is_positive, **kwargs):
        constraint_info = self._create_constraint_info(operator_key, operator_i, condition, symbolic_expr, is_positive)
        identifier = constraint_info['identifier']
        bit = self.tree.yes_bit if symbolic_expr.value else self.tree.no_bit
        child_node = self.find_child(identifier)

        if child_node is None:
            child_node = Constraint(tree= self.tree, parent = self, delta= [], **constraint_info)
            self.children[bit] = child_node
            self.tree.leaves.pop(self.pattern(), None)
            self.tree.leaves[self.pattern() + bit] = child_node
        
        child_branch_type = constraint_info['branch_type']
        if child_branch_type in {BranchType.POSITIVE, BranchType.NEGATIVE}:
            '''add sibiling node to current node'''
            sibiling_constraint_info = self._create_constraint_info(operator_key, operator_i, condition, symbolic_expr.__not__(), not is_positive)
            if self.find_child(sibiling_constraint_info['identifier']) is None:
                sibiling_node = Constraint(tree = self.tree, parent = self, delta= [], **sibiling_constraint_info)
                sibiling_bit = self.tree.no_bit if symbolic_expr.value else self.tree.yes_bit
                self.children[sibiling_bit] = sibiling_node
                self.tree.leaves.pop(self.pattern(), None)
                self.tree.leaves[self.pattern() + sibiling_bit] = sibiling_node
        
        p = symbolic_expr if symbolic_expr else symbolic_expr.__not__()
        child_node.delta.append(p)
        return child_node

    def find_child(self, identifier):
        for bit, child in self.children.items():
            if child.identifier == identifier:
                return child
        return None

    def get_length(self):
        if self.parent is None:
            return 0
        return 1 + self.parent.get_length()

    # def get_asserts_and_query2(self, *predicates):
    #     asserts = []
    #     variables = set()
    #     tuples = set()

    #     def implies(b):
    #         if isinstance(b, SymbolicType):
    #             b = b.expr
    #         rv_ = get_all_vars(b)
    #         rt_ = set(self.tree.context[7][str(v)].expr for v in rv_)
    #         if variables.intersection(rv_) or tuples.intersection(rt_):
    #             variables.update(rv_)
    #             tuples.update(rt_)
    #             return True
    #         return False

    #     for pred in predicates:
    #         if isinstance(pred, SymbolicType):
    #             pred = pred.expr
    #         v_ = get_all_vars(pred)
    #         variables.update(v_)
    #         for v in v_:
    #             tuples.add(self.tree.context[7][str(v)].expr)

    #     for p in self.get_path_to_root()[1: ]:
    #         for pred in p.delta:
    #             if implies(pred.expr.expr):
    #                 asserts.append(pred.expr)
    #     return asserts

    def get_asserts_and_query(self, predicates: List[Symbols], label = 'positive', tuples: Dict= None, variables = None) -> List[Symbols]:
        '''
            traverse current node to root path, get all related predicates based on predicates
        '''
        asserts = [] + predicates
        if tuples is None:
            tuples = set()
        if variables is None:
            variables = set()
            for pred in predicates:
                if isinstance(pred, Symbols):
                    pred = pred.expr
                v_ = get_all_vars(pred)
                variables.update(v_)
                for v in v_:
                    tuples.add(self.tree.context[7][str(v)].expr)
        def implies(b):
            if isinstance(b, Symbols):
                b = b.expr
            rv_ = get_all_vars(b)
            rt_ = set(self.tree.context[7][str(v)].expr for v in rv_)
            if variables.intersection(rv_) or tuples.intersection(rt_):
                variables.update(rv_)
                tuples.update(rt_)
                return True
            return False
        
        for pred in self.delta:
            if implies(pred.expr):
                if label == 'positive' and not self.is_positive:
                    asserts.append(pred.expr.__not__())
                else:
                    asserts.append(pred.expr)

        if self.parent is not None:
            asserts = self.parent.get_asserts_and_query(asserts, label= label, tuples = tuples, variables= variables)
        return asserts


    def render_node_graphviz(self):
        """
        Render a node suitable for use in a Pydot graph using the set internal attributes.

        @rtype:  pydot.Node
        @return: Pydot object representing node
        """

        import pydot
        # + str(self.id) +
        dot_node = pydot.Node(self.unique_id)
        
        dot_node.obj_dict["attributes"]["label"] = '<<font face="lucida console">{}</font>>'.format(
            self.identifier + f":(positive = {self.branch_type})"
        )
        dot_node.obj_dict["attributes"]["label"] = dot_node.obj_dict["attributes"]["label"].replace("\\n", "<br/>")
        dot_node.obj_dict["attributes"]["shape"] = self.shape
        dot_node.obj_dict["attributes"]["color"] = "#{:06x}".format(self.color)
        dot_node.obj_dict["attributes"]["fillcolor"] = "#{:06x}".format(self.color)

        return dot_node
