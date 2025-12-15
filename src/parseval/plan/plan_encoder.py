from __future__ import annotations
from abc import ABC, abstractmethod
from datetime import datetime
from dateutil import parser as date_parser
from src.parseval.plan.rex import *
from typing import List, Optional, Union, Dict, TYPE_CHECKING, Any, Tuple
from src.parseval.constants import PBit
from src.parseval.helper import convert_to_literal
from src.parseval.states import (
    SchemaException,
    ParSEvalError,
    SyntaxException,
    Metadata,
)
from src.parseval.symbol import (
    Symbol,
    Row,
    Group,
    SymbolicRegistry,
    Const,
    NullValueError,
    ITE,
)
from contextlib import contextmanager
import logging

if TYPE_CHECKING:
    from src.parseval.instance import Instance
    from src.parseval.uexpr import UExprToConstraint

logger = logging.getLogger("parseval.plan.encoder")


class OperatorEncoder(ABC):
    def __init__(self, encoder: PlanEncoder):
        self.encoder = encoder

    @property
    def context(self):
        return self.encoder.context

    def append_to_context(self, node: Any, value: Any) -> None:
        self.context.setdefault(node, []).append(value)

    @property
    def instance(self):
        return self.encoder.instance

    def resolve_sql_conditions(self, sql_conditions: List[Expression], schema: Schema):
        resolved_conditions = [
            expr.transform(resolve_schema, schema) for expr in sql_conditions
        ]
        return resolved_conditions

    @abstractmethod
    def handle(self, node: Any, parent_stack: List[Any]) -> None:
        """
        Executes the logic for the specific node type.
        Access state via self.encoder.context
        Access services via self.encoder.instance / self.encoder.trace
        """
        pass


class ScanEncoder(OperatorEncoder):
    def handle(self, node: LogicalScan, parent_stack: List[Any]) -> None:
        rows = self.instance.get_rows(node.table_name)
        table_ref = self.instance.catalog.get_table(node.table_name)
        sql_conditions = []
        schema_refs = node.schema().columns
        for index, column in enumerate(table_ref.columns):
            if column.args.get("unique", False):
                schema_refs[index].set("unique", True)
                sql_conditions.append(schema_refs[index])
        with self.encoder.scope(node) as tracer:
            for row in rows:
                self.append_to_context(node, row)
                symbolic_exprs = [row[columnref.ref] for columnref in sql_conditions]
                tracer.which_path(
                    node,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=symbolic_exprs,
                    takens=[PBit.TRUE] * len(symbolic_exprs),
                    branch=True,
                    rowids=row.rowid,
                )


class ProjectEncoder(OperatorEncoder):
    def handle(self, node: LogicalProject, parent_stack: List[Any]) -> None:
        input_data = self.context[node.this]
        input_schema = node.this.schema()
        with self.encoder.scope(node) as tracer:
            for row in input_data:
                data = []
                sql_conditions, smt_conditions = [], []
                for proj in node.expressions:
                    encoder = ExpressionEncoder()
                    local_context = encoder.encode(proj, schema=input_schema, row=row)
                    smt_expr = local_context[proj]
                    data.append(smt_expr)
                    smt_conditions.extend(local_context.get("smt_conditions"))
                    sql_conditions.extend(local_context.get("sql_conditions"))
                self.append_to_context(node, Row(row.rowid, *data))

                sql_conditions = self.resolve_sql_conditions(
                    sql_conditions, input_schema
                )

                tracer.which_path(
                    operator=node,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=[
                        1 if isinstance(sql, ColumnRef) else smt.concrete
                        for smt, sql in zip(smt_conditions, sql_conditions)
                    ],
                    branch=True,
                    rowids=row.rowid,
                )


