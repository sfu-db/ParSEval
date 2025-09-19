from __future__ import annotations
from functools import singledispatch
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import sqlglot
import uuid
from sqlglot import expressions as exp
from sqlglot.expressions import DATA_TYPE

@dataclass(frozen=True)
class Column:
    """Represents a column in the logical plan"""
    name: str
    data_type: DATA_TYPE
    nullable: bool = True
    unique: bool = False
    default_value: Optional[Any] = None
    table_alias: Optional[str] = None
    
    @property
    def qualified_name(self) -> str:
        return f"{self.table_alias}.{self.name}" if self.table_alias else self.name
    
    def __str__(self):
        return self.qualified_name


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

class Table:
    ...

class Catalog:
    # function_name -> metadata    
    functions : Dict[str, Dict[str, Any]] = {
        'COUNT': {'is_aggregate': True, 'return_type': exp.DataType.Type.INT},
        'SUM': {'is_aggregate': True, 'return_type': exp.DataType.Type.FLOAT},
        'AVG': {'is_aggregate': True, 'return_type': exp.DataType.Type.FLOAT},
        'MIN': {'is_aggregate': True, 'return_type': exp.DataType.Type.UNKNOWN},
        'MAX': {'is_aggregate': True, 'return_type': exp.DataType.Type.UNKNOWN},
        'UPPER': {'is_aggregate': False, 'return_type': exp.DataType.Type.TEXT},
        'LOWER': {'is_aggregate': False, 'return_type': exp.DataType.Type.TEXT},
        'LENGTH': {'is_aggregate': False, 'return_type': exp.DataType.Type.INT}
    }  
    def __init__(self, tables: Dict[str, Table] = None):
        self.tables: Dict[str, Any] = tables or {}
        
    def register_table(self, table_info: Table):
        """Register a table in the catalog"""
        self.tables[table_info.name] = table_info
    
    def get_table(self, name: str) -> Optional[Table]:
        """Get table information by name"""
        return self.tables.get(name)
    def resolve_column(self, column_name: str, table_context: Optional[str] = None) -> Optional[Column]:
        """Resolve a column reference to its full information"""
        if table_context:
            table = self.get_table(table_context)
            if table:
                return table.get_column(column_name)
        
        # If no table context, search all tables (ambiguous if found in multiple)
        matches = []
        for table in self.tables.values():
            col = table.get_column(column_name)
            if col:
                matches.append(col)
        
        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            raise ValueError(f"Ambiguous column reference: {column_name}")
        
        return None
    
    def get_function_info(self, func_name: str) -> Optional[Dict[str, Any]]:
        """Get function metadata"""
        return self.functions.get(func_name.upper())
    def __str__(self):
        return f"Catalog(tables={list(self.tables.keys())})"   


class JoinType(Enum):
    INNER = "INNER"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    FULL = "FULL"
    CROSS = "CROSS"

class AggregateFunction(Enum):
    COUNT = "COUNT"
    SUM = "SUM"
    AVG = "AVG"
    MIN = "MIN"
    MAX = "MAX"


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
    def __init__(self, operator_id = None):
        super().__init__(operator_id)

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




class LogicalPlan(ABC):
    """Base class for all logical plan nodes"""
    
    def __init__(self, catalog: Optional[Catalog] = None):
        self.output_schema: List[ColumnInfo] = []
        self.children: List['LogicalPlan'] = []
        self.catalog = catalog
        self.correlation_id: Optional[str] = None  # For correlated subqueries
    
    @abstractmethod
    def accept(self, visitor):
        pass
    
    def add_child(self, child: 'LogicalPlan'):
        self.children.append(child)
        if self.catalog and not child.catalog:
            child.catalog = self.catalog
    
    def get_output_schema(self) -> List[ColumnInfo]:
        return self.output_schema
    def resolve_column_reference(self, column_ref: ColumnReference) -> ColumnInfo:
        """Resolve column reference using catalog and current schema"""
        if self.catalog:
            table_context = column_ref.column.table_alias
            resolved = self.catalog.resolve_column(column_ref.column.name, table_context)
            if resolved:
                column_ref.column.column_info = resolved
                column_ref.resolved_type = resolved.data_type
                return resolved
        
        # Fallback: look in current output schema
        for col_info in self.output_schema:
            if (col_info.name == column_ref.column.name and
                (not column_ref.column.table_alias or 
                 col_info.table_alias == column_ref.column.table_alias)):
                return col_info
        
        raise ValueError(f"Cannot resolve column: {column_ref.column}")
    
    
