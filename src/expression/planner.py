
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Set, Tuple, Optional, Iterable, Dict, Any, Sequence, TYPE_CHECKING, Union
from enum import auto, Enum
import itertools
from sqlglot import parse_one
from sqlglot.planner import Plan

from .tree import *
if TYPE_CHECKING:
    from sqlglot import exp




class Planner:
    def __init__(self, query: str, dialect = "sqlite", recursive_cte_unroll: int = 2):
        self.query = query
        self.dialect = dialect
        self.schema_manager = None  # Placeholder for schema manager

        self.recursive_cte_unroll = recursive_cte_unroll
        self.cte_defs: Dict[str, Any] = {}
    
    def build(self) -> Node:
        from sqlglot import parse_one

        root = Node(name= "root")
        expr = parse_one(self.query, read = True, dialect=self.dialect)
        self._handle_statement(expr, root)
        return root
    
    def _handle_statement(self, expression: exp.Expression, parent: Node) -> Node:
        ctes = {}
        expression = expression.unnest()
        with_ = expression.args.get("with")
        ### SELECT
        if isinstance(expression, exp.Select):
            return self._handle_select(expression, parent, ctes)
        
    def _handle_select(self, expression: exp.Select, parent: Node) -> Node:
        current_node = parent
        from_ = expression.args.get("from")
        if from_:
            current_node = self._handle_from(from_, parent)

        joins = expression.args.get("joins")
        if joins:
            self._handle_joins(from_, joins, parent)
        
        where = expression.args.get("where")
        if where:
            current_node = self._handle_where(where, current_node)

    def _handle_from(self, from_: exp.From, parent: Node):

        item = from_.args.get("this")
        if isinstance(item, exp.Table):
            node = TableRefNode(name=item.alias_or_name, table= item, alias= item.alias_or_name, schema_manager= self.schema_manager)
            parent.add_child(node)
            return node

    def _handle_joins(self, from_: exp.From, joins: Iterable[exp.Join], parent: Node) -> Node:
        for join in joins:
            join_node = Node(name="join", parent=parent)
            left = join.args.get("left")
            right = join.args.get("right")
            if left:
                self._handle_expression(left, join_node)
            if right:
                self._handle_expression(right, join_node)

    def _handle_where(self, where: exp.Where, parent: Node) -> Node:
        this = where.args.get("this")
        current_node = parent

        # if isinstance(this, exp.Or):
        #     ornode = OrNode(node.sql())
        #     parent.add_child(ornode)
        #     for part in this.flatten():
        #         self._handle_boolean_expr(part, ornode, kind=kind)
        #     return
        if isinstance(this, exp.And):
            for part in this.flatten():
                current_node = self._handle_where(part, current_node)
            return current_node
        if isinstance(this, exp.Predicate) or isinstance(node, exp.Comparison) or isinstance(node, exp.Column) or isinstance(node, exp.Literal):
            parent.add_child(ConstraintNode(node.sql(), kind=kind))
            return
        # default
        parent.add_child(ConstraintNode(node.sql(), kind=kind))
        
        ...
    def _handle_predicate(self, predicate: exp.Predicate, parent: Node) -> Node:
        # Handle different types of predicates
        if isinstance(predicate, exp.Or):
            # Handle comparison predicates
            pass
        elif isinstance(predicate, exp.And):
            # Handle function predicates
            pass
        else:
            # Default handling for other predicates

            node = PredicateNode(name=str(predicate), sql_expr=predicate, schema_manager= self.schema_manager)
            parent.add_child(node)
            return node

        return parent