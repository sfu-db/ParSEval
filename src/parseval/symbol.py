from __future__ import annotations
from src.parseval.dtype import DataType
from typing import (
    Any,
    List,
    Optional,
    Dict,
    TYPE_CHECKING,
    Iterable,
    Tuple,
    Callable,
)
import logging
import fnmatch

if TYPE_CHECKING:
    from src.parseval.dtype import DATATYPE


class NullValueError(Exception):
    """Raised when a SQL NULL value is used in an invalid context."""

    pass


class _Symbol(type):
    def __new__(cls, clsname, bases, attrs):
        klass = super().__new__(cls, clsname, bases, attrs)
        # When an Expression class is created, its key is automatically set to be
        # the lowercase version of the class' name.
        klass.key = clsname.lower()

        # This is so that docstrings are not inherited in pdoc
        klass.__doc__ = klass.__doc__ or ""

        return klass


class Symbol(metaclass=_Symbol):
    """
    Represents an atomic symbolic expression with a data type.
    """

    OPS = {
        "eq": lambda *args: args[0] == args[1],
        "neq": lambda *args: args[0] != args[1],
        "gt": lambda *args: args[0] > args[1],
        "lt": lambda *args: args[0] < args[1],
        "le": lambda *args: args[0] >= args[1],
        "ge": lambda *args: args[0] <= args[1],
        "like": lambda *args: fnmatch.fnmatch(
            args[0], args[1].replace("%", "*").replace("_", "?")
        ),
        "add": lambda *args: args[0] + args[1],
        "sub": lambda *args: args[0] - args[1],
        "mul": lambda *args: args[0] * args[1],
        "div": lambda *args: args[0] / args[1],
        "floordiv": lambda *args: args[0] / args[1],
        "and": lambda *args: args[0] and args[1],
        "or": lambda *args: args[0] or args[1],
        "not": lambda *args: not args[0],
        "neg": lambda *args: -args[0],
        "is_null": lambda *args: args[0] is None,
        "ite": lambda *args: args[1] if args[0] else args[2],
    }

    key = "symbol"
    __slots__: Tuple[str, ...] = ("args", "dtype", "_concrete", "parent", "metadata")

    def __new__(
        cls,
        *args: Tuple[Any, ...],
        dtype: DATATYPE = None,
        concrete: Any = None,
        **kwargs,
    ):
        obj = super().__new__(cls)
        object.__setattr__(obj, "args", tuple(args))
        object.__setattr__(obj, "dtype", DataType.build(dtype) if dtype else None)
        object.__setattr__(obj, "_concrete", concrete)
        object.__setattr__(obj, "metadata", {})
        obj.metadata.update(kwargs)
        for arg in args:
            if isinstance(arg, Symbol):
                object.__setattr__(arg, "parent", obj)
        return obj

    @property
    def datatype(self):
        return self.dtype

    @property
    def concrete(self):
        mappings = {arg: arg.concrete for arg in self.args if isinstance(arg, Symbol)}
        if self._concrete is None and mappings:
            self.concrete = self.evaluate(mappings)
        return self._concrete

    def __bool__(self):
        if self.concrete is not None:
            return bool(self.concrete)
        raise NullValueError(
            f"Cannot convert symbolic expression {self} to bool without concrete value."
        )

    def evaluate(self, mapping: Dict):
        if self in mapping:
            return mapping[self]
        evaluated_args = tuple(
            arg.evaluate(mapping) if isinstance(arg, Symbol) else arg
            for arg in getattr(self, "args", ())
        )
        return self._eval_concrete(*evaluated_args)

    def _eval_concrete(self, *args):
        try:
            return self.OPS[self.key.lower()](*args)
        except KeyError as e:
            raise NotImplementedError(f"Unknown operator: {self.key.upper()},  {e}")
        except Exception as e:
            return None

    def subs(self, mapping: Dict):
        if self in mapping:
            return mapping[self]
        substituted_args = tuple(
            arg.subs(mapping) if isinstance(arg, Symbol) else arg
            for arg in getattr(self, "args", ())
        )
        return self.__class__(*substituted_args, dtype=self.dtype, **self.metadata)

    def find_any(self, targets: Tuple[Symbol]) -> Optional[Symbol]:
        """Find any instance of target expressions in the tree."""
        stack = [self]
        target_types = tuple(targets)
        while stack:
            current = stack.pop()
            if isinstance(current, target_types):
                return current

            for child in current.args:
                if isinstance(child, Symbol):
                    stack.append(child)
        return None

    def find_all(self, target: "Symbol") -> List["Symbol"]:
        """Find all instances of a target expression in the tree."""
        matches = []
        stack = [self]
        while stack:
            current = stack.pop()
            if isinstance(current, target):
                matches.append(current)

            for child in current.args:
                if isinstance(child, Symbol):
                    stack.append(child)
        return matches

    def iter(self, visited=None) -> Iterable[Symbol]:
        """Depth-first traversal of the symbol tree."""
        if visited is None:
            visited = set()
        if id(self) in visited:
            return
        yield self
        for arg in self.args:
            if isinstance(arg, Symbol):
                yield from arg.iter(visited)

    def __setattr__(self, name, value):
        if name != "concrete":
            raise AttributeError(f"{self.__class__.__name__} is immutable")
        object.__setattr__(self, "_concrete", value)

    def is_number(self):
        return self.dtype.is_type(DataType.NUMERIC_TYPES)

    def is_datetime(self):
        return self.dtype.is_type(*DataType.TEMPORAL_TYPES)

    def __str__(self):
        return f"{self.key.capitalize()}({', '.join(map(str, self.args))})"

    def __repr__(self) -> str:
        return f"{self.key}({', '.join(map(str, self.args))}:{self.dtype})"

    def __eq__(self, other) -> bool:
        if not isinstance(other, Symbol):
            return False
        if self is other:
            return True
        return type(self) == type(other) and self.args == other.args

    def __hash__(self):
        return hash((self.key, tuple(self.args)))

    def __add__(self, other):
        return Add(self, _ensure_symbol(other))

    def __radd__(self, other):
        return Add(_ensure_symbol(other), self)

    def __sub__(self, other):
        return Sub(self, _ensure_symbol(other))

    def __rsub__(self, other):
        return Sub(_ensure_symbol(other), self)

    def __mul__(self, other):
        return Mul(self, _ensure_symbol(other))

    def __rmul__(self, other):
        return Mul(_ensure_symbol(other), self)

    def __truediv__(self, other):
        return Div(self, _ensure_symbol(other))

    def __rtruediv__(self, other):
        return Div(_ensure_symbol(other), self)

    def __floordiv__(self, other):
        return FloorDiv(self, _ensure_symbol(other))

    def __rfloordiv__(self, other):
        return FloorDiv(_ensure_symbol(other), self)

    def __neg__(self):
        return Neg(self)

    def __lt__(self, other):
        return LT(self, _ensure_symbol(other))

    def __le__(self, other):
        return LE(self, _ensure_symbol(other))

    def __gt__(self, other):
        return GT(self, _ensure_symbol(other))

    def __ge__(self, other):
        return GE(self, _ensure_symbol(other))

    def __and__(self, other):
        return And(self, _ensure_symbol(other))

    def __rand__(self, other):
        return And(_ensure_symbol(other), self)

    def __or__(self, other):
        return Or(self, _ensure_symbol(other))

    def __ror__(self, other):
        return Or(_ensure_symbol(other), self)

    # def __xor__(self, other):
    #     return Xor(self, _ensure_symbol(other))

    # def __rxor__(self, other):
    #     return Xor(_ensure_symbol(other), self)

    def __invert__(self):
        return Not(self)

    def and_(self, other):
        return And(self, _ensure_symbol(other), dtype="bool")

    def or_(self, other):
        return Or(self, _ensure_symbol(other), dtype="bool")

    def not_(self):
        return Not(
            self, dtype="bool", concrete=not self.concrete, metadata=self.metadata
        )

    def eq(self, other):
        return EQ(self, _ensure_symbol(other), dtype="bool")

    def ne(self, other):
        return NEQ(self, _ensure_symbol(other), dtype="bool")

    def like(self, pattern: str) -> "Symbol":
        """Create a LIKE comparison symbol."""
        pattern = _ensure_symbol(pattern)
        return LIKE(self, pattern, dtype="bool")

    def is_(self, other: Symbol) -> "Symbol":
        """Create an IS comparison symbol."""
        if other is None:
            return IS_NULL(self, dtype="bool")
        return IS(self, _ensure_symbol(other), dtype="bool")


