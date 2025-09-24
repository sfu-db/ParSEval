from __future__ import annotations
from dataclasses import dataclass, field
from functools import singledispatchmethod
from abc import ABC, abstractmethod
from enum import Enum, auto
from functools import reduce
from typing import TYPE_CHECKING, List, Dict, Callable, Union
from collections.abc import Iterable

if TYPE_CHECKING:
    from .expression import *
    from .rel import *


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
