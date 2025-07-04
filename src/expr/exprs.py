from __future__ import annotations

"""
## Expressions

Every AST node in ParSEval is represented by a subclass of `Expression`.

This module contains the implementation of all supported `Expression` types. Additionally,
it exposes a number of helper functions, which are mainly used to programmatically build
SQL expressions.

The code adapted from sqlgloexpressions.py. Thanks for the authors.

----
"""
from sqlglot import exp
import datetime, textwrap, logging, numbers
from enum import auto
from .helper import AutoName
from .exceptions import *
from typing import Any, Dict, Optional, List, TYPE_CHECKING, Iterator, Union, Sequence, Mapping
from copy import deepcopy
if TYPE_CHECKING:
    from ._typing import SymbolLiterals
from decimal import Decimal

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

class _Expr(type):
    def __new__(cls, clsname, bases, attrs):
        klass = super().__new__(cls, clsname, bases, attrs)
        klass.key = clsname.lower().capitalize()
        klass.__doc__ = klass.__doc__ or ""
        return klass

class ExprVisitor:
    """Base visitor class for traversing and transforming expressions"""
    
    def visit(self, expr: Expr) -> Any:
        """Visit an expression node"""
        method = f'visit_{expr.__class__.__name__}'
        visitor = getattr(self, method, self.generic_visit)
        return visitor(expr)
    
    def generic_visit(self, expr: Expr) -> Any:
        """Default visit method for unhandled expression types"""
        raise NotImplementedError(
            f"No visit method for {expr.__class__.__name__}"
        )

