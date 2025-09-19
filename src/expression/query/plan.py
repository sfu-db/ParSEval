"""
Logical Plan

Classes representing logical query plan operators and conversion from AST.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Set
# from sql_optimizer import (
#     Select, From, Where, Column, Table, Join, JoinType,
#     BinaryExpression, BinaryOp, Literal, OrderBy, GroupBy, ASTNode
# )


class LogicalOperator:
    """Base class for all logical operators"""
    pass


@dataclass
class LogicalScan(LogicalOperator):
    """Represents a table scan operation"""
    table: Table
    columns: List[str] = field(default_factory=list)  # Columns required from this table
    
    def __str__(self) -> str:
        cols_str = "*" if not self.columns else ", ".join(self.columns)
        table_str = self.table.name
        if self.table.alias:
            table_str += f" AS {self.table.alias}"
        return f"LogicalScan({table_str}, columns=[{cols_str}])"


@dataclass
class LogicalProject(LogicalOperator):
    """Represents a projection operation"""
    input_op: LogicalOperator
    columns: List[Column]
    
    def __str__(self) -> str:
        cols_str = ", ".join(str(col) for col in self.columns)
        return f"LogicalProject([{cols_str}])\n  {str(self.input_op).replace('\n', '\n  ')}"


@dataclass
class LogicalFilter(LogicalOperator):
    """Represents a filter (WHERE) operation"""
    input_op: LogicalOperator
    condition: BinaryExpression
    
    def __str__(self) -> str:
        return f"LogicalFilter({self.condition})\n  {str(self.input_op).replace('\n', '\n  ')}"


@dataclass
class LogicalJoin(LogicalOperator):
    """Represents a join operation"""
    left: LogicalOperator
    right: LogicalOperator
    condition: Optional[BinaryExpression]
    join_type: JoinType
    
    def __str__(self) -> str:
        condition_str = str(self.condition) if self.condition else "None"
        return (
            f"LogicalJoin({self.join_type.value}, condition={condition_str})\n"
            f"  Left: {str(self.left).replace('\n', '\n  ')}\n"
            f"  Right: {str(self.right).replace('\n', '\n  ')}"
        )


@dataclass
class LogicalAggregate(LogicalOperator):
    """Represents an aggregation (GROUP BY) operation"""
    input_op: LogicalOperator
    group_by_columns: List[Column]
    aggregate_expressions: List[Any] = field(default_factory=list)  # Will hold aggregate functions
    
    def __str__(self) -> str:
        cols_str = ", ".join(str(col) for col in self.group_by_columns)
        agg_str = ", ".join(str(agg) for agg in self.aggregate_expressions)
        agg_part = f", aggregates=[{agg_str}]" if self.aggregate_expressions else ""
        return f"LogicalAggregate(group_by=[{cols_str}]{agg_part})\n  {str(self.input_op).replace('\n', '\n  ')}"


@dataclass
class LogicalSort(LogicalOperator):
    """Represents a sort (ORDER BY) operation"""
    input_op: LogicalOperator
    columns: List[Column]
    directions: List[str]
    
    def __str__(self) -> str:
        sort_items = [f"{col} {dir}" for col, dir in zip(self.columns, self.directions)]
        return f"LogicalSort([{', '.join(sort_items)}])\n  {str(self.input_op).replace('\n', '\n  ')}"


@dataclass
class LogicalLimit(LogicalOperator):
    """Represents a LIMIT operation"""
    input_op: LogicalOperator
    limit: int
    
    def __str__(self) -> str:
        return f"LogicalLimit({self.limit})\n  {str(self.input_op).replace('\n', '\n  ')}"


class ASTToLogicalConverter:
    """Converts an AST to a logical plan"""
    
    def convert(self, ast: Select) -> LogicalOperator:
        """Convert a Select AST node to a logical plan"""
        # Build the base logical plan from the FROM clause
        logical_op = self._build_from_clause(ast.from_clause)
        
        # Apply WHERE if present
        if ast.where_clause:
            logical_op = LogicalFilter(
                input_op=logical_op,
                condition=ast.where_clause.condition
            )
        
        # Apply GROUP BY if present
        if ast.group_by:
            logical_op = LogicalAggregate(
                input_op=logical_op,
                group_by_columns=ast.group_by.columns
            )
        
        # Apply ORDER BY if present
        if ast.order_by:
            logical_op = LogicalSort(
                input_op=logical_op,
                columns=ast.order_by.columns,
                directions=ast.order_by.directions
            )
        
        # Apply LIMIT if present
        if ast.limit is not None:
            logical_op = LogicalLimit(
                input_op=logical_op,
                limit=ast.limit
            )
        
        # Apply final projection
        logical_op = LogicalProject(
            input_op=logical_op,
            columns=ast.columns
        )
        
        return logical_op
    
    def _build_from_clause(self, from_clause: From) -> LogicalOperator:
        """Build the logical plan for the FROM clause"""
        # Start with the base table
        base_scan = LogicalScan(table=from_clause.table)
        
        # If no joins, return the base scan
        if not from_clause.joins:
            return base_scan
        
        # Build each join
        current_op = base_scan
        for join in from_clause.joins:
            right_scan = LogicalScan(table=join.table)
            current_op = LogicalJoin(
                left=current_op,
                right=right_scan,
                condition=join.condition,
                join_type=join.join_type
            )
        
        return current_op


# Example usage
if __name__ == "__main__":
    from sql_parser import SQLParser
    
    sql = """
    SELECT 
        a.id, 
        b.name 
    FROM 
        table_a AS a 
    JOIN 
        table_b b 
    ON 
        a.id = b.id 
    WHERE 
        a.value > 10 
    ORDER BY 
        a.id DESC
    LIMIT 100
    """
    
    parser = SQLParser(sql)
    ast = parser.parse()
    
    converter = ASTToLogicalConverter()
    logical_plan = converter.convert(ast)
    
    print("AST:")
    print(ast)
    print("\nLogical Plan:")
    print(logical_plan)