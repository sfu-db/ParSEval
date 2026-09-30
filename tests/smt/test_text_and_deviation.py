"""Direct SMT constraints: no SQL parser or coverage discovery."""
from datetime import date

import pytest
import z3

from parseval.catalog import Catalog
from parseval.smt.encoding import UExprEncoder, _Environment
from parseval.smt.instance import SymbolicInstance
from parseval.smt.values import SymbolicValue
from parseval.terms.arena import TermArena
from parseval.terms.builder import IRBuilder
from parseval.terms.context import AggregateSpec
from parseval.terms.sorts import ScalarSort
from parseval.terms.sorts import STRING, DATE, INTEGER, FLOAT


def encoder():
    c = Catalog(dialect='postgres')
    a = TermArena(c.context)
    return c, IRBuilder(a), UExprEncoder(a, SymbolicInstance(c, {}))


def check(e, *conditions):
    s = z3.Solver()
    s.set(timeout=3000)
    s.add(*e.constraints, *conditions)
    return s.check()


@pytest.mark.parametrize('value,pattern,expected', [
    ('abc', 'a%', 1), ('abc', 'a_d', 0), ('', '%', 1),
    ('', '_', 0), ('a\nb', 'a_b', 1), ('a%b', r'a\%b', 1),
    ('a_b', r'a\_b', 1), ('a\\b', r'a\\b', 1),
    (None, '%', -1), ('abc', None, -1),
])
def test_symbolic_like(value, pattern, expected):
    _, b, e = encoder()
    # A CASE expression exercises the dynamic-pattern path rather than Literal.
    p = b.case(b.true3(), b.literal(pattern, STRING), b.literal('unused', STRING))
    term = b.finish(b.like3(b.literal(value, STRING), p))
    actual = e.predicate(term, _Environment())
    assert check(e, actual != expected) == z3.unsat


def test_like_solves_for_pattern_and_rejects_dangling_escape():
    from parseval.smt.text import dynamic_like, valid_like_pattern
    _, _, e = encoder()
    pattern = z3.String('pattern')
    assert check(e, dynamic_like(z3.StringVal('abc'), pattern), pattern == 'a%') == z3.sat
    assert check(e, valid_like_pattern(z3.StringVal('a\\'))) == z3.unsat


@pytest.mark.parametrize('value,pattern,expected', [('ClOuD', '%cloud%', 1), ('ABC', 'a_d', 0), (None, '%', -1)])
def test_ilike(value, pattern, expected):
    _, b, e = encoder()
    result = e.predicate(b.finish(b.ilike3(b.literal(value, STRING), b.literal(pattern, STRING))), _Environment())
    assert check(e, result != expected) == z3.unsat


def test_lower_symbolic_and_null():
    _, _, e = encoder()
    text = z3.String('lower_input')
    result = e._call('lower', (SymbolicValue(text, z3.BoolVal(False), STRING),), STRING)
    assert check(e, text == 'AbC\n123', result.value != 'abc\n123') == z3.unsat
    assert check(e, result.value == 'abc') == z3.sat
    null = e._call('lower', (SymbolicValue(z3.StringVal(''), z3.BoolVal(True), STRING),), STRING)
    assert check(e, z3.Not(null.is_null)) == z3.unsat


@pytest.mark.parametrize('text', ['0001-01-01', '1900-03-01', '2000-02-29', '2024-12-31', '9999-12-31'])
def test_iso_date(text):
    _, _, e = encoder()
    value = z3.String('date_text')
    result = e._call('cast_string_to_date', (SymbolicValue(value, z3.BoolVal(False), STRING),), DATE)
    expected = date.fromisoformat(text).toordinal()
    assert check(e, value == text, result.value != expected) == z3.unsat
    assert check(e, value == text, result.value == expected) == z3.sat


@pytest.mark.parametrize('text', ['1900-02-29', '2023-02-29', '2024-04-31', '0000-01-01', '2024-13-01', '2024-1-01', 'nonsense'])
def test_invalid_date_excluded(text):
    _, _, e = encoder()
    e._call('cast_string_to_date', (SymbolicValue(z3.StringVal(text), z3.BoolVal(False), STRING),), DATE)
    assert check(e) == z3.unsat


