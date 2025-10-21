from __future__ import annotations
import operator, logging

from abc import ABC, abstractmethod
from functools import reduce
from typing import TYPE_CHECKING, List, Dict, Callable, Union
from collections.abc import Iterable

from .step import *
from .expression import *
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from src.parseval.instance import Instance
    from src.parseval.uexpr import UExprToConstraint


# =========================================================================
# Plan Builder
# =========================================================================


class PlannBuilder(ABC):
    TRANSFORM_MAPPING = {}

    def __init__(
        self,
        step_registry: Optional[StepRegistry] = None,
        expression_registry: Optional[ExpressionRegistry] = None,
    ):
        self._handlers: Dict[str, Callable] = {}
        self.step_registry = step_registry or StepRegistry
        self.expr_registry = expression_registry or ExpressionRegistry

    def register_handler(self, operator: str, handler: callable):
        """Register a custom handler for a specific operator."""
        self._handlers[operator.lower()] = handler

    @abstractmethod
    def explain(
        self, schema: str, sql: str, dialect: str = "postgres"
    ) -> LogicalOperator:
        pass


# =========================================================================
# Apache Calcite JSON to Logical Plan Conversion
# =========================================================================


class StepRegistry:
    _registry: Dict[str, Callable] = {}
    _fallback: Callable | None = None

    @classmethod
    def register(cls, node_type: str):
        def decorator(func: Callable):
            cls._registry[node_type] = func
            return func

        return decorator

    @classmethod
    def set_fallback(cls, func: Callable):
        cls._fallback = func
        return func

    @classmethod
    def get_handler(cls, node_type: str) -> Callable:
        return cls._registry.get(node_type, cls._fallback)


class ExpressionRegistry:
    _registry: Dict[str, Callable] = {
        "LITERAL": lambda planner, **kwargs: Literal(
            value=kwargs.pop("value"),
            datatype=DataType.build(
                kwargs.pop("type", "UNKNOWN"),
                kwargs.pop("nullable"),
                kwargs.pop("precision", None),
            ),
        ),
        "INPUT_REF": lambda planner, **kwargs: ColumnRef(
            name=kwargs.pop("name"),
            datatype=DataType(name=kwargs.pop("type", "UNKNOWN")),
            ref=kwargs.pop("index"),
        ),
    }

    @classmethod
    def register(cls, expr_type: Union[str, Iterable[str]]):
        def decorator(func):
            if isinstance(expr_type, str):
                cls._registry[expr_type] = func
            elif isinstance(expr_type, Iterable):
                for et in expr_type:
                    cls._registry[et] = func
            return func

        return decorator

    @classmethod
    def get_handler(cls, node_type: str) -> Callable:
        return cls._registry.get(node_type)


PREDICATE_OPERATORS = {
    "EQUALS": "=",
    "NOT_EQUALS": "!=",
    "GREATER_THAN": ">",
    "LESS_THAN": "<",
    "LESS_THAN_OR_EQUAL": "<=",
    "GREATER_THAN_OR_EQUAL": ">=",
    "LIKE": "Like",
}


