# SQL TIMESTAMP values intentionally have no time zone.
# ruff: noqa: DTZ001
"""Observable execution semantics and integration with the existing typed IR."""

import itertools
import subprocess
import sys
from datetime import date, datetime, time

import pytest

from parseval.symbolic import (
    Runtime,
    Semantics,
    ZBool,
    ZDate,
    ZFloat,
    ZInt,
    ZInterval,
    ZString,
    ZTime,
    ZTimestamp,
)
from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena, TermView
from parseval.terms.binding import shift_vars
from parseval.terms.builder import IRBuilder
from parseval.terms.context import Context
from parseval.terms.sorts import (
    BOOLEAN,
    DATE,
    INTEGER,
    STRING,
    IntervalValue,
    ScalarSort,
    ScalarType,
    TypeKind,
)
from parseval.terms.verify import verify_closed, verify_uexpr


def test_existing_terms_are_the_expression_and_sort():
    runtime = Runtime()
    x = runtime.input("x", 10)
    y = runtime.input("y", 3)
    value = x * 2 + y
    assert value.concrete == 23
    assert value.evaluate() == 23
    assert value.evaluate({"x": 20, "y": 4}) == 44
    assert value.concrete == 23
    assert (
        value.sort == runtime.arena[value.expression.root].sort == ScalarSort(INTEGER)
    )
    assert isinstance(value.expression, TermView)
    assert isinstance(runtime.arena[value.expression.root], nodes.ScalarCall)
    assert isinstance(runtime.arena[x.expression.root], nodes.ExternalParameter)
    assert verify_closed(runtime.arena, value.expression.root) == ScalarSort(INTEGER)
    assert verify_uexpr(runtime.arena, value.expression.root) == ScalarSort(INTEGER)
    assert (
        shift_vars(runtime.arena, value.expression.root, row_delta=2)
        == value.expression.root
    )
    assert (x * 2 + y).same_expression(value)


@pytest.mark.parametrize(
    "concrete,cls",
    [
        (12, ZInt),
        (1.5, ZFloat),
        (True, ZBool),
        ("hello", ZString),
        (date(2024, 1, 1), ZDate),
        (time(3, 4), ZTime),
        (datetime(2024, 1, 1), ZTimestamp),
        (IntervalValue(days=1), ZInterval),
    ],
)
def test_typed_values_keep_concrete_carriers(concrete, cls):
    value = Runtime().input("input", concrete)
    assert isinstance(value, cls)
    assert type(value.concrete) is type(concrete)
    assert value.evaluate() == concrete


@pytest.mark.parametrize(
    "build,expected",
    [
        (lambda x: x + 3, 13),
        (lambda x: 3 + x, 13),
        (lambda x: x - 3, 7),
        (lambda x: 3 - x, -7),
        (lambda x: x * 3, 30),
        (lambda x: 3 * x, 30),
        (lambda x: x / 3, 3),
        (lambda x: 31 / x, 3),
        (lambda x: x % 3, 1),
        (lambda x: 31 % x, 1),
        (lambda x: -x, -10),
        (lambda x: abs(-x), 10),
    ],
)
def test_numeric_methods_execute_and_replay(build, expected):
    x = Runtime().input("x", 10)
    result = build(x)
    assert result.concrete == expected
    assert result.evaluate() == expected


@pytest.mark.parametrize("a,b", [(-10, 3), (10, -3), (-10, -3), (10**90 + 1, 3)])
def test_integer_division_does_not_round_through_float(a, b):
    runtime = Runtime()
    x = runtime.input("x", a)
    result = x / b
    expected = abs(a) // abs(b) * (-1 if (a < 0) != (b < 0) else 1)
    assert result.concrete == result.evaluate() == expected
    remainder = x % b
    assert remainder.concrete == remainder.evaluate() == a - expected * b


def test_numeric_promotion_is_visible_in_terms():
    runtime = Runtime()
    x = runtime.input("x", 2)
    result = x + 0.5
    assert isinstance(result, ZFloat)
    assert result.concrete == result.evaluate() == 2.5
    assert result.evaluate({"x": 4}) == 4.5
    operations = [
        runtime.arena.context.function(runtime.arena[t].payload.function).operator
        for t in runtime.arena.post_order((result.expression.root,))
        if isinstance(runtime.arena[t], nodes.ScalarCall)
    ]
    assert "cast_integer_to_float" in operations


