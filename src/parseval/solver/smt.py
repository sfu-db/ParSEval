from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import z3
from sqlglot import exp
from sqlglot.expressions import DataType

from parseval.plan.rex import Const

logger = logging.getLogger("parseval.smt")

try:
    from z3.z3util import get_vars
except Exception:
    get_vars = None


@contextmanager
def checkpoint(z3solver):
    z3solver.push()
    try:
        yield z3solver
    finally:
        z3solver.pop()


def infer(value: Any) -> DataType:
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
    dtype = z3.Datatype(name, ctx=z3ctx)
    dtype.declare("NULL")
    dtype.declare("Some", ("value", inner_sort))
    return dtype.create()


class LogicalTypeRegistry:
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
    dtype: DataType
    logical_name: str
    family: str
    payload_sort: z3.SortRef
    logical_tag: z3.ExprRef


@dataclass(frozen=True)
class SMTValue:
    expr: Optional[z3.ExprRef]
    typeinfo: SMTTypeInfo
    is_null_literal: bool = False

    @property
    def is_value(self) -> bool:
        return self.expr is not None and not self.is_null_literal


class UnsupportedSMTError(NotImplementedError):
    pass


@dataclass(frozen=True)
class SpecialFunctionModel:
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
    return any(ch in value for ch in ("-", ":", "T", " "))


def _infer_temporal_dtype(value: str) -> DataType:
    if _parse_datetime(value) is not None and ("T" in value or " " in value):
        return DataType.build("DATETIME")
    if _parse_date(value) is not None and "-" in value and ":" not in value:
        return DataType.build("DATE")
    if _parse_time(value) is not None and ":" in value and "-" not in value:
        return DataType.build("TIME")
    return DataType.build("TEXT")


def _parse_date(value: Any) -> Optional[date]:
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
    parsed = _parse_date(value)
    if parsed is not None:
        return (parsed - date(1970, 1, 1)).days
    return int(value)


def _time_to_seconds(value: Any) -> int:
    parsed = _parse_time(value)
    if parsed is not None:
        return parsed.hour * 3600 + parsed.minute * 60 + parsed.second
    return int(value)


def _datetime_to_epoch_second(value: Any) -> int:
    parsed = _parse_datetime(value)
    if parsed is not None:
        return int(parsed.timestamp())
    return int(value)


def _from_epoch_day(days: int) -> date:
    return date(1970, 1, 1) + timedelta(days=days)


def _from_seconds(seconds: int) -> time:
    seconds = max(0, seconds) % 86400
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return time(hours, minutes, secs)


def _from_epoch_second(value: int) -> datetime:
    return datetime.utcfromtimestamp(value)


def normalize_dtype(
    dtype: DataType, z3ctx: Optional[z3.Context] = None, value: Any = None
) -> SMTTypeInfo:
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


def _coerce_numeric_sort(expr: z3.ExprRef, target_sort: z3.SortRef) -> z3.ExprRef:
    if expr.sort() == target_sort:
        return expr
    if (
        target_sort.kind() == z3.Z3_REAL_SORT
        and expr.sort().kind() == z3.Z3_INT_SORT
    ):
        return z3.ToReal(expr)
    return expr


def _to_z3_sort(dtype: DataType, z3ctx: Optional[z3.Context] = None) -> z3.SortRef:
    return normalize_dtype(dtype, z3ctx).payload_sort


def _python_to_payload(typeinfo: SMTTypeInfo, value: Any, z3ctx: Optional[z3.Context]):
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


def _to_z3val(dtype: DataType, value, z3ctx: Optional[z3.Context] = None) -> z3.ExprRef:
    return encode_literal(dtype, value, z3ctx).expr


def encode_literal(
    dtype: DataType, value: Any, z3ctx: Optional[z3.Context] = None
) -> SMTValue:
    typeinfo = normalize_dtype(dtype, z3ctx, value=value)
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
    if value is None:
        return SMTValue(option_sort.NULL, typeinfo, is_null_literal=True)
    payload = _python_to_payload(typeinfo, value, z3ctx)
    return SMTValue(option_sort.Some(payload), typeinfo)


def declare_column(variable: exp.Column, z3ctx: Optional[z3.Context] = None) -> SMTValue:
    dtype = getattr(variable, "type", None) or DataType.build("UNKNOWN")
    typeinfo = normalize_dtype(dtype, z3ctx)
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
    var_name = f"{variable.table}.{variable.name}"
    return SMTValue(z3.Const(var_name, option_sort), typeinfo)


