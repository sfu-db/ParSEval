"""Concrete, solver-independent evaluation of checked U-expressions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from functools import cmp_to_key, wraps
from itertools import product
import math
import operator
import re
from typing import ClassVar, Generic, TypeVar, cast

from parseval.instance import Instance, Row, RowValue, ScalarValue
from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder
from parseval.terms.constraints import (
    CheckDecl,
    ForeignKeyDecl,
    ForeignKeyMatch,
    GeneratedColumnDecl,
    NotNullDecl,
    NullConflictPolicy,
    PrimaryKeyDecl,
    UniqueDecl,
)
from parseval.terms.context import AggregateSpec, ScalarFunctionSpec
from parseval.terms.names import (
    AggregateSpecId,
    FunctionId,
    ParameterId,
    SchemaId,
)
from parseval.terms.sorts import BagSort, RowFunctionSort, RowSort, SeqSort
from parseval.terms.terms import (
    AggregateMode,
    BaseRelationPayload,
    FieldPayload,
    FoldPayload,
    LiteralPayload,
    OrderPayload,
    ScalarCallPayload,
    TermId,
    VariablePayload,
)
from .witness import UnitWitnessPlan, WitnessPlan, ScanVariable, plan_product
from .espnf import inspect_bag_espnf
from .normalize import to_espnf
from .observation import (
    BagCardinalityCondition,
    GroupCardinalityCondition,
    Condition,
    NullCondition,
    PredicateCondition,
    WeightCondition,
)


class TruthValue(Enum):
    FALSE = 0
    TRUE = 1
    UNKNOWN = -1

    @classmethod
    def from_boolean(cls, value: object | None) -> TruthValue:
        if value is None:
            return cls.UNKNOWN
        return cls.TRUE if bool(value) else cls.FALSE


_RowT = TypeVar("_RowT")
_MultiplicityT = TypeVar("_MultiplicityT")


@dataclass(frozen=True, slots=True)
class BagEntry(Generic[_RowT, _MultiplicityT]):
    """A semantic row and its multiplicity in any U-semiring carrier."""

    row: _RowT
    multiplicity: _MultiplicityT

    @property
    def active(self):
        return self.multiplicity > 0


@dataclass(frozen=True, slots=True)
class BagValue:
    schema: SchemaId
    entries: tuple[BagEntry[Row, int], ...] = ()

    def __post_init__(self) -> None:
        if any(entry.row.schema != self.schema for entry in self.entries):
            raise ValueError(f"Bag rows do not match {self.schema!r}")

    @classmethod
    def from_rows(cls, schema: SchemaId, rows: Iterable[Row]) -> BagValue:
        result = cls(schema)
        for row in rows:
            result = result.add(row, 1)
        return result

    def add(self, row: Row, multiplicity: int) -> BagValue:
        if multiplicity == 0:
            return self
        entries = list(self.entries)
        for index, entry in enumerate(entries):
            if row_identity_equal(entry.row.values, row.values):
                total = entry.multiplicity + multiplicity
                if total:
                    entries[index] = BagEntry(entry.row, total)
                else:
                    entries.pop(index)
                return BagValue(self.schema, tuple(entries))
        entries.append(BagEntry(row, multiplicity))
        return BagValue(self.schema, tuple(entries))

    def multiplicity(self, row: RowValue) -> int:
        return sum(
            entry.multiplicity
            for entry in self.entries
            if row_identity_equal(entry.row.values, row)
        )

    def expanded_rows(self) -> tuple[Row, ...]:
        return tuple(
            entry.row
            for entry in self.entries
            for _ in range(entry.multiplicity)
        )


@dataclass(frozen=True, slots=True)
class SequenceValue:
    schema: SchemaId
    rows: tuple[Row, ...]

    def __post_init__(self) -> None:
        if any(row.schema != self.schema for row in self.rows):
            raise ValueError(f"Sequence rows do not match {self.schema!r}")


def row_identity_equal(left: RowValue, right: RowValue) -> bool:
    return len(left) == len(right) and all(a == b for a, b in zip(left, right, strict=True))


ScalarFunction = Callable[[tuple[ScalarValue, ...]], ScalarValue]
ScalarArgument = Callable[[], ScalarValue]
LazyScalarFunction = Callable[[tuple[ScalarArgument, ...]], ScalarValue]
AggregateFunction = Callable[[AggregateSpec, tuple[ScalarValue, ...]], ScalarValue]


class EvaluationError(RuntimeError):
    """A term has no supported concrete interpretation."""


def strict(function: ScalarFunction) -> ScalarFunction:
    """Adapt a callback to return SQL NULL if any argument is NULL."""

    @wraps(function)
    def evaluate(arguments: tuple[ScalarValue, ...]) -> ScalarValue:
        if any(value is None for value in arguments):
            return None
        return function(arguments)

    return evaluate


def _coalesce(arguments: tuple[ScalarArgument, ...]) -> ScalarValue:
    for argument in arguments:
        value = argument()
        if value is not None:
            return value
    return None


def _substring(arguments):
    start = int(arguments[1]) - 1
    value = str(arguments[0])
    return value[start:] if len(arguments) == 2 else value[start : start + int(arguments[2])]


def _timestamp(value):
    return datetime.combine(value, time()) if isinstance(value, date) and not isinstance(value, datetime) else value


def _age(arguments):
    value = arguments[0]
    today = date.today()
    return today.year - value.year - ((today.month, today.day) < (value.month, value.day))


def _scalar_family(name: str | None) -> ScalarFunction | None:
    if name is None:
        return None
    if name.startswith("cast_") and "_to_" in name:
        _, target = name.removeprefix("cast_").split("_to_", 1)
        conversion = _CASTS.get(target)
        if conversion is not None:
            return strict(lambda arguments: conversion(arguments[0]))
    if name.startswith("extract_"):
        field = name.removeprefix("extract_")
        return strict(lambda arguments: getattr(arguments[0], field))
    return None


def _count(spec: AggregateSpec, values: tuple[ScalarValue, ...]) -> int:
    return len(values) if spec.input is None else sum(value is not None for value in values)


def _non_null_aggregate(function: Callable) -> AggregateFunction:
    def evaluate(spec: AggregateSpec, values: tuple[ScalarValue, ...]) -> ScalarValue:
        non_null = tuple(value for value in values if value is not None)
        return function(non_null) if non_null else None

    return evaluate


def _stddev_sample(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


_CASTS = {
    "date": lambda value: date.fromisoformat(str(value)),
    "boolean": bool,
    "integer": int,
    "float": float,
    "decimal": Decimal,
    "string": str,
}


@dataclass(frozen=True, slots=True)
class _Environment:
    rows: tuple[Row, ...] = ()
    relations: tuple[BagValue | SequenceValue, ...] = ()

    def bind_row(self, row: Row) -> _Environment:
        return _Environment((row, *self.rows), self.relations)

    def bind_relation(self, relation: BagValue | SequenceValue) -> _Environment:
        return _Environment(self.rows, (relation, *self.relations))


class UExprEvaluator:
    """Evaluate U-expression terms over one finite catalog instance.

    Override callback dictionaries in a subclass, copying inherited entries with
    dictionary unpacking when retaining the builtin implementations.
    """

    SCALAR_FUNCTIONS: ClassVar[dict[FunctionId | str, ScalarFunction]] = {
        name: strict(function)
        for name, function in {
            "add": lambda args: operator.add(*args),
            "sub": lambda args: operator.sub(*args),
            "mul": lambda args: operator.mul(*args),
            "div": lambda args: operator.truediv(*args),
            "mod": lambda args: operator.mod(*args),
            "neg": lambda args: operator.neg(*args),
            "abs": lambda args: abs(args[0]),
            "lower": lambda args: str(args[0]).lower(),
            "upper": lambda args: str(args[0]).upper(),
            "length": lambda args: len(args[0]),
            "substring": _substring,
            "round": lambda args: round(args[0], int(args[1]) if len(args) == 2 else 0),
            "date": lambda args: args[0].date() if isinstance(args[0], datetime) else args[0],
            "year": lambda args: args[0].year,
            "datediff": lambda args: (args[0] - args[1]).days,
            "instr": lambda args: str(args[0]).find(str(args[1])) + 1,
            "time": lambda args: args[0].time() if isinstance(args[0], datetime) else args[0],
            "julianday": lambda args: _timestamp(args[0]).timestamp() / 86400 + 2440587.5,
            "ts_or_ds_to_timestamp": lambda args: _timestamp(args[0]),
            "age": _age,
            "date_part": lambda args: getattr(args[1], str(args[0]).casefold()),
        }.items()
    }
    SCALAR_FUNCTIONS["nullif"] = lambda args: None if args[0] is not None and args[0] == args[1] else args[0]

    LAZY_SCALAR_FUNCTIONS: ClassVar[dict[FunctionId | str, LazyScalarFunction]] = {
        "coalesce": _coalesce,
    }

    AGGREGATE_FUNCTIONS: ClassVar[dict[AggregateSpecId | str, AggregateFunction]] = {
        "count": _count,
        "sum": _non_null_aggregate(sum),
        "avg": _non_null_aggregate(lambda values: sum(values) / len(values)),
        "min": _non_null_aggregate(min),
        "max": _non_null_aggregate(max),
        "stddev_samp": _non_null_aggregate(_stddev_sample),
        "stddev": _non_null_aggregate(_stddev_sample),
        "stddev_pop": _non_null_aggregate(lambda values: math.sqrt(
            sum((value - sum(values) / len(values)) ** 2 for value in values) / len(values)
        )),
        "__sqlite_arbitrary_value": _non_null_aggregate(lambda values: values[0]),
    }

    def __init__(
        self,
        arena: TermArena,
        instance: Instance,
        *,
        parameters: Mapping[ParameterId, object | None] | None = None,
    ) -> None:
        if arena.context is not instance.catalog.context:
            raise ValueError("The arena and instance must share one Context")
        self.arena = arena
        self.instance = instance
        self.parameters = parameters or {}

    @classmethod
    def scalar(
        cls,
        identity: FunctionId,
        spec: ScalarFunctionSpec,
        arguments: tuple[ScalarArgument, ...],
    ) -> ScalarValue:
        for key in (identity, spec.operator):
            if key in cls.SCALAR_FUNCTIONS:
                return cls.SCALAR_FUNCTIONS[key](tuple(argument() for argument in arguments))
            if key in cls.LAZY_SCALAR_FUNCTIONS:
                return cls.LAZY_SCALAR_FUNCTIONS[key](arguments)
        function = _scalar_family(spec.operator)
        if function is None:
            raise EvaluationError(
                f"Unsupported scalar function {identity!r} (operator={spec.operator!r})"
            )
        return function(tuple(argument() for argument in arguments))

    @classmethod
    def aggregate(
        cls,
        identity: AggregateSpecId,
        spec: AggregateSpec,
        values: tuple[ScalarValue, ...],
    ) -> ScalarValue:
        for key in (identity, spec.operator, spec.kind):
            if key in cls.AGGREGATE_FUNCTIONS:
                return cls.AGGREGATE_FUNCTIONS[key](spec, values)
        raise EvaluationError(
            f"Unsupported aggregate {identity!r} (operator={spec.operator!r})"
        )

    def evaluate_query(self, root: TermId) -> BagValue | SequenceValue:
        """Return the concrete relation produced by a bag or sequence root."""
        node = self.arena[root]
        if isinstance(node.sort, BagSort):
            return self._bag(root, _Environment())
        return self._sequence(root, _Environment())

    def observed_choices(
        self,
        plan: WitnessPlan,
        choices: tuple[tuple[Condition, ...], ...],
        relations: tuple[TermId, ...] = (),
        contexts: tuple[WitnessPlan, ...] = (),
    ) -> tuple[tuple[int, ...], ...]:
        """Return choice vectors witnessed by concrete rows of a product."""

        observed: dict[tuple[int, ...], None] = {}
        for environment in self._context_environments(relations, contexts):
            for _output, bound in self._bound_plan_environments(plan, environment):
                matched = tuple(
                    tuple(index for index, choice in enumerate(group)
                          if self._condition(choice, bound))
                    for group in choices
                )
                for vector in product(*matched):
                    observed[vector] = None
        return tuple(observed)

    def witnessed_conditions(
        self,
        plan: WitnessPlan | UnitWitnessPlan,
        conditions: tuple[Condition, ...],
        relations: tuple[TermId, ...] = (),
        contexts: tuple[WitnessPlan, ...] = (),
    ) -> bool:
        """Whether one finite witness satisfies every local weight condition."""

        for environment in self._context_environments(relations, contexts):
            if isinstance(plan, UnitWitnessPlan):
                if all(self._condition(condition, environment) for condition in conditions):
                    return True
            elif any(
                all(self._condition(condition, bound) for condition in conditions)
                for _output, bound in self._bound_plan_environments(plan, environment)
            ):
                return True
        return False

    def _context_environments(
        self, relations: tuple[TermId, ...], contexts: tuple[WitnessPlan, ...]
    ):
        environments = (self._relation_environment(relations),)
        for plan in contexts:
            environments = tuple(
                bound
                for environment in environments
                for _output, bound in self._bound_plan_environments(plan, environment)
            )
        return environments

    def _condition(self, condition: Condition, environment: _Environment) -> bool:
        if isinstance(condition, GroupCardinalityCondition):
            fold = self.arena[condition.term]
            if not isinstance(fold, (nodes.GroupFold, nodes.GlobalFold)):
                raise TypeError("Group cardinality requires a fold")
            source = self._bag(fold.children[0], environment)
            groups: list[tuple[RowValue, int]] = []
            if isinstance(fold, nodes.GlobalFold):
                groups.append(((), sum(entry.multiplicity for entry in source.entries)))
            else:
                for entry in source.entries:
                    key = cast(Row, self._apply(fold.children[1], entry.row, environment)).values
                    index = next((i for i, (known, _) in enumerate(groups)
                                  if row_identity_equal(known, key)), None)
                    if index is None:
                        groups.append((key, entry.multiplicity))
                    else:
                        groups[index] = (key, groups[index][1] + entry.multiplicity)
            return any(
                count >= condition.minimum
                and (condition.maximum is None or count <= condition.maximum)
                for _, count in groups
            )
        if isinstance(condition, WeightCondition):
            value = self._weight(condition.term, environment)
            return value >= condition.minimum and (
                condition.maximum is None or value <= condition.maximum
            )
        if isinstance(condition, PredicateCondition):
            return self._predicate(condition.term, environment).value == condition.truth.value
        if isinstance(condition, NullCondition):
            return (self._value(condition.term, environment) is None) is condition.is_null
        if isinstance(condition, BagCardinalityCondition):
            bag = self._bag(condition.term, environment)
            count = sum(entry.multiplicity for entry in bag.entries)
            return count >= condition.minimum and (
                condition.maximum is None or count <= condition.maximum
            )
        raise TypeError(f"Unsupported observation {type(condition).__name__}")

    def _relation_environment(self, definitions: tuple[TermId, ...]) -> _Environment:
        environment = _Environment()
        for definition in reversed(definitions):
            environment = environment.bind_relation(self._relation(definition, environment))
        return environment

    def value(self, term: TermId, rows: tuple[Row, ...] = ()) -> object | None | Row:
        return self._value(term, _Environment(rows))

    def predicate(self, term: TermId, rows: tuple[Row, ...] = ()) -> TruthValue:
        return self._predicate(term, _Environment(rows))

    def weight(self, term: TermId, rows: tuple[Row, ...] = ()) -> int:
        return self._weight(term, _Environment(rows))

    def apply(self, function: TermId, row: Row):
        return self._apply(function, row, _Environment())

    def _value(self, term: TermId, environment: _Environment) -> object | None | Row:
        node = self.arena[term]
        if isinstance(node, nodes.Literal):
            return cast(LiteralPayload, node.payload).value
        if isinstance(node, nodes.Null):
            return None
        if isinstance(node, nodes.RowVar):
            return environment.rows[cast(VariablePayload, node.payload).depth]
        if isinstance(node, nodes.Field):
            row = cast(Row, self._value(node.children[0], environment))
            return row.values[cast(FieldPayload, node.payload).index]
        if isinstance(node, nodes.ExternalParameter):
            return self.parameters[node.payload.parameter]
        if isinstance(node, nodes.Row):
            return Row(
                cast(RowSort, node.sort).schema,
                tuple(self._value(child, environment) for child in node.children),
            )
        if isinstance(node, nodes.ScalarCall):
            payload = cast(ScalarCallPayload, node.payload)
            return self.scalar(
                payload.function,
                self.arena.context.function(payload.function),
                tuple(
                    self._argument(child, environment)
                    for child in node.children
                ),
            )
        if isinstance(node, nodes.Case):
            condition, then, otherwise = node.children
            selected = then if self._predicate(condition, environment) is TruthValue.TRUE else otherwise
            return self._value(selected, environment)
        if isinstance(node, nodes.ToBoolean):
            predicate = self._predicate(node.children[0], environment)
            return None if predicate is TruthValue.UNKNOWN else predicate is TruthValue.TRUE
        if isinstance(node, nodes.Scalarize):
            bag = self._bag(node.children[0], environment)
            rows = bag.expanded_rows()
            if not rows:
                return None
            if len(rows) != 1:
                raise EvaluationError("Scalar subquery produced more than one row")
            return rows[0].values[0]
        if isinstance(node, nodes.Fold):
            return self._fold(node.children[0], node.payload.aggregate, environment)
        raise EvaluationError(f"Unsupported value node {type(node).key}")

    def _argument(self, term: TermId, environment: _Environment) -> ScalarArgument:
        evaluated = False
        value = None

        def evaluate():
            nonlocal evaluated, value
            if not evaluated:
                value = self._value(term, environment)
                evaluated = True
            return value

        return evaluate

    def _predicate(self, term: TermId, environment: _Environment) -> TruthValue:
        node = self.arena[term]
        if isinstance(node, nodes.True3):
            return TruthValue.TRUE
        if isinstance(node, nodes.False3):
            return TruthValue.FALSE
        if isinstance(node, nodes.Unknown3):
            return TruthValue.UNKNOWN
        if isinstance(node, nodes.ToPredicate):
            return TruthValue.from_boolean(self._value(node.children[0], environment))
        if isinstance(node, nodes.Eq3):
            return _nullable_compare(
                self._value(node.children[0], environment),
                self._value(node.children[1], environment),
                lambda left, right: left == right,
            )
        if isinstance(node, nodes.Lt3):
            return _nullable_compare(
                self._value(node.children[0], environment),
                self._value(node.children[1], environment),
                lambda left, right: left < right,
            )
        if isinstance(node, (nodes.Like3, nodes.ILike3)):
            value = self._value(node.children[0], environment)
            pattern = self._value(node.children[1], environment)
            if value is None or pattern is None:
                return TruthValue.UNKNOWN
            flags = re.DOTALL | (re.IGNORECASE if isinstance(node, nodes.ILike3) else 0)
            return TruthValue.TRUE if re.fullmatch(_like_pattern(str(pattern)), str(value), flags) else TruthValue.FALSE
        if isinstance(node, nodes.IsNull):
            return TruthValue.TRUE if self._value(node.children[0], environment) is None else TruthValue.FALSE
        if isinstance(node, nodes.IsNotNull):
            return TruthValue.FALSE if self._value(node.children[0], environment) is None else TruthValue.TRUE
        if isinstance(node, nodes.IsNotDistinct):
            left = self._value(node.children[0], environment)
            right = self._value(node.children[1], environment)
            return TruthValue.TRUE if left == right else TruthValue.FALSE
        if isinstance(node, nodes.And3):
            return _and3(*(self._predicate(child, environment) for child in node.children))
        if isinstance(node, nodes.Or3):
            return _or3(*(self._predicate(child, environment) for child in node.children))
        if isinstance(node, nodes.Not3):
            value = self._predicate(node.children[0], environment)
            return value if value is TruthValue.UNKNOWN else (
                TruthValue.FALSE if value is TruthValue.TRUE else TruthValue.TRUE
            )
        if isinstance(node, nodes.RowIdentityEq):
            left = cast(Row, self._value(node.children[0], environment))
            right = cast(Row, self._value(node.children[1], environment))
            return TruthValue.TRUE if row_identity_equal(left.values, right.values) else TruthValue.FALSE
        if isinstance(node, nodes.InSubquery):
            needle = self._value(node.children[0], environment)
            candidates = self._bag(node.children[1], environment).expanded_rows()
            if needle is None:
                return TruthValue.UNKNOWN
            if any(row.values[0] == needle for row in candidates if row.values[0] is not None):
                return TruthValue.TRUE
            return TruthValue.UNKNOWN if any(row.values[0] is None for row in candidates) else TruthValue.FALSE
        raise EvaluationError(f"Unsupported predicate node {type(node).key}")

    def _weight(self, term: TermId, environment: _Environment) -> int:
        node = self.arena[term]
        if isinstance(node, nodes.Zero):
            return 0
        if isinstance(node, nodes.One):
            return 1
        if isinstance(node, nodes.Add):
            return sum(self._weight(child, environment) for child in node.children)
        if isinstance(node, nodes.Mul):
            result = 1
            for child in node.children:
                result *= self._weight(child, environment)
            return result
        if isinstance(node, nodes.Squash):
            return int(self._weight(node.children[0], environment) > 0)
        if isinstance(node, nodes.UNot):
            return int(self._weight(node.children[0], environment) == 0)
        if isinstance(node, nodes.Indicator):
            return int(self._predicate(node.children[0], environment) is TruthValue.TRUE)
        if isinstance(node, nodes.At):
            row = cast(Row, self._value(node.children[1], environment))
            return self._bag(node.children[0], environment).multiplicity(row.values)
        if isinstance(node, nodes.Sum):
            builder = IRBuilder(self.arena)
            bag = builder.resolve(
                builder.checked(nodes.BagLambda, (node.children[0],))
            )
            return sum(
                entry.multiplicity
                for entry in self._bag(bag, environment).entries
            )
        raise EvaluationError(f"Unsupported multiplicity node {type(node).key}")

    def _bag(self, term: TermId, environment: _Environment) -> BagValue:
        node = self.arena[term]
        if isinstance(node, nodes.LetRel):
            definition, body = node.children
            value = self._relation(definition, environment)
            return self._bag(body, environment.bind_relation(value))
        if isinstance(node, nodes.Base):
            relation = cast(BaseRelationPayload, node.payload).relation
            schema = cast(BagSort, node.sort).schema
            return BagValue.from_rows(schema, self.instance.rows(relation))
        if isinstance(node, nodes.BagLambda):
            return self._evaluate_espnf_bag(term, environment)
        if isinstance(node, (nodes.GlobalFold, nodes.GroupFold)):
            return self._aggregate_bag(term, environment)
        if isinstance(node, nodes.ForgetOrder):
            sequence = self._sequence(node.children[0], environment)
            return BagValue.from_rows(sequence.schema, sequence.rows)
        if isinstance(node, nodes.RelVar):
            return cast(BagValue, environment.relations[cast(VariablePayload, node.payload).depth])
        raise EvaluationError(f"Unsupported bag node {type(node).key}")

    def _sequence(self, term: TermId, environment: _Environment) -> SequenceValue:
        node = self.arena[term]
        if isinstance(node, nodes.LetRel):
            definition, body = node.children
            value = self._relation(definition, environment)
            return self._sequence(body, environment.bind_relation(value))
        if isinstance(node, nodes.OrderBy):
            bag = self._bag(node.children[0], environment)
            payload = cast(OrderPayload, node.payload)
            key_functions = node.children[1:]

            def compare(left: Row, right: Row) -> int:
                for function, specification in zip(key_functions, payload.keys, strict=True):
                    left_key = self._apply(function, left, environment)
                    right_key = self._apply(function, right, environment)
                    result = _compare_order_values(left_key, right_key, specification.nulls.value)
                    if result:
                        if left_key is not None and right_key is not None:
                            return -result if specification.direction.value == "desc" else result
                        return result
                return 0

            return SequenceValue(bag.schema, tuple(sorted(bag.expanded_rows(), key=cmp_to_key(compare))))
        if isinstance(node, nodes.SeqMap):
            sequence = self._sequence(node.children[0], environment)
            rows = tuple(cast(Row, self._apply(node.children[1], row, environment)) for row in sequence.rows)
            return SequenceValue(cast(SeqSort, node.sort).schema, rows)
        if isinstance(node, (nodes.Take, nodes.Drop)):
            count = int(cast(object, self._value(node.children[0], environment)))
            sequence = self._sequence(node.children[1], environment)
            rows = sequence.rows[:count] if isinstance(node, nodes.Take) else sequence.rows[count:]
            return SequenceValue(sequence.schema, rows)
        if isinstance(node, nodes.Slice):
            offset = int(cast(object, self._value(node.children[0], environment)))
            count = int(cast(object, self._value(node.children[1], environment)))
            sequence = self._sequence(node.children[2], environment)
            return SequenceValue(sequence.schema, sequence.rows[offset : offset + count])
        if isinstance(node, nodes.RelVar):
            return cast(SequenceValue, environment.relations[cast(VariablePayload, node.payload).depth])
        raise EvaluationError(f"Unsupported sequence node {type(node).key}")

    def _relation(
        self, term: TermId, environment: _Environment
    ) -> BagValue | SequenceValue:
        if isinstance(self.arena[term].sort, BagSort):
            return self._bag(term, environment)
        return self._sequence(term, environment)

    def _apply(self, function: TermId, row: Row, environment: _Environment):
        node = self.arena[function]
        body = node.children[0]
        sort = self.arena[body].sort
        bound = environment.bind_row(row)
        if isinstance(sort, RowSort):
            return self._value(body, bound)
        if isinstance(sort, RowFunctionSort):
            raise EvaluationError("Higher-order row lambdas are unsupported")
        if isinstance(self.arena[body], nodes.Predicate):
            return self._predicate(body, bound)
        return self._value(body, bound)

    def _evaluate_espnf_bag(
        self, term: TermId, environment: _Environment
    ) -> BagValue:
        view = inspect_bag_espnf(self.arena, to_espnf(self.arena, term))
        result = BagValue(view.schema)
        for product in view.alternatives:
            plan = plan_product(self.arena, view.schema, product)
            contribution, _ = self._evaluate_product(plan, environment)
            for entry in contribution.entries:
                result = result.add(entry.row, entry.multiplicity)
        return result

    def _evaluate_product(
        self,
        plan: WitnessPlan,
        environment: _Environment,
    ) -> tuple[BagValue, int]:
        product = plan.product
        result = BagValue(plan.schema)
        total = 0
        for output, bound in self._bound_plan_environments(plan, environment):
            weight = 1
            for factor in product.factors:
                weight *= self._weight(factor, bound)
            if weight:
                result = result.add(output, weight)
                total += weight
        return result, total

    def _bound_plan_environments(
        self,
        plan: WitnessPlan,
        environment: _Environment,
    ):
        yielded: list[tuple[Row, ...]] = []

        def execute(
            steps,
            position: int,
            assigned: dict[int, Row],
        ):
            if position == len(steps):
                signature = tuple(
                    assigned[index]
                    for index in range(plan.output_variable + 1)
                )
                if any(
                    all(row_identity_equal(a.values, b.values) for a, b in zip(signature, known, strict=True))
                    for known in yielded
                ):
                    return
                yielded.append(signature)
                output = assigned[plan.output_variable]
                rows = tuple(
                    assigned[index]
                    for index in reversed(range(plan.output_variable))
                )
                yield output, _Environment(
                    (*rows, output, *environment.rows),
                    environment.relations,
                )
                return

            step = steps[position]
            scoped = self._scope_environment(plan, step.scope, assigned, environment)
            if isinstance(step, ScanVariable):
                rows = _unique_rows(entry.row for entry in self._bag(step.source, scoped).entries)
            else:
                rows = (cast(Row, self._value(step.expression, scoped)),)
            for row in rows:
                assigned[step.variable] = row
                yield from execute(steps, position + 1, assigned)
                del assigned[step.variable]

        for binding in plan.bindings:
            yield from execute(binding.steps, 0, {})

    def _scope_environment(
        self,
        plan: WitnessPlan,
        scope: tuple[int, ...],
        assigned: dict[int, Row],
        outer: _Environment,
    ) -> _Environment:
        return _Environment(
            tuple(
                assigned.get(variable, self._placeholder(plan.variables[variable]))
                for variable in scope
            )
            + outer.rows,
            outer.relations,
        )

    def _placeholder(self, schema: SchemaId) -> Row:
        return Row(
            schema,
            tuple(None for _ in self.arena.context.schema(schema).fields),
        )

    def _fold(self, source: TermId, aggregate, environment: _Environment):
        relation = self._bag(source, environment) if isinstance(self.arena[source].sort, BagSort) else self._sequence(source, environment)
        rows = relation.expanded_rows() if isinstance(relation, BagValue) else relation.rows
        values = tuple(row.values[0] for row in rows)
        return self.aggregate(aggregate, self.arena.context.aggregate(aggregate), values)

    def _aggregate_bag(self, term: TermId, environment: _Environment) -> BagValue:
        node = self.arena[term]
        payload = cast(FoldPayload, node.payload)
        source = self._bag(node.children[0], environment)
        rows = source.expanded_rows()
        groups: list[tuple[RowValue, list[Row]]] = []
        if isinstance(node, nodes.GroupFold):
            key_function = node.children[1]
            for row in rows:
                key = cast(Row, self._apply(key_function, row, environment)).values
                group = next((item for item in groups if row_identity_equal(item[0], key)), None)
                if group is None:
                    groups.append((key, [row]))
                else:
                    group[1].append(row)
        else:
            groups.append(((), list(rows)))
        result = BagValue(payload.output_schema)
        for key, group_rows in groups:
            output = list(key)
            for layout in payload.calls:
                admitted = group_rows
                if layout.filter_child is not None:
                    function = node.children[layout.filter_child]
                    admitted = [
                        row for row in admitted
                        if self._apply(function, row, environment) is TruthValue.TRUE
                    ]
                if layout.argument_child is None:
                    values = tuple(1 for _row in admitted)
                else:
                    function = node.children[layout.argument_child]
                    values = tuple(self._apply(function, row, environment) for row in admitted)
                if layout.mode is AggregateMode.DISTINCT:
                    values = _distinct(values)
                output.append(self.aggregate(
                    layout.aggregate,
                    self.arena.context.aggregate(layout.aggregate),
                    values,
                ))
            result = result.add(Row(payload.output_schema, tuple(output)), 1)
        return result


def _unique_rows(rows: Iterable[Row]) -> tuple[Row, ...]:
    result: list[Row] = []
    for row in rows:
        if not any(row_identity_equal(row.values, item.values) for item in result):
            result.append(row)
    return tuple(result)


def validate_instance(
    instance: Instance, *, evaluator_class: type[UExprEvaluator] = UExprEvaluator
) -> tuple[str, ...]:
    """Return semantic catalog-constraint violations for an instance."""

    catalog = instance.catalog
    evaluator = evaluator_class(catalog.constraint_arena, instance)
    violations: list[str] = []
    for relation, specification in catalog.context.relations():
        rows = instance.rows(relation)
        positions = {
            column.id: index for index, column in enumerate(specification.columns)
        }
        for declaration in specification.constraints:
            if not declaration.metadata.proof_active:
                continue
            label = f"constraint.{declaration.metadata.id.value}"
            if isinstance(declaration, NotNullDecl):
                position = positions[declaration.column]
                if any(row.values[position] is None for row in rows):
                    violations.append(label)
            elif isinstance(declaration, (PrimaryKeyDecl, UniqueDecl)):
                key = tuple(positions[column] for column in declaration.columns)
                if isinstance(declaration, PrimaryKeyDecl) and any(
                    any(row.values[position] is None for position in key) for row in rows
                ):
                    violations.append(label)
                    continue
                keys = [tuple(row.values[position] for position in key) for row in rows]
                for index, left in enumerate(keys):
                    for right in keys[index + 1 :]:
                        comparable = (
                            declaration.null_policy
                            is NullConflictPolicy.NULLS_NOT_DISTINCT
                            or all(value is not None for value in (*left, *right))
                        )
                        if comparable and row_identity_equal(left, right):
                            violations.append(label)
                            break
                    if violations and violations[-1] == label:
                        break
            elif isinstance(declaration, ForeignKeyDecl):
                source_positions = tuple(positions[column] for column in declaration.source)
                target_specification = catalog.context.relation(declaration.target_relation)
                target_positions = tuple(
                    target_specification.column_position(column)
                    for column in declaration.target
                )
                target_keys = tuple(
                    tuple(row.values[position] for position in target_positions)
                    for row in instance.rows(declaration.target_relation)
                )
                for row in rows:
                    key = tuple(row.values[position] for position in source_positions)
                    nulls = sum(value is None for value in key)
                    if declaration.match is ForeignKeyMatch.SIMPLE and nulls:
                        continue
                    if declaration.match is ForeignKeyMatch.FULL and nulls == len(key):
                        continue
                    if nulls or not any(row_identity_equal(key, target) for target in target_keys):
                        violations.append(label)
                        break
            elif isinstance(declaration, CheckDecl):
                if any(
                    evaluator.apply(declaration.predicate.term, row)
                    is TruthValue.FALSE
                    for row in rows
                ):
                    violations.append(label)
            elif isinstance(declaration, GeneratedColumnDecl):
                position = positions[declaration.column]
                if any(
                    evaluator.apply(declaration.expression.term, row)
                    != row.values[position]
                    for row in rows
                ):
                    violations.append(label)
    return tuple(violations)


def _nullable_compare(left, right, compare) -> TruthValue:
    if left is None or right is None:
        return TruthValue.UNKNOWN
    return TruthValue.TRUE if compare(left, right) else TruthValue.FALSE


def _and3(*values: TruthValue) -> TruthValue:
    if TruthValue.FALSE in values:
        return TruthValue.FALSE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in values else TruthValue.TRUE


def _or3(*values: TruthValue) -> TruthValue:
    if TruthValue.TRUE in values:
        return TruthValue.TRUE
    return TruthValue.UNKNOWN if TruthValue.UNKNOWN in values else TruthValue.FALSE


def _like_pattern(pattern: str) -> str:
    pieces = []
    escaped = False
    for character in pattern:
        if escaped:
            pieces.append(re.escape(character))
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "%":
            pieces.append(".*")
        elif character == "_":
            pieces.append(".")
        else:
            pieces.append(re.escape(character))
    if escaped:
        raise EvaluationError("LIKE pattern ends with an escape character")
    return "".join(pieces)


def _compare_order_values(left, right, nulls: str) -> int:
    if left is None or right is None:
        if left is right:
            return 0
        return -1 if (left is None) == (nulls == "first") else 1
    return (left > right) - (left < right)


def _distinct(values: tuple[object | None, ...]) -> tuple[object | None, ...]:
    result: list[object | None] = []
    for value in values:
        if not any(value == item for item in result):
            result.append(value)
    return tuple(result)


__all__ = [
    "AggregateFunction",
    "BagEntry",
    "BagValue",
    "EvaluationError",
    "LazyScalarFunction",
    "ScalarArgument",
    "ScalarFunction",
    "SequenceValue",
    "TruthValue",
    "UExprEvaluator",
    "strict",
    "validate_instance",
]
