from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Union, Set, Type, Callable
from dataclasses import dataclass, field
from enum import Enum
import uuid
from decimal import Decimal
import operator as py_operator
from sqlglot import expressions as sqlglot_exp

@dataclass(frozen=True)
class DataType:
    """Enhanced data type for symbolic execution"""
    name: str
    precision: Optional[int] = None
    scale: Optional[int] = None
    length: Optional[int] = None
    nullable: Optional[bool] = None
    
    def is_numeric(self) -> bool:
        return self.name.upper() in ['INTEGER', 'BIGINT', 'DECIMAL', 'FLOAT', 'DOUBLE', 'NUMERIC']
    
    def is_string(self) -> bool:
        return self.name.upper() in ['VARCHAR', 'CHAR', 'TEXT', 'STRING']
    
    def is_boolean(self) -> bool:
        return self.name.upper() == 'BOOLEAN'
    
    def is_date_time(self) -> bool:
        return self.name.upper() in ['DATE', 'TIME', 'TIMESTAMP', 'DATETIME']
    
    def can_cast_to(self, target_type: 'DataType') -> bool:
        """Check if this type can be cast to target type"""
        if self == target_type:
            return True
        
        # Numeric types can generally be cast to other numeric types
        if self.is_numeric() and target_type.is_numeric():
            return True
        
        # String types can be cast to most other types
        if self.is_string():
            return True
        
        # Most types can be cast to string
        if target_type.is_string():
            return True
        
        return False


class Expression(ABC):
    """Base class for all expressions in symbolic execution"""
    
    def __init__(self, expr_id: Optional[str] = None):
        self._id = expr_id or str(uuid.uuid4())
        self._data_type: Optional[DataType] = None
        self._is_nullable: Optional[bool] = None
        
    @property
    def id(self) -> str:
        return self._id
    
    @property
    def data_type(self) -> Optional[DataType]:
        return self._data_type
    
    @property
    def is_nullable(self) -> Optional[bool]:
        return self._is_nullable
    
    @abstractmethod
    def get_children(self) -> List['Expression']:
        """Return child expressions"""
        pass
    
    @abstractmethod
    def get_referenced_columns(self) -> Set['ColumnRef']:
        """Return all column references in this expression"""
        pass
    
    @abstractmethod
    def evaluate(self, context: 'EvaluationContext') -> Any:
        """Evaluate expression given a context"""
        pass
    
    @abstractmethod
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        """Infer the data type of this expression"""
        pass
    
    @abstractmethod
    def is_deterministic(self) -> bool:
        """Check if expression is deterministic (always returns same result for same input)"""
        pass
    
    @abstractmethod
    def to_sql(self, dialect: str = "sqlite") -> str:
        """Convert expression back to SQL string"""
        pass
    
    def __str__(self) -> str:
        return self.to_sql()
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.id[:8]})"


# Literal Expressions
class LiteralExpression(Expression):
    """Represents literal values"""
    
    def __init__(self, value: Any, data_type: Optional[DataType] = None, **kwargs):
        super().__init__(**kwargs)
        self.value = value
        self._data_type = data_type or self._infer_literal_type(value)
        self._is_nullable = False
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.LITERAL
    
    def get_children(self) -> List[Expression]:
        return []
    
    def get_referenced_columns(self) -> Set['ColumnRef']:
        return set()
    
    def evaluate(self, context: 'EvaluationContext') -> Any:
        return self.value
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        return self._data_type
    
    def is_deterministic(self) -> bool:
        return True
    
    def to_sql(self, dialect: str = "spark") -> str:
        if self.value is None:
            return "NULL"
        elif isinstance(self.value, str):
            return f"'{self.value.replace(\"'\", \"''\")}'"
        elif isinstance(self.value, bool):
            return "TRUE" if self.value else "FALSE"
        else:
            return str(self.value)
    
    def _infer_literal_type(self, value: Any) -> DataType:
        """Infer data type from literal value"""
        if value is None:
            return DataType("NULL")
        elif isinstance(value, bool):
            return DataType("BOOLEAN")
        elif isinstance(value, int):
            return DataType("INTEGER")
        elif isinstance(value, float):
            return DataType("DOUBLE")
        elif isinstance(value, Decimal):
            return DataType("DECIMAL", precision=value.as_tuple().digits.__len__())
        elif isinstance(value, str):
            return DataType("VARCHAR", length=len(value))
        else:
            return DataType("UNKNOWN")


class NullExpression(Expression):
    """Represents NULL values"""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._data_type = DataType("NULL")
        self._is_nullable = True
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.NULL
    
    def get_children(self) -> List[Expression]:
        return []
    
    def get_referenced_columns(self) -> Set['ColumnRef']:
        return set()
    
    def evaluate(self, context: 'EvaluationContext') -> Any:
        return None
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        return self._data_type
    
    def is_deterministic(self) -> bool:
        return True
    
    def to_sql(self, dialect: str = "spark") -> str:
        return "NULL"


