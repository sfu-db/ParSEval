from __future__ import annotations
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from typing import Any, Optional, Dict, TYPE_CHECKING, List

if TYPE_CHECKING:
    # from .catalog import Schema, Column, ColumnRef
    from .dtype import DATATYPE
from .dtype import DataType
from dataclasses import dataclass

from abc import ABC, abstractmethod
from sqlglot import exp


class Expression(ABC):
    """Base class for all expressions."""

    @abstractmethod
    def get_children(self) -> List["Expression"]:
        """Return the child expressions."""

    def accept(self, visitor: "ExpressionVisitor"):
        raise NotImplementedError

    @abstractmethod
    def infer_type(self, schema_context: "Schema") -> DataType:
        """Infer the data type of this expression"""
        pass

    @abstractmethod
    def is_deterministic(self) -> bool:
        """Check if expression is deterministic (always returns same result for same input)"""
        pass

    def to_sql(self, dialect="sqlite"):
        """Convert the expression to its SQL representation."""
        raise NotImplementedError("Subclasses must implement to_sql.")

    def __str__(self):
        return self.to_sql()

    def __repr__(self):
        return f"{self.__class__.__name__}({self.id[:8]})"


class Literal(Expression):
    __slots__ = ("value", "datatype")

    def __init__(self, value: Any, datatype: DATATYPE):
        super().__init__()
        self.value = value
        self.datatype = DataType.build(datatype)

    def get_children(self):
        return []

    def infer_type(self, schema_context):
        return self.datatype

    def is_deterministic(self):
        return True

    def to_sql(self, dialect="sqlite"):
        if self.datatype.is_numeric():
            return str(self.value)
        return f'"{self.value}"'


class ColumnRef(Expression):
    __slots__ = ("column_name", "table_alias", "ref", "datatype")

    def __init__(
        self,
        name: str,
        table_alias: Optional[str] = None,
        ref: Optional[int] = None,
        datatype: Optional[DATATYPE] = None,
    ):
        super().__init__()
        self.name = name
        self.table_alias = table_alias
        self.ref = ref
        self.datatype = DataType.build(datatype) if datatype else None

    def get_children(self):
        return []

    def infer_type(self, schema_context):
        return self.datatype

    def is_deterministic(self):
        return True

    @property
    def qualified_name(self) -> str:
        return f"{self.table_alias}.{self.name}" if self.table_alias else self.name

    def to_sql(self, dialect="sqlite"):
        if self.table_alias:
            return f"{self.table_alias}.{self.column_name}"
        return self.column_name


class Schema(Expression):
    __slots__ = ("columns",)

    def get_column(self, name: str) -> Optional[ColumnRef]:
        for col in self.columns:
            if col.name == name or col.qualified_name == name:
                return col
        return None

    def column_names(self) -> List[str]:
        return [col.name for col in self.columns]

    def __init__(self, columns: List[ColumnRef]):
        super().__init__()
        self.columns = columns

    def get_children(self):
        return []

    def infer_type(self, schema_context):
        raise NotImplementedError("Schema does not have a single data type")

    def is_deterministic(self):
        return True

    def to_sql(self, dialect="sqlite"):
        return ", ".join(col.name for col in self.columns)


class Star(Expression):
    """Represents a '*' in SQL (all columns)."""

    __slots__ = ()

    def get_children(self):
        return []

    def to_sql(self, dialect="sqlite"):
        return "*"


class Condition(Expression): ...


class Binary(Condition):
    __slots__ = ("left", "op", "right")

    def __init__(self, left: Expression, op: str, right: Expression):
        super().__init__()
        self.left = left
        self.op = op
        self.right = right

    def get_children(self):
        return [self.left, self.right]

    def to_sql(self, dialect="sqlite"):
        left_sql = self.left.to_sql(dialect)
        right_sql = self.right.to_sql(dialect)
        return f"({left_sql} {self.op} {right_sql})"


class Unary(Condition):
    __slots__ = ("operand", "op")

    def __init__(self, operand: Expression, op: str):
        super().__init__()
        self.operand = operand
        self.op = op

    def get_children(self):
        return [self.operand]

    def to_sql(self, dialect="sqlite"):
        operand_sql = self.operand.to_sql(dialect)
        return f"({self.op} {operand_sql})"


class Predicate(Binary):
    def infer_type(self, schema_context):
        return DataType.build("BOOLEAN")

    def is_deterministic(self):
        return True


class Arithmetic(Binary): ...


class AND(Predicate):
    pass


class OR(Predicate):
    pass


class NOT(Unary):
    pass


class IS(Unary):
    pass


class FunctionCall(Condition):
    pass


class AggFunc(FunctionCall):
    __slots__ = ("operand", "distinct", "ignorenulls", "datatype")

    def __init__(
        self,
        operand: Expression,
        distinct: bool = False,
        ignorenulls: bool = False,
        datatype: Optional[DATATYPE] = None,
    ):
        self.operand = operand
        self.distinct = distinct
        self.ignorenulls = ignorenulls
        self.datatype = DataType.build(datatype) if datatype else None


class Count(AggFunc): ...


class Sum(AggFunc): ...


class Avg(AggFunc): ...


class Min(AggFunc): ...


class Max(AggFunc): ...


class ExpressionVisitor:
    def visit(self, expr: Expression):
        method = "visit_" + expr.__class__.__name__
        return getattr(self, method, self.visit_default)(expr)

    def visit_default(self, expr: Expression):
        raise NotImplementedError(f"No visitor method for {expr.__class__.__name__}")


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
