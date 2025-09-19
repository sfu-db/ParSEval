from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Union, Set
from dataclasses import dataclass, field
from enum import Enum
import uuid


class DataType(Enum):
    """Represents different data types in the logical plan"""
    INTEGER = "integer"
    STRING = "string"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"
    NULL = "null"


@dataclass(frozen=True)
class Column:
    """Represents a column in the logical plan"""
    name: str
    data_type: DataType
    nullable: bool = True
    table_alias: Optional[str] = None
    
    @property
    def qualified_name(self) -> str:
        return f"{self.table_alias}.{self.name}" if self.table_alias else self.name


@dataclass(frozen=True)
class Schema:
    """Represents the schema of a logical plan operator"""
    columns: List[Column]
    
    def get_column(self, name: str) -> Optional[Column]:
        for col in self.columns:
            if col.name == name or col.qualified_name == name:
                return col
        return None
    
    def column_names(self) -> List[str]:
        return [col.name for col in self.columns]


class LogicalOperator(ABC):
    """Base class for all logical plan operators"""
    
    def __init__(self, operator_id: Optional[str] = None):
        self.operator_id = operator_id or str(uuid.uuid4())
        self._schema: Optional[Schema] = None
    
    @property
    @abstractmethod
    def operator_type(self) -> str:
        """Return the type of this operator"""
        pass
    
    @property
    @abstractmethod
    def children(self) -> List['LogicalOperator']:
        """Return the child operators"""
        pass
    
    @abstractmethod
    def schema(self) -> Schema:
        """Return the output schema of this operator"""
        pass
    
    @abstractmethod
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        """Accept a visitor for the visitor pattern"""
        pass
    
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
    
    def __init__(self, input_operator: LogicalOperator, operator_id: Optional[str] = None):
        super().__init__(operator_id)
        self.input = input_operator
    
    @property
    def children(self) -> List[LogicalOperator]:
        return [self.input]


class BinaryOperator(LogicalOperator):
    """Base class for operators with exactly two children"""
    
    def __init__(self, left: LogicalOperator, right: LogicalOperator, operator_id: Optional[str] = None):
        super().__init__(operator_id)
        self.left = left
        self.right = right
    
    @property
    def children(self) -> List[LogicalOperator]:
        return [self.left, self.right]


# =============================================================================
# LEAF OPERATORS
# =============================================================================

class TableScan(LeafOperator):
    """Represents scanning a table"""
    
    def __init__(self, table_name: str, table_schema: Schema, 
                 alias: Optional[str] = None, operator_id: Optional[str] = None):
        super().__init__(operator_id)
        self.table_name = table_name
        self.alias = alias or table_name
        self._table_schema = table_schema
    
    @property
    def operator_type(self) -> str:
        return "TableScan"
    
    def schema(self) -> Schema:
        # Apply table alias to all columns
        columns = []
        for col in self._table_schema.columns:
            new_col = Column(
                name=col.name,
                data_type=col.data_type,
                nullable=col.nullable,
                table_alias=self.alias
            )
            columns.append(new_col)
        return Schema(columns)
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_table_scan(self)


class Values(LeafOperator):
    """Represents a VALUES clause with literal values"""
    
    def __init__(self, values: List[List[Any]], column_names: List[str], 
                 operator_id: Optional[str] = None):
        super().__init__(operator_id)
        self.values = values
        self.column_names = column_names
    
    @property
    def operator_type(self) -> str:
        return "Values"
    
    def schema(self) -> Schema:
        # Infer schema from values - simplified version
        columns = []
        for i, col_name in enumerate(self.column_names):
            # This is a simplified type inference
            data_type = DataType.STRING  # Default to string
            if self.values and len(self.values[0]) > i:
                sample_value = self.values[0][i]
                if isinstance(sample_value, int):
                    data_type = DataType.INTEGER
                elif isinstance(sample_value, float):
                    data_type = DataType.FLOAT
                elif isinstance(sample_value, bool):
                    data_type = DataType.BOOLEAN
            
            columns.append(Column(col_name, data_type))
        return Schema(columns)
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_values(self)