# Column Reference Expression
class ColumnRefExpression(Expression):
    """References a column in the current context"""
    
    def __init__(self, column_ref: 'ColumnRef', **kwargs):
        super().__init__(**kwargs)
        self.column_ref = column_ref
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.COLUMN_REF
    
    def get_children(self) -> List[Expression]:
        return []
    
    def get_referenced_columns(self) -> Set['ColumnRef']:
        return {self.column_ref}
    
    def evaluate(self, context: 'EvaluationContext') -> Any:
        return context.get_column_value(self.column_ref)
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        resolved_col = schema_context.resolve_column(self.column_ref)
        if resolved_col:
            self._data_type = resolved_col.data_type
            self._is_nullable = resolved_col.is_nullable
            return self._data_type
        return DataType("UNKNOWN")
    
    def is_deterministic(self) -> bool:
        return True
    
    def to_sql(self, dialect: str = "spark") -> str:
        return str(self.column_ref)


# Binary Operations
class BinaryExpression(Expression):
    """Base class for binary expressions"""
    
    def __init__(self, left: Expression, right: Expression, **kwargs):
        super().__init__(**kwargs)
        self.left = left
        self.right = right
    
    def get_children(self) -> List[Expression]:
        return [self.left, self.right]
    
    def get_referenced_columns(self) -> Set['ColumnRef']:
        return self.left.get_referenced_columns() | self.right.get_referenced_columns()
    
    def is_deterministic(self) -> bool:
        return self.left.is_deterministic() and self.right.is_deterministic()
    
    @abstractmethod
    def get_operator_symbol(self) -> str:
        """Return the SQL operator symbol"""
        pass
    
    @abstractmethod
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        """Apply the binary operation"""
        pass
    
    def evaluate(self, context: 'EvaluationContext') -> Any:
        left_val = self.left.evaluate(context)
        right_val = self.right.evaluate(context)
        
        # Handle NULL values
        if left_val is None or right_val is None:
            return self._handle_null_operation(left_val, right_val)
        
        return self.apply_operation(left_val, right_val)
    
    def _handle_null_operation(self, left_val: Any, right_val: Any) -> Any:
        """Handle NULL values in binary operations (default: return NULL)"""
        return None
    
    def to_sql(self, dialect: str = "spark") -> str:
        return f"({self.left.to_sql(dialect)} {self.get_operator_symbol()} {self.right.to_sql(dialect)})"


# Arithmetic Operations
class AddExpression(BinaryExpression):
    """Addition expression"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.ADD
    
    def get_operator_symbol(self) -> str:
        return "+"
    
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        return left_val + right_val
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        left_type = self.left.infer_type(schema_context)
        right_type = self.right.infer_type(schema_context)
        
        # Simplified type inference for arithmetic
        if left_type.is_numeric() and right_type.is_numeric():
            if left_type.name == "DECIMAL" or right_type.name == "DECIMAL":
                return DataType("DECIMAL", precision=38, scale=10)
            elif left_type.name == "DOUBLE" or right_type.name == "DOUBLE":
                return DataType("DOUBLE")
            else:
                return DataType("BIGINT")
        
        return DataType("UNKNOWN")


class SubtractExpression(BinaryExpression):
    """Subtraction expression"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.SUBTRACT
    
    def get_operator_symbol(self) -> str:
        return "-"
    
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        return left_val - right_val
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        # Similar to AddExpression
        left_type = self.left.infer_type(schema_context)
        right_type = self.right.infer_type(schema_context)
        
        if left_type.is_numeric() and right_type.is_numeric():
            if left_type.name == "DECIMAL" or right_type.name == "DECIMAL":
                return DataType("DECIMAL", precision=38, scale=10)
            elif left_type.name == "DOUBLE" or right_type.name == "DOUBLE":
                return DataType("DOUBLE")
            else:
                return DataType("BIGINT")
        
        return DataType("UNKNOWN")


class MultiplyExpression(BinaryExpression):
    """Multiplication expression"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.MULTIPLY
    
    def get_operator_symbol(self) -> str:
        return "*"
    
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        return left_val * right_val
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        left_type = self.left.infer_type(schema_context)
        right_type = self.right.infer_type(schema_context)
        
        if left_type.is_numeric() and right_type.is_numeric():
            return DataType("DECIMAL", precision=38, scale=10)
        
        return DataType("UNKNOWN")


class DivideExpression(BinaryExpression):
    """Division expression"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.DIVIDE
    
    def get_operator_symbol(self) -> str:
        return "/"
    
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        if right_val == 0:
            raise ValueError("Division by zero")
        return left_val / right_val
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        # Division always returns decimal/double
        return DataType("DOUBLE")