class FilterEncoder(OperatorEncoder):
    def handle(self, node: LogicalFilter, parent_stack: List[Any]) -> None:

        logger.info(f"Handling Filter Node: {node.operator_id}")
        input_data = self.context[node.this]
        input_schema = node.this.schema()
        scalar_queries = list(node.condition.find_all(ScalarQuery))
        new_condition = node.condition.copy()
        for scalar in scalar_queries:
            logger.info(f"Processing scalar subquery: {scalar}")
            scalar_out = self.context[scalar.this]
            datatype = scalar.this.args.get("datatype")
            concrete = scalar_out[0][0].concrete
            literal = convert_to_literal(concrete, datatype)
            new_condition.find(ScalarQuery).replace(literal)

        with self.encoder.scope(node) as tracer:
            for row in input_data:
                encoder = ExpressionEncoder()
                local_context = encoder.encode(
                    new_condition, row=row, schema=input_schema
                )
                smt_expr = local_context[new_condition]
                smt_conditions = local_context.get("smt_conditions", [])
                sql_conditions = local_context.get("sql_conditions", [])
                try:
                    if smt_expr:
                        self.append_to_context(node, row)
                except NullValueError:
                    ...
                takens = [
                    (b.concrete if b.concrete is not None else 0)
                    for b in smt_conditions
                ]
                sql_conditions = self.resolve_sql_conditions(
                    sql_conditions, input_schema
                )

                tracer.which_path(
                    operator=node,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=takens,
                    branch=smt_expr.concrete,
                    rowids=row.rowid,
                )


class JoinEncoder(OperatorEncoder):
    def handle_leftjoin(self, node: LogicalJoin, parent_stack: List[Any]) -> None:
        left_input = self.context[node.left]
        right_input = self.context[node.right]

        with self.encoder.scope(node) as tracer:
            for lrow in left_input:
                smt_exprs = []
                for rrow in right_input:
                    row = lrow + rrow
                    encoder = ExpressionEncoder()
                    local_context = encoder.encode(
                        node.condition, row=row, schema=node.schema()
                    )
                    smt_conditions = local_context.get("smt_conditions", [])
                    sql_conditions = self.resolve_sql_conditions(
                        local_context.get("sql_conditions", []), node.schema()
                    )

                    try:
                        smt_expr = local_context[node.condition]
                        if smt_expr:
                            self.append_to_context(node, row)
                            takens = [2 if b else 3 for b in smt_conditions]
                            tracer.which_path(
                                operator=node,
                                sql_conditions=sql_conditions,
                                symbolic_exprs=smt_conditions,
                                takens=takens,
                                branch=smt_expr.concrete,
                                rowids=row.rowid,
                            )
                        smt_exprs.append(reduce(lambda x, y: x.and_(y), smt_conditions))
                    except NullValueError:
                        ...

                if smt_exprs and any(smt_exprs):
                    continue
                null_vlaues = [
                    Const(None, dtype=md.args.get("datatype"))
                    for md in node.right.schema().columns
                ]
                row = Row(lrow.rowid, *lrow, *null_vlaues)
                self.append_to_context(node, row)
                smt_condition = reduce(lambda x, y: x.and_(y), smt_exprs)

                tracer.which_path(
                    node,
                    sql_conditions=self.resolve_sql_conditions(
                        [node.condition], node.schema()
                    ),
                    symbolic_exprs=[smt_condition.not_()],
                    takens=[3],
                    branch=True,
                    rows=row.rowid,
                )

    def handle_innerjoin(self, node: LogicalJoin, parent_stack: List[Any]) -> None:
        left_input = self.context[node.left]
        right_input = self.context[node.right]
        with self.encoder.scope(node) as tracer:
            for lrow in left_input:
                for rrow in right_input:
                    row = lrow + rrow
                    encoder = ExpressionEncoder()
                    local_context = encoder.encode(
                        node.condition, row=row, schema=node.schema()
                    )
                    smt_expr = local_context[node.condition]
                    try:
                        branch = smt_expr.concrete is True
                        if smt_expr:
                            self.append_to_context(node, row)
                    except NullValueError:
                        branch = False
                    sql_conditions = self.resolve_sql_conditions(
                        local_context.get("sql_conditions", []), node.schema()
                    )
                    smt_conditions = local_context.get("smt_conditions", [])
                    takens = [2 if b.concrete is True else 3 for b in smt_conditions]
                    tracer.which_path(
                        operator=node,
                        sql_conditions=sql_conditions,
                        symbolic_exprs=smt_conditions,
                        takens=takens,
                        branch=branch,
                        rowids=row.rowid,
                    )

    def handle_rightjoin(self, node: LogicalJoin, parent_stack: List[Any]) -> None: ...
    def handle_naturaljoin(
        self, node: LogicalJoin, parent_stack: List[Any]
    ) -> None: ...

    def handle(self, node: LogicalJoin, parent_stack: List[Any]) -> None:
        if node.join_type.lower() == "inner":
            self.handle_innerjoin(node, parent_stack)
        elif node.join_type.lower() == "left":
            self.handle_leftjoin(node, parent_stack)


