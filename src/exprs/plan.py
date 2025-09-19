from __future__ import annotations
from functools import singledispatchmethod
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import sqlglot
import uuid
from sqlglot import expressions as exp
from sqlglot.expressions import DATA_TYPE
from .catalog import Column, Schema, Catalog
from .expression import Expression, ColumnRef, Literal, Condition

class JoinType(Enum):
    INNER = "INNER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    FULL = "FULL"
    CROSS = "CROSS"

class LogicalOperator(ABC):
    def __init__(self, operator_id: Optional[str] = None):
        self.operator_id = operator_id or str(uuid.uuid4())
        self._schema: Optional[Schema] = None
    
    @property
    def operator_type(self) -> str:
        """Return the type of this operator"""
        return self.__class__.__name__
    
    @abstractmethod
    def schema(self) -> Schema:
        """Return the output schema of this operator"""
        pass
    
    @property
    @abstractmethod
    def children(self) -> List['LogicalOperator']:
        """Return the child operators"""
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

class LogicalScan(LeafOperator):
    def __init__(self, table_name: str, alias: Optional[str] = None, operator_id: Optional[str] = None):
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
                table_alias=self.alias
            )
            columns.append(new_col)
        return Schema(columns)
    
    def accept(self, visitor):
        return visitor.visit_table_scan(self)

class LogicalValues(LeafOperator):
    """Represents a VALUES clause with literal values"""
    
    def __init__(self, values: List[List[Any]], column_names: List[str], 
                 operator_id: Optional[str] = None):
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
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_values(self)

class LogicalProjection(UnaryOperator):
    def __init__(self, input_operator: LogicalOperator, 
                 expressions: List[Expression], 
                 aliases: Optional[List[str]] = None,
                 operator_id: Optional[str] = None):
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


    def accept(self, visitor):
        return visitor.visit_projection(self)

class LogicalFilter(UnaryOperator):
    def __init__(self, input_operator: LogicalOperator, 
                 condition: Condition, operator_id: Optional[str] = None):
        super().__init__(input_operator, operator_id)
        self.condition = condition
    
    def schema(self) -> Schema:
        # Filter doesn't change the schema
        return self.input.schema()
    
    def accept(self, visitor: 'LogicalPlanVisitor') -> Any:
        return visitor.visit_filter(self)




# =============================================================================
# VISITOR PATTERN
# =============================================================================

class LogicalPlanVisitor(ABC):
    """Abstract visitor for traversing logical plans"""
    
    @abstractmethod
    def visit_table_scan(self, op: LogicalTableScan) -> Any:
        pass
    
    @abstractmethod
    def visit_values(self, op: LogicalValues) -> Any:
        pass
    
    @abstractmethod
    def visit_projection(self, op: LogicalProjection) -> Any:
        pass
    
    @abstractmethod
    def visit_filter(self, op: LogicalFilter) -> Any:
        pass
    
    @abstractmethod
    def visit_sort(self, op: LogicalSort) -> Any:
        pass
    
    @abstractmethod
    def visit_limit(self, op: LogicalLimit) -> Any:
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




# class LogicalPlan(ABC):
#     """Base class for all logical plan nodes"""
    
#     def __init__(self, catalog: Optional[Catalog] = None):
#         self.output_schema: List[ColumnInfo] = []
#         self.children: List['LogicalPlan'] = []
#         self.catalog = catalog
#         self.correlation_id: Optional[str] = None  # For correlated subqueries
    
#     @abstractmethod
#     def accept(self, visitor):
#         pass
    
#     def add_child(self, child: 'LogicalPlan'):
#         self.children.append(child)
#         if self.catalog and not child.catalog:
#             child.catalog = self.catalog
    
#     def get_output_schema(self) -> List[ColumnInfo]:
#         return self.output_schema
#     def resolve_column_reference(self, column_ref: ColumnReference) -> ColumnInfo:
#         """Resolve column reference using catalog and current schema"""
#         if self.catalog:
#             table_context = column_ref.column.table_alias
#             resolved = self.catalog.resolve_column(column_ref.column.name, table_context)
#             if resolved:
#                 column_ref.column.column_info = resolved
#                 column_ref.resolved_type = resolved.data_type
#                 return resolved
        
