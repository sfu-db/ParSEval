"""
Utilities for speculative typing: `TypeEnv` and the `SpeculateEngine`.

The speculate engine provides a small, extensible rule set to infer
data types from Logical Plan (sqlglot expressions). Results are
recorded in a `TypeEnv` which maps ColumnRef to `DataType` instances.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Iterable, Dict, Optional, Any
from src.parseval.plan.rex import (
    Expression,
    DataType,
    sqlglot_exp,
    ColumnRef,
    LogicalOperator,
    BinaryOperator,
    UnaryOperator,
    LogicalSort,
    resolve_schema,
)


class TypeEnv:
    """Environment storing speculative DataType information.
    Keys are ColumnRef; values are DataType instances created via DataType.build.
    """

    def __init__(self):
        self.env: Dict[Any, DataType] = {}

    def set(self, name: ColumnRef, type_: Any):
        new_t = DataType.build(type_)
        old_typ = self.env.get(name)
        if old_typ is None:
            old_typ = name.args.get("datatype")
        self.env[name] = self._unify(old_typ, new_t)

    def get(self, name: Any) -> Optional[DataType]:
        return self.env.get(name)

    def _unify(self, t1: DataType, t2: DataType) -> DataType:
        if t1.is_type(t2):
            return t1
        if t1.is_type(*DataType.TEMPORAL_TYPES):
            return t1
        if t2.is_type(*DataType.TEMPORAL_TYPES):
            return t2
        if t1.is_type(*DataType.TEXT_TYPES) or t2.is_type(*DataType.TEXT_TYPES):
            return t1
        if t1.is_type(*DataType.NUMERIC_TYPES) and t2.is_type(*DataType.NUMERIC_TYPES):
            if t1.is_type(*DataType.REAL_TYPES) and t2.is_type(*DataType.INTEGER_TYPES):
                return t1
            if t2.is_type(*DataType.REAL_TYPES) and t1.is_type(*DataType.INTEGER_TYPES):
                return t2
            return DataType.build("INT")
        if t1.is_type(*DataType.NUMERIC_TYPES) and t2.is_type(*DataType.INTEGER_TYPES):
            return t1
        return t2

    def items(self):
        return self.env.items()

    def __repr__(self):
        return f"TypeEnv({self.env})"


class SpeculativeRule(ABC):
    @abstractmethod
    def matches(self, expr: Expression) -> bool:
        pass

    @abstractmethod
    def apply(self, expr: Expression, env: TypeEnv):
        pass


class ArithmeticRule(SpeculativeRule):
    """Infer numeric types for operands of arithmetic binary operators."""

    ARITH_OPS = {"add", "sub", "mul", "div", "mod"}

    def matches(self, expr: Expression) -> bool:
        return (
            isinstance(expr, sqlglot_exp.Binary)
            and getattr(expr, "key", "").lower() in self.ARITH_OPS
        )

    def apply(self, expr: sqlglot_exp.Binary, env: TypeEnv):
        for side in {expr.this, expr.expression}:
            ancestor = side.find_ancestor(LogicalOperator)
            if isinstance(ancestor, UnaryOperator):
                input_schema = ancestor.this.schema()
            elif isinstance(ancestor, BinaryOperator):
                input_schema = ancestor.schema()
            s = side.transform(resolve_schema, input_schema)
            if isinstance(s, ColumnRef):
                env.set(s, DataType.build("INT"))


class CastRule(SpeculativeRule):
    """Infer types based on CAST expressions."""

    def matches(self, expr: Expression) -> bool:
        return isinstance(expr, sqlglot_exp.Cast)

    def apply(self, expr: sqlglot_exp.Cast, env: TypeEnv):
        target_type = expr.args.get("to")
        value_expr = expr.this
        if target_type is not None and isinstance(value_expr, ColumnRef):
            ancestor = expr.find_ancestor(LogicalOperator)
            if isinstance(ancestor, UnaryOperator):
                input_schema = ancestor.this.schema()
            elif isinstance(ancestor, BinaryOperator):
                input_schema = ancestor.schema()
            s = expr.transform(resolve_schema, input_schema)
            if isinstance(s, ColumnRef):
                env.set(s, target_type)

            # env.set(value_expr, target_type)


class AggFuncRule(SpeculativeRule):
    AGG_FUNCS = {
        "count",
        "sum",
        "avg",
        "min",
        "max",
    }

    def matches(self, expr: Expression) -> bool:
        if isinstance(expr, sqlglot_exp.AggFunc) and expr.key in self.AGG_FUNCS:
            return True
        return False

    def apply(self, expr: sqlglot_exp.AggFunc, env: TypeEnv):
        if expr.key in {"sum", "avg"}:
            arg = expr.args.get("this")
            if isinstance(arg, ColumnRef):
                env.set(arg, DataType.build("INT"))


class OrderByNumberRule(SpeculativeRule):
    """Infer numeric types for ORDER BY numeric literals."""

    def matches(self, expr: Expression) -> bool:
        if isinstance(expr, LogicalSort):
            if any(
                order_expr.args.get("datatype").is_type(*DataType.NUMERIC_TYPES)
                for order_expr in expr.expressions
            ):
                return True
        return False

    def apply(self, expr: LogicalSort, env: TypeEnv):
        # No ColumnRef to set in this case; this rule could be expanded
        # to handle more complex scenarios if needed.
        input_schema = expr.this.schema()
        for order_expr in expr.expressions:
            order_expr = order_expr.transform(resolve_schema, input_schema)
            if order_expr.key in {"count"}:
                continue
            for columnref in order_expr.find_all(ColumnRef):
                env.set(columnref, DataType.build("INT"))


class SpeculateEngine:
    """Engine that walks an expression and applies speculative rules."""

    def __init__(self, rules: Optional[Iterable[SpeculativeRule]] = None):
        if rules:
            self.rules = list(rules)
        else:
            self.rules = [
                ArithmeticRule(),
                CastRule(),
                AggFuncRule(),
                OrderByNumberRule(),
            ]

    def infer(self, expr: Expression, env: Optional[TypeEnv] = None) -> TypeEnv:
        if env is None:
            env = TypeEnv()
        if not self.rules:
            return env
        for node in expr.walk():
            try:
                for rule in self.rules:
                    if rule.matches(node):
                        rule.apply(node, env)
            except Exception:
                continue
        return env
