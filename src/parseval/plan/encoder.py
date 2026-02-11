from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from dateutil import parser as date_parser
from src.parseval.plan.rex import *
from typing import List, Optional, Union, Dict, TYPE_CHECKING, Any, Tuple, Set
from src.parseval.constants import PBit
from src.parseval.helper import convert_to_literal
from functools import total_ordering, reduce
from collections import deque, defaultdict

from sqlglot import exp
from sqlglot.planner import Plan, Step, Scan, Join, Aggregate, Sort, SetOperation
from sqlglot.optimizer.scope import Scope, ScopeType
from src.parseval.states import (
    SchemaException,
    ParSEvalError,
    SyntaxException,
    Metadata,
    non_fatal
)
from contextlib import contextmanager
import logging, math

if TYPE_CHECKING:
    from src.parseval.instance import Instance
    from src.parseval.uexpr.uexprs import UExprToConstraint

logger = logging.getLogger("parseval.coverage")

from sqlglot.executor import execute
from sqlglot.executor.python import PythonExecutor
from .context import Context, PredicateTracker, DerivedSchema

# GLOBAL_SYMBOLIC_REGISTRY = SymbolicRegistry()

class ExpressionEncoder:
    DATETIME_FMT = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m"]
    def encode(self, expr: exp.Expression, **kwargs):
        self.ctx = {}
        for k, v in kwargs.items():
            self.ctx[k] = v
        parent_stack = []
        self._visit(expr, parent_stack)
        return self.ctx
    
    def in_predicates(self):
        return bool(self.ctx.get("_predicate_stack", []))
    
    @contextmanager
    def predicate_scope(self, is_branch: bool):
        """Context manager to track predicates.  If `is_branch` is False, tracking is a no-op."""
        if is_branch:
            self.ctx.setdefault("_predicate_stack", []).append(True)
            try:
                def track(expr, smt_expr):
                    self.ctx.setdefault("sql_conditions", []).append(expr)
                    self.ctx.setdefault("smt_conditions", []).append(smt_expr)
                    return smt_expr

                yield track
            finally:
                self.ctx["_predicate_stack"].pop()
        else:
            def track(expr, smt_expr):
                return smt_expr
            yield track
    
    def _visit(self, expr, parent_stack):
        if expr is None:
            return None
        parent_stack = parent_stack or []
        if expr in self.ctx:
            return self.ctx[expr]
        expr_key = expr.key if not isinstance(expr, FunctionCall) else str(expr.this)
        is_branch = isinstance(expr, exp.Predicate)
        if expr_key.upper() in OPS:
            with self.predicate_scope(is_branch) as track:
                try:
                    args = tuple(
                        self._visit(e, parent_stack + [e])
                        for e in expr.iter_expressions()
                        if not isinstance(e, DataType)
                    )
                    smt_expr = OPS[expr.key.upper()](*args)
                    self.ctx[expr] = track(expr, smt_expr)
                    return self.ctx[expr] 
                except KeyError as e:
                    raise e
        
        # if registry.has_handler(expr_key):
        #     handler = registry.get_handlers(expr_key)
        #     is_branch = registry.is_branch(expr_key)
        #     with self.predicate_scope(is_branch) as track:
        #         try:
        #             args = tuple(
        #                 self._visit(e, parent_stack + [e])
        #                 for e in expr.iter_expressions()
        #                 if not isinstance(e, DataType)
        #             )
        #             smt_expr = handler(*args)
        #             self.ctx[expr] = track(expr, smt_expr)
        #             return self.ctx[expr] 
        #         except KeyError as e:
        #             raise e
        else:
            handler = getattr(self, f"visit_{expr_key}", self.generic_visit)
            self.ctx[expr] = handler(expr, parent_stack)
            return self.ctx[expr] 
    
    def generic_visit(self, expr, parent_stack):
        raise NotImplementedError(f"No visit_{expr.key} method defined")
    
    def visit_columnref(self, expr: ColumnRef, parent_stack):
        smt_expr = self.ctx['row'][expr] if 'row' in self.ctx else self.ctx[expr]
        
        if not self.in_predicates():
            self.ctx.setdefault("sql_conditions", []).append(expr)
            self.ctx.setdefault("smt_conditions", []).append(smt_expr)
        return smt_expr
    
    def visit_column(self, expr: exp.Column, parent_stack):
        table = expr.table
        column_name = expr.name
        
        row = self.ctx['scope'][table].row
        smt_expr = row[column_name]
        # smt_expr = self.ctx['row'][colref] if 'row' in self.ctx else self.ctx[colref]
        if not self.in_predicates():
            self.ctx.setdefault("sql_conditions", []).append(expr)
            self.ctx.setdefault("smt_conditions", []).append(smt_expr)
        return smt_expr
    
    def visit_literal(self, expr: exp.Literal, parent_stack):
        value = expr.this
        datatype = expr.type
        try:
            if datatype.is_type(*DataType.INTEGER_TYPES):
                value = int(value)
            elif datatype.is_type(*DataType.REAL_TYPES):
                value = float(value)
            elif datatype.is_type(DataType.Type.BOOLEAN):
                value = bool(value)
            elif datatype.is_type(*DataType.TEMPORAL_TYPES):
                for fmt in self.DATETIME_FMT:
                    try:
                        value = datetime.strptime(value, fmt)
                    except ValueError:
                        continue
            elif datatype.is_type(*DataType.TEXT_TYPES):
                value = str(value)
            else:
                raise ValueError(f"Unsupported datatype: {datatype}")
        except Exception as e:
            logger.info(f"Failed to parse literal value '{value}' as type '{datatype}': {e}")
            value = None
        # logger.info(f"Visiting literal: value={value}, datatype={datatype}")
        ret = Const(this = value, _type=datatype)
        ret.type = datatype
        return ret
    
    def visit_cast(self, expr: exp.Cast, parent_stack):
        inner = self._visit(expr.this, parent_stack + [expr])
        to_type = expr.to
        if isinstance(expr.this, ColumnRef):
            self.ctx.setdefault("datatype", {})[expr.this] = to_type
        concrete = inner.concrete
        if to_type.is_type(*DataType.TEMPORAL_TYPES):
            try:
                concrete = date_parser.parse(inner.concrete)
            except Exception as e:
                concrete = None
        try:
            args = (a for a in inner.args)
        except Exception as e:
            raise e
        return inner.__class__(
            *args, dtype=expr.to, concrete=concrete, **inner.metadata
        )
    def visit_case(self, expr: exp.Case, parent_stack):
        for when in expr.args.get("ifs"):
            smt_expr = self._visit(when.this, parent_stack + [expr])
            if smt_expr:
                return self._visit(
                    when.args.get("true"), parent_stack + [expr]
                )
        return self._visit(expr.args.get("default"), parent_stack + [expr])