# Comparison Operations
class EqualsExpression(BinaryExpression):
    """Equality comparison"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.EQUALS
    
    def get_operator_symbol(self) -> str:
        return "="
    
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        return left_val == right_val
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        return DataType("BOOLEAN")


class GreaterThanExpression(BinaryExpression):
    """Greater than comparison"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.GREATER_THAN
    
    def get_operator_symbol(self) -> str:
        return ">"
    
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        return left_val > right_val
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        return DataType("BOOLEAN")


class LessThanExpression(BinaryExpression):
    """Less than comparison"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.LESS_THAN
    
    def get_operator_symbol(self) -> str:
        return "<"
    
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        return left_val < right_val
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        return DataType("BOOLEAN")


# Logical Operations
class AndExpression(BinaryExpression):
    """Logical AND"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.AND
    
    def get_operator_symbol(self) -> str:
        return "AND"
    
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        return left_val and right_val
    
    def _handle_null_operation(self, left_val: Any, right_val: Any) -> Any:
        """Handle NULL in AND: NULL AND FALSE = FALSE, otherwise NULL"""
        if left_val is False or right_val is False:
            return False
        return None
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        return DataType("BOOLEAN")


class OrExpression(BinaryExpression):
    """Logical OR"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.OR
    
    def get_operator_symbol(self) -> str:
        return "OR"
    
    def apply_operation(self, left_val: Any, right_val: Any) -> Any:
        return left_val or right_val
    
    def _handle_null_operation(self, left_val: Any, right_val: Any) -> Any:
        """Handle NULL in OR: NULL OR TRUE = TRUE, otherwise NULL"""
        if left_val is True or right_val is True:
            return True
        return None
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        return DataType("BOOLEAN")


# Unary Operations
class UnaryExpression(Expression):
    """Base class for unary expressions"""
    
    def __init__(self, operand: Expression, **kwargs):
        super().__init__(**kwargs)
        self.operand = operand
    
    def get_children(self) -> List[Expression]:
        return [self.operand]
    
    def get_referenced_columns(self) -> Set['ColumnRef']:
        return self.operand.get_referenced_columns()
    
    def is_deterministic(self) -> bool:
        return self.operand.is_deterministic()


class NotExpression(UnaryExpression):
    """Logical NOT"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.NOT
    
    def evaluate(self, context: 'EvaluationContext') -> Any:
        operand_val = self.operand.evaluate(context)
        if operand_val is None:
            return None
        return not operand_val
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        return DataType("BOOLEAN")
    
    def to_sql(self, dialect: str = "spark") -> str:
        return f"NOT ({self.operand.to_sql(dialect)})"


class IsNullExpression(UnaryExpression):
    """IS NULL check"""
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.IS_NULL
    
    def evaluate(self, context: 'EvaluationContext') -> Any:
        operand_val = self.operand.evaluate(context)
        return operand_val is None
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        return DataType("BOOLEAN")
    
    def to_sql(self, dialect: str = "spark") -> str:
        return f"({self.operand.to_sql(dialect)} IS NULL)"


# Function Call Expression
class FunctionCallExpression(Expression):
    """Represents function calls"""
    
    def __init__(self, function_name: str, arguments: List[Expression], **kwargs):
        super().__init__(**kwargs)
        self.function_name = function_name.upper()
        self.arguments = arguments
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.FUNCTION_CALL
    
    def get_children(self) -> List[Expression]:
        return self.arguments
    
    def get_referenced_columns(self) -> Set['ColumnRef']:
        columns = set()
        for arg in self.arguments:
            columns.update(arg.get_referenced_columns())
        return columns
    
    def evaluate(self, context: 'EvaluationContext') -> Any:
        # Get function implementation from context
        func_impl = context.get_function(self.function_name)
        if not func_impl:
            raise ValueError(f"Unknown function: {self.function_name}")
        
        # Evaluate arguments
        arg_values = [arg.evaluate(context) for arg in self.arguments]
        
        return func_impl(*arg_values)
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        # Function type inference would be implementation-specific
        function_registry = schema_context.get_function_registry()
        return function_registry.get_return_type(self.function_name, self.arguments)
    
    def is_deterministic(self) -> bool:
        # Some functions are non-deterministic (e.g., RAND(), NOW())
        non_deterministic = {'RAND', 'RANDOM', 'NOW', 'CURRENT_TIMESTAMP', 'UUID'}
        if self.function_name in non_deterministic:
            return False
        
        return all(arg.is_deterministic() for arg in self.arguments)
    
    def to_sql(self, dialect: str = "spark") -> str:
        args_sql = ", ".join(arg.to_sql(dialect) for arg in self.arguments)
        return f"{self.function_name}({args_sql})"


