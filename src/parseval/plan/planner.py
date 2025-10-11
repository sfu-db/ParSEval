from __future__ import annotations
import operator
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
    operator = kwargs.pop("kind", kwargs.pop("operator"))
    op = reduce(
        lambda x, y: Predicate(left=x, right=y, op=PREDICATE_OPERATORS[operator]),
        expressions,
    )
    return op


ARITHMETIC_OPERATORS = {"PLUS": "+", "MINUS": "-", "TIMES": "*", "DIVIDE": "/"}


@ExpressionRegistry.register(ARITHMETIC_OPERATORS.keys())
def default_binary_handler(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    operator = kwargs.pop("kind", kwargs.pop("operator"))
    op = reduce(
        lambda x, y: BinaryOp(left=x, right=y, op=operator),
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


UNARY_OPERATORS = {"NOT": NOT, "IS_NULL": IsNull}


@ExpressionRegistry.register(UNARY_OPERATORS.keys())
def default_unary_handler(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    operator = kwargs.pop("kind", kwargs.pop("operator"))
    return UNARY_OPERATORS[operator](operand=expressions.pop())


AGGFUNC_OPERATORS = {
    "COUNT": Count,
    "SUM": Sum,
    "AVG": Avg,
    "MAX": Max,
    "MIN": Min,
}


@ExpressionRegistry.register(AGGFUNC_OPERATORS.keys())
def default_aggfunc_expr(planner, **kwargs) -> Expression:
    func_name = kwargs.pop("operator")
    distinct = kwargs.pop("distinct")
    operands = kwargs.pop("operands")
    operand = Star()
    if operands:
        operand = ColumnRef(
            name=f"${operands[0]['column']}",
            datatype=operands[0].get("type"),
            ref=operands[0]["column"],
        )

    func = AGGFUNC_OPERATORS[func_name](
        arg=[operand],
        distinct=distinct,
        ignore_nulls=kwargs.pop("ignoreNulls"),
        datatype=DataType(name=kwargs.pop("type")),
    )
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
                name=s["column"],
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

    # @abstractmethod
    # def visit_scan(self, node: LogicalScan) -> Any:
    #     pass

    # @abstractmethod
    # def visit_values(self, node: LogicalValues) -> Any:
    #     pass

    # @abstractmethod
    # def visit_projection(self, node: LogicalProject) -> Any:
    #     pass

    # @abstractmethod
    # def visit_filter(self, node: LogicalFilter) -> Any:
    #     pass

    # @abstractmethod
    # def visit_sort(self, node: LogicalSort) -> Any:
    #     pass

    # @abstractmethod
    # def visit_join(self, node: LogicalJoin) -> Any:
    #     pass

    # @abstractmethod
    # def visit_union(self, node: LogicalUnion) -> Any:
    #     pass

    # @abstractmethod
    # def visit_aggregate(self, node: LogicalAggregate) -> Any:
    #     pass


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


def track_predicate(func):
    def wrapper(self, expr, *args, **kwargs):
        result = func(self, expr, *args, **kwargs)
        self.sql_conditions.append(expr)
        self.smt_conditions.append(result)
        return result

    return wrapper


class ExpressionEncoder(ExpressionVisitor):
    OPS = {
        ">": operator.gt,
        "<": operator.lt,
        ">=": operator.ge,
        "<=": operator.le,
        "+": operator.add,
        "-": operator.sub,
        "*": operator.mul,
        "/": operator.truediv,
    }

    def __init__(self, row: Row):
        super().__init__()
        self.row = row

        self.sql_conditions = []
        self.smt_conditions = []

    def visit_columnref(self, expr: ColumnRef):
        return self.row[expr.ref]

    def visit_literal(self, expr: Literal):
        return expr.value

    @track_predicate
    def visit_predicate(self, expr: Predicate):
        left = expr.left.accept(self)
        right = expr.right.accept(self)

        if expr.op == "=":
            return left.eq(right)
        elif expr.op == "!=":
            return left.ne(right)
        else:
            return self.OPS[expr.op](left, right)

    def visit_binaryop(self, expr: BinaryOp):
        left = expr.left.accept(self)
        right = expr.right.accept(self)
        return self.OPS[expr.op](left, right)

    def visit_and(self, expr):
        left = expr.left.accept(self)
        right = expr.right.accept(self)
        return left.and_(right)

    def visit_or(self, expr):
        left = expr.left.accept(self)
        right = expr.right.accept(self)
        return left.or_(right)


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
            print(message)

    def visit_scan(self, node: LogicalScan):
        rows = self.instance.get_rows(node.table_name)
        self.log(f"Scan table: {node.table_name}, rows: {len(rows)}")
        return SymbolTable(data=rows)

    def visit_project(self, node: LogicalProject):
        st = node.children[0].accept(self)

        out = []
        for row in st.data:
            data = []
            for expr in node.expressions:
                if isinstance(expr, ColumnRef):
                    data.append(row[expr.ref])
                    self.trace.which_path(
                        operator=node,
                        sql_conditions=[expr],
                        symbolic_exprs=[row[expr.ref]],
                        takens=[True],
                        branch=True,
                        rows=[row],
                    )
            out.append(Row(columns=data))

        return SymbolTable(data=out, tbl_exprs=st.tbl_exprs)

    def visit_filter(self, node: LogicalFilter):
        st = node.children[0].accept(self)

        out = []
        for row in st.data:
            encoder = ExpressionEncoder(row)
            smt = encoder.visit(node.condition)
            if smt:
                out.append(row)

            takens = [b.concrete for b in encoder.smt_conditions]
            self.trace.which_path(
                operator=node,
                sql_conditions=encoder.sql_conditions,
                symbolic_exprs=encoder.smt_conditions,
                takens=takens,
                branch=smt.concrete,
                rows=[row],
            )
            self.log(f"encoder smt: {encoder.smt_conditions}")
        return SymbolTable(data=out, tbl_exprs=st.tbl_exprs)

    def visit_join(self, node: LogicalJoin):
        left_st = node.children[0].accept(self)
        right_st = node.children[1].accept(self)
        out = []
        self.log(
            f"Join left rows: {len(left_st.data)}, right rows: {len(right_st.data)}"
        )
        for lrow in left_st.data:
            # self.log(f"lrow: {lrow}")
            for rrow in right_st.data:
                row = Row(columns=lrow.columns + rrow.columns)
                self.log(f"joined row: {len(row.columns)}")
                encoder = ExpressionEncoder(row)
                smt = encoder.visit(node.condition)

                self.log(f"smt: {smt}")
                if smt:
                    out.append(row)
                print(f"encoder.predicates: {encoder.predicates}")
        return SymbolTable(data=out, tbl_exprs=left_st.tbl_exprs + right_st.tbl_exprs)

    def visit_sort(self, node: LogicalSort):
        st = node.children[0].accept(self)
        return st
