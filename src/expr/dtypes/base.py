"""Base type system definitions"""
from __future__ import annotations
from enum import auto
from typing import Dict, Set, Type, Callable, Any, Optional
from ..helper import AutoName

class DataTypeRegistry:
    """Registry for managing data types and their relationships"""
    
    def __init__(self):
        self.types: Dict[str, Type[DataType]] = {}
        self.type_sets: Dict[str, Set[str]] = {}
        self.coercion_rules: Dict[tuple[str, str], bool] = {}
        self.validators: Dict[str, Callable[[Any], bool]] = {}
        self.default_values: Dict[str, Any] = {}
        
    def register_type(self, name: str, 
                     validator: Optional[Callable[[Any], bool]] = None,
                     default_value: Any = None) -> None:
        """
        Register a new data type
        
        Args:
            name: Name of the type (e.g., 'INTEGER', 'FLOAT')
            validator: Function to validate values of this type
            default_value: Default value for this type
        """
        name = name.upper()
        if name in self.types:
            raise ValueError(f"Type {name} already registered")
            
        self.types[name] = name
        if validator:
            self.validators[name] = validator
        if default_value is not None:
            self.default_values[name] = default_value
            
    def register_type_set(self, set_name: str, types: Set[str]) -> None:
        """
        Register a set of related types
        
        Args:
            set_name: Name of the type set (e.g., 'NUMERIC_TYPES')
            types: Set of type names that belong to this set
        """
        self.type_sets[set_name.upper()] = {t.upper() for t in types}
        
    def add_coercion_rule(self, from_type: str, to_type: str) -> None:
        """Register that from_type can be safely coerced to to_type"""
        from_type = from_type.upper()
        to_type = to_type.upper()
        self.coercion_rules[(from_type, to_type)] = True
        
    def can_coerce(self, from_type: str, to_type: str) -> bool:
        """Check if one type can be coerced to another"""
        from_type = from_type.upper()
        to_type = to_type.upper()
        
        if from_type == to_type:
            return True
            
        return self.coercion_rules.get((from_type, to_type), False)
        
    def validate_value(self, value: Any, type_name: str) -> bool:
        """Validate that a value matches a type"""
        type_name = type_name.upper()
        if value is None:
            return True
            
        validator = self.validators.get(type_name)
        if validator:
            return validator(value)
        return True
        
    def get_default_value(self, type_name: str) -> Any:
        """Get the default value for a type"""
        return self.default_values.get(type_name.upper())
        
    def is_type_in_set(self, type_name: str, set_name: str) -> bool:
        """Check if a type belongs to a type set"""
        type_name = type_name.upper()
        set_name = set_name.upper()
        return type_name in self.type_sets.get(set_name, set())

# Global registry instance
registry = DataTypeRegistry()

# Register built-in types
def _register_builtin_types():
    # Basic types
    registry.register_type('INTEGER', 
                         validator=lambda x: isinstance(x, int),
                         default_value=0)
    registry.register_type('FLOAT',
                         validator=lambda x: isinstance(x, (int, float)),
                         default_value=0.0)
    registry.register_type('BOOLEAN',
                         validator=lambda x: isinstance(x, bool),
                         default_value=False)
    registry.register_type('STRING',
                         validator=lambda x: isinstance(x, str),
                         default_value='')
    
    # Type sets
    registry.register_type_set('NUMERIC_TYPES', {'INTEGER', 'FLOAT'})
    registry.register_type_set('TEXT_TYPES', {'STRING'})
    
    # Coercion rules
    registry.add_coercion_rule('INTEGER', 'FLOAT')
    registry.add_coercion_rule('INTEGER', 'STRING')
    registry.add_coercion_rule('FLOAT', 'STRING')
    registry.add_coercion_rule('BOOLEAN', 'STRING')

_register_builtin_types()

class Type(AutoName):
    """Enumeration of all supported data types"""
    ARRAY = auto()
    BIGINT = auto()
    BOOLEAN = auto()
    DATE = auto()
    DATETIME = auto()
    DECIMAL = auto()
    DOUBLE = auto()
    FLOAT = auto()
    INT = auto()
    NULL = auto()
    TEXT = auto()
    # ... (rest of type definitions)

class TypeSet:
    """Collection of related types"""
    INTEGER_TYPES: Set[Type] = {
        Type.BIGINT,
        Type.INT,
        # ... other integer types
    }

    FLOAT_TYPES: Set[Type] = {
        Type.DOUBLE,
        Type.FLOAT,
    }

    NUMERIC_TYPES: Set[Type] = {
        *INTEGER_TYPES,
        *FLOAT_TYPES,
        Type.DECIMAL,
    }

    TEXT_TYPES: Set[Type] = {
        Type.TEXT,
        # ... other text types
    }

    # ... other type sets 