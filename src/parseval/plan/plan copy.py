from __future__ import annotations
from typing import List, Optional, Union, Dict, TYPE_CHECKING
from src.parseval.states import SchemaException, ParSEvalError, SyntaxException
from src.parseval.symbol import Row, Group, SymbolicRegistry, Const, NullValueError
import json, logging
from sqlglot.expressions import to_identifier
from contextlib import contextmanager

from .rex import *

if TYPE_CHECKING:
    from src.parseval.instance import Instance
    from src.parseval.uexpr import UExprToConstraint

logger = logging.getLogger("parseval.planner")


def to_columnref(
    name: str | sqlglot_exp.Identifier,
    datatype: str | DataType | Dict,
    index=None,
    **kwargs,
):
    if isinstance(name, ColumnRef):
        return name
    name = to_identifier(name)
    datatype = to_type(datatype)
    return ColumnRef(this=name, ref=index, datatype=datatype, **kwargs)


def to_type(type_def: str | DataType | dict) -> DataType:
    if isinstance(type_def, (DataType, str)):
        return DataType.build(type_def)
    elif isinstance(type_def, dict):
        if "name" in type_def:
            type_def["dtype"] = type_def.pop("name")
    else:
        raise SyntaxException(f"Invalid type definition: {type_def}")
    return DataType.build(**type_def)


def parse_literal(planner, **kwargs):
    value = kwargs.pop("value")
    datatype = to_type(kwargs.pop("type"))

    if datatype.is_type(*DataType.NUMERIC_TYPES):
        literal = sqlglot_exp.Literal.number(value)
    else:
        literal = sqlglot_exp.Literal.string(value)

    literal.set("datatype", datatype)
    return literal


def parse_case(planner, **kwargs) -> Expression:
    operands = kwargs.pop("operands")
    default = planner.walk(operands.pop())
    whens = []

    for index in range(0, len(operands), 2):
        when = planner.walk(operands[index])
        then = planner.walk(operands[index + 1])
        whens.append(sqlglot_exp.If(this=when, true=then))
    return sqlglot_exp.Case(ifs=whens, default=default)


def parse_scalary_query(planner, **kwargs) -> Expression:

    with open("tests/db/scalar_query.txt", "w") as f:
        import json

        json.dump(kwargs, f, indent=2)
        # f.write(str(kwargs))

    query = [planner.walk(q) for q in kwargs.pop("query")]

    subquery_type = kwargs.pop("operator")[1:].lower()

    return ScalarQuery(
        this=query[0],
        type=subquery_type,
        correlated=False,
        datatype=to_type(kwargs.pop("type")),
    )


# LogicalCorrelate(this=query[0], type=subquery_type, correlated=False)


