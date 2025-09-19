from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, Union, Set
from dataclasses import dataclass
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

@dataclass
class Column:
    """Represents a column reference"""
    name: str
    table_alias: Optional[str] = None
    data_type: Optional[str] = None
    
    def __str__(self):
        return f"{self.table_alias}.{self.name}" if self.table_alias else self.name

@dataclass
class Expression(ABC):
    """Base class for all expressions in the logical plan"""
    
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
    data_type: str
    
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

class LogicalPlan(ABC):
    """Base class for all logical plan nodes"""
    
    def __init__(self):
        self.output_columns: List[Column] = []
        self.children: List['LogicalPlan'] = []
    
    @abstractmethod
    def accept(self, visitor):
        pass
    
    def add_child(self, child: 'LogicalPlan'):
        self.children.append(child)
    
    def get_output_schema(self) -> List[Column]:
        return self.output_columns

class TableScan(LogicalPlan):
    def __init__(self, table_name: str, alias: Optional[str] = None, columns: Optional[List[str]] = None):
        super().__init__()
        self.table_name = table_name
        self.alias = alias or table_name
        
        if columns:
            self.output_columns = [Column(col, self.alias) for col in columns]
    
    def accept(self, visitor):
        return visitor.visit_table_scan(self)

class Filter(LogicalPlan):
    def __init__(self, condition: Expression, child: LogicalPlan):
        super().__init__()
        self.condition = condition
        self.add_child(child)
        self.output_columns = child.get_output_schema()
    
    def accept(self, visitor):
        return visitor.visit_filter(self)

class Projection(LogicalPlan):
    def __init__(self, expressions: List[Expression], child: LogicalPlan):
        super().__init__()
        self.expressions = expressions
        self.add_child(child)
        
        self.output_columns = []
        for expr in expressions:
            if isinstance(expr, ColumnReference):
                self.output_columns.append(expr.column)
            else:
                self.output_columns.append(Column(f"expr_{len(self.output_columns)}"))
    
    def accept(self, visitor):
        return visitor.visit_projection(self)

class Join(LogicalPlan):
    def __init__(self, join_type: JoinType, condition: Optional[Expression], 
                 left: LogicalPlan, right: LogicalPlan):
        super().__init__()
        self.join_type = join_type
        self.condition = condition
        self.add_child(left)
        self.add_child(right)
        
        self.output_columns = left.get_output_schema() + right.get_output_schema()
    
    def accept(self, visitor):
        return visitor.visit_join(self)

class Aggregate(LogicalPlan):
    def __init__(self, group_by: List[Expression], aggregates: List[Expression], child: LogicalPlan):
        super().__init__()
        self.group_by = group_by
        self.aggregates = aggregates
        self.add_child(child)
        
        self.output_columns = []
        for expr in group_by:
            if isinstance(expr, ColumnReference):
                self.output_columns.append(expr.column)
        
        for i, agg in enumerate(aggregates):
            self.output_columns.append(Column(f"agg_{i}"))
    
    def accept(self, visitor):
        return visitor.visit_aggregate(self)

class Sort(LogicalPlan):
    def __init__(self, sort_expressions: List[tuple], child: LogicalPlan):
        super().__init__()
        self.sort_expressions = sort_expressions
        self.add_child(child)
        self.output_columns = child.get_output_schema()
    
    def accept(self, visitor):
        return visitor.visit_sort(self)

class Limit(LogicalPlan):
    def __init__(self, count: int, offset: int = 0, child: LogicalPlan = None):
        super().__init__()
        self.count = count
        self.offset = offset
        if child:
            self.add_child(child)
            self.output_columns = child.get_output_schema()
    
    def accept(self, visitor):
        return visitor.visit_limit(self)

class Union(LogicalPlan):
    """Represents UNION operations"""
    def __init__(self, left: LogicalPlan, right: LogicalPlan, is_all: bool = False):
        super().__init__()
        self.is_all = is_all
        self.add_child(left)
        self.add_child(right)
        # Union output schema matches left child (assuming compatible schemas)
        self.output_columns = left.get_output_schema()
    
    def accept(self, visitor):
        return visitor.visit_union(self)

