from __future__ import annotations
from abc import abstractmethod
from functools import reduce, wraps
from sys import flags
from sqlglot import exp as sqlglot_exp
from sqlglot import generator
from sqlglot.helper import is_type
from src.parseval.dtype import DataType
from typing import TYPE_CHECKING, List, Optional, Dict, Any
from src.parseval.symbol import Row
from .rex import *
import operator, logging


if TYPE_CHECKING:
    from parseval.instance import Instance
    from src.parseval.uexpr import UExprToConstraint

from abc import ABC


from dataclasses import dataclass, field
from contextlib import contextmanager


class Skipnulls(Exception):
    pass


class ExpressionEncoder:
    SYMBOLIC_EVAL_REGISTRY = {
        "eq": lambda *args: args[0].eq(args[1]),
        "neq": lambda *args: args[0].ne(args[1]),
        "gt": lambda *args: args[0] > args[1],
        "lt": lambda *args: args[0] < args[1],
        "lte": lambda *args: args[0] >= args[1],
        "gte": lambda *args: args[0] <= args[1],
        "like": lambda *args: args[0].like(args[1]),
        "and": lambda *args: args[0].and_(args[1]),
        "or": lambda *args: args[0].or_(args[1]),
        "add": lambda *args: args[0] + args[1],
        "sub": lambda *args: args[0] - args[1],
        "mul": lambda *args: args[0] * args[1],
        "div": lambda *args: args[0] // args[1],
    }

    def __init__(
        self,
        row: Row,
        plan_encoder: Optional[PlanEncoder] = None,
        ignore_nulls: bool = False,
    ):
        super().__init__()
        self.row = row
        self.ignore_nulls = ignore_nulls
        self.plan_encoder = plan_encoder

    def in_predicates(self, context):
        return bool(context.get("_predicate_stack", []))

    @contextmanager
    def predicate_scope(self, context):
        stack = context.setdefault("_predicate_stack", [])
        stack.append(True)
        try:

            def track(expr, smt_expr):
                context.setdefault("ref_conditions", []).append(expr)
                context.setdefault("smt_conditions", []).append(smt_expr)

                return smt_expr

            yield track
        finally:
            stack.pop()

    def visit(self, expr, parent_stack=None, context=None):
        if expr is None:
            return None
        parent_stack = parent_stack or []
        context = context if context is not None else {}
        handler = getattr(self, f"visit_{expr.key}", self.generic_visit)
        result = handler(expr, parent_stack, context)
        return result

    def generic_visit(self, expr, parent_stack, context):
        if isinstance(expr, sqlglot_exp.Predicate):
            return self.visit_predicate(expr, parent_stack, context)
        elif isinstance(expr, sqlglot_exp.Binary):
            return self.visit_binary(expr, parent_stack, context)
        raise NotImplementedError(f"No visit_{expr.key} method defined")

    def visit_columnref(self, expr: ColumnRef, parent_stack=None, context=None):
        smt_expr = self.row[expr.ref]
        if not self.in_predicates(context=context):
            context.setdefault("ref_conditions", []).append(expr)
            context.setdefault("smt_conditions", []).append(smt_expr)
        return smt_expr

    def visit_literal(self, expr: sqlglot_exp.Literal, parent_stack=None, context=None):
        from src.parseval.symbol import Const
        value = expr.this
        datatype = expr.args.get('datatype')
        try:
            if datatype.is_type(*DataType.INTEGER_TYPES):
                value = int(value)
            elif datatype.is_type(*DataType.REAL_TYPES):
                value = float(value)
            elif datatype.is_type(DataType.Type.BOOLEAN):
                value = bool(value)
            elif datatype.is_type(*DataType.TEMPORAL_TYPES):
                from datetime import datetime
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        value = datetime.strptime(value, fmt)
                    except ValueError:
                        continue
            elif datatype.is_type(*DataType.TEXT_TYPES):
                value = str(value)
        except Exception as e:
            logging.info(f'Failed to convert {repr(expr)} to const in datatype {datatype}, {e}')
            value = value            
        return Const(value, dtype= datatype)

    def visit_predicate(self, expr: sqlglot_exp.Predicate, parent_stack, context):
        with self.predicate_scope(context=context) as track:
            left = self.visit(
                expr.this, parent_stack=parent_stack + [expr], context=context
            )
            right = self.visit(
                expr.expression, parent_stack=parent_stack + [expr], context=context
            )
            
            try:
                smt_expr = self.SYMBOLIC_EVAL_REGISTRY[expr.key](left, right)
                return track(expr, smt_expr)
            except Exception as e:
                if not self.ignore_nulls and left.concrete is None:
                    raise Skipnulls()
                
            
            return None

    def visit_is_null(self, expr: Is_Null, parent_stack=None, context=None):
        with self.predicate_scope(context=context) as track:
            this = self.visit(
                expr.this, parent_stack=parent_stack + [expr], context=context
            )
            smt_expr = this.is_(None)
            return track(expr, smt_expr)

    def visit_is_not_null(self, expr, parent_stack=None, context=None):

        with self.predicate_scope(context=context) as track:
            this = self.visit(
                expr.this, parent_stack=parent_stack + [expr], context=context
            )
            smt_expr = this.is_(None).not_()
            # smt_expr = this.is_not(None)
            return track(expr, smt_expr)

    def visit_binary(self, expr: sqlglot_exp.Binary, parent_stack, context):
        left = self.visit(
            expr.this, parent_stack=parent_stack + [expr], context=context
        )
        right = self.visit(
            expr.expression, parent_stack=parent_stack + [expr], context=context
        )
        smt_expr = self.SYMBOLIC_EVAL_REGISTRY[expr.key](left, right)
        return smt_expr


    def visit_not(self, expr: sqlglot_exp.Not, parent_stack=None, context=None):
        this = self.visit(
            expr.this, parent_stack=parent_stack + [expr], context=context
        )
        return this.not_()

    def visit_cast(self, expr, parent_stack=None, context=None):
        inner = self.visit(expr.this, parent_stack + [expr], context)
        to_type = expr.to
        if to_type.is_type(DataType.Type.DATE, DataType.Type.DATE32):
            from datetime import datetime
            inner.concrete = datetime.strptime(inner.concrete, "%Y-%m-%d")
        elif to_type.is_type(DataType.Type.DATETIME, DataType.Type.DATETIME64):
            from datetime import datetime
            inner.concrete = datetime.strptime(inner.concrete, "%Y-%m-%dT%H:%M:%S")

        args = (a for a in inner.args)
        return inner.__class__(*args, dtype = expr.to, **inner.metadata)

        concrete = operand.concrete if isinstance(expr.args[0], ColumnRef) else operand

        if concrete is not None:
            if expr.to_type.is_numeric():
                concrete = int(concrete)
            elif expr.to_type.is_datetime():
                from datetime import datetime

                concrete = datetime.strptime(concrete, "%Y-%m-%d")
            else:
                concrete = str(concrete)
        if isinstance(expr.args[0], Literal):
            return concrete
        else:
            operand.concrete = concrete
            return operand

    def visit_case(self, expr: sqlglot_exp.Case, parent_stack=None, context=None):
        for when in expr.args.get("ifs"):
            smt_expr = self.visit(when.this, parent_stack + [expr], context)
            if smt_expr:
                return self.visit(when.args.get("true"), parent_stack + [expr], context)
        return self.visit(expr.args.get("default"), parent_stack + [expr], context)

    def visit_subquery(self, expr, parent_stack, context):
        sub_ctx = {
            "ref_conditions": [],
            "sql_conditions": [],
            "smt_conditions": [],
            "parent": expr,
        }
        for child in expr.expressions:
            self.visit(child, parent_stack + [expr], sub_ctx)
        # Merge subquery predicates into main context
        # context["predicates"].extend(sub_ctx["predicates"])
        # context["columns"].extend(sub_ctx["columns"])
        # context["smt_constraints"].extend(sub_ctx["smt_constraints"])
        return expr

    # def visit_subquery(self, expr: sqlglot_exp.Subquery):
    #     for query in expr.query:

    #         print(query.pprint())
    #         res = query.accept(self.plan_encoder)

    #         logging.info(f"Subquery result rows: {len(res.data)}, {res}")
    #         return res.data[0][0]


