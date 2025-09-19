class SQLglotToLogicalPlanConverter:
    """Enhanced converter with catalog support, correlated subquery and CTE handling"""
    
    def __init__(self, catalog: Optional[Catalog] = None):
        self.catalog = catalog or Catalog()
        self.table_aliases = {}
        self.correlation_stack = []
        self.correlation_counter = 0
        self.cte_definitions = {}  # Track CTE definitions in current query
    
    def convert(self, sql: str, dialect: str = "postgres") -> LogicalPlan:
        """Main entry point: parse SQL and convert to logical plan"""
        try:
            parsed = sqlglot.parse_one(sql, dialect=dialect)
            if not parsed:
                raise ValueError("Failed to parse SQL")
            
            # Reset CTE definitions for new query
            self.cte_definitions = {}
            
            return self._convert_expression(parsed)
        except Exception as e:
            raise ValueError(f"Error converting SQL to logical plan: {e}")
    
    def _convert_expression(self, expr: exp.Expression) -> LogicalPlan:
        """Convert a SQLglot expression to logical plan node"""
        if isinstance(expr, exp.Select):
            # Check for WITH clause first
            if expr.find(exp.With):
                return self._convert_with(expr)
            else:
                return self._convert_select(expr)
        elif isinstance(expr, exp.Union):
            return self._convert_union(expr)
        else:
            raise NotImplementedError(f"Conversion not implemented for {type(expr)}")
    
    def _convert_with(self, select_with_cte: exp.Select) -> LogicalPlan:
        """Convert WITH clause (CTEs)"""
        with_clause = select_with_cte.find(exp.With)
        if not with_clause:
            return self._convert_select(select_with_cte)
        
        # Process CTE definitions
        cte_definitions = []
        
        for cte in with_clause.expressions:
            if isinstance(cte, exp.CTE):
                cte_name = cte.alias
                cte_query = cte.this
                
                # Convert CTE query to logical plan
                cte_plan = self._convert_expression(cte_query)
                
                # Store CTE definition for later reference
                self.cte_definitions[cte_name] = cte_plan.get_output_schema()
                cte_definitions.append((cte_name, cte_plan))
        
        # Convert main query
        main_query = self._convert_select(select_with_cte)
        
        # Create WITH node
        return With(cte_definitions, main_query)
    
    def _convert_select(self, select: exp.Select) -> LogicalPlan:
        """Enhanced SELECT conversion with window function and CTE support"""
        
        # Check if this is a correlated subquery
        is_correlated = self._is_correlated_subquery(select)
        correlation_id = None
        
        if is_correlated:
            correlation_id = f"corr_{self.correlation_counter}"
            self.correlation_counter += 1
            self.correlation_stack.append(correlation_id)
        
        try:
            # Build plan starting from FROM clause
            plan = self._convert_from_clause(select)
            
            # Apply WHERE clause
            if select.find(exp.Where):
                where_expr = select.find(exp.Where)
                condition = self._convert_sql_expression(where_expr.this)
                plan = Filter(condition, plan)
            
            # Handle GROUP BY and aggregates
            group_by_exprs = []
            if select.find(exp.Group):
                group_by = select.find(exp.Group)
                for expr in group_by.expressions:
                    group_by_exprs.append(self._convert_sql_expression(expr))
            
            # Process SELECT expressions, separating regular, aggregate, and window functions
            select_exprs = []
            aggregate_exprs = []
            window_exprs = []
            has_aggregates = False
            has_windows = False
            
            for expr in select.expressions:
                converted_expr = self._convert_sql_expression(expr)
                select_exprs.append(converted_expr)
                
                if self._is_window_expression(converted_expr):
                    window_exprs.append(converted_expr)
                    has_windows = True
                elif self._is_aggregate_expression(converted_expr):
                    aggregate_exprs.append(converted_expr)
                    has_aggregates = True
            
            # Add aggregate node if needed
            if has_aggregates or group_by_exprs:
                plan = Aggregate(group_by_exprs, aggregate_exprs, plan)
            
            # Add window node if needed
            if has_windows:
                # Extract partition by and order by from window expressions
                # This is simplified - real implementation would parse OVER clauses
                plan = Window(window_exprs, [], [], plan)
            
            # Add projection for SELECT clause
            plan = Projection(select_exprs, plan)
            
            # Handle HAVING clause (comes after GROUP BY)
            if select.find(exp.Having):
                having_expr = select.find(exp.Having)
                having_condition = self._convert_sql_expression(having_expr.this)
                plan = Filter(having_condition, plan)
            
            # Handle ORDER BY
            if select.find(exp.Order):
                order = select.find(exp.Order)
                sort_exprs = []
                for expr in order.expressions:
                    sql_expr = self._convert_sql_expression(expr.this)
                    is_desc = isinstance(expr, exp.Ordered) and expr.desc
                    sort_exprs.append((sql_expr, not is_desc))
                plan = Sort(sort_exprs, plan)
            
            # Handle LIMIT/OFFSET
            if select.find(exp.Limit):
                limit = select.find(exp.Limit)
                count = int(limit.expression.this) if limit.expression else 0
                offset = int(limit.offset.this) if limit.offset else 0
                plan = Limit(count, offset, plan)
            
            # Wrap in CorrelatedSubquery if needed
            if is_correlated:
                correlated_cols = self._find_correlated_columns(select)
                plan = CorrelatedSubquery(plan, correlated_cols, correlation_id)
            
            return plan
            
        finally:
            if is_correlated:
                self.correlation_stack.pop()
    
    def _convert_table_reference(self, table_ref: exp.Expression) -> LogicalPlan:
        """Enhanced table reference conversion with CTE support"""
        if isinstance(table_ref, exp.Table):
            table_name = table_ref.name
            alias = table_ref.alias if table_ref.alias else table_name
            
            # Check if this is a CTE reference
            if table_name in self.cte_definitions:
                cte_schema = self.cte_definitions[table_name]
                return CTEReference(table_name, cte_schema, self.catalog)
            
            # Regular table reference
            self.table_aliases[alias] = table_name
            return TableScan(table_name, alias, catalog=self.catalog)
        
        elif isinstance(table_ref, exp.Subquery):
            subquery_plan = self._convert_expression(table_ref.this)
            return subquery_plan
        
        else:
            raise NotImplementedError(f"Table reference type {type(table_ref)} not supported")
    
    def _convert_sql_expression(self, expr: exp.Expression) -> Expression:
        """Enhanced expression conversion with more function support"""
        
        if isinstance(expr, exp.Column):
            column = Column(expr.name, expr.table if expr.table else None)
            
            # Check if this is a correlated reference
            if self._is_correlated_column_reference(expr):
                return CorrelatedColumnReference(column, correlation_depth=len(self.correlation_stack))
            
            col_ref = ColumnReference(column)
            
            # Try to resolve type from catalog or CTE definitions
            if self.catalog:
                try:
                    table_context = expr.table or None
                    resolved = self.catalog.resolve_column(expr.name, table_context)
                    if resolved:
                        col_ref.resolved_type = resolved.data_type
                        column.column_info = resolved
                except ValueError:
                    pass
            
            return col_ref
        
        elif isinstance(expr, exp.Literal):
            data_type = self._infer_literal_type(expr.this)
            return Literal(expr.this, data_type)
        
        elif isinstance(expr, exp.Binary):
            left = self._convert_sql_expression(expr.left)
            right = self._convert_sql_expression(expr.right)
            operator = self._convert_operator(expr)
            
            bin_op = BinaryOperation(left, operator, right)
            bin_op.resolved_type = self._infer_binary_operation_type(left, right, operator)
            return bin_op
        
        elif isinstance(expr, exp.Func):
            func_name = self._get_function_name(expr)
            args = [self._convert_sql_expression(arg) for arg in expr.expressions]
            
            # Get function info from catalog
            func_info = self.catalog.get_function_info(func_name) if self.catalog else None
            is_agg = func_info.get('is_aggregate', False) if func_info else self._is_builtin_aggregate(func_name)
            
            func_call = FunctionCall(func_name, args, is_agg)
            if func_infofrom abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union, Set, Tuple