# SQLglot AST to Logical Plan Converter
class SQLglotToLogicalPlanConverter:
    """Converts SQLglot AST to our logical plan representation"""
    
    def __init__(self):
        self.table_aliases = {}  # Maps aliases to table names
        self.current_scope_columns = {}  # Tracks available columns in current scope
    
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
        """Convert SELECT statement to logical plan"""
        
        # Start with FROM clause (table scans and joins)
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
        
        # Check if we have aggregates in SELECT
        select_exprs = []
        aggregate_exprs = []
        has_aggregates = False
        
        for expr in select.expressions:
            converted_expr = self._convert_sql_expression(expr)
            select_exprs.append(converted_expr)
            
            if self._is_aggregate_expression(converted_expr):
                aggregate_exprs.append(converted_expr)
                has_aggregates = True
        
        # If we have aggregates, add Aggregate node
        if has_aggregates or group_by_exprs:
            plan = Aggregate(group_by_exprs, aggregate_exprs, plan)
        
        # Add projection for SELECT clause
        plan = Projection(select_exprs, plan)
        
        # Handle ORDER BY
        if select.find(exp.Order):
            order = select.find(exp.Order)
            sort_exprs = []
            for expr in order.expressions:
                sql_expr = self._convert_sql_expression(expr.this)
                is_desc = isinstance(expr, exp.Ordered) and expr.desc
                sort_exprs.append((sql_expr, not is_desc))  # True for ASC
            plan = Sort(sort_exprs, plan)
        
        # Handle LIMIT/OFFSET
        if select.find(exp.Limit):
            limit = select.find(exp.Limit)
            count = int(limit.expression.this) if limit.expression else 0
            offset = int(limit.offset.this) if limit.offset else 0
            plan = Limit(count, offset, plan)
        
        return plan
    
    def _convert_from_clause(self, select: exp.Select) -> LogicalPlan:
        """Convert FROM clause including joins"""
        
        from_expr = select.find(exp.From)
        if not from_expr:
            raise ValueError("No FROM clause found")
        
        # Start with the main table
        plan = self._convert_table_reference(from_expr.this)
        
        # Handle JOINs
        joins = select.find_all(exp.Join)
        for join in joins:
            right_plan = self._convert_table_reference(join.this)
            
            # Convert join type
            join_type = self._convert_join_type(join)
            
            # Convert join condition
            condition = None
            if join.on:
                condition = self._convert_sql_expression(join.on)
            
            plan = Join(join_type, condition, plan, right_plan)
        
        return plan
    
    def _convert_table_reference(self, table_ref: exp.Expression) -> LogicalPlan:
        """Convert table reference to TableScan"""
        
        if isinstance(table_ref, exp.Table):
            table_name = table_ref.name
            alias = table_ref.alias if table_ref.alias else table_name
            
            # Track the alias for column resolution
            self.table_aliases[alias] = table_name
            
            return TableScan(table_name, alias)
        
        elif isinstance(table_ref, exp.Subquery):
            # Handle subqueries
            subquery_plan = self._convert_expression(table_ref.this)
            # TODO: Wrap in a SubqueryPlan if needed
            return subquery_plan
        
        else:
            raise NotImplementedError(f"Table reference type {type(table_ref)} not supported")
    
    def _convert_sql_expression(self, expr: exp.Expression) -> Expression:
        """Convert SQLglot expression to our Expression type"""
        
        if isinstance(expr, exp.Column):
            table_alias = expr.table if expr.table else None
            column = Column(expr.name, table_alias)
            return ColumnReference(column)
        
        elif isinstance(expr, exp.Literal):
            return Literal(expr.this, self._infer_literal_type(expr.this))
        
        elif isinstance(expr, exp.Binary):
            left = self._convert_sql_expression(expr.left)
            right = self._convert_sql_expression(expr.right)
            operator = self._convert_operator(expr)
            return BinaryOperation(left, operator, right)
        
        elif isinstance(expr, exp.Func):
            func_name = expr.__class__.__name__.upper()
            args = [self._convert_sql_expression(arg) for arg in expr.expressions]
            is_agg = func_name in ['COUNT', 'SUM', 'AVG', 'MIN', 'MAX']
            return FunctionCall(func_name, args, is_agg)
        
        elif isinstance(expr, exp.Star):
            # Handle SELECT * - this is a simplification
            return ColumnReference(Column("*"))
        
        else:
            raise NotImplementedError(f"Expression type {type(expr)} not supported")
    
    def _convert_union(self, union: exp.Union) -> LogicalPlan:
        """Convert UNION to Union logical plan node"""
        left = self._convert_expression(union.left)
        right = self._convert_expression(union.right)
        is_all = isinstance(union, exp.Union) and union.distinct is False
        return Union(left, right, is_all)
    
    def _convert_join_type(self, join: exp.Join) -> JoinType:
        """Convert SQLglot join type to our enum"""
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
            return JoinType.INNER  # Default
    
    def _convert_operator(self, binary_expr: exp.Binary) -> str:
        """Convert SQLglot binary operator to string"""
        type_map = {
            exp.EQ: "=",
            exp.NEQ: "!=",
            exp.LT: "<",
            exp.LTE: "<=",
            exp.GT: ">",
            exp.GTE: ">=",
            exp.And: "AND",
            exp.Or: "OR",
            exp.Add: "+",
            exp.Sub: "-",
            exp.Mul: "*",
            exp.Div: "/",
        }
        return type_map.get(type(binary_expr), str(binary_expr.key))
    
    def _infer_literal_type(self, value) -> str:
        """Infer data type from literal value"""
        if isinstance(value, int):
            return "INTEGER"
        elif isinstance(value, float):
            return "FLOAT"
        elif isinstance(value, str):
            return "STRING"
        elif isinstance(value, bool):
            return "BOOLEAN"
        else:
            return "UNKNOWN"
    
    def _is_aggregate_expression(self, expr: Expression) -> bool:
        """Check if expression contains aggregate functions"""
        if isinstance(expr, FunctionCall) and expr.is_aggregate:
            return True
        elif isinstance(expr, BinaryOperation):
            return (self._is_aggregate_expression(expr.left) or 
                   self._is_aggregate_expression(expr.right))
        return False

