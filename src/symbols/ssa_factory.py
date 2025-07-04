import functools
from typing import Dict, Tuple, TypeVar, Type, Union, Any,Set
import logging, z3, datetime
from pathlib import Path
import importlib
from .type_utils import normalize_type, BASE_SYMBOLIC_TYPE_MAPPINGS

logger = logging.getLogger('src.parseval.symbol')

T = TypeVar('T')


SYMBOLIC_TYPES_DIR = Path(__file__).parent / "ztypes"

@functools.lru_cache
def get_supported_symbolic_types() -> Set[str]:
    """Get all supported symbolic type names."""
    return {f.stem[9:].capitalize() for f in SYMBOLIC_TYPES_DIR.glob("symbolic_*.py")}


@functools.lru_cache
def get_symbolic_module(dtype: str) -> Any:
    """
    Get the module for a symbolic type with caching.
    
    Args:
        dtype: Normalized data type name
    """
    module_path = f"src.symbols.ztypes.symbolic_{dtype.lower()}"
    try:
        return importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"Could not import module for type '{dtype}': {str(e)}. "
            f"Supported types: {get_supported_symbolic_types()}"
        )


def create_symbol(dtype: str, 
                 context: Any, 
                 expr: Union[z3.ExprRef, Any], 
                 value: Any) -> Any:
    """Dynamically create an instance of a symbolic data type.
        
        Args:
            dtype: The data type name
            context: The context object
            expr: The Z3 expression or value
            value: The concrete value
            
        Returns:
            An instance of the appropriate symbolic type"""
    normalized_dtype = normalize_type(dtype)
    if normalized_dtype not in BASE_SYMBOLIC_TYPE_MAPPINGS:
        supported = get_supported_symbolic_types()
        raise ValueError(
            f"Normalized type '{normalized_dtype}' not supported. "
            f"Supported types: {supported}"
        )
    
    dtype, z3_type = BASE_SYMBOLIC_TYPE_MAPPINGS[normalized_dtype]
    expr = z3_type(expr) if not z3.is_expr(expr) else expr
    
    module = get_symbolic_module(dtype)
    dtype_class = getattr(module, f"Symbolic{dtype.capitalize()}")
    zval = dtype_class(context, expr=expr, value=value)
    context.set('symbols', {str(expr) : zval})
    return zval

def create_ite(context: Any, 
               condition: Union[bool, Any], 
               t: Any, 
               f: Any) -> Any:
    """
    Create an if-then-else expression.
    
    Args:
        context: The context object
        condition: The condition expression
        t: The true branch value
        f: The false branch value
    """
    c_ = condition.expr if hasattr(condition, 'dtype') else condition
    t_ = t.expr if hasattr(t, 'dtype') else t
    f_ = f.expr if hasattr(f, 'dtype') else f
    tv_ = t.value if hasattr(t, 'dtype') else t
    fv_ = f.value if hasattr(f, 'dtype') else f
    
    e_ = z3.If(c_, t_, f_)
    v_ = tv_ if condition else fv_
    typ = v_.dtype if hasattr(v_, 'dtype') else type(v_).__name__
    
    return create_symbol(typ, context=context, expr=e_, value=v_)


def logical_any(*args: Any) -> Any:
    """Logical OR of all arguments."""

    return functools.reduce(
        lambda x, y: x.logical(y, 'or'), 
        args[1:], 
        args[0]
    ) if args else True

def logical_all(*args: Any) -> Any:
    """Logical AND of all arguments."""
    return functools.reduce(
        lambda x, y: x.logical(y, 'and'), 
        args[1:], 
        args[0]
    ) if args else True



# class ssa_factory:
#     DATATYPE_MAPPINGS: Dict[str, Tuple[str, Type[z3.SortRef]]] = {
#         'bool': ('bool', z3.Bool),
#         'int': ('int', z3.Int),
#         'str': ('string', z3.String),
#         'string': ('string', z3.String),
#         'text': ('string', z3.String),
#         'varchar': ('string', z3.String),
#         'real': ('real', z3.Real),
#         'float': ('real', z3.Real),
#         'decimal': ('real', z3.Real),
#         'bigint': ('int', z3.Int),
#         'datetime': ('datetime', z3.Int),
#         'date': ('datetime', z3.Int)
#     }
#     SYMBOLIC_TYPES_DIR = Path(__file__).parent / "ztypes"

#     @classmethod

#     @classmethod
#     def create_symbol(cls, dtype: str, context, expr: Union[z3.ExprRef, Any], value: Any):
#         """Dynamically create an instance of a symbolic data type.
        
#         Args:
#             dtype: The data type name
#             context: The context object
#             expr: The Z3 expression or value
#             value: The concrete value
            
#         Returns:
#             An instance of the appropriate symbolic type"""
#         dtype = dtype.lower().strip()
#         dtype, z3_type = cls.DATATYPE_MAPPINGS[dtype.lower()]
#         expr = z3_type(expr) if not z3.is_expr(expr) else expr
#         dtype_class_name = f"Symbolic{dtype.capitalize()}"
#         dtype_module_name = f"symbolic_{dtype}"
#         module_path = f"src.symbols.ztypes.{dtype_module_name}"
#         # Lazily load the module
#         try:
#             module = importlib.import_module(module_path)
#         except ImportError as e:
#             raise ImportError(
#                 f"Could not import module {module_path}: {str(e)}. Please ensure the data type is supported by doing SSA_Factory.get_supported_symbolic_types()"
#             )
#         dtype_class = getattr(module, dtype_class_name)
#         zval = dtype_class(context, expr = expr, value = value)
#         context.set('symbols', {str(expr) : zval})
#         return zval
    
#     @classmethod
#     @functools.lru_cache
#     def get_supported_symbolic_types(cls):
#         """List all supported data type names based on files present in the providers directory."""
#         dtype_files = Path(cls.SYMBOLIC_TYPES_DIR).glob("symbolic_*.py")
#         return {f.stem[9:].capitalize() for f in dtype_files}
    
    
#     @classmethod
#     def ite(cls, context, condition, t, f):
#         '''
#             Create a if-then-else expression
#         '''
#         c_ = condition.expr if hasattr(condition, 'dtype') else condition
#         t_ = t.expr if hasattr(t, 'dtype') else t
#         f_ = f.expr if hasattr(f, 'dtype') else f
#         tv_ = t.value if hasattr(t, 'dtype') else t
#         fv_ = f.value if hasattr(f, 'dtype') else f
#         e_ = z3.If(c_, t_, f_)
#         v_ = tv_ if condition else fv_
#         typ = v_.dtype if hasattr(v_, 'dtype') else type(v_).__name__
#         return cls.create_symbol(typ, context= context, expr= e_, value= v_)

#     @classmethod
#     def sany(cls, *args):
#         if args:
#             result = args[0]
#             for i in range(1, len(args)):
#                 result = result.logical(args[i], 'or')
#             return result
#         return True

#     @classmethod
#     def sall(cls, *args):
#         if args:
#             result = args[0]
#             for i in range(1, len(args)):
#                 result = result.logical(args[i], 'and')
#             return result
#         return True



