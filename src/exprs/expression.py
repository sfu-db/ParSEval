from __future__ import annotations
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .plan import Schema
    from sqlglot.expressions import DATA_TYPE

from dataclasses import dataclass
from typing import Any, Optional
from abc import ABC, abstractmethod

@dataclass(frozen=True)
class Expression(ABC):
    """Base class for expressions in the logical plan"""
    
    @abstractmethod
    def evaluate_type(self, input_schema: 'Schema') -> DATA_TYPE:
        """Return the data type this expression evaluates to"""
        pass

    @abstractmethod
    def accept(self, visitor) -> Any:
        """Accept a visitor for the visitor pattern"""
        pass

@dataclass(frozen=True, slots=True)
class ColumnRef(Expression):
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
    
    def evaluate_type(self, input_schema: 'Schema') -> DATA_TYPE:
        qualified_name = f"{self.table_alias}.{self.column_name}" if self.table_alias else self.column_name
        col = input_schema.get_column(qualified_name) or input_schema.get_column(self.column_name)
        if not col:
            raise ValueError(f"Column '{qualified_name}' not found in the schema")
        return col.data_type


@dataclass(frozen=True, slots=True)
class Literal(Expression):
    """Represents a literal value"""
    value: Any
    data_type: DATA_TYPE

    def evaluate_type(self, input_schema: 'Schema') -> DATA_TYPE:
        return self.data_type
    
@dataclass(frozen=True)
class Condition(Expression):
    """Represents a boolean condition"""
    