from dataclasses import dataclass, field
from enum import Enum
import sqlglot
from sqlglot import expressions as exp

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

class DataType(Enum):
    INTEGER = "INTEGER"
    BIGINT = "BIGINT"
    FLOAT = "FLOAT"
    DOUBLE = "DOUBLE"
    STRING = "STRING"
    VARCHAR = "VARCHAR"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    DECIMAL = "DECIMAL"
    UNKNOWN = "UNKNOWN"

@dataclass
class ColumnInfo:
    """Extended column information with schema details"""
    name: str
    data_type: DataType
    table_name: Optional[str] = None
    table_alias: Optional[str] = None
    is_nullable: bool = True
    is_primary_key: bool = False
    default_value: Optional[Any] = None
    
    def qualified_name(self) -> str:
        prefix = self.table_alias or self.table_name
        return f"{prefix}.{self.name}" if prefix else self.name

@dataclass
class TableInfo:
    """Table schema information"""
    name: str
    columns: List[ColumnInfo]
    primary_keys: List[str] = field(default_factory=list)
    foreign_keys: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # col -> (table, col)
    
    def get_column(self, name: str) -> Optional[ColumnInfo]:
        return next((col for col in self.columns if col.name == name), None)

class Catalog:
    """Schema catalog for tracking table and column information"""
    
    def __init__(self):
        self.tables: Dict[str, TableInfo] = {}
        self.functions: Dict[str, Dict[str, Any]] = {}  # function_name -> metadata
        self._init_builtin_functions()
    
    def register_table(self, table_info: TableInfo):
        """Register a table in the catalog"""
        self.tables[table_info.name] = table_info
    
    def get_table(self, name: str) -> Optional[TableInfo]:
        """Get table information by name"""
        return self.tables.get(name)
    
    def resolve_column(self, column_name: str, table_context: Optional[str] = None) -> Optional[ColumnInfo]:
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
    
    def _init_builtin_functions(self):
        """Initialize built-in SQL functions"""
        aggregates = {
            'COUNT': {'is_aggregate': True, 'return_type': DataType.BIGINT},
            'SUM': {'is_aggregate': True, 'return_type': DataType.DOUBLE},
            'AVG': {'is_aggregate': True, 'return_type': DataType.DOUBLE},
            'MIN': {'is_aggregate': True, 'return_type': DataType.UNKNOWN},
            'MAX': {'is_aggregate': True, 'return_type': DataType.UNKNOWN},
        }
        
        scalars = {
            'UPPER': {'is_aggregate': False, 'return_type': DataType.STRING},
            'LOWER': {'is_aggregate': False, 'return_type': DataType.STRING},
            'LENGTH': {'is_aggregate': False, 'return_type': DataType.INTEGER},
        }
        
        self.functions.update(aggregates)
        self.functions.update(scalars)