def _value_some(value: SMTValue) -> z3.BoolRef:
    if value.expr is None:
        return z3.BoolVal(False)
    return option_of(value.expr).is_Some(value.expr)


def _value_null(value: SMTValue) -> z3.BoolRef:
    if value.expr is None:
        return z3.BoolVal(True)
    return option_of(value.expr).is_NULL(value.expr)


def _value_payload(value: SMTValue) -> z3.ExprRef:
    if value.expr is None:
        raise RuntimeError("NULL literal does not have a payload")
    return unwrap_option(value.expr)


def _coerce_pair(left: SMTValue, right: SMTValue) -> Tuple[z3.ExprRef, z3.ExprRef, str]:
    if left.typeinfo.family == "real" or right.typeinfo.family == "real":
        target_sort = z3.RealSort()
        return (
            _coerce_numeric_sort(_value_payload(left), target_sort),
            _coerce_numeric_sort(_value_payload(right), target_sort),
            "real",
        )
    return _value_payload(left), _value_payload(right), left.typeinfo.family


def _bool_value(expr: z3.BoolRef, z3ctx: Optional[z3.Context] = None) -> SMTValue:
    typeinfo = normalize_dtype(DataType.build("BOOLEAN"), z3ctx)
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
    return SMTValue(option_sort.Some(expr), typeinfo)


def _null_value(typeinfo: SMTTypeInfo, z3ctx: Optional[z3.Context] = None) -> SMTValue:
    option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, z3ctx)
    return SMTValue(option_sort.NULL, typeinfo, is_null_literal=True)


def _zfill2(expr: z3.ExprRef, z3ctx: Optional[z3.Context] = None) -> z3.ExprRef:
    return z3.If(expr < 10, z3.Concat(z3.StringVal("0", ctx=z3ctx), z3.IntToStr(expr)), z3.IntToStr(expr))


def like_to_z3(var: SMTValue, pattern: Union[SMTValue, str]) -> z3.BoolRef:
    some_checks = [_value_some(var)]
    raw = _value_payload(var)
    parts: List[z3.ExprRef] = []
    constraints: List[z3.BoolRef] = []

    if isinstance(pattern, SMTValue):
        if pattern.is_null_literal:
            return z3.BoolVal(False)
        some_checks.append(_value_some(pattern))
        pattern_expr = z3.simplify(_value_payload(pattern))
        if z3.is_string_value(pattern_expr):
            pattern = pattern_expr.as_string()
        else:
            raise UnsupportedSMTError("LIKE currently requires a concrete string pattern")
    elif not isinstance(pattern, str):
        raise UnsupportedSMTError("LIKE currently requires a concrete string pattern")

    for i, ch in enumerate(pattern):
        if ch == "_":
            char_expr = z3.String(f"like_char_{i}")
            constraints.append(z3.Length(char_expr) == 1)
            parts.append(char_expr)
        elif ch == "%":
            tail = z3.String(f"like_tail_{i}")
            constraints.append(z3.Length(tail) >= 0)
            parts.append(tail)
        else:
            parts.append(z3.StringVal(ch))
    expr = parts[0] if parts else z3.StringVal("")
    for part in parts[1:]:
        expr = z3.Concat(expr, part)
    constraints.append(raw == expr)
    return z3.And(*some_checks, *constraints)