class Variable(Symbol):
    """
    Represents a symbolic variable with additional attributes.
    """

    @property
    def name(self) -> str:
        return self.args[0]


class Const(Symbol):
    @property
    def value(self) -> Any:
        return self.args[0]

    @property
    def concrete(self):
        return self._concrete if self._concrete is not None else self.value


class Condition(Symbol):
    pass


class Arithmetic(Condition): ...


class Binary(Condition):

    @property
    def left(self) -> Symbol:
        return self.args[0]

    @property
    def right(self) -> Symbol:
        return self.args[1]

    @property
    def concrete(self):
        if self._concrete is None:
            self.concrete = self._eval_concrete(self.left.concrete, self.right.concrete)
        return self._concrete


class Add(Binary, Arithmetic):
    pass


class Sub(Binary, Arithmetic):
    pass


class Mul(Binary, Arithmetic):
    pass


class Div(Binary, Arithmetic):
    pass


class FloorDiv(Binary, Arithmetic):
    pass


class Unary(Symbol):
    @property
    def operand(self) -> Symbol:
        return self.args[0]


class Neg(Unary):
    pass


class Not(Unary):
    pass


class Compare(Binary):
    pass


class LT(Compare):
    pass


class LE(Compare):
    pass


class GT(Compare):
    pass