@dataclass
class Column:
    """Represents a column reference with resolved schema info"""
    name: str
    table_alias: Optional[str] = None
    column_info: Optional[ColumnInfo] = None
    
    def __str__(self):
        return f"{self.table_alias}.{self.name}" if self.table_alias else self.name

@dataclass
class Expression(ABC):
    """Base class for all expressions in the logical plan"""
    resolved_type: Optional[DataType] = None
    
    @abstractmethod
    def accept(self, visitor):
        pass

@dataclass
class ColumnReference(Expression):
    column: Column
    
    def accept(self, visitor):
        return visitor.visit_column_reference(self)

@dataclass
class Literal(Expression):
    value: Any
    data_type: DataType
    
    def accept(self, visitor):
        return visitor.visit_literal(self)

@dataclass
class BinaryOperation(Expression):
    left: Expression
    operator: str
    right: Expression
    
    def accept(self, visitor):
        return visitor.visit_binary_operation(self)

@dataclass
class FunctionCall(Expression):
    function_name: str
    arguments: List[Expression]
    is_aggregate: bool = False
    
    def accept(self, visitor):
        return visitor.visit_function_call(self)

@dataclass
class CorrelatedColumnReference(Expression):
    """Represents a column reference that depends on outer query scope"""
    column: Column
    correlation_depth: int = 1  # How many levels up to find the column
    
    def accept(self, visitor):
        return visitor.visit_correlated_column_reference(self)

@dataclass
class Subquery(Expression):
    """Represents a subquery expression"""
    plan: 'LogicalPlan'
    is_correlated: bool = False
    correlated_columns: Set[str] = field(default_factory=set)
    
    def accept(self, visitor):
        return visitor.visit_subquery(self)

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

class TableScan(LogicalPlan):
    def __init__(self, table_name: str, alias: Optional[str] = None, 
                 columns: Optional[List[str]] = None, catalog: Optional[Catalog] = None):
        super().__init__(catalog)
        self.table_name = table_name
        self.alias = alias or table_name
        
        # Use catalog to get full schema information
        if catalog:
            table_info = catalog.get_table(table_name)
            if table_info:
                # Create output schema with proper types
                if columns:
                    # Only selected columns
                    self.output_schema = [
                        ColumnInfo(col, table_info.get_column(col).data_type if table_info.get_column(col) else DataType.UNKNOWN,
                                 table_name, self.alias)
                        for col in columns
                    ]
                else:
                    # All columns
                    self.output_schema = [
                        ColumnInfo(col.name, col.data_type, table_name, self.alias)
                        for col in table_info.columns
                    ]
            else:
                raise ValueError(f"Table {table_name} not found in catalog")
        else:
            # Fallback without catalog
            if columns:
                self.output_schema = [ColumnInfo(col, DataType.UNKNOWN, table_name, self.alias) for col in columns]
    
    def accept(self, visitor):
        return visitor.visit_table_scan(self)

class Filter(LogicalPlan):
    def __init__(self, condition: Expression, child: LogicalPlan):
        super().__init__(child.catalog)
        self.condition = condition
        self.add_child(child)
        self.output_schema = child.get_output_schema().copy()
    
    def accept(self, visitor):
        return visitor.visit_filter(self)

class Projection(LogicalPlan):
    def __init__(self, expressions: List[Expression], child: LogicalPlan):
        super().__init__(child.catalog)
        self.expressions = expressions
        self.add_child(child)
        
        # Build output schema based on projected expressions
        self.output_schema = []
        for i, expr in enumerate(expressions):
            if isinstance(expr, ColumnReference):
                # Try to resolve from child's schema
                resolved = None
                for col_info in child.get_output_schema():
                    if (col_info.name == expr.column.name and
                        (not expr.column.table_alias or col_info.table_alias == expr.column.table_alias)):
                        resolved = col_info
                        break
                
                if resolved:
                    self.output_schema.append(resolved)
                else:
                    # Create new column info
                    self.output_schema.append(
                        ColumnInfo(expr.column.name, expr.resolved_type or DataType.UNKNOWN)
                    )
            else:
                # For expressions, create derived column
                col_name = f"expr_{i}"
                self.output_schema.append(
                    ColumnInfo(col_name, expr.resolved_type or DataType.UNKNOWN)
                )
    
    def accept(self, visitor):
        return visitor.visit_projection(self)

class Join(LogicalPlan):
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

