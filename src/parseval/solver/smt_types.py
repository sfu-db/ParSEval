"""Z3 type system for SQL: Option types, sort registry, type inference."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import z3
from sqlglot import exp
from sqlglot.expressions import DataType


def infer(value: Any) -> DataType:
    """Infer a SQL DataType from a Python value's runtime type."""
    if value is None:
        return DataType.build("NULL")
    if isinstance(value, bool):
        return DataType.build("BOOLEAN")
    if isinstance(value, int):
        return DataType.build("INT")
    if isinstance(value, float):
        return DataType.build("FLOAT")
    if isinstance(value, str):
        return DataType.build("TEXT", length=len(value))
    if isinstance(value, time):
        return DataType.build("TIME")
    if isinstance(value, datetime):
        return DataType.build("DATETIME")
    if isinstance(value, date):
        return DataType.build("DATE")
    return DataType.build("TEXT")


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


class LogicalTypeRegistry:
    """Global cache of Z3 sort/tag definitions for SQL logical types.

    Maps each SQL type name (INT, FLOAT, TEXT, etc.) to a Z3 constructor
    within a shared ``LogicalSQLType`` datatype, ensuring a single canonical
    representation per Z3 context.
    """

    _sort_cache: Dict[int, z3.DatatypeSortRef] = {}
    _tag_cache: Dict[int, Dict[str, z3.ExprRef]] = {}

    @classmethod
    def _ctx_key(cls, z3ctx: Optional[z3.Context]) -> int:
        return id(z3ctx) if z3ctx is not None else 0

    @classmethod
    def sort(cls, z3ctx: Optional[z3.Context] = None) -> z3.DatatypeSortRef:
        key = cls._ctx_key(z3ctx)
        if key not in cls._sort_cache:
            dtype = z3.Datatype("LogicalSQLType", ctx=z3ctx)
            for name in [
                "NULL",
                "INT",
                "FLOAT",
                "TEXT",
                "BOOLEAN",
                "DATE",
                "TIME",
                "DATETIME",
                "TIMESTAMP",
            ]:
                dtype.declare(name)
            cls._sort_cache[key] = dtype.create()
        return cls._sort_cache[key]

    @classmethod
    def tag(cls, name: str, z3ctx: Optional[z3.Context] = None) -> z3.ExprRef:
        key = cls._ctx_key(z3ctx)
        if key not in cls._tag_cache:
            sort = cls.sort(z3ctx)
            cls._tag_cache[key] = {
                sort.constructor(i).name(): getattr(sort, sort.constructor(i).name())
                for i in range(sort.num_constructors())
            }
        return cls._tag_cache[key][name]