class Expr(metaclass = _Expr):
    key = "Expr"
    arg_types = {"context": True, "this": True, "value": True}
    __slots__ = ("args", "parent", "arg_key", "index", "_type")
    def __init__(self, **args: Any):
        self.args: Dict[str, Any] = args
        self.parent: Optional[Expr] = None
        self.arg_key: Optional[str] = None
        self.index: Optional[int] = None
        for arg_key, value in self.args.items():
            self._set_parent(arg_key, value)
        self._type: Optional[DataType] = self.args.get('_type', None)
    
    @property
    def context(self):
        return self.args.get("context")
    
    @property
    def dtype(self) -> Optional[DataType]:
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
    def operand(self) -> List[Any]:
        """
            Retrieves the argument with key "operand".
        """
        return self.args.get("operand") or []
    @property
    def operands(self) -> List[Any]:
        """
            Retrieves the argument with key "operands".
        """
        return self.args.get("operands") or []

    @property
    def value(self) -> SymbolLiterals:
        return self.args.get("value")

    def copy(self):
        """
        Returns a deep copy of the expression.
        """
        return deepcopy(self)

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


    def _apply_operation(self, op: str, other: Any, expr_class) -> Expr:
        """
        Apply an operation to both symbolic expression and concrete values
        
        Args:
            op: The operator function name ('add', 'sub', etc.)
            other: The other operand
            expr_class: The expression class to create
        """
        other = self._ensure_expr(other)

        # Get concrete values
        left_val = self.value
        right_val = other.value
        new_value = None

        if left_val is not None and right_val is not None:
            try:
                import operator
                op_func = getattr(operator, op)
                new_value = op_func(left_val, right_val)
            except Exception as e:
                logging.warning(f"Failed to compute concrete value for {op}: {e}")

        return expr_class(
            context=self.context,
            this = self,
            operand=other,
            value=new_value
        )
    def _apply_unary_operation(self, op: str, expr_class: type) -> Expr:
        """Apply a unary operation to both symbolic expression and concrete value"""
        new_value = None
        if self.value is not None:
            try:
                import operator
                op_func = getattr(operator, op)
                new_value = op_func(self.value)
            except Exception as e:
                logging.warning(f"Failed to compute concrete value for unary {op}: {e}")

        return expr_class(
            context=self.context,
            this=self,
            value=new_value
        )
    def __lt__(self, other: Any) -> Expr:
        return self._apply_operation('lt', other, LT)

    def __le__(self, other: Any) -> Expr:
        return self._apply_operation('le', other, LTE)

    def __gt__(self, other: Any) -> Expr:
        return self._apply_operation('gt', other, GT)

    def __ge__(self, other: Any) -> Expr:
        return self._apply_operation('ge', other, GTE)

    def __eq__(self, other: Any) -> Expr:
        if other is None:
            return Is_Null(context=self.context, this=self, value=self.value is None)
        return self._apply_operation('eq', other, EQ)

    def __ne__(self, other: Any) -> Expr:
        if other is None:
            return Is_NotNull(context=self.context, this=self, value=self.value is not None)
        return self._apply_operation('ne', other, NEQ)

    # Arithmetic Operations
    def __add__(self, other: Any) -> Expr:
        return self._apply_operation('add', other, Add)

    def __sub__(self, other: Any) -> Expr:
        return self._apply_operation('sub', other, Sub)

    def __mul__(self, other: Any) -> Expr:
        return self._apply_operation('mul', other, Mul)

    def __truediv__(self, other: Any) -> Expr:
        return self._apply_operation('truediv', other, Div)

    def __neg__(self) -> Expr:
        return self._apply_unary_operation('neg', Neg)

    def _compute_and_value(self, other):
        if self.value is None or other.value is None:
            return None
        return bool(self.value and other.value)
    def _compute_or_value(self, other: Expr) -> Optional[bool]:
        """Compute concrete value for OR operation"""
        if self.value is None or other.value is None:
            return None
        return bool(self.value or other.value)
     # Logical Operations
    def and_(self, other: Any) -> Expr:
        other = self._ensure_expr(other)
        if self.value is None or other.value is None:
            value = None
        else:
            value = bool(self.value and other.value)
        result = None
        if isinstance(self, And):
            self.append("operands", other)
            result = self
        elif isinstance(other, And):
            other.append("operands", self)
            result = other
        else:
            result = And(
                context=self.context,
                operands=[self, other],
                value=self._compute_and_value(other)
            )
        result.set("value", value)
        return result

    def or_(self, other: Any) -> Expr:
        other = self._ensure_expr(other)
        if self.value is None or other.value is None:
            value = None
        else:
            value = bool(self.value or other.value)
        if isinstance(self, Or):
            self.append("operands", other)
            result =  self
        elif isinstance(other, Or):
            other.append("operands", self)
            result =  other
        else:
            result = Or(
                context=self.context,
                operands=[self, other],
                value=self._compute_and_value(other)
            )
        result.set("value", value)
        return result
    

    def not_(self) -> Expr:
        return self._apply_unary_operation('not_', Not)

    def __str__(self) -> str:
        """
        Returns a readable string representation of the expression.
        This should be user-friendly and show the expression structure.
        """
        if self.this is None:
            return self.key
        return str(self.this)

    def __repr__(self) -> str:
        """
        Returns a detailed representation of the expression including type and value.
        This should contain all information needed to understand the expression state.
        """
        args_str = []
        for key, value in self.args.items():
            if key in ['this', 'operand', 'operands']:
                continue  # These are handled separately
            args_str.append(f"{key}={value!r}")
        
        type_str = f"type={self.dtype}" if self.dtype else ""
        value_str = f"value={self.value!r}" if self.value is not None else ""        
        details = ", ".join(filter(None, [*args_str, type_str, value_str]))
        if details:
            details = f" [{details}]"
            
        return f"{self.__class__.__name__}({self.this!r}){details}"

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
        result = indent + marker + f"{str(self) } Value = {self.value}" + "\n"
        
        # Prepare the indent for children
        child_indent = indent + ("    " if is_last else "│   ")
        
        # Get all child expressions
        children = list(self.iter_expressions())
        
        # Add each child to the result
        for i, child in enumerate(children):
            is_last_child = i == len(children) - 1
            result += child.tree_str(child_indent, is_last_child)
            
        return result

    def print_tree(self):
        """Print the expression tree to console"""
        print(self.tree_str())

    def accept(self, visitor: ExprVisitor) -> Any:
        """Accept a visitor and return its result"""
        return visitor.visit(self)

    def transform(self, visitor: ExprVisitor) -> 'Expr':
        """
            Transform this expression using a visitor.
            Returns a new expression or self if no transformation needed.
        """
        return visitor.visit(self)

ExpOrStr = Union[str, Expr]