class Aggregate(LogicalPlan):
    def __init__(self, group_by: List[Expression], aggregates: List[Expression], child: LogicalPlan):
        super().__init__(child.catalog)
        self.group_by = group_by
        self.aggregates = aggregates
        self.add_child(child)
        
        # Build output schema
        self.output_schema = []
        
        # Add group by columns
        for expr in group_by:
            if isinstance(expr, ColumnReference):
                # Find in child schema
                for col_info in child.get_output_schema():
                    if (col_info.name == expr.column.name and
                        (not expr.column.table_alias or col_info.table_alias == expr.column.table_alias)):
                        self.output_schema.append(col_info)
                        break
        
        # Add aggregate result columns
        for i, agg_expr in enumerate(aggregates):
            if isinstance(agg_expr, FunctionCall) and self.catalog:
                func_info = self.catalog.get_function_info(agg_expr.function_name)
                return_type = func_info.get('return_type', DataType.UNKNOWN) if func_info else DataType.UNKNOWN
            else:
                return_type = DataType.UNKNOWN
            
            self.output_schema.append(
                ColumnInfo(f"agg_{i}", return_type)
            )
    
    def accept(self, visitor):
        return visitor.visit_aggregate(self)

class Sort(LogicalPlan):
    def __init__(self, sort_expressions: List[tuple], child: LogicalPlan):
        super().__init__(child.catalog)
        self.sort_expressions = sort_expressions
        self.add_child(child)
        self.output_schema = child.get_output_schema().copy()
    
    def accept(self, visitor):
        return visitor.visit_sort(self)

class Limit(LogicalPlan):
    def __init__(self, count: int, offset: int = 0, child: LogicalPlan = None):
        super().__init__(child.catalog if child else None)
        self.count = count
        self.offset = offset
        if child:
            self.add_child(child)
            self.output_schema = child.get_output_schema().copy()
    
    def accept(self, visitor):
        return visitor.visit_limit(self)

class Union(LogicalPlan):
    def __init__(self, left: LogicalPlan, right: LogicalPlan, is_all: bool = False):
        super().__init__(left.catalog)
        self.is_all = is_all
        self.add_child(left)
        self.add_child(right)
        # Union output schema matches left child
        self.output_schema = left.get_output_schema().copy()
    
    def accept(self, visitor):
        return visitor.visit_union(self)

class CorrelatedSubquery(LogicalPlan):
    """Represents a correlated subquery that references outer query columns"""
    
    def __init__(self, subquery: LogicalPlan, correlated_columns: List[CorrelatedColumnReference],
                 correlation_id: str):
        super().__init__(subquery.catalog)
        self.subquery = subquery
        self.correlated_columns = correlated_columns
        self.correlation_id = correlation_id
        self.add_child(subquery)
        self.output_schema = subquery.get_output_schema().copy()
    
    def accept(self, visitor):
        return visitor.visit_correlated_subquery(self)

class Apply(LogicalPlan):
    """Represents correlated operations like EXISTS, IN with correlated subqueries"""
    
    def __init__(self, left: LogicalPlan, right: CorrelatedSubquery, apply_type: str = "CROSS"):
        super().__init__(left.catalog)
        self.apply_type = apply_type  # CROSS, OUTER, SEMI, ANTI
        self.add_child(left)
        self.add_child(right)
        
        # Output schema depends on apply type
        if apply_type in ["SEMI", "ANTI"]:
            # Semi/Anti joins only return left side columns
            self.output_schema = left.get_output_schema().copy()
        else:
            # Cross/Outer apply returns both sides
            self.output_schema = left.get_output_schema().copy() + right.get_output_schema().copy()
    
    def accept(self, visitor):
        return visitor.visit_apply(self)

