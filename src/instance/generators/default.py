import random
import string
from datetime import datetime, timedelta
from typing import Optional, List, Set, Any, Union
import logging
from src.expression.types import DataType
from .registry import ValueGeneratorRegistry

logger = logging.getLogger('src.parseval.instance.generators')

def register_default_generators() -> None:
    """Register default value generators for common column types.
    
    This function registers generators for common SQL data types:
    - Integer types: int, integer
    - Floating point types: float, double, real
    - String types: varchar, char, text
    - Boolean types: boolean, bool
    - Date and time types: date, timestamp, datetime
    
    Each generator supports:
    - Generating random values of the appropriate type
    - Ensuring uniqueness when required
    - Avoiding duplicates with existing values
    
    Note: This function is thread-safe when used with a thread-safe registry.
    """
    
    # Integer generator
    def int_generator(existing_values: Optional[Set[Any]] = None, is_unique: bool = False) -> int:
        """
        Generate a random integer value.        
        Args:
            existing_values: Set of existing values to avoid duplicates if is_unique is True
            is_unique: Whether the value should be unique
            
        Returns:
            int: A random integer value
        """
        value = random.randint(1, 1000)
        if is_unique and existing_values:
            while value in existing_values:
                value = random.randint(1, 1000)
        return value
    
    # Float generator
    def float_generator(existing_values: Optional[Set[Any]] = None, is_unique: bool = False) -> float:
        """
        Generate a random float value.        
        Args:
            existing_values: Set of existing values to avoid duplicates if is_unique is True
            is_unique: Whether the value should be unique            
        Returns:
            float: A random float value
        """
        value = round(random.uniform(0, 100), 2)
        if is_unique and existing_values:
            while value in existing_values:
                value = round(random.uniform(0, 100), 2)
        return value
    
    # String generator
    def string_generator(existing_values: Optional[Set[Any]] = None, is_unique: bool = False, length: int = 10) -> str:
        """
        Generate a random string value.
        
        Args:
            existing_values: Set of existing values to avoid duplicates if is_unique is True
            is_unique: Whether the value should be unique
            length: Length of the generated string
            
        Returns:
            str: A random string value
        """
        chars = string.ascii_letters + string.digits
        value = ''.join(random.choice(chars) for _ in range(length))
        if is_unique and existing_values:
            while value in existing_values:
                value = ''.join(random.choice(chars) for _ in range(length))
        return value
    
    # Boolean generator
    def boolean_generator(existing_values: Optional[Set[Any]] = None, is_unique: bool = False) -> bool:
        """
        Generate a random boolean value.
        
        Args:
            existing_values: Set of existing values to avoid duplicates if is_unique is True
            is_unique: Whether the value should be unique
            
        Returns:
            bool: A random boolean value
        """
        return random.choice([True, False])
    
    # Date generator
    def date_generator(existing_values: Optional[Set[Any]] = None, is_unique: bool = False) -> str:
        """
        Generate a random date value.
        
        Args:
            existing_values: Set of existing values to avoid duplicates if is_unique is True
            is_unique: Whether the value should be unique
            
        Returns:
            str: A random date value in YYYY-MM-DD format
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365*10)
        days_between = (end_date - start_date).days
        random_days = random.randint(0, days_between)
        random_date = start_date + timedelta(days=random_days)
        date_str = random_date.strftime('%Y-%m-%d')
        
        if is_unique and existing_values:
            while date_str in existing_values:
                random_days = random.randint(0, days_between)
                random_date = start_date + timedelta(days=random_days)
                date_str = random_date.strftime('%Y-%m-%d')
                
        return date_str
    
    # Timestamp generator
    def timestamp_generator(existing_values: Optional[Set[Any]] = None, is_unique: bool = False) -> str:
        """
        Generate a random timestamp value.
        
        Args:
            existing_values: Set of existing values to avoid duplicates if is_unique is True
            is_unique: Whether the value should be unique
            
        Returns:
            str: A random timestamp value in YYYY-MM-DD HH:MM:SS format
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365*10)
        days_between = (end_date - start_date).days
        random_days = random.randint(0, days_between)
        random_date = start_date + timedelta(days=random_days)
        timestamp_str = random_date.strftime('%Y-%m-%d %H:%M:%S')
        
        if is_unique and existing_values:
            while timestamp_str in existing_values:
                random_days = random.randint(0, days_between)
                random_date = start_date + timedelta(days=random_days)
                timestamp_str = random_date.strftime('%Y-%m-%d %H:%M:%S')
                
        return timestamp_str
    
    # Register the generators
    
    ValueGeneratorRegistry.register('int', int_generator)
    ValueGeneratorRegistry.register('integer', int_generator)
    ValueGeneratorRegistry.register('float', float_generator)
    ValueGeneratorRegistry.register('double', float_generator)
    ValueGeneratorRegistry.register('real', float_generator)
    ValueGeneratorRegistry.register('varchar', string_generator)
    ValueGeneratorRegistry.register('char', string_generator)
    ValueGeneratorRegistry.register('text', string_generator)
    ValueGeneratorRegistry.register('boolean', boolean_generator)
    ValueGeneratorRegistry.register('bool', boolean_generator)
    ValueGeneratorRegistry.register('date', date_generator)
    ValueGeneratorRegistry.register('timestamp', timestamp_generator)
    ValueGeneratorRegistry.register('datetime', timestamp_generator)