class AggregateEncoder(OperatorEncoder):
    def handle(self, node: LogicalAggregate, parent_stack: List[Any]) -> None:
        input_data = self.context[node.this]
        input_schema = node.this.schema()
        ### implement group by
        sql_conditions, takens = [], []
        for key in node.keys + node.aggs:
            sql_conditions.append(key.transform(resolve_schema, input_schema))
            takens.append(PBit.GROUP_SIZE)

        # group_count_expr = None
        # for agg_func in node.aggs:
        #     func = key.transform(resolve_schema, input_schema)
        #     sql_conditions.append(func)
        #     if agg_func.key == "count":
        #         group_count_expr = func

        groups = {}
        for row in input_data:
            group_key = tuple(row[expr.ref] for expr in node.keys)
            groups.setdefault(group_key, []).append(row)

        with self.encoder.scope(node) as tracer:
            for gid, (group_key, rows) in enumerate(groups.items()):
                row = [] + list(group_key)
                rowids = sum([row.rowid for row in rows], ())
                smt_conditions = [] + list(group_key)
                for func_index, agg_func in enumerate(node.aggs):
                    ref = (
                        func_index + len(node.keys)
                        if isinstance(agg_func.this, sqlglot_exp.Star)
                        else agg_func.this.ref
                    )

                    if not isinstance(agg_func, sqlglot_exp.AggFunc):
                        raise NotImplementedError(
                            f"Aggregate function {agg_func} not implemented yet."
                        )
                    values = [row[ref] for row in rows]
                    smt_conditions.append(Group(gid, *values))
                    if agg_func.key == "count":
                        count_values = [
                            ITE(
                                v.is_(None),
                                Const(0, dtype="INT"),
                                Const(1, dtype="INT"),
                            )
                            for v in values
                        ]
                        count_value = sum(count_values)
                        row.append(count_value)
                    elif agg_func.key == "sum":
                        sum_value = sum(values)
                        row.append(sum_value)
                    elif agg_func.key == "min":
                        min_value = min([v for v in values if v.concrete is not None])
                        row.append(min_value)
                    elif agg_func.key == "max":
                        concretes = [v for v in values if v.concrete is not None]
                        max_value = (
                            max(concretes)
                            if concretes
                            else Const(None, dtype=row[ref].args.get("datatype"))
                        )
                        row.append(max_value)
                    elif agg_func.key == "avg":
                        valid_values = [v for v in values if v.concrete is not None]
                        if valid_values:
                            avg_value = sum(valid_values) / len(valid_values)
                        else:
                            avg_value = Const(None, dtype="REAL")
                        row.append(avg_value)
                    else:
                        raise NotImplementedError(
                            f"Aggregate function {agg_func} not implemented yet."
                        )

                self.append_to_context(node, Row(rowids, *row))
                tracer.which_path(
                    operator=node,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=takens,
                    branch=True,
                    rowids=rowids,
                    group=rows,
                )


class SortEncoder(OperatorEncoder):
    def handle(self, node: LogicalSort, parent_stack: List[Any]) -> None:
        input_data = self.context[node.this]
        input_schema = node.this.schema()

        def sort_key(row):
            # Returns a tuple of concrete values for Python's native sort
            # We handle None explicitly for consistent ordering (None usually lasts in SQL)
            return tuple(
                (
                    (0, row[col.ref].concrete)
                    if row[col.ref].concrete is not None
                    else (1, None)
                )
                for col in node.sorts
            )

        # Use Python's Timsort (Stable)
        is_reverse = "DESCENDING" in node.dirs
        sorted_data = sorted(input_data, key=sort_key, reverse=is_reverse)

        sql_conditions = [input_schema.columns[column.ref] for column in node.sorts]

        with self.encoder.scope(node) as tracer:
            for row in sorted_data:
                smt_conditions = [row[column.ref] for column in node.sorts]
                self.append_to_context(node, row)
                tracer.which_path(
                    operator=node,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=[True] * len(smt_conditions),
                    branch=True,
                    rowids=row.rowid,
                )