# Enhanced SQLglot converter with catalog support
class SQLglotToLogicalPlanConverter:
    """Enhanced converter with catalog support and correlated subquery handling"""
    
    def __init__(self, catalog: Optional[Catalog] = None):
        self.catalog = catalog or Catalog()
        self.table_aliases = {}
        self.correlation_stack = []  # Track nested correlation contexts
        self.correlation_counter = 0
    
    def convert(self, sql: str, dialect: str = "postgres") -> LogicalPlan:
        """Main entry point: parse SQL and convert to logical plan"""
        try:
            parsed = sqlglot.parse_one(sql, dialect=dialect)
            if not parsed:
                raise ValueError("Failed to parse SQL")
            
            return self._convert_expression(parsed)
        except Exception as e:
            raise ValueError(f"Error converting SQL to logical plan: {e}")
    
    def _convert_expression(self, expr: exp.Expression) -> LogicalPlan:
        """Convert a SQLglot expression to logical plan node"""
        if isinstance(expr, exp.Select):
            return self._convert_select(expr)
        elif isinstance(expr, exp.Union):
            return self._convert_union(expr)
        else:
            raise NotImplementedError(f"Conversion not implemented for {type(expr)}")
    
    def _convert_select(self, select: exp.Select) -> LogicalPlan:
        """Enhanced SELECT conversion with correlation handling"""
        
        # Check if this is a correlated subquery
        is_correlated = self._is_correlated_subquery(select)
        correlation_id = None
        
        if is_correlated:
            correlation_id = f"corr_{self.correlation_counter}"
            self.correlation_counter += 1
            self.correlation_stack.append(correlation_id)
        
        try:
            # Build plan as before
            plan = self._convert_from_clause(select)
            
            if select.find(exp.Where):
                where_expr = select.find(exp.Where)
                condition = self._convert_sql_expression(where_expr.this)
                plan = Filter(condition, plan)
            
            # Handle GROUP BY and aggregates
            group_by_exprs = []
            if select.find(exp.Group):
                group_by = select.find(exp.Group)
                for expr in group_by.expressions:
                    group_by_exprs.append(self._convert_sql_expression(expr))
            
            # Process SELECT expressions
            select_exprs = []
            aggregate_exprs = []
            has_aggregates = False
            
            for expr in select.expressions:
                converted_expr = self._convert_sql_expression(expr)
                select_exprs.append(converted_expr)
                
                if self._is_aggregate_expression(converted_expr):
                    aggregate_exprs.append(converted_expr)
                    has_aggregates = True
            
            if has_aggregates or group_by_exprs:
                plan = Aggregate(group_by_exprs, aggregate_exprs, plan)
            
            plan = Projection(select_exprs, plan)
            
            # Handle ORDER BY
            if select.find(exp.Order):
                order = select.find(exp.Order)
                sort_exprs = []
                for expr in order.expressions:
                    sql_expr = self._convert_sql_expression(expr.this)
                    is_desc = isinstance(expr, exp.Ordered) and expr.desc
                    sort_exprs.append((sql_expr, not is_desc))
                plan = Sort(sort_exprs, plan)
            
            # Handle LIMIT/OFFSET
            if select.find(exp.Limit):
                limit = select.find(exp.Limit)
                count = int(limit.expression.this) if limit.expression else 0
                offset = int(limit.offset.this) if limit.offset else 0
                plan = Limit(count, offset, plan)
            
            # Wrap in CorrelatedSubquery if needed
            if is_correlated:
                correlated_cols = self._find_correlated_columns(select)
                plan = CorrelatedSubquery(plan, correlated_cols, correlation_id)
            
            return plan
            
        finally:
            if is_correlated:
                self.correlation_stack.pop()
    
    def _convert_from_clause(self, select: exp.Select) -> LogicalPlan:
        """Enhanced FROM clause conversion with catalog integration"""
        from_expr = select.find(exp.From)
        if not from_expr:
            raise ValueError("No FROM clause found")
        
        plan = self._convert_table_reference(from_expr.this)
        
        # Handle JOINs
        joins = select.find_all(exp.Join)
        for join in joins:
            right_plan = self._convert_table_reference(join.this)
            join_type = self._convert_join_type(join)
            
            condition = None
            if join.on:
                condition = self._convert_sql_expression(join.on)
            
            plan = Join(join_type, condition, plan, right_plan)
        
        return plan
    
    def _convert_table_reference(self, table_ref: exp.Expression) -> LogicalPlan:
        """Enhanced table reference conversion with catalog lookup"""
        if isinstance(table_ref, exp.Table):
            table_name = table_ref.name
            alias = table_ref.alias if table_ref.alias else table_name
            
            self.table_aliases[alias] = table_name
            return TableScan(table_name, alias, catalog=self.catalog)
        
        elif isinstance(table_ref, exp.Subquery):
            subquery_plan = self._convert_expression(table_ref.this)
            return subquery_plan
        
        else:
            raise NotImplementedError(f"Table reference type {type(table_ref)} not supported")
    
    def _convert_sql_expression(self, expr: exp.Expression) -> Expression:
        """Enhanced expression conversion with type resolution and correlation detection"""
        
        if isinstance(expr, exp.Column):
            column = Column(expr.name, expr.table if expr.table else None)
            
            # Check if this is a correlated reference
            if self._is_correlated_column_reference(expr):
                return CorrelatedColumnReference(column, correlation_depth=len(self.correlation_stack))
            
            col_ref = ColumnReference(column)
            
            # Try to resolve type from catalog
            if self.catalog:
                try:
                    table_context = expr.table or None
                    resolved = self.catalog.resolve_column(expr.name, table_context)
                    if resolved:
                        col_ref.resolved_type = resolved.data_type
                        column.column_info = resolved
                except ValueError:
                    # Column resolution failed, might be correlated
                    pass
            
            return col_ref
        
        elif isinstance(expr, exp.Literal):
            data_type = self._infer_literal_type(expr.this)
            return Literal(expr.this, data_type)
        
        elif isinstance(expr, exp.Binary):
            left = self._convert_sql_expression(expr.left)
            right = self._convert_sql_expression(expr.right)
            operator = self._convert_operator(expr)
            
            # Type inference for binary operations
            bin_op = BinaryOperation(left, operator, right)
            bin_op.resolved_type = self._infer_binary_operation_type(left, right, operator)
            return bin_op
        
        elif isinstance(expr, exp.Func):
            func_name = expr.__class__.__name__.upper()
            args = [self._convert_sql_expression(arg) for arg in expr.expressions]
            
            # Get function info from catalog
            func_info = self.catalog.get_function_info(func_name) if self.catalog else None
            is_agg = func_info.get('is_aggregate', False) if func_info else func_name in ['COUNT', 'SUM', 'AVG', 'MIN', 'MAX']
            
            func_call = FunctionCall(func_name, args, is_agg)
            if func_info:
                func_call.resolved_type = func_info.get('return_type', DataType.UNKNOWN)
            
            return func_call
        
        elif isinstance(expr, exp.Subquery):
            subquery_plan = self._convert_expression(expr.this)
            is_corr = self._contains_correlated_references(expr.this)
            return Subquery(subquery_plan, is_corr)
        
        elif isinstance(expr, exp.Star):
            return ColumnReference(Column("*"))
        
        else:
            raise NotImplementedError(f"Expression type {type(expr)} not supported")
    
    def _is_correlated_subquery(self, select: exp.Select) -> bool:
        """Check if SELECT contains correlated column references"""
        # This is a simplified check - in practice, you'd need more sophisticated analysis
        return len(self.correlation_stack) > 0
    
    def _is_correlated_column_reference(self, column: exp.Column) -> bool:
        """Check if column reference is correlated (references outer query)"""
        # Simplified: if we're in a subquery context and can't resolve locally
        if not self.correlation_stack:
            return False
        
        # More sophisticated logic would check if the column exists in current scope
        # vs outer scopes
        return False  # Placeholder
    
    def _find_correlated_columns(self, select: exp.Select) -> List[CorrelatedColumnReference]:
        """Find all correlated column references in a subquery"""
        # Placeholder - would implement visitor to find correlated refs
        return []
    
    def _contains_correlated_references(self, expr: exp.Expression) -> bool:
        """Check if expression tree contains correlated references"""
        # Placeholder for recursive check
        return False
    
    def _infer_binary_operation_type(self, left: Expression, right: Expression, operator: str) -> DataType:
        """Infer result type of binary operation"""
        if operator in ['=', '!=', '<', '<=', '>', '>=', 'AND', 'OR']:
            return DataType.BOOLEAN
        elif operator in ['+', '-', '*', '/']:
            # Simplified numeric type promotion
            if left.resolved_type in [DataType.INTEGER, DataType.BIGINT] and right.resolved_type in [DataType.INTEGER, DataType.BIGINT]:
                return DataType.BIGINT
            else:
                return DataType.DOUBLE
        return DataType.UNKNOWN
    
    # ... (keep existing helper methods from previous version)
    def _convert_union(self, union: exp.Union) -> LogicalPlan:
        left = self._convert_expression(union.left)
        right = self._convert_expression(union.right)
        is_all = isinstance(union, exp.Union) and union.distinct is False
        return Union(left, right, is_all)
    
    def _convert_join_type(self, join: exp.Join) -> JoinType:
        if join.kind == "INNER" or not join.kind:
            return JoinType.INNER
        elif join.kind == "LEFT":
            return JoinType.LEFT
        elif join.kind == "RIGHT":
            return JoinType.RIGHT
        elif join.kind == "FULL":
            return JoinType.FULL
        elif join.kind == "CROSS":
            return JoinType.CROSS
        else:
            return JoinType.INNER
    
    def _convert_operator(self, binary_expr: exp.Binary) -> str:
        type_map = {
            exp.EQ: "=", exp.NEQ: "!=", exp.LT: "<", exp.LTE: "<=",
            exp.GT: ">", exp.GTE: ">=", exp.And: "AND", exp.Or: "OR",
            exp.Add: "+", exp.Sub: "-", exp.Mul: "*", exp.Div: "/",
        }
        return type_map.get(type(binary_expr), str(binary_expr.key))
    
    def _infer_literal_type(self, value) -> DataType:
        if isinstance(value, int):
            return DataType.INTEGER
        elif isinstance(value, float):
            return DataType.FLOAT
        elif isinstance(value, str):
            return DataType.STRING
        elif isinstance(value, bool):
            return DataType.BOOLEAN
        else:
            return DataType.UNKNOWN
    
    def _is_aggregate_expression(self, expr: Expression) -> bool:
        if isinstance(expr, FunctionCall) and expr.is_aggregate:
            return True
        elif isinstance(expr, BinaryOperation):
            return (self._is_aggregate_expression(expr.left) or 
                   self._is_aggregate_expression(expr.right))
        return False