def test_null_date_does_not_require_valid_payload():
    _, _, e = encoder()
    result = e._call('cast_string_to_date', (SymbolicValue(z3.StringVal('bad'), z3.BoolVal(True), STRING),), DATE)
    assert check(e, result.is_null) == z3.sat


@pytest.mark.parametrize('operator,values,squared,is_null', [
    ('stddev_samp', [], 0, True), ('stddev_samp', [(2, 1)], 0, True),
    ('stddev_pop', [(2, 1)], 0, False), ('stddev_pop', [(None, 4)], 0, True),
    ('stddev_samp', [(1, 1), (3, 1)], 2, False),
    ('stddev_pop', [(1, 1), (3, 1)], 1, False),
    ('stddev', [(1, 2), (4, 1), (None, 3)], 3, False),
    ('stddev_pop', [(1, 2), (4, 1), (99, 0)], 2, False),
])
def test_weighted_deviation(operator, values, squared, is_null):
    c, _, e = encoder()
    spec = c.context.intern_aggregate(AggregateSpec(ScalarSort(INTEGER, True), ScalarSort(FLOAT, True), operator=operator))
    args = tuple((SymbolicValue(z3.IntVal(value or 0), z3.BoolVal(value is None), INTEGER), z3.IntVal(weight)) for value, weight in values)
    actual = e._aggregate(spec, args)
    assert check(e, actual.is_null != is_null) == z3.unsat
    assert check(e) == z3.sat
    if not is_null:
        assert check(e, actual.value * actual.value != squared) == z3.unsat
        assert check(e, actual.value < 0) == z3.unsat


def test_distinct_filtered_deviation():
    from parseval.terms.builder import AggregateCall
    from parseval.terms.decls import RowShape
    c = Catalog.from_ddl('CREATE TABLE t(x INT)', dialect='postgres')
    relation, info = next(iter(c.context.relations()))
    a = TermArena(c.context)
    b = IRBuilder(a)
    db = SymbolicInstance(c, {relation: 4})
    e = UExprEncoder(a, db)
    spec = c.context.intern_aggregate(AggregateSpec(ScalarSort(INTEGER, True), ScalarSort(FLOAT, True), operator='stddev_pop'))
    out = c.context.intern_schema(RowShape((ScalarSort(FLOAT, True),)))
    folded = b.global_fold(b.base(relation), (AggregateCall(spec, lambda r: b.field(r, 0),
        filter=lambda r: b.lt3(b.literal(0, INTEGER), b.field(r, 0)), distinct=True),), out)
    result = e.bag(b.finish(folded), _Environment())[0].row.values[0]
    pinned = []
    for entry, value, weight in zip(db.relations[relation], [1, 3, -10, 0], [5, 2, 3, 1]):
        pinned.extend((entry.row.values[0].value == value, entry.row.values[0].is_null == (value == 0), entry.multiplicity == weight))
    assert check(e, *pinned) == z3.sat
    assert check(e, *pinned, z3.Or(result.is_null, result.value != 1)) == z3.unsat


def test_solver_generates_lowercase_witness():
    from parseval.coverage.model import CoverageSite, CoverageTarget, WitnessedObligation
    from parseval.coverage import target_is_covered
    from parseval.smt import Solver, SolveStatus
    from parseval.terms.sorts import RowSort
    from parseval.uexpr.observation import WeightCondition
    from parseval.uexpr.witness import UnitWitnessPlan
    c = Catalog.from_ddl('CREATE TABLE t(s TEXT)', dialect='postgres')
    relation, info = next(iter(c.context.relations()))
    a = TermArena(c.context)
    b = IRBuilder(a)
    term = b.finish(b.sum(RowSort(info.schema), lambda r: b.mul(b.at(b.base(relation), r),
        b.indicator(b.eq3(b.apply('lower', (b.field(r, 0),), ScalarSort(STRING, True)), b.literal('abc', STRING))))))
    target = CoverageTarget('lower', CoverageSite(term, ()), WitnessedObligation(UnitWitnessPlan(), (WeightCondition(term),)), 'lower')
    result = Solver(c, timeout_ms=3000, minimize=False).solve(a, target)
    assert result.status is SolveStatus.SAT, result.reason
    assert target_is_covered(a, result.instance, target)
