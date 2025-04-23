from __future__ import annotations

"""
## Expressions

Every AST node in ParSEval is represented by a subclass of `Expression`.
The code adapted from sqlglo expressions.py. Thanks for the authors.

----
"""
from ..types import DATA_TYPE, DataType, SymbolLiterals, can_coerce
from typing import Any, Dict, Optional, List, TYPE_CHECKING, Iterator, Union, Sequence, Mapping
from ..exceptions import *
from decimal import Decimal
import numbers, datetime, logging
from copy import deepcopy
if TYPE_CHECKING:
    from ..visitors.base import ExprVisitor

class _Expr(type):
    def __new__(cls, clsname, bases, attrs):
        klass = super().__new__(cls, clsname, bases, attrs)
        klass.key = clsname.lower().capitalize()
        klass.__doc__ = klass.__doc__ or ""
        return klass

class Expr(metaclass=_Expr):
    """Base class for all expressions"""
    key = "Expr"
    arg_types = {"this": True, "value": True}
    __slots__ = ("args", "parent", "arg_key", "index", "_type")
    def __init__(self, **args: Any):
        self.args: Dict[str, Any] = args
        self.parent: Optional[Expr] = None
        self.arg_key: Optional[str] = None
        self.index: Optional[int] = None
        for arg_key, value in self.args.items():
            self._set_parent(arg_key, value)
        self._type: Optional['DataType'] = self.args.get('_type', None)

    @property
    def dtype(self) -> Optional['DataType']:
        """Get the data type of this expression"""
        if self._type is None:
            self._type = self.resolve_type()
        return self._type
    
    def resolve_type(self) -> Optional[DataType]:
        """
            Resolve the type of this expression based on its contents.
            Should be overridden by subclasses.
        """
        return None

    def is_type(self, *dtypes) -> bool:
        return self.dtype is not None and self.dtype.is_type(*dtypes)
    
    @property
    def this(self):
        return self.args.get("this")
    @property
    def operand(self) -> Any:
        return self.args.get("operand")
    @property
    def operands(self) -> List[Any]:
        return self.args.get("operands") or []
    
    def accept(self, visitor: 'ExprVisitor') -> Any:
        """Accept a visitor and return its result"""
        return visitor.visit(self)

    def transform(self, visitor: 'ExprVisitor') -> 'Expr':
        """Transform this expression using a visitor"""
        return visitor.visit(self)
    
    @property
    def value(self) -> SymbolLiterals:
        return self.args.get("value")

    def _set_parent(self, arg_key: str, value: Any, index: Optional[int] = None) -> None:
        if hasattr(value, "parent"):
            value.parent = self
            value.arg_key = arg_key
            value.index = index
        elif type(value) is list:
            for index, v in enumerate(value):
                if hasattr(v, "parent"):
                    v.parent = self
                    v.arg_key = arg_key
                    v.index = index

    def append(self, arg_key: str, value: Any) -> None:
        """
        Appends value to arg_key if it's a list or sets it as a new list.

        Args:
            arg_key (str): name of the list expression arg
            value (Any): value to append to the list
        """
        if type(self.args.get(arg_key)) is not list:
            self.args[arg_key] = []
        self._set_parent(arg_key, value)
        values = self.args[arg_key]
        if hasattr(value, "parent"):
            value.index = len(values)
        values.append(value)

    def set(self, arg_key: str, value: Any, index: Optional[int] = None) -> None:
        """
        Sets arg_key to value.

        Args:
            arg_key: name of the expression arg.
            value: value to set the arg to.
            index: if the arg is a list, this specifies what position to add the value in it.
        """
        if index is not None:
            expressions = self.args.get(arg_key) or []
            if value is None:
                expressions.pop(index)
                for v in expressions[index:]:
                    v.index = v.index - 1
                return
            if isinstance(value, list):
                expressions.pop(index)
                expressions[index:index] = value
            else:
                expressions[index] = value
            value = expressions
        elif value is None:
            self.args.pop(arg_key, None)
            return
        self.args[arg_key] = value
        self._set_parent(arg_key, value, index)
    
    def copy(self):
        """
        Returns a deep copy of the expression.
        """
        return deepcopy(self)


    def __deepcopy__(self, memo):
        root = self.__class__()
        stack = [(self, root)]

        while stack:
            node, copy = stack.pop()
            if node._type is not None:
                copy._type = deepcopy(node._type)
            for k, vs in node.args.items():
                if hasattr(vs, "parent"):
                    stack.append((vs, vs.__class__()))
                    copy.set(k, stack[-1][-1])
                elif type(vs) is list:
                    copy.args[k] = []
                    for v in vs:
                        if hasattr(v, "parent"):
                            stack.append((v, v.__class__()))
                            copy.append(k, stack[-1][-1])
                        else:
                            copy.append(k, v)
                else:
                    copy.args[k] = vs
        return root

    @property
    def depth(self) -> int:
        """
        Returns the depth of this tree.
        """
        if self.parent:
            return self.parent.depth + 1
        return 0

    def iter_expressions(self, reverse: bool = False) -> Iterator[Expr]:
        """Yields the key and expression for all arguments, exploding list args."""
        for vs in reversed(tuple(self.args.values())) if reverse else self.args.values():
            if type(vs) is list:
                for v in reversed(vs) if reverse else vs:
                    if hasattr(v, "parent"):
                        yield v
            else:
                if hasattr(vs, "parent"):
                    yield vs

    def _ensure_expr(self, other: Any) -> Expr:
        """Ensure the other value is an Expr by converting if necessary"""
        if isinstance(other, Expr):
            return other
        return convert(other)
    
    def _binop(self, op: str, other: Any, expr_class) -> Expr:
        """
        Apply an operation to both symbolic expression and concrete values
        
        Args:
            op: The operator function name ('add', 'sub', etc.)
            other: The other operand
            expr_class: The expression class to create
        """
        other = self._ensure_expr(other)
        left_val = self.value
        right_val = other.value
        new_value = None

        if left_val is not None and right_val is not None:
            try:
                import operator
                op_func = getattr(operator, op)
                new_value = op_func(left_val, right_val)
            except Exception as e:
                raise ValueError(f"Operation {op} failed between {left_val} and {right_val}: {str(e)}")
                
        try:
            return expr_class(this=self, operand=other, value=new_value)
        except Exception as e:
            raise ValueError(f"Failed to create {expr_class.__name__} expression: {str(e)}")

    def __lt__(self, other: Any) -> Expr:
        return self._binop('lt', other, LT)

    def __le__(self, other: Any) -> Expr:
        return self._binop('le', other, LTE)

    def __gt__(self, other: Any) -> Expr:
        return self._binop('gt', other, GT)

    def __ge__(self, other: Any) -> Expr:
        return self._binop('ge', other, GTE)
    
    def __eq__(self, other: Any) -> Expr:
        if other is None:
            return Is_Null(this=self, value=self.value is None)
        return self._binop('eq', other, EQ)

    def __ne__(self, other: Any) -> Expr:
        if other is None:
            return Is_NotNull(this=self, value=self.value is not None)
        return self._binop('ne', other, NEQ)
    
    # Arithmetic Operations
    def __add__(self, other: Any) -> Expr:
        return self._binop('add', other, Add)

    def __radd__(self, other: Any) -> Expr:
        if isinstance(other, int) and other == 0:
            return self
        return self._binop('add', other, Add)

    def __sub__(self, other: Any) -> Expr:
        return self._binop('sub', other, Sub)

    def __mul__(self, other: Any) -> Expr:
        return self._binop('mul', other, Mul)

    def __truediv__(self, other: Any) -> Expr:
        return self._binop('truediv', other, Div)
    def __neg__(self) -> Expr:
        new_value = None if self.value is None else - self.value
        return Neg(this = self, value = new_value)
    
    def _logicop(self, op: str, other: Any, expr_class) -> Expr:
        other = self._ensure_expr(other)
        op_func = {'and': lambda x, y: x and y, 
                   'or': lambda x, y: x or y}
        new_value = None
        if self.value is not None and  other.value is not None:
            new_value = bool(op_func[op](self.value, other.value))
        if isinstance(self, expr_class):
            self.append("operands", other)
            self.set("value", new_value)
            return self
        elif isinstance(other, expr_class):
            other.append("operands", self)
            other.set("value", new_value)
            return other
        else:
            result = expr_class(
                operands=[self, other],
                value = new_value
            )
            return result
    def and_(self, other: Any) -> Expr:
        return self._logicop('and', other, And)
    def or_(self, other: Any) -> Expr:
        return self._logicop('or', other, Or)
    def not_(self) -> Expr:
        new_value = None if self.value is None else not self.value
        negation_map = {
            GT: LTE,   # x > y  ->  x <= y
            GTE: LT,   # x >= y ->  x < y
            LT: GTE,   # x < y  ->  x >= y
            LTE: GT,   # x <= y ->  x > y
            EQ: NEQ,   # x = y  ->  x != y
            NEQ: EQ    # x != y ->  x = y
        }
        if self.__class__ in negation_map:
            return negation_map[self.__class__](this=self.left, operand = self.right, value = new_value)
        return Not(this = self, value = new_value)
    
    def tree_str(self, indent: str = "", is_last: bool = True) -> str:
        """
        Returns a tree-like string representation of the expression.
        Args:
            indent: Current indentation string
            is_last: Whether this is the last child of its parent
            
        Returns:
            A formatted string showing the expression tree structure
        """
        # Prepare the prefix for this node
        marker = "└── " if is_last else "├── "
        if not indent:
            marker = ""
        result = indent + marker + f"{str(self) }, Value: {self.value}, type: {self.dtype}" + "\n"

        child_indent = indent + ("    " if is_last else "│   ")
        children = list(self.iter_expressions())
        for i, child in enumerate(children):
            is_last_child = i == len(children) - 1
            result += child.tree_str(child_indent, is_last_child)
        return result
    def __repr__(self) -> str:
        return self.tree_str()
    

    def __str__(self) -> str:
        """
            Returns a readable string representation of the expression.
            This should be user-friendly and show the expression structure.
        """
        from ..visitors.printer import PrinterVistor
        printer = PrinterVistor()
        return self.accept(printer)

    def __hash__(self) -> int:
        return hash(str(self))
    
    def equals(self, other: Any) -> bool:
        """
        Compare two expressions for structural equality.
        Two expressions are equal if they have:
        1. The same class
        2. The same arg_types
        3. Equal values for all required args
        4. Equal children (recursively)
        """
        if not isinstance(other, Expr):
            return False            
        if self.__class__ != other.__class__:
            return False            
        # Compare required args
        for arg_key, is_required in self.arg_types.items():
            if is_required:
                self_val = getattr(self, arg_key)
                other_val = getattr(other, arg_key)                
                if isinstance(self_val, Expr):
                    if not self_val.equals(other_val):
                        return False
                elif self_val != other_val:
                    return False                    
        return True