# Case Expression
class CaseWhenExpression(Expression):
    """CASE WHEN expression"""
    
    @dataclass
    class WhenClause:
        condition: Expression
        result: Expression
    
    def __init__(self, when_clauses: List['CaseWhenExpression.WhenClause'], 
                 else_expr: Optional[Expression] = None, **kwargs):
        super().__init__(**kwargs)
        self.when_clauses = when_clauses
        self.else_expr = else_expr or NullExpression()
    
    def get_expression_type(self) -> ExpressionType:
        return ExpressionType.CASE_WHEN
    
    def get_children(self) -> List[Expression]:
        children = []
        for when_clause in self.when_clauses:
            children.extend([when_clause.condition, when_clause.result])
        children.append(self.else_expr)
        return children
    
    def get_referenced_columns(self) -> Set['ColumnRef']:
        columns = set()
        for child in self.get_children():
            columns.update(child.get_referenced_columns())
        return columns
    
    def evaluate(self, context: 'EvaluationContext') -> Any:
        for when_clause in self.when_clauses:
            condition_result = when_clause.condition.evaluate(context)
            if condition_result is True:
                return when_clause.result.evaluate(context)
        
        return self.else_expr.evaluate(context)
    
    def infer_type(self, schema_context: 'SchemaContext') -> DataType:
        # Return type is the common type of all result expressions
        result_types = []
        for when_clause in self.when_clauses:
            result_types.append(when_clause.result.infer_type(schema_context))
        result_types.append(self.else_expr.infer_type(schema_context))
        
        # Simplified: return the first non-unknown type
        for data_type in result_types:
            if data_type.name != "UNKNOWN":
                return data_type
        
        return DataType("UNKNOWN")
    
    def is_deterministic(self) -> bool:
        for when_clause in self.when_clauses:
            if not when_clause.condition.is_deterministic() or not when_clause.result.is_deterministic():
                return False
        return self.else_expr.is_deterministic()
    
    def to_sql(self, dialect: str = "spark") -> str:
        sql_parts = ["CASE"]
        
        for when_clause in self.when_clauses:
            condition_sql = when_clause.condition.to_sql(dialect)
            result_sql = when_clause.result.to_sql(dialect)
            sql_parts.append(f"WHEN {condition_sql} THEN {result_sql}")
        
        sql_parts.append(f"ELSE {self.else_expr.to_sql(dialect)}")
        sql_parts.append("END")
        
        return " ".join(sql_parts)


# Context Classes for Evaluation and Schema Resolution
class EvaluationContext:
    """Context for evaluating expressions"""
    
    def __init__(self):
        self.column_values: Dict[str, Any] = {}
        self.functions: Dict[str, Callable] = {}
        self._setup_built_in_functions()
    
    def set_column_value(self, column_ref: 'ColumnRef', value: Any) -> None:
        """Set value for a column"""
        self.column_values[str(column_ref)] = value
    
    def get_column_value(self, column_ref: 'ColumnRef') -> Any:
        """Get value for a column"""
        return self.column_values.get(str(column_ref))
    
    def register_function(self, name: str, func: Callable) -> None:
        """Register a function"""
        self.functions[name.upper()] = func
    
    def get_function(self, name: str) -> Optional[Callable]:
        """Get function implementation"""
        return self.functions.get(name.upper())
    
    def _setup_built_in_functions(self) -> None:
        """Setup built-in functions"""
        import math
        
        self.functions.update({
            'ABS': abs,
            'SQRT': math.sqrt,
            'UPPER': lambda s: s.upper() if s else None,
            'LOWER': lambda s: s.lower() if s else None,
            'LENGTH': lambda s: len(s) if s else None,
            'COALESCE': lambda *args: next((arg for arg in args if arg is not None), None)
        })


class SchemaContext:
    """Context for schema resolution and type inference"""
    
    def __init__(self, schema: 'Schema'):
        self.schema = schema
        self.function_registry = FunctionRegistry()
    
    def resolve_column(self, column_ref: 'ColumnRef') -> Optional['ResolvedColumn']:
        """Resolve column reference to column definition"""
        return self.schema.get_column(column_ref)
    
    def get_function_registry(self) -> 'FunctionRegistry':
        """Get function registry for type inference"""
        return self.function_registry