class GE(Compare):
    pass


class EQ(Compare):
    pass


class NEQ(Compare):
    pass


class LIKE(Compare):
    pass


class IS(Compare):
    pass


class IS_NULL(Unary):
    pass


class And(Binary):
    pass


class Or(Binary):
    pass


class ITE(Symbol):
    @property
    def condition(self) -> Symbol:
        return self.args[0]

    @property
    def true_branch(self) -> Symbol:
        return self.args[1]

    @property
    def false_branch(self) -> Symbol:
        return self.args[2]


class Group(Symbol):
    @property
    def name(self) -> Any:
        return self.args[0]

    def __iter__(self):
        return iter(self.args[1:])

    def __getitem__(self, index):
        return self.args[1:][index]


class Quantifier(Symbol):
    @property
    def var(self) -> Tuple[Symbol, ...]:
        v = self.args[0]
        return v if isinstance(v, tuple) else (v,)

    @property
    def body(self) -> Tuple[Symbol, ...]:
        return self.args[1]


class ForAll(Quantifier): ...


class Exists(Quantifier): ...


class Distinct(Condition): ...


class Function(Symbol):
    @property
    def name(self):
        return self.args[0]

    @property
    def operands(self):
        return self.args[1:]


class Row(Symbol):
    @property
    def columns(self):
        return self.args[1:]
    @property
    def rowid(self) -> Tuple[Any, ...]:
        if isinstance(self.args[0], tuple):
            return self.args[0]
        return (self.args[0],)
    
    def __iter__(self):
        return iter(self.columns)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return Row(*self.columns[index])
        return self.columns[index]  # return single Symbol

    def __add__(self, other):
        if not isinstance(other, Row):
            raise TypeError(f"Cannot add Row with {type(other)}")
        new_columns = self.columns + other.columns
        new_metadata = {**self.metadata, **other.metadata}
        rid = self.rowid + other.rowid
        return Row(rid, *new_columns, metadata=new_metadata)

    def __len__(self):
        return len(self.columns)


def _ensure_symbol(value: Any) -> Symbol:
    """Convert value to Symbol if needed."""
    if isinstance(value, Symbol):
        return value

    return Const(value, dtype=DataType.infer(value))