class Planner:
    EXPRESSION_TRANSFORM = {
        "INPUT_REF": lambda self, **kwargs: to_columnref(
            kwargs.pop("name"), kwargs.pop("type"), index=kwargs.pop("index"), **kwargs
        ),
        "IS_NULL": lambda self, **kwargs: Is_Null(
            this=self.walk(kwargs.pop("operands").pop())
        ),
        "LITERAL": parse_literal,
        "CASE": parse_case,
        "SCALAR_QUERY": parse_scalary_query,
        "NOT": lambda self, **kwargs: negate_predicate(
            self.walk(kwargs.pop("operands").pop())
        ),
        "CAST": lambda self, **kwargs: sqlglot_exp.Cast(
            this=self.walk(kwargs.pop("operands").pop()),
            to=to_type(kwargs.pop("type")),
        ),
        # "STRFTIME": lambda self, **kwargs: Strftime(
        #     this=self.walk(kwargs["operands"].pop()),
        #     expressions=[self.walk(kwargs["operands"].pop())],
        #     datatype=to_type(kwargs.pop("type")),
        # ),
        "OTHER_FUNCTION": lambda self, **kwargs: FunctionCall(
            this=to_identifier(kwargs.get("operator")),
            expressions=[self.walk(op) for op in kwargs.get("operands", [])],
            datatype=to_type(kwargs.get("type")),
        ),
    }
    OPERATOR_TRANSFORM = {
        "LOGICALTABLESCAN": lambda self, **kwargs: self.to_scan(**kwargs),
        "LOGICALPROJECT": lambda self, **kwargs: self.to_project(**kwargs),
        "LOGICALFILTER": lambda self, **kwargs: self.to_filter(**kwargs),
        "LOGICALJOIN": lambda self, **kwargs: self.to_join(**kwargs),
        "LOGICALAGGREGATE": lambda self, **kwargs: self.to_aggregate(**kwargs),
        "LOGICALSORT": lambda self, **kwargs: self.to_sort(**kwargs),
        # "LogicalUnion": lambda self, **kwargs: LogicalUnion(
    }

    def __init__(self):
        self.dispatches = {
            "relOp": self.OPERATOR_TRANSFORM,
            "kind": self.EXPRESSION_TRANSFORM,
            "operator": self.EXPRESSION_TRANSFORM,
        }

    def explain(self, schema: str, sql: str, dialect: str = "sqlite"):
        from src.parseval.calcite import get_logical_plan

        res = get_logical_plan(ddls=schema, queries=[sql], dialect=dialect)
        src = json.loads(res)[0]
        if src["state"] != "SUCCESS":
            raise SchemaException(f"Failed to get logical plan: {src['error']}")
        src = json.loads(src["plan"])
        return self.walk(src)

    def walk(self, node):
        for key, transforms in self.dispatches.items():
            if key in node:
                identifier = node.get(key).upper()
                if identifier in transforms:
                    return transforms[identifier](self, **node)
        raise SyntaxException(f"Cannot find relOp or kind/operator in node: {node}")

    def to_scan(self, **kwargs):
        this = to_identifier(kwargs.pop("table"))
        operator_id = kwargs.pop("id")
        columns = [
            self.walk(
                {
                    "kind": "INPUT_REF",
                    "index": index,
                    **col,
                    "table": this.name,
                    "alias": operator_id,
                }
            )
            for index, col in enumerate(kwargs.pop("columns", []))
        ]
        return LogicalScan(this=this, operator_id=operator_id, columns=columns)

    def to_project(self, **kwargs):
        child = self.walk(kwargs.pop("inputs")[0])
        expressions = [self.walk(proj) for proj in kwargs.pop("project", [])]
        operator_id = kwargs.pop("id")
        return LogicalProject(
            this=child, expressions=expressions, operator_id=operator_id
        )

    def to_filter(self, **kwargs):
        child = self.walk(kwargs.pop("inputs")[0])
        condition = self.walk(kwargs.pop("condition"))
        operator_id = kwargs.pop("id")
        klass = (
            LogicalFilter if not isinstance(child, LogicalAggregate) else LogicalHaving
        )
        return klass(this=child, condition=condition, operator_id=operator_id)

    def to_join(self, **kwargs):
        children = [self.walk(child) for child in kwargs.pop("inputs")]
        condition = self.walk(kwargs.pop("condition"))
        join_type = kwargs.pop("joinType", "INNER").upper()
        return LogicalJoin(
            this=children[0],
            expression=children[1],
            join_type=join_type,
            condition=condition,
            operator_id=kwargs.pop("id"),
        )

    def to_aggregate(self, **kwargs):
        child = self.walk(kwargs.pop("inputs")[0])
        groupby = []
        for gid, key in enumerate(kwargs.pop("keys")):
            groupby.append(
                to_columnref(
                    name=f"${gid}", datatype=key.get("type"), index=key.get("column")
                )
            )

        aggs = kwargs.pop("aggs", [])
        agg_funcs = [self.walk(func_def) for func_def in aggs]
        return LogicalAggregate(this=child, expressions=groupby, aggs=agg_funcs)

    def to_sort(self, **kwargs):
        this = self.walk(kwargs["inputs"][0])
        sort = kwargs.get("sort", [])
        return LogicalSort(
            this=this,
            expressions=[
                to_columnref(
                    name=str(s["column"]), datatype=s["type"], index=s["column"]
                )
                for s in sort
            ],
            dirs=kwargs.pop("dir", []),
            offset=kwargs.pop("offset", 0),
            limit=kwargs.pop("limit", 1),
            operator_id=kwargs.pop("id"),
        )

    def to_union(self, **kwargs):
        children = [self.walk(child) for child in kwargs.pop("inputs")]
        return LogicalUnion(
            this=children[0], expression=children[1], operator_id=kwargs.pop("id")
        )


BINARY_OPERATORS = {
    "EQUALS": sqlglot_exp.EQ,
    "NOT_EQUALS": sqlglot_exp.NEQ,
    "GREATER_THAN": sqlglot_exp.GT,
    "LESS_THAN": sqlglot_exp.LT,
    "LESS_THAN_OR_EQUAL": sqlglot_exp.LTE,
    "GREATER_THAN_OR_EQUAL": sqlglot_exp.GTE,
    "LIKE": sqlglot_exp.Like,
    "AND": sqlglot_exp.And,
    "OR": sqlglot_exp.Or,
    "PLUS": sqlglot_exp.Add,
    "MINUS": sqlglot_exp.Sub,
    "TIMES": sqlglot_exp.Mul,
    "DIVIDE": sqlglot_exp.Div,
}