class FunctionRegistry:
    """Registry of functions and their signatures"""
    
    def __init__(self):
        self.functions: Dict[str, Dict[str, Any]] = {}
        self._setup_built_in_functions()
    
    def register_function(self, name: str, return_type: DataType, 
                         arg_types: List[DataType]) -> None:
        """Register a function signature"""
        self.functions[name.upper()] = {
            'return_type': return_type,
            'arg_types': arg_types
        }
    
    def get_return_type(self, name: str, arguments: List[Expression]) -> DataType:
        """Get return type for a function call"""
        func_info = self.functions.get(name.upper())
        if func_info:
            return func_info['return_type']
        return DataType("UNKNOWN")
    
    def _setup_built_in_functions(self) -> None:
        """Setup built-in function signatures"""
        self.register_function('ABS', DataType('DOUBLE'), [DataType('DOUBLE')])
        self.register_function('UPPER', DataType('VARCHAR'), [DataType('VARCHAR')])
        self.register_function('LENGTH', DataType('INTEGER'), [DataType('VARCHAR')])
        # Add more as needed


# Converter from SQLGlot to our Expression system
class SQLGlotExpressionConverter:
    """Converts SQLGlot expressions to our expression system"""
    
    def __init__(self):
        # Mapping of SQLGlot expression types to our converters
        self.converters = {
            sqlglot_exp.Literal: self._convert_literal,
            sqlglot_exp.Column: self._convert_column,
            sqlglot_exp.Add: self._convert_add,
            sqlglot_exp.Sub: self._convert_subtract,
            sqlglot_exp.Mul: self._convert_multiply,
            sqlglot_exp.Div: self._convert_divide,
            sqlglot_exp.EQ: self._convert_equals,
            sqlglot_exp.GT: self._convert_greater_than,
            sqlglot_exp.LT: self._convert_less_than,
            sqlglot_exp.And: self._convert_and,
            sqlglot_exp.Or: self._convert_or,
            sqlglot_exp.Not: self._convert_not,
            sqlglot_exp.Is: self._convert_is_null,
            sqlglot_exp.Case: self._convert_case,
            # Add more mappings as needed
        }
    
    def convert(self, sqlglot_expr: sqlglot_exp.Expression) -> Expression:
        """Convert SQLGlot expression to our expression"""
        expr_type = type(sqlglot_expr)
        
        converter = self.converters.get(expr_type)
        if converter:
            return converter(sqlglot_expr)
        
        # Fallback: try to handle as function call
        if hasattr(sqlglot_expr, 'name'):
            return self._convert_function_call(sqlglot_expr)
        
        # Ultimate fallback: create a generic function call
        return self._convert_unknown_expression(sqlglot_expr)
    
    def _convert_literal(self, expr: sqlglot_exp.Literal) -> LiteralExpression:
        """Convert SQLGlot literal to our literal"""
        if expr.is_string:
            return LiteralExpression(expr.this)
        elif expr.is_int:
            return LiteralExpression(int(expr.this))
        elif expr.is_number:
            return LiteralExpression(float(expr.this))
        else:
            return LiteralExpression(expr.this)
    
    def _convert_column(self, expr: sqlglot_exp.Column) -> ColumnRefExpression:
        """Convert SQLGlot column to our column reference"""
        from . import ColumnRef  # Avoid circular import
        column_ref = ColumnRef(name=expr.name, table_ref=expr.table)
        return ColumnRefExpression(column_ref)
    
    def _convert_add(self, expr: sqlglot_exp.Add) -> AddExpression:
        """Convert SQLGlot addition"""
        left = self.convert(expr.left)
        right = self.convert(expr.right)
        return AddExpression(left, right)
    
    def _convert_subtract(self, expr: sqlglot_exp.Sub) -> SubtractExpression:
        """Convert SQLGlot subtraction"""
        left = self.convert(expr.left)
        right = self.convert(expr.right)
        return SubtractExpression(left, right)
    
    def _convert_multiply(self, expr: sqlglot_exp.Mul) -> MultiplyExpression:
        """Convert SQLGlot multiplication"""
        left = self.convert(expr.left)
        right = self.convert(expr.right)
        return MultiplyExpression(left, right)
    
    def _convert_divide(self, expr: sqlglot_exp.Div) -> DivideExpression:
        """Convert SQLGlot division"""
        left = self.convert(expr.left)
        right = self.convert(expr.right)
        return DivideExpression(left, right)
    
    def _convert_equals(self, expr: sqlglot_exp.EQ) -> EqualsExpression:
        """Convert SQLGlot equality"""
        left = self.convert(expr.left)
        right = self.convert(expr.right)
        return EqualsExpression(left, right)
    
    def _convert_greater_than(self, expr: sqlglot_exp.GT) -> GreaterThanExpression:
        """Convert SQLGlot greater than"""
        left = self.convert(expr.left)
        right = self.convert(expr.right)
        return GreaterThanExpression(left, right)
    
    def _convert_less_than(self, expr: sqlglot_exp.LT) -> LessThanExpression:
        """Convert SQLGlot less than"""
        left = self.convert(expr.left)
        right = self.convert(expr.right)
        return LessThanExpression(left, right)
    
    def _convert_and(self, expr: sqlglot_exp.And) -> AndExpression:
        """Convert SQLGlot AND"""
        left = self.convert(expr.left)
        right = self.convert(expr.right)
        return AndExpression(left, right)
    
    def _convert_or(self, expr: sqlglot_exp.Or) -> OrExpression:
        """Convert SQLGlot OR"""
        left = self.convert(expr.left)
        right = self.convert(expr.right)
        return OrExpression(left, right)
    
    def _convert_not(self, expr: sqlglot_exp.Not) -> NotExpression:
        """Convert SQLGlot NOT"""
        operand = self.convert(expr.this)
        return NotExpression(operand)
    
    def _convert_is_null(self, expr: sqlglot_exp.Is) -> Expression:
        """Convert SQLGlot IS NULL/IS NOT NULL"""
        operand = self.convert(expr.this)
        if isinstance(expr.expression, sqlglot_exp.Null):
            return IsNullExpression(operand)
        else:
            # IS NOT NULL
            return NotExpression(IsNullExpression(operand))
    
    def _convert_case(self, expr: sqlglot_exp.Case) -> CaseWhenExpression:
        """Convert SQLGlot CASE expression"""
        when_clauses = []
        
        for when_expr in expr.ifs:
            condition = self.convert(when_expr.this)
            result = self.convert(when_expr.true)
            when_clauses.append(CaseWhenExpression.WhenClause(condition, result))
        
        else_expr = None
        if expr.default:
            else_expr = self.convert(expr.default)
        
        return CaseWhenExpression(when_clauses, else_expr)
    
    def _convert_function_call(self, expr: sqlglot_exp.Expression) -> FunctionCallExpression:
        """Convert SQLGlot function call"""
        function_name = expr.name if hasattr(expr, 'name') else expr.__class__.__name__
        
        arguments = []
        if hasattr(expr, 'expressions') and expr.expressions:
            arguments = [self.convert(arg) for arg in expr.expressions]
        elif hasattr(expr, 'this') and expr.this:
            arguments = [self.convert(expr.this)]
        
        return FunctionCallExpression(function_name, arguments)
    
    def _convert_unknown_expression(self, expr: sqlglot_exp.Expression) -> Expression:
        """Fallback converter for unknown expressions"""
        # Try to extract any sub-expressions
        children = []
        if hasattr(expr, 'expressions') and expr.expressions:
            children = [self.convert(child) for child in expr.expressions]
        elif hasattr(expr, 'this') and expr.this:
            children = [self.convert(expr.this)]
        
        # Create a generic function call with the class name
        function_name = expr.__class__.__name__.replace('Expression', '').upper()
        return FunctionCallExpression(function_name, children)