class LogicalScan(LogicalPlan):
    def __init__(self, table_name: str, alias: Optional[str] = None, 
                 catalog: Optional[Catalog] = None):
        super().__init__(catalog)
        self.table_name = table_name
        self.alias = alias or table_name

    
    def accept(self, visitor):
        return visitor.visit_logical_scan(self)
    
class LogicalFilter(LogicalPlan):
    def __init__(self, condition: Expression, child: LogicalPlan):
        super().__init__(child.catalog)
        self.condition = condition
        self.add_child(child)
        self.output_schema = child.get_output_schema().copy()
    
    def accept(self, visitor):
        return visitor.visit_logical_filter(self)

class LogicalProjection(LogicalPlan):
    def __init__(self, expressions: List[Expression], child: LogicalPlan):
        super().__init__(child.catalog)
        self.expressions = expressions
        self.add_child(child)
    
    def accept(self, visitor):
        return visitor.visit_logical_projection(self)
    

class LogicalJoin(LogicalPlan):
    def __init__(self, join_type: JoinType, condition: Optional[Expression], 
                 left: LogicalPlan, right: LogicalPlan):
        super().__init__(left.catalog)
        self.join_type = join_type
        self.condition = condition
        self.add_child(left)
        self.add_child(right)
        
        # Combine schemas from both sides
        self.output_schema = left.get_output_schema().copy() + right.get_output_schema().copy()
    
    def accept(self, visitor):
        return visitor.visit_join(self)
    

from functools import singledispatchmethod
class Planner:
    def __init__(self, catalog: Optional[Catalog] = None):
        self.catalog = catalog or Catalog()
        self.table_aliases = {}
        self.correlation_stack = []  # Track nested correlation contexts
        self.correlation_counter = 0
        
    def explain(self, sql: str, dialect: str = "postgres") -> LogicalPlan:
        """Main entry point: parse SQL and convert to logical plan"""
        try:
            parsed = sqlglot.parse_one(sql, dialect=dialect)
            if not parsed:
                raise ValueError("Failed to parse SQL")
            
            return self._convert_expression(parsed)
        except Exception as e:
            raise ValueError(f"Error converting SQL to logical plan: {e}")
    
    @singledispatchmethod
    def _convert(self, expr, catalog, **kwargs) -> LogicalPlan:
        raise TypeError(type(expr))
    
    @_convert.register
    def _(self, expr: exp.Select, catalog, **kwargs):
        """Enhanced SELECT conversion with correlation handling"""
        
        from_ = expr.args.get("from")
        
        if not from_:
            raise ValueError("No FROM clause found")
        
        joins = expr.args.get("joins")
        
        plan = self._convert(from_, catalog, joins = joins)
        
        ...
        
    @_convert.register
    def _(self, expr: exp.Union, catalog, **kwargs):
        ...
        
    @_convert.register
    def _(self, expr: exp.From, catalog, **kwargs):
        """Enhanced table reference conversion with catalog lookup"""
        # from sqlglot.planner import Plan
        if isinstance(expr, exp.Table):
            table_name = expr.name
            alias = expr.alias if expr.alias else table_name
            self.table_aliases[alias] = table_name
            return LogicalScan(table_name, alias, catalog= catalog)
        elif isinstance(expr, exp.Subquery):
            subquery_plan = self._convert(expr.this)
            return subquery_plan
        else:
            raise NotImplementedError(f"Table reference type {type(expr)} not supported")
        
    
        
    
    