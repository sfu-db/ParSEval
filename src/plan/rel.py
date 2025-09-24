from __future__ import annotations
from dataclasses import dataclass, field
from functools import singledispatchmethod
from abc import ABC, abstractmethod
from enum import Enum, auto
from functools import reduce
from typing import TYPE_CHECKING, List, Dict, Callable, Union

# from .catalog import Catalog

if TYPE_CHECKING:
    # from .catalog import Schema, Column, ColumnRef
    from sqlglot.expressions import DATA_TYPE
    from .expression import Expression, Condition
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Optional
from abc import ABC, abstractmethod
from collections.abc import Iterable

from .expression import (
    Condition,
    Binary,
    Predicate,
    Arithmetic,
    ColumnRef,
    Literal,
    DataType,
    AND,
    OR,
    NOT,
    IS,
    Count,
    Max,
    Min,
    Avg,
    Sum,
    Star,
    Schema,
)

# from sqlglot import exp


class JoinType(Enum):
    INNER = auto()
    LEFT = auto()
    RIGHT = auto()
    FULL = auto()
    CROSS = auto()
    SEMI = auto()
    ANTI = auto()


class LogicalOperator(ABC):
    def __init__(self, operator_id: Optional[str] = None):
        self._id = operator_id or str(uuid.uuid4())
        self._schema: Optional[Schema] = None

    @property
    def operator_type(self) -> str:
        """Return the type of this operator"""
        return self.__class__.__name__[len("Logical") :]

    @property
    def id(self) -> str:
        """Return the unique identifier of this operator"""
        return str(self._id)

    @abstractmethod
    def schema(self) -> Schema:
        """Return the output schema of this operator"""
        pass

    @property
    @abstractmethod
    def children(self) -> List["LogicalOperator"]:
        """Return the child operators"""
        pass

    def accept(self, visitor: "LogicalPlanVisitor") -> Any:
        """Accept a visitor for the visitor pattern"""
        method_name = f"visit_{self.operator_type}"
        visit_method = getattr(visitor, method_name, None)
        if not visit_method:
            raise NotImplementedError(
                f"{visitor.__class__.__name__} does not implement {method_name}"
            )
        return visit_method(self)

    def __str__(self) -> str:
        return f"{self.operator_type}({self.operator_id})"

    def __repr__(self) -> str:
        return self.__str__()


class LeafOperator(LogicalOperator):
    """Base class for operators with no children (leaf nodes)"""

    @property
    def children(self) -> List[LogicalOperator]:
        return []


class UnaryOperator(LogicalOperator):
    """Base class for operators with exactly one child"""

    def __init__(
        self, input_operator: LogicalOperator, operator_id: Optional[str] = None
    ):
        super().__init__(operator_id)
        self.input = input_operator

    @property
    def children(self) -> List[LogicalOperator]:
        return [self.input]


class BinaryOperator(LogicalOperator):
    """Base class for operators with exactly two children"""

    def __init__(
        self,
        left: LogicalOperator,
        right: LogicalOperator,
        operator_id: Optional[str] = None,
    ):
        super().__init__(operator_id)
        self.left = left
        self.right = right

    @property
    def children(self) -> List[LogicalOperator]:
        return [self.left, self.right]


class LogicalScan(LeafOperator):
    def __init__(
        self,
        table_name: str,
        alias: Optional[str] = None,
        operator_id: Optional[str] = None,
    ):
        super().__init__(operator_id)
        self.table_name = table_name
        self.alias = alias or table_name
        self._table_schema = None  # To be set from catalog or external source

    def schema(self):
        # Apply table alias to all columns
        columns = []
        for col in self._table_schema.columns:
            new_col = ColumnRef(
                name=col.name,
                data_type=col.data_type,
                nullable=col.nullable,
                unique=col.unique,
                default_value=col.default_value,
                table_alias=self.alias,
            )
            columns.append(new_col)
        return Schema(columns)