#         # Fallback: look in current output schema
#         for col_info in self.output_schema:
#             if (col_info.name == column_ref.column.name and
#                 (not column_ref.column.table_alias or 
#                  col_info.table_alias == column_ref.column.table_alias)):
#                 return col_info
        
#         raise ValueError(f"Cannot resolve column: {column_ref.column}")
    
    
# class LogicalFilter(LogicalPlan):
#     def __init__(self, condition: Expression, child: LogicalPlan):
#         super().__init__(child.catalog)
#         self.condition = condition
#         self.add_child(child)
#         self.output_schema = child.get_output_schema().copy()
    
#     def accept(self, visitor):
#         return visitor.visit_logical_filter(self)

# class LogicalProjection(LogicalPlan):
#     def __init__(self, expressions: List[Expression], child: LogicalPlan):
#         super().__init__(child.catalog)
#         self.expressions = expressions
#         self.add_child(child)
    
#     def accept(self, visitor):
#         return visitor.visit_logical_projection(self)
    

# class LogicalJoin(LogicalPlan):
#     def __init__(self, join_type: JoinType, condition: Optional[Expression], 
#                  left: LogicalPlan, right: LogicalPlan):
#         super().__init__(left.catalog)
#         self.join_type = join_type
#         self.condition = condition
#         self.add_child(left)
#         self.add_child(right)
        
#         # Combine schemas from both sides
#         self.output_schema = left.get_output_schema().copy() + right.get_output_schema().copy()
    
#     def accept(self, visitor):
#         return visitor.visit_join(self)
    


class Planner:
    def __init__(self, catalog: Optional[Catalog] = None):
        self.catalog = catalog or Catalog()
        self.table_aliases = {}
        self.correlation_stack = []  # Track nested correlation contexts
        self.correlation_counter = 0
        
    def explain(self, sql: str, dialect: str = "postgres") -> LogicalOperator:
        """Main entry point: parse SQL and convert to logical plan"""
        try:
            parsed = sqlglot.parse_one(sql, dialect=dialect)
            if not parsed:
                raise ValueError("Failed to parse SQL")
            
            return self._convert_expression(parsed)
        except Exception as e:
            raise ValueError(f"Error converting SQL to logical plan: {e}")
    
    @singledispatchmethod
    def _convert(self, expr, **kwargs) -> LogicalOperator:
        raise TypeError(type(expr))
    
    @_convert.register
    def _(self, expr: exp.Select, **kwargs):
        """Enhanced SELECT conversion with correlation handling"""
        
        from_ = expr.args.get("from")
        
        if not from_:
            raise ValueError("No FROM clause found")
        
        joins = expr.args.get("joins")
        
        plan = self._convert(from_, joins = joins)
        
        where = expr.args.get("where")
        if where:
            where_expr = where.this
            condition = where_expr
            plan = LogicalFilter(plan, condition)
        
    @_convert.register
    def _(self, expr: exp.Union,  **kwargs):
        ...
        
    @_convert.register
    def _(self, expr: exp.From, **kwargs):
        """Enhanced table reference conversion with catalog lookup"""
        # from sqlglot.planner import Plan
        if isinstance(expr, exp.Table):
            table_name = expr.name
            alias = expr.alias if expr.alias else table_name
            if table_name in self.cte_definitions:
                cte_schema = self.cte_definitions[table_name]
                raise ValueError("CTE support not implemented")
            
            self.table_aliases[alias] = table_name
            op = LogicalScan(table_name= table_name, alias= alias)
            op._table_schema = self.catalog.get_table(table_name).schema
            return op
            
        elif isinstance(expr, exp.Subquery):
            subquery_plan = self._convert(expr.this)
            return subquery_plan
        else:
            raise NotImplementedError(f"Table reference type {type(expr)} not supported")
        
    @_convert.register
    def _(self, expr: exp.Where, **kwargs):
        
        ...
    
    @_convert.register
    def _(self, expr: exp.Condition, **kwargs):
        
        ...
    
