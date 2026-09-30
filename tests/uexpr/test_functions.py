from datetime import date, datetime
from decimal import Decimal

import pytest

from parseval.catalog import Catalog
from parseval.instance import Instance
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder
from parseval.terms.context import AggregateKind, AggregateSpec, ScalarFunctionSpec
from parseval.terms.names import AggregateSpecId, FunctionId
from parseval.terms.sorts import ScalarSort
from parseval.terms.types import INTEGER
from parseval.uexpr import EvaluationError, UExprEvaluator, strict


def _scalar(functions, operator, *values):
    spec = ScalarFunctionSpec((), ScalarSort(INTEGER), operator=operator)
    return functions.scalar(FunctionId(0), spec, tuple(lambda value=value: value for value in values))


@pytest.mark.parametrize(
    "operator, arguments, expected",
    [
        ("add", (2, 3), 5),
        ("sub", (2, 3), -1),
        ("mul", (2, 3), 6),
        ("div", (7, 2), 3.5),
        ("mod", (7, 2), 1),
        ("neg", (2,), -2),
        ("abs", (-2,), 2),
        ("lower", ("ABC",), "abc"),
        ("upper", ("abc",), "ABC"),
        ("length", ("abc",), 3),
        ("substring", ("abcde", 2, 3), "bcd"),
        ("substring", ("abcde", 2), "bcde"),
        ("round", (1.234, 2), 1.23),
        ("date", (datetime(2020, 1, 2, 3),), date(2020, 1, 2)),
        ("time", (datetime(2020, 1, 2, 3),), datetime(2020, 1, 2, 3).time()),
        ("year", (date(2020, 1, 2),), 2020),
        ("extract_month", (date(2020, 1, 2),), 1),
        ("datediff", (date(2020, 1, 3), date(2020, 1, 1)), 2),
        ("instr", ("abc", "b"), 2),
        ("instr", ("abc", "x"), 0),
        ("ts_or_ds_to_timestamp", (date(2020, 1, 2),), datetime(2020, 1, 2)),
        ("date_part", ("YEAR", date(2020, 1, 2)), 2020),
        ("cast_string_to_integer", ("12",), 12),
        ("cast_integer_to_decimal", (12,), Decimal(12)),
        ("nullif", (1, 1), None),
        ("nullif", (1, None), 1),
        ("coalesce", (None, 3, 4), 3),
        ("coalesce", (None, None), None),
        ("add", (None, 3), None),
    ],
)
def test_builtin_callbacks(operator, arguments, expected):
    assert _scalar(UExprEvaluator, operator, *arguments) == expected


def test_subclass_overrides_leave_base_and_sibling_callbacks_unchanged():
    class CustomEvaluator(UExprEvaluator):
        SCALAR_FUNCTIONS = {**UExprEvaluator.SCALAR_FUNCTIONS, "abs": lambda args: 42}

    class SiblingEvaluator(UExprEvaluator):
        pass

    assert _scalar(CustomEvaluator, "abs", -2) == 42
    assert _scalar(CustomEvaluator, "add", 2, 3) == 5
    assert _scalar(UExprEvaluator, "abs", -2) == 2
    assert _scalar(SiblingEvaluator, "abs", -2) == 2


def test_identity_override_precedes_operator_override():
    class CustomEvaluator(UExprEvaluator):
        SCALAR_FUNCTIONS = {**UExprEvaluator.SCALAR_FUNCTIONS, "abs": lambda args: 10}
        LAZY_SCALAR_FUNCTIONS = {
            **UExprEvaluator.LAZY_SCALAR_FUNCTIONS,
            FunctionId(0): lambda args: 20,
        }

    assert _scalar(CustomEvaluator, "abs", -2) == 20


def test_strict_callbacks_propagate_null_without_calling_user_code():
    seen = []
    class CustomEvaluator(UExprEvaluator):
        SCALAR_FUNCTIONS = {
            **UExprEvaluator.SCALAR_FUNCTIONS,
            "f": strict(lambda args: seen.append(args) or 7),
        }

    assert _scalar(CustomEvaluator, "f", None) is None
    assert seen == []
    assert _scalar(CustomEvaluator, "f", 2) == 7
    assert seen == [(2,)]