class SMTSolver:
    def __init__(
        self,
        variables,
        z3ctx: Optional[z3.Context] = None,
        verbose: bool = False,
        function_models: Optional[
            Union[Sequence[SpecialFunctionModel], Dict[str, SpecialFunctionModel]]
        ] = None,
    ):
        self.variables = variables
        self.verbose = verbose
        self.z3ctx = z3ctx
        self.solver = z3.Solver(ctx=self.z3ctx)
        self.model = None
        self.context: Dict[str, Dict[str, Any]] = {}
        self._domain_constraints_applied = False
        self.constrained_var_names = set()
        self.function_models = self._build_function_models(function_models)
        self.core_registry = self._build_core_registry()

        z3.set_option(html_mode=False)
        z3.set_option(rational_to_decimal=True)
        z3.set_option(precision=32)
        z3.set_option(max_width=21049)
        z3.set_option(max_args=100)

    def _build_function_models(
        self,
        function_models: Optional[
            Union[Sequence[SpecialFunctionModel], Dict[str, SpecialFunctionModel]]
        ],
    ) -> Dict[str, SpecialFunctionModel]:
        models = dict(_SPECIAL_FUNCTION_MODELS)
        if function_models is None:
            return models
        if isinstance(function_models, dict):
            for key, model in function_models.items():
                models[key.upper()] = model
            return models
        for model in function_models:
            models[model.name.upper()] = model
        return models

    def _build_core_registry(self) -> Dict[str, Callable[[exp.Expression], Union[SMTValue, z3.BoolRef]]]:
        return {
            "ADD": self._translate_add,
            "SUB": self._translate_sub,
            "MUL": self._translate_mul,
            "DIV": self._translate_div,
            "MOD": self._translate_mod,
            "GT": self._translate_gt,
            "LT": self._translate_lt,
            "GTE": self._translate_gte,
            "LTE": self._translate_lte,
            "EQ": self._translate_eq,
            "NEQ": self._translate_neq,
            "LIKE": self._translate_like,
            "AND": self._translate_and,
            "OR": self._translate_or,
            "NOT": self._translate_not,
            "DISTINCT": self._translate_distinct,
            "IS": self._translate_is,
            "CAST": self._translate_cast,
            "NULLIF": self._translate_nullif,
            "BETWEEN": self._translate_between,
            "IN": self._translate_in,
        }

    def add(self, constraint, track_vars: bool = True):
        if isinstance(constraint, SMTValue):
            constraint = self._as_predicate(constraint)
        if z3.is_bool(constraint):
            if self.verbose:
                logger.info(constraint)
            if track_vars and get_vars is not None:
                for var in get_vars(constraint):
                    self.constrained_var_names.add(str(var))
            self.solver.add(constraint)

    def solve(self):
        if not self._domain_constraints_applied:
            for var_name, z3var in self.context.get("variable_to_z3", {}).items():
                column = self.context["z3_to_variable"][str(z3var)]
                typeinfo = normalize_dtype(column.type, self.z3ctx)
                if typeinfo.family in {"date", "time", "datetime", "timestamp"}:
                    self._ensure_temporal_bounds(z3var, typeinfo)
                if typeinfo.family == "text":
                    self._ensure_str_printable(z3var)
                    self._ensure_str_length(z3var, 0)
            self._domain_constraints_applied = True

        status = self.solver.check()
        if status != z3.sat:
            return "unsat", {}
        self.model = self.solver.model()
        solutions = self.z3_to_python(self.model) or {}
        logger.info(f"SMT solver found solution: {solutions}")
        return "sat", solutions

    def _declare_or_get_column(self, condition: exp.Column) -> SMTValue:
        col_key = f"{condition.table}.{condition.name}"
        if col_key not in self.context.get("variable_to_z3", {}):
            value = declare_column(condition, z3ctx=self.z3ctx)
            self.context.setdefault("variable_to_z3", {})[col_key] = value.expr
            self.context.setdefault("z3_to_variable", {})[str(value.expr)] = condition
        expr = self.context["variable_to_z3"][col_key]
        return SMTValue(expr, normalize_dtype(condition.type, self.z3ctx))

    def _as_value(self, item) -> SMTValue:
        if isinstance(item, SMTValue):
            return item
        raise UnsupportedSMTError(f"Expected a value expression, got {item!r}")

    def _as_predicate(self, item) -> z3.BoolRef:
        if z3.is_bool(item):
            return item
        value = self._as_value(item)
        if value.typeinfo.family != "bool":
            raise UnsupportedSMTError(
                f"Cannot use non-boolean value as predicate: {value.typeinfo.logical_name}"
            )
        if value.is_null_literal:
            return z3.BoolVal(False, ctx=self.z3ctx)
        return z3.And(_value_some(value), _value_payload(value))

    def _result_family_type(self, family: str, left: SMTTypeInfo, right: Optional[SMTTypeInfo] = None) -> DataType:
        if family == "real":
            return DataType.build("FLOAT")
        if family == "int":
            return DataType.build("INT")
        if family == "text":
            return DataType.build("TEXT")
        if family == "bool":
            return DataType.build("BOOLEAN")
        if family == "date":
            return DataType.build("DATE")
        if family == "time":
            return DataType.build("TIME")
        if family == "timestamp":
            return DataType.build("TIMESTAMP")
        if family == "datetime":
            return DataType.build("DATETIME")
        return left.dtype if right is None else left.dtype

    def _wrap_payload(self, payload: z3.ExprRef, dtype: DataType) -> SMTValue:
        typeinfo = normalize_dtype(dtype, self.z3ctx)
        option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, self.z3ctx)
        return SMTValue(option_sort.Some(payload), typeinfo)

    def _nullable_numeric_binary(
        self,
        left: SMTValue,
        right: SMTValue,
        op: Callable[[z3.ExprRef, z3.ExprRef], z3.ExprRef],
        result_family: Optional[str] = None,
        null_condition: Optional[Callable[[z3.ExprRef, z3.ExprRef], z3.BoolRef]] = None,
    ) -> SMTValue:
        result_family = result_family or (
            "real"
            if left.typeinfo.family == "real" or right.typeinfo.family == "real"
            else "int"
        )
        result_dtype = self._result_family_type(result_family, left.typeinfo, right.typeinfo)
        result_type = normalize_dtype(result_dtype, self.z3ctx)
        option_sort = OptionTypeRegistry.get(result_type.payload_sort, self.z3ctx)
        left_some = _value_some(left)
        right_some = _value_some(right)
        raw_left, raw_right, _ = _coerce_pair(left, right)
        if result_family == "real":
            raw_left = _coerce_numeric_sort(raw_left, z3.RealSort())
            raw_right = _coerce_numeric_sort(raw_right, z3.RealSort())
        null_expr = z3.Not(z3.And(left_some, right_some))
        if null_condition is not None:
            null_expr = z3.Or(null_expr, null_condition(raw_left, raw_right))
        return SMTValue(
            z3.If(null_expr, option_sort.NULL, option_sort.Some(op(raw_left, raw_right))),
            result_type,
        )

    def _nullable_unary(
        self,
        arg: SMTValue,
        op: Callable[[z3.ExprRef], z3.ExprRef],
        result_dtype: DataType,
    ) -> SMTValue:
        result_type = normalize_dtype(result_dtype, self.z3ctx)
        option_sort = OptionTypeRegistry.get(result_type.payload_sort, self.z3ctx)
        return SMTValue(
            z3.If(_value_some(arg), option_sort.Some(op(_value_payload(arg))), option_sort.NULL),
            result_type,
        )

    def _compare_values(
        self, left: SMTValue, right: SMTValue, op: Callable[[z3.ExprRef, z3.ExprRef], z3.BoolRef]
    ) -> z3.BoolRef:
        if left.is_null_literal or right.is_null_literal:
            return z3.BoolVal(False, ctx=self.z3ctx)
        raw_left, raw_right, _ = _coerce_pair(left, right)
        return z3.And(_value_some(left), _value_some(right), op(raw_left, raw_right))

    def _translate_children(self, expression: exp.Expression):
        return [self._to_z3_expr(child) for child in expression.iter_expressions() if not isinstance(child, exp.DataType)]

    def _translate_add(self, expression: exp.Expression) -> SMTValue:
        left = self._as_value(self._to_z3_expr(expression.this))
        right = self._as_value(self._to_z3_expr(expression.expression))
        return self._nullable_numeric_binary(left, right, lambda a, b: a + b)

    def _translate_sub(self, expression: exp.Expression) -> SMTValue:
        left = self._as_value(self._to_z3_expr(expression.this))
        right = self._as_value(self._to_z3_expr(expression.expression))
        return self._nullable_numeric_binary(left, right, lambda a, b: a - b)

    def _translate_mul(self, expression: exp.Expression) -> SMTValue:
        left = self._as_value(self._to_z3_expr(expression.this))
        right = self._as_value(self._to_z3_expr(expression.expression))
        return self._nullable_numeric_binary(left, right, lambda a, b: a * b)

    def _translate_div(self, expression: exp.Expression) -> SMTValue:
        left = self._as_value(self._to_z3_expr(expression.this))
        right = self._as_value(self._to_z3_expr(expression.expression))
        return self._nullable_numeric_binary(
            left,
            right,
            lambda a, b: a / b,
            result_family="real",
            null_condition=lambda _a, b: b == 0,
        )

    def _translate_mod(self, expression: exp.Expression) -> SMTValue:
        left = self._as_value(self._to_z3_expr(expression.this))
        right = self._as_value(self._to_z3_expr(expression.expression))
        return self._nullable_numeric_binary(
            left,
            right,
            lambda a, b: a % b,
            result_family="int",
            null_condition=lambda _a, b: b == 0,
        )

    def _translate_gt(self, expression: exp.Expression) -> z3.BoolRef:
        return self._compare_values(
            self._as_value(self._to_z3_expr(expression.this)),
            self._as_value(self._to_z3_expr(expression.expression)),
            lambda a, b: a > b,
        )

    def _translate_lt(self, expression: exp.Expression) -> z3.BoolRef:
        return self._compare_values(
            self._as_value(self._to_z3_expr(expression.this)),
            self._as_value(self._to_z3_expr(expression.expression)),
            lambda a, b: a < b,
        )

    def _translate_gte(self, expression: exp.Expression) -> z3.BoolRef:
        return self._compare_values(
            self._as_value(self._to_z3_expr(expression.this)),
            self._as_value(self._to_z3_expr(expression.expression)),
            lambda a, b: a >= b,
        )

    def _translate_lte(self, expression: exp.Expression) -> z3.BoolRef:
        return self._compare_values(
            self._as_value(self._to_z3_expr(expression.this)),
            self._as_value(self._to_z3_expr(expression.expression)),
            lambda a, b: a <= b,
        )

    def _translate_eq(self, expression: exp.Expression) -> z3.BoolRef:
        return self._compare_values(
            self._as_value(self._to_z3_expr(expression.this)),
            self._as_value(self._to_z3_expr(expression.expression)),
            lambda a, b: a == b,
        )

    def _translate_neq(self, expression: exp.Expression) -> z3.BoolRef:
        return self._compare_values(
            self._as_value(self._to_z3_expr(expression.this)),
            self._as_value(self._to_z3_expr(expression.expression)),
            lambda a, b: a != b,
        )

    def _translate_like(self, expression: exp.Expression) -> z3.BoolRef:
        return like_to_z3(
            self._as_value(self._to_z3_expr(expression.this)),
            self._as_value(self._to_z3_expr(expression.expression)),
        )

    def _translate_and(self, expression: exp.Expression) -> z3.BoolRef:
        return z3.And(
            self._as_predicate(self._to_z3_expr(expression.this)),
            self._as_predicate(self._to_z3_expr(expression.expression)),
        )

    def _translate_or(self, expression: exp.Expression) -> z3.BoolRef:
        return z3.Or(
            self._as_predicate(self._to_z3_expr(expression.this)),
            self._as_predicate(self._to_z3_expr(expression.expression)),
        )

    def _translate_not(self, expression: exp.Expression) -> z3.BoolRef:
        return z3.Not(self._as_predicate(self._to_z3_expr(expression.this)))

    def _translate_distinct(self, expression: exp.Expression) -> z3.BoolRef:
        items = [self._as_value(self._to_z3_expr(arg)) for arg in expression.expressions]
        exprs = [item.expr for item in items if item.expr is not None]
        return z3.Distinct(*exprs)

    def _translate_is(self, expression: exp.Expression) -> z3.BoolRef:
        left = self._as_value(self._to_z3_expr(expression.this))
        right = self._as_value(self._to_z3_expr(expression.expression))
        if left.is_null_literal and right.is_null_literal:
            return z3.BoolVal(True, ctx=self.z3ctx)
        if right.is_null_literal:
            return _value_null(left)
        if left.is_null_literal:
            return _value_null(right)
        raw_left, raw_right, _ = _coerce_pair(left, right)
        return z3.Or(
            z3.And(_value_null(left), _value_null(right)),
            z3.And(_value_some(left), _value_some(right), raw_left == raw_right),
        )

    def _translate_cast(self, expression: exp.Expression) -> SMTValue:
        value = self._as_value(self._to_z3_expr(expression.this))
        to_dtype = expression.args.get("to") or value.typeinfo.dtype
        to_type = normalize_dtype(to_dtype, self.z3ctx)
        if to_type.family == value.typeinfo.family:
            return SMTValue(value.expr, to_type, value.is_null_literal)
        if value.is_null_literal:
            return _null_value(to_type, self.z3ctx)
        raw = _value_payload(value)
        if to_type.family == "text":
            converted = z3.IntToStr(raw) if value.typeinfo.family in {"int", "date", "time", "datetime", "timestamp"} else raw
            return self._wrap_payload(converted, to_type.dtype)
        if to_type.family == "int" and value.typeinfo.family == "text":
            return self._wrap_payload(z3.StrToInt(raw), to_type.dtype)
        raise UnsupportedSMTError(
            f"Unsupported CAST from {value.typeinfo.logical_name} to {to_type.logical_name}"
        )

    def _translate_nullif(self, expression: exp.Expression) -> SMTValue:
        left = self._as_value(self._to_z3_expr(expression.this))
        right = self._as_value(self._to_z3_expr(expression.expression))
        option_sort = OptionTypeRegistry.get(left.typeinfo.payload_sort, self.z3ctx)
        if left.is_null_literal:
            return left
        raw_left, raw_right, _ = _coerce_pair(left, right)
        return SMTValue(
            z3.If(
                _value_null(left),
                option_sort.NULL,
                z3.If(
                    z3.And(_value_some(left), _value_some(right), raw_left == raw_right),
                    option_sort.NULL,
                    option_sort.Some(_value_payload(left)),
                ),
            ),
            left.typeinfo,
        )

    def _translate_between(self, expression: exp.Expression) -> z3.BoolRef:
        value = self._as_value(self._to_z3_expr(expression.this))
        low = self._as_value(self._to_z3_expr(expression.args["low"]))
        high = self._as_value(self._to_z3_expr(expression.args["high"]))
        if value.is_null_literal or low.is_null_literal or high.is_null_literal:
            return z3.BoolVal(False, ctx=self.z3ctx)
        raw_value = _value_payload(value)
        raw_low = _value_payload(low)
        raw_high = _value_payload(high)
        return z3.And(
            _value_some(value),
            _value_some(low),
            _value_some(high),
            raw_low <= raw_value,
            raw_value <= raw_high,
        )

    def _translate_in(self, expression: exp.Expression) -> z3.BoolRef:
        needle = self._as_value(self._to_z3_expr(expression.this))
        clauses = []
        for candidate_expr in expression.expressions:
            candidate = self._as_value(self._to_z3_expr(candidate_expr))
            clauses.append(self._compare_values(needle, candidate, lambda a, b: a == b))
        return z3.Or(*clauses) if clauses else z3.BoolVal(False, ctx=self.z3ctx)

    def _function_name(self, expression: exp.Expression) -> Optional[str]:
        if isinstance(expression, exp.Anonymous):
            return (expression.name or "").upper()
        if isinstance(expression, exp.Substring):
            return "SUBSTR"
        if isinstance(expression, exp.TimeToStr):
            return "STRFTIME"
        return expression.key.upper() if expression.key else None

    def _function_args(self, expression: exp.Expression):
        if isinstance(expression, exp.Substring):
            args = [expression.this]
            if expression.args.get("start") is not None:
                args.append(expression.args["start"])
            if expression.args.get("length") is not None:
                args.append(expression.args["length"])
            return args
        if isinstance(expression, exp.TimeToStr):
            return [expression.args.get("format"), expression.this]
        return [child for child in expression.iter_expressions() if not isinstance(child, exp.DataType)]

    def _resolve_special_function(
        self, expression: exp.Expression
    ) -> Optional[Union[SMTValue, z3.BoolRef]]:
        name = self._function_name(expression)
        if not name:
            return None
        model = self.function_models.get(name)
        if model is None or not model.matches(expression):
            return None
        args = [self._to_z3_expr(arg) for arg in self._function_args(expression) if arg is not None]
        return model.translator(self, expression, args)

    def _to_z3_expr(self, condition: exp.Expression):
        if isinstance(condition, exp.Paren):
            return self._to_z3_expr(condition.this)
        if isinstance(condition, exp.Column):
            return self._declare_or_get_column(condition)
        if isinstance(condition, exp.Null):
            dtype = condition.args.get("_type") or DataType.build("NULL")
            return encode_literal(dtype, None, self.z3ctx)
        if isinstance(condition, exp.Boolean):
            return z3.BoolVal(bool(condition.this), ctx=self.z3ctx)
        if isinstance(condition, (exp.Literal, Const)):
            datatype = condition.datatype
            literal_value = condition.this
            if datatype.is_type(*DataType.TEMPORAL_TYPES) and isinstance(literal_value, str):
                return encode_literal(datatype, literal_value, self.z3ctx)
            if datatype.is_type(*DataType.TEXT_TYPES) and isinstance(literal_value, str):
                return encode_literal(datatype, literal_value, self.z3ctx)
            if datatype.is_type(DataType.Type.UNKNOWN) and isinstance(literal_value, str) and _is_temporal_string(literal_value):
                return encode_literal(_infer_temporal_dtype(literal_value), literal_value, self.z3ctx)
            return encode_literal(datatype, literal_value, self.z3ctx)

        function_result = self._resolve_special_function(condition)
        if function_result is not None:
            return function_result

        key = condition.key.upper()
        translator = self.core_registry.get(key)
        if translator is not None:
            return translator(condition)

        raise UnsupportedSMTError(
            f"{repr(condition)} not supported in SMT conversion, {type(condition)}"
        )

    def _ensure_str_printable(self, expr: z3.ExprRef):
        if is_option_expr(expr) and option_of(expr).value(expr).sort() == z3.StringSort():
            raw = unwrap_option(expr)
            ascii_printable = z3.Range(chr(32), chr(126))
            self.add(z3.InRe(raw, z3.Star(ascii_printable)), track_vars=False)

    def _ensure_str_length(self, expr: z3.ExprRef, length: int):
        if is_option_expr(expr):
            raw = unwrap_option(expr)
            if isinstance(raw.sort(), z3.SeqSortRef):
                opt = option_of(expr)
                self.add(
                    z3.Implies(
                        opt.is_Some(expr),
                        z3.And(
                            z3.Length(raw) > z3.IntVal(length, ctx=self.z3ctx),
                            z3.Or(
                                z3.Length(raw) == 0,
                                z3.SubString(raw, 0, 1) != z3.StringVal(" ", ctx=self.z3ctx),
                            ),
                        ),
                    ),
                    track_vars=False,
                )

    def _ensure_temporal_bounds(self, expr: z3.ExprRef, typeinfo: SMTTypeInfo):
        opt = option_of(expr)
        value = unwrap_option(expr)
        if typeinfo.family == "date":
            lower = _date_to_epoch_day(date(1970, 1, 1))
            upper = _date_to_epoch_day(date(2030, 1, 1))
        elif typeinfo.family == "time":
            lower, upper = 0, 24 * 3600
        else:
            lower = _datetime_to_epoch_second(datetime(1970, 1, 1, 0, 0, 0))
            upper = _datetime_to_epoch_second(datetime(2030, 1, 1, 0, 0, 0))
        self.add(z3.Implies(opt.is_Some(expr), value > lower), track_vars=False)
        self.add(z3.Implies(opt.is_Some(expr), value < upper), track_vars=False)

    def z3_to_python(self, model: z3.ModelRef):
        result = {}
        for var_name, z3var in self.context.get("variable_to_z3", {}).items():
            if var_name not in self.constrained_var_names:
                continue
            concrete = self._z3_to_python(model.evaluate(z3var, model_completion=True), var_name)
            variable = self.context["z3_to_variable"][var_name]
            if concrete == "":
                continue
            result[var_name] = concrete
            logger.info(
                f"Variable {var_name} with Z3 value {concrete} and data type {DataType.build(variable.type)}"
            )
        return result

    def _decode_option_value(
        self, value: z3.ExprRef, var_name: Optional[str] = None
    ) -> Any:
        decl = value.decl()
        name = decl.name() if decl is not None else ""
        if name == "NULL":
            return None
        if name == "Some" and value.num_args() == 1:
            return self._decode_payload(value.arg(0), var_name)
        rendered = str(z3.simplify(value))
        if rendered == "NULL":
            return None
        if rendered.startswith("Some(") and value.num_args() == 1:
            return self._decode_payload(value.arg(0), var_name)
        raise RuntimeError(f"Invalid option value: {value}")

    def _decode_payload(self, payload: z3.ExprRef, var_name: Optional[str] = None) -> Any:
        if var_name is None:
            return self._raw_payload_to_python(payload)
        variable = self.context["z3_to_variable"][var_name]
        typeinfo = normalize_dtype(variable.type, self.z3ctx)
        raw = self._raw_payload_to_python(payload)
        if raw is None:
            return None
        if typeinfo.family == "date":
            return _from_epoch_day(raw)
        if typeinfo.family == "time":
            return _from_seconds(raw)
        if typeinfo.family in {"datetime", "timestamp"}:
            return _from_epoch_second(raw)
        return raw

    def _raw_payload_to_python(self, payload: z3.ExprRef) -> Any:
        if z3.is_int_value(payload):
            return payload.as_long()
        if z3.is_rational_value(payload):
            value = payload.as_decimal(20)
            return float(value.replace("?", ""))
        if z3.is_string_value(payload):
            return payload.as_string()
        if z3.is_true(payload):
            return True
        if z3.is_false(payload):
            return False
        return str(payload)

    def _z3_to_python(self, value: z3.ExprRef, var_name: Optional[str] = None) -> Any:
        if isinstance(value.sort(), z3.DatatypeSortRef) and OptionTypeRegistry.is_option_sort(
            value.sort()
        ):
            return self._decode_option_value(value, var_name)
        return self._decode_payload(value, var_name)


