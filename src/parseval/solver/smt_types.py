"""Z3 type system for SQL: Option types, sort registry, type inference."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import z3
from sqlglot import exp

from parseval.dtype import (
    DataType,
    is_enum_type,
    parse_date,
    parse_time,
    parse_datetime,
    date_to_epoch_day,
    time_to_seconds,
    datetime_to_epoch_second,
    epoch_day_to_date,
    seconds_to_time,
    epoch_second_to_datetime,
    infer_type_from_value,
)


def infer(value: Any) -> DataType:
    """Infer a SQL DataType from a Python value's runtime type."""
    return infer_type_from_value(value)


def make_option_type(
    name: str, inner_sort: z3.SortRef, z3ctx: Optional[z3.Context] = None
) -> z3.DatatypeSortRef:
    """Build a Z3 Option datatype with NULL and Some(value) constructors.

    This wraps an inner sort in a tagged union so SQL NULL semantics
    (three-valued logic) can be represented in Z3.
    """
    dtype = z3.Datatype(name, ctx=z3ctx)
    dtype.declare("NULL")
    dtype.declare("Some", ("value", inner_sort))
    return dtype.create()


@dataclass(frozen=True)
class SMTTypeInfo:
    """Metadata about a SQL type as seen by the Z3 SMT solver.

    Attributes:
        dtype: The original DataType.
        logical_name: Canonical type name (INT, FLOAT, TEXT, etc.).
        family: Broad family string (int, real, text, bool, date, etc.).
        payload_sort: The Z3 sort for the value payload (inside the Option wrapper).
    """

    dtype: DataType
    logical_name: str
    family: str
    payload_sort: z3.SortRef


@dataclass(frozen=True)
class SMTValue:
    """A value expression in the Z3 SMT solver, wrapped in an Option type.

    Attributes:
        expr: The Z3 expression (or None).
        typeinfo: Type metadata from SMTTypeInfo.
        is_null_literal: True if this represents an explicit SQL NULL.
    """

    expr: Optional[z3.ExprRef]
    typeinfo: SMTTypeInfo
    is_null_literal: bool = False

    @property
    def is_value(self) -> bool:
        return self.expr is not None and not self.is_null_literal


@dataclass
class _VarRef:
    """Lightweight stand-in for a sqlglot Column in z3_to_variable context.

    _z3_to_python expects context["z3_to_variable"][name] to have a .type
    attribute for temporal decoding. This wraps a DataType so declare_variable
    entries satisfy that contract without importing sqlglot Column.
    """

    type: DataType


class UnsupportedSMTError(NotImplementedError):
    """Raised when an expression or operation is not supported by the SMT solver."""


@dataclass(frozen=True)
class SpecialFunctionModel:
    """Describes how a SQL function should be translated into Z3 constraints.

    Attributes:
        name: Canonical function name (e.g. "ABS").
        translator: Callable that translates the function into Z3 expressions.
        return_type: Optional callable that infers the return DataType.
    """

    name: str
    translator: Callable[
        ["Z3SmtSession", exp.Expression, List[Union["SMTValue", z3.BoolRef]]],
        Union["SMTValue", z3.BoolRef],
    ]
    return_type: Optional[Callable[[exp.Expression, Sequence[SMTTypeInfo]], DataType]] = None


_SPECIAL_FUNCTION_MODELS: Dict[str, SpecialFunctionModel] = {}


def register_special_function(
    name: str,
    translator: Callable[
        ["Z3SmtSession", exp.Expression, List[Union[SMTValue, z3.BoolRef]]],
        Union[SMTValue, z3.BoolRef],
    ],
    return_type: Optional[Callable[[exp.Expression, Sequence[SMTTypeInfo]], DataType]] = None,
) -> SpecialFunctionModel:
    """Register a custom SMT translation model for a SQL function.

    This is the plugin mechanism that allows extending the SMT solver's
    function support without modifying its core translation logic.

    Args:
        name: SQL function name (will be uppercased).
        translator: Callable that receives the solver, the SQL expression,
            and resolved Z3 argument values, and returns an SMTValue or BoolRef.
        return_type: Optional callable to infer the return DataType.

    Returns:
        The created SpecialFunctionModel.
    """
    model = SpecialFunctionModel(
        name=name.upper(),
        translator=translator,
        return_type=return_type,
    )
    _SPECIAL_FUNCTION_MODELS[model.name] = model
    return model