class HavingEncoder(OperatorEncoder):
    def handle(self, node: LogicalHaving, parent_stack: List[Any]) -> None:
        pass


class UnionEncoder(OperatorEncoder):
    def handle(self, node: LogicalUnion, parent_stack: List[Any]) -> None:
        pass


from .context import Context

GLOBAL_SYMBOLIC_REGISTRY = SymbolicRegistry()


class ExpressionEncoder:
    DATETIME_FMT = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m"]
    TRANSFORMS = {}

    def encode(self, expr: Expression, **kwargs):
        context = Context(**kwargs)
        parent_stack = []
        self._visit(expr, parent_stack, context=context)
        return context

    def _visit(self, expr, parent_stack, context):
        if expr is None:
            return None
        parent_stack = parent_stack or []
        if expr in context:
            return context[expr]
        expr_key = expr.key if not isinstance(expr, FunctionCall) else str(expr.this)
        registry = GLOBAL_SYMBOLIC_REGISTRY
        if expr_key in self.TRANSFORMS:
            context[expr] = self.TRANSFORMS[expr_key](expr, parent_stack, context)
        elif registry.has_handler(expr_key):
            handler = registry.get_handlers(expr_key)
            is_branch = registry.is_branch(expr_key)
            with context(is_branch) as track:
                try:
                    args = tuple(
                        self._visit(e, parent_stack + [e], context=context)
                        for e in expr.iter_expressions()
                        if not isinstance(e, DataType)
                    )
                    smt_expr = handler(*args)
                    context[expr] = track(expr, smt_expr)
                    return context[expr]
                except KeyError as e:
                    raise NotImplementedError(
                        f"Predicate {expr.key} not supported yet.{e}"
                    )
        else:
            handler = getattr(self, f"visit_{expr_key}", self.generic_visit)
            context[expr] = handler(expr, parent_stack, context)
            return context[expr]

    def generic_visit(self, expr, parent_stack, context):
        raise NotImplementedError(f"No visit_{expr.key} method defined")

    def visit_columnref(self, expr: ColumnRef, parent_stack, context: Context):
        smt_expr = context["row"][expr.ref] if "row" in context else context[expr]
        if not context.in_predicates():
            # self.in_predicates(context):
            # schema = context.get("schema")
            # if schema:
            #     expr = expr.transform(resolve_schema, schema)
            context.setdefault("sql_conditions", []).append(expr)
            context.setdefault("smt_conditions", []).append(smt_expr)
        return smt_expr

    def visit_literal(self, expr: sqlglot_exp.Literal, parent_stack, context):
        """We should convert the literal value to the appropriate type const based on its datatype."""
        value = expr.this
        datatype = expr.args.get("datatype")

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
            value = None
        return Const(value, dtype=datatype)

    def visit_cast(self, expr: sqlglot_exp.Cast, parent_stack, context):
        inner = self._visit(expr.this, parent_stack + [expr], context)
        to_type = expr.to
        if isinstance(expr.this, ColumnRef):
            context.setdefault("datatype", {})[expr.this] = to_type
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

    def visit_case(self, expr: sqlglot_exp.Case, parent_stack, context):
        for when in expr.args.get("ifs"):
            smt_expr = self._visit(when.this, parent_stack + [expr], context)
            if smt_expr:
                return self._visit(
                    when.args.get("true"), parent_stack + [expr], context
                )
        return self._visit(expr.args.get("default"), parent_stack + [expr], context)


# class ExpressionEncoder:
#     _symbolic_registry: SymbolicRegistry
#     DATETIME_FMT = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y-%m"]

#     def __init__(
#         self,
#         expr: Expression,
#         row: Row,
#         symbolic_registry: Optional[SymbolicRegistry] = None,
#     ):
#         self.expr = expr
#         self.row = row
#         self._symbolic_registry = symbolic_registry or SymbolicRegistry()

#     def encode(self, **kwargs) -> Tuple[Symbol, Dict[str, Any]]:
#         context = {**kwargs}
#         result = self._visit(self.expr, context=context)
#         return result, context

#     def _visit(self, expr, parent_stack=None, context=None):
#         if expr is None:
#             return None
#         parent_stack = parent_stack or []
#         context = context if context is not None else {}
#         expr_key = expr.key if not isinstance(expr, FunctionCall) else str(expr.this)