# Enhanced visitor with correlation support
class LogicalPlanVisitor(ABC):
    @abstractmethod
    def visit_table_scan(self, node: TableScan): pass
    @abstractmethod
    def visit_filter(self, node: Filter): pass
    @abstractmethod
    def visit_projection(self, node: Projection): pass
    @abstractmethod
    def visit_join(self, node: Join): pass
    @abstractmethod
    def visit_aggregate(self, node: Aggregate): pass
    @abstractmethod
    def visit_sort(self, node: Sort): pass
    @abstractmethod
    def visit_limit(self, node: Limit): pass
    @abstractmethod
    def visit_union(self, node: Union): pass
    @abstractmethod
    def visit_correlated_subquery(self, node: CorrelatedSubquery): pass
    @abstractmethod
    def visit_apply(self, node: Apply): pass
    @abstractmethod
    def visit_column_reference(self, expr: ColumnReference): pass
    @abstractmethod
    def visit_literal(self, expr: Literal): pass
    @abstractmethod
    def visit_binary_operation(self, expr: BinaryOperation): pass
    @abstractmethod
    def visit_function_call(self, expr: FunctionCall): pass
    @abstractmethod
    def visit_correlated_column_reference(self, expr: CorrelatedColumnReference): pass
    @abstractmethod
    def visit_subquery(self, expr: Subquery): pass