def _is_temporal_string(value: str) -> bool:
    """Check if a string value looks like a temporal (date/time/datetime) representation."""
    return any(ch in value for ch in ("-", ":", "T", " "))


def _infer_temporal_dtype(value: str) -> DataType:
    """Infer the most likely temporal DataType from a string value."""
    if parse_datetime(value) is not None and ("T" in value or " " in value):
        return DataType.build("DATETIME")
    if parse_date(value) is not None and "-" in value and ":" not in value:
        return DataType.build("DATE")
    if parse_time(value) is not None and ":" in value and "-" not in value:
        return DataType.build("TIME")
    return DataType.build("TEXT")




def normalize_dtype(
    dtype: DataType, z3ctx: Optional[z3.Context] = None, value: Any = None
) -> SMTTypeInfo:
    """Map a SQL DataType to its Z3 sort representation and logical tag.

    Dispatches to the appropriate Z3 sort (IntSort, RealSort, StringSort,
    or BoolSort) and caches the result by context. Also returns the
    corresponding ``SMTTypeInfo`` metadata record.

    Args:
        dtype: The SQL DataType to normalize.
        z3ctx: Optional Z3 context.
        value: Optional sample value for type inference when dtype is UNKNOWN.

    Returns:
        An SMTTypeInfo with payload sort, logical name, and family.

    Raises:
        RuntimeError: If the data type is unsupported.
    """
    dtype = DataType.build(dtype)
    if str(dtype) == "UNKNOWN":
        dtype = infer(value)

    if dtype.is_type(DataType.Type.NULL):
        logical_name, family = "NULL", "null"
        payload_sort = z3.IntSort(z3ctx)
    elif dtype.is_type(*DataType.INTEGER_TYPES):
        logical_name, family = "INT", "int"
        payload_sort = z3.IntSort(z3ctx)
    elif dtype.is_type(*DataType.REAL_TYPES):
        logical_name, family = "FLOAT", "real"
        payload_sort = z3.RealSort(z3ctx)
    elif dtype.is_type(DataType.Type.BOOLEAN):
        logical_name, family = "BOOLEAN", "bool"
        payload_sort = z3.BoolSort(z3ctx)
    elif is_enum_type(dtype):
        logical_name, family = "TEXT", "text"
        payload_sort = z3.StringSort(z3ctx)
    elif dtype.is_type(*DataType.TEXT_TYPES):
        logical_name, family = "TEXT", "text"
        payload_sort = z3.StringSort(z3ctx)
    elif dtype.is_type(DataType.Type.DATE) or dtype.is_type(DataType.Type.DATE32):
        logical_name, family = "DATE", "date"
        payload_sort = z3.IntSort(z3ctx)
    elif dtype.is_type(DataType.Type.TIME) or dtype.is_type(DataType.Type.TIMETZ):
        logical_name, family = "TIME", "time"
        payload_sort = z3.IntSort(z3ctx)
    elif dtype.is_type(DataType.Type.TIMESTAMP) or dtype.is_type(
        DataType.Type.TIMESTAMP_S
    ) or dtype.is_type(DataType.Type.TIMESTAMP_MS) or dtype.is_type(
        DataType.Type.TIMESTAMP_NS
    ) or dtype.is_type(DataType.Type.TIMESTAMPTZ) or dtype.is_type(
        DataType.Type.TIMESTAMPLTZ
    ):
        logical_name, family = "TIMESTAMP", "timestamp"
        payload_sort = z3.IntSort(z3ctx)
    elif dtype.is_type(DataType.Type.DATETIME) or dtype.is_type(
        DataType.Type.DATETIME64
    ):
        logical_name, family = "DATETIME", "datetime"
        payload_sort = z3.IntSort(z3ctx)
    else:
        raise RuntimeError(f"Unsupported data type: {repr(dtype)}")

    return SMTTypeInfo(
        dtype=dtype,
        logical_name=logical_name,
        family=family,
        payload_sort=payload_sort,
    )


