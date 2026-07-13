"""Production-readiness tests for :mod:`parseval.plan.rex`.

Pins the concrete-evaluation contract for the new stack:

* Symbol hierarchy: tri-state, Const coercion, Variable table+column identity
* :class:`Environment`: Identifier row maps, SolverVar assignments, scope chaining
* :func:`concrete`: class-dispatched handlers including SolverVar leaves
* Three-valued logic, comparisons, membership, strings, casts
"""

from __future__ import annotations

import unittest
from datetime import date

from sqlglot import exp, parse_one

from parseval.dtype import DataType
from parseval.plan.rex import (
    Const,
    Environment,
    Symbol,
    Variable,
    concrete,
    negate_predicate,
    tvl_and,
    tvl_not,
    tvl_or,
)
from parseval.solver.types import SolverVar


INT = DataType.build("INT")
REAL = DataType.build("REAL")
TEXT = DataType.build("TEXT")
BOOL = DataType.build("BOOLEAN")
DATE_T = DataType.build("DATE")


def _variable(name: str = "x", type=INT) -> Variable:
    return Variable(
        this=name,
        type=type,
        table=exp.to_table("t"),
        column=exp.to_identifier(name),
        rowid="row_0",
    )


def _predicate(sql: str) -> exp.Expression:
    where = parse_one(f"SELECT * FROM t WHERE {sql}").find(exp.Where)
    assert where is not None
    return where.this


def _value(sql: str) -> exp.Expression:
    select = parse_one(f"SELECT {sql} AS v FROM t")
    return select.expressions[0].this


def _env(bindings: dict) -> Environment:
    """Build an Environment from ``{column_name: value}`` or Identifier keys."""
    row = {}
    for key, value in bindings.items():
        if isinstance(key, exp.Identifier):
            row[key] = value
        else:
            row[exp.to_identifier(str(key))] = value
    return Environment.from_row(row)


# ---------------------------------------------------------------------------
# Symbol hierarchy
# ---------------------------------------------------------------------------


class TestSymbolTriState(unittest.TestCase):
    def test_const_bound_to_value(self):
        c = Const(this=5, type=INT)
        self.assertTrue(c.is_bound)
        self.assertFalse(c.is_null)
        self.assertEqual(c.concrete, 5)
        self.assertEqual(c.value, 5)
        self.assertEqual(c.type, INT)

    def test_const_null_via_classmethod(self):
        c = Const.null(INT)
        self.assertTrue(c.is_bound)
        self.assertTrue(c.is_null)
        self.assertIsNone(c.concrete)
        self.assertEqual(c.type, INT)

    def test_const_none_defaults_to_null(self):
        c = Const(this=None)
        self.assertTrue(c.is_null)
        self.assertTrue(c.is_bound)

    def test_variable_starts_unbound(self):
        v = _variable()
        self.assertFalse(v.is_bound)
        self.assertFalse(v.is_null)
        self.assertIsNone(v.concrete)
        self.assertEqual(v.name, "x")

    def test_variable_bind_value(self):
        v = _variable()
        v.bind(42)
        self.assertTrue(v.is_bound)
        self.assertFalse(v.is_null)
        self.assertEqual(v.concrete, 42)

    def test_variable_bind_null(self):
        v = _variable()
        v.bind_null()
        self.assertTrue(v.is_bound)
        self.assertTrue(v.is_null)
        self.assertIsNone(v.concrete)

    def test_variable_unbind_round_trips(self):
        v = _variable()
        v.bind(7)
        v.unbind()
        self.assertFalse(v.is_bound)
        self.assertIsNone(v.concrete)

    def test_variable_identity_table_column(self):
        v = Variable(
            this="T_0_x",
            type=INT,
            table=exp.to_table("t"),
            column=exp.to_identifier("x"),
            rowid="row_0",
            nullable=False,
            unique=True,
        )
        self.assertEqual(v.table_name, "t")
        self.assertEqual(v.column_name, "x")
        self.assertEqual(v.rowid, "row_0")
        self.assertFalse(v.args.get("nullable"))
        self.assertTrue(v.args.get("unique"))

    def test_variable_accepts_string_table_column(self):
        v = Variable(this="T_0_x", type=INT, table="T", column="x", rowid="row_0")
        self.assertEqual(v.table_name, "T")
        self.assertEqual(v.column_name, "x")

    def test_variable_requires_table_column_rowid(self):
        with self.assertRaises(ValueError):
            Variable(this="x", type=INT, rowid="row_0")
        with self.assertRaises(ValueError):
            Variable(this="x", type=INT, table="t", column="x")

    def test_legacy_underscore_type_kwarg(self):
        c = Const(this=5, _type=INT)
        self.assertEqual(c.type, INT)
        v = Variable(this="x", _type=INT, table="t", column="x", rowid="row_0")
        self.assertEqual(v.type, INT)