AGG_FUNCS = {
    "COUNT": sqlglot_exp.Count,
    "SUM": sqlglot_exp.Sum,
    "AVG": sqlglot_exp.Avg,
    "MAX": sqlglot_exp.Max,
    "MIN": sqlglot_exp.Min,
}


for func_name, func_class in AGG_FUNCS.items():
    Planner.EXPRESSION_TRANSFORM[func_name] = (
        lambda self, func_class=func_class, **kwargs: func_class(
            this=(
                to_columnref(
                    f'${kwargs["operands"][0]["column"]}',
                    kwargs["operands"][0].get("type"),
                    index=kwargs["operands"][0]["column"],
                )
                if kwargs.get("operands")
                else sqlglot_exp.Star()
            ),
            distinct=kwargs.get("distinct", False),
            ignorenulls=kwargs.get("ignorenulls", False),
            datatype=to_type(kwargs.get("type")),
        )
    )


for kind, op_class in BINARY_OPERATORS.items():
    Planner.EXPRESSION_TRANSFORM[kind] = (
        lambda self, op_class=op_class, **kwargs: reduce(
            lambda x, y: op_class(this=x, expression=y),
            [self.walk(operand) for operand in kwargs.pop("operands", [])],
        )
    )


for klass in [
    ColumnRef,
    Is_Null,
    Is_Not_Null,
    LogicalOperator,
    LogicalAggregate,
    LogicalJoin,
    LogicalFilter,
    LogicalProject,
    LogicalScan,
    LogicalSort,
    LogicalCorrelate,
    ScalarQuery,
]:
    generator.Generator.TRANSFORMS[klass] = lambda self, expression: expression.sql(
        dialect=self.dialect
    )


class ExpressionEncoder:
    def __init__(
        self,
        row: Row,
        ignore_nulls: bool = False,
        symbolic_registry: Optional[SymbolicRegistry] = None,
    ):
        self.row = row
        self.ignore_nulls = ignore_nulls
        self.symbolic_registry = symbolic_registry or SymbolicRegistry()

    def visit(self, expr, parent_stack=None, context=None):
        if expr is None:
            return None
        parent_stack = parent_stack or []
        context = context if context is not None else {}
        if self.symbolic_registry.has_handler(expr.key):
            handler = self.symbolic_registry.get_handlers(expr.key)
            is_branch = self.symbolic_registry.is_branch(expr.key)
            with self.predicate_scope(context=context, is_branch=is_branch) as track:
                try:
                    args = tuple(
                        self.visit(e, parent_stack + [e], context=context)
                        for e in expr.iter_expressions()
                        if not isinstance(e, DataType)
                    )
                    smt_expr = handler(*args)
                    return track(expr, smt_expr)
                except KeyError as e:
                    raise NotImplementedError(
                        f"Predicate {expr.key} not supported yet.{e}"
                    )
        else:
            handler = getattr(self, f"visit_{expr.key}", self.generic_visit)
            result = handler(expr, parent_stack, context)
            return result

    def generic_visit(self, expr, parent_stack, context):
        raise NotImplementedError(f"No visit_{expr.key} method defined")

    def visit_columnref(self, expr: ColumnRef, parent_stack, context):
        smt_expr = self.row[expr.ref]
        if not bool(context.get("_predicate_stack", [])):
            schema = context.get("schema", None)
            context.setdefault("sql_conditions", []).append(schema.columns[expr.ref])
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
                from datetime import datetime

                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        value = datetime.strptime(value, fmt)
                    except ValueError:
                        continue
            elif datatype.is_type(*DataType.TEXT_TYPES):
                value = str(value)
        except Exception as e:
            value = None
        return Const(value, dtype=datatype)

    def visit_not(self, expr: sqlglot_exp.Not, parent_stack, context):
        this = self.visit(
            expr.this, parent_stack=parent_stack + [expr], context=context
        )
        return this.not_()

    def visit_cast(self, expr: sqlglot_exp.Cast, parent_stack, context):
        inner = self.visit(expr.this, parent_stack + [expr], context)
        to_type = expr.to
        concrete = inner.concrete
        try:
            if to_type.is_type(*DataType.TEMPORAL_TYPES):
                from dateutil import parser as date_parser

                concrete = date_parser.parse(inner.concrete)
        except Exception as e:
            concrete = None
        try:
            args = (a for a in inner.args)
        except Exception as e:
            logging.info(expr)
            raise e
        return inner.__class__(
            *args, dtype=expr.to, concrete=concrete, **inner.metadata
        )

    def visit_case(self, expr: sqlglot_exp.Case, parent_stack, context):
        for when in expr.args.get("ifs"):
            smt_expr = self.visit(when.this, parent_stack + [expr], context)
            if smt_expr:
                return self.visit(when.args.get("true"), parent_stack + [expr], context)

        return self.visit(expr.args.get("default"), parent_stack + [expr], context)

    def in_predicates(self, context):
        return bool(context.get("_predicate_stack", []))

    @contextmanager
    def predicate_scope(self, context, is_branch: bool):
        """
        Context manager to track predicates.
        If `is_branch` is False, no stack is maintained and `track` is a no-op.
        """
        if is_branch:
            stack = context.setdefault("_predicate_stack", [])
            stack.append(True)
            try:

                def track(expr, smt_expr):
                    schema = context.get("schema", None)
                    if schema:
                        e = expr.transform(resolve_schema, schema)
                        context.setdefault("sql_conditions", []).append(e)
                    context.setdefault("smt_conditions", []).append(smt_expr)
                    return smt_expr

                yield track
            finally:
                stack.pop()
        else:

            def track(expr, smt_expr):
                return smt_expr

            yield track


