from __future__ import annotations
from sqlglot import exp
import datetime, textwrap, logging
from typing import Any, Optional, Union, TYPE_CHECKING, Callable, Tuple, List, Iterator
from .func import IntToStr, StrToInt, Length
if TYPE_CHECKING:
    from ._typing import SymbolLiterals, SymbolOrLiteral

logger = logging.getLogger('src.parseval.symbol')

NULL_VALUES = {
    'Integer' : 6789,
    'Real' : 0.6789,
    'String' : 'NULL',
    'Boolean' : False,
    'Datetime' : int(round(datetime.datetime(1970, 1, 1, 0, 0, 0).timestamp())),
    'Date' : datetime.date(1970, 1, 1),
}
DEFAULT_VALUES = {
    'Integer' : lambda x: 1,
    'Real' : lambda x: 1,
    'String' : lambda x: str(x),
    'Boolean' : lambda name: True,
    'Datetime' : lambda x: int(round(datetime.datetime(1970, 1, 1, 0, 0, 0).timestamp())),
    'Date' : lambda x: datetime.date(1970, 1, 1)
}
BASE_DT = datetime.datetime(1970, 1, 1, hour= 0, minute= 0, second= 0)
ExpOrStr = Union[str, exp.Expression]

class _Symbol(type):
    def __new__(cls, clsname, bases, attrs):
        klass = super().__new__(cls, clsname, bases, attrs)
        klass.dtype = clsname.lower().capitalize()
        klass.__doc__ = klass.__doc__ or ""
        return klass

class Symbol(metaclass = _Symbol):
    key = "symbol"
    def __init__(self, context, expr: ExpOrStr, value: SymbolLiterals):
        assert isinstance(expr, (str, exp.Expression)), expr
        self.context = context
        self.expr: exp.Expression = exp.to_identifier(expr) if isinstance(expr, str) else expr
        self._value: SymbolLiterals = value
        # self.parent = None

    @property
    def value(self) -> SymbolLiterals:
        if self._value is None:
            return NULL_VALUES[self.dtype]
        return self._value
    @value.setter
    def value(self, value: SymbolLiterals):
        self._value = value
    def __repr__(self) -> str:
        return to_str(self, verbose = True)
    def __str__(self) -> str:
        return  to_str(self)
    def _zv(self, other: SymbolOrLiteral) -> Tuple[exp.Expression, SymbolLiterals]:
        if isinstance(other, Symbol):
            return other, other.value
        return exp.convert(other), other

    def __deepcopy__(self, memo):
        import copy
        
        v_ = copy.deepcopy(self.value)
        e_ = None
        
        if isinstance(self.expr, exp.Identifier):
            e_  = exp.Identifier(this = self.expr.this)
            root = self.__class__(context = self.context, expr = e_, value = v_)
            return root
        
        q = [self.expr]
        stack1 = []
        stack2 = []
        while q:
            node = q.pop()
            if isinstance(node, Symbol):
                # node.__class__(context = node.context, expr = )
                expr = None
                value = None
            elif isinstance(node, exp.Expression):
                ...
            else:
                raise ValueError(f'unknown data types {node} in {self}')
            
            if isinstance(node.expr, exp.Expression) and not isinstance(node.expr, exp.Identifier):
                args = [v for _, v in node.expr.args.items() if isinstance(v, Symbol)]
                for v in reversed(args):
                    q.append(v)


    def _make_bool_ops(self, other: Any, op: str, klass: Callable) -> Symbol:
        """
        Create a comparison operation.
        Args:
            other: Value to compare with
            op: Comparison operator            
        Returns:
            New symbolic boolean value
        """
        e, v = self._zv(other)
        value = getattr(self.value, f"__{op}__")(v)
        return Boolean(self.context, klass(this=self, expression = e), value = value)

    def binary_op(self, klass: Callable, e: exp.Expression, value: SymbolLiterals, **kwargs) -> Symbol:
        return self.__class__(self.context, klass(this=self, expression = e), value = value)
    

    def dfs(self, prune: Optional[Callable[[Symbol], bool]] = None) -> Iterator[Symbol]:
        """
        Returns a generator object which visits all nodes in this tree in
        the DFS (Depth-first) order.
        Returns:
            The generator object.
        """
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            if prune and prune(node):
                continue

            if isinstance(node.expr, exp.Expression) and not isinstance(node.expr, exp.Identifier):
                args = [v for _, v in node.expr.args.items() if isinstance(v, Symbol)]
                for v in reversed(args):
                    stack.append(v)