# ---------------------------------------------------------------------------
# Const coercion
# ---------------------------------------------------------------------------


class TestConstCoercion(unittest.TestCase):
    def test_identity_coercion_returns_self(self):
        c = Const(this=5, type=INT)
        self.assertIs(c.coerce_to(INT), c)

    def test_int_to_real(self):
        c = Const(this=5, type=INT).coerce_to(REAL)
        self.assertEqual(c.concrete, 5.0)
        self.assertEqual(c.type, REAL)

    def test_int_to_text(self):
        c = Const(this=5, type=INT).coerce_to(TEXT)
        self.assertEqual(c.concrete, "5")
        self.assertEqual(c.type, TEXT)

    def test_text_to_int_success(self):
        c = Const(this="42", type=TEXT).coerce_to(INT)
        self.assertEqual(c.concrete, 42)

    def test_text_to_int_strict_failure_yields_null(self):
        c = Const(this="not-a-number", type=TEXT).coerce_to(INT, dialect="postgres")
        self.assertIsNone(c.concrete)

    def test_text_to_int_lenient_failure_yields_zero(self):
        c = Const(this="not-a-number", type=TEXT).coerce_to(INT, dialect="sqlite")
        self.assertEqual(c.concrete, 0)

    def test_null_const_coerces_to_typed_null(self):
        c = Const.null(INT).coerce_to(TEXT)
        self.assertTrue(c.is_null)
        self.assertEqual(c.type, TEXT)

    def test_bool_to_int(self):
        self.assertEqual(Const(this=True, type=BOOL).coerce_to(INT).concrete, 1)
        self.assertEqual(Const(this=False, type=BOOL).coerce_to(INT).concrete, 0)

    def test_date_coerces_from_string(self):
        c = Const(this="2024-01-15", type=TEXT).coerce_to(DATE_T)
        self.assertEqual(c.concrete, date(2024, 1, 15))


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class TestEnvironment(unittest.TestCase):
    def test_from_row_resolves_column(self):
        env = Environment.from_row({exp.to_identifier("x"): 5})
        self.assertEqual(env.resolve(exp.column("x")), 5)

    def test_from_row_string_helper(self):
        env = _env({"x": 7})
        self.assertEqual(env.resolve(_value("x")), 7)

    def test_outer_scope_chain(self):
        outer = Environment.from_row({exp.to_identifier("y"): 100})
        inner = outer.extend(row={exp.to_identifier("x"): 10})
        self.assertEqual(inner.resolve(exp.column("x")), 10)
        self.assertEqual(inner.resolve(exp.column("y")), 100)

    def test_inner_shadowing(self):
        outer = Environment.from_row({exp.to_identifier("x"): 100})
        inner = outer.extend(row={exp.to_identifier("x"): 1})
        self.assertEqual(inner.resolve(exp.column("x")), 1)

    def test_unresolved_returns_none(self):
        env = _env({"x": 5})
        self.assertIsNone(env.resolve(exp.column("y")))

    def test_contains(self):
        outer = Environment.from_row({exp.to_identifier("y"): 1})
        inner = outer.extend(row={exp.to_identifier("x"): 2})
        self.assertTrue(inner.contains(exp.column("x")))
        self.assertTrue(inner.contains(exp.column("y")))
        self.assertFalse(inner.contains(exp.column("z")))

    def test_bind_mutates_this_scope_only(self):
        outer = Environment.from_row({exp.to_identifier("y"): 1})
        inner = outer.extend()
        inner.bind(exp.to_identifier("z"), 3)
        self.assertEqual(inner.resolve(exp.column("z")), 3)
        self.assertFalse(outer.contains(exp.column("z")))

    def test_from_assignments_solver_var(self):
        sv = SolverVar(key="v1", dtype=INT)
        env = Environment.from_assignments({sv: 5})
        self.assertEqual(concrete(sv, env), 5)

    def test_solver_var_unassigned_is_none(self):
        sv = SolverVar(key="v1", dtype=INT)
        self.assertIsNone(concrete(sv, Environment.from_assignments({})))


