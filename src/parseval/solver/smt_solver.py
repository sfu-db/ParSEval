"""Z3 solver wrapper for ParSEval constraint solving."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import z3
from sqlglot import exp
from sqlglot.expressions import DataType

from .smt_types import (
    SMTTypeInfo,
    SMTValue,
    _VarRef,
    UnsupportedSMTError,
    SpecialFunctionModel,
    _SPECIAL_FUNCTION_MODELS,
    _is_temporal_string,
    _infer_temporal_dtype,
    normalize_dtype,
    OptionTypeRegistry,
    is_option_expr,
    option_of,
    unwrap_option,
    encode_literal,
)
from .types import (
    date_to_epoch_day,
    time_to_seconds,
    datetime_to_epoch_second,
    epoch_day_to_date,
    seconds_to_time,
    epoch_second_to_datetime,
)
from .smt_translate import (
    _coerce_numeric_sort,
    declare_column,
    _value_some,
    _value_null,
    _value_payload,
    _coerce_pair,
    _null_value,
    like_to_z3,
)

logger = logging.getLogger("parseval.smt")

_TEMPORAL_FMTS = {
    "date": ["%Y-%m-%d"],
    "datetime": ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"],
    "timestamp": ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"],
    "time": ["%H:%M:%S", "%H:%M"],
}


def _string_to_temporal_epoch(s: str, family: str) -> Optional[int]:
    """Convert a string to its temporal epoch value (days, seconds, etc.)."""
    from datetime import time as dt_time
    for fmt in _TEMPORAL_FMTS.get(family, []):
        try:
            if family == "date":
                return date_to_epoch_day(datetime.strptime(s, fmt).date())
            elif family in ("datetime", "timestamp"):
                return datetime_to_epoch_second(datetime.strptime(s, fmt))
            elif family == "time":
                t = dt_time.fromisoformat(s) if hasattr(dt_time, "fromisoformat") else datetime.strptime(s, fmt).time()
                return time_to_seconds(t)
        except (ValueError, AttributeError):
            continue
    return None


try:
    from z3.z3util import get_vars
except Exception:
    get_vars = None


@contextmanager
def checkpoint(z3solver):
    """Context manager that pushes/pops an SMT solver checkpoint.

    Allows tentative constraint additions to be rolled back on failure.
    """
    z3solver.push()
    try:
        yield z3solver
    finally:
        z3solver.pop()


class SMTSolver:
    """Full Z3-backed SMT solver for complex SQL constraint satisfaction.

    Translates SQL expressions (via sqlglot AST) into Z3 constraints using
    a discriminated-union (Option type) encoding for SQL NULL semantics.
    Supports arithmetic, comparisons, logical operators, LIKE, IS, CAST,
    NULLIF, BETWEEN, IN, CASE, IF, COALESCE, NEG, string concatenation (DPIPE),
    and registered special functions.

    Attributes:
        verbose: If True, log constraints as they are added.
        z3ctx: Optional Z3 context (defaults to global context).
        timeout_ms: Optional solver timeout in milliseconds.
        model: The Z3 model after a successful solve, or None.
        context: Dict storing the bidirectional mapping between column names
            and their Z3 variable expressions.
        function_models: Registry of special function translators.
        core_registry: Mapping of SQL expression keys to translation methods.
    """

    def __init__(
        self,
        z3ctx: Optional[z3.Context] = None,
        verbose: bool = False,
        function_models: Optional[
            Union[Sequence[SpecialFunctionModel], Dict[str, SpecialFunctionModel]]
        ] = None,
        timeout_ms: Optional[int] = None,
    ):
        """Initialize the SMT solver.

        Args:
            z3ctx: Optional Z3 context; defaults to Z3's global context.
            verbose: If True, log each added constraint via the ``parseval.smt`` logger.
            function_models: Optional custom function translators (list or dict).
            timeout_ms: Optional solver timeout in milliseconds.
        """
        self.verbose = verbose
        self.z3ctx = z3ctx
        self.solver = z3.Solver(ctx=self.z3ctx)
        if timeout_ms is not None and timeout_ms > 0:
            try:
                self.solver.set("timeout", int(timeout_ms))
            except Exception:
                pass
        self.timeout_ms = timeout_ms
        self.model = None
        self.context: Dict[str, Dict[str, Any]] = {}
        self._domain_constraints_applied = False
        self.constrained_var_names = set()
        self._translate_ctx = None
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
        """Merge custom function models with the global registry into a single dict."""
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
        """Build the dispatch table mapping SQL expression types to translators."""
        return {
            "ADD": lambda e: self._translate_arithmetic(e, lambda a, b: a + b),
            "SUB": lambda e: self._translate_arithmetic(e, lambda a, b: a - b),
            "MUL": lambda e: self._translate_arithmetic(e, lambda a, b: a * b),
            "DIV": self._translate_div,
            "MOD": self._translate_mod,
            "GT": lambda e: self._translate_comparison(e, lambda a, b: a > b),
            "LT": lambda e: self._translate_comparison(e, lambda a, b: a < b),
            "GTE": lambda e: self._translate_comparison(e, lambda a, b: a >= b),
            "LTE": lambda e: self._translate_comparison(e, lambda a, b: a <= b),
            "EQ": lambda e: self._translate_comparison(e, lambda a, b: a == b),
            "NEQ": lambda e: self._translate_comparison(e, lambda a, b: a != b),
            "LIKE": self._translate_like,
            "AND": self._translate_and,
            "OR": self._translate_or,
            "NOT": self._translate_not,
            "DISTINCT": self._translate_distinct,
            "IS": self._translate_is,
            "CAST": self._translate_cast,
            "TSORDSTOTIMESTAMP": self._translate_ts_or_ds_to_timestamp,
            "TSORDSTODATE": self._translate_ts_or_ds_to_date,
            "NULLIF": self._translate_nullif,
            "BETWEEN": self._translate_between,
            "IN": self._translate_in,
            "CASE": self._translate_case,
            "IF": self._translate_if,
            "COALESCE": self._translate_coalesce,
            "NEG": self._translate_neg,
            "DPIPE": self._translate_dpipe,
        }

    def declare_variable(self, name: str, datatype: DataType) -> z3.ExprRef:
        """Declare an Option-wrapped Z3 variable with a custom name.

        Unlike _declare_or_get_column (which takes sqlglot Column objects),
        this accepts a string name and DataType directly. The variable is
        stored in the solver's context so translate() and solve() can find it.

        Returns the Option-wrapped Z3 expression.
        """
        if name in self.context.get("variable_to_z3", {}):
            return self.context["variable_to_z3"][name]
        type_info = normalize_dtype(datatype, self.z3ctx)
        option_type = OptionTypeRegistry.get(type_info.payload_sort, self.z3ctx)
        z3_var = z3.Const(name, option_type)
        self.context.setdefault("variable_to_z3", {})[name] = z3_var
        # Store _VarRef so _z3_to_python can access .type for temporal decoding
        self.context.setdefault("z3_to_variable", {})[name] = _VarRef(type=datatype)
        return z3_var

    def translate(
        self, expr: exp.Expression, ctx: Optional[Dict[str, z3.ExprRef]] = None
    ) -> Optional[z3.BoolRef]:
        """Translate a sqlglot AST expression to Z3.

        If ctx is provided, Column nodes are resolved from ctx (keyed as
        "normalized_table.normalized_name") before the solver's default context.
        Returns a raw z3.BoolRef, or None on failure.
        """
        prev_ctx = self._translate_ctx
        self._translate_ctx = ctx
        try:
            result = self._to_z3_expr(expr, ctx=ctx)
            if isinstance(result, SMTValue):
                return self._as_predicate(result)
            return result
        except Exception as e:
            logger.debug("translate failed: %s", e)
            return None
        finally:
            self._translate_ctx = prev_ctx

    def add_raw(self, constraint: z3.BoolRef) -> None:
        """Add a raw Z3 boolean expression directly to the solver.

        Unlike add(), this does not convert SMTValue or track variables
        via get_vars. Use for constraints built outside translate()
        (e.g., JOIN equalities between declared variables).
        """
        self.solver.add(constraint)

    def solve_raw(
        self, var_symbols: Dict[str, Any]
    ) -> Tuple[str, Dict[str, Any]]:
        """Solve and extract values for variables declared via declare_variable.

        Args:
            var_symbols: Maps variable name (str) to Variable symbol.
                Only variables in this dict are extracted from the model.

        Returns:
            ("sat", {var_name: python_value}) or ("unsat", {})
        """
        self._apply_domain_constraints()

        status = self.solver.check()
        if status != z3.sat:
            return ("unsat", {})

        model = self.solver.model()
        self.model = model
        solution = {}
        for var_name in var_symbols:
            z3_var = self.context.get("variable_to_z3", {}).get(var_name)
            if z3_var is None:
                continue
            z3_val = model.evaluate(z3_var, model_completion=True)
            python_val = self._z3_to_python(z3_val, var_name)
            if python_val is not None:
                solution[var_name] = python_val
        return ("sat", solution)

    @staticmethod
    def apply_solution(
        var_symbols: Dict[str, Any], solution: Dict[str, Any]
    ) -> None:
        """Write solution values back into Variable symbols.

        Args:
            var_symbols: Maps variable name -> Variable symbol (has .set method).
            solution: Maps variable name -> Python value (from solve_raw).
        """
        for var_name, value in solution.items():
            sym = var_symbols.get(var_name)
            if sym is not None and value is not None:
                sym.set("concrete", value)
                sym.set("is_bound", True)
                sym.set("is_null", False)

    def _infer_type_info(self, col: exp.Column) -> SMTTypeInfo:
        """Infer SMTTypeInfo for a Column from its .type attribute."""
        dtype = getattr(col, "type", None)
        if dtype is None or str(dtype) in ("", "UNKNOWN"):
            dtype = DataType.build("TEXT")
        return normalize_dtype(dtype, self.z3ctx)

    def add(self, constraint, track_vars: bool = True):
        """Add a constraint expression to the Z3 solver.

        Args:
            constraint: A Z3 boolean expression or an SMTValue (which gets
                converted to a predicate via ``_as_predicate``).
            track_vars: If True, extract and track Z3 variables for later
                solution extraction.
        """
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
        """Check satisfiability and return the solution mapping.

        Applies domain constraints (temporal bounds, string character limits)
        on the first call, then invokes the Z3 solver. Returns a tuple of
        ``("sat", {var_name: python_value})`` or ``("unsat", {})``.

        The returned dict keys are ``"table.column"`` strings. Only variables
        referenced in the added constraints are included.
        """
        self._apply_domain_constraints()

        status = self.solver.check()
        if status != z3.sat:
            return "unsat", {}
        self.model = self.solver.model()
        solutions = self.z3_to_python(self.model) or {}
        logger.info(f"SMT solver found solution: {solutions}")
        return "sat", solutions

    def _declare_or_get_column(self, condition: exp.Column) -> SMTValue:
        """Look up or create a Z3 variable for a column reference.

        Maintains a bidirectional mapping between column names
        (``"table.column"``) and Z3 expressions in ``self.context``.

        Args:
            condition: A sqlglot Column expression.

        Returns:
            An SMTValue wrapping the Z3 variable with its type info.
        """
        col_key = f"{condition.table}.{condition.name}"
        if col_key not in self.context.get("variable_to_z3", {}):
            value = declare_column(condition, z3ctx=self.z3ctx)
            self.context.setdefault("variable_to_z3", {})[col_key] = value.expr
            self.context.setdefault("z3_to_variable", {})[str(value.expr)] = condition
        expr = self.context["variable_to_z3"][col_key]
        return SMTValue(expr, normalize_dtype(condition.type, self.z3ctx))

    def _as_value(self, item) -> SMTValue:
        """Assert that an item is an SMTValue and return it."""
        if isinstance(item, SMTValue):
            return item
        raise UnsupportedSMTError(f"Expected a value expression, got {item!r}")

    def _as_predicate(self, item) -> z3.BoolRef:
        """Convert an SMTValue to a Z3 boolean predicate.

        A non-boolean SMTValue is rejected. NULL literals map to False.
        Non-null values are unwrapped to their payload and asserted as Some.
        """
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
        """Determine the result DataType for a binary operation between two type families."""
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
        """Wrap a Z3 payload expression in an ``Option.Some(...)`` with type info."""
        typeinfo = normalize_dtype(dtype, self.z3ctx)
        option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, self.z3ctx)
        return SMTValue(option_sort.Some(payload), typeinfo)

    def _wrap_nullable_payload(self, source: SMTValue, payload: z3.ExprRef, dtype: DataType) -> SMTValue:
        """Wrap a payload as ``Some(...)`` when source is non-null, else ``NULL``."""
        typeinfo = normalize_dtype(dtype, self.z3ctx)
        option_sort = OptionTypeRegistry.get(typeinfo.payload_sort, self.z3ctx)
        return SMTValue(
            z3.If(_value_some(source), option_sort.Some(payload), option_sort.NULL),
            typeinfo,
        )

    def _coerce_value_to_type(self, value: SMTValue, target_dtype: DataType) -> SMTValue:
        """Coerce an SMTValue to a different SQL DataType within Z3.

        Supports int<->real, int/text/date/time/datetime/timestamp<->text,
        and text->int conversions.  Raises UnsupportedSMTError for
        unsupported type pairs.
        """
        target_type = normalize_dtype(target_dtype, self.z3ctx)
        if value.typeinfo.family == target_type.family:
            return SMTValue(value.expr, target_type, value.is_null_literal)
        if value.is_null_literal:
            return _null_value(target_type, self.z3ctx)
        raw = _value_payload(value)
        if target_type.family == "real" and value.typeinfo.family == "int":
            return self._wrap_payload(z3.ToReal(raw), target_type.dtype)
        if target_type.family == "int" and value.typeinfo.family == "real":
            return self._wrap_payload(z3.ToInt(raw), target_type.dtype)
        if target_type.family == "text":
            if value.typeinfo.family in {"int", "date", "time", "datetime", "timestamp"}:
                return self._wrap_payload(z3.IntToStr(raw), target_type.dtype)
            if value.typeinfo.family == "bool":
                return self._wrap_payload(
                    z3.If(
                        raw,
                        z3.StringVal("TRUE", ctx=self.z3ctx),
                        z3.StringVal("FALSE", ctx=self.z3ctx),
                    ),
                    target_type.dtype,
                )
        if target_type.family == "int" and value.typeinfo.family == "text":
            return self._wrap_payload(z3.StrToInt(raw), target_type.dtype)
        raise UnsupportedSMTError(
            f"Unsupported conversion from {value.typeinfo.logical_name} to {target_type.logical_name}"
        )

    def _common_case_dtype(self, expression: exp.Expression, branches: Sequence[SMTValue]) -> DataType:
        """Infer the common result DataType from a list of branch SMTValues.

        Checks for explicit type annotations first, then falls back to
        family-based precedence (text > real > int > bool > datetime >
        timestamp > date > time).
        """
        annotated = getattr(expression, "type", None)
        if annotated is not None and not DataType.build(annotated).is_type(DataType.Type.UNKNOWN):
            return annotated
        families = {branch.typeinfo.family for branch in branches}
        if "text" in families:
            return DataType.build("TEXT")
        if "real" in families:
            return DataType.build("FLOAT")
        if "int" in families:
            return DataType.build("INT")
        if "bool" in families:
            return DataType.build("BOOLEAN")
        if "datetime" in families:
            return DataType.build("DATETIME")
        if "timestamp" in families:
            return DataType.build("TIMESTAMP")
        if "date" in families:
            return DataType.build("DATE")
        if "time" in families:
            return DataType.build("TIME")
        return branches[0].typeinfo.dtype

    def _nullable_numeric_binary(
        self,
        left: SMTValue,
        right: SMTValue,
        op: Callable[[z3.ExprRef, z3.ExprRef], z3.ExprRef],
        result_family: Optional[str] = None,
        null_condition: Optional[Callable[[z3.ExprRef, z3.ExprRef], z3.BoolRef]] = None,
    ) -> SMTValue:
        """Apply a binary numeric operation with SQL NULL propagation.

        If either operand is NULL (absent), the result is NULL.  An optional
        ``null_condition`` can force NULL for additional cases (e.g., div-by-zero).
        """
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

    def _translate_arithmetic(self, expression: exp.Expression, op: Callable) -> SMTValue:
        """Translate a binary arithmetic operation with NULL propagation."""
        left = self._as_value(self._to_z3_expr(expression.this))
        right = self._as_value(self._to_z3_expr(expression.expression))
        return self._nullable_numeric_binary(left, right, op)

    def _translate_div(self, expression: exp.Expression) -> SMTValue:
        """Translate a division (``a / b``) with NULL propagation and div-by-zero handling."""
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
        """Translate a modulo (``a % b``) with NULL propagation and div-by-zero handling."""
        left = self._as_value(self._to_z3_expr(expression.this))
        right = self._as_value(self._to_z3_expr(expression.expression))
        return self._nullable_numeric_binary(
            left,
            right,
            lambda a, b: a % b,
            result_family="int",
            null_condition=lambda _a, b: b == 0,
        )

    def _translate_comparison(self, expression: exp.Expression, op: Callable) -> z3.BoolRef:
        """Translate a binary comparison with the given operator."""
        left_expr = expression.this
        right_expr = expression.expression
        left = self._as_value(self._to_z3_expr(left_expr))
        right = self._as_value(self._to_z3_expr(right_expr))
        # Auto-coerce temporal column vs string literal.
        left, right = self._coerce_temporal_pair(left, right, left_expr, right_expr)
        return self._compare_values(left, right, op)

    _TEMPORAL_FAMILIES = {"date", "time", "datetime", "timestamp"}

    def _coerce_temporal_pair(
        self, left: SMTValue, right: SMTValue,
        left_expr: exp.Expression, right_expr: exp.Expression,
    ) -> Tuple[SMTValue, SMTValue]:
        """If one side is temporal and the other is a string literal, coerce."""
        if left.typeinfo.family in self._TEMPORAL_FAMILIES and isinstance(right_expr, exp.Literal) and right_expr.is_string:
            coerced = self._encode_temporal_literal(str(right_expr.this), left.typeinfo)
            if coerced is not None:
                return left, coerced
        if right.typeinfo.family in self._TEMPORAL_FAMILIES and isinstance(left_expr, exp.Literal) and left_expr.is_string:
            coerced = self._encode_temporal_literal(str(left_expr.this), right.typeinfo)
            if coerced is not None:
                return coerced, right
        return left, right

    def _encode_temporal_literal(self, s: str, target: SMTTypeInfo) -> Optional[SMTValue]:
        """Encode a string as a temporal epoch value wrapped in Option."""
        epoch = _string_to_temporal_epoch(s, target.family)
        if epoch is None:
            return None
        z3val = z3.IntVal(epoch, ctx=self.z3ctx)
        option = OptionTypeRegistry.get(target.payload_sort, self.z3ctx)
        return SMTValue(option.Some(z3val), target)

    def _translate_like(self, expression: exp.Expression) -> z3.BoolRef:
        """Translate a LIKE pattern match into Z3 string constraints."""
        return like_to_z3(
            self._as_value(self._to_z3_expr(expression.this)),
            self._as_value(self._to_z3_expr(expression.expression)),
        )

    def _translate_and(self, expression: exp.Expression) -> z3.BoolRef:
        """Translate a logical AND (``a AND b``)."""
        return z3.And(
            self._as_predicate(self._to_z3_expr(expression.this)),
            self._as_predicate(self._to_z3_expr(expression.expression)),
        )

    def _translate_or(self, expression: exp.Expression) -> z3.BoolRef:
        """Translate a logical OR (``a OR b``)."""
        return z3.Or(
            self._as_predicate(self._to_z3_expr(expression.this)),
            self._as_predicate(self._to_z3_expr(expression.expression)),
        )

    def _translate_not(self, expression: exp.Expression) -> z3.BoolRef:
        """Translate a logical NOT."""
        return z3.Not(self._as_predicate(self._to_z3_expr(expression.this)))

    def _translate_distinct(self, expression: exp.Expression) -> z3.BoolRef:
        """Translate a ``DISTINCT`` / ``!= ALL`` comparison."""
        items = [self._as_value(self._to_z3_expr(arg)) for arg in expression.expressions]
        exprs = [item.expr for item in items if item.expr is not None]
        return z3.Distinct(*exprs)

    def _translate_is(self, expression: exp.Expression) -> z3.BoolRef:
        """Translate an ``IS`` / ``IS NOT`` comparison (including NULL checks)."""
        left = self._as_value(self._to_z3_expr(expression.this))
        right_expr = expression.expression
        # IS NOT NULL: col IS NOT(NULL)
        if (isinstance(right_expr, exp.Not)
                and isinstance(right_expr.this, exp.Null)):
            return _value_some(left)
        # IS NULL
        if isinstance(right_expr, exp.Null):
            return _value_null(left)
        # General IS: translate right side as value
        right = self._as_value(self._to_z3_expr(right_expr))
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
        """Translate a ``CAST(expr AS type)`` expression."""
        value = self._as_value(self._to_z3_expr(expression.this))
        to_dtype = expression.args.get("to") or value.typeinfo.dtype
        to_type = normalize_dtype(to_dtype, self.z3ctx)
        if to_type.family == value.typeinfo.family:
            return SMTValue(value.expr, to_type, value.is_null_literal)
        if value.is_null_literal:
            return _null_value(to_type, self.z3ctx)
        raw = _value_payload(value)
        if value.typeinfo.family in {"date", "time", "datetime", "timestamp"} and to_type.family in {
            "date",
            "time",
            "datetime",
            "timestamp",
        }:
            if value.typeinfo.family == "date" and to_type.family in {"datetime", "timestamp"}:
                return self._wrap_nullable_payload(value, raw * 86400, to_type.dtype)
            if value.typeinfo.family in {"datetime", "timestamp"} and to_type.family == "date":
                return self._wrap_nullable_payload(value, raw / 86400, to_type.dtype)
            if value.typeinfo.family == "time" and to_type.family in {"datetime", "timestamp"}:
                return self._wrap_nullable_payload(value, raw, to_type.dtype)
            if value.typeinfo.family in {"datetime", "timestamp"} and to_type.family == "time":
                return self._wrap_nullable_payload(value, raw % 86400, to_type.dtype)
        if to_type.family == "text":
            converted = z3.IntToStr(raw) if value.typeinfo.family in {"int", "date", "time", "datetime", "timestamp"} else raw
            return self._wrap_nullable_payload(value, converted, to_type.dtype)
        if to_type.family == "int" and value.typeinfo.family == "text":
            return self._wrap_nullable_payload(value, z3.StrToInt(raw), to_type.dtype)
        raise UnsupportedSMTError(
            f"Unsupported CAST from {value.typeinfo.logical_name} to {to_type.logical_name}"
        )

    def _translate_ts_or_ds_to_timestamp(self, expression: exp.Expression) -> SMTValue:
        """Translate ``TsOrDsToTimestamp(expr)`` — sqlglot's auto-inserted cast.

        sqlglot wraps temporal arguments to STRFTIME in this node to coerce
        them to TIMESTAMP. For DATE inputs, multiplies epoch-days by 86400
        to produce epoch-seconds. For already-temporal inputs, it is a
        no-op.
        """
        value = self._as_value(self._to_z3_expr(expression.this))
        target_type = normalize_dtype(DataType.build("TIMESTAMP"), self.z3ctx)
        if value.is_null_literal:
            return _null_value(target_type, self.z3ctx)
        if value.typeinfo.family in {"datetime", "timestamp"}:
            return SMTValue(value.expr, target_type, value.is_null_literal)
        if value.typeinfo.family == "date":
            raw = _value_payload(value)
            return self._wrap_nullable_payload(value, raw * 86400, target_type.dtype)
        if value.typeinfo.family == "time":
            return SMTValue(value.expr, target_type, value.is_null_literal)
        raise UnsupportedSMTError(
            f"Unsupported TsOrDsToTimestamp from {value.typeinfo.logical_name}"
        )

    def _translate_ts_or_ds_to_date(self, expression: exp.Expression) -> SMTValue:
        """Translate ``TsOrDsToDate(expr)`` — sqlglot's auto-inserted cast.

        Used by MySQL/PG wrappers around temporal extractors (YEAR/MONTH/
        DAY) when the underlying column is a TIMESTAMP or DATETIME. For
        those, we divide epoch-seconds by 86400 to recover epoch-days.
        For DATE inputs this is a no-op.
        """
        value = self._as_value(self._to_z3_expr(expression.this))
        target_type = normalize_dtype(DataType.build("DATE"), self.z3ctx)
        if value.is_null_literal:
            return _null_value(target_type, self.z3ctx)
        if value.typeinfo.family == "date":
            return SMTValue(value.expr, target_type, value.is_null_literal)
        if value.typeinfo.family in {"datetime", "timestamp"}:
            raw = _value_payload(value)
            return self._wrap_nullable_payload(value, raw / 86400, target_type.dtype)
        if value.typeinfo.family == "time":
            return SMTValue(value.expr, target_type, value.is_null_literal)
        raise UnsupportedSMTError(
            f"Unsupported TsOrDsToDate from {value.typeinfo.logical_name}"
        )

    def _translate_nullif(self, expression: exp.Expression) -> SMTValue:
        """Translate ``NULLIF(a, b)`` — returns NULL if a equals b, else a."""
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
        """Translate ``value BETWEEN low AND high`` (inclusive range check)."""
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
        """Translate ``value IN (v1, v2, ...)`` as a disjunction of equalities."""
        needle = self._as_value(self._to_z3_expr(expression.this))
        clauses = []
        for candidate_expr in expression.expressions:
            candidate = self._as_value(self._to_z3_expr(candidate_expr))
            clauses.append(self._compare_values(needle, candidate, lambda a, b: a == b))
        return z3.Or(*clauses) if clauses else z3.BoolVal(False, ctx=self.z3ctx)

    def _translate_neg(self, expression: exp.Expression) -> SMTValue:
        """Translate unary negation (``-a``) with NULL propagation."""
        value = self._as_value(self._to_z3_expr(expression.this))
        return self._nullable_unary(value, lambda raw: -raw, value.typeinfo.dtype)

    def _translate_dpipe(self, expression: exp.Expression) -> SMTValue:
        """Translate string concatenation (``a || b``), coercing both sides to TEXT."""
        left = self._coerce_value_to_type(
            self._as_value(self._to_z3_expr(expression.this)),
            DataType.build("TEXT"),
        )
        right = self._coerce_value_to_type(
            self._as_value(self._to_z3_expr(expression.expression)),
            DataType.build("TEXT"),
        )
        result_type = normalize_dtype(DataType.build("TEXT"), self.z3ctx)
        option_sort = OptionTypeRegistry.get(result_type.payload_sort, self.z3ctx)
        return SMTValue(
            z3.If(
                z3.And(_value_some(left), _value_some(right)),
                option_sort.Some(z3.Concat(_value_payload(left), _value_payload(right))),
                option_sort.NULL,
            ),
            result_type,
        )

    def _translate_if(self, expression: exp.Expression) -> SMTValue:
        """Translate an ``IF(cond, true_val, false_val)`` expression."""
        condition = self._as_predicate(self._to_z3_expr(expression.this))
        true_value = self._as_value(self._to_z3_expr(expression.args["true"]))
        false_expr = expression.args.get("false")
        false_value = (
            self._as_value(self._to_z3_expr(false_expr))
            if false_expr is not None
            else _null_value(normalize_dtype(true_value.typeinfo.dtype, self.z3ctx), self.z3ctx)
        )
        result_dtype = self._common_case_dtype(expression, [true_value, false_value])
        true_value = self._coerce_value_to_type(true_value, result_dtype)
        false_value = self._coerce_value_to_type(false_value, result_dtype)
        result_type = normalize_dtype(result_dtype, self.z3ctx)
        option_sort = OptionTypeRegistry.get(result_type.payload_sort, self.z3ctx)
        return SMTValue(
            z3.If(condition, true_value.expr, false_value.expr),
            result_type,
        )

    def _translate_case(self, expression: exp.Expression) -> SMTValue:
        """Translate a ``CASE WHEN ... THEN ... ELSE ... END`` expression."""
        branches: List[Tuple[z3.BoolRef, SMTValue]] = []
        for when in expression.args.get("ifs") or []:
            predicate = self._as_predicate(self._to_z3_expr(when.this))
            branch_value = self._as_value(self._to_z3_expr(when.args["true"]))
            branches.append((predicate, branch_value))
        default_expr = expression.args.get("default")
        if default_expr is not None:
            default_value = self._as_value(self._to_z3_expr(default_expr))
        elif branches:
            default_value = _null_value(
                normalize_dtype(branches[0][1].typeinfo.dtype, self.z3ctx), self.z3ctx
            )
        else:
            default_value = encode_literal(DataType.build("NULL"), None, self.z3ctx)
        all_values = [value for _, value in branches] + [default_value]
        result_dtype = self._common_case_dtype(expression, all_values)
        result_type = normalize_dtype(result_dtype, self.z3ctx)
        default_value = self._coerce_value_to_type(default_value, result_dtype)
        branch_expr = default_value.expr
        for predicate, value in reversed(branches):
            coerced = self._coerce_value_to_type(value, result_dtype)
            branch_expr = z3.If(predicate, coerced.expr, branch_expr)
        return SMTValue(branch_expr, result_type)

    def _translate_coalesce(self, expression: exp.Expression) -> SMTValue:
        """Translate ``COALESCE(a, b, ...)`` — first non-NULL argument."""
        args = [self._as_value(self._to_z3_expr(arg)) for arg in expression.expressions]
        if not args:
            return encode_literal(DataType.build("NULL"), None, self.z3ctx)
        result_dtype = self._common_case_dtype(expression, args)
        result_type = normalize_dtype(result_dtype, self.z3ctx)
        fallback = _null_value(result_type, self.z3ctx)
        expr = fallback.expr
        for arg in reversed(args):
            coerced = self._coerce_value_to_type(arg, result_dtype)
            expr = z3.If(_value_some(coerced), coerced.expr, expr)
        return SMTValue(expr, result_type)

    def _function_name(self, expression: exp.Expression) -> Optional[str]:
        """Extract the canonical function name from a sqlglot expression.

        Handles exp.Anonymous, exp.Substring, exp.TimeToStr, and
        generic expressions with a ``key`` attribute.
        """
        if isinstance(expression, exp.Anonymous):
            return (expression.name or "").upper()
        if isinstance(expression, exp.Substring):
            return "SUBSTR"
        if isinstance(expression, exp.TimeToStr):
            return "STRFTIME"
        return expression.key.upper() if expression.key else None

    def _function_args(self, expression: exp.Expression):
        """Extract the argument list from a sqlglot function expression.

        Handles special cases for SUBSTR, STRFTIME, and generic functions,
        skipping DataType child nodes.
        """
        if isinstance(expression, exp.Substring):
            args = [expression.this]
            if expression.args.get("start") is not None:
                args.append(expression.args["start"])
            if expression.args.get("length") is not None:
                args.append(expression.args["length"])
            return args
        if isinstance(expression, exp.TimeToStr):
            return [expression.args.get("format"), expression.this]
        if isinstance(expression, exp.Extract):
            return [expression.expression]
        if isinstance(expression, (exp.DateAdd, exp.DateSub)):
            return [expression.this, expression.expression]
        if isinstance(expression, (exp.DateDiff, exp.TimestampDiff)):
            return [expression.this, expression.expression]
        return [child for child in expression.iter_expressions() if not isinstance(child, exp.DataType)]

    def _resolve_special_function(
        self, expression: exp.Expression
    ) -> Optional[Union[SMTValue, z3.BoolRef]]:
        """Try to translate a function call using a registered special model.

        Looks up the function name in ``self.function_models`` and, if a
        matching model is found, invokes the model's translator.

        Returns None if no model matches.
        """
        name = self._function_name(expression)
        if not name:
            return None
        model = self.function_models.get(name)
        if model is None:
            return None
        args = [self._to_z3_expr(arg) for arg in self._function_args(expression) if arg is not None]
        return model.translator(self, expression, args)

    def _to_z3_expr(self, condition: exp.Expression, ctx: Optional[Dict[str, z3.ExprRef]] = None):
        """Recursively translate a sqlglot AST node into a Z3 expression.

        Handles: Paren, Column, Null, Boolean, Literal, and any
        node matching a registered special function or core registry key.
        """
        # Use explicit ctx or fall back to instance-level context set by translate()
        effective_ctx = ctx if ctx is not None else getattr(self, '_translate_ctx', None)
        if isinstance(condition, exp.Paren):
            return self._to_z3_expr(condition.this, ctx=effective_ctx)
        if isinstance(condition, exp.Column):
            # Check caller's context first
            if effective_ctx is not None:
                from parseval.helper import normalize_name
                col_key = (
                    f"{normalize_name(condition.table)}.{normalize_name(condition.name)}"
                    if condition.table
                    else normalize_name(condition.name)
                )
                if col_key in effective_ctx:
                    raw = effective_ctx[col_key]
                    type_info = self._infer_type_info(condition)
                    return SMTValue(raw, type_info)
            return self._declare_or_get_column(condition)
        if isinstance(condition, exp.Null):
            dtype = condition.args.get("_type") or DataType.build("NULL")
            return encode_literal(dtype, None, self.z3ctx)
        if isinstance(condition, exp.Boolean):
            return z3.BoolVal(bool(condition.this), ctx=self.z3ctx)
        if isinstance(condition, exp.Literal) or condition.key == "const":
            datatype = getattr(condition, 'datatype', None)
            if datatype is None:
                if condition.is_string:
                    datatype = DataType.build("TEXT")
                elif condition.is_int:
                    datatype = DataType.build("INT")
                else:
                    datatype = DataType.build("FLOAT")
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

    def _apply_domain_constraints(self) -> None:
        """Apply printable-ASCII and temporal-bound constraints to all variables.

        Called once before the first ``solver.check()``.
        """
        if self._domain_constraints_applied:
            return
        for var_name, z3var in self.context.get("variable_to_z3", {}).items():
            column = self.context["z3_to_variable"].get(str(z3var))
            if column is None:
                continue
            typeinfo = normalize_dtype(column.type, self.z3ctx)
            if typeinfo.family in {"date", "time", "datetime", "timestamp"}:
                self._ensure_temporal_bounds(z3var, typeinfo)
            if typeinfo.family == "text":
                self._ensure_str_printable(z3var)
                self._ensure_str_length(z3var, 0)
        self._domain_constraints_applied = True

    def _ensure_str_printable(self, expr: z3.ExprRef):
        """Constrain string values to printable ASCII (space through tilde)."""
        if is_option_expr(expr) and option_of(expr).value(expr).sort() == z3.StringSort():
            raw = unwrap_option(expr)
            ascii_printable = z3.Range(chr(32), chr(126))
            self.add(z3.InRe(raw, z3.Star(ascii_printable)), track_vars=False)

    def _ensure_str_length(self, expr: z3.ExprRef, length: int):
        """Constrain string values to be non-empty and longer than ``length``."""
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
        """Constrain temporal values to the range 1970-01-01 to 2030-01-01."""
        opt = option_of(expr)
        value = unwrap_option(expr)
        if typeinfo.family == "date":
            lower = date_to_epoch_day(date(1970, 1, 1))
            upper = date_to_epoch_day(date(2030, 1, 1))
        elif typeinfo.family == "time":
            lower, upper = 0, 24 * 3600
        else:
            lower = datetime_to_epoch_second(datetime(1970, 1, 1, 0, 0, 0))
            upper = datetime_to_epoch_second(datetime(2030, 1, 1, 0, 0, 0))
        self.add(z3.Implies(opt.is_Some(expr), value > lower), track_vars=False)
        self.add(z3.Implies(opt.is_Some(expr), value < upper), track_vars=False)

    def z3_to_python(self, model: z3.ModelRef):
        """Extract concrete Python values from a Z3 model for tracked variables.

        Only returns values for variables that were actively constrained
        (i.e., appear in ``self.constrained_var_names``).

        Args:
            model: A satisfiable Z3 model.

        Returns:
            Dict mapping variable name (``"table.column"``) to Python value.
        """
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
        """Decode a Z3 Option value (NULL or Some(...)) into a Python value.

        Args:
            value: A Z3 expression of an Option datatype.
            var_name: Optional variable name for type-aware decoding.

        Returns:
            None for NULL, or the decoded payload value.

        Raises:
            RuntimeError: If the value is not a valid Option.
        """
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
        """Convert a Z3 payload expression to a Python value.

        If ``var_name`` is provided, uses the column's type info for
        temporal/date decoding. Otherwise, uses raw payload conversion.

        Args:
            payload: The Z3 payload expression (already unwrapped from Option).
            var_name: Optional variable name for type-aware decoding.

        Returns:
            A Python value (int, float, str, bool, date, time, datetime, or None).
        """
        if var_name is None:
            return self._raw_payload_to_python(payload)
        variable = self.context["z3_to_variable"][var_name]
        typeinfo = normalize_dtype(variable.type, self.z3ctx)
        raw = self._raw_payload_to_python(payload)
        if raw is None:
            return None
        if typeinfo.family == "date":
            return epoch_day_to_date(raw)
        if typeinfo.family == "time":
            return seconds_to_time(raw)
        if typeinfo.family in {"datetime", "timestamp"}:
            return epoch_second_to_datetime(raw)
        return raw

    def _raw_payload_to_python(self, payload: z3.ExprRef) -> Any:
        """Convert a raw Z3 payload expression to a Python value.

        Supports integer, rational, string, boolean, and falls back to
        string representation for unrecognized sorts.
        """
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
        """Convert a Z3 expression to a Python value, handling Option wrappers.

        If the value is an Option datatype, decodes the NULL/Some distinction.
        Otherwise decodes the raw payload.
        """
        if isinstance(value.sort(), z3.DatatypeSortRef) and OptionTypeRegistry.is_option_sort(
            value.sort()
        ):
            return self._decode_option_value(value, var_name)
        return self._decode_payload(value, var_name)