#         if self._symbolic_registry.has_handler(expr_key):
#             handler = self._symbolic_registry.get_handlers(expr_key)
#             is_branch = self._symbolic_registry.is_branch(expr_key)
#             with self.predicate_scope(context=context, is_branch=is_branch) as track:
#                 try:
#                     args = tuple(
#                         self._visit(e, parent_stack + [e], context=context)
#                         for e in expr.iter_expressions()
#                         if not isinstance(e, DataType)
#                     )
#                     smt_expr = handler(*args)
#                     return track(expr, smt_expr)
#                 except KeyError as e:
#                     raise NotImplementedError(
#                         f"Predicate {expr.key} not supported yet.{e}"
#                     )
#         else:
#             handler = getattr(self, f"visit_{expr.key}", self.generic_visit)
#             result = handler(expr, parent_stack, context)
#             return result

#     def generic_visit(self, expr, parent_stack, context):
#         raise NotImplementedError(f"No visit_{expr.key} method defined")

#     def visit_columnref(self, expr: ColumnRef, parent_stack=None, context=None):
#         smt_expr = self.row[expr.ref]
#         if not self.in_predicates(context):
#             schema = context.get("schema")
#             if schema:
#                 expr = expr.transform(resolve_schema, schema)
#             context.setdefault("sql_conditions", []).append(expr)
#             context.setdefault("smt_conditions", []).append(smt_expr)
#         return smt_expr

#     def visit_literal(self, expr: sqlglot_exp.Literal, parent_stack, context):
#         """We should convert the literal value to the appropriate type const based on its datatype."""
#         value = expr.this
#         datatype = expr.args.get("datatype")

#         try:
#             if datatype.is_type(*DataType.INTEGER_TYPES):
#                 value = int(value)
#             elif datatype.is_type(*DataType.REAL_TYPES):
#                 value = float(value)
#             elif datatype.is_type(DataType.Type.BOOLEAN):
#                 value = bool(value)
#             elif datatype.is_type(*DataType.TEMPORAL_TYPES):
#                 for fmt in self.DATETIME_FMT:
#                     try:
#                         value = datetime.strptime(value, fmt)
#                     except ValueError:
#                         continue
#             elif datatype.is_type(*DataType.TEXT_TYPES):
#                 value = str(value)
#         except Exception as e:
#             value = None
#         return Const(value, dtype=datatype)

#     def visit_not(self, expr: sqlglot_exp.Not, parent_stack=None, context=None):
#         this = self._visit(expr.this, parent_stack + [expr], context)
#         return this.not_()

#     def visit_cast(self, expr: sqlglot_exp.Cast, parent_stack, context):
#         inner = self._visit(expr.this, parent_stack + [expr], context)
#         to_type = expr.to
#         if isinstance(expr.this, ColumnRef):
#             context.setdefault("datatype", {})[expr.this] = to_type
#         concrete = inner.concrete
#         try:
#             if to_type.is_type(*DataType.TEMPORAL_TYPES):
#                 concrete = date_parser.parse(inner.concrete)
#         except Exception as e:
#             concrete = None
#         try:
#             args = (a for a in inner.args)
#         except Exception as e:
#             raise e
#         return inner.__class__(
#             *args, dtype=expr.to, concrete=concrete, **inner.metadata
#         )

#     def visit_case(self, expr: sqlglot_exp.Case, parent_stack, context):
#         for when in expr.args.get("ifs"):
#             smt_expr = self._visit(when.this, parent_stack + [expr], context)
#             if smt_expr:
#                 return self._visit(
#                     when.args.get("true"), parent_stack + [expr], context
#                 )
#         return self._visit(expr.args.get("default"), parent_stack + [expr], context)

#     def visit_logicalcorrelate(self, expr, parent_stack, context):
#         sub_ctx = {
#             "ref_conditions": [],
#             "sql_conditions": [],
#             "smt_conditions": [],
#             "parent": expr,
#         }
#         results = []