# Expression Builder for convenient construction
class ExpressionBuilder:
    """Builder for creating expressions with fluent API"""
    
    @staticmethod
    def literal(value: Any) -> LiteralExpression:
        """Create literal expression"""
        return LiteralExpression(value)
    
    @staticmethod
    def null() -> NullExpression:
        """Create null expression"""
        return NullExpression()
    
    @staticmethod
    def column(name: str, table: Optional[str] = None) -> ColumnRefExpression:
        """Create column reference expression"""
        from . import ColumnRef  # Avoid circular import
        return ColumnRefExpression(ColumnRef(name, table))
    
    @staticmethod
    def add(left: Expression, right: Expression) -> AddExpression:
        """Create addition expression"""
        return AddExpression(left, right)
    
    @staticmethod
    def subtract(left: Expression, right: Expression) -> SubtractExpression:
        """Create subtraction expression"""
        return SubtractExpression(left, right)
    
    @staticmethod
    def multiply(left: Expression, right: Expression) -> MultiplyExpression:
        """Create multiplication expression"""
        return MultiplyExpression(left, right)
    
    @staticmethod
    def divide(left: Expression, right: Expression) -> DivideExpression:
        """Create division expression"""
        return DivideExpression(left, right)
    
    @staticmethod
    def equals(left: Expression, right: Expression) -> EqualsExpression:
        """Create equals expression"""
        return EqualsExpression(left, right)
    
    @staticmethod
    def greater_than(left: Expression, right: Expression) -> GreaterThanExpression:
        """Create greater than expression"""
        return GreaterThanExpression(left, right)
    
    @staticmethod
    def less_than(left: Expression, right: Expression) -> LessThanExpression:
        """Create less than expression"""
        return LessThanExpression(left, right)
    
    @staticmethod
    def and_(left: Expression, right: Expression) -> AndExpression:
        """Create AND expression"""
        return AndExpression(left, right)
    
    @staticmethod
    def or_(left: Expression, right: Expression) -> OrExpression:
        """Create OR expression"""
        return OrExpression(left, right)
    
    @staticmethod
    def not_(operand: Expression) -> NotExpression:
        """Create NOT expression"""
        return NotExpression(operand)
    
    @staticmethod
    def is_null(operand: Expression) -> IsNullExpression:
        """Create IS NULL expression"""
        return IsNullExpression(operand)
    
    @staticmethod
    def function(name: str, *args: Expression) -> FunctionCallExpression:
        """Create function call expression"""
        return FunctionCallExpression(name, list(args))
    
    @staticmethod
    def case() -> 'CaseExpressionBuilder':
        """Start building a CASE expression"""
        return CaseExpressionBuilder()


