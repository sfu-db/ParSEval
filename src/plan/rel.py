from __future__ import annotations
from dataclasses import dataclass, field
from functools import singledispatchmethod
from abc import ABC, abstractmethod
from enum import Enum, auto

from typing import TYPE_CHECKING, List, Dict, Callable, Union
from .catalog import Catalog

if TYPE_CHECKING:
    from .catalog import Schema, Column, ColumnRef
    from sqlglot.expressions import DATA_TYPE
    from .expression import TypedExpression, Condition  # Expression, Condition
import uuid
from dataclasses import dataclass
from typing import Any, Optional
from abc import ABC, abstractmethod


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
        expressions: List[TypedExpression],
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
            columns.append(Column(alias, data_type))
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
        sorts: List[TypedExpression],
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
    def register(cls, expr_type: str):
        def decorator(func):
            cls._registry[expr_type] = func
            return func

        return decorator

    @classmethod
    def get_handler(cls, node_type: str) -> Callable:
        return cls._registry.get(node_type, cls._fallback)


# @ExpressionRegistry.register("glot")
# def parse_glot_expr(factory, node: exp.Expression):
#     return TypedExpression(
#         glot_expr=node,
#         type_=node.args.get("type"),
#         nullable=True,
#         metadata={"original_type": type(node).__name__},
#     )


class Planner(PlannBuilder):
    # REL_MAPPING = {
    #     "LogicalTableScan": "scan",
    #     "EnumerableTableScan": "scan",
    #     "LogicalProject": "project",
    #     "LogicalFilter": "filter",
    #     "LogicalJoin": "join",
    #     "LogicalAggregate": "aggregate",
    #     "LogicalUnion": "union",
    #     "LogicalIntersect": "intersect",
    #     "LogicalMinus": "minus",
    #     "LogicalSort": "sort",
    #     "LogicalValues": "values",
    #     "SCALAR_QUERY": "scalar",
    # }

    def __init__(self, step_registry: None, expr_registry: None):
        self.step_registry = step_registry or StepRegistry
        self.expr_registry = expr_registry or ExpressionRegistry

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
        CONDITION = "condition"
        AGG_FUNCS = {"COUNT", "SUM", "AVG", "MAX", "MIN"}
        fname = None
        if RELOP in node:
            """parse rel expression, i.e. LogicalProject, LogicalFilter, LogicalJoin, etc."""
            relOp = node.pop(RELOP)
            handler = self.step_registry.get_handler(relOp)
            return handler(self, **node)
        elif "kind" in node or "operator" in node:
            """parse rex expression, i.e. AND, OR, +, -, *, /, =, <>, >, <, >=, <=, etc."""
            kind_operator = node.get("kind", node.get("operator"))
            if kind_operator in AGG_FUNCS:
                fname = "aggfunc"
            else:
                fname = kind_operator
        fn = self._handlers.get(fname)
        return fn(self, node)

    def _convert_projection(self, **kwargs) -> LogicalProject:
        input_op = self._convert(**kwargs)
        expressions = [
            self._convert(expr, self.catalog) for expr in kwargs.pop("expressions", [])
        ]
        aliases = kwargs.pop("aliases", None)
        operator_id = kwargs.pop("id", None)
        return LogicalProject(input_op, expressions, aliases, operator_id)


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
def _convert_filter(planner: Planner, **kwargs) -> LogicalFilter: ...


@StepRegistry.register("LogicalProject")
def _convert_projection(planner: Planner, **kwargs) -> LogicalProject: ...


def _convert_join(planner: Planner, **kwargs) -> LogicalJoin: ...


def _convert_aggregate(planner: Planner, **kwargs) -> LogicalAggregate: ...


def _convert_sort(planner: Planner, **kwargs) -> LogicalSort: ...


def _convert_union(planner: Planner, **kwargs) -> LogicalUnion: ...
def _convert_values(planner: Planner, **kwargs) -> LogicalValues: ...


def _parse_condition(planner: Planner, node: dict) -> TypedExpression: ...


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
