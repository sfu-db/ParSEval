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
    def __init__(self, instance: Instance, expr: exp.Expression, tracer: UExprToConstraint, table_alias: Optional[Dict] = None,  verbose: bool = True):
        self.instance = instance
        self.expr = expr
        self.tracer = tracer
        self.verbose = verbose
        self.table_alias = table_alias or _table_alias(instance= self.instance, expr= expr)
        self.scope_graph = build_graph_from_scopes(expr)
        
        
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
                predicates.extend(self.__preprocess_predicate(node.condition))
            elif isinstance(node, Join):
                joins.append(node)
                predicates.extend(self.__preprocess_predicate(node.condition))
            elif isinstance(node, Aggregate):
                for g in list(node.group.values()):
                    group_by.append(g)
                    
                having_condition = None
                aggregations, aggregation_alias = [], {}
                
                for agg_func in node.aggregations:
                    if node.condition and agg_func.alias_or_name == node.condition.alias_or_name:
                        having_condition = agg_func
                    else:
                        aggregation_alias[agg_func.alias_or_name] = agg_func.this
                        aggregations.append(agg_func)

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
        def unwrap(e):
            if e.same_parent:
                return e
            if e.parent and isinstance(e.parent, (exp.Paren, exp.Subquery)):
                return unwrap(e.parent)
            return e.parent
        
        predicates = []
        from sqlglot.optimizer.eliminate_joins import _has_single_output_row
        for p in list(condition.find_all(exp.Predicate)):
            sub_queries = list(p.find_all(exp.Subquery))
            if sub_queries:
                for sub in sub_queries:
                    parent = unwrap(sub).copy()
                    if sub.is_star:
                        ...
                    else:
                        out = sub.this.expressions[0].unalias()
                        if isinstance(out, exp.Column):
                            parent.find(exp.Subquery).replace(out)
                if parent:
                    predicates.append(parent)
            else:
                predicates.append(p)
        return predicates
    
    def encode(self):
        self.tracer.reset()
        self.current_scope = 0
        infos = self.__preprocess(self.expr)
        ctx = self.init_context(self.table_alias)
        self._scan(infos["columns"], ctx)
        self._join(infos['joins'], ctx)
        self._filters(infos['predicates'], ctx)
        self._group_by(infos['group_by'], ctx)
            
    
    def init_context(self,  table_alias):
        
        def product(left, right):
            rows = []
            for lrow in left:
                for rrow in right:
                    rows.append(lrow + rrow)
            return rows
        
        
        tables = {}
        start = 0
        end = 0
        body = None
        global_columns = []
        
        for alias, table in table_alias.items():
            columns = self.instance.column_names(table, dialect= self.dialect)
            global_columns.extend(columns)
            rows = self.instance.get_rows(table)
            start = end
            end += len(columns)
            scm = DerivedSchema(columns= columns, column_range= range(start, end), rows= rows)
            tables[alias] = scm 
            body = rows if body is None else product(body, rows)
        ctx = Context(tables = tables)
        return ctx
    

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

    def _scan(self, column_scans: List[exp.Column], ctx: Context):
        table_columns = {}
        for column in column_scans:
            tbl_alias = column.table
            table_columns.setdefault(tbl_alias, []).append(column)
            self.tracer.which_path(0, "scan", column.name, sql_conditions=[column], takens= [PBit.TRUE], smt_exprs= [], rowids= [], branch= True)
        
        for tbl_alias, columns in table_columns.items():
            for row in ctx.table_iter(tbl_alias):
                for column in columns:
                    smt_exprs = [row[column.name]]
                    self.tracer.which_path(0, "scan", column.name, sql_conditions=[column], takens= [PBit.TRUE], smt_exprs= smt_exprs, rowids= row.rowid(), branch= True)
    
    def _join(self, joins: List[Join], ctx: Context)-> Context:
        for node in joins:
            source = node.source_name
            # print(f'processing join with source {repr(node)}')
            for name, join in node.joins.items():
                kind = join['side']
                if kind == "inner":
                    self._inner_join(source, name, join, ctx)
                elif kind== "left":
                    self._left_join(join)
                elif kind == "right":
                    self._right_join(join)
                elif kind == "natural":
                    self._natural_join(join, ctx)
                else:
                    return self._inner_join(source, name, join, ctx)
        
    def _inner_join(self, source, join_name, join, ctx: Context) -> Context:
        # for row in ctx.iters():
        #     smt_exprs, sql_conditions = [], []
        #     for source_key, join_key in zip(join['source_key'], join['join_key']):
        #         l = row.get(source, source_key.name)
        #         r = row.get(join_name, join_key.name)
        #         smt_exprs.append(l.eq(r))
        #         # smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
        #         sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
        #     smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
        #     branch = smt_expr.concrete is True
        #     if branch:
        #         left_flag = True
        #         rowids = row.rowid()
        #         # combined_row.rowid()
        #         takens = [2] * len(smt_exprs) #[2 if b.concrete is True else 3 for b in smt_exprs]
        #         self.tracer.which_path(scope_id=self.current_scope, step_type= "Join", step_name= join_name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= rowids)
        #     else:
        #         ctx.set_mask(row.rowid())
        
        source_table = self.table_alias.get(source, source)
        join_table = self.table_alias.get(join_name, join_name)
        left_rows = self.instance.get_rows(source_table)
        right_rows = self.instance.get_rows(join_table)
        for row in left_rows:
            left_flag = False
            for rrow in right_rows:
                combined_row = row + rrow
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(join['source_key'], join['join_key']):
                    smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
                branch = smt_expr.concrete is True
                
                if branch:
                    left_flag = True
                    rowids = combined_row.rowid
                    takens = [2] * len(smt_exprs) #[2 if b.concrete is True else 3 for b in smt_exprs]
                    self.tracer.which_path(scope_id=self.current_scope, step_type= "Join", step_name= join_name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= rowids)
                else:
                    ctx.set_mask(combined_row.rowid)
                    
            if not left_flag:
                self.tracer.which_path(scope_id=self.current_scope, step_type= "Join", step_name= join_name, sql_conditions= sql_conditions, smt_exprs= [], takens= [3] * len(sql_conditions), branch= False, rowids= row.rowid)
        
        
    def _left_join(self, source, join_name, join, ctx) -> Context:
        source_table = self.table_alias.get(source, source)
        join_table = self.table_alias.get(join_name, join_name)
        left_rows = self.instance.get_rows(source_table)
        right_rows = self.instance.get_rows(join_table)
        
        for row in left_rows:
            left_flag = False
            for rrow in right_rows:
                combined_row = row + rrow
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(join['source_key'], join['join_key']):
                    smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
                branch = smt_expr.concrete is True
                if branch:
                    left_flag = True
                    rowids = combined_row.rowid()
                    takens = [2] * len(smt_exprs) #[2 if b.concrete is True else 3 for b in smt_exprs]
                    self.tracer.which_path(scope_id=self.current_scope, step_type= "Join", step_name= join_name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= rowids)
            if not left_flag:
                self.tracer.which_path(scope_id=self.current_scope, step_type= "Join", step_name= join_name, sql_conditions= sql_conditions, smt_exprs= [], takens= [3] * len(sql_conditions), branch= True, rowids= row.rowid())
                
    
    def _right_join(self, source, join_name, join, ctx) -> Context:
        source_table = self.table_alias.get(source, source)
        join_table = self.table_alias.get(join_name, join_name)
        left_rows = self.instance.get_rows(source_table)
        right_rows = self.instance.get_rows(join_table)
        
        for row in right_rows:
            left_flag = False
            for rrow in left_rows:
                combined_row = row + rrow
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(join['source_key'], join['join_key']):
                    smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
                branch = smt_expr.concrete is True
                if branch:
                    left_flag = True
                    rowids = combined_row.rowid()
                    takens = [2] * len(smt_exprs) #[2 if b.concrete is True else 3 for b in smt_exprs]
                    self.tracer.which_path(scope_id=self.current_scope, step_type= "Join", step_name= join_name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= rowids)
            if not left_flag:
                self.tracer.which_path(scope_id=self.current_scope, step_type= "Join", step_name= join_name, sql_conditions= sql_conditions, smt_exprs= [], takens= [3] * len(sql_conditions), branch= True, rowids= row.rowid())
                
    
        
    def _natural_join(self, source, join_name, join, ctx) -> Context:
        source_keys = []
        join_keys = []
        source_table = self.table_alias.get(source, source)
        join_table = self.table_alias.get(join_name, join_name)
        left_rows = self.instance.get_rows(source_table)
        right_rows = self.instance.get_rows(join_table)
        join_table_column_names = self.instance.column_names(join_table)
        for column in self.instance.column_names(source_table):
            if column in join_table_column_names:
                source_keys.append(exp.Column(this= exp.to_identifier(column), table = source))
                join_keys.append(exp.Column(this= exp.to_identifier(column), table= join_name))
                                   
        for row in left_rows:
            left_flag = False
            for rrow in right_rows:
                combined_row = row + rrow
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(join['source_key'], join['join_key']):
                    smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
                branch = smt_expr.concrete is True
                
                if branch:
                    left_flag = True
                    rowids = combined_row.rowid()
                    takens = [2] * len(smt_exprs) #[2 if b.concrete is True else 3 for b in smt_exprs]
                    self.tracer.which_path(scope_id=self.current_scope, step_type= "Join", step_name= join_name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= rowids)
                else:
                    ctx.set_mask(combined_row.rowid())
                    
            if not left_flag:
                self.tracer.which_path(scope_id=self.current_scope, step_type= "Join", step_name= join_name, sql_conditions= sql_conditions, smt_exprs= [], takens= [3] * len(sql_conditions), branch= False, rowids= row.rowid())
    
    def _filters(self, predicates: List[exp.Predicate], ctx: Context):
        for predicate in predicates:
            if not predicate:
                continue
            for row in ctx.iters():
                smt_exprs = []
                sql_conditions = []
                smt_conditions = []
                local_ctx = self.encode_condition(predicate, ctx= {"scope": row})
                smt_expr = local_ctx[predicate]
                sql_conditions.extend(local_ctx.get('sql_conditions', []))
                smt_conditions.extend(local_ctx.get('smt_conditions', []))
                smt_exprs.append(smt_expr)
                
                branch = any(smt_expr.concrete is True  for smt_expr in smt_exprs)
                takens = [
                        b.concrete is True for b in smt_conditions
                    ]
                rowids = row.rowid()
                self.tracer.which_path(scope_id=self.current_scope, step_type= "filter", step_name= "filter", sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= branch, rowids= rowids)
                if not branch:
                    ctx.set_mask(row.rowid())

    def _group_by(self, groupby: List, ctx: Context):        
        if not groupby:
            return        
        sql_conditions, takens = [], []
        for group in groupby:
            sql_conditions.append(group)
            takens.append(PBit.GROUP_SIZE)
        
        groups = {}
        for row in ctx.iters():
            group_key = ()
            alias = []
            for gid, expression in enumerate(groupby):
                group_key += (row.get(expression.table, expression.alias_or_name), )
                alias.append(gid)
            concrete_group_key = tuple(v.concrete for v in group_key)
            if concrete_group_key not in groups:
                groups[concrete_group_key] = {"group_key": group_key, "rows": [], "alias": alias}
            groups[concrete_group_key]["rows"].append(row)
            
        for _, group_info in groups.items():
            group_key = group_info["group_key"]
            group_rows = group_info["rows"]
            rowids = sum((row.rowid() for row in group_rows), ())
            g = AggGroup(this = rowids, group_key = group_key, group_values = group_rows)
            smt_conditions = [g] * len(sql_conditions)
            self.tracer.which_path(scope_id=self.current_scope, step_type= "Groupby", step_name = "Groupby", sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= True, rowids= rowids)
    
    def _order_by(self):
        ...
        
    def encode_condition(self, condition: exp.Expression, ctx: Optional[Dict] = None, **kwargs) -> Dict:
        
        ctx = ctx if ctx is not None else {}
        ctx.update(**kwargs)
        if condition in ctx:
            return ctx
        
        result = condition.transform(self.transform, copy = True, ctx = ctx)
        mappings = ctx.pop("mappings", {})
        
        for smt_expr in ctx.get("smt_conditions", []):
            sql_cond = smt_expr.transform(lambda node: mappings[node] if node in mappings else node, copy = True)
            ctx.setdefault('sql_conditions', []).append(sql_cond)
        if not ctx.get('sql_conditions'):
            for smt_cond, sql_cond in mappings.items():
                ctx.setdefault('sql_conditions', []).append(sql_cond)
                ctx.setdefault("smt_conditions", []).append(smt_cond)
            
        ctx[condition] = result
        return ctx
    
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
            smt_expr = row.get(table, column_name)
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
        elif isinstance(condition, exp.Subquery):
            ...
        return condition
        
            