class CaseExpressionBuilder:
    """Builder for CASE expressions"""
    
    def __init__(self):
        self.when_clauses: List[CaseWhenExpression.WhenClause] = []
        self.else_expression: Optional[Expression] = None
    
    def when(self, condition: Expression, result: Expression) -> 'CaseExpressionBuilder':
        """Add WHEN clause"""
        self.when_clauses.append(CaseWhenExpression.WhenClause(condition, result))
        return self
    
    def else_(self, expression: Expression) -> 'CaseExpressionBuilder':
        """Set ELSE clause"""
        self.else_expression = expression
        return self
    
    def build(self) -> CaseWhenExpression:
        """Build the CASE expression"""
        return CaseWhenExpression(self.when_clauses, self.else_expression)


# Expression Visitor for traversal and analysis
class ExpressionVisitor(ABC):
    """Base visitor for expression trees"""
    
    def __init__(self):
        self._method_cache: Dict[type, Callable] = {}
    
    def visit(self, expression: Expression) -> Any:
        """Visit expression using dynamic dispatch"""
        expr_type = type(expression)
        
        if expr_type in self._method_cache:
            method = self._method_cache[expr_type]
            return method(expression)
        
        method = self._find_visit_method(expression)
        self._method_cache[expr_type] = method
        
        return method(expression)
    
    def _find_visit_method(self, expression: Expression) -> Callable:
        """Find appropriate visit method"""
        # Try class name method
        class_method = f"visit_{expression.__class__.__name__}"
        if hasattr(self, class_method):
            return getattr(self, class_method)
        
        # Try expression type method
        type_method = f"visit_{expression.get_expression_type().value}"
        if hasattr(self, type_method):
            return getattr(self, type_method)
        
        # Default fallback
        return getattr(self, 'visit_default', lambda expr: None)
    
    def traverse(self, expression: Expression, order: str = 'post') -> List[Any]:
        """Traverse expression tree"""
        results = []
        
        if order == 'pre':
            results.append(self.visit(expression))
            for child in expression.get_children():
                results.extend(self.traverse(child, order))
        else:  # post-order
            for child in expression.get_children():
                results.extend(self.traverse(child, order))
            results.append(self.visit(expression))
        
        return results


# Expression Analyzer
class ExpressionAnalyzer(ExpressionVisitor):
    """Analyzes expressions for various properties"""
    
    def __init__(self):
        super().__init__()
        self.column_references = set()
        self.function_calls = []
        self.literal_values = []
        self.max_depth = 0
        self.current_depth = 0
    
    def analyze(self, expression: Expression) -> Dict[str, Any]:
        """Analyze expression and return summary"""
        self.column_references = set()
        self.function_calls = []
        self.literal_values = []
        self.max_depth = 0
        self.current_depth = 0
        
        self.traverse(expression)
        
        return {
            'column_references': list(self.column_references),
            'function_calls': self.function_calls,
            'literal_count': len(self.literal_values),
            'max_depth': self.max_depth,
            'is_deterministic': expression.is_deterministic(),
            'complexity': self._calculate_complexity(expression)
        }
    
    def visit_ColumnRefExpression(self, expr: ColumnRefExpression) -> None:
        """Visit column reference"""
        self.column_references.add(str(expr.column_ref))
    
    def visit_LiteralExpression(self, expr: LiteralExpression) -> None:
        """Visit literal"""
        self.literal_values.append(expr.value)
    
    def visit_FunctionCallExpression(self, expr: FunctionCallExpression) -> None:
        """Visit function call"""
        self.function_calls.append({
            'name': expr.function_name,
            'arg_count': len(expr.arguments)
        })
    
    def visit_default(self, expr: Expression) -> None:
        """Default visit method"""
        self.current_depth += 1
        self.max_depth = max(self.max_depth, self.current_depth)
        
        for child in expr.get_children():
            self.visit(child)
        
        self.current_depth -= 1
    
    def _calculate_complexity(self, expression: Expression) -> str:
        """Calculate expression complexity"""
        total_nodes = len(list(self._get_all_nodes(expression)))
        
        if total_nodes <= 3:
            return "simple"
        elif total_nodes <= 10:
            return "medium"
        else:
            return "complex"
    
    def _get_all_nodes(self, expression: Expression):
        """Generator for all nodes in expression tree"""
        yield expression
        for child in expression.get_children():
            yield from self._get_all_nodes(child)