class LogicalPlanVisitor(ABC):
    """Abstract visitor for traversing logical plans"""

    def visit(self, expr: LogicalOperator, parent_stack=None, context=None):
        if expr is None:
            return
        parent_stack = parent_stack or []
        context = context or {}
        handler = getattr(
            self, f"visit_{expr.operator_type.lower()}", self.generic_visit
        )
        return handler(expr, parent_stack, context)

    def generic_visit(self, expr, parent_stack, context):
        raise NotImplementedError(
            f"No visit_{expr.operator_type.lower()} method defined"
        )


class UExprEncoder(LogicalPlanVisitor):
    def __init__(
        self, instance: Instance, trace: UExprToConstraint, verbose: bool = True
    ):
        super().__init__()
        self.instance = instance
        self.trace = trace
        self.context = {}
        self.verbose = verbose

    def visit_logicalscan(self, expr: LogicalScan, parent_stack=None, context=None):
        ref_conditions, sql_conditions = [], []
        for columnref in expr.schema(catalog=self.instance.catalog).columns:
            unique = columnref.args.get("unique", False)
            if unique:
                ref_conditions.append(
                    ColumnRef(
                        this=sqlglot_exp.to_identifier("$" + str(columnref.ref)),
                        table=expr.table_name,
                        ref=columnref.ref,
                        datatype=columnref.datatype,
                    )
                )
                sql_conditions.append(columnref)

        expr.set("sql_conditions", sql_conditions)
        expr.set("ref_conditions", ref_conditions)

        self.trace.which_path(
            operator=expr,
            ref_conditions=ref_conditions,
            sql_conditions=sql_conditions,
            takens=[True] * len(sql_conditions),
            branch=True,
            rows=[],
        )

    def visit_logicalproject(
        self, expr: LogicalProject, parent_stack=None, context=None
    ):
        input_schema = expr.this.schema(catalog=self.instance.catalog)
        ref_conditions, sql_conditions = [], []
        for proj in expr.expressions:
            if isinstance(proj, ColumnRef):
                sql_conditions.append(input_schema.columns[proj.ref])
                ref_conditions.append(proj)
            elif isinstance(proj, sqlglot_exp.Case):
                for when in proj.ifs:
                    for predicate in when.condition.find_all(sqlglot_exp.Predicate):
                        sql_conditions.append(
                            predicate.transform(resolve_schema, input_schema)
                        )
                        ref_conditions.append(predicate)
                    sql_conditions.append(
                        when.true.transform(resolve_schema, input_schema)
                    )
                    ref_conditions.append(when.true)
                    break
            else:

                sql_conditions.append(proj.transform(resolve_schema, input_schema))
                ref_conditions.append(proj)
        self.trace.which_path(
            expr,
            ref_conditions=ref_conditions,
            sql_conditions=sql_conditions,
            takens=[True] * len(sql_conditions),
            branch=True,
        )

    def visit_logicalfilter(self, expr: LogicalFilter, parent_stack=None, context=None):
        input_schema = expr.schema(catalog=self.instance.catalog)
        ref_conditions, sql_conditions = [], []
        takens = []
        for predicate in expr.condition.find_all(sqlglot_exp.Predicate):
            sql_conditions.append(predicate.transform(resolve_schema, input_schema))

            if isinstance(predicate.parent, sqlglot_exp.Not):
                takens.append(False)
            else:
                takens.append(True)
            ref_conditions.append(predicate)

        self.trace.which_path(
            expr,
            ref_conditions=ref_conditions,
            sql_conditions=sql_conditions,
            takens=takens,
            branch=True,
        )

    def visit_logicaljoin(self, expr: LogicalJoin, parent_stack=None, context=None):
        self.visit_logicalfilter(expr, parent_stack, context)

    def visit_logicalsort(self, expr: LogicalSort, parent_stack=None, context=None):
        input_schema = expr.this.schema(catalog=self.instance.catalog)
        ref_conditions = expr.sorts

        sql_conditions = [
            cond.transform(resolve_schema, input_schema) for cond in ref_conditions
        ]
        self.trace.which_path(
            expr,
            ref_conditions=ref_conditions,
            sql_conditions=sql_conditions,
            takens=[True] * len(sql_conditions),
            branch=True,
        )

    def visit_logicalaggregate(
        self, expr: LogicalAggregate, parent_stack=None, context=None
    ):
        input_schema = expr.this.schema(catalog=self.instance.catalog)
        ref_conditions, sql_conditions = [], []
        for key in expr.keys:
            ref_conditions.append(key)
            sql_conditions.append(key.transform(resolve_schema, input_schema))

        for func in expr.aggs:
            ref_conditions.append(func)
            sql_conditions.append(func.transform(resolve_schema, input_schema))
        self.trace.which_path(
            expr,
            ref_conditions=ref_conditions,
            sql_conditions=sql_conditions,
            takens=[True] * len(sql_conditions),
            branch=True,
        )

    def visit_having(self, expr, parent_stack=None, context=None):
        return self.visit_logicalfilter(expr, parent_stack, context)