class Condition(Expr):
    """Logical conditions like x AND y, or simply x"""

class Predicate(Condition):
    """Relationships like x = y, x > 1, x >= y."""

# Binary expressions like (ADD a b)
class Binary(Condition):
    arg_types = {"context": True, "this": True, "operand": True, "value": True}

    def __init__(self, **args: Any):
        super().__init__(**args)
        self._validate_operand_types()
        self._validate_concrete_values()
    
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
            # Division always returns float
            return DataType.build("DOUBLE")
            
        elif isinstance(self, (Predicate)):
            return DataType.build("BOOLEAN")
            
        elif isinstance(self, (And, Or)):
            return DataType.build("BOOLEAN")
        return None

    def __str__(self) -> str:
        """Show binary expression in infix notation with proper parentheses"""
        return f"{self.__class__.__name__}({self.left}, {self.right})"


class Nary(Condition):
    arg_types = {"context": True, "operands": True, "value": True}

    def add_operand(self, operand: Any) -> None:
        """Add a new operand to this n-ary expression"""
        operand = self._ensure_expr(operand)
        if "operands" not in self.args:
            self.args["operands"] = []
        self.args["operands"].append(operand)
        self._set_parent("operands", operand, len(self.args["operands"]) - 1)


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

    def __str__(self) -> str:
        if not self.operands:
            return "TRUE"
        if len(self.operands) == 1:
            return str(self.operands[0])
        return f"({' AND '.join(str(op) for op in self.operands)})"
    
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



class Is_Null(Unary, Predicate):
    def __str__(self):
        return f"({self.this} IS NULL)"

class Is_NotNull(Unary, Predicate):
    def __str__(self):
        return f"({self.this} IS NOT NULL)"