# Example usage with catalog
if __name__ == "__main__":
    # Setup catalog
    catalog = Catalog()
    
    # Register tables
    users_table = TableInfo("users", [
        ColumnInfo("id", DataType.INTEGER, "users", is_primary_key=True),
        ColumnInfo("name", DataType.STRING, "users"),
        ColumnInfo("age", DataType.INTEGER, "users"),
        ColumnInfo("email", DataType.STRING, "users"),
    ])
    
    orders_table = TableInfo("orders", [
        ColumnInfo("id", DataType.INTEGER, "orders", is_primary_key=True),
        ColumnInfo("user_id", DataType.INTEGER, "orders"),
        ColumnInfo("amount", DataType.DECIMAL, "orders"),
        ColumnInfo("order_date", DataType.DATE, "orders"),
    ])
    
    catalog.register_table(users_table)
    catalog.register_table(orders_table)
    
    # Enhanced pretty print visitor with catalog and correlation support
    class EnhancedPrettyPrintVisitor(LogicalPlanVisitor):
        def __init__(self):
            self.indent_level = 0
        
        def _indent(self):
            return "  " * self.indent_level
        
        def visit_table_scan(self, node: TableScan):
            schema_info = []
            for col in node.output_schema:
                schema_info.append(f"{col.name}:{col.data_type.value}")
            print(f"{self._indent()}TableScan: {node.table_name} AS {node.alias}")
            print(f"{self._indent()}  Schema: [{', '.join(schema_info)}]")
            return node
        
        def visit_filter(self, node: Filter):
            print(f"{self._indent()}Filter:")
            self.indent_level += 1
            print(f"{self._indent()}Condition: {self._expr_to_string(node.condition)}")
            print(f"{self._indent()}Child:")
            self.indent_level += 1
            node.children[0].accept(self)
            self.indent_level -= 2
            return node
        
        def visit_projection(self, node: Projection):
            print(f"{self._indent()}Projection:")
            self.indent_level += 1
            expr_strs = []
            for i, expr in enumerate(node.expressions):
                expr_str = self._expr_to_string(expr)
                if expr.resolved_type:
                    expr_str += f":{expr.resolved_type.value}"
                expr_strs.append(expr_str)
            print(f"{self._indent()}Expressions: {expr_strs}")
            
            # Show output schema
            schema_info = [f"{col.name}:{col.data_type.value}" for col in node.output_schema]
            print(f"{self._indent()}Output Schema: [{', '.join(schema_info)}]")
            
            print(f"{self._indent()}Child:")
            self.indent_level += 1
            node.children[0].accept(self)
            self.indent_level -= 2
            return node
        
        def visit_join(self, node: Join):
            print(f"{self._indent()}Join ({node.join_type.value}):")
            self.indent_level += 1
            if node.condition:
                print(f"{self._indent()}Condition: {self._expr_to_string(node.condition)}")
            
            # Show output schema
            schema_info = [f"{col.name}:{col.data_type.value}" for col in node.output_schema]
            print(f"{self._indent()}Output Schema: [{', '.join(schema_info)}]")
            
            print(f"{self._indent()}Left:")
            self.indent_level += 1
            node.children[0].accept(self)
            self.indent_level -= 1
            print(f"{self._indent()}Right:")
            self.indent_level += 1
            node.children[1].accept(self)
            self.indent_level -= 2
            return node
        
        def visit_aggregate(self, node: Aggregate):
            print(f"{self._indent()}Aggregate:")
            self.indent_level += 1
            if node.group_by:
                group_strs = [self._expr_to_string(e) for e in node.group_by]
                print(f"{self._indent()}Group By: {group_strs}")
            if node.aggregates:
                agg_strs = [self._expr_to_string(e) for e in node.aggregates]
                print(f"{self._indent()}Aggregates: {agg_strs}")
            
            # Show output schema
            schema_info = [f"{col.name}:{col.data_type.value}" for col in node.output_schema]
            print(f"{self._indent()}Output Schema: [{', '.join(schema_info)}]")
            
            print(f"{self._indent()}Child:")
            self.indent_level += 1
            node.children[0].accept(self)
            self.indent_level -= 2
            return node
        
        def visit_sort(self, node: Sort):
            sort_strs = [f"{self._expr_to_string(expr)} {'ASC' if asc else 'DESC'}" 
                         for expr, asc in node.sort_expressions]
            print(f"{self._indent()}Sort: {sort_strs}")
            self.indent_level += 1
            node.children[0].accept(self)
            self.indent_level -= 1
            return node
        
        def visit_limit(self, node: Limit):
            print(f"{self._indent()}Limit: {node.count} OFFSET {node.offset}")
            self.indent_level += 1
            if node.children:
                node.children[0].accept(self)
            self.indent_level -= 1
            return node
        
        def visit_union(self, node: Union):
            union_type = "UNION ALL" if node.is_all else "UNION"
            print(f"{self._indent()}{union_type}:")
            self.indent_level += 1
            print(f"{self._indent()}Left:")
            self.indent_level += 1
            node.children[0].accept(self)
            self.indent_level -= 1
            print(f"{self._indent()}Right:")
            self.indent_level += 1
            node.children[1].accept(self)
            self.indent_level -= 2
            return node
        
        def visit_correlated_subquery(self, node: CorrelatedSubquery):
            print(f"{self._indent()}CorrelatedSubquery (ID: {node.correlation_id}):")
            self.indent_level += 1
            if node.correlated_columns:
                corr_cols = [self._expr_to_string(col) for col in node.correlated_columns]
                print(f"{self._indent()}Correlated Columns: {corr_cols}")
            print(f"{self._indent()}Subquery:")
            self.indent_level += 1
            node.children[0].accept(self)
            self.indent_level -= 2
            return node
        
        def visit_apply(self, node: Apply):
            print(f"{self._indent()}Apply ({node.apply_type}):")
            self.indent_level += 1
            print(f"{self._indent()}Left:")
            self.indent_level += 1
            node.children[0].accept(self)
            self.indent_level -= 1
            print(f"{self._indent()}Right:")
            self.indent_level += 1
            node.children[1].accept(self)
            self.indent_level -= 2
            return node
        
        def _expr_to_string(self, expr: Expression) -> str:
            if isinstance(expr, ColumnReference):
                return str(expr.column)
            elif isinstance(expr, CorrelatedColumnReference):
                return f"CORR[{expr.correlation_depth}]{expr.column}"
            elif isinstance(expr, Literal):
                return str(expr.value)
            elif isinstance(expr, BinaryOperation):
                return f"({self._expr_to_string(expr.left)} {expr.operator} {self._expr_to_string(expr.right)})"
            elif isinstance(expr, FunctionCall):
                args = [self._expr_to_string(arg) for arg in expr.arguments]
                func_str = f"{expr.function_name}({', '.join(args)})"
                if expr.is_aggregate:
                    func_str = f"AGG[{func_str}]"
                return func_str
            elif isinstance(expr, Subquery):
                corr_indicator = "CORRELATED " if expr.is_correlated else ""
                return f"{corr_indicator}SUBQUERY"
            return str(expr)
        
        # Expression visitors
        def visit_column_reference(self, expr: ColumnReference): return expr
        def visit_correlated_column_reference(self, expr: CorrelatedColumnReference): return expr
        def visit_literal(self, expr: Literal): return expr
        def visit_binary_operation(self, expr: BinaryOperation): return expr
        def visit_function_call(self, expr: FunctionCall): return expr
        def visit_subquery(self, expr: Subquery): return expr
    
    # Test queries with enhanced features
    test_queries = [
        # Basic query with type resolution
        "SELECT id, name, age FROM users WHERE age > 25",
        
        # Join with full schema information
        "SELECT u.name, o.amount FROM users u JOIN orders o ON u.id = o.user_id",
        
        # Aggregation with proper type inference
        "SELECT u.name, COUNT(*) as order_count, SUM(o.amount) as total FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.name",
        
        # Complex query with multiple operations
        "SELECT u.name, AVG(o.amount) as avg_order FROM users u JOIN orders o ON u.id = o.user_id WHERE u.age > 21 GROUP BY u.name HAVING COUNT(*) > 2 ORDER BY avg_order DESC LIMIT 10",
    ]
    
    # Example of correlated subquery (simplified for demonstration)
    correlated_query = """
    SELECT u.name 
    FROM users u 
    WHERE EXISTS (
        SELECT 1 
        FROM orders o 
        WHERE o.user_id = u.id 
        AND o.amount > 100
    )
    """
    
    converter = SQLglotToLogicalPlanConverter(catalog)
    visitor = EnhancedPrettyPrintVisitor()
    
    print("=== Testing Enhanced Logical Plan Conversion ===\n")
    
    for i, sql in enumerate(test_queries, 1):
        print(f"Query {i}: {sql}")
        print("=" * 80)
        try:
            plan = converter.convert(sql)
            plan.accept(visitor)
        except Exception as e:
            print(f"Error: {e}")
        print("\n")
    
    # Demonstrate catalog capabilities
    print("=== Catalog Information ===")
    print("Registered Tables:")
    for table_name, table_info in catalog.tables.items():
        print(f"  {table_name}:")
        for col in table_info.columns:
            pk_indicator = " (PK)" if col.is_primary_key else ""
            print(f"    {col.name}: {col.data_type.value}{pk_indicator}")
    
    print("\nBuilt-in Functions:")
    for func_name, func_info in catalog.functions.items():
        agg_indicator = " (AGGREGATE)" if func_info.get('is_aggregate') else ""
        return_type = func_info.get('return_type', DataType.UNKNOWN).value
        print(f"  {func_name} -> {return_type}{agg_indicator}")
    
    # Demonstrate type resolution
    print("\n=== Type Resolution Example ===")
    simple_query = "SELECT name, age + 1 as next_age FROM users WHERE age > 18"
    print(f"Query: {simple_query}")
    print("-" * 50)
    try:
        plan = converter.convert(simple_query)
        plan.accept(visitor)
    except Exception as e:
        print(f"Error: {e}")
    
    print("\n=== Schema Evolution Example ===")
    print("How schema information flows through the plan:")
    
    # Create a simple manual plan to show schema evolution
    users_scan = TableScan("users", "u", catalog=catalog)
    filter_node = Filter(
        BinaryOperation(
            ColumnReference(Column("age", "u")),
            ">",
            Literal(18, DataType.INTEGER)
        ),
        users_scan
    )
    projection_node = Projection([
        ColumnReference(Column("name", "u")),
        BinaryOperation(
            ColumnReference(Column("age", "u")),
            "+",
            Literal(1, DataType.INTEGER)
        )
    ], filter_node)
    
    print("Manual plan construction with schema tracking:")
    print("-" * 50)
    projection_node.accept(visitor)