def track_next(func):
    @wraps(func)
    def wrapper(self, expr, *args, **kwargs):
        result = func(self, expr, *args, **kwargs)
        self.trace.advance(expr)
        return result

    return wrapper


class PlanEncoder(LogicalPlanVisitor):
    def __init__(
        self, instance: Instance, trace: UExprToConstraint, verbose: bool = True
    ):
        super().__init__()
        self.instance = instance
        self.trace = trace
        self.verbose = verbose

    @track_next
    def visit_scan(self, expr: LogicalScan, parent_stack, context):
        rows = self.instance.get_rows(expr.table_name)
        input_schema = expr.schema(catalog=self.instance.catalog)
        sql_conditions, ref_conditions = [], []
        for columnref in input_schema.columns:
            unique = columnref.args.get("unique", False)
            if unique:
                ref_conditions.append(
                    ColumnRef(
                        this=sqlglot_exp.to_identifier("$" + str(columnref.ref)),
                        table=expr.table_name,
                        ref=columnref.ref,
                        datatype=columnref.datatype,
                    )
                )
                sql_conditions.append(columnref)
        for row in rows:
            symbolic_exprs = [row[columnref.ref] for columnref in sql_conditions]
            if symbolic_exprs:
                self.trace.which_path(
                    expr,
                    ref_conditions=ref_conditions,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=symbolic_exprs,
                    takens=[True] * len(symbolic_exprs),
                    branch=True,
                    rows=[row],
                )
        return rows

    @track_next
    def visit_project(self, node: LogicalProject, parent_stack, context):
        input_data = self.visit(node.this, parent_stack + [node], context)
        input_schema = node.this.schema(catalog=self.instance.catalog)
        out = []
        for row in input_data:
            data = []
            ref_conditions, sql_conditions, smt_conditions = [], [], []
            for proj in node.expressions:
                context = {}
                encoder = ExpressionEncoder(row, plan_encoder=self, ignore_nulls=False)
                data.append(encoder.visit(proj, context=context))
                smt_conditions.extend(context.get("smt_conditions", []))
                ref_conditions.extend(context.get("ref_conditions", []))
                for cond in context.get("ref_conditions", []):
                    sql_conditions.append(cond.transform(resolve_schema, input_schema))

            self.trace.which_path(
                operator=node,
                ref_conditions=context.get("ref_conditions", []),
                sql_conditions=sql_conditions,
                symbolic_exprs=context.get("smt_conditions", []),
                takens=[
                    True if isinstance(sql, ColumnRef) else smt.concrete
                    for smt, sql in zip(smt_conditions, sql_conditions)
                ],
                branch=True,
                rows=[row],
            )
            out.append(Row(*data))
        return out

    @track_next
    def visit_filter(self, node: LogicalFilter, parent_stack, context):
        input_schema = node.this.schema(catalog=self.instance.catalog)
        input_data = self.visit(node.this, parent_stack + [node], context)
        out = []

        for row in input_data:
            encoder = ExpressionEncoder(row, plan_encoder=self)
            try:
                context = {}
                smt = encoder.visit(node.condition, context=context)
                if smt:
                    out.append(row)
                smt_conditions = context.get("smt_conditions", [])
                ref_conditions = context.get("ref_conditions", [])
                sql_conditions = [
                    cond.transform(resolve_schema, input_schema)
                    for cond in ref_conditions
                ]
                takens = [True if b.concrete else False for b in smt_conditions]
                self.trace.which_path(
                    operator=node,
                    ref_conditions=ref_conditions,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=takens,
                    branch=smt.concrete,
                    rows=[row],
                )
            except Skipnulls:
                logging.info(f"Skipping row with nulls: {row}")
                continue
        return out

    @track_next
    def visit_join(self, node: LogicalJoin, parent_stack, context):
        left_input = self.visit(node.left, parent_stack + [node], context)
        right_input = self.visit(node.right, parent_stack + [node], context)
        input_schema = node.schema(catalog=self.instance.catalog)
        out = []
        for lrow in left_input:
            for rrow in right_input:
                row = lrow + rrow
                try:
                    local_ctx = {}
                    encoder = ExpressionEncoder(row, plan_encoder=self)
                    smt = encoder.visit(node.condition, context=local_ctx)

                    if smt:
                        out.append(row)
                    ref_conditions = local_ctx.get("ref_conditions", [])
                    smt_conditions = local_ctx.get("smt_conditions", [])
                    takens = [bool(b) for b in smt_conditions]
                    sql_conditions = [
                        cond.transform(resolve_schema, input_schema)
                        for cond in ref_conditions
                    ]
                    self.trace.which_path(
                        operator=node,
                        ref_conditions=ref_conditions,
                        sql_conditions=sql_conditions,
                        symbolic_exprs=smt_conditions,
                        takens=takens,
                        branch=smt.concrete,
                        rows=[row],
                    )
                except Skipnulls:
                    logging.info(f"Skipping row with nulls: {row}")
                    pass
        return out

    @track_next
    def visit_sort(self, node: LogicalSort, parent_stack, context):
        input_data = self.visit(node.this, parent_stack + [node], context)
        input_schema = node.this.schema(catalog=self.instance.catalog)

        def sorted_pure(iterable, key=None, reverse=False):
            filtered = [
                row
                for row in iterable
                for value in key(row)
                if value.concrete is not None
            ]
            null_values = [
                row for row in iterable for value in key(row) if value.concrete is None
            ]

            def merge_sort(lst):
                if len(lst) <= 1:
                    return lst
                mid = len(lst) // 2
                left = merge_sort(lst[:mid])
                right = merge_sort(lst[mid:])
                return merge(left, right)

            def merge(left, right):
                result = []
                i = j = 0
                while i < len(left) and j < len(right):
                    a = key(left[i]) if key else left[i]
                    b = key(right[j]) if key else right[j]
                    if (a < b and not reverse) or (a > b and reverse):
                        result.append(left[i])
                        i += 1
                    else:
                        result.append(right[j])
                        j += 1
                result.extend(left[i:])
                result.extend(right[j:])
                return result

            return merge_sort(list(filtered)) + null_values

        data = sorted_pure(
            input_data,
            key=lambda row: tuple(row[column.ref - 1] for column in node.sorts),
            reverse=("DESCENDING" in node.dirs),
        )
        ref_conditions = node.sorts
        sql_conditions = [
            cond.transform(resolve_schema, input_schema) for cond in ref_conditions
        ]

        for row in data:
            smt_conditions = [row[column.ref - 1] for column in node.sorts]
            self.trace.which_path(
                operator=node,
                ref_conditions=ref_conditions,
                sql_conditions=sql_conditions,
                symbolic_exprs=smt_conditions,
                takens=[True] * len(ref_conditions),
                branch=True,
                rows=[row],
            )

        return data

    @track_next
    def visit_aggregate(self, node: LogicalAggregate, parent_stack, context):
        input_data = self.visit(node.this, parent_stack + [node], context)
        input_schema = node.this.schema(catalog=self.instance.catalog)
        from collections import defaultdict

        ### implement group by
        ref_conditions, sql_conditions, smt_conditions = [], [], []
        for key in node.keys + node.aggs:
            ref_conditions.append(key)
            sql_conditions.append(key.transform(resolve_schema, input_schema))

        out = []
        groups = defaultdict(list)
        for row in input_data:
            group_key = tuple(row[expr.ref] for expr in node.keys)
            groups[group_key].append(row)

        for group_key, rows in groups.items():
            row = [] + list(group_key)
            for agg_func in node.aggs:
                if not isinstance(agg_func, sqlglot_exp.AggFunc):
                    raise NotImplementedError(
                        f"Aggregate function {agg_func} not implemented yet."
                    )
                if agg_func.key == "count":
                    from src.parseval.symbol import Const

                    count_value = len(rows)
                    smt_conditions.append(Const(count_value, dtype="INT"))
                    row.append(Const(count_value, dtype="INT"))

                elif agg_func.key == "sum":
                    sum_value = sum(
                        row[agg_func.args[0].ref]
                        for row in rows
                        if row[agg_func.args[0].ref].concrete is not None
                    )
                    smt_conditions.append(sum_value)
                    row.append(sum_value)
                elif agg_func.key == "min":
                    min_value = min(
                        row[agg_func.this.ref]
                        for row in rows
                        if row[agg_func.this.ref].concrete is not None
                    )
                    smt_conditions.append(min_value)
                    row.append(min_value)
                else:
                    raise NotImplementedError(
                        f"Aggregate function {agg_func} not implemented yet."
                    )
            out.append(Row(row))
            self.trace.which_path(
                operator=node,
                ref_conditions=ref_conditions,
                sql_conditions=sql_conditions,
                symbolic_exprs=smt_conditions,
                takens=[True] * len(ref_conditions),
                branch=True,
                rows=[Row(row)],
            )
        return out

    def visit_having(self, node: LogicalHaving, parent_stack, context):
        input_data = self.visit(node.this, parent_stack + [node], context)
        input_schema = node.this.schema(catalog=self.instance.catalog)        
        out = []
        for row in input_data:
            encoder = ExpressionEncoder(row, plan_encoder=self)
            try:
                context = {}
                smt = encoder.visit(node.condition, context=context)
                if smt:
                    out.append(row)
                smt_conditions = context.get("smt_conditions", [])
                ref_conditions = context.get("ref_conditions", [])
                sql_conditions = [
                    cond.transform(resolve_schema, input_schema)
                    for cond in ref_conditions
                ]
                takens = [True if b.concrete else False for b in smt_conditions]
                self.trace.which_path(
                    operator=node,
                    ref_conditions=ref_conditions,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=takens,
                    branch=smt.concrete,
                    rows=[row],
                )
            except Skipnulls:
                logging.info(f"Skipping row with nulls: {row}")
                continue
        return out
        
        