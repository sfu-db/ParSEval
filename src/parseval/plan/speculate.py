from __future__ import annotations
from typing import List, Set, Tuple, Dict, Optional, TYPE_CHECKING, Any
from collections import defaultdict, deque
from functools import reduce, total_ordering
from dataclasses import dataclass, field
from sqlglot.optimizer.scope import Scope, traverse_scope
from sqlglot.optimizer.eliminate_joins import join_condition
from sqlglot.planner import Plan, Scan, Aggregate, Join, Sort, SetOperation, Step
from sqlglot import exp
from parseval.plan.rex import *
from parseval.plan.context import Context, DerivedSchema
from parseval.constants import PBit
import logging, math
from dateutil import parser as date_parser
from parseval.solver.smt import OperationRegistry

from parseval.plan.planner import build_graph_from_scopes
from parseval.states import (
    non_fatal
)

logger = logging.getLogger("parseval.coverage")

if TYPE_CHECKING:
    from parseval.instance import Instance
    from parseval.uexpr.uexprs import UExprToConstraint
    from parseval.configuration import Config
    
def _table_alias(instance: Instance, expr: exp.Expression) -> Dict[str, str]:
    alias = {}
    for table in expr.find_all(exp.Table):
        alias[table.alias_or_name] = instance._normalize_name(table.name)
    return alias

