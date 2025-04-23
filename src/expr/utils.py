from __future__ import annotations
from typing import TYPE_CHECKING, List, Union, Optional,Dict, Generator, Any
from sqlglot import exp
import re
if TYPE_CHECKING:
    from ._typing import Symbol
from .base import Expr
from .dtypes.dtype import DataType
from .dtypes.base import Type
from .operators.literal import Variable, Literal




# def visit_expr(
#         e: Union[exp.Expression, Symbol],
#         seen: Optional[Dict[Union[exp.Expression, Symbol], bool]] = None) -> \
#         Generator[Union[exp.Expression, Symbol], None, None]:
#     if seen is None:
#         seen = {}
#     elif e in seen:
#         return

#     seen[e] = True
#     yield e
#     if isinstance(e, Symbol):
#         yield e.expr

#     if z3.is_app(e):
#         for ch in e.children():
#             for e in visit_z3_expr(ch, seen):
#                 yield e
#         return

#     if z3.is_quantifier(e):
#         for e in visit_z3_expr(e.body(), seen):
#             yield e
#         return

# def is_z3_var(e: z3.ExprRef) -> bool:
#     # print(z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED, e)
#     return z3.is_const(e) and e.decl().kind() == z3.Z3_OP_UNINTERPRETED

# def get_all_vars(e: z3.ExprRef) -> Set[z3.ExprRef]:
#     return {sub for sub in visit_z3_expr(e) if is_z3_var(sub)}


def get_all_symbols(expr: Symbol) -> List[exp.Identifier]:
    return set(expr.expr.find_all(exp.Identifier))



def split_conditions(condition: exp.Condition):
    """
    Splits the WHERE condition into individual filters.
    Args:
        condition (exp.Expression): The WHERE condition expression.
    Returns:
        list[exp.Expression]: A list of individual filter conditions.
    """
    if isinstance(condition, exp.Connector):
        return split_conditions(condition.this) + split_conditions(condition.expression)
    return [condition]

def clean_str(s):
    return re.sub(r"[^0-9a-zA-Z+\-*/%=$!() ]|!(?!=)", "", s)

DataTypeArg = Union[str, Type, DataType]

def convert(value: Any, copy: bool = False, context: Optional[Any] = None) -> Expr:
    """Convert any Python value to an appropriate Expr instance"""
    # ... implementation

def to_variable(dtype: DataTypeArg, context, name: str, value: Any, 
               quoted=None, copy=True) -> Variable:
    """Create a new variable with the given type and name"""
    dtype = DataType.build(dtype)
    # ... rest of implementation

def can_coerce(from_type: DataType, to_type: DataType) -> bool:
    """Check if one type can be safely coerced to another"""
    # ... (existing can_coerce implementation)

def validate_value(value: Any, dtype: DataType) -> bool:
    """Validate that a value matches a data type"""
    # ... (existing validate_value implementation)