def test_catalog_function_without_operator_uses_identity_callback():
    catalog = Catalog()
    sort = ScalarSort(INTEGER)
    declaration = catalog.register_scalar_function("double_it", ScalarFunctionSpec((sort,), sort))
    arena = TermArena(catalog.context)
    builder = IRBuilder(arena)
    root = builder.finish(builder.scalar_call(declaration.function, (builder.literal(3, INTEGER),)))
    class CustomEvaluator(UExprEvaluator):
        SCALAR_FUNCTIONS = {
            **UExprEvaluator.SCALAR_FUNCTIONS,
            declaration.function: lambda args: args[0] * 2,
        }

    evaluator = CustomEvaluator(arena, Instance.empty(catalog))
    assert evaluator.value(root) == 6


def test_lazy_callback_memoizes_arguments_only_within_each_call():
    catalog = Catalog()
    arena = TermArena(catalog.context)
    builder = IRBuilder(arena)
    sort = ScalarSort(INTEGER)
    leaf = builder.apply("tick", (), sort)
    unused = builder.apply("unimplemented", (), sort)
    root = builder.finish(builder.apply("twice", (leaf, unused), sort))
    calls = []
    class CustomEvaluator(UExprEvaluator):
        SCALAR_FUNCTIONS = {
            **UExprEvaluator.SCALAR_FUNCTIONS,
            "tick": lambda args: calls.append(1) or len(calls),
        }
        LAZY_SCALAR_FUNCTIONS = {
            **UExprEvaluator.LAZY_SCALAR_FUNCTIONS,
            "twice": lambda args: args[0]() + args[0](),
        }

    evaluator = CustomEvaluator(arena, Instance.empty(catalog))
    assert evaluator.value(root) == 2
    assert evaluator.value(root) == 4
    assert len(calls) == 2


def test_fold_uses_catalog_aggregate_callback_and_preserves_nulls_and_duplicates():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT)")
    relation, spec = next(iter(catalog.context.relations()))
    declaration = catalog.register_aggregate(
        "size", AggregateSpec(catalog.context.schema(spec.schema).fields[0], ScalarSort(INTEGER))
    )
    arena = TermArena(catalog.context)
    builder = IRBuilder(arena)
    root = builder.finish(builder.fold(declaration.aggregate, builder.base(relation)))
    seen = []
    class CustomEvaluator(UExprEvaluator):
        AGGREGATE_FUNCTIONS = {
            **UExprEvaluator.AGGREGATE_FUNCTIONS,
            declaration.aggregate: lambda spec, values: seen.append(values) or len(values),
        }

    evaluator = CustomEvaluator(
        arena, Instance.from_rows(catalog, {relation: ((1,), (1,), (None,))}),
    )
    assert evaluator.value(root) == 3
    assert seen == [(1, 1, None)]


@pytest.mark.parametrize("values", [(), (None,), (1,)])
def test_unknown_aggregate_fails_even_for_empty_or_null_input(values):
    spec = AggregateSpec(ScalarSort(INTEGER), ScalarSort(INTEGER), operator="unknown")
    with pytest.raises(EvaluationError, match="Unsupported aggregate"):
        UExprEvaluator.aggregate(AggregateSpecId(0), spec, values)


def test_unknown_scalar_fails_even_for_null_input():
    with pytest.raises(EvaluationError, match="Unsupported scalar function"):
        _scalar(UExprEvaluator, "unknown", None)


@pytest.mark.parametrize(
    "kind, values, expected",
    [
        (AggregateKind.COUNT, (1, 1, None), 2),
        (AggregateKind.COUNT, (), 0),
        (AggregateKind.SUM, (1, 1, None), 2),
        (AggregateKind.SUM, (), None),
        (AggregateKind.AVG, (1, 2, None), 1.5),
        (AggregateKind.MIN, (1, 2, None), 1),
        (AggregateKind.MAX, (1, 2, None), 2),
    ],
)
def test_builtin_aggregate_callbacks(kind, values, expected):
    spec = AggregateSpec(ScalarSort(INTEGER), ScalarSort(INTEGER), kind=kind)
    assert UExprEvaluator.aggregate(AggregateSpecId(0), spec, values) == expected