@pytest.mark.parametrize("a,b", itertools.product((True, False, None), repeat=2))
def test_sql_truth_tables(a, b):
    runtime = Runtime()
    sort = ScalarSort(BOOLEAN, True)
    x, y = runtime.input("x", a, sort), runtime.input("y", b, sort)
    conjunction = (
        False if a is False or b is False else None if a is None or b is None else True
    )
    disjunction = (
        True if a is True or b is True else None if a is None or b is None else False
    )
    assert (x & y).concrete is conjunction
    assert (x & y).evaluate() is conjunction
    assert (x | y).concrete is disjunction
    assert (x | y).evaluate() is disjunction
    assert (~x).concrete is (None if a is None else not a)
    assert (~x).evaluate() is (~x).concrete
    assert x.is_true().evaluate() is (a is True)
    assert x.is_false().evaluate() is (a is False)
    assert x.is_unknown().evaluate() is (a is None)


@pytest.mark.parametrize(
    "build",
    [
        lambda x: x == 3,
        lambda x: x != 3,
        lambda x: x < 3,
        lambda x: x <= 3,
        lambda x: x > 3,
        lambda x: x >= 3,
    ],
)
def test_comparisons_preserve_nullable_predicate_terms(build):
    runtime = Runtime()
    x = runtime.input("x", None, ScalarSort(INTEGER, True))
    value = build(x)
    assert isinstance(value, ZBool)
    assert value.concrete is None and value.evaluate() is None
    assert isinstance(runtime.arena[value.expression.root], nodes.ToBoolean)
    assert value.evaluate({"x": 3}) == build(runtime.literal(3)).concrete


def test_null_is_distinct_from_missing_and_false():
    runtime = Runtime()
    x = runtime.input("x", None, ScalarSort(INTEGER, True))
    assert (x + 1).evaluate() is None
    assert (x == None).evaluate() is None
    assert x.is_null().evaluate() is True
    assert x.is_not_distinct_from(None).evaluate() is True
    with pytest.raises(KeyError):
        (x + 1).evaluate({})
    with pytest.raises(TypeError):
        bool(x == 1)
    with pytest.raises(ValueError):
        runtime.input("required", None, ScalarSort(INTEGER))
    with pytest.raises(TypeError):
        runtime.literal(None)


@pytest.mark.parametrize(
    "build,expected",
    [
        (lambda s: s + "!", "Abc!"),
        (lambda s: "!" + s, "!Abc"),
        (lambda s: s.lower(), "abc"),
        (lambda s: s.upper(), "ABC"),
        (lambda s: s.length(), 3),
        (lambda s: s.substring(2, 2), "bc"),
        (lambda s: s.substring(0, 2), "A"),
        (lambda s: s.contains("b"), True),
        (lambda s: s.startswith("A"), True),
        (lambda s: s.endswith("c"), True),
        (lambda s: s.like("A_c"), True),
        (lambda s: s.ilike("a%"), True),
    ],
)
def test_string_methods(build, expected):
    s = Runtime().input("s", "Abc")
    result = build(s)
    assert result.concrete == result.evaluate() == expected


def test_string_nulls_wildcards_and_pattern_errors():
    runtime = Runtime()
    s = runtime.input("s", None, ScalarSort(STRING, True))
    assert s.length().concrete is None
    assert s.like("%").evaluate() is None
    assert s.like(r"a\%").evaluate({"s": "a%"}) is True
    assert s.like("a_b").evaluate({"s": "a\nb"}) is True
    with pytest.raises(ValueError):
        runtime.literal("abc").like("x\\")
    with pytest.raises(ValueError):
        runtime.literal("abc").substring(1, -1)


def test_temporal_operations_and_casts_replay():
    runtime = Runtime()
    day = runtime.input("day", date(2024, 1, 31))
    interval = runtime.input("interval", IntervalValue(months=1))
    later = day + interval
    assert isinstance(later, ZTimestamp)
    assert later.concrete == later.evaluate() == datetime(2024, 2, 29)
    assert later.evaluate(
        {"day": date(2023, 1, 31), "interval": IntervalValue(months=1)}
    ) == datetime(2023, 2, 28)
    assert (day - date(2024, 1, 1)).evaluate() == 30
    assert (interval + day).evaluate() == later.concrete
    assert (-interval).evaluate() == IntervalValue(months=-1)
    assert day.cast(STRING).evaluate() == "2024-01-31"
    assert runtime.literal("2024-01-31").cast(DATE).evaluate() == day.concrete