# Visitor implementations
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
    def visit_column_reference(self, expr: ColumnReference): pass
    @abstractmethod
    def visit_literal(self, expr: Literal): pass
    @abstractmethod
    def visit_binary_operation(self, expr: BinaryOperation): pass
    @abstractmethod
    def visit_function_call(self, expr: FunctionCall): pass

class PrettyPrintVisitor(LogicalPlanVisitor):
    def __init__(self):
        self.indent_level = 0
    
    def _indent(self):
        return "  " * self.indent_level
    
    def visit_table_scan(self, node: TableScan):
        print(f"{self._indent()}TableScan: {node.table_name} AS {node.alias}")
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
        expr_strs = [self._expr_to_string(e) for e in node.expressions]
        print(f"{self._indent()}Expressions: {expr_strs}")
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
            print(f"{self._indent()}Group By: {[self._expr_to_string(e) for e in node.group_by]}")
        if node.aggregates:
            print(f"{self._indent()}Aggregates: {[self._expr_to_string(e) for e in node.aggregates]}")
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
    
    def _expr_to_string(self, expr: Expression) -> str:
        if isinstance(expr, ColumnReference):
            return str(expr.column)
        elif isinstance(expr, Literal):
            return str(expr.value)
        elif isinstance(expr, BinaryOperation):
            return f"({self._expr_to_string(expr.left)} {expr.operator} {self._expr_to_string(expr.right)})"
        elif isinstance(expr, FunctionCall):
            args = [self._expr_to_string(arg) for arg in expr.arguments]
            return f"{expr.function_name}({', '.join(args)})"
        return str(expr)
    
    # Expression visitors
    def visit_column_reference(self, expr: ColumnReference): return expr
    def visit_literal(self, expr: Literal): return expr
    def visit_binary_operation(self, expr: BinaryOperation): return expr
    def visit_function_call(self, expr: FunctionCall): return expr

# Example usage and testing
if __name__ == "__main__":
    # Example queries to test
    queries = [
        "SELECT * FROM users",
        "SELECT name, age FROM users WHERE age > 18",
        "SELECT u.name, COUNT(*) FROM users u JOIN orders o ON u.id = o.user_id GROUP BY u.name",
        "SELECT * FROM users UNION SELECT * FROM customers",
        "SELECT name FROM users ORDER BY age DESC LIMIT 10"
    ]
    
    converter = SQLglotToLogicalPlanConverter()
    visitor = PrettyPrintVisitor()
    
    for sql in queries:
        print(f"\nSQL: {sql}")
        print("=" * 50)
        try:
            plan = converter.convert(sql)
            plan.accept(visitor)
        except Exception as e:
            print(f"Error: {e}")
        print()