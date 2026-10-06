"""Aggregate values as Terms over weighted inputs.

Each input is a pair of a weight Term (membership, FILTER and multiplicity)
and an argument Term. COUNT, SUM, AVG, MIN and MAX are exact Terms, so absent
candidate rows remain symbolic. Other aggregates are computed from the present
inputs and enter the Term as constants.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from parseval.terms.context import AggregateKind, AggregateSpec
from parseval.terms.sorts import FLOAT, ScalarSort, TypeKind
from parseval.terms.terms import TermId

from .valuation import Valuation

Input = tuple[TermId, TermId | None]


def _sample_stddev(values):
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _population_stddev(values):
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


CONCRETE_AGGREGATES: dict[str, Callable[[list], object]] = {
    "stddev": _sample_stddev,
    "stddev_samp": _sample_stddev,
    "stddev_pop": _population_stddev,
    "__sqlite_arbitrary_value": lambda values: values[0],
}


def concrete_aggregate(spec: AggregateSpec, values: list) -> object:
    """Fold non-NULL argument values, as used by window frames."""
    if spec.kind is AggregateKind.COUNT:
        return len(values)
    if not values:
        return None
    if spec.kind is AggregateKind.SUM:
        return sum(values)
    if spec.kind is AggregateKind.AVG:
        return sum(values) / len(values)
    if spec.kind is AggregateKind.MIN:
        return min(values)
    if spec.kind is AggregateKind.MAX:
        return max(values)
    return CONCRETE_AGGREGATES[spec.operator](values)


class Aggregates:
    def __init__(self, valuation: Valuation):
        self.v = valuation

    def build(self, spec: AggregateSpec, inputs: Sequence[Input], distinct: bool) -> TermId:
        v = self.v
        output = spec.output
        if spec.input is not None:
            inputs = [(v.mul(weight, v.indicator(v.not3(v.is_null(value)))), value) for weight, value in inputs]
        if distinct:
            inputs = self._first_occurrences(inputs)
        kind = spec.kind
        if kind is AggregateKind.COUNT:
            return v.add(*(weight for weight, _ in inputs))
        if kind in (AggregateKind.MIN, AggregateKind.MAX):
            return self._extreme(inputs, output, kind is AggregateKind.MIN)
        if kind in (AggregateKind.SUM, AggregateKind.AVG):
            count = v.add(*(weight for weight, _ in inputs))
            if kind is AggregateKind.SUM:
                total = self._sum(inputs, output)
            else:
                total = v.apply(
                    "div",
                    (self._sum(inputs, ScalarSort(FLOAT, True)), v.to_float(count)),
                    ScalarSort(FLOAT, True),
                )
                total = self._convert(total, output)
            return v.case(v.positive(count), total, v.builder.resolve(v.builder.null(output.sql_type)))
        return self._concrete(spec, inputs)

    def _sum(self, inputs: Sequence[Input], output: ScalarSort) -> TermId:
        v = self.v
        zero = v.literal(0.0 if output.sql_type.kind is not TypeKind.INTEGER else 0, output.sql_type)
        terms = []
        for weight, value in inputs:
            value = self._convert(value, ScalarSort(output.sql_type, True))
            terms.append(v.guard(weight, v.scale(weight, value, ScalarSort(output.sql_type, True)), zero))
        return self._balanced(terms, output) if terms else zero

    def _balanced(self, terms: list[TermId], output: ScalarSort) -> TermId:
        v = self.v
        while len(terms) > 1:
            terms = [
                v.apply("add", terms[index : index + 2], ScalarSort(output.sql_type, True))
                if index + 1 < len(terms) else terms[index]
                for index in range(0, len(terms), 2)
            ]
        return terms[0]

    def _extreme(self, inputs: Sequence[Input], output: ScalarSort, minimum: bool) -> TermId:
        """A balanced tournament over present non-NULL values, NULL if none."""
        v = self.v
        null = v.builder.resolve(v.builder.null(output.sql_type))
        values = [v.guard(weight, value, null) for weight, value in inputs]
        if not values:
            return null
        while len(values) > 1:
            paired = []
            for index in range(0, len(values) - 1, 2):
                left, right = values[index], values[index + 1]
                better = v.lt3(left, right) if minimum else v.lt3(right, left)
                keep_left = v.or3(v.is_null(right), v.and3(v.not3(v.is_null(left)), better))
                paired.append(v.case(keep_left, left, right))
            if len(values) % 2:
                paired.append(values[-1])
            values = paired
        return values[0]

    def _first_occurrences(self, inputs: Sequence[Input]) -> list[Input]:
        """Weight each distinct argument once, at its first present occurrence."""
        v = self.v
        seen: list[Input] = []
        result = []
        for weight, value in inputs:
            earlier = v.or3(*(
                v.and3(v.positive(previous), v.same(value, other))
                for previous, other in seen
                if other == value or v.is_open(other) or v.is_open(value)
            ))
            result.append((v.indicator(v.and3(v.positive(weight), v.not3(earlier))), value))
            seen.append((weight, value))
        return result

    def _convert(self, value: TermId, target: ScalarSort) -> TermId:
        v = self.v
        sort = v.arena[value].sort
        if sort.sql_type.kind is target.sql_type.kind:
            return value
        return v.apply(
            f"cast_{sort.sql_type.kind.value}_to_{target.sql_type.kind.value}",
            (value,),
            ScalarSort(target.sql_type, sort.nullable),
        )

    def _concrete(self, spec: AggregateSpec, inputs: Sequence[Input]) -> TermId:
        v = self.v
        values = []
        for weight, value in inputs:
            count = v.concrete(weight)
            if count:
                values.extend([v.concrete(value)] * count)
        values = [value for value in values if value is not None]
        result = CONCRETE_AGGREGATES[spec.operator](values) if values else None
        if result is None:
            return v.builder.resolve(v.builder.null(spec.output.sql_type))
        return v.literal(result, spec.output.sql_type)


__all__ = ["Aggregates", "CONCRETE_AGGREGATES", "concrete_aggregate"]