# =============================================================================
# UNARY OPERATORS
# =============================================================================

@dataclass(frozen=True)
class Expression(ABC):
    """Base class for expressions in the logical plan"""
    
    @abstractmethod
    def evaluate_type(self, input_schema: Schema) -> DataType:
        """Return the data type this expression evaluates to"""
        pass


@dataclass(frozen=True)
class ColumnRef(Expression):
    """References a column"""
    column_name: str
    table_alias: Optional[str] = None
    
    def evaluate_type(self, input_schema: Schema) -> DataType:
        qualified_name = f"{self.table_alias}.{self.column_name}" if self.table_alias else self.column_name
        col = input_schema.get_column(qualified_name) or input_schema.get_column(self.column_name)
        return col.data_type if col else DataType.NULL


@dataclass(frozen=True)
class Literal(Expression):
    """Represents a literal value"""
    value: Any
    data_type: DataType
    
    def evaluate_type(self, input_schema: Schema) -> DataType:
        return self.data_type


class Projection(UnaryOperator):
    """Represents a SELECT clause with specific columns"""
    
    def __init__(self, input_operator: LogicalOperator, 
                 expressions: List[Expression], 
                 aliases: Optional[List[str]] = None,
                 operator_id: Optional[str] = None):
        super().__init__(input_operator, operator_id)
        self.expressions = expressions
        self.aliases = aliases or [f"col_{i}" for i in range(len(expressions))]
        
        if len(self.expressions) != len(self.aliases):
            raise ValueError("Number of expressions must match number of aliases")
    
    @property
    def operator_type(self) -> str:
        return "Projection"
    
    def schema(self) -> Schema:
        input_schema = self.input.schema()
        columns = []
        
        for expr, alias in zip(self.expressions, self.aliases):
            data_type = expr.evaluate_type(input_schema)
            columns.append(Column(alias, data_type))
        
        return Schema(columns)
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_projection(self)


class Filter(UnaryOperator):
    """Represents a WHERE clause"""
    
    def __init__(self, input_operator: LogicalOperator, 
                 condition: Expression, operator_id: Optional[str] = None):
        super().__init__(input_operator, operator_id)
        self.condition = condition
    
    @property
    def operator_type(self) -> str:
        return "Filter"
    
    def schema(self) -> Schema:
        # Filter doesn't change the schema
        return self.input.schema()
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_filter(self)


class Sort(UnaryOperator):
    """Represents ORDER BY clause"""
    
    @dataclass(frozen=True)
    class SortKey:
        expression: Expression
        ascending: bool = True
        nulls_first: bool = False
    
    def __init__(self, input_operator: LogicalOperator, 
                 sort_keys: List[SortKey], operator_id: Optional[str] = None):
        super().__init__(input_operator, operator_id)
        self.sort_keys = sort_keys
    
    @property
    def operator_type(self) -> str:
        return "Sort"
    
    def schema(self) -> Schema:
        # Sort doesn't change the schema
        return self.input.schema()
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_sort(self)


class Limit(UnaryOperator):
    """Represents LIMIT clause"""
    
    def __init__(self, input_operator: LogicalOperator, 
                 limit: int, offset: int = 0, operator_id: Optional[str] = None):
        super().__init__(input_operator, operator_id)
        self.limit = limit
        self.offset = offset
    
    @property
    def operator_type(self) -> str:
        return "Limit"
    
    def schema(self) -> Schema:
        # Limit doesn't change the schema
        return self.input.schema()
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_limit(self)


# =============================================================================
# BINARY OPERATORS
# =============================================================================

class JoinType(Enum):
    INNER = "inner"
    LEFT = "left"
    RIGHT = "right"
    FULL = "full"
    CROSS = "cross"


