"""DataType implementation"""
from __future__ import annotations
from typing import Optional, Any, Union, Dict
from .base import Type, TypeSet, registry
from ..base import Expr
from copy import deepcopy

class DataType(Expr):
    """Type representation in the expression system"""
    arg_types = {
        "this": True,
        "kind": False,
    }

    def __init__(self, name: str, **kwargs):
        super().__init__(**kwargs)
        self.name = name.upper()
        if self.name not in registry.types:
            raise ValueError(f"Unknown type: {self.name}")
            
    def validate_value(self, value: Any) -> bool:
        """Validate that a value matches this type"""
        return registry.validate_value(value, self.name)
        
    def can_coerce_to(self, other: 'DataType') -> bool:
        """Check if this type can be coerced to another"""
        return registry.can_coerce(self.name, other.name)
        
    def get_default_value(self) -> Any:
        """Get the default value for this type"""
        return registry.get_default_value(self.name)
        
    def is_numeric(self) -> bool:
        """Check if this is a numeric type"""
        return registry.is_type_in_set(self.name, 'NUMERIC_TYPES')
        
    def is_text(self) -> bool:
        """Check if this is a text type"""
        return registry.is_type_in_set(self.name, 'TEXT_TYPES')
        
    def __str__(self) -> str:
        return self.name
        
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DataType):
            return NotImplemented
        return self.name == other.name

    def is_type(self, *types: Union[str, Type, DataType]) -> bool:
        """Check if this type matches any of the given types"""
        if self.name not in registry.types:
            return False
            
        for t in types:
            if isinstance(t, str):
                try:
                    t = Type[t.upper()]
                except KeyError:
                    continue
            elif isinstance(t, DataType):
                t = t.name
                
            if t == self.name:
                return True
                
        return False

    @classmethod
    def build(cls, dtype: Union[str, Type, DataType], **kwargs) -> DataType:
        """Construct a DataType instance"""
        if isinstance(dtype, DataType):
            return dtype
            
        if isinstance(dtype, str):
            try:
                name = Type[dtype.upper()]
            except KeyError:
                raise ValueError(f"Unknown type: {dtype}")
        else:
            name = dtype
            
        return cls(name=name, **kwargs)

    def __deepcopy__(self, memo: Dict) -> 'DataType':
        """Create a deep copy of the DataType"""
        if id(self) in memo:
            return memo[id(self)]
            
        new_args = {}
        for key, value in self.args.items():
            new_args[key] = deepcopy(value, memo)
            
        new_type = self.__class__(**new_args)
        
        memo[id(self)] = new_type
        return new_type 