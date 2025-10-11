from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple, Iterable, List, Callable
from dataclasses import dataclass
from enum import Enum, auto
from .dtype import DataType

if TYPE_CHECKING:
    from .dtype import DATATYPE

_SQL_OP_MAP = {
    "ADD": "+",
    "SUB": "-",
    "MUL": "*",
    "DIV": "/",
    "FLOORDIV": "//",
    "MOD": "%",
    "POW": "^",
    "EQ": "=",
    "NE": "!=",
    "LT": "<",
    "LE": "<=",
    "GT": ">",
    "GE": ">=",
    "AND": "AND",
    "OR": "OR",
    "NOT": "NOT",
}


class Symbol(ABC):
    __slots__ = ("dtype", "concrete", "meta", "_hash")

    def __init__(
        self,
        dtype: DATATYPE,
        concrete: Any = None,
        meta: Optional[Dict[str, Any]] = None,
    ):

        self.dtype = DataType.build(dtype)
        self.concrete = concrete
        self.meta = meta or {}
        self._hash = None

    @abstractmethod
    def children(self) -> Tuple[Symbol, ...]:
        """Return child symbols."""
        pass

    def canonical_key(self) -> Tuple:
        return (self.__class__, self.dtype, self.children())

    def __hash__(self):
        if self._hash is None:
            self._hash = hash(self.canonical_key())
        return self._hash

    def iter(self) -> Iterable[Symbol]:
        """Depth-first traversal of the symbol tree."""
        yield self
        for child in self.children():
            yield from child.iter()

    def predicates(self, include_nested: bool = False) -> List[Symbol]:
        """Collect all boolean predicate symbols in the expression tree.

        Args:
            include_nested (bool): If True, include nested predicates within other predicates.
                                   If False, only include top-level predicates.

        Returns:
            List[Symbol]: List of predicate symbols.
        """
        results: List[Symbol] = []
        self._collect_predicates(results, include_nested, is_root=True)
        return results

    def _collect_predicates(
        self, results: List[Symbol], include_nested: bool, is_root: bool = False
    ):
        if self.dtype.is_boolean() and isinstance(self, Compare):
            results.append(self)
            if not include_nested and not is_root:
                return
        for child in self.children():
            child._collect_predicates(results, include_nested, is_root=False)

    def __eq__(self, other) -> bool:
        if not isinstance(other, Symbol):
            return False
        return self.canonical_key() == other.canonical_key()

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

    # def __mod__(self, other):
    #     return Mod(self, _ensure_symbol(other))

    # def __rmod__(self, other):
    #     return Mod(_ensure_symbol(other), self)

    # def __pow__(self, other):
    #     return Pow(self, _ensure_symbol(other))

    # def __rpow__(self, other):
    #     return Pow(_ensure_symbol(other), self)

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
        return And(self, _ensure_symbol(other))

    def or_(self, other):
        return Or(self, _ensure_symbol(other))

    # Use .eq() method instead
    def eq(self, other):
        return EQ(self, _ensure_symbol(other))

    def ne(self, other):
        return NE(self, _ensure_symbol(other))


# ======================================================================================================
# Leaf Nodes
# =====================================================================================================


class Var(Symbol):
    __slots__ = ("name",)

    def __init__(self, name, dtype, concrete=None, meta=None):
        super().__init__(dtype, concrete, meta)
        self.name = name

    def canonical_key(self) -> Tuple:
        return (self.__class__, self.name, self.dtype)

    def children(self) -> Tuple[Symbol, ...]:
        return ()

    def __str__(self):
        return str(self.concrete)

    def __repr__(self) -> str:
        return f"Var({self.name})"


class Const(Symbol):

    def __init__(
        self, value: Any, dtype: DATATYPE, meta: Optional[Dict[str, Any]] = None
    ):
        super().__init__(dtype, concrete=value, meta=meta)

    def canonical_key(self) -> Tuple:
        return (self.__class__, self.concrete, self.dtype)

    def children(self) -> Tuple[Symbol, ...]:
        return ()

    def __repr__(self) -> str:
        return f"Const({self.concrete})"


# ============================================================================
# Binary Operations Base
# ============================================================================


class BinaryOp(Symbol):
    __slots__ = ("left", "right", "op")
    OP_SYMBOL: str = ""

    def __init__(self, left: Symbol, right: Symbol, dtype, concrete=None, meta=None):
        self.left = left
        self.right = right
        concrete = concrete or self._eval()
        super().__init__(dtype, concrete, meta)

    def canonical_key(self) -> Tuple:
        return (self.__class__.__name__, self.left, self.right, self.dtype)

    def children(self) -> Tuple[Symbol, ...]:
        return (self.left, self.right)

    def __repr__(self) -> str:
        return f"{self.OP_SYMBOL}({repr(self.left)}, {repr(self.right)})"

    def _eval(self):
        if self.OP_SYMBOL == "<":
            return self.left.concrete < self.right.concrete
        elif self.OP_SYMBOL == "<=":
            return self.left.concrete <= self.right.concrete
        elif self.OP_SYMBOL == ">":
            return self.left.concrete > self.right.concrete
        elif self.OP_SYMBOL == ">=":
            return self.left.concrete >= self.right.concrete
        elif self.OP_SYMBOL == "=":
            return self.left.concrete == self.right.concrete
        elif self.OP_SYMBOL == "!=":
            return self.left.concrete != self.right.concrete
        else:
            raise NotImplementedError(f"Unknown comparison operator: {self.OP_SYMBOL}")