class Join(BinaryOperator):
    """Represents JOIN operations"""
    
    def __init__(self, left: LogicalOperator, right: LogicalOperator,
                 join_type: JoinType, condition: Optional[Expression] = None,
                 operator_id: Optional[str] = None):
        super().__init__(left, right, operator_id)
        self.join_type = join_type
        self.condition = condition
    
    @property
    def operator_type(self) -> str:
        return f"{self.join_type.value.title()}Join"
    
    def schema(self) -> Schema:
        left_schema = self.left.schema()
        right_schema = self.right.schema()
        
        # Combine schemas from both sides
        columns = left_schema.columns + right_schema.columns
        return Schema(columns)
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_join(self)


class Union(BinaryOperator):
    """Represents UNION operations"""
    
    def __init__(self, left: LogicalOperator, right: LogicalOperator,
                 is_all: bool = False, operator_id: Optional[str] = None):
        super().__init__(left, right, operator_id)
        self.is_all = is_all  # UNION ALL vs UNION
    
    @property
    def operator_type(self) -> str:
        return "UnionAll" if self.is_all else "Union"
    
    def schema(self) -> Schema:
        # Union uses the schema from the left side
        # In practice, you'd want to validate that both sides are compatible
        return self.left.schema()
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_union(self)


# =============================================================================
# AGGREGATE OPERATORS
# =============================================================================

@dataclass(frozen=True)
class AggregateFunction(Expression):
    """Represents an aggregate function"""
    function_name: str
    arguments: List[Expression]
    distinct: bool = False
    
    def evaluate_type(self, input_schema: Schema) -> DataType:
        # Simplified type inference for aggregates
        if self.function_name.lower() in ['count']:
            return DataType.INTEGER
        elif self.function_name.lower() in ['sum', 'avg', 'min', 'max']:
            if self.arguments:
                return self.arguments[0].evaluate_type(input_schema)
            return DataType.NULL
        return DataType.NULL


class Aggregate(UnaryOperator):
    """Represents GROUP BY with aggregate functions"""
    
    def __init__(self, input_operator: LogicalOperator,
                 group_by_expressions: List[Expression],
                 aggregate_expressions: List[AggregateFunction],
                 aggregate_aliases: Optional[List[str]] = None,
                 operator_id: Optional[str] = None):
        super().__init__(input_operator, operator_id)
        self.group_by_expressions = group_by_expressions
        self.aggregate_expressions = aggregate_expressions
        self.aggregate_aliases = aggregate_aliases or [
            f"agg_{i}" for i in range(len(aggregate_expressions))
        ]
    
    @property
    def operator_type(self) -> str:
        return "Aggregate"
    
    def schema(self) -> Schema:
        input_schema = self.input.schema()
        columns = []
        
        # Add group by columns
        for i, expr in enumerate(self.group_by_expressions):
            if isinstance(expr, ColumnRef):
                col_name = expr.column_name
                data_type = expr.evaluate_type(input_schema)
            else:
                col_name = f"group_{i}"
                data_type = expr.evaluate_type(input_schema)
            columns.append(Column(col_name, data_type))
        
        # Add aggregate columns
        for expr, alias in zip(self.aggregate_expressions, self.aggregate_aliases):
            data_type = expr.evaluate_type(input_schema)
            columns.append(Column(alias, data_type))
        
        return Schema(columns)
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_aggregate(self)


# =============================================================================
# VISITOR PATTERN
# =============================================================================

class LogicalPlanVisitor(ABC):
    """Abstract visitor for traversing logical plans"""
    
    @abstractmethod
    def visit_table_scan(self, op: TableScan) -> Any:
        pass
    
    @abstractmethod
    def visit_values(self, op: Values) -> Any:
        pass
    
    @abstractmethod
    def visit_projection(self, op: Projection) -> Any:
        pass
    
    @abstractmethod
    def visit_filter(self, op: Filter) -> Any:
        pass
    
    @abstractmethod
    def visit_sort(self, op: Sort) -> Any:
        pass
    
    @abstractmethod
    def visit_limit(self, op: Limit) -> Any:
        pass
    
    @abstractmethod
    def visit_join(self, op: Join) -> Any:
        pass
    
    @abstractmethod
    def visit_union(self, op: Union) -> Any:
        pass
    
    @abstractmethod
    def visit_aggregate(self, op: Aggregate) -> Any:
        pass


