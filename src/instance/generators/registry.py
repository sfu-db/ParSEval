from typing import Dict, Callable, Any, Optional, List, Set
import logging
import threading

logger = logging.getLogger('src.parseval.instance.generators')

class ValueGeneratorRegistry:
    """
    Thread-local registry for value generators by column type.
    Each thread has its own separate registry.
    """
    _thread_local = threading.local()
    
    @classmethod
    def _get_generators(cls) -> Dict[str, Callable]:
        """
        Get the thread-local generators dictionary.
        
        Returns:
            Dict[str, Callable]: The thread-local generators dictionary
        """
        if not hasattr(cls._thread_local, 'generators'):
            cls._thread_local.generators = {}
        return cls._thread_local.generators
    
    @classmethod
    def register(cls, column_type: str, generator_func: Callable) -> None:
        """
        Register a value generator function for a specific column type.
        Thread-local implementation.
        
        Args:
            column_type: The column type (e.g., 'int', 'varchar', etc.)
            generator_func: Function that generates a value for the given type
        """
        generators = cls._get_generators()
        generators[column_type.lower()] = generator_func
        logger.debug(f"Registered generator for column type: {column_type} in thread {threading.get_ident()}")
    
    @classmethod
    def get_generator(cls, column_type: str) -> Optional[Callable]:
        """
        Get the generator function for a specific column type.
        Thread-local implementation.
        
        Args:
            column_type: The column type
            
        Returns:
            Function: The generator function or None if not found
        """
        generators = cls._get_generators()
        generator = generators.get(column_type.lower())
        if generator is None:
            logger.warning(f"No generator found for column type: {column_type} in thread {threading.get_ident()}")
        return generator
    
    @classmethod
    def has_generator(cls, column_type: str) -> bool:
        """
        Check if a generator exists for a specific column type.
        Thread-local implementation.
        
        Args:
            column_type: The column type
            
        Returns:
            bool: True if a generator exists, False otherwise
        """
        generators = cls._get_generators()
        return column_type.lower() in generators
    
    @classmethod
    def clear(cls) -> None:
        """
        Clear all registered generators for the current thread.
        Thread-local implementation.
        """
        generators = cls._get_generators()
        generators.clear()
        logger.debug(f"Cleared all registered generators in thread {threading.get_ident()}")
    
    @classmethod
    def list_registered_types(cls) -> List[str]:
        """
        Get a list of all registered column types for the current thread.
        Thread-local implementation.
        
        Returns:
            List[str]: List of registered column types
        """
        generators = cls._get_generators()
        return list(generators.keys())