# Integration with Logical Plan
class ExpressionIntegratedOperator:
    """Mixin to integrate expressions with logical operators"""
    
    def convert_sqlglot_expressions(self, sqlglot_exprs: List[sqlglot_exp.Expression]) -> List[Expression]:
        """Convert SQLGlot expressions to our expressions"""
        converter = SQLGlotExpressionConverter()
        return [converter.convert(expr) for expr in sqlglot_exprs]
    
    def get_all_expressions(self) -> List[Expression]:
        """Get all expressions in this operator"""
        expressions = []
        
        # Each operator type would implement this differently
        if hasattr(self, 'condition') and isinstance(self.condition, Expression):
            expressions.append(self.condition)
        
        if hasattr(self, 'projections'):
            for proj in self.projections:
                if isinstance(proj, Expression):
                    expressions.append(proj)
        
        return expressions
    
    def analyze_expressions(self) -> Dict[str, Any]:
        """Analyze all expressions in this operator"""
        analyzer = ExpressionAnalyzer()
        analysis = {
            'total_expressions': 0,
            'column_references': set(),
            'function_calls': [],
            'complexity_distribution': {'simple': 0, 'medium': 0, 'complex': 0}
        }
        
        for expr in self.get_all_expressions():
            expr_analysis = analyzer.analyze(expr)
            analysis['total_expressions'] += 1
            analysis['column_references'].update(expr_analysis['column_references'])
            analysis['function_calls'].extend(expr_analysis['function_calls'])
            analysis['complexity_distribution'][expr_analysis['complexity']] += 1
        
        analysis['column_references'] = list(analysis['column_references'])
        return analysis


# Example Usage and Testing
def demo_expression_system():
    """Demonstrate the expression system"""
    print("=== Custom Expression System Demo ===\n")
    
    # 1. Create expressions using builder
    print("1. Building expressions:")
    
    # age > 25 AND name = 'John'
    age_col = ExpressionBuilder.column("age", "users")
    name_col = ExpressionBuilder.column("name", "users")
    
    age_condition = ExpressionBuilder.greater_than(age_col, ExpressionBuilder.literal(25))
    name_condition = ExpressionBuilder.equals(name_col, ExpressionBuilder.literal("John"))
    combined_condition = ExpressionBuilder.and_(age_condition, name_condition)
    
    print(f"  Expression: {combined_condition.to_sql()}")
    print(f"  Deterministic: {combined_condition.is_deterministic()}")
    
    # 2. Case expression
    print("\n2. CASE expression:")
    case_expr = (ExpressionBuilder.case()
                .when(ExpressionBuilder.greater_than(age_col, ExpressionBuilder.literal(65)), 
                      ExpressionBuilder.literal("Senior"))
                .when(ExpressionBuilder.greater_than(age_col, ExpressionBuilder.literal(18)), 
                      ExpressionBuilder.literal("Adult"))
                .else_(ExpressionBuilder.literal("Minor"))
                .build())
    
    print(f"  Expression: {case_expr.to_sql()}")
    
    # 3. Function call
    print("\n3. Function call:")
    upper_name = ExpressionBuilder.function("UPPER", name_col)
    print(f"  Expression: {upper_name.to_sql()}")
    
    # 4. Expression analysis
    print("\n4. Expression analysis:")
    analyzer = ExpressionAnalyzer()
    analysis = analyzer.analyze(combined_condition)
    
    print(f"  Column references: {analysis['column_references']}")
    print(f"  Function calls: {analysis['function_calls']}")
    print(f"  Max depth: {analysis['max_depth']}")
    print(f"  Complexity: {analysis['complexity']}")
    
    # 5. Evaluation
    print("\n5. Expression evaluation:")
    context = EvaluationContext()
    context.set_column_value(ColumnRef("age", "users"), 30)
    context.set_column_value(ColumnRef("name", "users"), "John")
    
    result = combined_condition.evaluate(context)
    print(f"  Result: {result}")
    
    # 6. SQLGlot conversion
    print("\n6. SQLGlot conversion:")
    from sqlglot import parse_one
    
    sqlglot_expr = parse_one("age > 25 AND name = 'John'").find(sqlglot_exp.And)
    converter = SQLGlotExpressionConverter()
    
    if sqlglot_expr:
        converted_expr = converter.convert(sqlglot_expr)
        print(f"  Original SQLGlot: {sqlglot_expr}")
        print(f"  Converted: {converted_expr.to_sql()}")
        print(f"  Types match: {type(converted_expr).__name__}")


if __name__ == "__main__":
    demo_expression_system()