class LogicalPlanPrinter(LogicalPlanVisitor):
    """Example visitor that prints the logical plan"""
    
    def __init__(self, indent: int = 0):
        self.indent = indent
    
    def _print(self, text: str) -> str:
        return "  " * self.indent + text
    
    def visit_table_scan(self, op: TableScan) -> str:
        return self._print(f"TableScan: {op.table_name} AS {op.alias}")
    
    def visit_values(self, op: Values) -> str:
        return self._print(f"Values: {len(op.values)} rows")
    
    def visit_projection(self, op: Projection) -> str:
        result = self._print(f"Projection: {op.aliases}")
        child_visitor = LogicalPlanPrinter(self.indent + 1)
        result += "\n" + op.input.accept(child_visitor)
        return result
    
    def visit_filter(self, op: Filter) -> str:
        result = self._print("Filter: <condition>")
        child_visitor = LogicalPlanPrinter(self.indent + 1)
        result += "\n" + op.input.accept(child_visitor)
        return result
    
    def visit_sort(self, op: Sort) -> str:
        result = self._print(f"Sort: {len(op.sort_keys)} keys")
        child_visitor = LogicalPlanPrinter(self.indent + 1)
        result += "\n" + op.input.accept(child_visitor)
        return result
    
    def visit_limit(self, op: Limit) -> str:
        result = self._print(f"Limit: {op.limit} offset {op.offset}")
        child_visitor = LogicalPlanPrinter(self.indent + 1)
        result += "\n" + op.input.accept(child_visitor)
        return result
    
    def visit_join(self, op: Join) -> str:
        result = self._print(f"{op.operator_type}: <condition>")
        child_visitor = LogicalPlanPrinter(self.indent + 1)
        result += "\n" + op.left.accept(child_visitor)
        result += "\n" + op.right.accept(child_visitor)
        return result
    
    def visit_union(self, op: Union) -> str:
        result = self._print(f"{op.operator_type}")
        child_visitor = LogicalPlanPrinter(self.indent + 1)
        result += "\n" + op.left.accept(child_visitor)
        result += "\n" + op.right.accept(child_visitor)
        return result
    
    def visit_aggregate(self, op: Aggregate) -> str:
        result = self._print(f"Aggregate: {len(op.group_by_expressions)} groups, {len(op.aggregate_expressions)} aggs")
        child_visitor = LogicalPlanPrinter(self.indent + 1)
        result += "\n" + op.input.accept(child_visitor)
        return result


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    # Create a sample schema
    users_schema = Schema([
        Column("id", DataType.INTEGER, False),
        Column("name", DataType.STRING),
        Column("age", DataType.INTEGER)
    ])
    
    orders_schema = Schema([
        Column("id", DataType.INTEGER, False),
        Column("user_id", DataType.INTEGER),
        Column("amount", DataType.FLOAT)
    ])
    
    # Build a logical plan: SELECT u.name, SUM(o.amount) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.name
    users_scan = TableScan("users", users_schema, "u")
    orders_scan = TableScan("orders", orders_schema, "o")
    
    # Join condition: u.id = o.user_id
    join_condition = None  # Simplified for this example
    join = Join(users_scan, orders_scan, JoinType.INNER, join_condition)
    
    # Group by u.name and aggregate SUM(o.amount)
    group_by_expr = [ColumnRef("name", "u")]
    agg_expr = [AggregateFunction("sum", [ColumnRef("amount", "o")])]
    aggregate = Aggregate(join, group_by_expr, agg_expr, ["total_amount"])
    
    # Project final columns
    final_projection = Projection(
        aggregate,
        [ColumnRef("name", "u"), ColumnRef("total_amount")],
        ["user_name", "total_spent"]
    )
    
    # Print the logical plan
    printer = LogicalPlanPrinter()
    print("Logical Plan:")
    print(final_projection.accept(printer))