class PlanEncoder:
    def __init__(self, scope: Scope, instance: Instance, trace: UExprToConstraint, dialect: str , verbose: bool = True):
        self.scope = scope
        self.instance = instance
        self.trace = trace
        self.verbose = verbose
        self.dialect = dialect
        
    @contextmanager
    def tracker(self, node: Step):
        """
        Public facade that yields the context manager.
        """
        from src.parseval.uexpr import _ScopeManager
        with _ScopeManager(self.trace, node) as trace:
            yield trace
        
    def encode(self):
        expr = self.scope.expression
        plan = Plan(expr)
        
        print(plan)
        
        finished = set()
        queue = set(plan.leaves)
        contexts = {}
        self.prev_steps = deque([None])
        while queue:
            node = queue.pop()
            try:
                context = self.context(
                    {
                        name: table
                        for dep in node.dependencies
                        for name, table in contexts[dep].tables.items()
                    }
                )
                if isinstance(node, Scan):
                    contexts[node] = self.scan(node, context)
                elif isinstance(node, Aggregate):
                    contexts[node] = self.aggregate(node, context)
                elif isinstance(node, Join):
                    contexts[node] = self.join(node, context)
                elif isinstance(node, Sort):
                    contexts[node] = self.sort(node, context)
                elif isinstance(node, SetOperation):
                    contexts[node] = self.set_operation(node, context)
                else:
                    raise NotImplementedError
                finished.add(node)
                for dep in node.dependents:
                    if all(d in contexts for d in dep.dependencies):
                        queue.add(dep)

                for dep in node.dependencies:
                    if all(d in finished for d in dep.dependents):
                        contexts.pop(dep)
            except Exception as e:
                raise NotImplementedError(f"Failed to encode step '{node.id}' of type {type(node)}") from e

        root = plan.root
        return contexts[root].tables[root.name]
    
    def context(self, tables):
        return Context(tables)
    def derived_schema(self, expressions):
        return DerivedSchema(
            expression.alias_or_name if isinstance(expression, exp.Expression) else expression for expression in expressions
        )
       
    def scan(self, node: Scan, context: Context) -> Dict:
        prev_step = self.prev_steps.popleft()
        if node.source:
            if isinstance(node.source, exp.Table):
                rows = self.instance.get_rows(node.source.name)
                sql_conditions = []
                for column in self.instance.column_names(node.source.name, dialect= self.dialect):
                    if self.instance.is_unique(node.source.name, column):
                        sql_conditions.append(exp.Column(this= exp.to_identifier(column, quoted=True), table = node.source.alias_or_name, _type = self.instance.get_column_type(node.source.name, column, dialect= self.dialect), is_unique= True, nullable = self.instance.nullable(node.source.name, column)))
            elif isinstance(node.source, exp.Subquery):
                ...
            else:
                raise NotImplementedError(f"Scan source type {type(node.source)} not supported yet.")
            self.trace.which_path(step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= [], takens= [PBit.TRUE] * len(sql_conditions), branch= True, rowids = (), attach_to= prev_step)
            for row in rows:
                symbolic_exprs = [row[columnref.name] for columnref in sql_conditions]
                self.trace.which_path(step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= symbolic_exprs, takens= [PBit.TRUE] * len(symbolic_exprs), branch= True, rowids= row.rowid, attach_to= prev_step)

            tbl = DerivedSchema(columns = self.instance.column_names(node.source.name, dialect= self.dialect), rows = rows)
            self.prev_steps.append((node.type_name, node.name))
            source_context = self.context({node.name: tbl})
            return self._project_and_filter(node, source_context)
            
    def _project_and_filter(self, node: Step, context: Context) -> Context:
        if node.condition:
            print(repr(node.condition))
            context = self.filters(node, context)
        if node.projections:
            context = self.project(node, context)
        return context
    def project(self, node: Step, context: Context) -> Context:
        
        if node.projections is None:
            return context
        prev_step = self.prev_steps.popleft()
        sink = self.derived_schema(node.projections)
        
        for reader, _ in context:
            row = {}
            sql_conditions, smt_conditions = [], []
            for project in node.projections:
                expr_encoder = ExpressionEncoder()
                alias_name = project.alias_or_name
                if isinstance(project, exp.Alias):
                    project = project.this
                ctx = expr_encoder.encode(project, scope = context.env["scope"])
                row[alias_name] =  ctx[project]
                smt_conditions.extend(ctx.get("smt_conditions"))
                sql_conditions.extend(ctx.get("sql_conditions"))
            
            sink.append(Row(this = reader.row.rowid, columns = row))
            self.trace.which_path(step_type= "Project", step_name= node.name, sql_conditions=sql_conditions, smt_exprs=smt_conditions, takens=[1 if isinstance(sql, ColumnRef) or isinstance(sql, exp.Column) else bool(smt.concrete)
                        for smt, sql in zip(smt_conditions, sql_conditions)], branch=True, rowids=reader.row.rowid, attach_to= prev_step)
        self.prev_steps.append(('Project', node.name))
        return self.context( {node.name : sink})
        
    def filters(self, node: Step, context: Context) -> Dict:
        if node.condition is None:
            return context
        
        prev_step = self.prev_steps.popleft()
        rows = []
        for reader, _ in context:
            expr_encoder = ExpressionEncoder()
            ctx = expr_encoder.encode(node.condition, scope = context.env["scope"])
            result = ctx[node.condition]
            print(ctx)
            branch = result.concrete is True
            smt_conditions = ctx['smt_conditions']
            sql_conditions = ctx['sql_conditions']
            if branch:
                rows.append(reader.row)
            takens = [
                (b.concrete if b.concrete is not None else 0)
                for b in smt_conditions
            ]
            self.trace.which_path(step_type= "Filter", step_name= node.name, sql_conditions=sql_conditions, smt_exprs=smt_conditions, takens=takens, branch=branch, rowids=reader.row.rowid, attach_to= prev_step)
        self.prev_steps.append(('Filter', node.name))
        return self.context({
            name : DerivedSchema(table.columns, rows, column_range= table.column_range) for name, table in context.tables.items()
        })
        
    def join(self, node: Join, context: Context) -> Dict:
        source = node.source_name
        source_table = context.tables[source]
        source_context = self.context({source: source_table})
        column_ranges = {source: range(0, len(source_table.columns))}
        logger.info(f"column ranges: {column_ranges}")        
        for name, join in node.joins.items():
            table = context.tables[name]
            start = max(r.stop for r in column_ranges.values())
            column_ranges[name] = range(start, len(table.columns) + start)
            logger.info(f"column ranges after adding {name}: {column_ranges}")
            join_context = self.context({name: table})
            kind = join['side']
            
            if kind == "LEFT":
                rows = self._left_join(node, join, source_context, join_context)
            elif kind == "RIGHT":
                rows = self._right_join(node, join, source_context, join_context)
            elif kind == "NATURAL":
                rows = self._natural_join(node, join, source_context, join_context)
            else:
                rows = self._inner_join(node, join, source_context, join_context)
                
            source_context = self.context(
                {
                    name: DerivedSchema(source_table.columns + table.columns, rows, column_range)
                    for name, column_range in column_ranges.items()
                }
            )
        return self._project_and_filter(node, source_context)
    
    def _product(self, source_context: Context, join_context: Context) -> List[Dict]:
        rows = []
        for left_row in source_context.table:
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                rows.append(combined_row)
        return rows
    
    def _inner_join(self, node, join, source_context: Context, join_context: Context) -> List:
        prev_step = self.prev_steps.popleft()
        rows = []
        for left_row in source_context.table:
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(join['source_key'], join['join_key']):
                    smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                try:
                    smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
                    branch = smt_expr.concrete is True
                    if branch:
                        rows.append(combined_row)
                except NullValueError:
                    branch = False
                takens = [2 if b.concrete is True else 3 for b in smt_exprs]
                self.trace.which_path(step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= combined_row.rowid, attach_to= prev_step)              
        self.prev_steps.append((node.type_name, node.name))
        return rows
        
    def _left_join(self, node, join, source_context: Context, join_context: Context) -> List[Dict]:
        prev_step = self.prev_steps.popleft()
        rows = []
        for left_row in source_context.table:
            smt_exprs = []
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                smt_conditions, sql_conditions = [], []
                for source_key, join_key in zip(join['source_key'], join['join_key']):
                    smt_conditions.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                try:
                    smt_expr = reduce(lambda x, y: x.and_( y), smt_conditions)
                    smt_exprs.append(smt_expr)
                    branch = smt_expr.concrete is True
                    if branch:
                        rows.append(combined_row)
                        takens = [2 if b else 3 for b in smt_conditions]
                        self.trace.which_path(step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= branch, rowids= combined_row.rowid, attach_to= prev_step)
                    
                except NullValueError:
                    branch = False
            if smt_exprs and any(smt_exprs):
                continue
            null_vlaues = {column: Const(None) for column in join_context.table.columns}
            new_row = {c: v for c, v in left_row.row.items()}
            new_row.update(null_vlaues)
            row = Row(left_row.row.rowid, new_row)
            smt_condition = reduce(lambda x, y: x.and_(y).not_(), smt_exprs)
            self.trace.which_path(step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= [smt_condition], takens= [3], branch= True, rowids= row.rowid, attach_to= prev_step)
            rows.append(row)
        self.prev_steps.append((node.type_name, node.name))
        return rows
          
    def _right_join(self, node, join, source_context: Context, join_context: Context) -> List[Dict]:
        ...
    def _natural_join(self, node, join, source_context: Context, join_context: Context) -> List[Dict]:
        
        prev_step = self.prev_steps.popleft()
        rows = []
        source_keys = []
        join_keys = []
        for column in source_context.table.columns:
            if column in join_context.table.columns:
                source_keys.append(exp.Column(this= exp.to_identifier(column), table= source_context.table.alias_or_name))
                join_keys.append(exp.Column(this= exp.to_identifier(column), table= join_context.table.alias_or_name))
                        
        for left_row in source_context.table:
            for right_row in join_context.table:
                combined_row = left_row.row + right_row.row
                smt_exprs, sql_conditions = [], []
                for source_key, join_key in zip(source_keys, join_keys):
                    smt_exprs.append(combined_row[source_key.name].eq(combined_row[join_key.name]))
                    sql_conditions.append(exp.EQ(this= source_key, expression= join_key))
                try:
                    smt_expr = reduce(lambda x, y: x.and_( y), smt_exprs)
                    branch = smt_expr.concrete is True
                    if branch:
                        rows.append(combined_row)
                except NullValueError:
                    branch = False
                takens = [2 if b.concrete is True else 3 for b in smt_exprs]
                self.trace.which_path(step_type= node.type_name, step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_exprs, takens= takens, branch= branch, rowids= combined_row.rowid, attach_to= prev_step)              
        self.prev_steps.append((node.type_name, node.name))
        return rows
    
    def aggregate(self, node: Aggregate, context: Context) -> Dict:
        
        having_condition = None
        aggregations, aggregation_alias = [], {}
        
        for agg_func in node.aggregations:
            if node.condition and agg_func.alias_or_name == node.condition.alias_or_name:
                having_condition = agg_func
            else:
                aggregation_alias[agg_func.alias_or_name] = agg_func.this
                aggregations.append(agg_func)
                
        
        
        operand_alias_names = {node.alias_or_name: node.this for node in node.operands}
        
        if node.operands:
            operand_schema = DerivedSchema(self.derived_schema(node.operands).columns)
            for reader, ctx in context:
                mapping = {}
                for operand in node.operands:
                    alias_name = operand.alias_or_name
                    if isinstance(operand, exp.Alias):
                        operand = operand.this
                    if isinstance(operand, exp.Distinct):
                        operand = operand.expressions[0]
                    if isinstance(operand, exp.Star):
                        r = Const(1, dtype= DataType.build('int'))
                    else:
                        expr_encoder = ExpressionEncoder()
                        ctx = expr_encoder.encode(operand, scope = context.env["scope"])
                        r = ctx[operand]
                    mapping[alias_name] = r
                
                operand_schema.append(mapping)
            for i, (a, b) in enumerate(zip(context.table.rows, operand_schema.rows)):
                new_row = {k: v for k, v in a.items()}
                new_row.update({k: v for k, v in b.items()})
                context.table.rows[i] = Row(a.rowid, new_row)
            width = len(context.columns)
            for column in operand_schema.columns:
                if column not in context.table.columns:
                    context.table.add_columns(column)
            operand_table = DerivedSchema(
                context.columns,
                context.table.rows,
                range(width, -1),
            )
            context = self.context(
                {
                    None: operand_table,
                    **context.tables,
                }
            )
        
        sink = self.derived_schema(node.projections)
        
        groups = {}
        for reader, _ in context:
            row = reader.row
            group_key = ()
            alias = []
            for gid, expression in node.group.items():
                group_key += (row[expression.alias_or_name],)
                alias.append(gid)                
            concrete_group_key = tuple(v.concrete for v in group_key)
            if concrete_group_key not in groups:
                groups[concrete_group_key] = {"group_key": group_key, "rows": [], "alias": alias}
            groups[concrete_group_key]["rows"].append(row)
        
        self.groupby(node, groups)
        # for _, group_info in groups.items():
        #     group_key = group_info["group_key"]
        #     group_rows = group_info["rows"]
        #     rowids = sum((row.rowid for row in group_rows), ())
        #     g = Group({c: k for c, k in zip(sql_conditions, group_key)}, rowids)
        #     smt_conditions = [g] * len(sql_conditions)
        #     self.trace.which_path(step_type= "Groupby", step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= True, rowids= rowids, attach_to= prev_step)
        
        # if node.group:
        #     self.prev_steps.append(('Groupby', node.name))
            # prev_step = ('Groupby', node.name)
            
        # result_rows = []
        # for _, group_info in groups.items():
        #     group_key = group_info["group_key"]
        #     group_rows = group_info["rows"]
        #     rowids = sum((row.rowid for row in group_rows), ())
        #     new_row = {g_name: k for g_name, k in zip(group_info['alias'], group_key)}
            
        #     for func_index, agg_func in enumerate(aggregations):
        #         alias = agg_func.alias_or_name
        #         func = agg_func.this
        #         operand_alias = func.this.alias_or_name
        #         operand = operand_alias_names[operand_alias]
        #         values = []
        #         for row in group_rows:
        #             v = row[operand_alias]
        #             if isinstance(operand, exp.Star) or v.concrete is not None:
        #                 values.append(v)
        #         if isinstance(operand, exp.Distinct):
        #             values = list(set(values))                    
        #         if isinstance(func, exp.Count):
        #             value = Const(len(values), dtype= DataType.build('int'))
        #         elif isinstance(func, exp.Sum):
        #             sum_value = sum(values) if values else Const(0, dtype= DataType.build('int'))
        #             value = Const(sum_value, dtype= DataType.build('int'))
        #         elif isinstance(func, exp.Max):
        #             min_value = (
        #                     max(values)
        #                     if values
        #                     else None
        #                 )
        #             value = Const(min_value, dtype=agg_func.type)
        #         elif isinstance(func, exp.Min):
        #             min_value = (
        #                     min(values)
        #                     if values
        #                     else None
        #                 )
        #             value = Const(min_value, dtype=agg_func.type)
                
        #         elif isinstance(agg_func.this, exp.Avg):
        #             if values:
        #                 avg_value = sum(values) / len(values)
        #             else:
        #                 avg_value = None
        #             value = Const(avg_value, dtype= DataType.build('REAL'))
        #         else:
        #             raise NotImplementedError(f"Aggregation function {func} not supported yet.")
        #         new_row[alias] = value
        #     result_rows.append(Row(rowids, new_row))
        #     g = Group({c: k for c, k in zip(sql_conditions, group_key)}, rowids)
        #     sql_conditions = list(aggregations)
            
        #     smt_conditions = [g] * len(sql_conditions)
        #     takens = [PBit.GROUP_SIZE] * len(sql_conditions)
        #     if aggregations:
        #         self.trace.which_path(step_type= "Aggregate", step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= True, rowids= rowids, attach_to= prev_step, operand_alias_names = operand_alias_names)            
            
        # if aggregations:
        #     self.prev_steps.append(('Aggregate', node.name))
        # else:
        #     self.prev_steps.append(('Groupby', node.name))
        
        result_rows = self.aggregate_functions(node, groups, aggregations, operand_alias_names)
        
        sink = self.derived_schema(list(node.group) + aggregations)
        if node.projections:
            for row in result_rows:
                mappings = {}
                for project in node.projections:
                    alias_name = project.alias_or_name
                    if isinstance(project, exp.Alias):
                        alias_name = project.this.alias_or_name
                    mappings[project.alias_or_name] = row[alias_name]
                sink.append(Row(row.rowid, mappings))
        else:
            sink.rows.extend(result_rows)
        context = self.context({node.name: sink, **{name: sink for name in context.tables}})
        if having_condition:
            return self.having(node, having_condition, aggregation_alias, operand_alias_names, context)
        return context
    
    def groupby(self, node: Aggregate, groups: Dict):
        if not node.group:
            return
        
        prev_step = self.prev_steps.popleft()
        sql_conditions, takens = [], []
        for group in list(node.group.values()):
            sql_conditions.append(group)
            takens.append(PBit.GROUP_SIZE)
        
        for _, group_info in groups.items():
            group_key = group_info["group_key"]
            group_rows = group_info["rows"]
            rowids = sum((row.rowid for row in group_rows), ())
            g = AggGroup(this = rowids, group_key = group_key, group_values = group_rows)
            # g = Group({c: k for c, k in zip(sql_conditions, group_key)}, rowids)
            smt_conditions = [g] * len(sql_conditions)
            self.trace.which_path(step_type= "Groupby", step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= True, rowids= rowids, attach_to= prev_step)
        self.prev_steps.append(('Groupby', node.name))
        
        
        
        
    def aggregate_functions(self, node: Aggregate, groups: Dict, aggregations: List, operand_alias_names: Dict, ) -> List[Row]:
        prev_step = self.prev_steps.popleft()
        result_rows = []
        for _, group_info in groups.items():
            group_key = group_info["group_key"]
            group_rows = group_info["rows"]
            rowids = sum((row.rowid for row in group_rows), ())
            new_row = {g_name: k for g_name, k in zip(group_info['alias'], group_key)}
            
            for func_index, agg_func in enumerate(aggregations):
                alias = agg_func.alias_or_name
                func = agg_func.this
                operand_alias = func.this.alias_or_name
                operand = func.unnest_operands()[0]
                if operand.alias_or_name in operand_alias_names:
                    operand = operand_alias_names[operand.alias_or_name]
                
                values = []
                for row in group_rows:
                    v = row[operand_alias]
                    if isinstance(operand, exp.Star) or v.concrete is not None:
                        values.append(v)
                if isinstance(operand, exp.Distinct):
                    values = list(set(values))                    
                if isinstance(func, exp.Count):
                    value = Const(len(values), dtype= DataType.build('int'))
                elif isinstance(func, exp.Sum):
                    sum_value = sum(values) if values else Const(0, dtype= DataType.build('int'))
                    value = Const(sum_value, dtype= DataType.build('int'))
                elif isinstance(func, exp.Max):
                    min_value = (
                            max(values)
                            if values
                            else None
                        )
                    value = Const(min_value, dtype=agg_func.type)
                elif isinstance(func, exp.Min):
                    min_value = (
                            min(values)
                            if values
                            else None
                        )
                    value = Const(min_value, dtype=agg_func.type)
                
                elif isinstance(agg_func.this, exp.Avg):
                    if values:
                        avg_value = sum(values) / len(values)
                    else:
                        avg_value = None
                    value = Const(avg_value, dtype= DataType.build('REAL'))
                else:
                    raise NotImplementedError(f"Aggregation function {func} not supported yet.")
                new_row[alias] = value
            result_rows.append(Row(rowids, new_row))
            g = AggGroup(this = rowids, group_key = group_key, group_values = group_rows)
            sql_conditions = list(aggregations)
            smt_conditions = [g] * len(sql_conditions)
            takens = [PBit.AGGREGATE_SIZE] * len(sql_conditions)
            if aggregations:
                self.trace.which_path(step_type= "Aggregate", step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= takens, branch= True, rowids= rowids, attach_to= prev_step, operand_alias_names = operand_alias_names)
                
        self.prev_steps.append(('Aggregate', node.name))
        return result_rows
    
    def having(self, node: Aggregate, having: exp.Expression, aggregation_alias: Dict[str, exp.Expression], operand_alias_names: Dict[str, exp.Expression], context: Context) -> Dict:
        if node.condition is None:
            return context
        prev_step = self.prev_steps.popleft()
        
        cond = having.copy()
        def replace_func(e):
            for alias, agg_func in aggregation_alias.items():
                if e == agg_func:
                    return exp.Column(this = exp.to_identifier(alias), table = node.name)
            return e
        condition = cond.transform(replace_func)
        condition = condition.this
        rows = []
        for reader, _ in context:
            expr_encoder = ExpressionEncoder()
            ctx = expr_encoder.encode(condition, scope = context.env["scope"])
            result = ctx[condition]
            branch = result.concrete is True
            smt_conditions = ctx['smt_conditions']
            sql_conditions = ctx['sql_conditions']
            if branch:
                rows.append(reader.row)
            takens = [
                (PBit.HAVING_TRUE if b.concrete is True else PBit.HAVING_FALSE)
                for b in smt_conditions
            ]
            
            logger.info(f"Having clause SQL conditions: {sql_conditions}, takens: {takens}")
            self.trace.which_path(step_type= "Having", step_name= node.name, sql_conditions=sql_conditions, smt_exprs=smt_conditions, takens=takens, branch=branch, rowids=reader.row.rowid, attach_to= prev_step, aggregation_alias_names = aggregation_alias, operand_alias_names= operand_alias_names)
        self.prev_steps.append(('Having', node.name))
        return self.context({
            name : DerivedSchema(table.columns, rows, column_range= table.column_range) for name, table in context.tables.items()
        })
        
    @non_fatal(default_from_args=lambda *args, **kwargs: args[2])
    def sort(self, node: Sort, context: Context) -> Dict:
        prev_step = self.prev_steps.popleft()
        all_columns = list(context.columns)
        for p in [p.alias_or_name for p in node.projections]:
            if p not in all_columns:
                all_columns.append(p)
        sink = self.derived_schema(all_columns)
        index = 0
        for reader, ctx in context:
            o_row = {k : v for k, v in reader.row.items()}
            for p in node.projections:
                alias = p.alias_or_name
                p = p.this if isinstance(p, exp.Alias) else p
                try:
                    o_row[p.alias_or_name] = reader.row[alias]
                except KeyError:
                    if p.args.get('table') in ctx.tables:
                        table = ctx.tables[p.args.get('table')]
                        if p.name in table.columns:
                            o_row[p.alias_or_name] = table.rows[index][p.name]
            index += 1
            sink.append(Row(reader.row.rowid, o_row))
        
        @total_ordering
        class SortValue:
            def __init__(self, value, descending: bool):
                self.value = value
                self.desc = descending

            def __eq__(self, other):
                return self.value == other.value

            def __lt__(self, other):
                if self.desc:
                    return self.value > other.value
                return self.value < other.value
        def sort_key(row):
            key = []
            for expression in node.key:
                
                v = row[expression.this.alias_or_name].concrete
                desc = expression.args.get('desc')
                null_first = expression.args.get('nulls_first', False)
                
                if v is None:
                    w = 1 if null_first else -1
                    key.append((w, None))
                else:
                    key.append((0, SortValue(v, desc)))
            return tuple(key)
        
        sorted_data = sorted(sink.rows, key=sort_key)
        sql_conditions = [o.this for o in node.key]
        
        for row in sorted_data:
            smt_conditions = [row[o.this.alias_or_name]  for o in node.key]
            self.trace.which_path(step_type= "Sort", step_name= node.name, sql_conditions= sql_conditions, smt_exprs= smt_conditions, takens= [True] * len(smt_conditions), branch= True, rowids= row.rowid, attach_to=prev_step)
        rows = sorted_data   
        if not math.isinf(node.limit):
            rows = sorted_data[0 : node.limit]
        new_rows = []
        for row in rows:
            new_row = {}
            for p in node.projections:
                new_row[p.alias_or_name] = row[p.alias_or_name]
            new_rows.append(Row(row.rowid, new_row))
        
        output = DerivedSchema(
            [p.alias_or_name for p in node.projections],
            rows=new_rows,
        )
        self.prev_steps.append(('Sort', node.name))
        return self.context({node.name: output})
    def set_operation(self, node: SetOperation, context: Context) -> Dict:
        """We do not need to track set operations here"""
        
        left = context.tables[node.left]
        right = context.tables[node.right]

        sink = self.derived_schema(left.columns)

        if issubclass(node.op, exp.Intersect):
            sink.rows = list(set(left.rows).intersection(set(right.rows)))
        elif issubclass(node.op, exp.Except):
            sink.rows = list(set(left.rows).difference(set(right.rows)))
        elif issubclass(node.op, exp.Union) and node.distinct:
            sink.rows = list(set(left.rows).union(set(right.rows)))
        else:
            sink.rows = left.rows + right.rows
        if not math.isinf(node.limit):
            sink.rows = sink.rows[0 : node.limit]
        return self.context({node.name: sink})
    