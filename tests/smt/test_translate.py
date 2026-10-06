"""Z3 translations agree with the concrete semantics of the symbolic runtime."""

from datetime import date, datetime

import pytest
import z3

from parseval.catalog import Catalog
from parseval.instance import Instance, Valuation
from parseval.smt import Status, Translator, solve
from parseval.terms.sorts import DATE, INTEGER, STRING, TIMESTAMP, ScalarSort


@pytest.fixture
def valuation():
    return Valuation(Instance(Catalog()))


def constant(valuation, term):
    """Evaluate a closed Term through Z3 and through concrete execution."""
    value, _ = Translator(valuation).translate(term)
    return z3.simplify(value.value), valuation.value(term)


@pytest.mark.parametrize("a,b", [(7, 2), (-7, 2), (7, -2), (-7, -2)])
def test_integer_division_and_remainder_truncate(valuation, a, b):
    v = valuation
    left = v.builder.resolve(v.builder.literal(a, INTEGER))
    right = v.builder.resolve(v.builder.literal(b, INTEGER))
    for operator in ("div", "mod"):
        term = v.builder.resolve(v.builder.apply(operator, (left, right), ScalarSort(INTEGER)))
        encoded, concrete = constant(v, term)
        assert encoded.as_long() == concrete


@pytest.mark.parametrize("day", [date(2000, 2, 29), date(1999, 12, 31), date(1, 1, 1), date(2024, 3, 1)])
def test_calendar_fields_and_formatting(valuation, day):
    v = valuation
    value = v.builder.resolve(v.builder.literal(day, DATE))
    for operator, result in (("year", INTEGER), ("extract_month", INTEGER), ("extract_day", INTEGER)):
        term = v.builder.resolve(v.builder.apply(operator, (value,), ScalarSort(result)))
        encoded, concrete = constant(v, term)
        assert encoded.as_long() == concrete
    pattern = v.builder.resolve(v.builder.literal("%Y-%m-%d", STRING))
    stamp = v.builder.resolve(v.builder.literal(datetime.combine(day, datetime.min.time()), TIMESTAMP))
    term = v.builder.resolve(v.builder.apply("time_to_str", (stamp, pattern), ScalarSort(STRING)))
    encoded, concrete = constant(v, term)
    assert encoded.as_string() == concrete


def test_division_by_zero_is_undefined_only_where_evaluated():
    catalog = Catalog.from_ddl("CREATE TABLE t(a INT NOT NULL, b INT NOT NULL)")
    relation = catalog.resolve_table("t").relation
    instance, slot = Instance(catalog).insert(relation, (1, 1), 0)
    v = Valuation(instance, frozenset(slot.parameters))
    a, b = (v.input(cell) for cell in slot.cells)
    quotient = v.apply("div", (a, b), ScalarSort(INTEGER))
    guarded = v.case(v.eq3(b, v.zero), v.zero, quotient)
    # CASE WHEN b = 0 THEN 0 ELSE a / b END = 0 is satisfiable with b = 0.
    solution = solve(v, [[v.eq3(guarded, v.zero)], [v.eq3(b, v.zero)]], timeout_ms=2000)
    assert solution.status is Status.SAT
    # a / b = 0 with b = 0 evaluates the division, which is an error.
    solution = solve(v, [[v.eq3(quotient, v.zero)], [v.eq3(b, v.zero)]], timeout_ms=2000)
    assert solution.status is Status.UNSAT
