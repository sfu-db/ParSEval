from __future__ import annotations
from functools import singledispatchmethod
from abc import ABC, abstractmethod
from enum import Enum, auto
from functools import reduce
from typing import TYPE_CHECKING, List, Dict, Callable, Union, Any, Optional, Optional

if TYPE_CHECKING:
    from .expression import Expression, Condition, Schema, Catalog
    from .planner import LogicalPlanVisitor
from .expression import Schema
import uuid

# from dataclasses import dataclass
from abc import ABC, abstractmethod

# from .expression import ColumnRef

import src.parseval.plan.expression as sql_exp


class JoinType(Enum):
    def _generate_next_value_(name, start, count, last_values):
        return name.lower()

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
        name = self.__class__.__name__
        return name[7:] if name.startswith("Logical") else name

    @property
    def id(self) -> str:
        """Return the unique identifier of this operator"""
        return str(self._id)

    @abstractmethod
    def schema(self, catalog: Optional[Catalog]) -> Schema:
        """
        Return the output schema of this operator.

        Returns:
            Schema representing output columns and their types

        Raises:
            SchemaError: If schema cannot be determined
        """
        pass

    @property
    @abstractmethod
    def children(self) -> List["LogicalOperator"]:
        """Return the child operators"""
        pass

    def accept(self, visitor: "LogicalPlanVisitor") -> Any:
        """Accept a visitor for the visitor pattern"""
        return visitor.visit(self)

    def pprint(self, indent: int = 0) -> str:
        """Pretty print the operator tree"""
        pad = "  " * indent
        lines = [f"{pad}{str(self)}"]
        for child in self.children:
            lines.append(child.pprint(indent + 1))
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"{self.operator_type}(id={self.id[:8]})"


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

    def schema(self, catalog):
        if self._schema:
            return self._schema
        self._schema = self.input.schema(catalog)
        return self._schema


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
        self.alias = alias or f"{table_name}_{self.id}"
        self._table_schema = None  # To be set from catalog or external source

    def schema(self, catalog: Catalog):
        if self._schema:
            return self._schema
        table = catalog.get_table(self.table_name)
        columns = []
        for index, col in enumerate(table.schema.columns):
            # data_type=col.datatype,
            #     nullable=col.nullable,
            #     unique=col.unique,
            #     default_value=col.default_value,
            unique = table.is_unique(col.name)
            nullable = table.nullable(col.name)
            datatype = col.datatype
            datatype.nullable = nullable
            columns.append(
                sql_exp.ColumnRef(
                    name=col.name,
                    datatype=datatype,
                    ref=index,
                    table_alias=self.alias,
                    metadata={"table": self.table_name, "unique": unique},
                )
            )
        self._schema = Schema(columns)
        return self._schema

    def __str__(self):
        return f"LogicalScan(table={self.table_name})"

    # def __repr__(self):
    #     return f"LogicalScan(table={self.table_name})"


class LogicalValues(LeafOperator):
    """Represents a VALUES clause with literal values"""

    def __init__(
        self,
        values: List[List[Any]],
        operator_id: Optional[str] = None,
    ):
        super().__init__(operator_id)
        self.values = values

    def schema(self, catalog):
        if self._schema:
            return self._schema

        self._schema = Schema(columns=self.values)

        return self._schema

    def __str__(self):
        return f"LogicalValues({self.values})"

    def __repr__(self):
        return f"LogicalValues({self.values})"


class LogicalProject(UnaryOperator):
    def __init__(
        self,
        input_operator: LogicalOperator,
        expressions: List[Expression],
        aliases: Optional[List[str]] = None,
        operator_id: Optional[str] = None,
    ):
        """
        Create a projection operator.

        Args:
            input_op: Input operator
            expressions: Expressions to compute
            aliases: Output column aliases
            operator_id: Unique identifier
        """
        super().__init__(input_operator, operator_id)
        self.expressions = expressions
        self.aliases = aliases or [f"col_{i}" for i in range(len(expressions))]

        if len(self.expressions) != len(self.aliases):
            raise ValueError("Number of expressions must match number of aliases")

    def schema(self, catalog):
        if self._schema:
            return self._schema
        input_schema = self.input.schema(catalog=catalog)
        columns = []
        for expr, alias in zip(self.expressions, self.aliases):

            def resolve_schema(e):
                if isinstance(e, sql_exp.ColumnRef) and e.ref is not None:
                    return input_schema.columns[e.ref]
                return None

            new_expr = expr.transform(resolve_schema)
            columns.append(new_expr)
        self._schema = Schema(columns)
        return self._schema

    def __str__(self):
        exprs = ", ".join(
            f"{alias}={expr}" for expr, alias in zip(self.expressions, self.aliases)
        )
        return f"LogicalProject({exprs})"