BOOL_OPS = [
    ('eq', exp.EQ),
    # '__req__',
    ('ne', exp.NEQ),
    # '__rne__',
    ('gt', exp.GT),
    ('lt', exp.LT),
    ('le', exp.LTE),
    ('ge', exp.GTE),
]

for (name, klass) in BOOL_OPS:
    def make_method(op_name: str, klass: Callable) -> Callable:
        def method(self, other: Any) -> Symbol:
            return self._make_bool_ops(other, op_name, klass)
        return method
    setattr(Symbol, f"__{name}__", make_method(name, klass))
    
class Boolean(Symbol):
    def __bool__(self):
        r = True if self.value else False
        q = [self]
        while q:
            e = q.pop(0)
            if isinstance(e.expr, exp.Connector):
                left = e.expr.left
                right = e.expr.right
                q.append(left)
                q.append(right)
            else:
                self.context.set('paths', e)
        return r
    def and_(self, other: Any) -> Symbol:
        e, v = self._zv(other)
        return Boolean(self.context, exp.And(this = self, expression = e), value = self.value and v)
    
    def or_(self, other: Any) -> Symbol:
        e, v = self._zv(other)
        return Boolean(self.context, exp.Or(this = self, expression = e), value = self.value or v)
    
    def __not__(self):
        assert isinstance(self.expr, exp.Expression), self
        
        if isinstance(self.expr, exp.Not):
            return self.expr.this
        v_ = not self.value
        e_ = exp.Not(this = self)
        return Boolean(self.context, e_, value = v_)
    
    def __add__(self, other):
        z, v = self._zv(other)
        v_ = self.value + v
        return self.bool_op(exp.Add, z, v_)
        
    def __radd__(self, other):
        return self.__add__(other)

class Integer(Symbol):
    def __int__(self):
        return self.value
    
    def __bool__(self):
        if self != 0:
            return True
        return False
    
    def to_str(self):
        v_ = str(self.value)
        e_ = IntToStr(self.expr)
        return String(self.context, e_, v_)
    
    def _make_binary_ops(self, other: Any, op: str, klass: Callable) -> Symbol:
        """
        Create a binary operation.
        Args:
            other: Value to compare with
            op: Comparison operator            
        Returns:
            New symbolic boolean value
        """
        e, v = self._zv(other)
        v_ = getattr(self.value, f"__{op}__")(v)
        return self.binary_op(klass, e, v_)

ARITH_OPS =  [("add", exp.Add),\
	("sub",    exp.Sub),\
	("mul",    exp.Mul),\
	("mod",    exp.Mod),\
    ("truediv", exp.Div),\
	("floordiv", exp.Div )]

for (name, klass) in ARITH_OPS:
    def make_method(op_name: str, klass: Callable) -> Callable:
        def method(self, other: Any) -> Symbol:
            return self._make_binary_ops(other, op_name, klass)
        return method
    setattr(Integer, f"__{name}__", make_method(name, klass))
    setattr(Integer, f"__r{name}__", make_method(name, klass))


class Real(Integer):
    def __float__(self):
        return self.value

class String(Symbol):
    @property
    def length(self):
        return Integer(self.context, Length(this = self.expr), value = len(self.value))
    
    def to_int(self):
        v_ = int(self.value)
        e_ = StrToInt(self.expr)
        return Integer(self.context, e_, value = v_)

    def like(self, pattern: str):
        raise NotImplementedError("Like is not implemented for String")
    
    def substring(self, start: int, length: int):
        raise NotImplementedError("Substring is not implemented for String")