class Strftime(Function):
    """
    Represents the strftime function for formatting dates.
    """

    def _eval_concrete(self, *args):
        from datetime import datetime

        _, format_arg, date_arg = args

        for arg in self.parent.args:
            if isinstance(arg, Symbol) and arg is not self:
                arg.metadata["format"] = format_arg
        try:
            return date_arg.strftime(format_arg)
        except Exception as e:
            return None

    @property
    def operand(self):
        return self.args[1]

    @property
    def fmt(self):
        return self.args[2]


# class DateAdd(Function):
#     """
#     Represents the addition of a time interval to a date.
#     """

#     nargs = 2

#     @classmethod
#     def eval(cls, date, interval):
#         # Evaluation logic can be added here if needed
#         pass


# class DateSub(Function):
#     """
#     Represents the subtraction of a time interval from a date.
#     """

#     nargs = 2

#     @classmethod
#     def eval(cls, date, interval):
#         # Evaluation logic can be added here if needed
#         pass


# class Extract(Function):
#     """
#     Represents the extraction of a specific part from a date.
#     """

#     nargs = 2

#     @classmethod
#     def eval(cls, part, date):
#         # Evaluation logic can be added here if needed
#         pass


class SymbolicRegistry:
    """Default implementation of SymbolicEncoder."""

    VALID_CATEGORIES = {"BRANCH", "OPAQUE"}

    DEFAULT_BRANCH_EXPRESSIONS: Dict[str, Callable] = {
        "eq": lambda *args: args[0].eq(args[1]),
        "neq": lambda *args: args[0].ne(args[1]),
        "gt": lambda *args: args[0] > args[1],
        "lt": lambda *args: args[0] < args[1],
        "lte": lambda *args: args[0] >= args[1],
        "gte": lambda *args: args[0] <= args[1],
        "like": lambda *args: args[0].like(args[1]),
        "is_": lambda *args: args[0].is_(args[1]),
        "is_null": lambda *args: args[0].is_(None),
        "is_not_null": lambda *args: args[0].is_(None).not_(),
    }

    DEFAULT_OPAQUE_EXPRESSIONS: Dict[str, Callable] = {
        "and": lambda *args: args[0].and_(args[1]),
        "or": lambda *args: args[0].or_(args[1]),
        "add": lambda *args: args[0] + args[1],
        "sub": lambda *args: args[0] - args[1],
        "mul": lambda *args: args[0] * args[1],
        "div": lambda *args: args[0] // args[1],
        "floordiv": lambda *args: args[0] // args[1],
        "strftime": lambda *args: Strftime(
            "strftime", args[0], args[1], dtype=args[2] if len(args) > 2 else "STRING"
        ),
    }

    def __init__(
        self,
        branch_handlers: Optional[Dict[str, Callable]] = None,
        opaque_handlers: Optional[Dict[str, Callable]] = None,
    ):
        super().__init__()
        self.branch_handlers: Dict[str, Callable] = dict(
            self.DEFAULT_BRANCH_EXPRESSIONS
        )
        self.opaque_handlers: Dict[str, Callable] = dict(
            self.DEFAULT_OPAQUE_EXPRESSIONS
        )
        self.register_handlers(branch=branch_handlers, opaque=opaque_handlers)

    def register_handlers(
        self,
        branch: Optional[Dict[str, Callable]] = None,
        opaque: Optional[Dict[str, Callable]] = None,
    ):
        """Register or update handler mappings after initialization."""
        if branch:
            self.branch_handlers.update(branch)
        if opaque:
            self.opaque_handlers.update(opaque)

    def get_handlers(self, name):
        if name.lower() in self.branch_handlers:
            return self.branch_handlers[name.lower()]
        elif name.lower() in self.opaque_handlers:
            return self.opaque_handlers[name.lower()]
        else:
            raise KeyError(f"Unknown symbolic function: {name}")

    def has_handler(self, name: str) -> bool:
        return (
            name.lower() in self.branch_handlers or name.lower() in self.opaque_handlers
        )

    def is_branch(self, name: str) -> bool:
        return name.lower() in self.branch_handlers

    def is_opaque(self, name: str) -> bool:
        return name.lower() in self.opaque_handlers