class LogicalFilter(UnaryOperator):
    def __init__(
        self,
        input_operator: LogicalOperator,
        condition: Condition,
        operator_id: Optional[str] = None,
    ):
        super().__init__(input_operator, operator_id)
        self.condition = condition

    def __str__(self):
        return f"LogicalFilter({self.condition})"


class LogicalHaving(LogicalFilter):
    def __str__(self):
        return f"LogicalHaving({self.condition})"


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

    def __str__(self):
        return f"LogicalSort({', '.join([str(s) for s in self.sorts])}, dir={self.dir}, offset={self.offset}, limit={self.limit})"


class LogicalLimit(UnaryOperator):
    def __init__(self, limit, input_operator, operator_id=None):
        super().__init__(input_operator, operator_id)
        self.limit = limit


class LogicalAggregate(UnaryOperator):
    def __init__(
        self,
        keys: List[Expression],
        aggs: List[Expression],
        input_operator: LogicalOperator,
        operator_id: Optional[str] = None,
    ):
        super().__init__(input_operator, operator_id)
        self.keys = keys
        self.aggs = aggs

    def schema(self, catalog):
        if self._schema:
            return self._schema

        input_schema = self.input.schema(catalog)
        columns = []

        for key in self.keys:
            columns.append(input_schema.columns[key.ref])

        def resolve_schema(e):
            if isinstance(e, sql_exp.ColumnRef) and e.ref is not None:
                return input_schema.columns[e.ref]
            return None

        for agg_expr in self.aggs:
            agg = agg_expr.transform(resolve_schema)
            columns.append(agg)

        self._schema = Schema(columns)
        return self._schema

    def __str__(self):
        keys = ", ".join([str(k) for k in self.keys])
        agg_funcs = ", ".join([str(a) for a in self.aggs])

        return f"LogicalAggregate(keys=[{keys}], aggs=[{agg_funcs})]"


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
        self.join_type = JoinType(join_type.lower())
        self.condition = condition

    def schema(self, catalog):
        if self._schema:
            return self._schema

        self._schema = Schema(
            columns=[column for column in self.left.schema(catalog).columns]
            + [column for column in self.right.schema(catalog).columns]
        )
        return self._schema

    def __str__(self):
        return f"LogicalJoin(type={self.join_type.name}, condition={self.condition})"

    def __str__(self):
        return f"LogicalJoin(type={self.join_type.name}, condition={self.condition})"


class CorrelatedSubquery(UnaryOperator):
    """Represents a correlated subquery that references outer query columns"""

    def __init__(self, input_operator, correlation_id=None, operator_id=None):
        super().__init__(input_operator, operator_id)
        self.correlation_id = correlation_id or 0
        self.subquery = input_operator

    def schema(self, catalog):
        return self.subquery.schema(catalog)


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

    def schema(self, catalog):
        if self._schema:
            return self._schema
        self._schema = self.left.schema(catalog)
        return self._schema


class LogicalIntersect(LogicalUnion):
    def __init__(
        self,
        intersect_all: bool,
        left: LogicalOperator,
        right: LogicalOperator,
        operator_id=None,
    ):
        super().__init__(left, right, operator_id)
        self.intersect_all = intersect_all


class LogicalDifference(LogicalUnion):
    def __init__(
        self,
        difference_all: bool,
        left: LogicalOperator,
        right: LogicalOperator,
        operator_id=None,
    ):
        super().__init__(left, right, operator_id)
        self.difference_all = difference_all
