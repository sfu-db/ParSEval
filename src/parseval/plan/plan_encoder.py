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

logger = logging.getLogger("parseval.coverage")


class OperatorEncoder(ABC):
    def __init__(self, encoder: PlanEncoder):
        self.encoder = encoder

    @property
    def context(self) -> Dict[Any, Any]:
        return self.encoder.context

    def append_to_context(self, node: Any, value: Any) -> None:
        self.context.setdefault(node, []).append(value)

    @property
    def instance(self):
        return self.encoder.instance

    def resolve_sql_conditions(self, sql_conditions: List[Expression], schema: Schema):

        def resolve_schema(expr, input_schema: Schema):
            if isinstance(expr, ColumnRef):
                return input_schema.columns[expr.ref]
            return expr

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
            if table_ref.is_unique(column.name):
                schema_refs[index].set("unique", True)
                sql_conditions.append(schema_refs[index])
        logger.info(f"sql conditions: {sql_conditions}")
        with self.encoder.scope(node) as tracer:
            if not rows:
                tracer.which_path(
                    node,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=[],
                    takens=[PBit.TRUE] * len(sql_conditions),
                    branch=True,
                    rowids=(),
                    **self.context["metadata"][node.operator_id],
                )

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
                    **self.context["metadata"][node.operator_id],
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
                        1 if isinstance(sql, ColumnRef) else bool(smt.concrete)
                        for smt, sql in zip(smt_conditions, sql_conditions)
                    ],
                    branch=True,
                    rowids=row.rowid,
                    **self.context["metadata"][node.operator_id],
                )


class FilterEncoder(OperatorEncoder):
    def get_subqueryid(self):
        pass

    def handle_fieldaccess(self, node: LogicalFilter, parent_stack: List[Any]) -> None:
        fieldaccesses = list(node.condition.find_all(FieldAccess))
        if not fieldaccesses:
            return node.condition

        new_conditions = []
        for fa in fieldaccesses:
            cor_name = fa.name
            input_data = self.context["variableset"][cor_name]
            for row in input_data:
                concrete = row[fa.column].concrete
                datatype = row[fa.column].datatype
                logger.info(
                    f'"FieldAccess subquery output {row}: {concrete} of type {datatype}"'
                )
                literal = convert_to_literal(concrete, datatype)
                new_condition = node.condition.copy()
                new_condition.find(FieldAccess).replace(literal)
                new_conditions.append(new_condition)

        new_condition = reduce(
            lambda x, y: sqlglot_exp.Or(this=x, expression=y), new_conditions
        )
        input_data = self.context[node.this]
        input_schema = node.this.schema()
        with self.encoder.scope(node) as tracer:
            for row in input_data:
                encoder = ExpressionEncoder()
                local_context = encoder.encode(
                    new_condition,
                    row=row,
                    schema=input_schema,
                    variableset=self.context.get("variableset", {}),
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
                    **self.context["metadata"][node.operator_id],
                )

    def handle_exists(self, node: LogicalFilter, parent_stack: List[Any]):
        exists_out = self.context[node.condition.this]
        input_data = self.context[node.this]

        if exists_out:

            for row in input_data:
                self.append_to_context(node, row)

        # with self.encoder.scope(node) as tracer:
        #     for row in input_data:
        #         logger.info(f"processing {row.rowid}")
        #         encoder = ExpressionEncoder()
        #         local_context = encoder.encode(
        #             new_condition,
        #             row=row,
        #             schema=input_schema,
        #             variableset=self.context.get("variableset", {}),
        #         )
        #         smt_expr = local_context[new_condition]
        #         smt_conditions = local_context.get("smt_conditions", [])
        #         sql_conditions = local_context.get("sql_conditions", [])
        #         try:
        #             if smt_expr:
        #                 self.append_to_context(node, row)
        #         except NullValueError:
        #             ...
        #         takens = [
        #             (b.concrete if b.concrete is not None else 0)
        #             for b in smt_conditions
        #         ]
        #         sql_conditions = self.resolve_sql_conditions(
        #             sql_conditions, input_schema
        #         )

        #         tracer.which_path(
        #             operator=node,
        #             sql_conditions=sql_conditions,
        #             symbolic_exprs=smt_conditions,
        #             takens=takens,
        #             branch=smt_expr.concrete,
        #             rowids=row.rowid,
        #             **self.context["metadata"][node.operator_id],
        #         )

    def handle(self, node: LogicalFilter, parent_stack: List[Any]) -> None:
        if isinstance(node.condition, sqlglot_exp.Exists):
            self.handle_exists(node, parent_stack)
            return

        fa = list(node.condition.find_all(FieldAccess))
        if fa:
            self.handle_fieldaccess(node, parent_stack)
            return

        input_data = self.context[node.this]

        input_schema = node.this.schema()

        vset = node.args.get("variableset")
        vset = vset[1:-1] if vset else vset

        logger.info(f"filter variableset: {node.args.get('variableset')}")

        # variablesets = {vset: input_data}

        # self.context.setdefault('variableset', {})[vset] = input_data

        # if isinstance(node.condition, sqlglot_exp.Exists):
        #     logger.info(f'node exist this: {node.condition.this}')
        #     logger.info(self.context.keys())
        #     outs = self.context[node.condition.this]
        #     if outs:
        #         ...
        #     return

        scalar_queries = list(node.condition.find_all(ScalarQuery))
        new_condition = node.condition.copy()
        for scalar in scalar_queries:
            scalar_outs = self.context[scalar.this]
            literals = []
            for out in scalar_outs:
                concrete = out[0].concrete
                datatype = out[0].datatype
                logger.info(
                    f'"Scalar subquery output {out[0]}: {concrete} of type {datatype}"'
                )
                literal = convert_to_literal(concrete, datatype)
                literals.append(literal)
            fix_point = ScalarQuery(expressions=literals)
            new_condition.find(ScalarQuery).replace(fix_point)

        # logger.info(f'variable sets: {variablesets.keys()}')
        with self.encoder.scope(node) as tracer:
            for row in input_data:
                logger.info(f"processing {row.rowid}")
                encoder = ExpressionEncoder()
                local_context = encoder.encode(
                    new_condition,
                    row=row,
                    schema=input_schema,
                    variableset=self.context.get("variableset", {}),
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
                    **self.context["metadata"][node.operator_id],
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
                                **self.context["metadata"][node.operator_id],
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
                    **self.context["metadata"][node.operator_id],
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
                        **self.context["metadata"][node.operator_id],
                    )

    def handle_rightjoin(self, node: LogicalJoin, parent_stack: List[Any]) -> None: ...
    def handle_naturaljoin(self, node: LogicalJoin, parent_stack: List[Any]) -> None:
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
                        **self.context["metadata"][node.operator_id],
                    )

    def handle(self, node: LogicalJoin, parent_stack: List[Any]) -> None:
        if node.join_type.lower() == "inner":
            self.handle_innerjoin(node, parent_stack)
        elif node.join_type.lower() == "left":
            self.handle_leftjoin(node, parent_stack)