# ---------------------------------------------------------------------------
# concrete() — literals + columns
# ---------------------------------------------------------------------------


class TestConcreteLiterals(unittest.TestCase):
    def test_integer_literal(self):
        self.assertEqual(concrete(_value("5")), 5)

    def test_float_literal(self):
        self.assertEqual(concrete(_value("3.14")), 3.14)

    def test_string_literal(self):
        self.assertEqual(concrete(_value("'hello'")), "hello")

    def test_null_literal(self):
        self.assertIsNone(concrete(_value("NULL")))

    def test_boolean_literals(self):
        self.assertTrue(concrete(_value("TRUE")))
        self.assertFalse(concrete(_value("FALSE")))

    def test_column_resolution(self):
        env = _env({"x": 42})
        self.assertEqual(concrete(_value("x"), env), 42)

    def test_column_unresolved_is_none(self):
        self.assertIsNone(concrete(_value("missing"), Environment()))


# ---------------------------------------------------------------------------
# concrete() — arithmetic
# ---------------------------------------------------------------------------


class TestConcreteArithmetic(unittest.TestCase):
    def test_add(self):
        self.assertEqual(concrete(_value("1 + 2")), 3)

    def test_sub(self):
        self.assertEqual(concrete(_value("10 - 3")), 7)

    def test_mul(self):
        self.assertEqual(concrete(_value("4 * 5")), 20)

    def test_div(self):
        self.assertEqual(concrete(_value("10 / 2")), 5.0)

    def test_div_by_zero_returns_none(self):
        self.assertIsNone(concrete(_value("10 / 0")))

    def test_mod(self):
        self.assertEqual(concrete(_value("10 % 3")), 1)

    def test_neg(self):
        self.assertEqual(concrete(_value("-5")), -5)

    def test_add_propagates_null_from_column(self):
        env = _env({"x": None})
        self.assertIsNone(concrete(_value("x + 1"), env))

    def test_add_with_column(self):
        env = _env({"x": 5})
        self.assertEqual(concrete(_value("x + 3"), env), 8)


# ---------------------------------------------------------------------------
# concrete() — comparison with NULL propagation
# ---------------------------------------------------------------------------


