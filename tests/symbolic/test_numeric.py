"""Independent arithmetic checks and shared fractional-value behavior."""

import itertools
import operator
import sqlite3

import pytest

from parseval.symbolic import Runtime, Semantics, ZFloat
from parseval.terms.sorts import FLOAT, INTEGER, ScalarSort, ScalarType, TypeKind


@pytest.mark.parametrize(
    "operation,sql",
    [
        (operator.add, "+"),
        (operator.sub, "-"),
        (operator.mul, "*"),
        (operator.truediv, "/"),
        (operator.mod, "%"),
    ],
)
def test_integer_arithmetic_against_sqlite(operation, sql):
    runtime = Runtime()
    x, y = runtime.input("x", 7), runtime.input("y", 3)
    result = operation(x, y)
    connection = sqlite3.connect(":memory:")
    try:
        expected = connection.execute(f"SELECT 7 {sql} 3").fetchone()[0]
        assert result.concrete == expected
        for a, b in itertools.product((-9, -3, -1, 0, 1, 3, 9), (-3, -1, 1, 3)):
            expected = connection.execute(f"SELECT ? {sql} ?", (a, b)).fetchone()[0]
            assert result.evaluate({"x": a, "y": b}) == expected
    finally:
        connection.close()


@pytest.mark.parametrize("value", [1.25])
def test_one_fractional_class_preserves_its_carrier(value):
    x = Runtime().input("x", value)
    result = x + 2
    assert isinstance(x, ZFloat)
    assert isinstance(result, ZFloat)
    assert type(result.concrete) is type(value)
    assert result.concrete == result.evaluate() == value + 2
    replacement = type(value)(3)
    assert result.evaluate({"x": replacement}) == replacement + 2
    assert type(result.evaluate({"x": replacement})) is type(value)


def test_decimal_inputs_use_the_float_carrier():
    runtime = Runtime()
    x = runtime.input("x", 0.1, ScalarSort(ScalarType(TypeKind.DECIMAL)))
    result = x + 0.2
    assert isinstance(result, ZFloat)
    assert type(result.concrete) is float
    assert result.concrete == result.evaluate() == 0.1 + 0.2
    assert result.evaluate({"x": 0.3}) == 0.3 + 0.2


@pytest.mark.parametrize(
    "sql_type,carrier", [(FLOAT, float), (ScalarType(TypeKind.DECIMAL), float)]
)
def test_fractional_nulls_keep_the_declared_carrier(sql_type, carrier):
    runtime = Runtime()
    x = runtime.input("x", None, ScalarSort(sql_type, True))
    result = x * 2
    assert isinstance(result, ZFloat)
    assert result.concrete is result.evaluate() is None
    assert result.evaluate({"x": carrier("1.25")}) == carrier("2.5")
    assert type(result.evaluate({"x": carrier("1.25")})) is carrier
    assert x.cast(INTEGER).evaluate() is None


def test_nonfinite_arithmetic_results_are_rejected_on_both_execution_paths():
    runtime = Runtime()
    x = runtime.input("x", 1.0)
    result = x * 2.0
    with pytest.raises(TypeError):
        result.evaluate({"x": 1e308})
    with pytest.raises(TypeError):
        runtime.literal(1e308) * 2.0