@dataclass(frozen=True)
class SMTTypeInfo:
    """Metadata about a SQL type as seen by the Z3 SMT solver.

    Attributes:
        dtype: The original DataType.
        logical_name: Canonical type name in the LogicalTypeRegistry.
        family: Broad family string (int, real, text, bool, date, etc.).
        payload_sort: The Z3 sort for the value payload (inside the Option wrapper).
        logical_tag: Z3 constructor for this type's tag in the Option union.
    """

    dtype: DataType
    logical_name: str
    family: str
    payload_sort: z3.SortRef
    logical_tag: z3.ExprRef


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
        arg_policy: Argument handling policy ("fixed", "variadic", etc.).
        evaluator: Optional callable for concrete evaluation.
        matcher: Optional predicate to filter which expressions this model handles.
        null_propagation: How NULL inputs propagate ("any", "never", etc.).
    """

    name: str
    translator: Callable[
        ["SMTSolver", exp.Expression, List[Union["SMTValue", z3.BoolRef]]],
        Union["SMTValue", z3.BoolRef],
    ]
    return_type: Optional[Callable[[exp.Expression, Sequence[SMTTypeInfo]], DataType]] = None
    arg_policy: str = "fixed"
    evaluator: Optional[Callable[..., Any]] = None
    matcher: Optional[Callable[[exp.Expression], bool]] = None
    null_propagation: str = "any"

    def matches(self, expression: exp.Expression) -> bool:
        if self.matcher is None:
            return True
        return self.matcher(expression)


_SPECIAL_FUNCTION_MODELS: Dict[str, SpecialFunctionModel] = {}


def register_special_function(
    name: str,
    translator: Callable[
        ["SMTSolver", exp.Expression, List[Union[SMTValue, z3.BoolRef]]],
        Union[SMTValue, z3.BoolRef],
    ],
    return_type: Optional[Callable[[exp.Expression, Sequence[SMTTypeInfo]], DataType]] = None,
    arg_policy: str = "fixed",
    evaluator: Optional[Callable[..., Any]] = None,
    matcher: Optional[Callable[[exp.Expression], bool]] = None,
    null_propagation: str = "any",
) -> SpecialFunctionModel:
    """Register a custom SMT translation model for a SQL function.

    This is the plugin mechanism that allows extending the SMT solver's
    function support without modifying its core translation logic.

    Args:
        name: SQL function name (will be uppercased).
        translator: Callable that receives the solver, the SQL expression,
            and resolved Z3 argument values, and returns an SMTValue or BoolRef.
        return_type: Optional callable to infer the return DataType.
        arg_policy: Argument handling policy (default "fixed").
        evaluator: Optional callable for concrete evaluation.
        matcher: Optional predicate for custom expression filtering.
        null_propagation: How NULL inputs are handled.

    Returns:
        The created SpecialFunctionModel.
    """
    model = SpecialFunctionModel(
        name=name.upper(),
        translator=translator,
        return_type=return_type,
        arg_policy=arg_policy,
        evaluator=evaluator,
        matcher=matcher,
        null_propagation=null_propagation,
    )
    _SPECIAL_FUNCTION_MODELS[model.name] = model
    return model


def _is_temporal_string(value: str) -> bool:
    """Check if a string value looks like a temporal (date/time/datetime) representation."""
    return any(ch in value for ch in ("-", ":", "T", " "))


def _infer_temporal_dtype(value: str) -> DataType:
    """Infer the most likely temporal DataType from a string value."""
    if _parse_datetime(value) is not None and ("T" in value or " " in value):
        return DataType.build("DATETIME")
    if _parse_date(value) is not None and "-" in value and ":" not in value:
        return DataType.build("DATE")
    if _parse_time(value) is not None and ":" in value and "-" not in value:
        return DataType.build("TIME")
    return DataType.build("TEXT")


def _parse_date(value: Any) -> Optional[date]:
    """Parse a value into a ``date``, or None if unparseable."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            if "T" in value or " " in value:
                return datetime.fromisoformat(value.replace(" ", "T")).date()
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _parse_time(value: Any) -> Optional[time]:
    """Parse a value into a ``time``, or None if unparseable."""
    if isinstance(value, datetime):
        return value.time().replace(microsecond=0)
    if isinstance(value, time):
        return value.replace(microsecond=0)
    if isinstance(value, str):
        try:
            if "T" in value or " " in value:
                return datetime.fromisoformat(value.replace(" ", "T")).time().replace(
                    microsecond=0
                )
            return time.fromisoformat(value[:8])
        except ValueError:
            return None
    return None


def _parse_datetime(value: Any) -> Optional[datetime]:
    """Parse a value into a ``datetime``, or None if unparseable."""
    if isinstance(value, datetime):
        return value.replace(microsecond=0)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        for candidate in (value.replace(" ", "T"), value):
            try:
                return datetime.fromisoformat(candidate).replace(microsecond=0)
            except ValueError:
                continue
    return None


def _date_to_epoch_day(value: Any) -> int:
    """Convert a date value to days since the Unix epoch (1970-01-01)."""
    parsed = _parse_date(value)
    if parsed is not None:
        return (parsed - date(1970, 1, 1)).days
    return int(value)


def _time_to_seconds(value: Any) -> int:
    """Convert a time value to seconds since midnight."""
    parsed = _parse_time(value)
    if parsed is not None:
        return parsed.hour * 3600 + parsed.minute * 60 + parsed.second
    return int(value)


def _datetime_to_epoch_second(value: Any) -> int:
    """Convert a datetime value to seconds since the Unix epoch."""
    parsed = _parse_datetime(value)
    if parsed is not None:
        return int(parsed.timestamp())
    return int(value)


def _from_epoch_day(days: int) -> date:
    """Convert days since Unix epoch back to a ``date``."""
    return date(1970, 1, 1) + timedelta(days=days)


def _from_seconds(seconds: int) -> time:
    """Convert seconds since midnight back to a ``time``."""
    seconds = max(0, seconds) % 86400
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return time(hours, minutes, secs)


def _from_epoch_second(value: int) -> datetime:
    """Convert Unix epoch seconds back to a timezone-naive ``datetime``."""
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(tzinfo=None)


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
        logical_tag=LogicalTypeRegistry.tag(logical_name, z3ctx),
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
        return z3.IntVal(_date_to_epoch_day(value), ctx=z3ctx)
    if typeinfo.family == "time":
        return z3.IntVal(_time_to_seconds(value), ctx=z3ctx)
    if typeinfo.family in {"datetime", "timestamp"}:
        return z3.IntVal(_datetime_to_epoch_second(value), ctx=z3ctx)
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