class LogicalValues(LeafOperator):
    """Represents a VALUES clause with literal values"""

    def __init__(
        self,
        values: List[List[Any]],
        column_names: List[str],
        operator_id: Optional[str] = None,
    ):
        super().__init__(operator_id)
        self.values = values
        self.column_names = column_names

    def schema(self) -> Schema:
        # Infer schema from values - simplified version
        columns = []
        raise NotImplementedError("Schema inference not implemented")
        # for i, col_name in enumerate(self.column_names):
        #     # This is a simplified type inference
        #     data_type = DataType.STRING  # Default to string
        #     if self.values and len(self.values[0]) > i:
        #         sample_value = self.values[0][i]
        #         if isinstance(sample_value, int):
        #             data_type = DataType.INTEGER
        #         elif isinstance(sample_value, float):
        #             data_type = DataType.FLOAT
        #         elif isinstance(sample_value, bool):
        #             data_type = DataType.BOOLEAN

        #     columns.append(Column(col_name, data_type))
        return Schema(columns)


class LogicalProject(UnaryOperator):
    def __init__(
        self,
        input_operator: LogicalOperator,
        expressions: List[Expression],
        aliases: Optional[List[str]] = None,
        operator_id: Optional[str] = None,
    ):
        super().__init__(input_operator, operator_id)
        self.expressions = expressions
        self.aliases = aliases or [f"col_{i}" for i in range(len(expressions))]

        if len(self.expressions) != len(self.aliases):
            raise ValueError("Number of expressions must match number of aliases")

    def schema(self) -> Schema:
        input_schema = self.input.schema()
        columns = []
        for expr, alias in zip(self.expressions, self.aliases):
            data_type = expr.evaluate_type(input_schema)
            columns.append(ColumnRef(alias, data_type))
        return Schema(columns)


class LogicalFilter(UnaryOperator):
    def __init__(
        self,
        input_operator: LogicalOperator,
        condition: Condition,
        operator_id: Optional[str] = None,
    ):
        super().__init__(input_operator, operator_id)
        self.condition = condition

    def schema(self) -> Schema:
        # Filter doesn't change the schema
        return self.input.schema()


class LogicalSort(UnaryOperator):
    def __init__(
        self,
        sorts: List[Expression],
        dir: List,
        offset: int,
        limit: 1,
        input_operator,
        operator_id=None,
    ):
        super().__init__(input_operator, operator_id)

        self.sorts = sorts
        self.dir = dir
        self.offset = offset
        self.limit = limit

    def schema(self):
        return self.input.schema()


class LogicalLimit(UnaryOperator):
    def __init__(self, limit, input_operator, operator_id=None):
        super().__init__(input_operator, operator_id)
        self.limit = limit


class LogicalAggregate(UnaryOperator):
    def __init__(
        self, keys: List[Expression], aggs: List, input_operator, operator_id=None
    ):
        super().__init__(input_operator, operator_id)
        self.keys = keys
        self.aggs = aggs


class LogicalJoin(BinaryOperator):
    def __init__(
        self,
        join_type: JoinType,
        condition: Optional[Condition],
        left: LogicalOperator,
        right: LogicalOperator,
        operator_id=None,
    ):
        super().__init__(left, right, operator_id)
        self.join_type = join_type
        self.condition = condition

    def schema(self):
        return self.left.schema() + self.right.schema()


class LogicalUnion(BinaryOperator):
    def __init__(
        self,
        union_all: bool,
        left: LogicalOperator,
        right: LogicalOperator,
        operator_id=None,
    ):
        super().__init__(left, right, operator_id)
        self.union_all = union_all


class LogicalIntersect(BinaryOperator):
    def __init__(
        self,
        intersect_all: bool,
        left: LogicalOperator,
        right: LogicalOperator,
        operator_id=None,
    ):
        super().__init__(left, right, operator_id)
        self.intersect_all = intersect_all


class LogicalDifference(BinaryOperator):
    def __init__(
        self,
        difference_all: bool,
        left: LogicalOperator,
        right: LogicalOperator,
        operator_id=None,
    ):
        super().__init__(left, right, operator_id)
        self.difference_all = difference_all


# =========================================================================
# Plan Builder
# =========================================================================


class PlannBuilder(ABC):
    TRANSFORM_MAPPING = {}

    def __init__(self):
        self._handlers: Dict[str, Callable] = {}

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
    _registry: Dict[str, Callable] = {}

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


@ExpressionRegistry.register("LITERAL")
def parse_literal_expr(planner, **kwargs) -> Expression:
    value = kwargs.pop("value")
    dtype = kwargs.pop("type", "UNKNOWN")

    datatype = DataType(
        name=dtype,
        nullable=kwargs.pop("nullable"),
        precision=kwargs.pop("precision", None),
    )
    literal = Literal(value=value, datatype=datatype)
    return literal