class TestConcreteComparisons(unittest.TestCase):
    def test_eq(self):
        self.assertTrue(concrete(_predicate("1 = 1")))
        self.assertFalse(concrete(_predicate("1 = 2")))

    def test_neq(self):
        self.assertTrue(concrete(_predicate("1 <> 2")))

    def test_gt_gte_lt_lte(self):
        self.assertTrue(concrete(_predicate("3 > 2")))
        self.assertTrue(concrete(_predicate("3 >= 3")))
        self.assertTrue(concrete(_predicate("2 < 3")))
        self.assertTrue(concrete(_predicate("3 <= 3")))

    def test_null_propagation_through_inequality(self):
        env = _env({"x": None})
        self.assertIsNone(concrete(_predicate("x > 5"), env))

    def test_null_eq_literal_is_unknown(self):
        env = _env({"x": None})
        self.assertIsNone(concrete(_predicate("x = 5"), env))

    def test_both_null_eq_is_unknown(self):
        left = SolverVar(key="a", dtype=INT)
        right = SolverVar(key="b", dtype=INT)
        env = Environment.from_assignments({left: None, right: None})
        self.assertIsNone(concrete(exp.EQ(this=left, expression=right), env))

    def test_numeric_string_coercion(self):
        env = _env({"x": "42"})
        self.assertTrue(concrete(_predicate("x = 42"), env))

    def test_incomparable_values_return_none_or_false(self):
        env = _env({"x": "abc"})
        result = concrete(_predicate("x > 5"), env)
        self.assertIn(result, (None, False))


# ---------------------------------------------------------------------------
# Three-valued logic
# ---------------------------------------------------------------------------


class TestThreeValuedLogic(unittest.TestCase):
    def test_tvl_and_truth_table(self):
        self.assertTrue(tvl_and(True, True))
        self.assertFalse(tvl_and(True, False))
        self.assertFalse(tvl_and(False, True))
        self.assertFalse(tvl_and(False, False))
        self.assertIsNone(tvl_and(True, None))
        self.assertFalse(tvl_and(False, None))
        self.assertFalse(tvl_and(None, False))
        self.assertIsNone(tvl_and(None, True))
        self.assertIsNone(tvl_and(None, None))

    def test_tvl_or_truth_table(self):
        self.assertTrue(tvl_or(True, False))
        self.assertTrue(tvl_or(False, True))
        self.assertFalse(tvl_or(False, False))
        self.assertTrue(tvl_or(True, None))
        self.assertTrue(tvl_or(None, True))
        self.assertIsNone(tvl_or(False, None))
        self.assertIsNone(tvl_or(None, None))

    def test_tvl_not(self):
        self.assertFalse(tvl_not(True))
        self.assertTrue(tvl_not(False))
        self.assertIsNone(tvl_not(None))

    def test_concrete_honors_tvl_for_logical_connectives(self):
        env = _env({"x": None})
        self.assertTrue(concrete(_predicate("1 = 1 OR x > 0"), env))
        self.assertFalse(concrete(_predicate("1 = 2 AND x > 0"), env))
        self.assertIsNone(concrete(_predicate("x > 0 AND 1 = 1"), env))

    def test_null_comparison_yields_unknown(self):
        env = _env({"x": None})
        self.assertIsNone(concrete(_predicate("x = 'Alameda'"), env))
        self.assertIsNone(concrete(_predicate("x != 'Alameda'"), env))


# ---------------------------------------------------------------------------
# NULL checks
# ---------------------------------------------------------------------------


class TestNullChecks(unittest.TestCase):
    def test_is_null_true(self):
        env = _env({"x": None})
        self.assertTrue(concrete(_predicate("x IS NULL"), env))

    def test_is_null_false_when_bound(self):
        env = _env({"x": 5})
        self.assertFalse(concrete(_predicate("x IS NULL"), env))

    def test_is_not_null_true_when_bound(self):
        env = _env({"x": 5})
        self.assertTrue(concrete(_predicate("x IS NOT NULL"), env))

    def test_is_not_null_false_when_null(self):
        env = _env({"x": None})
        self.assertFalse(concrete(_predicate("x IS NOT NULL"), env))

    def test_constructed_is_not_null_true_when_bound(self):
        col = _value("x")
        pred = exp.Is(this=col, expression=exp.Not(this=exp.Null()))
        env = _env({"x": 5})
        self.assertTrue(concrete(pred, env))

    def test_constructed_is_not_null_false_when_null(self):
        col = _value("x")
        pred = exp.Is(this=col, expression=exp.Not(this=exp.Null()))
        env = _env({"x": None})
        self.assertFalse(concrete(pred, env))


