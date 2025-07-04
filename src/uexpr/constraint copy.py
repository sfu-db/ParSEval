from __future__ import annotations
from collections import defaultdict
from typing import List, Optional, Dict, Union, Set
from .predicate import Predicate
from .helper import get_all_vars
from src.symbols import Symbols, SymbolicType
from src.uexpr.constant import *
from sqlglot import exp
import logging, enum, copy
logger = logging.getLogger('src.parseval.constraitn')

def clean_identifier(i):
    return i.replace('<>', '!=')

def _to_identifier(operator_key, operator_i, condition, symbolic_expr):
    condition_str = str(condition)
    if not symbolic_expr:
        condition_str = f'Not({str(condition)})' if condition.key != 'not' else str(condition.this)
    i = '%s%s(%s)' % (operator_key, operator_i, condition_str)
    return clean_identifier(i)


class Constraint(object):
    cnt = 0
    def __init__(self, parent, identifier: str, delta: List, is_positive, 
                sql_condition: exp.Condition, 
                constraint_type: PathConstraintType, tables, **kwargs):
        self.parent: Optional[Constraint] = parent
        self.children: Dict[str, Constraint] = {}
        self.identifier = clean_identifier(identifier)
        self.is_positive = is_positive
        self.sql_condition = sql_condition
        self.constraint_type: PathConstraintType = constraint_type
        self.delta:List[Predicate] = delta or []

        self.tables: Set[str] = tables
        self.records = []

        for k,v in kwargs.items():
            setattr(self, k, v)

        self.unique_id = f"{self.identifier}{self.__class__.cnt}"
        self.__class__.cnt += 1
        
        self.processed = False
        self._pattern = None
        self.path = None
        self.tree = None

        # general graph attributes
        self.color = 0xEEF7FF
        self.border_color = 0xEEEEEE
        self.shape = "box"
        self.label = ""

    def no(self):
        return self.children.get(self.tree.no_bit, None)
    def yes(self):
        return self.children.get(self.tree.yes_bit, None)
    def sibiling(self) -> Constraint:
        if self.bit() == self.tree.no_bit :
            return self.parent.yes()
        return self.parent.no()
        
    
    def get_children(self): return (self.no(), self.yes())
    
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


    def add_child(self, operator_key, operator_i, condition: exp.Condition, symbolic_expr, is_positive, **kwargs):
        is_positive = self.is_positive and is_positive if self.identifier != 'ROOT' else is_positive
        identifier = _to_identifier(operator_key, operator_i, condition, symbolic_expr.value)
        sibiling = _to_identifier(operator_key, operator_i, condition, not symbolic_expr.value)

        bit = self.tree.yes_bit if symbolic_expr.value else self.tree.no_bit
        child_node = self.find_child(identifier)
        sibiling_node = self.find_child(sibiling)

        constraint_info = self._create_constraint_info(operator_key, condition, symbolic_expr, is_positive)

        if child_node is None:
            child_node = Constraint(parent = self, identifier = identifier, delta= [], **constraint_info)
            child_node.tree = self.tree
            self.children[bit] = child_node
            self.tree.leaves.pop(self.pattern(), None)
            self.tree.leaves[self.pattern() + bit] = child_node

        # if self.find_child(sibiling) is None:
        #     sibiling_node_label =  self.is_positive and not is_positive
        #     sibiling_constraint_info = copy.deepcopy(constraint_info)
        #     sibiling_condition = condition if is_positive else (exp.Not(this=condition) if condition.key != 'not' else condition.this)
        #     sibiling_constraint_info['sql_condition'] = sibiling_condition
        #     sibiling_node = Constraint(parent = self, identifier= sibiling, delta= [], **sibiling_constraint_info)
        #     sibiling_node.tree = self.tree
        #     sibiling_bit = self.tree.no_bit if symbolic_expr.value else self.tree.yes_bit
        #     self.children[sibiling_bit] = sibiling_node
        #     self.tree.leaves.pop(self.pattern(), None)
        #     self.tree.leaves[self.pattern() + sibiling_bit] = sibiling_node

        p = Predicate(symbolic_expr if symbolic_expr else symbolic_expr.__not__(), symbolic_expr.value)
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

    def get_asserts_and_query2(self, *predicates):
        asserts = []
        variables = set()
        tuples = set()

        def implies(b):
            if isinstance(b, SymbolicType):
                b = b.expr
            rv_ = get_all_vars(b)
            rt_ = set(self.tree.context[7][str(v)].expr for v in rv_)
            if variables.intersection(rv_) or tuples.intersection(rt_):
                variables.update(rv_)
                tuples.update(rt_)
                return True
            return False

        for pred in predicates:
            if isinstance(pred, SymbolicType):
                pred = pred.expr
            v_ = get_all_vars(pred)
            variables.update(v_)
            for v in v_:
                tuples.add(self.tree.context[7][str(v)].expr)

        for p in self.get_path_to_root()[1: ]:
            for pred in p.delta:
                if implies(pred.expr.expr):
                    asserts.append(pred.expr)
        return asserts

    def get_asserts_and_query(self, predicates: List[Symbols], label = 'positive', tuples: Dict= None, variables = None) -> List[Symbols]:
        '''
        '''
        asserts = [] + predicates
        if tuples is None:
            tuples = set()
        if variables is None:
            variables = set()
            for pred in predicates:
                if isinstance(pred, SymbolicType):
                    pred = pred.expr
                v_ = get_all_vars(pred)
                variables.update(v_)
                for v in v_:
                    tuples.add(self.tree.context[7][str(v)].expr)
        def implies(b):
            if isinstance(b, SymbolicType):
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

        is_positive = self.is_positive

        for p in self.get_path_to_root()[1: -1]:
            for pred in p.delta:
                if ensure_same_row(pred.expr.expr):
                    if label == 'positive':
                        if is_positive or pred.result:
                            asserts.append(pred.expr)
                        else:
                            asserts.append(pred.expr.__not__())
                    else:
                        asserts.append(pred.expr)
        return asserts


    def _create_constraint_info(self, operator_key: str, condition: exp.Condition, smt_expr: Symbols, is_positive) -> Dict:
        if operator_key in {'join', 'aggregate'}:
            c_type = PathConstraintType.PATH
        else:
            c_type = self._analyze_constraint_type(condition)
        
        tables = set()
        for smt_var in get_all_vars(smt_expr.expr):
            table_name, column_name = self.tree.context.get('symbol_to_table', str(smt_var))
            tables.add(table_name)
        
        return {
            'is_positive': is_positive,
            'constraint_type' : c_type,
            'sql_condition': condition,
            'tables': tables
        }

    def _analyze_constraint_type(self, condition: exp.Condition) -> PathConstraintType:
        if isinstance(condition, (exp.Count, exp.Exists)):
            return PathConstraintType.SIZE
        if isinstance(condition, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.EQ, exp.NEQ)):
            if isinstance(condition.expression, exp.Literal):
                return PathConstraintType.VALUE
            # Check if comparing columns from different tables
            # condition.this.table != condition.expression.table)
            if isinstance(condition.this, exp.Column) and isinstance(condition.expression, exp.Column):
                return PathConstraintType.PATH
        return PathConstraintType.VALUE



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
            self.identifier + f":(positive = {self.is_positive})"
        )
        dot_node.obj_dict["attributes"]["label"] = dot_node.obj_dict["attributes"]["label"].replace("\\n", "<br/>")
        dot_node.obj_dict["attributes"]["shape"] = self.shape
        dot_node.obj_dict["attributes"]["color"] = "#{:06x}".format(self.color)
        dot_node.obj_dict["attributes"]["fillcolor"] = "#{:06x}".format(self.color)

        return dot_node