class Speculative:
    def __init__(self, instance: Instance, expr: exp.Expression, config: Config, tracer: UExprToConstraint, verbose: bool = True):
        self.instance = instance
        self.expr = expr
        self.tracer = tracer
        self.config = config
        self.table_alias = _table_alias(instance, expr)
        
        self.scope_graph = build_graph_from_scopes(expr)
        
        self.projections = []
        self.predicates = []
        self.joins = []
        self.group_by = []
        self.aggregations = []
        self.order_by = []
        self.having = []
        
    @property
    def dialect(self):
        return self.instance.dialect
    
    def __preprocess(self, expr: exp.Expression):
        offset = expr.find(exp.Offset)
        offset = int(offset.expression.this) if offset else 0
        columns = []
        for column in expr.find_all(exp.Column):
            if column not in columns:
                columns.append(column)
        plan = Plan(expr)
        root = plan.root
        nodes = {root}
        projections = plan.root.projections
        predicates = []
        sorts = []
        joins = []
        group_by = []
        aggregations = []
        having = []
        order_by = []
        
        while nodes:
            node = nodes.pop()
            if isinstance(node, Scan):
                node.source.name
                predicates.extend(self.__preprocess_predicate(node.condition))
            elif isinstance(node, Join):
                joins.append(node)
                predicates.extend(self.__preprocess_predicate(node.condition))
            elif isinstance(node, Aggregate):
                ...
                
            elif isinstance(node, Sort):
                sorts.append(node)
            elif isinstance(node, SetOperation):
                ...
            for dep in node.dependencies:
                nodes.add(dep)
            
        return {
            "projection": projections,
            "predicates": predicates,
            "columns": columns,
            "joins": joins,
            "group_by": group_by,
            "having": having,
            "aggregations": aggregations,
            "order_by": order_by,
            "limit": plan.root.limit,
            "offset": offset,
        }
    def __preprocess_predicate(self, condition: exp.Expression):
        if not condition:
            return []
        predicates = [] + list(condition.find_all(exp.Predicate))
        
        return predicates
    
    def encode(self):
        for node_id in self.scope_graph.get_dependency_order():
            node = self.scope_graph.get_node(node_id)
            scope = node.scope
            self.current_scope = scope
            infos = self.__preprocess(scope.expression)
            
            self._scan(infos["columns"])
            
            
    
    

    def randomdb(self, expr: exp.Expression, min_rows: int = 10):
        predicates = expr.find_all(exp.Predicate)
        if predicates:
            return
        limit = expr.find(exp.Limit)
        offset = expr.find(exp.Offset)
        
        if limit:
            limit = int(limit.expression.this)
        else:
            limit = 0
        
        if offset:
            offset = int(offset.expression.this)
        else:
            offset = 0
        table_alias = _table_alias(self.instance, expr)
        concretes = {table: [] for table in table_alias.values()}
        for _ in range(max(limit + offset, min_rows)):
            self.instance.create_rows(concretes)

    def _scan(self, column_scans: List[exp.Column]):
        table_columns = {}
        
        print(f"Scanning columns: {[f'{col.table}.{col.name}' for col in column_scans]}")
        
        for column in column_scans:
            tbl_alias = column.table
            table_columns.setdefault(tbl_alias, []).append(column)
        
        for tbl_alias, columns in table_columns.items():
            table_name = self.table_alias.get(tbl_alias, tbl_alias)
            rows = self.instance.get_rows(table_name)
            if not rows:
                print(f"No rows found for table {table_name}, marking path as taken with unknown conditions.")
                print(f"Columns involved: {[f'{col.table}.{col.name}' for col in columns]}")
                for column in columns:
                    self.tracer.which_path(0, "scan", column.name, sql_conditions=[column], takens= [PBit.TRUE], smt_exprs= [], rowids= [], branch= False)
                return
            for row in rows:
                smt_exprs = [row[column.name] for column in columns]
                self.tracer.which_path(0, "scan", column.name, sql_conditions=columns, takens= [PBit.TRUE] * len(columns), smt_exprs= smt_exprs, rowids= row.rowid, branch= True)
    
    def _join(self):
        for join in self.infos["joins"]:
            kind = join.args.get("kind").lower()
            if kind == "inner":
                self._inner_join(join)
            elif kind== "left":
                self._left_join(join)
            elif kind == "right":
                self._right_join(join)
            elif kind == "natural":
                self._natural_join(join)
    
    def _inner_join(self, join: Join):
        logger.info(f'start to processing inner join, {join.sql()}')
        source = join.source_name
        source_table = self.table_alias.get(source, source)
        for name, join in join.joins.items():
            join_table = self.table_alias.get(name, name)
            left_rows = self.instance.get_rows(source_table)
            right_rows = self.instance.get_rows(join_table)
            for row in left_rows:
                for rrow in right_rows:
                    combined_row = row.row + rrow.row
                    smt_exprs, sql_conditions = [], []
                    for source_key, join_key in zip(join['source_key'], join['join_key']):
                        smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                        sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                    
                    smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
                    branch = smt_expr.concrete is True
                    rowids = combined_row.rowid
                    takens = [2] * len(smt_exprs) #[2 if b.concrete is True else 3 for b in smt_exprs]
                    self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= join.type_name, step_name= join.name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= rowids)
                    
            
    def _left_join(self, join: exp.Join):
        ...
    
    def _right_join(self, join: exp.Join):
        ...
        
    def _natural_join(self, join: exp.Join):
        ...
    
    def _filters(self):
        from parseval.faker.domain import UnionFind
        self.uf = UnionFind()
        grouped_predicates = defaultdict(list)
        for predicate in self.infos["predicates"]:
            tbl_alias = set()
            ref = None
            for column in predicate.find_all(exp.Column):
                grouped_predicates[column.table].append(predicate)
                tbl_alias.add(column.table)
                self.uf.find(column.table)
                if ref is None:
                    ref = column.table
                if ref and len(tbl_alias) > 1:
                    self.uf.union(ref, column.table)
                    
        for table_name, predicates in grouped_predicates.items():
            rows = self.instance.get_rows(self.table_alias.get(table_name, table_name))
            for row in rows:
                for predicate in predicates:
                    smt_expr = predicate.transform(self.transform, copy = True, ctx={"scope": row})
                    sql_condition = predicate
                    branch = smt_expr.concrete is True
                    rowids = row.rowid
                    takens = 1 if branch else 0
                    self.tracer.which_path(scope_id=self.current_scope.node_id, step_type= "filter", step_name= predicate.sql(), sql_conditions= [sql_condition], smt_exprs= [smt_expr], takens= [takens], branch= branch, rowids= rowids)
                    
            
    def _group_by(self):
        ...
    
    def _order_by(self):
        ...
        
    
    def _get_sql_condition(self, smt_conditions: List[exp.Expression], ctx: Dict):
        mappings = ctx.get("mappings", {})
        sql_conditions = []
        for smt_cond in smt_conditions:
            sql_conditions.append(smt_cond.transform(lambda node: mappings[node] if node in mappings else node, copy = True))
        return sql_conditions

    def transform(self, condition: exp.Expression, ctx: Dict[str, Any]):
        
        if isinstance(condition, exp.Predicate):
            ctx.setdefault("smt_conditions", []).append(condition)
            
        if isinstance(condition, exp.Column):
            column = condition
            table = column.table
            column_name = column.name
            row = ctx['scope']
            smt_expr = row[column_name]            
            ctx.setdefault("mappings", {})[smt_expr] = column
            return smt_expr
        
        elif isinstance(condition, exp.Literal):
            from .helper import to_literal
            literal = to_literal(condition, datatype= condition.type)
            return literal
            
        elif isinstance(condition, exp.Cast):
            to_type = condition.to
            inner = condition.this
            if isinstance(condition.this, exp.Column):
                ctx.setdefault("datatype", {})[condition.this] = to_type
            inner.type = to_type
            return inner
        elif isinstance(condition, exp.Case):
            for when in condition.args.get("ifs"):
                smt_expr = when.this.transform(self.transform, copy = True, ctx = ctx)
                if smt_expr.concrete:
                    return when.args.get("true").transform(self.transform, copy = True, ctx = ctx)
            return condition.args.get("default").transform(self.transform, copy = True, ctx = ctx)
        return condition
        
            
    