class Datetime(Integer):
    @property
    def date_fmt(self):
        return self.date_fmt_
    @date_fmt.setter
    def date_fmt(self, value: str):
        self.date_fmt_ = value


def to_symbol(name, quoted=None, copy=True):
    """
        Builds a symbol.
        Args:
            name: The name to turn into a symbol.
            quoted: Whether to force quote the symbol.
            copy: Whether to copy name if it's an Symbol.
        Returns:
            The symbol ast node.
    """
    if name is None:
        return None
    
    # if isinstance(name, Symbol):
    #     symbol = name
    # elif isinstance(name, str):
    #     symbol = 

    

# def to_identifier(name, quoted=None, copy=True):
#     """Builds an identifier.

#     Args:
#         name: The name to turn into an identifier.
#         quoted: Whether to force quote the identifier.
#         copy: Whether to copy name if it's an Identifier.

#     Returns:
#         The identifier ast node.
#     """

#     if name is None:
#         return None

#     if isinstance(name, Identifier):
#         identifier = maybe_copy(name, copy)
#     elif isinstance(name, str):
#         identifier = Identifier(
#             this=name,
#             quoted=not SAFE_IDENTIFIER_RE.match(name) if quoted is None else quoted,
#         )
#     else:
#         raise ValueError(f"Name needs to be a string or an Identifier, got: {name.__class__}")
#     return identifier



def create_symbol(dtype: str, context, expr: ExpOrStr, value: SymbolLiterals):
    
    dtype = exp.DataType.build(dtype)    
    if dtype.is_type(*exp.DataType.INTEGER_TYPES):
        value = value or DEFAULT_VALUES['Integer'](expr)
        return Integer(context, expr, value)
    elif dtype.is_type(*exp.DataType.REAL_TYPES):
        value = value or DEFAULT_VALUES['Real'](expr)
        return Real(context, expr, value)
    elif dtype.is_type(*exp.DataType.TEXT_TYPES):
        value = value or DEFAULT_VALUES['String'](expr)
        return String(context, expr, value)
    elif dtype.is_type(exp.DataType.Type.BOOLEAN):
        value = value or DEFAULT_VALUES['Boolean'](expr)
        return Boolean(context, expr, value)
    elif dtype.is_type(*exp.DataType.TEMPORAL_TYPES):
        value = value or DEFAULT_VALUES['Datetime'](expr)
        return Datetime(context, expr, value)
    raise ValueError(f"Unsupported dtype: {dtype}")


def distinct(symbols: List[Symbol]) -> Symbol:
    return exp.Distinct(exoressions = symbols)

import functools



def sany(*symbols) -> Symbol:
    """Logical OR of all arguments."""

    return functools.reduce(lambda x, y: x.or_(y), symbols)

def sall(*symbols) -> Symbol:
    return functools.reduce(lambda x, y: x.and_(y), symbols)




def visit_expr(e: Symbol, seen = None):
    if seen is None:
        seen = {}
    elif e in seen:
        return
    seen[e] = True
    yield e
    if isinstance(e.expr, exp.Expression) and not isinstance(e.expr, exp.Identifier):
        args = {v for _, v in e.expr.args.items() if isinstance(v, Symbol)}        
        for v in args:            
            yield from visit_expr(v, seen)
    return 

def is_symbol(expr) -> bool:
    return isinstance(expr, Symbol) and isinstance(expr.expr, exp.Identifier)

def get_all_symbols(expr: Symbol) -> List[Symbol]:    
    return {sub for sub in expr.dfs() if is_symbol(sub)}



def substitute(expr: Symbol, src, tar) -> Symbol:
        '''
            Substitute the free variables in the expression with the given values.
        '''
        assert isinstance(expr, Symbol), expr
        assert isinstance(src, Symbol), src
        assert isinstance(tar, Symbol), tar
        from copy import deepcopy
        expr2 = deepcopy(expr)
        for symbol in expr2.dfs():
            if symbol.expr == src.expr:
                symbol.expr = tar.expr
                symbol.value = tar.value
        return expr2