ExprOrStr = Union[str, Expr]

class Condition(Expr):
    """Logical conditions like x AND y, or simply x"""

class Predicate(Condition):
    """Relationships like x = y, x > 1, x >= y."""


# Binary expressions like (ADD a b)
class Binary(Condition):
    arg_types = {"this": True, "operand": True, "value": True}

    def __init__(self, **args: Any):
        super().__init__(**args)
        # if args:
        #     self._validate_operand_types()
        #     self._validate_concrete_values()
    
    def _validate_operand_types(self):
        """Validate that operand types are compatible for this operation"""
        left_type = self.left.dtype
        right_type = self.right.dtype
        if left_type is None or right_type is None:
            return
        if isinstance(self, (Add, Sub, Mul, Div)):
            if not (left_type.is_type(*DataType.NUMERIC_TYPES) and 
                   right_type.is_type(*DataType.NUMERIC_TYPES)):
                raise TypeMismatchError(
                    f"Arithmetic operation {self.__class__.__name__} requires numeric types, "
                    f"got {left_type} and {right_type}"
                )
        
        elif isinstance(self, (GT, GTE, LT, LTE)):
            if not can_coerce(left_type, right_type) and not can_coerce(right_type, left_type):
                raise TypeMismatchError(
                    f"Comparison operation {self.__class__.__name__} requires compatible types, "
                    f"got {left_type} and {right_type}"
                )
    

    @property
    def left(self) -> Expr:
        return self.args.get("this")

    @property
    def right(self) -> Expr:
        return self.args.get("operand")

    def _validate_concrete_values(self):
        """Validate concrete values match their types"""
        left_val = self.left.value
        right_val = self.right.value
        
        if left_val is not None:
            if not validate_value(left_val, self.left.dtype):
                raise ValueError(
                    f"Left concrete value {left_val} does not match type {self.left.dtype}"
                )
        
        if right_val is not None:
            if not validate_value(right_val, self.right.dtype):
                raise ValueError(
                    f"Right concrete value {right_val} does not match type {self.right.dtype}"
                )

    def resolve_type(self) -> Optional[DataType]:
        """Resolve type for binary operations"""
        left_type = self.left.dtype
        right_type = self.right.dtype
        
        if left_type is None or right_type is None:
            return None
            
        if isinstance(self, (Add, Sub, Mul)):
            # Numeric operation type promotion
            if left_type.is_type(*DataType.REAL_TYPES) or right_type.is_type(*DataType.REAL_TYPES):
                return DataType.build("DOUBLE")
            if left_type.is_type(*DataType.INTEGER_TYPES) and right_type.is_type(*DataType.INTEGER_TYPES):
                return DataType.build("INT")
                
        elif isinstance(self, Div):
            return DataType.build("DOUBLE")
            
        elif isinstance(self, (Predicate)):
            return DataType.build("BOOLEAN")
            
        elif isinstance(self, (And, Or)):
            return DataType.build("BOOLEAN")
        return None

