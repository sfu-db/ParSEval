from __future__ import annotations
from contextvars import ContextVar
from typing import Dict, List, Tuple, Any, Optional, Set, TYPE_CHECKING
from collections import defaultdict
import z3
if TYPE_CHECKING:
    from src.symbols._typing import Symbols
import logging
logger = logging.getLogger('src.context')
class Context:
    """
    This module provides a thread-safe context management system for handling symbolic expressions, paths, and related data structures in a symbolic execution environment. 
    
    Args:
        z3ctx: z3.Context, z3 Context
        symbols: Dict[str, Symbols], symbols in the context
        paths: List[Symbols], paths in the context
        symbol_to_table: Dict[str, Tuple[str, str]], symbol to table mapping
        pk_fk_symbols: Set[str], primary key and foreign key symbols
        used_symbols: Set[str], used symbols in current path
        positive_branch: Dict[str, List[Any]], positive branch
        negative_branch: Dict[str, Any], negative branch
        symbol_to_tuple_id: Dict[str, Any], symbol to tuple id mapping
        tuple_id_to_symbols: Dict[str, List[Symbols]], tuple id to symbols mapping
    Example Usage:
    >>> context = Context()
    >>> context.set('symbols', {'x': some_symbol})
    >>> with ExprIncrementalTrack(context) as tracker:
    >>>     # Perform operations that may add new expressions
    >>>     context.set('paths', new_path)
    >>>     new_exprs = tracker.get_new_exprs()
    >>> 
    >>> # Reset context state
    >>> context.reset()

    """
    def __init__(self) -> None:
        self.z3ctx: ContextVar[z3.Context] = ContextVar('z3ctx', default = None)
        self.symbols: ContextVar[Dict[str, Symbols]] = ContextVar('symbols', default= {})
        self.paths: ContextVar[List[Symbols]] =  ContextVar('paths', default= [])
        self.symbol_to_table: ContextVar[Dict[str, Tuple[str, str]]] = ContextVar('symbol_to_table', default= {})
        self.pk_fk_symbols: ContextVar[Set[str]] = ContextVar('pk_fk_symbols', default= set())
        self.used_symbols: ContextVar[Set[str]]  = ContextVar('used_symbols', default= set())
        self.positive_branch: ContextVar[Dict[str, List[Any]]] =  ContextVar('positive_branch', default= defaultdict(list))
        self.negative_branch: ContextVar[Dict[str, Any]] =  ContextVar('negative_branch', default= {})
        self.symbol_to_tuple_id: ContextVar[Dict[str, Any]] = ContextVar('symbol_to_tuple_id', default= {})
        self.tuple_id_to_symbols: ContextVar[Dict[str, List[Symbols]]] = ContextVar('tuple_id_to_symbols', default= defaultdict(list))

    def get(self, key: str, subkey: Optional[str] = None):
        ctx_var = getattr(self, key)
        assert ctx_var is not None and isinstance(ctx_var, ContextVar)
        value = ctx_var.get()
        if subkey is not None and isinstance(value, dict):
            return value.get(subkey)
        return value

    def set(self, key:str, *args, **kwargs):
        if args and kwargs:
            raise TypeError('Cannot set both args and kwargs at the same time')
        ctx_var = getattr(self, key)
        
        if not isinstance(ctx_var, ContextVar):
            raise TypeError(f"'{key}' is not a valid context variable")
        current = ctx_var.get()
        if args:
            ctx_var.set(self.__merge(current, *args))
        elif kwargs:
            ctx_var.set(self.__merge(current, **kwargs))

    def __merge(self, current, new):
        if isinstance(current, list):
            value = new if isinstance(new, list) else [new]
            return current + value
        elif isinstance(current, set):
            value = new if isinstance(new, set) else {new}
            return current | value
        elif isinstance(current, defaultdict):
            dummy = current['EMPTY_KEY']
            if isinstance(dummy, list):
                for key, value in new.items():
                    value = value if isinstance(value, list) else [value]
                    current[key].extend(value)
            elif isinstance(dummy, set):
                for key, value in new.items():
                    value = value if isinstance(value, set) else {value}
                    current[key] |= value
            else:
                raise TypeError(f"Cannot merge {type(current)} with {type(new)}")
            return current
        elif isinstance(current, dict):
            current.update(new)
            return current
        elif isinstance(current, set):
            return current | new
        else:
            raise TypeError(f"Cannot merge {type(current)} with {type(new)}")

    def __repr__(self) -> str:
        lines = ["Context("]
        for attr, var in self.__dict__.items():
            if isinstance(var, ContextVar):
                value = var.get()
                value_type = type(value).__name__
                # Format the value based on its type
                if isinstance(value, dict):
                    if len(value) > 3:
                        formatted_value = f"{{{', '.join(f'{k}: {v}' for k, v in list(value.items())[:3])}, ... }}"
                    else:
                        formatted_value = f"{{{', '.join(f'{k}: {v}' for k, v in value.items())}}}"
                elif isinstance(value, (list, set)):
                    if len(value) > 3:
                        formatted_value = f"{list(value)[:3] + ['...']}"
                    else:
                        formatted_value = f"{value}"
                else:
                    formatted_value = f"{value}"
                
                lines.append(f"    {attr}: {value_type} = {formatted_value}")
        
        lines.append(")")
        return '\n'.join(lines)
        
    def __str__(self) -> str:
        """
        Create a simplified string representation of the context's state.
        
        Returns:
            A concise summary of the context state, focusing on key metrics.
        """
        metrics = {
            'symbols_count': len(self.symbols.get()),
            'paths_count': len(self.paths.get()),
            'used_symbols_count': len(self.used_symbols.get()),
            'pk_fk_symbols_count': len(self.pk_fk_symbols.get()),
            'positive_branches': sum(len(v) for v in self.positive_branch.get().values()),
            'negative_branches': len(self.negative_branch.get()),
            'mapped_tuples': len(self.tuple_id_to_symbols.get())
        }
        
        return (
            f"Context Summary:\n"
            f"  Symbols: {metrics['symbols_count']}\n"
            f"  Paths: {metrics['paths_count']}\n"
            f"  Used Symbols: {metrics['used_symbols_count']}\n"
            f"  PK/FK Symbols: {metrics['pk_fk_symbols_count']}\n"
            f"  Positive Branches: {metrics['positive_branches']}\n"
            f"  Negative Branches: {metrics['negative_branches']}\n"
            f"  Mapped Tuples: {metrics['mapped_tuples']}"
        )

    def reset(self):
        new_defaults = {
            'symbols': {},
            'paths': [],
            'symbol_to_table': {},
            'pk_fk_symbols': set(),
            'used_symbols': set(),
            'positive_branch': defaultdict(list),
            'negative_branch': {},
            'symbol_to_tuple_id': {},
            'tuple_id_to_symbols': defaultdict(list)
        }
        for key, default_value in new_defaults.items():
            # if key in args:
            ctx_var = getattr(self, key)
            if isinstance(ctx_var, ContextVar):
                ctx_var.set(default_value)



