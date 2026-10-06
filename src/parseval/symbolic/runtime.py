"""Own input bindings and execute typed operations while constructing Terms."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from types import MappingProxyType
from typing import ClassVar, TypeAlias

from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder
from parseval.terms.context import (
    Context,
    ParameterSpec,
    ScalarFunctionSpec,
    Volatility,
)
from parseval.terms.names import FunctionId, ParameterId
from parseval.terms.sorts import ScalarSort, TypeKind

from .base import ZValue
from .boolean import ZBool
from .expression import ZExpr
from .functions import SQL_FUNCTIONS
from .numeric import ZFloat, ZInt
from .operations import (
    ARITHMETIC,
    COMPARISONS,
    NUMERIC,
    execute,
    promoted_type,
    result_sort,
)
from .semantics import Semantics, infer_sort, validate
from .string import ZString
from .temporal import ZDate, ZInterval, ZTime, ZTimestamp

ScalarFunction: TypeAlias = Callable[[tuple[object, ...]], object]


def strict(function: ScalarFunction) -> ScalarFunction:
    """Propagate SQL NULL before calling a function on concrete arguments.

    Unwrapped callbacks receive None and can implement their own NULL behavior.
    A callback must be deterministic and must not mutate its arguments.
    """

    @wraps(function)
    def evaluate(arguments: tuple[object, ...]) -> object:
        if any(argument is None for argument in arguments):
            return None
        return function(arguments)

    return evaluate


@dataclass(frozen=True, slots=True)
class Input:
    name: str
    sort: ScalarSort


_VALUE_TYPES = {
    TypeKind.INTEGER: ZInt,
    TypeKind.FLOAT: ZFloat,
    TypeKind.DECIMAL: ZFloat,
    TypeKind.BOOLEAN: ZBool,
    TypeKind.STRING: ZString,
    TypeKind.DATE: ZDate,
    TypeKind.TIME: ZTime,
    TypeKind.TIMESTAMP: ZTimestamp,
    TypeKind.INTERVAL: ZInterval,
}


class Runtime:
    """Typed scalar execution with user-extensible concrete function callbacks.

    Subclasses extend SCALAR_FUNCTIONS using dictionary unpacking. FunctionId
    entries take precedence over operator-name entries. Callbacks receive a
    tuple of concrete arguments; wrap one with strict() for NULL propagation.
    SQL predicate nodes retain their fixed three-valued semantics.
    """

    SCALAR_FUNCTIONS: ClassVar[dict[FunctionId | str, ScalarFunction]] = {
        "lower": strict(lambda args: args[0].lower()),
        "upper": strict(lambda args: args[0].upper()),
        "length": strict(lambda args: len(args[0])),
        "concat": strict(lambda args: args[0] + args[1]),
        "contains": strict(lambda args: args[1] in args[0]),
        "startswith": strict(lambda args: args[0].startswith(args[1])),
        "endswith": strict(lambda args: args[0].endswith(args[1])),
        **SQL_FUNCTIONS,
    }

    def __init__(self, arena=None, *, semantics=None):
        self.arena = arena if arena is not None else TermArena(Context())
        self.builder = IRBuilder(self.arena)
        self.semantics = Semantics() if semantics is None else semantics
        self._inputs = {}
        self._names = {}
        self._assignments = {}

    @property
    def inputs(self):
        return MappingProxyType(self._inputs)

    @property
    def assignments(self):
        return MappingProxyType(self._assignments)

    def _value(self, term, concrete):
        expression = ZExpr(self.arena, self.builder.resolve(term), self)
        return _VALUE_TYPES[expression.sort.sql_type.kind](expression, concrete)

    def input(self, name, concrete, sort=None):
        if not isinstance(name, str) or not name:
            raise ValueError("Inputs need a nonempty name")
        if name in self._names:
            raise ValueError(f"Input already declared: {name}")
        sort = infer_sort(concrete) if sort is None else sort
        validate(concrete, sort)
        parameter = self.arena.context.allocate_id(ParameterId)
        self.arena.context.register_parameter(parameter, ParameterSpec(sort))
        self._inputs[parameter] = Input(name, sort)
        self._names[name] = parameter
        self._assignments[parameter] = concrete
        return self._value(self.builder.external_parameter(parameter), concrete)

    def literal(self, concrete, sort=None):
        sort = infer_sort(concrete) if sort is None else sort
        validate(concrete, sort)
        return self._value(self.builder.literal(concrete, sort.sql_type), concrete)

    def coerce(self, value, *, hint=None):
        if isinstance(value, ZValue):
            if value.runtime is not self:
                raise ValueError("Cannot mix input assignments from different runtimes")
            return value
        sort = ScalarSort(hint, True) if value is None and hint is not None else None
        return self.literal(value, sort)

    def scalar(self, identity, specification, arguments):
        """Execute a typed scalar call identically during construction and replay."""
        # STABLE functions are constant within one query; the registry fixes
        # their value (for example the current time) so replay is repeatable.
        if specification.volatility is Volatility.VOLATILE:
            raise ValueError("Concolic scalar callbacks cannot be volatile")
        # Terms enforce the signature; inputs and child results are checked at
        # their boundaries. Only the newly computed result needs validation.
        for key in (identity, specification.operator):
            if key in self.SCALAR_FUNCTIONS:
                result = self.SCALAR_FUNCTIONS[key](arguments)
                return validate(result, specification.result)
        if specification.operator is None:
            raise NotImplementedError(
                f"No concrete callback registered for {identity!r}"
            )
        return validate(
            execute(
                specification.operator,
                arguments,
                specification.parameters,
                specification.result,
                self.semantics,
            ),
            specification.result,
        )

    def _scalar_value(self, term, arguments):
        term = self.builder.resolve(term)
        identity = self.arena[term].payload.function
        specification = self.arena.context.function(identity)
        concrete = self.scalar(
            identity, specification, tuple(value.concrete for value in arguments)
        )
        return self._value(term, concrete)

    def call(
        self, function: str | FunctionId, *arguments, result: ScalarSort | None = None
    ):
        """Execute a registered callback and retain its typed ScalarCall Term.

        Named calls require an explicit result sort. FunctionId calls use the
        signature already registered in the arena's Context. Type information
        never depends on the result of a particular concrete execution.
        """
        values = tuple(self.coerce(value) for value in arguments)
        if isinstance(function, str):
            if not function:
                raise ValueError("Function names must be nonempty")
            if not isinstance(result, ScalarSort):
                raise TypeError("Named functions require an explicit result ScalarSort")
            function = self.arena.context.intern_function(
                ScalarFunctionSpec(
                    tuple(value.sort for value in values),
                    result,
                    operator=function,
                )
            )
        elif isinstance(function, FunctionId):
            if result is not None:
                raise TypeError("FunctionId calls use their registered result sort")
        else:
            raise TypeError("Expected a function name or FunctionId")
        term = self.builder.scalar_call(
            function, (value.expression.root for value in values)
        )
        return self._scalar_value(term, values)

    def cast(self, value, sql_type):
        value = self.coerce(value)
        sql_type = sql_type.value_type
        operator = f"cast_{value.sort.sql_type.kind.value}_to_{sql_type.kind.value}"
        term = self.builder.apply(
            operator,
            (value.expression.root,),
            ScalarSort(sql_type, value.sort.nullable),
        )
        return self._scalar_value(term, (value,))

    def apply(self, operation, *arguments):
        values = tuple(self.coerce(value) for value in arguments)
        sorts = tuple(value.sort for value in values)
        result = result_sort(operation, sorts)
        # Promotions are explicit cast Terms, shared by execution and replay.
        if (
            len(values) == 2
            and operation in ARITHMETIC | COMPARISONS
            and all(sort.sql_type.kind in NUMERIC for sort in sorts)
        ):
            common = promoted_type(*(sort.sql_type for sort in sorts))
            values = tuple(
                self.cast(value, common) if value.sort.sql_type != common else value
                for value in values
            )
            sorts = tuple(value.sort for value in values)
        children = tuple(value.expression.root for value in values)
        b = self.builder
        predicate = None
        if operation in {"eq", "ne", "lt", "le", "gt", "ge"}:
            left, right = children
            if operation in {"eq", "ne"}:
                predicate = b.eq3(left, right)
            elif operation in {"lt", "ge"}:
                predicate = b.lt3(left, right)
            else:
                predicate = b.lt3(right, left)
            if operation in {"ne", "le", "ge"}:
                predicate = b.not3(predicate)
        elif operation in {
            "is_null",
            "is_not_null",
            "is_not_distinct",
            "like",
            "ilike",
        }:
            method = {"like": b.like3, "ilike": b.ilike3}.get(operation)
            predicate = (method if method is not None else getattr(b, operation))(
                *children
            )
        elif operation in {"and", "or", "not"}:
            predicate = getattr(b, operation + "3")(
                *(b.to_predicate(child) for child in children)
            )
        if predicate is not None:
            term = b.to_boolean(predicate)
            concrete = execute(
                operation,
                tuple(value.concrete for value in values),
                sorts,
                result,
                self.semantics,
            )
            return self._value(term, concrete)
        term = b.apply(operation, children, result)
        return self._scalar_value(term, values)

    def evaluate(self, term, assignments=None, cache=None):
        """Evaluate an existing scalar Term in this runtime's input context."""
        return ZExpr(self.arena, term, self).evaluate(assignments, cache)

    def observe(self, term, assignments=None):
        """Pair an existing scalar Term with its independently computed result."""
        return self._value(term, self.evaluate(term, assignments))