def extend_summation(expr: Symbol, src, tar) -> Symbol:
    '''
        Extend the existing expression with new variables.
        The new variables are added to the existing expression.
        Args:
            existing_expr: The existing expression.
            varis: The new variables (src, tar) to add to the expression.
        Returns:
            The extended expression.
        Example Usage:
        >>> expr: Or(Or(a0 == b0, a0 == b1), Or(a0 == b2, a0 == b3))
        >>> src: a0
        >>> tar: a1
        >>> result: Or(Or(a0 == b0, a0 == b1), Or(a0 == b2, a0 == b3), a1 == b0, a1 == b1, a1 == b2, a1 == b3)        
    '''
    existing_symbols = []

    atomic_exprs = []
    for symbol in expr.dfs():
        if isinstance(symbol.expr, (exp.EQ, exp.NEQ, exp.GT, exp.LT, exp.LTE, exp.GTE)):
            # Check if this comparison involves our source symbol
            if any(sub.expr == src.expr for sub in symbol.dfs() if is_symbol(sub)):
                atomic_exprs.append(symbol)

    new_exprs = set()
    for atomic in atomic_exprs:
        print(f'atomic: {atomic}')
        new_expr = substitute(atomic, src, tar)
        print(f'after sub atomic: {atomic}')
        print(f'new : {new_expr}')

        new_exprs.add(new_expr)

    # Combine the new expressions with the original expression
    if isinstance(expr.expr, exp.Or):
        result = expr
        for new_expr in new_exprs:
            result = result.or_(new_expr)
        return result
    elif isinstance(expr.expr, exp.And):
        # If the original expression is an AND, add all new expressions to it
        result = expr
        for new_expr in new_exprs:
            result = result.and_(new_expr)
        return result
    else:
        # If the original expression is not a connector, create a new connector
        if new_exprs:
            # Determine if we should use OR or AND based on the expression type
            if isinstance(expr.expr, (exp.EQ, exp.NEQ)):
                # For equality/inequality, typically OR makes more sense
                result = expr
                for new_expr in new_exprs:
                    result = result.or_(new_expr)
                return result
            else:
                # For other comparisons, AND might be more appropriate
                result = expr
                for new_expr in new_exprs:
                    result = result.and_(new_expr)
                return result
        return expr
    
    # if z3.is_or(existing_expr):
    #     existing_terms.extend(existing_expr.children())
    # elif z3.is_and(existing_expr):
    #     existing_terms.extend(existing_expr.children())
    #     op = z3.And
    # else:
    #     existing_terms.append(existing_expr)
    new_terms = set()


    ...

def to_str(node: Any, verbose: bool = False, level: int = 0) -> str:
    indent =  ("" * (level + 1))
    delim = f",{indent}"
    if isinstance(node, Symbol):
        if verbose:
            return "%s(%s, %s)" % (node.dtype, to_str(node.expr, verbose ), node.value)
        else:
            return  to_str(node.expr, verbose )
    elif isinstance(node, exp.Identifier):
        return f"{node.__class__.__name__}({node.this})" if verbose else str(node.this)
    elif isinstance(node, exp.Literal):
        return " " + repr(node) if verbose else  node.sql() 
    
    elif isinstance(node, exp.Expression):
        args = {k: v for k, v in node.args.items() if (isinstance(v, Symbol) or (v is not None and v != [])) or verbose}
        # Inline leaves for a more compact representation
        if node.is_leaf():
            indent = ""
            delim = ", "
        items = delim.join([f"{to_str(v, verbose, level + 1)}" for k, v in args.items()])
        return f"{node.__class__.__name__}({indent}{items})"
    
    if isinstance(node, list):
        items = delim.join(to_str(i, verbose, level + 1) for i in node)
        items = f"{indent}{items}" if items else ""
        return f"[{items}]"
    return indent.join(textwrap.dedent(str(node).strip("\n")).splitlines())