# ---------------------------------------------------------------------------
# Conditional
# ---------------------------------------------------------------------------


class TestConditional(unittest.TestCase):
    def test_case_when_simple(self):
        env = _env({"x": 5})
        value = concrete(_value("CASE WHEN x > 0 THEN 'pos' ELSE 'neg' END"), env)
        self.assertEqual(value, "pos")

    def test_case_when_multiple_arms(self):
        env = _env({"x": 0})
        sql = "CASE WHEN x > 0 THEN 'pos' WHEN x < 0 THEN 'neg' ELSE 'zero' END"
        self.assertEqual(concrete(_value(sql), env), "zero")

    def test_case_falls_through_to_null_without_default(self):
        env = _env({"x": -1})
        self.assertIsNone(concrete(_value("CASE WHEN x > 0 THEN 'pos' END"), env))

    def test_coalesce_first_non_null(self):
        env = _env({"x": None, "y": 7})
        self.assertEqual(concrete(_value("COALESCE(x, y, 99)"), env), 7)

    def test_coalesce_all_null(self):
        env = _env({"x": None, "y": None})
        self.assertIsNone(concrete(_value("COALESCE(x, y)"), env))

    def test_nullif_matching(self):
        env = _env({"x": 5})
        self.assertIsNone(concrete(_value("NULLIF(x, 5)"), env))

    def test_nullif_not_matching(self):
        env = _env({"x": 5})
        self.assertEqual(concrete(_value("NULLIF(x, 7)"), env), 5)


# ---------------------------------------------------------------------------
# Membership
# ---------------------------------------------------------------------------


class TestMembership(unittest.TestCase):
    def test_between_inclusive(self):
        env = _env({"x": 5})
        self.assertTrue(concrete(_predicate("x BETWEEN 1 AND 10"), env))
        self.assertTrue(concrete(_predicate("x BETWEEN 5 AND 5"), env))
        self.assertFalse(concrete(_predicate("x BETWEEN 6 AND 10"), env))

    def test_between_null_value_is_null(self):
        env = _env({"x": None})
        self.assertIsNone(concrete(_predicate("x BETWEEN 1 AND 10"), env))

    def test_in_list_match(self):
        env = _env({"x": 2})
        self.assertTrue(concrete(_predicate("x IN (1, 2, 3)"), env))

    def test_in_list_no_match(self):
        env = _env({"x": 99})
        self.assertFalse(concrete(_predicate("x IN (1, 2, 3)"), env))

    def test_in_null_value(self):
        env = _env({"x": None})
        self.assertIsNone(concrete(_predicate("x IN (1, 2, 3)"), env))

    def test_in_with_null_candidate_and_no_match_yields_null(self):
        env = _env({"x": 99})
        self.assertIsNone(concrete(_predicate("x IN (1, 2, NULL)"), env))


# ---------------------------------------------------------------------------
# Strings
# ---------------------------------------------------------------------------


class TestStrings(unittest.TestCase):
    def test_concat(self):
        env = _env({"a": "hello", "b": "world"})
        self.assertEqual(concrete(_value("CONCAT(a, ' ', b)"), env), "hello world")

    def test_substring(self):
        env = _env({"s": "abcdef"})
        self.assertEqual(concrete(_value("SUBSTRING(s, 2, 3)"), env), "bcd")

    def test_length(self):
        env = _env({"s": "abc"})
        self.assertEqual(concrete(_value("LENGTH(s)"), env), 3)

    def test_upper_lower(self):
        env = _env({"s": "AbC"})
        self.assertEqual(concrete(_value("UPPER(s)"), env), "ABC")
        self.assertEqual(concrete(_value("LOWER(s)"), env), "abc")

    def test_like_percent_wildcard(self):
        env = _env({"s": "hello"})
        self.assertTrue(concrete(_predicate("s LIKE 'he%'"), env))
        self.assertFalse(concrete(_predicate("s LIKE 'wo%'"), env))

    def test_like_null_propagation(self):
        env = _env({"s": None})
        self.assertIsNone(concrete(_predicate("s LIKE 'a%'"), env))


