
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Optional, Iterable, Dict, Any, Sequence, TYPE_CHECKING, Union
from enum import auto, Enum
import itertools
from sqlglot import parse_one
from sqlglot.planner import Plan

from .helper import negate_sql_condition
from .tree import *
if TYPE_CHECKING:
    from sqlglot import exp


class BranchType(Enum):
    """Represents different types of branches in the AST.
    Attributes:
        NEGATIVE: Branch that evaluates to false
        POSITIVE: Branch that evaluates to true
        STRAIGHT: Branch that doesn't change evaluation
        ROOT: Root node of the AST
        PLAUSIBLE: Branch that is plausible
    """

    NEGATIVE = auto()
    POSITIVE =  auto()   
    PLAUSIBLE =  auto()
    RPLAUSIBLE =  auto()
    ROOT =  auto()
    UNREACHABLE =  auto()
    NULLABLE =  auto()
    SIZE =  auto()



class Planner:
    def __init__(self, query: str, dialect = "sqlite"):
        self.query = query
        self.dialect = dialect
        self.schema_manager = None  # Placeholder for schema manager
        
    def build(self) -> Node:
        from sqlglot import parse_one
        root = Node(label= "positive", name= "root")
        expr = parse_one(self.query, read = True, dialect=self.dialect)
        self._handle_statement(expr, root)
        return root
    
    def _handle_statement(self, expression: exp.Expression, parent: Node) -> Node:
        ctes = ctes or {}
        expression = expression.unnest()
        with_ = expression.args.get("with")
        ### SELECT
        if isinstance(expression, exp.Select):
            return self._handle_select(expression, parent, ctes)

    def _handle_select(self, expression: exp.Select, parent: Node) -> Node:
        projs: List = []
        from_ = expression.args.get("from")
        joins = expression.args.get("joins")

        ## From and Join
        if joins:
            
            ...
        
        node = parent
        # WHERE
        where = expression.args.get("where")
        if where:
            self._handle_where(where.this, node)

    
    def _handle_where(self, expression: exp.Expression, parent: Node, kind: str = "filter") -> Node:
        if isinstance(expression, exp.And):
            # AND: keep as sequence of ConstraintNodes
            for part in expression.flatten():
                parent = self._handle_where(part, parent, kind=kind)
            return parent
        if isinstance(expression, exp.Or):
            ...
        
        if isinstance(expression, exp.Not):
            ...

        predicate = PredicateNode(expression, label=BranchType.POSITIVE, schema_manager= self.schema_manager)
        parent.add_dependency()
        
        # parent.add_dependency(PredicateNode(expression, ))

        # node may be AND / OR / comparison / CASE
        # if isinstance(node, exp.Or):
        #     ornode = OrNode(description=node.sql())
        #     parent.add_child(ornode)
        #     for part in node.flatten():
        #         self._handle_where(part, ornode, kind=kind)
        #     return
        # if isinstance(node, exp.And):
        #     # AND: keep as sequence of ConstraintNodes
        #     for part in node.flatten():
        #         self._handle_where(part, parent, kind=kind)
        #     return
        # if isinstance(node, exp.Case):
        #     # CASE in WHERE/HAVING -> expand branches
        #     case_alias = f"case_expr_{len(parent.children)}"
        #     cnode = CaseNode(case_alias)
        #     parent.add_child(cnode)
        #     # sqlglot represents WHEN as list in args['ifs'] with corresponding thens in args['thens']
        #     if node.args.get('ifs') and node.args.get('thens'):
        #         for w, t in zip(node.args['ifs'], node.args['thens']):
        #             cond = w.sql()
        #             cnode.add_when_then(cond, t.sql())
        #     if node.args.get('default'):
        #         cnode.set_else(node.args['default'].sql())
        #     return
        # # base comparison or existence
        # parent.add_child(ConstraintNode(node.sql(), kind=kind))

        ...
    
    def _handle_boolean_expr(self, node: exp.Expression, parent: Node, kind: str = "filter"):
        # Generic boolean expr handler (used for ON and WHERE)
        if isinstance(node, exp.Or):
            ornode = OrNode(node.sql())
            parent.add_child(ornode)
            for part in node.flatten():
                self._handle_boolean_expr(part, ornode, kind=kind)
            return
        if isinstance(node, exp.And):
            for part in node.flatten():
                self._handle_boolean_expr(part, parent, kind=kind)
            return
        if isinstance(node, exp.Predicate) or isinstance(node, exp.Comparison) or isinstance(node, exp.Column) or isinstance(node, exp.Literal):
            parent.add_child(ConstraintNode(node.sql(), kind=kind))
            return
        # default
        parent.add_child(ConstraintNode(node.sql(), kind=kind))


    def _handle_join(self, expression: exp.Select, parent: Node):
        # jkind = (item.args.get("kind") or "INNER").upper()
        # join_node = JoinNode(jkind)
        # parent.add_child(join_node)
        ...

    def _handle_from(self, expression: exp.Select, parent: Node):

        ## Table
        # Table ref
        if isinstance(expression, exp.Table):
            parent.add_child(TableRefNode(expression, label= BranchType.POSITIVE, alias = expression.alias_or_name))
            return
        ## Union
        if isinstance(expression, exp.Union):
            ...

        ## Subquery
        raise NotImplementedError
        