class AggregateEncoder(OperatorEncoder):
    TRANSFORMS = {
        "count": lambda self, *args: self.handle_count(*args),
        "sum": lambda self, *args: self.handle_sum(*args),
        "min": lambda self, *args: self.handle_min(*args),
        "max": lambda self, *args: self.handle_max(*args),
        "avg": lambda self, *args: self.handle_avg(*args),
    }

    def handle_count(self, values: List[Symbol]):
        cnt = 0
        for value in values:
            if value.concrete is not None:
                cnt += 1
        return Const(cnt, dtype="INT")

    def handle(self, node: LogicalAggregate, parent_stack: List[Any]) -> None:
        input_data = self.context[node.this]
        input_schema = node.this.schema()
        ### implement group by
        sql_conditions, takens = [], []
        for key in node.keys + node.aggs:
            sql_conditions.append(key.transform(resolve_schema, input_schema))
            takens.append(PBit.GROUP_SIZE)

        groups = {}
        for row in input_data:
            group_key = tuple(row[expr.ref] for expr in node.keys)
            concrete_group_key = tuple(v.concrete for v in group_key)
            if concrete_group_key not in groups:
                groups[concrete_group_key] = {"group_key": group_key, "rows": []}
            groups[concrete_group_key]["rows"].append(row)

        with self.encoder.scope(node) as tracer:

            for concrete_group_key, group_info in groups.items():
                group_key = group_info["group_key"]
                new_row = [] + list(group_key)
                rows = group_info["rows"]
                rowids = sum([row.rowid for row in rows], ())
                g = Group({c: k for c, k in zip(sql_conditions, group_key)}, rowids)
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

                    g.extend(values)
                    concretes = [v for v in values if v.concrete is not None]
                    # smt_conditions.append(Group(group_key, *values))
                    if agg_func.key == "count":
                        if agg_func.args.get("distinct"):
                            count = len(set(concretes))
                        else:
                            count = len(concretes)
                        count_value = Const(count, dtype="INT")
                        new_row.append(count_value)
                    elif agg_func.key == "sum":
                        sum_value = (
                            sum(concretes) if concretes else Const(0, dtype="INT")
                        )
                        # logger.info(
                        #     f"SUM concretes: {concretes}, sum_value: {sum_value}"
                        # )
                        new_row.append(sum_value)
                    elif agg_func.key == "min":
                        min_value = (
                            min(concretes)
                            if concretes
                            else Const(None, dtype=values[0].datatype)
                        )
                        new_row.append(min_value)
                    elif agg_func.key == "max":
                        max_value = (
                            max(concretes)
                            if concretes
                            else Const(None, dtype=values[0].datatype)
                        )
                        new_row.append(max_value)
                    elif agg_func.key == "avg":
                        if concretes:
                            avg_value = sum(concretes) / len(concretes)
                        else:
                            avg_value = Const(None, dtype="REAL")
                        new_row.append(avg_value)
                    else:
                        raise NotImplementedError(
                            f"Aggregate function {agg_func} not implemented yet."
                        )
                smt_conditions = [g] * len(sql_conditions)

                self.append_to_context(node, Row(rowids, *new_row))
                tracer.which_path(
                    operator=node,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=takens,
                    branch=True,
                    rowids=rowids,
                    group=rows,
                    **self.context["metadata"][node.operator_id],
                )


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