class Add(BinaryOp):
    OP_SYMBOL = _SQL_OP_MAP["ADD"]


class Sub(BinaryOp):
    OP_SYMBOL = _SQL_OP_MAP["SUB"]


class Mul(BinaryOp):
    OP_SYMBOL = _SQL_OP_MAP["MUL"]


class Div(BinaryOp):
    OP_SYMBOL = _SQL_OP_MAP["DIV"]


class FloorDiv(BinaryOp):
    OP_SYMBOL = _SQL_OP_MAP["FLOORDIV"]


class UnaryOp(Symbol):
    __slots__ = "operand"
    OP_SYMBOL: str = ""

    def __init__(self, operand: Symbol, dtype, concrete=None, meta=None):
        super().__init__(dtype, concrete, meta)
        self.operand = operand

    def canonical_key(self) -> Tuple:
        return (self.__class__, self.operand, self.dtype)

    def children(self) -> Tuple[Symbol, ...]:
        return (self.operand,)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.operand})"


class Neg(UnaryOp):
    OP_SYMBOL = _SQL_OP_MAP["SUB"]


class Not(UnaryOp):
    OP_SYMBOL = _SQL_OP_MAP["NOT"]


class Cast(UnaryOp):
    OP_SYMBOL = "CAST"


# ============================================================================
# Comparison Operations
# ============================================================================
class Compare(BinaryOp):
    def __init__(self, left, right, dtype="bool", concrete=None, meta=None):
        super().__init__(left, right, dtype, concrete, meta)

    def __bool__(self):
        if self.concrete is None:
            self.concrete = self._eval()
        return self.concrete


class LT(Compare):
    OP_SYMBOL = _SQL_OP_MAP["LT"]


class LE(Compare):
    OP_SYMBOL = _SQL_OP_MAP["LE"]


class GT(Compare):
    OP_SYMBOL = _SQL_OP_MAP["GT"]


class GE(Compare):
    OP_SYMBOL = _SQL_OP_MAP["GE"]


class EQ(Compare):
    OP_SYMBOL = _SQL_OP_MAP["EQ"]


class NE(Compare):
    OP_SYMBOL = _SQL_OP_MAP["NE"]


class And(Symbol):
    __slots__ = ("operands",)

    def __init__(self, *operands: Symbol, concrete: Optional[bool] = None, meta=None):
        concrete = all(op.concrete for op in operands)
        super().__init__("bool", concrete=concrete, meta=meta)
        self.operands = operands

    def canonical_key(self) -> Tuple:
        return (self.__class__, self.operands, self.dtype)

    def children(self) -> Tuple[Symbol, ...]:
        return self.operands

    def __repr__(self) -> str:
        return f"And({', '.join(repr(op) for op in self.operands)})"

    def __bool__(self):
        if self.concrete is None:
            self.concrete = all(op.concrete for op in self.operands)
        return self.concrete


class Or(Symbol):
    __slots__ = ("operands",)

    def __init__(self, *operands: Symbol, concrete: Optional[bool] = None, meta=None):
        concrete = any(op.concrete for op in operands)
        super().__init__("bool", concrete=concrete, meta=meta)
        self.operands = operands

    def canonical_key(self) -> Tuple:
        return (self.__class__, self.operands, self.dtype)

    def children(self) -> Tuple[Symbol, ...]:
        return self.operands

    def __repr__(self) -> str:
        return f"Or({', '.join(repr(op) for op in self.operands)})"

    def __bool__(self):
        if self.concrete is None:
            self.concrete = all(op.concrete for op in self.operands)
        return self.concrete


class FunctionDef(Symbol):
    __slots__ = ("name", "args")
    OP_SYMBOL = "FUNC"

    def __init__(self, name, args, return_type, concrete=None, meta=None):
        super().__init__(return_type, concrete, meta)
        self.name = name
        self.args = args


class Row(Symbol):
    __slots__ = ("columns",)

    def __init__(self, columns: List[Symbol]):
        super().__init__("row", None, None)
        self.columns = columns

    def children(self) -> Tuple[Symbol, ...]:
        return tuple(self.columns)

    def __iter__(self):
        return iter(self.columns)

    def __getitem__(self, idx):
        return self.columns[idx]

    def __repr__(self) -> str:
        return f"Row({', '.join(repr(op) for op in self.columns)})"


class FunctionRegistry:
    _registry: Dict[str, FunctionDef] = {}

    @classmethod
    def register(cls, f: FunctionDef):
        cls._registry[f.name] = f

    @classmethod
    def get(cls, name: str) -> Optional[FunctionDef]:
        return cls._registry.get(name)


def _ensure_symbol(value: Any) -> Symbol:
    """Convert value to Symbol if needed."""
    if isinstance(value, Symbol):
        return value

    return Const(value, dtype=DataType.infer(value))