@ExpressionRegistry.register(PREDICATE_OPERATORS.keys())
def default_predicate_handler(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    op = kwargs.pop("kind", kwargs.pop("operator"))
    op = reduce(
        lambda x, y: Predicate(left=x, right=y, op=PREDICATE_OPERATORS[op]),
        expressions,
    )
    return op


@ExpressionRegistry.register({"IS", "IS_NULL"})
def default_is_handler(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    op = kwargs.pop("kind", kwargs.pop("operator"))

    if op == "IS_NULL":
        datatype = expressions[0].datatype
        right = Literal(value=None, datatype=datatype)

    else:
        raise ValueError(f"Unsupported IS operator: {op}")
    return IS(left=expressions.pop(), op="IS", right=right)


ARITHMETIC_OPERATORS = {"PLUS": "+", "MINUS": "-", "TIMES": "*", "DIVIDE": "/"}


@ExpressionRegistry.register(ARITHMETIC_OPERATORS.keys())
def default_binary_handler(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    op = kwargs.pop("kind", kwargs.pop("operator"))
    op = reduce(
        lambda x, y: BinaryOp(left=x, right=y, op=ARITHMETIC_OPERATORS[op]),
        expressions,
    )
    return op


CONNECTOR_OPERATORS = {"AND": AND, "OR": OR}


@ExpressionRegistry.register(CONNECTOR_OPERATORS.keys())
def default_connector_handler(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    operator = kwargs.pop("kind", kwargs.pop("operator"))
    op = reduce(
        lambda x, y: CONNECTOR_OPERATORS[operator](left=x, right=y),
        expressions,
    )
    return op


UNARY_OPERATORS = {"NOT": NOT, "IS": IS}


@ExpressionRegistry.register(UNARY_OPERATORS.keys())
def default_unary_handler(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    operator = kwargs.pop("kind", kwargs.pop("operator"))
    return UNARY_OPERATORS[operator](operand=expressions.pop())


AGGFUNC_OPERATORS = {
    "COUNT",
    "SUM",
    "AVG",
    "MAX",
    "MIN",
}


@ExpressionRegistry.register(AGGFUNC_OPERATORS)
def default_aggfunc_expr(planner, **kwargs) -> Expression:
    func_name = kwargs.pop("operator")
    distinct = kwargs.pop("distinct")
    operands = kwargs.pop("operands")
    operands = [
        ColumnRef(
            name=f"${operand['column']}",
            datatype=operand.get("type"),
            ref=operand["column"],
        )
        for operand in operands
    ]
    if not operands:
        operands.append(Star())

        # operand = ColumnRef(
        #     name=f"${operands[0]['column']}",
        #     datatype=operands[0].get("type"),
        #     ref=operands[0]["column"],
        # )
    func = AggFunc(
        name=func_name,
        args=operands,
        distinct=distinct,
        ignore_nulls=kwargs.pop("ignoreNulls"),
        datatype=DataType(name=kwargs.pop("type")),
    )

    # func = AGGFUNC_OPERATORS[func_name](
    #     arg=operand,
    #     distinct=distinct,
    #     ignore_nulls=kwargs.pop("ignoreNulls"),
    #     datatype=DataType(name=kwargs.pop("type")),
    # )
    return func


@ExpressionRegistry.register("CASE")
def default_case_handler(planner, **kwargs) -> Expression:
    operands = kwargs.pop("operands")
    default = planner.walk(operands.pop())
    whens = []
    for index in range(0, len(operands), 2):
        when = planner.walk(operands[index])
        then = planner.walk(operands[index + 1])
        whens.append(When(condition=when, true_expr=then))
    # if len(whens) == 1:
    #     cond = whens[0].condition
    #     true_expr = whens[0].true_expr
    #     if (
    #         isinstance(cond, BinaryOp)
    #         and cond.op == "="
    #         and isinstance(cond.right, Literal)
    #         and cond.right.value == 0
    #         and isinstance(default, ColumnRef)
    #     ):
    #         return default
    #         # return Predicate(left=cond.left, right=true_expr, op="=")

    case = Case(whens=whens, default=default)
    return case


@ExpressionRegistry.register("CAST")
def default_cast_handler(planner, **kwargs) -> Expression:
    operands = kwargs.pop("operands")
    input_operator = planner.walk(operands.pop())
    return Cast(operand=input_operator, to_type=DataType.build(kwargs.pop("type")))


@ExpressionRegistry.register("OTHER_FUNCTION")
def parse_other_function(planner, **kwargs) -> Expression:
    operator = kwargs.pop("operator").upper()
    operands = kwargs.pop("operands")

    if operator == "STRFTIME":
        _format = planner.walk(operands.pop())
        operand = planner.walk(operands.pop())
        return Strftime(args=[operand, _format], datatype=kwargs.pop("type"))
    elif operator == "ABS":
        operand = planner.walk(operands.pop())
        return ABS(arg=operand, datatype=kwargs.pop("type"))

    handler = ExpressionRegistry.get_handler(operator)
    if handler:
        return handler(planner, **kwargs)
    else:
        raise ValueError(f"Unsupported function: {operator}, {kwargs}")


# Strftime
@ExpressionRegistry.register("SCALAR_QUERY")
def default_subquery_handler(planner, **kwargs) -> Expression:
    query = [planner.walk(q) for q in kwargs.pop("query")]

    subquery_type = kwargs.pop("operator")[1:].lower()  # remove leading '$'

    return Subquery(query=query, subquery_type=subquery_type, correlated=False)

    # return Cast(expr=input_operator, to_type=DataType.build(kwargs.pop("type")))


# =========================================================================
# Logical Plan Steps
# ========================================================================


@StepRegistry.register("LogicalTableScan")
def _convert_scan(planner: Planner, **kwargs) -> LogicalScan:
    table_name = kwargs.pop("table")
    operator_id = kwargs.pop("id", None)
    op = LogicalScan(table_name, operator_id=operator_id)
    catalog = kwargs.pop("catalog", None)
    if catalog:
        schema = catalog.get_table(table_name)
        op._table_schema = schema
    return op


@StepRegistry.register("LogicalFilter")
def _convert_filter(planner: Planner, **kwargs) -> LogicalFilter:
    child = planner.walk(kwargs.pop("inputs")[0])
    condition = planner.walk(kwargs.pop("condition"))
    operator_id = kwargs.pop("id", None)
    if isinstance(child, LogicalAggregate):
        return LogicalHaving(child, condition, operator_id=operator_id)
    return LogicalFilter(child, condition, operator_id=operator_id)


@StepRegistry.register("LogicalProject")
def _convert_projection(planner: Planner, **kwargs) -> LogicalProject:
    child = planner.walk(kwargs.pop("inputs")[0])
    expressions = [planner.walk(proj) for proj in kwargs.pop("project", [])]
    operator_id = kwargs.pop("id", None)
    return LogicalProject(child, expressions, operator_id=operator_id)


@StepRegistry.register("LogicalJoin")
def _convert_join(planner: Planner, **kwargs) -> LogicalJoin:
    children = [planner.walk(child) for child in kwargs.pop("inputs")]

    condition = planner.walk(kwargs.pop("condition"))
    join_type = kwargs.pop("joinType", "INNER").upper()
    return LogicalJoin(
        join_type=join_type,
        condition=condition,
        left=children[0],
        right=children[1],
        operator_id=kwargs.pop("id", None),
    )


@StepRegistry.register("LogicalAggregate")
def _convert_aggregate(planner: Planner, **kwargs) -> LogicalAggregate:
    children = [planner.walk(child) for child in kwargs.pop("inputs")]
    groupby = []
    for gid, key in enumerate(kwargs.pop("keys")):
        groupby.append(
            ColumnRef(name=f"${gid}", ref=key.get("column"), datatype=key.get("type"))
        )
    agg_funcs = [planner.walk(func_def) for func_def in kwargs.pop("aggs")]
    return LogicalAggregate(keys=groupby, aggs=agg_funcs, input_operator=children[0])


@StepRegistry.register("LogicalSort")
def _convert_sort(planner: Planner, **kwargs) -> LogicalSort:
    this = planner.walk(kwargs.pop("inputs")[0])
    sort = kwargs.pop("sort", [])
    return LogicalSort(
        sorts=[
            ColumnRef(
                name=str(s["column"]),
                ref=s["column"],
                datatype=DataType.build(s["type"]),
            )
            for s in sort
        ],
        dir=kwargs.pop("dir", []),
        offset=kwargs.pop("offset", 0),
        limit=kwargs.pop("limit", 1),
        input_operator=this,
        operator_id=kwargs.pop("id", None),
    )


def _convert_union(planner: Planner, **kwargs) -> LogicalUnion: ...
def _convert_values(planner: Planner, **kwargs) -> LogicalValues: ...


def _parse_condition(planner: Planner, node: dict) -> Expression: ...


# =========================================================================
# Planner
# = ========================================================================


class Planner(PlannBuilder):
    def __init__(self, step_registry=None, expression_registry=None):
        super().__init__(step_registry, expression_registry)

    def explain2(self, schema: str, plan_path: str, dialect: str = "postgres"):
        if isinstance(schema, str):
            schema = schema.split(";")
        import json

        with open(plan_path) as f:
            plan = json.load(f)

        return self.walk(plan)

    def explain(
        self, schema: str, sql: str, dialect: str = "postgres"
    ) -> LogicalOperator:
        if isinstance(schema, str):
            schema = schema.split(";")
        root = self.walk(sql)
        root.set("dialect", self)
        return root

    def walk(self, node):
        RELOP = "relOp"
        fname = None
        if RELOP in node:
            """parse rel expression, i.e. LogicalProject, LogicalFilter, LogicalJoin, etc."""
            relOp = node.pop(RELOP)
            handler = self.step_registry.get_handler(relOp)
            # return handler(self, **node)
        elif "kind" in node or "operator" in node:
            """parse rex expression, i.e. AND, OR, +, -, *, /, =, <>, >, <, >=, <=, etc."""
            kind_operator = node.get("kind", node.get("operator"))

            handler = self.expr_registry.get_handler(kind_operator)

        else:
            raise ValueError(f"Cannot find relOp or kind/operator in node: {node}")

        if handler is None:
            raise KeyError(
                f"Cannot find handler for node: {node}, currently can handle {self.expr_registry._registry.keys()}"
            )
        return handler(self, **node)


# =============================================================================
# VISITOR PATTERN
# =============================================================================


class LogicalPlanVisitor(ABC):
    """Abstract visitor for traversing logical plans"""

    def visit(self, node: LogicalOperator):
        method_name = f"visit_{node.operator_type.lower()}"
        visitor = getattr(self, method_name, self.generic_visit)
        return visitor(node)

    def generic_visit(self, node: LogicalOperator):
        for child in node.children:
            child.accept(self)


class PrintVisitor(LogicalPlanVisitor):
    def __init__(self):
        super().__init__()
        self.indent = 0

    def generic_visit(self, node):
        line = "  " * self.indent + str(node) + "\n"

        self.indent += 1
        for child in node.children:
            line += child.accept(self)
        self.indent -= 1
        return line


@dataclass
class SymbolTable:
    data: List
    tbl_exprs: List[ColumnRef] = field(default_factory=list, repr=False)


from src.parseval.symbol import Row
import functools


class Skipnulls(Exception):
    pass


def track_next(func):
    @functools.wraps(func)
    def wrapper(self, expr, *args, **kwargs):
        result = func(self, expr, *args, **kwargs)
        self.trace.advance(expr)
        return result

    return wrapper


from contextlib import contextmanager


class ExpressionEncoder(ExpressionVisitor):
    SYMBOLIC_EVAL_REGISTRY = {}
    OPS = {
        ">": operator.gt,
        "<": operator.lt,
        ">=": operator.ge,
        "<=": operator.le,
        "+": operator.add,
        "-": operator.sub,
        "*": operator.mul,
        "/": operator.truediv,
        "Like": lambda left, right: left.like(right),
        "IS": lambda left, right: left.is_(right),
    }

    def __init__(
        self,
        row: Row,
        plan_encoder: Optional[PlanEncoder] = None,
        ignore_nulls: bool = False,
    ):
        super().__init__()
        self.row = row
        self.ref_conditions = []  ## the reference index style Expression
        self.sql_conditions = []  ## the resolved schema style Expression
        self.smt_conditions = []  ## the smt expressions
        self._predicate_stack = []
        self.ignore_nulls = ignore_nulls
        self.plan_encoder = plan_encoder

    @property
    def in_predicates(self):
        return bool(self._predicate_stack)

    @contextmanager
    def predicate_scope(self):
        self._predicate_stack.append(True)
        try:

            def track(expr, smt_expr):
                # result = expr.accept(self)
                # result = func(expr)
                self.sql_conditions.append(expr)
                self.smt_conditions.append(smt_expr)
                return smt_expr

            yield track
        finally:
            self._predicate_stack.pop()

    def visit_columnref(self, expr: ColumnRef):

        if not self.in_predicates:
            self.sql_conditions.append(expr)
            self.smt_conditions.append(self.row[expr.ref])
        return self.row[expr.ref]

    def visit_literal(self, expr: Literal):
        return expr.value

    def visit_predicate(self, expr: Predicate):
        with self.predicate_scope() as track:
            left = expr.left.accept(self)
            right = expr.right.accept(self)
            if not self.ignore_nulls and left.concrete is None:
                raise Skipnulls()
            if expr.op == "=":
                smt_expr = left.eq(right)
            elif expr.op == "!=":
                smt_expr = left.ne(right)
            else:
                smt_expr = self.OPS[expr.op](left, right)
            return track(expr, smt_expr)

    def visit_is(self, expr: IS):
        with self.predicate_scope() as track:
            left = expr.left.accept(self)
            right = expr.right.accept(self)
            return track(expr, left.is_(right))

    # from sqlglot.executor import env

    def visit_binaryop(self, expr: BinaryOp):
        left = expr.left.accept(self)
        right = expr.right.accept(self)
        try:
            res = self.OPS[expr.op](left, right)
        except Exception:
            res.conrete = None
        return res

    def visit_and(self, expr):
        left = expr.left.accept(self)
        right = expr.right.accept(self)
        return left.and_(right)

    def visit_or(self, expr):
        left = expr.left.accept(self)
        right = expr.right.accept(self)
        return left.or_(right)

    def visit_not(self, expr):
        operand = expr.operand.accept(self)
        return operand.not_()

    def visit_cast(self, expr):
        operand = expr.args[0].accept(self)
        return operand
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

    def visit_case(self, expr):
        for when in expr.whens:
            smt_expr = when.condition.accept(self)
            if smt_expr:
                return when.true_expr.accept(self)
        return expr.default.accept(self)

    def visit_subquery(self, expr: Subquery):
        for query in expr.query:

            print(query.pprint())
            res = query.accept(self.plan_encoder)

            logging.info(f"Subquery result rows: {len(res.data)}, {res}")
            return res.data[0][0]

        # return super().visit(expr)


def resolve_schema(expr, node, catalog):
    """
    Resolve a ColumnRef expression to its corresponding schema entry.

    Args:
        expr (sql_exp.Expression): The expression to check.
        node: The current query plan node containing child nodes.
        catalog: The database catalog used to retrieve schema info.

    Returns:
        The resolved schema column if applicable, otherwise None.
    """
    if isinstance(expr, sql_exp.ColumnRef) and expr.ref is not None:
        input_schema = []
        for child in node.children:
            input_schema.extend(child.schema(catalog).columns)
        return input_schema[expr.ref]
    return None


from sqlglot import exp


class PlanEncoder(LogicalPlanVisitor):
    def __init__(
        self, instance: Instance, trace: UExprToConstraint, verbose: bool = True
    ):
        super().__init__()
        self.instance = instance
        self.trace: UExprToConstraint = trace
        self.context = {}
        self.verbose = verbose

    def log(self, message):
        if self.verbose:
            logging.info(message)

    @track_next
    def visit_scan(self, node: LogicalScan):
        rows = self.instance.get_rows(node.table_name)
        self.log(f"Scan table: {node.table_name}, rows: {len(rows)}")
        table = self.instance.catalog.get_table(node.table_name)
        for row in rows:
            ref_conditions, sql_conditions, symbolic_exprs = [], [], []
            for index, column in enumerate(table.columns):
                unique = column.metadata["unique"]
                if unique:
                    ref_conditions.append(
                        sql_exp.ColumnRef(
                            name="$" + str(index),
                            ref=index,
                            datatype=column.datatype,
                            metadata={**column.metadata},
                        )
                    )
                    new_expr = node.schema(catalog=self.instance.catalog).columns[index]
                    sql_conditions.append(new_expr)
                    symbolic_exprs.append(row[column.ref])

            self.trace.which_path(
                node,
                ref_conditions=ref_conditions,
                sql_conditions=sql_conditions,
                symbolic_exprs=symbolic_exprs,
                takens=[True] * len(symbolic_exprs),
                branch=True,
                rows=[row],
            )
        return SymbolTable(data=rows)

    @track_next
    def visit_project(self, node: LogicalProject):
        st = node.children[0].accept(self)
        if st is None:
            logging.error(f"Project child returned None: {node.children[0]}")
        out = []
        for row in st.data:
            data = []
            ref_conditions, sql_conditions = [], []
            smt_conditions = []
            for expr_idx, expr in enumerate(node.expressions):
                encoder = ExpressionEncoder(row, plan_encoder=self, ignore_nulls=True)
                r = encoder.visit(expr)
                data.append(r)
                smt_conditions.extend(encoder.smt_conditions)
                ref_conditions.extend(encoder.sql_conditions)
                for cond in encoder.sql_conditions:
                    new_expr = cond.transform(
                        lambda e: resolve_schema(e, node, self.instance.catalog)
                    )
                    sql_conditions.append(new_expr)

            self.trace.which_path(
                operator=node,
                ref_conditions=ref_conditions,
                sql_conditions=sql_conditions,
                symbolic_exprs=smt_conditions,
                takens=[
                    True if isinstance(sql, ColumnRef) else smt.concrete
                    for smt, sql in zip(smt_conditions, sql_conditions)
                ],
                branch=True,
                rows=[row],
            )
            out.append(Row(columns=data))

        return SymbolTable(data=out, tbl_exprs=st.tbl_exprs)

    @track_next
    def visit_filter(self, node: LogicalFilter):
        st = node.children[0].accept(self)
        out = []
        for row in st.data:
            encoder = ExpressionEncoder(row, plan_encoder=self)
            try:
                smt = encoder.visit(node.condition)
                if smt:
                    out.append(row)
                logging.info(encoder.smt_conditions)

                takens = [b.concrete for b in encoder.smt_conditions]
                sql_conditions = []
                for cond in encoder.sql_conditions:
                    logging.info(f"Condition before schema resolve: {cond}")
                    if cond.right and isinstance(cond.right, sql_exp.Subquery):
                        logging.info(f"Subquery detected in condition: {cond}")
                        sql_conditions.append(
                            sql_exp.Predicate(cond.left, cond.op, sql_exp.Literal(5))
                        )
                        continue
                    else:
                        new_cond = cond.transform(
                            lambda e: resolve_schema(e, node, self.instance.catalog)
                        )
                        sql_conditions.append(new_cond)
                self.trace.which_path(
                    operator=node,
                    ref_conditions=encoder.sql_conditions,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=encoder.smt_conditions,
                    takens=takens,
                    branch=smt.concrete,
                    rows=[row],
                )
            except Skipnulls:
                logging.info(f"Skipping row with nulls: {row}")
                continue
        # self.trace.advance(node)
        return SymbolTable(data=out, tbl_exprs=st.tbl_exprs)

    @track_next
    def visit_join(self, node: LogicalJoin):
        left_st = node.children[0].accept(self)
        right_st = node.children[1].accept(self)
        out = []
        for lrow in left_st.data:
            for rrow in right_st.data:
                row = Row(columns=lrow.columns + rrow.columns)
                try:
                    encoder = ExpressionEncoder(row)
                    smt = encoder.visit(node.condition, plan_encoder=self)
                    if smt:
                        out.append(row)
                    takens = [b.concrete for b in encoder.smt_conditions]
                    sql_conditions = [
                        cond.transform(
                            lambda e: resolve_schema(e, node, self.instance.catalog)
                        )
                        for cond in encoder.sql_conditions
                    ]
                    self.trace.which_path(
                        operator=node,
                        ref_conditions=encoder.sql_conditions,
                        sql_conditions=sql_conditions,
                        symbolic_exprs=encoder.smt_conditions,
                        takens=takens,
                        branch=smt.concrete,
                        rows=[row],
                    )
                except Skipnulls:
                    pass
        return SymbolTable(data=out, tbl_exprs=left_st.tbl_exprs + right_st.tbl_exprs)

    @track_next
    def visit_sort(self, node: LogicalSort):
        """
        We just choose the max and min rows based on the sort order
        """

        st = node.children[0].accept(self)
        out = []
        self.log(f"Sort input rows: {len(st.data)}")

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
            st.data,
            key=lambda row: tuple(row[column.ref - 1] for column in node.sorts),
            reverse=("DESCENDING" in node.dir),
        )
        ref_conditions = node.sorts
        sql_conditions = [
            cond.transform(lambda e: resolve_schema(e, node, self.instance.catalog))
            for cond in ref_conditions
        ]
        smt_conditions = [row[column.ref - 1] for row in data for column in node.sorts]

        for row in data:
            self.trace.which_path(
                operator=node,
                ref_conditions=ref_conditions,
                sql_conditions=sql_conditions,
                symbolic_exprs=smt_conditions,
                takens=[True] * len(ref_conditions),
                branch=True,
                rows=[row],
            )

        return SymbolTable(data=data[: node.limit], tbl_exprs=st.tbl_exprs)

    @track_next
    def visit_aggregate(self, node: LogicalAggregate):
        st = node.children[0].accept(self)
        self.log(f"Aggregate input rows: {len(st.data)}")

        ### implement group by
        ref_conditions, sql_conditions, smt_conditions = [], [], []
        for key in node.keys:
            ref_conditions.append(key)
            sql_conditions.append(
                key.transform(lambda e: resolve_schema(e, node, self.instance.catalog))
            )

        for func in node.aggs:
            ref_conditions.append(func)
            sql_conditions.append(
                func.transform(lambda e: resolve_schema(e, node, self.instance.catalog))
            )

        out = []
        groups = {}
        for row in st.data:
            group_key = tuple(row[expr.ref] for expr in node.keys)
            if group_key not in groups:
                groups[group_key] = []
            groups[group_key].append(row)

        for group_key, rows in groups.items():
            row = [] + list(group_key)
            for agg_func in node.aggs:
                self.log(f"Processing aggregate function: {agg_func}")
                if not isinstance(agg_func, AggFunc):
                    raise NotImplementedError(
                        f"Aggregate function {agg_func} not implemented yet."
                    )
                if agg_func.name == "COUNT":
                    from src.parseval.symbol import Const

                    count_value = len(rows)
                    smt_conditions.append(Const(count_value, dtype="INT"))
                    row.append(Const(count_value, dtype="INT"))

                elif agg_func.name == "SUM":
                    sum_value = sum(
                        row[agg_func.args[0].ref]
                        for row in rows
                        if row[agg_func.args[0].ref].concrete is not None
                    )
                    logging.info(f"SUM value: {sum_value}, {type(sum_value)}")
                    smt_conditions.append(sum_value)
                    row.append(sum_value)
                else:
                    raise NotImplementedError(
                        f"Aggregate function {agg_func} not implemented yet."
                    )
            out.append(Row(columns=row))
            self.trace.which_path(
                operator=node,
                ref_conditions=ref_conditions,
                sql_conditions=sql_conditions,
                symbolic_exprs=smt_conditions,
                takens=[True] * len(ref_conditions),
                branch=True,
                rows=[Row(columns=row)],
            )

        # for agg_func in node.aggs:
        #     self.log(f"Processing aggregate function: {agg_func}")
        #     if not isinstance(agg_func, AggFunc):
        #         raise NotImplementedError(
        #             f"Aggregate function {agg_func} not implemented yet."
        #         )
        #     if agg_func.name == "COUNT":
        #         for group_key, rows in groups.items():
        #             count_value = len(rows)
        #             smt_conditions.append(count_value)
        #             new_columns = list(group_key) + [count_value]
        #             out.append(Row(columns=new_columns))
        #     elif agg_func.name == "SUM":
        #         for group_key, rows in groups.items():
        #             sum_value = sum(
        #                 row[agg_func.args[0].ref]
        #                 for row in rows
        #                 if row[agg_func.args[0].ref].concrete is not None
        #             )
        #             smt_conditions.append(sum_value)
        #             new_columns = list(group_key) + [sum_value]
        #             out.append(Row(columns=new_columns))
        #     else:
        #         raise NotImplementedError(
        #             f"Aggregate function {agg_func} not implemented yet."
        #         )
        # self.trace.which_path(
        #     operator=node,
        #     ref_conditions=ref_conditions,
        #     sql_conditions=sql_conditions,
        #     symbolic_exprs=smt_conditions,
        #     takens=[True] * len(ref_conditions),
        #     branch=True,
        #     rows=out,
        # )
        return SymbolTable(data=out, tbl_exprs=st.tbl_exprs)

    @track_next
    def visit_having(self, node: LogicalHaving):
        st = node.children[0].accept(self)
        out = []
        for row in st.data:
            encoder = ExpressionEncoder(row)
            try:
                smt = encoder.visit(node.condition)
                if smt:
                    out.append(row)
                logging.info(encoder.smt_conditions)

                takens = [b.concrete for b in encoder.smt_conditions]
                sql_conditions = []
                for cond in encoder.sql_conditions:
                    new_cond = cond.transform(
                        lambda e: resolve_schema(e, node, self.instance.catalog)
                    )
                    sql_conditions.append(new_cond)
                self.trace.which_path(
                    operator=node,
                    ref_conditions=encoder.sql_conditions,
                    sql_conditions=sql_conditions,
                    symbolic_exprs=encoder.smt_conditions,
                    takens=takens,
                    branch=smt.concrete,
                    rows=[row],
                )
            except Skipnulls:
                logging.info(f"Skipping row with nulls: {row}")
                continue
        return SymbolTable(data=out, tbl_exprs=st.tbl_exprs)

    def visit_subquery(self, node: CorrelatedSubquery):
        # st = node.children[0].accept(self)

        st = node.subquery.accept(self)
        return st