class Nary(Condition):
    arg_types = {"operands": True, "value": True}

    def _validate_operand_types(self):
        """Validate types of all operands"""
        for operand in self.operands:
            if operand.dtype is None:
                continue
            if isinstance(self, (And, Or)) and not operand.dtype.is_type("BOOLEAN"):
                raise TypeMismatchError(
                    f"Logical operation {self.__class__.__name__} requires boolean operands, "
                    f"got {operand.dtype}"
                )

    def _validate_concrete_values(self):
        """Validate concrete values of all operands"""
        for operand in self.operands:
            if operand.value is not None:
                if not validate_value(operand.value, operand.dtype):
                    raise ValueError(
                        f"Operand value {operand.value} does not match type {operand.dtype}"
                    )

    def resolve_type(self) -> Optional[DataType]:
        """N-ary logical operations always return boolean"""
        if isinstance(self, (And, Or)):
            return DataType.build("BOOLEAN")
        return None
    
class And(Nary):
    pass


class Or(Nary):
    pass


class EQ(Binary, Predicate):
    pass

class NEQ(Binary, Predicate):
    pass

class GT(Binary, Predicate):
    pass

class GTE(Binary, Predicate):
    pass
class LT(Binary, Predicate):
    pass

class LTE(Binary, Predicate):
    pass