@ExpressionRegistry.register("INPUT_REF")
def parse_input_ref_expr(planner, **kwargs) -> Expression:
    name = kwargs.pop("name")
    index = kwargs.pop("index")
    dtype = kwargs.pop("type", "UNKNOWN")

    input_ref = ColumnRef(name=name, datatype=DataType(name=dtype), ref=index)
    return input_ref


@ExpressionRegistry.register(PREDICATE_OPERATORS.keys())
def parse_predicate_expr(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    operator = kwargs.pop("kind", kwargs.pop("operator"))
    op = reduce(
        lambda x, y: Predicate(left=x, right=y, op=PREDICATE_OPERATORS[operator]),
        expressions,
    )
    return op


ARITHMETIC_OPERATORS = {"PLUS": "+", "MINUS": "-", "TIMES": "*", "DIVIDE": "/"}


@ExpressionRegistry.register(ARITHMETIC_OPERATORS.keys())
def parse_arithmetic_expr(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    operator = kwargs.pop("kind", kwargs.pop("operator"))
    op = reduce(
        lambda x, y: Arithmetic(left=x, right=y, op=ARITHMETIC_OPERATORS[operator]),
        expressions,
    )
    return op


CONNECTOR_OPERATORS = {"AND": AND, "OR": OR}


@ExpressionRegistry.register(CONNECTOR_OPERATORS.keys())
def parse_connector_expr(planner, **kwargs) -> Expression:
    expressions = [planner.walk(operand) for operand in kwargs.get("operands", [])]
    operator = kwargs.pop("kind", kwargs.pop("operator"))
    op = reduce(
        lambda x, y: ARITHMETIC_OPERATORS[operator](left=x, right=y),
        expressions,
    )
    return op


UNARY_OPERATORS = {"NOT": NOT, "IS": IS}


@ExpressionRegistry.register(UNARY_OPERATORS.keys())
def parse_unary_expr(planner, **kwargs) -> Expression:
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
def parse_aggfunc_expr(planner, **kwargs) -> Expression:
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
        operand=operand,
        distinct=distinct,
        ignorenulls=kwargs.pop("ignorenulls"),
        datatype=DataType(name=kwargs.pop("type")),
    )
    return func


class Planner(PlannBuilder):
    def __init__(
        self, step_registry: Optional[Any] = None, expr_registry: Optional[Any] = None
    ):
        self.step_registry = step_registry or StepRegistry
        self.expr_registry = expr_registry or ExpressionRegistry

    def explain2(self, schema: str, plan_path: str, dialect: str = "postgres"):
        from sqlglot import parse_one, exp

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

        if handler is None:
            print(f"Cannot find handler for node: {node}")
        return handler(self, **node)

    # def _convert_projection(self, **kwargs) -> LogicalProject:
    #     input_op = self._convert(**kwargs)
    #     expressions = [
    #         self._convert(expr, self.catalog) for expr in kwargs.pop("expressions", [])
    #     ]
    #     aliases = kwargs.pop("aliases", None)
    #     operator_id = kwargs.pop("id", None)
    #     return LogicalProject(input_op, expressions, aliases, operator_id)


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

    # parameters = {k: v for k, v in node.items() if k != "inputs"}

    # return Join(this=deps[0], expression=deps[1], condition=condition, **parameters)


def _convert_aggregate(planner: Planner, **kwargs) -> LogicalAggregate: ...


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


# =============================================================================
# VISITOR PATTERN
# =============================================================================


class LogicalPlanVisitor(ABC):
    """Abstract visitor for traversing logical plans"""

    @abstractmethod
    def visit_scan(self, op: LogicalScan) -> Any:
        pass

    @abstractmethod
    def visit_values(self, op: LogicalValues) -> Any:
        pass

    @abstractmethod
    def visit_projection(self, op: LogicalProject) -> Any:
        pass

    @abstractmethod
    def visit_filter(self, op: LogicalFilter) -> Any:
        pass

    @abstractmethod
    def visit_sort(self, op: LogicalSort) -> Any:
        pass

    @abstractmethod
    def visit_join(self, op: LogicalJoin) -> Any:
        pass

    @abstractmethod
    def visit_union(self, op: LogicalUnion) -> Any:
        pass

    @abstractmethod
    def visit_aggregate(self, op: LogicalAggregate) -> Any:
        pass