#         results.append(
#             self.plan_encoder.visit(expr.this, parent_stack + [expr], sub_ctx)
#         )
#         # for child in expr.expressions:
#         #     results.append(self.visit(child, parent_stack + [expr], sub_ctx))
#         #     sqlglot_exp.Subquery
#         context.setdefault("ref_conditions", []).extend(
#             sub_ctx.get("ref_conditions", [])
#         )
#         context.setdefault("smt_conditions", []).extend(
#             sub_ctx.get("smt_conditions", [])
#         )
#         # Merge subquery predicates into main context
#         # context["predicates"].extend(sub_ctx["predicates"])
#         # context["columns"].extend(sub_ctx["columns"])
#         # context["smt_constraints"].extend(sub_ctx["smt_constraints"])
#         logging.info(f"Subquery results: {results}")
#         return results[0]

#     def in_predicates(self, context):
#         return bool(context.get("_predicate_stack", []))

#     @contextmanager
#     def predicate_scope(self, context, is_branch: bool):
#         """
#         Context manager to track predicates.
#         If `is_branch` is False, no stack is maintained and `track` is a no-op.
#         """
#         if is_branch:
#             context.setdefault("_predicate_stack", []).append(True)
#             try:

#                 def track(expr: Expression, smt_expr):
#                     schema = context.get("schema")
#                     if schema:
#                         expr = expr.transform(resolve_schema, schema)
#                     context.setdefault("sql_conditions", []).append(expr)
#                     context.setdefault("smt_conditions", []).append(smt_expr)
#                     return smt_expr

#                 yield track
#             finally:
#                 context["_predicate_stack"].pop()
#         else:

#             def track(expr, smt_expr):
#                 return smt_expr

#             yield track


class PlanEncoder:
    OPERATOR_TRANSFORMS = {
        "Scan": ScanEncoder,
        "Project": ProjectEncoder,
        "Filter": FilterEncoder,
        "Join": JoinEncoder,
        "Aggregate": AggregateEncoder,
        "Sort": SortEncoder,
        "Having": HavingEncoder,
        "Union": UnionEncoder,
    }

    def __init__(
        self,
        plan: LogicalOperator,
        instance: Instance,
        trace: UExprToConstraint,
        verbose: bool = True,
    ):
        self.plan = plan
        self.instance = instance
        self.trace = trace
        self.verbose = verbose

    @contextmanager
    def scope(self, node: LogicalOperator):
        """
        Public facade that yields the context manager.
        """
        from src.parseval.uexpr import _ScopeManager

        manager = _ScopeManager(self.trace, node)
        with manager as trace:
            yield trace

    def encode(self):
        assert self.plan is not None, "Plan cannot be None"
        self.context = {}
        parent_stack = []

        for child in reversed(list(self.plan.find_all(LogicalOperator))):
            handler = self.OPERATOR_TRANSFORMS[child.operator_type](self)
            if handler:
                handler.handle(child, parent_stack)
            else:
                self._generic_handle(child)
            if self.context.get(child) is None:
                logger.warning(
                    f"Warning: No context found for child node {child.operator_type} with ID {child.operator_id}"
                )
                return

    def _visit(self, node, parent_stack=None):
        """
        The Visitor Loop.
        1. Recurse down.
        2. Check for Early Stop.
        3. Dispatch to Handler.
        """
        parent_stack = parent_stack or []

        for child in reversed(list(node.find_all(LogicalOperator))):

            # for child in node.find_all(LogicalOperator, bfs=False):
            logger.info(
                f"Current node: {node.operator_id}, Visiting child node: {child.operator_type} with ID {child.operator_id}"
            )
            handler = self.OPERATOR_TRANSFORMS[child.operator_type](self)
            if handler:
                handler.handle(child, parent_stack)
            else:
                self._generic_handle(child)
            if self.context.get(child) is None:
                logger.warning(
                    f"Warning: No context found for child node {child.operator_type} with ID {child.operator_id}"
                )
                return
        # for child in node.children:

        #     #     if child in parent_stack:
        #     #         continue
        #     #     if child is node:
        #     #         continue
        #     self._visit(child, parent_stack + [node])
        #     child_data = self.context.get(child)
        #     if not child_data:
        #         return

        # handler = self.OPERATOR_TRANSFORMS[node.operator_type](self)
        # if handler:
        #     handler.handle(node, parent_stack)
        # else:
        #     self._generic_handle(node)

    def _generic_handle(self, node):
        raise NotImplementedError(f"No handler registered for {type(node)}")