class PlanEncoder:
    OPERATOR_TRANSFORMS = {}

    def __init__(
        self, instance: Instance, trace: UExprToConstraint, verbose: bool = True
    ):
        self.instance = instance
        self.trace = trace
        self.verbose = verbose

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
            f"Encoding not implemented for operator type: {expr.operator_type}"
        )

    def visit_scan(self, expr: LogicalScan, parent_stack, context):
        rows = self.instance.get_rows(expr.table_name)
        table = self.instance.catalog.get_table(expr.table_name)
        sql_conditions, schema_columns = [], []
        schema_refs = expr.schema().columns
        for tbl_col, schema_ref in zip(table.columns, schema_refs):
            colref = schema_ref.copy()
            if tbl_col.args.get("unique", False):
                colref.set("unique", True)
                sql_conditions.append(colref)
            schema_columns.append(colref)
        with self.trace.scope(expr):
            out = []
            for row in rows:
                ctx = dict(zip(schema_columns, row))
                out.append(ctx)
                symbolic_exprs = [row[columnref.ref] for columnref in sql_conditions]
                self.trace.which_path(
                    expr,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=symbolic_exprs,
                    takens=[True] * len(symbolic_exprs),
                    branch=True,
                    rows=[row],
                )
            return rows

    def visit_project(self, expr: LogicalProject, parent_stack, context):
        input_data = self.visit(expr.this, parent_stack + [expr], context)
        input_schema = expr.this.schema()
        out = []
        with self.trace.scope(expr):
            for row in input_data:
                data = []
                sql_conditions, smt_conditions = [], []
                for proj in expr.expressions:
                    local_context = {"schema": input_schema}
                    encoder = ExpressionEncoder(row, ignore_nulls=False)
                    data.append(encoder.visit(proj, context=local_context))
                    smt_conditions.extend(local_context.get("smt_conditions", []))
                    sql_conditions.extend(local_context.get("sql_conditions", []))

                self.trace.which_path(
                    operator=expr,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=[
                        True if isinstance(sql, ColumnRef) else smt.concrete
                        for smt, sql in zip(smt_conditions, sql_conditions)
                    ],
                    branch=True,
                    rows=[row],
                )
                out.append(Row(*data))
            return out

    def visit_filter(self, expr: LogicalFilter, parent_stack, context):
        input_schema = expr.this.schema()
        input_data = self.visit(expr.this, parent_stack + [expr], context)

        scalar_queries = list(expr.condition.find_all(ScalarQuery))
        scalar_outputs = {}
        new_condition = expr.condition.copy()

        for scalar in scalar_queries:
            scalar_out = self.visit(scalar.this, parent_stack + [expr, scalar], context)
            scalar_outputs[scalar] = scalar_out
            datatype = scalar.args.get("datatype")
            concrete = scalar_out[0][0].concrete
            if datatype.is_type(*DataType.NUMERIC_TYPES):
                literal = sqlglot_exp.Literal.number(concrete)
            else:
                literal = sqlglot_exp.Literal.string(concrete)
            literal.set("datatype", datatype)
            new_condition.find(ScalarQuery).replace(literal)

        out = []
        with self.trace.scope(expr):
            for row in input_data:
                encoder = ExpressionEncoder(row)
                try:
                    local_context = {"schema": input_schema}
                    smt = encoder.visit(new_condition, context=local_context)
                    if smt:
                        out.append(row)
                    smt_conditions = local_context.get("smt_conditions", [])
                    sql_conditions = local_context.get("sql_conditions", [])
                    takens = [True if b else False for b in smt_conditions]
                    self.trace.which_path(
                        operator=expr,
                        sql_conditions=sql_conditions,
                        symbolic_exprs=smt_conditions,
                        takens=takens,
                        branch=smt.concrete,
                        rows=[row],
                    )
                except NullValueError:
                    logging.info(f"Skipping row with nulls: {row}")
                    continue
            return out

    def visit_join(self, expr: LogicalJoin, parent_stack, context):
        left_input = self.visit(expr.left, parent_stack + [expr], context)
        right_input = self.visit(expr.right, parent_stack + [expr], context)
        out = []
        with self.trace.scope(expr):
            for lrow in left_input:
                for rrow in right_input:
                    row = lrow + rrow
                    try:
                        local_ctx = {"schema": expr.schema()}
                        encoder = ExpressionEncoder(row)
                        smt = encoder.visit(expr.condition, context=local_ctx)
                        if smt:
                            out.append(row)
                        sql_conditions = local_ctx.get("sql_conditions", [])
                        smt_conditions = local_ctx.get("smt_conditions", [])
                        takens = [bool(b) for b in smt_conditions]
                        self.trace.which_path(
                            operator=expr,
                            sql_conditions=sql_conditions,
                            symbolic_exprs=smt_conditions,
                            takens=takens,
                            branch=smt.concrete,
                            rows=[row],
                        )
                    except NullValueError:
                        pass
        return out

    def visit_aggregate(self, expr: LogicalAggregate, parent_stack, context):

        input_data = self.visit(expr.this, parent_stack + [expr], context)
        input_schema = expr.this.schema()

        ### implement group by
        sql_conditions = []
        for key in expr.keys + expr.aggs:
            sql_conditions.append(key.transform(resolve_schema, input_schema))

        out, groups = [], {}
        with self.trace.scope(expr):
            for row in input_data:
                group_key = tuple(row[expr.ref] for expr in expr.keys)
                groups.setdefault(group_key, []).append(row)
            for gid, (group_key, rows) in enumerate(groups.items()):
                row = [] + list(group_key)
                smt_conditions = [] + list(group_key)
                for func_index, agg_func in enumerate(expr.aggs):
                    ref = (
                        func_index + len(expr.keys)
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
                        from src.parseval.symbol import Const, ITE

                        count_values = [
                            ITE(
                                v.is_(None),
                                Const(0, dtype="INT"),
                                Const(1, dtype="INT"),
                            )
                            for v in values
                        ]
                        count_value = sum(count_values)
                        # count_value = len([v for v in values if v.concrete is not None])
                        row.append(count_value)
                    elif agg_func.key == "sum":
                        sum_value = sum(values)
                        row.append(sum_value)
                    elif agg_func.key == "min":
                        min_value = min([v for v in values if v.concrete is not None])
                        row.append(min_value)
                    elif agg_func.key == "max":
                        max_value = max([v for v in values if v.concrete is not None])
                        row.append(max_value)
                    else:
                        raise NotImplementedError(
                            f"Aggregate function {agg_func} not implemented yet."
                        )
                out.append(Row(*row))
                self.trace.which_path(
                    operator=expr,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=[True] * len(sql_conditions),
                    branch=True,
                    rows=[Row(*row)],
                )
        return out

    def visit_sort(self, expr: LogicalSort, parent_stack, context):
        input_data = self.visit(expr.this, parent_stack + [expr], context)
        input_schema = expr.this.schema()

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
            key=lambda row: tuple(row[column.ref - 1] for column in expr.sorts),
            reverse=("DESCENDING" in expr.dirs),
        )
        sql_conditions = [
            cond.transform(resolve_schema, input_schema) for cond in expr.sorts
        ]
        with self.trace.scope(expr):
            for row in data:
                smt_conditions = [row[column.ref - 1] for column in expr.sorts]
                self.trace.which_path(
                    operator=expr,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=smt_conditions,
                    takens=[True] * len(expr.sorts),
                    branch=True,
                    rows=[row],
                )

            return data

    def visit_union(self, expr: LogicalUnion, parent_stack, context):
        raise NotImplementedError("Union encoding not implemented")