class SortEncoder(OperatorEncoder):
    def handle(self, node: LogicalSort, parent_stack: List[Any]) -> None:
        input_data = self.context[node.this]
        input_schema = node.this.schema()
        is_reverse = "DESCENDING" in node.dirs

        def sort_key(row):
            key = []
            for col, direction in zip(node.sorts, node.dirs):
                v = row[col.ref].concrete
                if v is None:
                    key.append((1, None))
                else:
                    key.append((0, SortValue(v, is_reverse)))

            return tuple(key)

        sorted_data = sorted(input_data, key=sort_key)

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
                    **self.context["metadata"][node.operator_id],
                )


class HavingEncoder(OperatorEncoder):
    def handle(self, node: LogicalHaving, parent_stack: List[Any]) -> None:
        input_data = self.context[node.this]
        input_schema = node.this.schema()
        scalar_queries = list(node.condition.find_all(ScalarQuery))
        new_condition = node.condition.copy()
        for scalar in scalar_queries:
            scalar_outs = self.context[scalar.this]
            literals = []
            for out in scalar_outs:
                concrete = out[0].concrete
                datatype = out[0].datatype
                literal = convert_to_literal(concrete, datatype)
                literals.append(literal)
            fix_point = ScalarQuery(expressions=literals)
            new_condition.find(ScalarQuery).replace(fix_point)

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
                    (PBit.HAVING_TRUE if b.concrete is True else PBit.HAVING_FALSE)
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
                    **self.context["metadata"][node.operator_id],
                )


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
        logger.info(f"Start encoding expression: {expr}")
        logger.info(f"Initial context keys: {list(context.keys())}")
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
                    raise e
                    raise NotImplementedError(
                        f"Predicate {expr.key} not supported yet.{e}"
                    )
        else:
            handler = getattr(self, f"visit_{expr_key}", self.generic_visit)
            context[expr] = handler(expr, parent_stack, context)
            return context[expr]

    def generic_visit(self, expr, parent_stack, context):
        raise NotImplementedError(f"No visit_{expr.key} method defined")

    def visit_fieldaccess(self, expr: FieldAccess, parent_stack, context):
        logger.info(f"Visiting FieldAccess: {expr}, {expr.parent}")
        logger.info(f"Context keys: {list(context.keys())}")
        logger.info(f"Variablesets: {context.get('variableset').keys()}")
        col_name = expr.name
        input_data = context["variableset"][col_name]
        values = [row[expr.column] for row in input_data]
        return values

    def visit_columnref(self, expr: ColumnRef, parent_stack, context: Context):

        smt_expr = context["row"][expr.ref] if "row" in context else context[expr]
        if not context.in_predicates():
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
            # raise e
        return Const(value, dtype=datatype)

    def visit_exists(self, expr: sqlglot_exp.Exist, parent_stack, context):
        subquery = expr.this
        values = self._visit(subquery, parent_stack + [expr], context)
        exists = any(v.concrete is not None for v in values)
        return Const(exists, dtype="BOOLEAN")

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

    def visit_scalarquery(self, expr: ScalarQuery, parent_stack, context):
        for expression in expr.expressions:
            self._visit(expression, parent_stack + [expr], context)
        context[expr] = context[expr.expressions[0]]
        return context[expr]
        # values = self._visit()
        # return self._visit(expr.this, parent_stack + [expr], context)


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

    def is_subquery_root(self, node: LogicalOperator) -> bool:
        return node.parent and not isinstance(node.parent, LogicalOperator)

    def topo(self, node: LogicalOperator) -> List[LogicalOperator]:
        degree = {}
        visited = set()
        correlated = {}

        queue = deque(node)

        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            if isinstance(current, LogicalFilter):
                vsets = current.args.get("variableset", [])
                for vs in vsets:
                    correlated[vs] = current.operator_id
                for cor in node.condition.find_all(FieldAccess):
                    parent = cor.find_ancestor(LogicalOperator)
                    degree[parent.operator_id] = degree.get(parent.operator_id, 0) + 1
                    degree[correlated[cor.name]] = (
                        degree.get(correlated[cor.name], 0) + 1
                    )

            for child in current.children:
                degree[child.operator_id] = degree.get(child.operator_id, 0) + 1
                queue.append(child)
        return result

    def is_visited(
        self,
        node: LogicalOperator,
        visited: Set[LogicalOperator],
        correlated: Dict[str, LogicalOperator],
    ) -> bool:

        for c in node.children:
            if c not in visited:
                return False
        if isinstance(node, LogicalFilter):
            for sub in node.condition.find_all(LogicalOperator):
                if sub not in visited:
                    return False
            for cor in node.condition.find_all(FieldAccess):
                if cor.name not in correlated:
                    return False
                if correlated[cor.name] not in visited:
                    return False
        return True

    def dependencies(
        self, node: LogicalOperator, correlated: Dict[str, LogicalOperator]
    ) -> List[LogicalOperator]:
        field_accesses = {}
        variablesets = {}

        deps = set()
        for c in node.children:
            deps.add(c)
        # for cor in dpe
        variablesets = []
        if isinstance(node, LogicalFilter):
            # feildaccess = node.condition.find_all(FieldAccess)
            for cor in node.condition.find_all(FieldAccess):
                parent = cor.find_ancestor(LogicalOperator)
                deps.add(parent)

            vsets = node.args.get("variableset", [])
            for vs in vsets:
                correlated[vs] = node
        return list(deps)

    def encode(self):
        assert self.plan is not None, "Plan cannot be None"
        self.context = {"metadata": {}, "variableset": {}}
        parent_stack = []

        operators = list(self.plan.find_all(LogicalOperator))
        subquery_ids = {}
        for operator in operators:
            if self.is_subquery_root(operator):
                subquery_ids[operator.operator_id] = operator.operator_id
            elif operator.parent:
                subquery_ids[operator.operator_id] = subquery_ids[
                    operator.parent.operator_id
                ]
            else:
                subquery_ids[operator.operator_id] = self.plan.operator_id
        for opid, subid in subquery_ids.items():
            self.context["metadata"].setdefault(opid, {})["subquery"] = subid

        q = deque(operators)
        visited = set()
        correlated = {}

        while q:
            current = q.popleft()

            if isinstance(current, LogicalFilter):
                vsets = current.args.get("variableset", [])
                if vsets:
                    vsets = vsets[1:-1]
                correlated[vsets] = current.children[0]
            if current in visited:
                continue
            if not self.is_visited(current, visited, correlated):
                q.append(current)
                continue

            logger.info(f"start to process {current}")

            handler = self.OPERATOR_TRANSFORMS[current.operator_type](self)
            if handler:
                handler.handle(current, parent_stack)
            else:
                self._generic_handle(current)
            visited.add(current)
            if current.parent:
                vset = current.parent.args.get("variableset")
                if vset is not None and len(vset[1:-1]) > 0:
                    vset = vset[1:-1]
                    self.context.setdefault("variableset", {})[vset] = self.context.get(
                        current
                    )
            logger.info(f"output from {current}: {self.context.get(current)}")
            if self.context.get(
                current
            ) is None and current is not current.parent.args.get("expression"):
                return

        # for child in reversed(operators):
        #     handler = self.OPERATOR_TRANSFORMS[child.operator_type](self)
        #     if handler:
        #         handler.handle(child, parent_stack)
        #     else:
        #         self._generic_handle(child)
        #     if self.context.get(child) is None and child is not child.parent.args.get(
        #         "expression"
        #     ):
        #         return

    # def _visit(self, node, parent_stack=None):
    #     """
    #     The Visitor Loop.
    #     1. Recurse down.
    #     2. Check for Early Stop.
    #     3. Dispatch to Handler.
    #     """
    #     parent_stack = parent_stack or []
    #     for child in reversed(list(node.find_all(LogicalOperator))):
    #         logger.info(f"Visiting Node: {child.parent}")

    #         handler = self.OPERATOR_TRANSFORMS[child.operator_type](self)
    #         if handler:
    #             handler.handle(child, parent_stack)
    #         else:
    #             self._generic_handle(child)
    #         if self.context.get(child) is None and child is not node.args.get(
    #             "expression"
    #         ):
    #             return

    def _generic_handle(self, node):
        raise NotImplementedError(f"No handler registered for {type(node)}")