class Add(Binary):
    pass

class Sub(Binary):
    pass

class Mul(Binary):
    pass

class Div(Binary):
    arg_types = {"this": True, "operand": True, "typed": False, "safe": False}

class Unary(Condition):
    pass

class Not(Unary):
    pass

class Neg(Unary):
    pass

class Distinct(Expr):
    arg_types = {"operands": False, "on": False}



class Is_Null(Unary, Predicate):
    pass

class Is_NotNull(Unary, Predicate):
    pass
    

class Variable(Expr):
    arg_types = {"this": True, "value": True}

    def resolve_type(self) -> Optional[DataType]:
        """Variables should already have their type set"""
        return self._type


class Literal(Expr):
    arg_types = {"this": True, "value": True}

    @classmethod
    def number(cls, number: Union[int, float]) -> Literal:
        """Create a numeric literal"""
        if isinstance(number, int):
            return cls(
                this=str(number),
                _type=DataType.build("INT"),
                value=number
            )
        return cls(
            this=str(number),
            _type=DataType.build("DOUBLE"),
            value=number
        )

    @classmethod
    def string(cls, string) -> Literal:
        return cls(this=f"'{string}'", value=string, _type = DataType.build('TEXT'))
    
    @classmethod
    def boolean(cls, value: bool) -> 'Literal':
        """Create a boolean literal"""
        return cls(
            this=str(value).lower(),
            _type=DataType.build("BOOLEAN"),
            value=value
        )

    def null(cls) -> 'Literal':
        """Create a NULL literal"""
        return cls(
            this="NULL",
            _type=DataType.build("NULL"),
            value=None
        )

    @classmethod
    def datetime(cls, dt: datetime.datetime) -> 'Literal':
        """Create a datetime literal"""
        return cls(
            this=dt.isoformat(),
            _type=DataType.build("DATETIME"),
            value=dt
        )

    def resolve_type(self) -> Optional[DataType]:
        """Literals should already have their type set"""
        return self._type
    