class DataType(Expr):
    arg_types = {
        "this": True,
        "expressions": False,
        "nested": False,
        "values": False,
        "prefix": False,
        "kind": False,
    }
    class Type(AutoName):
        ARRAY = auto()
        AGGREGATEFUNCTION = auto()
        SIMPLEAGGREGATEFUNCTION = auto()
        BIGDECIMAL = auto()
        BIGINT = auto()
        BIGSERIAL = auto()
        BINARY = auto()
        BIT = auto()
        BOOLEAN = auto()
        BPCHAR = auto()
        CHAR = auto()
        DATE = auto()
        DATE32 = auto()
        DATEMULTIRANGE = auto()
        DATERANGE = auto()
        DATETIME = auto()
        DATETIME64 = auto()
        DECIMAL = auto()
        DOUBLE = auto()
        ENUM = auto()
        ENUM8 = auto()
        ENUM16 = auto()
        FIXEDSTRING = auto()
        FLOAT = auto()
        GEOGRAPHY = auto()
        GEOMETRY = auto()
        HLLSKETCH = auto()
        HSTORE = auto()
        IMAGE = auto()
        INET = auto()
        INT = auto()
        INT128 = auto()
        INT256 = auto()
        INT4MULTIRANGE = auto()
        INT4RANGE = auto()
        INT8MULTIRANGE = auto()
        INT8RANGE = auto()
        INTERVAL = auto()
        IPADDRESS = auto()
        IPPREFIX = auto()
        IPV4 = auto()
        IPV6 = auto()
        JSON = auto()
        JSONB = auto()
        LONGBLOB = auto()
        LONGTEXT = auto()
        LOWCARDINALITY = auto()
        MAP = auto()
        MEDIUMBLOB = auto()
        MEDIUMINT = auto()
        MEDIUMTEXT = auto()
        MONEY = auto()
        NAME = auto()
        NCHAR = auto()
        NESTED = auto()
        NULL = auto()
        NULLABLE = auto()
        NUMMULTIRANGE = auto()
        NUMRANGE = auto()
        NVARCHAR = auto()
        OBJECT = auto()
        ROWVERSION = auto()
        SERIAL = auto()
        SET = auto()
        SMALLINT = auto()
        SMALLMONEY = auto()
        SMALLSERIAL = auto()
        STRUCT = auto()
        SUPER = auto()
        TEXT = auto()
        TINYBLOB = auto()
        TINYTEXT = auto()
        TIME = auto()
        TIMETZ = auto()
        TIMESTAMP = auto()
        TIMESTAMPLTZ = auto()
        TIMESTAMPTZ = auto()
        TIMESTAMP_S = auto()
        TIMESTAMP_MS = auto()
        TIMESTAMP_NS = auto()
        TINYINT = auto()
        TSMULTIRANGE = auto()
        TSRANGE = auto()
        TSTZMULTIRANGE = auto()
        TSTZRANGE = auto()
        UBIGINT = auto()
        UINT = auto()
        UINT128 = auto()
        UINT256 = auto()
        UMEDIUMINT = auto()
        UDECIMAL = auto()
        UNIQUEIDENTIFIER = auto()
        UNKNOWN = auto()  # Sentinel value, useful for type annotation
        USERDEFINED = "USER-DEFINED"
        USMALLINT = auto()
        UTINYINT = auto()
        UUID = auto()
        VARBINARY = auto()
        VARCHAR = auto()
        VARIANT = auto()
        XML = auto()
        YEAR = auto()

    STRUCT_TYPES = {
        Type.NESTED,
        Type.OBJECT,
        Type.STRUCT,
    }

    NESTED_TYPES = {
        *STRUCT_TYPES,
        Type.ARRAY,
        Type.MAP,
    }

    TEXT_TYPES = {
        Type.CHAR,
        Type.NCHAR,
        Type.NVARCHAR,
        Type.TEXT,
        Type.VARCHAR,
        Type.NAME,
    }

    SIGNED_INTEGER_TYPES = {
        Type.BIGINT,
        Type.INT,
        Type.INT128,
        Type.INT256,
        Type.MEDIUMINT,
        Type.SMALLINT,
        Type.TINYINT,
    }

    UNSIGNED_INTEGER_TYPES = {
        Type.UBIGINT,
        Type.UINT,
        Type.UINT128,
        Type.UINT256,
        Type.UMEDIUMINT,
        Type.USMALLINT,
        Type.UTINYINT,
    }

    INTEGER_TYPES = {
        *SIGNED_INTEGER_TYPES,
        *UNSIGNED_INTEGER_TYPES,
        Type.BIT,
    }

    FLOAT_TYPES = {
        Type.DOUBLE,
        Type.FLOAT,
    }

    REAL_TYPES = {
        *FLOAT_TYPES,
        Type.BIGDECIMAL,
        Type.DECIMAL,
        Type.MONEY,
        Type.SMALLMONEY,
        Type.UDECIMAL,
    }

    NUMERIC_TYPES = {
        *INTEGER_TYPES,
        *REAL_TYPES,
    }

    TEMPORAL_TYPES = {
        Type.DATE,
        Type.DATE32,
        Type.DATETIME,
        Type.DATETIME64,
        Type.TIME,
        Type.TIMESTAMP,
        Type.TIMESTAMPLTZ,
        Type.TIMESTAMPTZ,
        Type.TIMESTAMP_MS,
        Type.TIMESTAMP_NS,
        Type.TIMESTAMP_S,
        Type.TIMETZ,
    }

    @classmethod
    def build(
        cls,
        dtype: DATA_TYPE,
        copy: bool = True,
        **kwargs,
    ) -> DataType:
        """
        Constructs a DataType object.

        Args:
            dtype: the data type of interest.
            dialect: the dialect to use for parsing `dtype`, in case it's a string.
            copy: whether to copy the data type.
            kwargs: additional arguments to pass in the constructor of DataType.
        Returns:
            The constructed DataType object.
        """
        if isinstance(dtype, str):
            if dtype.upper() == "UNKNOWN":
                return DataType(this=DataType.Type.UNKNOWN, **kwargs)
            if hasattr(DataType.Type, dtype.upper()):
                return DataType(this = getattr(DataType.Type, dtype.upper()), kind = dtype, **kwargs)
        elif isinstance(dtype, DataType.Type):
            data_type_exp = DataType(this=dtype)
        elif isinstance(dtype, DataType):
            return dtype
        return DataType(**{**data_type_exp.args, **kwargs})

    def is_type(self, *dtypes: DATA_TYPE) -> bool:
        """
        Checks whether this DataType matches one of the provided data types. Nested types or precision
        will be compared using "structural equivalence" semantics, so e.g. array<int> != array<float>.

        Args:
            dtypes: the data types to compare this DataType to.

        Returns:
            True, if and only if there is a type in `dtypes` which is equal to this DataType.
        """
        for dtype in dtypes:
            other = DataType.build(dtype, copy=False, udt=True)

            if (
                other.operands
                or self.this == DataType.Type.USERDEFINED
                or other.this == DataType.Type.USERDEFINED
            ):
                matches = self == other
            else:
                matches = self.this == other.this

            if matches:
                return True
        return False