def test_cast_precision_and_invalid_values():
    runtime = Runtime()
    decimal = ScalarType(TypeKind.DECIMAL, precision=5, scale=2)
    value = runtime.input("x", 1.236)
    assert value.cast(decimal).concrete == 1.24
    assert value.cast(decimal).evaluate() == 1.24
    with pytest.raises(OverflowError):
        runtime.literal(1000.0).cast(decimal)
    with pytest.raises(TypeError):
        runtime.literal("abc").cast(BOOLEAN)
    with pytest.raises(ValueError):
        runtime.literal("bad-date").cast(DATE)
    with pytest.raises(TypeError):
        runtime.literal(float("inf"))


def test_text_temporals_parse_text_and_read_numeric_prefixes():
    runtime = Runtime(semantics=Semantics(lenient_conversions=True, text_temporals=True))
    text = runtime.input("text", "bad-date")
    assert text.cast(DATE).concrete is None
    assert text.cast(DATE).evaluate({"text": "2020-02-03"}) == date(2020, 2, 3)
    moment = runtime.input("moment", datetime(1990, 5, 1, 12))
    assert moment.cast(INTEGER).concrete == 1990


def test_input_identity_ownership_and_expression_type_checks():
    runtime = Runtime()
    x = runtime.input("x", 1)
    with pytest.raises(ValueError):
        runtime.input("x", 2)
    with pytest.raises(ValueError):
        x + Runtime().input("x", 1)
    with pytest.raises(TypeError):
        x + "a"
    with pytest.raises(TypeError):
        x + True
    with pytest.raises(TypeError):
        x.evaluate({"x": True})
    with pytest.raises(TypeError):
        x.evaluate({"x": 1.2})
    with pytest.raises(ZeroDivisionError):
        x / 0
    assert runtime.arena[x.expression.root].payload.parameter in x.expression.inputs


def test_existing_scalar_terms_can_be_observed_and_reexecuted():
    runtime = Runtime(TermArena(Context()))
    x = runtime.input("x", 7)
    b = IRBuilder(runtime.arena)
    term = b.finish(
        b.apply("mul", (x.expression.root, b.literal(3, INTEGER)), ScalarSort(INTEGER))
    )
    value = runtime.observe(term)
    assert value.concrete == 21
    assert runtime.evaluate(term, {"x": 9}) == 27


def test_deep_expression_dag_does_not_require_python_recursion():
    runtime = Runtime()
    result = runtime.input("x", 1)
    for _ in range(2000):
        result = result + 1
    assert result.concrete == result.evaluate() == 2001


def test_no_solver_or_application_layer_dependency():
    script = """
import sys
from importlib.abc import MetaPathFinder
class Reject(MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname == 'z3' or fullname.startswith(('parseval.smt','parseval.instance','parseval.generator')):
            raise AssertionError(fullname)
sys.meta_path.insert(0,Reject())
from parseval.symbolic import Runtime
r=Runtime();x=r.input('x',3)
assert (x+4).evaluate()==7
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def test_case_only_executes_selected_arm():
    runtime = Runtime()
    condition = runtime.input("condition", True, ScalarSort(BOOLEAN, True))
    numerator = runtime.input("numerator", 3)
    b = runtime.builder
    division = b.apply(
        "div", (numerator.expression.root, b.literal(0, INTEGER)), ScalarSort(INTEGER)
    )
    term = b.finish(
        b.case(
            b.to_predicate(condition.expression.root), b.literal(7, INTEGER), division
        )
    )
    assert runtime.observe(term).concrete == 7
    assert runtime.evaluate(term, {"condition": True, "numerator": 5}) == 7
    for value in (False, None):
        with pytest.raises(ZeroDivisionError):
            runtime.evaluate(term, {"condition": value, "numerator": 5})


def test_expression_view_and_parameter_id_assignments():
    from parseval.symbolic import ZExpr

    runtime = Runtime()
    x = runtime.input("x", 5)
    value = x + 3
    assert isinstance(value.expression, ZExpr)
    (parameter,) = x.expression.inputs
    assert value.evaluate({parameter: 8}) == 11
    with pytest.raises(ValueError, match="arena"):
        ZExpr(runtime.arena, value.expression.root, Runtime())
    with pytest.raises(TypeError, match="ScalarSort"):
        runtime.evaluate(runtime.builder.finish(runtime.builder.true3()))
