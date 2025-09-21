from __future__ import annotations
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from typing import Any, Optional, Dict, TYPE_CHECKING

if TYPE_CHECKING:
    from .catalog import Schema, Column, ColumnRef
    from .dtype import DATATYPE

from dataclasses import dataclass

from abc import ABC, abstractmethod

from sqlglot import exp


@dataclass(frozen=True, slots=True)
class TypedExpression:
    """
    Wraps a SQLGlot Expression in our TypedExpression system
    for metadata, type info, and visitor compatibility.
    """

    glot_expr: exp.Expression
    type_: Optional[DATATYPE] = None
    nullable: Optional[bool] = None
    unique: Optional[bool] = None
    default: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Condition(TypedExpression): ...


@dataclass(frozen=True, slots=True)
class Predicate(Condition): ...


@dataclass(frozen=True, slots=True)
class Subquery(TypedExpression):
    correlated: bool = False


class ExpressionVisitor:
    def visit(self, expr: Any):
        method = "visit_" + expr.__class__.__name__
        return getattr(self, method, self.visit_default)(expr)

    def visit_default(self, expr: Any):
        if isinstance(expr, TypedExpression):
            return expr.glot_expr.sql()
        raise NotImplementedError(f"No visitor for {type(expr)}")


class PrettyPrinter(ExpressionVisitor):
    def visit_TypeExpressionWrapper(self, expr: TypedExpression):
        return expr.glot_expr.sql()


# @dataclass(frozen=True, slots=True)
# class Expression(ABC):
#     """Base class for expressions in the logical plan"""

#     @abstractmethod
#     def evaluate_type(self, input_schema: "Schema") -> DATATYPE:
#         """Return the data type this expression evaluates to"""
#         pass


# @dataclass(frozen=True, slots=True)
# class TypeExpression(Expression):
#     """Expression with a predefined type, used for type checking"""

#     data_type: DATATYPE
#     nullable: Optional[bool] = None
#     unique: Optional[bool] = None
#     default: Optional[Any] = None


# @dataclass(frozen=True, slots=True)
# class ColumnRef(TypeExpression):
#     """References a column"""

#     column_name: str
#     table_alias: Optional[str] = None

#     def evaluate_type(self, input_schema: Schema) -> DATATYPE:
#         qualified_name = (
#             f"{self.table_alias}.{self.column_name}"
#             if self.table_alias
#             else self.column_name
#         )
#         col = input_schema.get_column(qualified_name) or input_schema.get_column(
#             self.column_name
#         )
#         return col.data_type if col else DATATYPE.NULL


# @dataclass(frozen=True, slots=True)
# class Literal(TypeExpression):
#     """Represents a literal value"""

#     value: Any

#     def evaluate_type(self, input_schema: Schema) -> DATATYPE:
#         return self.data_type


# class Condition(TypeExpression): ...


# class Predicate(TypeExpression): ...


# from sqlglot import exp


# @dataclass(frozen=True, slots=True)
# class Binary(TypeExpression):
#     """Binary expression with two operands and an operator"""

#     left: Expression
#     right: Expression
#     operator: str


# @dataclass(frozen=True, slots=True)
# class Unary(TypeExpression):
#     """Unary expression with one operand and an operator"""

#     operand: Expression
#     operator: str


# @dataclass(frozen=True, slots=True)
# class EQ(Binary, Predicate):
#     pass


# @dataclass(frozen=True, slots=True)
# class NEQ(Binary, Predicate):
#     pass


# @dataclass(frozen=True, slots=True)
# class GT(Binary, Predicate):
#     pass


# @dataclass(frozen=True, slots=True)
# class GTE(Binary, Predicate):
#     pass


# @dataclass(frozen=True, slots=True)
# class LT(Binary, Predicate):
#     pass


# @dataclass(frozen=True, slots=True)
# class LTE(Binary, Predicate):
#     pass


# @dataclass(frozen=True, slots=True)
# class AND(Binary, Predicate):
#     pass


# @dataclass(frozen=True, slots=True)
# class OR(Binary, Predicate):
#     pass


# @dataclass(frozen=True, slots=True)
# class NOT(Unary, Predicate):
#     pass


# @dataclass(frozen=True, slots=True)
# class Like(Binary, Predicate):
#     pass


# class ExpressionVisitor:
#     def visit(self, expr: Expression):
#         method = "visit_" + expr.__class__.__name__
#         return getattr(self, method, self.visit_default)(expr)

#     def visit_default(self, expr: Expression):
#         raise NotImplementedError(f"No visitor method for {expr.__class__.__name__}")


# class PrettyPrinter(ExpressionVisitor):
#     def visit_ColumnRef(self, expr: ColumnRef):
#         return expr.name

#     def visit_Literal(self, expr: Literal):
#         return repr(expr.value)

#     def visit_BinaryOp(self, expr: Binary):
#         return f"({self.visit(expr.left)} {expr.op} {self.visit(expr.right)})"

#     def visit_UnaryOp(self, expr: Unary):
#         return f"({expr.op} {self.visit(expr.operand)})"

#     # def visit_FunctionCall(self, expr: FunctionCall):
#     #     args_str = ", ".join(self.visit(arg) for arg in expr.args)
#     #     return f"{expr.name}({args_str})"

#     # def visit_Subquery(self, expr: Subquery):
#     #     return f"(SUBQUERY: {expr.plan})"

#     def visit_EQ(self, expr: EQ):
#         return f"({self.visit(expr.left)} = {self.visit(expr.right)})"

#     def visit_NEQ(self, expr: NEQ):
#         return f"({self.visit(expr.left)} != {self.visit(expr.right)})"

#     def visit_GT(self, expr: GT):
#         return f"({self.visit(expr.left)} > {self.visit(expr.right)})"

#     def visit_LT(self, expr: LT):
#         return f"({self.visit(expr.left)} < {self.visit(expr.right)})"