DATA_TYPE = Union[str, DataType, DataType.Type]




class Variable(Expr):
    arg_types = {"context": True, "this": True, "value": True}

    def __str__(self) -> str:
        """Format variable name with optional quoting"""
        name = self.this
        return name

    def __repr__(self) -> str:
        type_str = f"type = {self.dtype}" if self.dtype else ""
        value_str = f"value = {self.value!r}" if self.value is not None else ""
        
        details = ", ".join(filter(None, [type_str, value_str]))
        return f"Variable({self.this!r})[{details}]"

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

    def __str__(self) -> str:
        """Format literal based on its type"""
        if self.dtype is None:
            return str(self.this)
        
        if self.dtype.is_type(*DataType.TEXT_TYPES):
            return f"'{self.value}'"
        elif self.dtype.is_type("NULL"):
            return "NULL"
        elif self.dtype.is_type("BOOLEAN"):
            return str(self.value).lower()
        elif self.dtype.is_type(*DataType.TEMPORAL_TYPES):
            return f"'{self.value}'"
        return str(self.value)

    def __repr__(self) -> str:
        type_str = f"type={self.dtype}" if self.dtype else ""
        value_str = f"value={self.value!r}" if self.value is not None else ""
        details = ", ".join(filter(None, [type_str, value_str]))
        return f"Literal({self.this!r})[{details}]"

    def resolve_type(self) -> Optional[DataType]:
        """Literals should already have their type set"""
        return self._type


def convert(value: Any, copy: bool = False, context: Optional[Any] = None) -> Expr:
    """
    Convert any Python value to an appropriate Expr instance.
    
    Args:
        value: The Python value to convert
        copy: Whether to copy if value is already an Expr
        context: Optional context to pass to created expressions
    
    Returns:
        Expr: An appropriate expression instance for the value
    """
    # Handle None/NULL
    if value is None:
        return Literal(
            this="NULL",
            value=None,
            _type=DataType.build("NULL")
        )

    # Return existing expressions (with optional copy)
    if isinstance(value, Expr):
        return maybe_copy(value, copy)
        
    if isinstance(value, bool):
        return Literal.boolean(value)
    if isinstance(value, numbers.Number):
        return Literal.number(value)
    if isinstance(value, float):
        return Literal.number(float(value))
    if isinstance(value, Decimal):
        return Literal(
            this=str(value),
            value=value,
            _type=DataType.build("DECIMAL")
        )
    
    if isinstance(value, str):
        return Literal.string(value)
    
    raise ValueError(f"cannot convert value {value} with type: {type(value)}" )
    

def to_variable(dtype: DATA_TYPE, context, name: ExpOrStr, value: SymbolLiterals, quoted = None, copy = True):
    dtype = DataType.build(dtype)
    if isinstance(name, Variable):
        identifier = maybe_copy(name, copy)
    elif isinstance(name, str):
        identifier = Variable(
            context = context,
            this=name,
            value = value,
            _type = dtype
        )
    else:
        raise ValueError(f"Name needs to be a string or an Variable, got: {name.__class__}")
    return identifier


def maybe_copy(instance, copy=True):
    return instance.copy() if copy and instance else instance

def can_coerce(from_type: DataType, to_type: DataType) -> bool:
    """Check if one type can be safely coerced to another"""
    if from_type == to_type:
        return True
    
    if from_type.is_type(*DataType.INTEGER_TYPES):
        return to_type.is_type(*DataType.NUMERIC_TYPES)
    
    if from_type.is_type(*DataType.NUMERIC_TYPES):
        return to_type.is_type(*DataType.TEXT_TYPES)
    
    return False

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

