"""SQL Boolean results and Python operator dispatch are separate contracts."""

import itertools
import sqlite3

import pytest

from parseval.symbolic import Runtime, ZBool
from parseval.terms.sorts import BOOLEAN, ScalarSort

TRUTH_VALUES = (True, False, None)


@pytest.fixture
def database():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


@pytest.mark.parametrize("left,right", itertools.product(TRUTH_VALUES, repeat=2))
def test_forward_and_reflected_boolean_operators_match_sql(database, left, right):
    runtime = Runtime()
    x = runtime.input("x", left, ScalarSort(BOOLEAN, True))
    sql_and, sql_or = database.execute(
        "SELECT ? AND ?, ? OR ?", (left, right, left, right)
    ).fetchone()
    for result, expected in (
        (x & right, sql_and),
        (right & x, sql_and),
        (x | right, sql_or),
        (right | x, sql_or),
        (x.sql_and(right), sql_and),
        (x.sql_or(right), sql_or),
    ):
        assert isinstance(result, ZBool)
        expected = None if expected is None else bool(expected)
        assert result.concrete is expected
        assert result.evaluate() is expected


def test_nested_predicates_replay_all_sql_truth_assignments(database):
    runtime = Runtime()
    sort = ScalarSort(BOOLEAN, True)
    x, y, z = (runtime.input(name, True, sort) for name in ("x", "y", "z"))
    result = (x & y) | ~z
    original_root = result.expression.root
    for a, b, c in itertools.product(TRUTH_VALUES, repeat=3):
        expected = database.execute("SELECT (? AND ?) OR NOT ?", (a, b, c)).fetchone()[
            0
        ]
        expected = None if expected is None else bool(expected)
        assert result.evaluate({"x": a, "y": b, "z": c}) is expected
    assert result.concrete is True
    assert result.expression.root == original_root
    assert (
        result.expression.inputs
        == x.expression.inputs | y.expression.inputs | z.expression.inputs
    )


@pytest.mark.parametrize("concrete", TRUTH_VALUES)
@pytest.mark.parametrize(
    "operation", [lambda x: x and True, lambda x: x or False, lambda x: not x]
)
def test_python_logical_keywords_reject_loss_of_symbolic_expression(
    concrete, operation
):
    value = Runtime().input("x", concrete, ScalarSort(BOOLEAN, True))
    with pytest.raises(TypeError, match="truth"):
        operation(value)


def test_bitwise_boolean_syntax_is_eager():
    runtime = Runtime()
    false = runtime.input("false", False)
    number = runtime.input("number", 1)
    with pytest.raises(ZeroDivisionError):
        false & (number / 0 > 1)


@pytest.mark.parametrize("other", [0, 1, 2, 0.0, "true"])
def test_boolean_operators_reject_nonboolean_operands(other):
    value = Runtime().input("x", True)
    for operation in (
        lambda: value & other,
        lambda: other & value,
        lambda: value | other,
        lambda: other | value,
    ):
        with pytest.raises(TypeError):
            operation()


def test_compound_comparisons_keep_both_inputs():
    runtime = Runtime()
    x, y = runtime.input("x", 2), runtime.input("y", 4)
    result = (x > 0) & (y < 5)
    assert result.concrete is True
    assert result.evaluate({"x": -1, "y": 4}) is False
    assert result.evaluate({"x": 2, "y": 9}) is False
    assert result.expression.inputs == x.expression.inputs | y.expression.inputs
    with pytest.raises(TypeError, match="truth"):
        _ = 0 < x < 5