class OptionTypeRegistry:
    """Global cache that maps base Z3 sorts to their ``Option(NULL | Some)`` wrapper types.

    This avoids recreating the same Option datatype for the same inner
    sort across multiple SMT solver instances.
    """

    _base_to_option: Dict[Tuple[int, str], z3.DatatypeSortRef] = {}
    _sort_to_option: Dict[Tuple[int, str], z3.DatatypeSortRef] = {}

    @classmethod
    def _ctx_key(cls, sort: z3.SortRef) -> Tuple[int, str]:
        return id(sort.ctx), sort.sexpr()

    @classmethod
    def get(
        cls, base_sort: z3.SortRef, z3ctx: Optional[z3.Context] = None
    ) -> z3.DatatypeSortRef:
        key = cls._ctx_key(base_sort)
        if key not in cls._base_to_option:
            suffix = f"{abs(hash(key[1]))}"
            name = f"Option_{base_sort.name()}_{suffix}"
            opt = make_option_type(name, base_sort, z3ctx=z3ctx or base_sort.ctx)
            cls._base_to_option[key] = opt
            cls._sort_to_option[(id(opt.ctx), opt.sexpr())] = opt
        return cls._base_to_option[key]

    @classmethod
    def from_sort(cls, option_sort: z3.SortRef) -> z3.DatatypeSortRef:
        return cls._sort_to_option[(id(option_sort.ctx), option_sort.sexpr())]

    @classmethod
    def is_option_sort(cls, sort: z3.SortRef) -> bool:
        return (id(sort.ctx), sort.sexpr()) in cls._sort_to_option


def is_option_expr(expr: z3.ExprRef) -> bool:
    return OptionTypeRegistry.is_option_sort(expr.sort())


def option_of(expr: z3.ExprRef) -> z3.DatatypeSortRef:
    return OptionTypeRegistry.from_sort(expr.sort())


def unwrap_option(expr: z3.ExprRef) -> z3.ExprRef:
    return option_of(expr).value(expr)


def _python_to_payload(typeinfo: SMTTypeInfo, value: Any, z3ctx: Optional[z3.Context]):
    """Convert a Python value to a Z3 constant of the appropriate sort."""
    if typeinfo.family == "int":
        return z3.IntVal(int(value), ctx=z3ctx)
    if typeinfo.family == "real":
        return z3.RealVal(value, ctx=z3ctx)
    if typeinfo.family == "bool":
        return z3.BoolVal(bool(value), ctx=z3ctx)
    if typeinfo.family == "text":
        return z3.StringVal(str(value), ctx=z3ctx)
    if typeinfo.family == "date":
        return z3.IntVal(date_to_epoch_day(value), ctx=z3ctx)
    if typeinfo.family == "time":
        return z3.IntVal(time_to_seconds(value), ctx=z3ctx)
    if typeinfo.family in {"datetime", "timestamp"}:
        return z3.IntVal(datetime_to_epoch_second(value), ctx=z3ctx)
    raise RuntimeError(f"Unsupported value family: {typeinfo.family}")


def encode_literal(
    dtype: DataType, value: Any, z3ctx: Optional[z3.Context] = None
) -> SMTValue:
    typeinfo = normalize_dtype(dtype, z3ctx, value=value)
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
    if value is None:
        return SMTValue(option_sort.NULL, typeinfo, is_null_literal=True)
    payload = _python_to_payload(typeinfo, value, z3ctx)
    return SMTValue(option_sort.Some(payload), typeinfo)