def _return_same_type(expression: exp.Expression, arg_types: Sequence[SMTTypeInfo]) -> DataType:
    del expression
    return arg_types[0].dtype


def _return_int(_expression: exp.Expression, _arg_types: Sequence[SMTTypeInfo]) -> DataType:
    return DataType.build("INT")


def _return_text(_expression: exp.Expression, _arg_types: Sequence[SMTTypeInfo]) -> DataType:
    return DataType.build("TEXT")


def _translate_abs(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    arg = solver._as_value(args[0])
    return solver._nullable_unary(
        arg,
        lambda raw: z3.If(raw >= 0, raw, -raw),
        arg.typeinfo.dtype,
    )


def _translate_length(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    arg = solver._as_value(args[0])
    return solver._nullable_unary(arg, lambda raw: z3.Length(raw), DataType.build("INT"))


def _translate_substr(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    source = solver._as_value(args[0])
    start = solver._as_value(args[1])
    length = solver._as_value(args[2]) if len(args) > 2 else None
    result_type = normalize_dtype(DataType.build("TEXT"), solver.z3ctx)
    option_sort = OptionTypeRegistry.get(result_type.payload_sort, solver.z3ctx)
    start_payload = z3.If(_value_payload(start) >= 1, _value_payload(start) - 1, 0)
    if length is None:
        body = z3.SubString(
            _value_payload(source), start_payload, z3.Length(_value_payload(source))
        )
        some = z3.And(_value_some(source), _value_some(start))
    else:
        body = z3.SubString(_value_payload(source), start_payload, _value_payload(length))
        some = z3.And(_value_some(source), _value_some(start), _value_some(length))
    return SMTValue(z3.If(some, option_sort.Some(body), option_sort.NULL), result_type)


def _translate_instr(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    haystack = solver._as_value(args[0])
    needle = solver._as_value(args[1])
    result_type = normalize_dtype(DataType.build("INT"), solver.z3ctx)
    option_sort = OptionTypeRegistry.get(result_type.payload_sort, solver.z3ctx)
    index = z3.IndexOf(_value_payload(haystack), _value_payload(needle), z3.IntVal(0))
    one_based = z3.If(index >= 0, index + 1, 0)
    return SMTValue(
        z3.If(
            z3.And(_value_some(haystack), _value_some(needle)),
            option_sort.Some(one_based),
            option_sort.NULL,
        ),
        result_type,
    )


def _ymd_hms_from_temporal(solver: SMTSolver, value: SMTValue):
    raw = _value_payload(value)
    if value.typeinfo.family == "date":
        ts = raw * 86400
    elif value.typeinfo.family == "time":
        ts = raw
    else:
        ts = raw
    second = ts % 60
    minute = (ts / 60) % 60
    hour = (ts / 3600) % 24
    days_since_epoch = ts / 86400
    # Use the Gregorian average year length for a closer symbolic year estimate.
    year_offset = (days_since_epoch * 400) / 146097
    year = 1970 + year_offset
    day_of_year = days_since_epoch - ((year_offset * 146097) / 400)
    month = (day_of_year / 30) + 1
    day = (day_of_year % 30) + 1
    return year, month, day, hour, minute, second


def _translate_strftime(
    solver: SMTSolver, _expression: exp.Expression, args: List[Union[SMTValue, z3.BoolRef]]
) -> SMTValue:
    fmt = solver._as_value(args[0])
    temporal = solver._as_value(args[1])
    fmt_expr = z3.simplify(_value_payload(fmt))
    if not z3.is_string_value(fmt_expr):
        raise UnsupportedSMTError("STRFTIME requires a concrete format string")
    fmt_value = fmt_expr.as_string()
    year, month, day, hour, minute, second = _ymd_hms_from_temporal(solver, temporal)
    if fmt_value == "%Y":
        body = z3.IntToStr(year)
    elif fmt_value == "%m":
        body = _zfill2(month, solver.z3ctx)
    elif fmt_value == "%d":
        body = _zfill2(day, solver.z3ctx)
    elif fmt_value == "%Y-%m-%d":
        body = z3.Concat(
            z3.IntToStr(year),
            z3.StringVal("-"),
            _zfill2(month, solver.z3ctx),
            z3.StringVal("-"),
            _zfill2(day, solver.z3ctx),
        )
    elif fmt_value == "%H":
        body = _zfill2(hour, solver.z3ctx)
    elif fmt_value == "%M":
        body = _zfill2(minute, solver.z3ctx)
    elif fmt_value == "%S":
        body = _zfill2(second, solver.z3ctx)
    else:
        raise UnsupportedSMTError(f"Unsupported STRFTIME format: {fmt_value}")
    result_type = normalize_dtype(DataType.build("TEXT"), solver.z3ctx)
    option_sort = OptionTypeRegistry.get(result_type.payload_sort, solver.z3ctx)
    return SMTValue(
        z3.If(
            z3.And(_value_some(fmt), _value_some(temporal)),
            option_sort.Some(body),
            option_sort.NULL,
        ),
        result_type,
    )


register_special_function("ABS", _translate_abs, return_type=_return_same_type)
register_special_function("LENGTH", _translate_length, return_type=_return_int)
register_special_function("SUBSTR", _translate_substr, return_type=_return_text)
register_special_function("INSTR", _translate_instr, return_type=_return_int)
register_special_function("STRFTIME", _translate_strftime, return_type=_return_text)