def convert(value: Any, copy: bool = False) -> Expr:
    """
    Convert any Python value to an appropriate Expr instance.
    
    Args:
        value: The Python value to convert
        copy: Whether to copy if value is already an Expr    
    Returns:
        Expr: An appropriate expression instance for the value
    """
    # Handle None/NULL
    if value is None:
        return Literal(this="NULL", value=None, _type=DataType.build("NULL"))
    if isinstance(value, Expr):
        return maybe_copy(value, copy)
    if isinstance(value, bool):
        return Literal.boolean(value)
    if isinstance(value, numbers.Number):
        return Literal.number(value)
    if isinstance(value, str):
        return Literal.string(value)
    raise ValueError(f"cannot convert value {value} with type: {type(value)}" )
    

def maybe_copy(instance, copy=True):
    return instance.copy() if copy and instance else instance


def validate_value(value: Any, dtype: DataType) -> bool:
    """Validate that a value matches a data type"""
    if value is None:
        return True  # None is valid for any type
        
    try:
        if dtype.is_type(*DataType.INTEGER_TYPES):
            int(value)
        elif dtype.is_type(*DataType.REAL_TYPES):
            float(value)
        elif dtype.is_type("BOOLEAN"):
            return isinstance(value, bool)
        elif dtype.is_type(*DataType.TEXT_TYPES):
            return isinstance(value, str)
        elif dtype.is_type("DATETIME"):
            return isinstance(value, datetime.datetime)
        elif dtype.is_type("DATE"):
            return isinstance(value, datetime.date)
        return True
    except (ValueError, TypeError):
        return False


def to_variable(dtype: DATA_TYPE, name: ExprOrStr, value: SymbolLiterals, copy = True):
    dtype = DataType.build(dtype)
    if isinstance(name, Variable):
        identifier = maybe_copy(name, copy)
    elif isinstance(name, str):
        identifier = Variable(this=name, value = value,  _type = dtype)
    else:
        raise ValueError(f"Name needs to be a string or an Variable, got: {name.__class__}")
    return identifier

def distinct(operands: List[Expr]) -> Distinct:
    return Distinct(operands= operands)

def or_(operands: List[Expr]) -> Or:
    return Or(operands= operands)

def and_(operands: List[Expr]) -> And:
    return And(operands= operands)