# ---------------------------------------------------------------------------
# Cast
# ---------------------------------------------------------------------------


class TestCast(unittest.TestCase):
    def test_cast_int_to_text(self):
        self.assertEqual(concrete(_value("CAST(42 AS TEXT)")), "42")

    def test_cast_text_to_int(self):
        self.assertEqual(concrete(_value("CAST('42' AS INT)")), 42)

    def test_cast_null_stays_null(self):
        self.assertIsNone(concrete(_value("CAST(NULL AS INT)")))


# ---------------------------------------------------------------------------
# negate_predicate
# ---------------------------------------------------------------------------


class TestNegatePredicate(unittest.TestCase):
    def test_negates_is_null_to_is_not_null(self):
        col = _value("x")
        negated = negate_predicate(exp.Is(this=col, expression=exp.Null()))
        self.assertTrue(concrete(negated, _env({"x": 5})))
        self.assertFalse(concrete(negated, _env({"x": None})))

    def test_negates_is_not_null_to_is_null(self):
        col = _value("x")
        negated = negate_predicate(
            exp.Is(this=col, expression=exp.Not(this=exp.Null()))
        )
        self.assertTrue(concrete(negated, _env({"x": None})))
        self.assertFalse(concrete(negated, _env({"x": 5})))

    def test_negates_parsed_is_not_null_to_is_null(self):
        negated = negate_predicate(_predicate("x IS NOT NULL"))
        self.assertTrue(concrete(negated, _env({"x": None})))
        self.assertFalse(concrete(negated, _env({"x": 5})))

    def test_negates_general_predicate(self):
        pred = _predicate("x > 5")
        negated = negate_predicate(pred)
        env = _env({"x": 4})
        self.assertTrue(concrete(negated, env))


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


class TestRealisticExpressions(unittest.TestCase):
    def test_filter_with_and_or_null(self):
        env = _env({"a": 3, "b": None})
        pred = _predicate("(a > 0 AND b IS NULL) OR a = 10")
        self.assertTrue(concrete(pred, env))

    def test_case_with_column_arithmetic(self):
        env = _env({"a": 5, "b": 3})
        sql = "CASE WHEN a > b THEN a - b WHEN a = b THEN 0 ELSE b - a END"
        self.assertEqual(concrete(_value(sql), env), 2)

    def test_coalesce_chain_with_nulls(self):
        env = _env({"a": None, "b": None, "c": 42})
        self.assertEqual(concrete(_value("COALESCE(a, b, c, 99)"), env), 42)

    def test_variable_embedded_in_expression(self):
        v = _variable()
        v.bind(10)
        expr = exp.GT(this=v, expression=exp.Literal.number(5))
        self.assertTrue(concrete(expr))

    def test_unbound_variable_propagates_as_null(self):
        v = _variable()
        expr = exp.GT(this=v, expression=exp.Literal.number(5))
        self.assertIsNone(concrete(expr))

    def test_solver_var_arithmetic(self):
        sv = SolverVar(key="n", dtype=INT)
        env = Environment.from_assignments({sv: 10})
        expr = exp.Add(this=sv, expression=exp.Literal.number(3))
        self.assertEqual(concrete(expr, env), 13)


def test_like_pattern_cached():
    from unittest.mock import patch

    import parseval.plan.rex as rex_module
    from parseval.plan.rex import _like

    result1 = _like("hello", "%ell%", case_insensitive=False)
    assert result1 is True

    with patch.object(rex_module, "like_to_pattern") as mock_compile:
        _like("world", "%ell%", case_insensitive=False)
        mock_compile.assert_not_called()


if __name__ == "__main__":
    unittest.main()
