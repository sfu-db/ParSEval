from __future__ import annotations

import unittest
from datetime import date, datetime

from sqlglot import exp

from parseval.dtype import DataType, StorageLiteral
from parseval.solver.csp import CspBackend
from parseval.solver.types import Problem, SolverVar


def var(key: str, dtype: str | DataType) -> SolverVar:
    return SolverVar(key=key, dtype=DataType.build(dtype))


def text(value: str) -> exp.Literal:
    return exp.Literal.string(value)


def number(value: str | int | float) -> exp.Literal:
    return exp.Literal.number(value)


def both(left: exp.Expression, right: exp.Expression) -> exp.And:
    return exp.And(this=left, expression=right)


class CspBackendTests(unittest.TestCase):
    def test_seed_spaces_uses_all_problem_variables(self):
        explicit = var("c.explicit", "INT")
        constrained = var("a.constrained", "INT")
        left = var("b.left", "INT")
        right = var("d.right", "INT")
        problem = Problem(
            constraints=[
                exp.GT(this=constrained, expression=number(5)),
            ],
            equalities=[(left, right)],
            variables={explicit},
        )

        spaces = CspBackend()._seed_spaces(problem)

        self.assertEqual(
            [variable.var_key for variable in spaces],
            ["a.constrained", "b.left", "c.explicit", "d.right"],
        )

    def test_decimal_strict_interval_combines_bounds_and_assignment_check(self):
        x = var("t.x", "FLOAT")
        constraint = both(
            exp.GT(this=x, expression=number("1.25")),
            exp.LT(this=x, expression=number("1.26")),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        value = result.assignments[x]
        self.assertGreater(value, 1.25)
        self.assertLess(value, 1.26)

    def test_datetime_strict_interval_combines_bounds_and_assignment_check(self):
        ts = var("t.ts", "DATETIME")
        lower = "2024-01-01 00:00:00"
        upper = "2024-01-01 00:00:01"
        constraint = both(
            exp.GT(this=ts, expression=text(lower)),
            exp.LT(this=ts, expression=text(upper)),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        value = result.assignments[ts]
        self.assertIsInstance(value, datetime)
        self.assertGreater(value, datetime.fromisoformat(lower))
        self.assertLess(value, datetime.fromisoformat(upper))

    def test_date_literal_flagged_numeric_does_not_crash_support_check(self):
        observed = var("t.observed", "DATE")
        constraint = exp.LT(
            this=observed,
            expression=exp.Literal.number("2019-12-27"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertIn(result.status, {"sat", "unknown"})

    def test_datetime_equality_preserves_slash_separated_fractional_literal_for_storage(self):
        created = var("t.CreationDate", "DATETIME")
        constraint = exp.EQ(
            this=created,
            expression=text("2014/4/23 20:29:39.0"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertIsInstance(result.assignments[created], StorageLiteral)
        self.assertEqual(result.assignments[created], "2014/4/23 20:29:39.0")

    def test_datetime_storage_literal_equality_participates_in_temporal_ranges(self):
        created = var("t.CreationDate", "DATETIME")
        constraint = both(
            exp.EQ(
                this=created,
                expression=text("2014/4/23 20:29:39.0"),
            ),
            both(
                exp.GT(this=created, expression=text("2014-04-23 00:00:00")),
                exp.LT(this=created, expression=text("2014-04-24 00:00:00")),
            ),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertEqual(result.assignments[created], "2014/4/23 20:29:39.0")

    def test_datetime_storage_literal_equality_rejects_conflicting_range(self):
        created = var("t.CreationDate", "DATETIME")
        constraint = both(
            exp.EQ(
                this=created,
                expression=text("2014/4/23 20:29:39.0"),
            ),
            exp.GT(this=created, expression=text("2014-04-24 00:00:00")),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "unsat")

    def test_enum_var_rejects_value_outside_declared_domain(self):
        status = var("t.status", "ENUM('open', 'closed')")
        constraint = exp.EQ(this=status, expression=text("archived"))

        result = CspBackend().solve(Problem(constraints=[constraint], variables={status}))

        self.assertEqual(result.status, "unsat")

    def test_enum_var_picks_declared_value_after_narrowing(self):
        status = var("t.status", "ENUM('open', 'closed')")
        constraint = exp.NEQ(this=status, expression=text("open"))

        result = CspBackend().solve(Problem(constraints=[constraint], variables={status}))

        self.assertEqual(result.status, "sat")
        self.assertEqual(result.assignments[status], "closed")

    def test_year_function_predicate_generates_matching_datetime(self):
        created = var("t.created", "DATETIME")
        constraint = exp.GT(
            this=exp.Year(this=created),
            expression=number(2024),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertGreater(result.assignments[created].year, 2024)

    def test_strftime_year_predicate_generates_matching_datetime(self):
        created = var("t.created", "DATETIME")
        constraint = exp.GT(
            this=exp.TimeToStr(
                this=exp.TsOrDsToTimestamp(this=created),
                format=text("%Y"),
            ),
            expression=text("2024"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertGreater(result.assignments[created].year, 2024)

    def test_strftime_year_with_cast_as_text_peels_to_date(self):
        opened = var("schools.opendate", "DATE")
        constraint = exp.GT(
            this=exp.TimeToStr(
                this=exp.Cast(this=opened, to=DataType.build("TEXT")),
                format=text("%Y"),
            ),
            expression=text("1991"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertGreater(result.assignments[opened].year, 1991)

    def test_anonymous_strftime_year_with_cast_as_text(self):
        closed = var("schools.closeddate", "DATE")
        constraint = exp.LT(
            this=exp.Anonymous(
                this="strftime",
                expressions=[
                    text("%Y"),
                    exp.Cast(this=closed, to=DataType.build("TEXT")),
                ],
            ),
            expression=text("2000"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertLess(result.assignments[closed].year, 2000)

    def test_temporal_component_conflicts_with_full_datetime_bound(self):
        created = var("t.created", "DATETIME")
        constraint = both(
            exp.EQ(this=exp.Year(this=created), expression=number(2024)),
            exp.LT(this=created, expression=text("2024-01-01 00:00:00")),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "unsat")

    def test_month_function_in_predicate_generates_matching_datetime(self):
        created = var("t.created", "DATETIME")
        constraint = exp.In(
            this=exp.Month(this=created),
            expressions=[number(1), number(2)],
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertIn(result.assignments[created].month, {1, 2})

    def test_date_function_equality_generates_datetime_on_that_date(self):
        created = var("t.created", "DATETIME")
        constraint = exp.EQ(
            this=exp.Date(this=created),
            expression=text("2024-03-10"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertEqual(result.assignments[created].date(), date(2024, 3, 10))

    def test_unsupported_temporal_udf_returns_unknown(self):
        created = var("t.created", "DATETIME")
        constraint = exp.GT(
            this=exp.Week(this=created),
            expression=number(1),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "unknown")

    def test_date_add_day_predicate_shifts_bound_back_to_source_datetime(self):
        created = var("t.created", "DATETIME")
        constraint = exp.GT(
            this=exp.DateAdd(
                this=created,
                expression=number(1),
                unit=exp.Var(this="DAY"),
            ),
            expression=text("2024-01-02 00:00:00"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertGreater(result.assignments[created], datetime(2024, 1, 1))

    def test_date_sub_day_equality_shifts_target_forward_to_source_datetime(self):
        created = var("t.created", "DATETIME")
        constraint = exp.EQ(
            this=exp.DateSub(
                this=created,
                expression=exp.Interval(this=text("2"), unit=exp.Var(this="DAY")),
            ),
            expression=text("2024-01-01 00:00:00"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertEqual(result.assignments[created], datetime(2024, 1, 3))

    def test_date_add_week_conflict_with_source_bound_is_unsat(self):
        created = var("t.created", "DATETIME")
        constraint = both(
            exp.EQ(
                this=exp.DateAdd(
                    this=created,
                    expression=number(1),
                    unit=exp.Var(this="WEEK"),
                ),
                expression=text("2024-01-08 00:00:00"),
            ),
            exp.GT(this=created, expression=text("2024-01-01 00:00:00")),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "unsat")

    def test_date_add_month_remains_unknown_for_calendar_arithmetic(self):
        created = var("t.created", "DATETIME")
        constraint = exp.LT(
            this=exp.DateAdd(
                this=created,
                expression=number(1),
                unit=exp.Var(this="MONTH"),
            ),
            expression=text("2024-03-01"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "unknown")

    def test_date_add_on_date_column_preserves_date_assignment_type(self):
        created = var("t.created", "DATE")
        constraint = exp.EQ(
            this=exp.DateAdd(
                this=created,
                expression=number(1),
                unit=exp.Var(this="DAY"),
            ),
            expression=text("2024-01-02"),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertEqual(result.assignments[created], date(2024, 1, 1))

    def test_bare_boolean_predicates_assign_truth_values(self):
        flag = var("t.flag", "BOOLEAN")

        positive = CspBackend().solve(Problem(constraints=[flag]))
        negative = CspBackend().solve(Problem(constraints=[exp.Not(this=flag)]))

        self.assertEqual(positive.status, "sat")
        self.assertIs(positive.assignments[flag], True)
        self.assertEqual(negative.status, "sat")
        self.assertIs(negative.assignments[flag], False)

    def test_bare_non_boolean_predicate_is_unknown(self):
        name = var("t.name", "TEXT")

        result = CspBackend().solve(Problem(constraints=[name]))

        self.assertEqual(result.status, "unknown")

    def test_not_like_is_unknown_for_smt_fallback(self):
        name = var("t.name", "TEXT")
        constraint = exp.Not(
            this=exp.Like(this=name, expression=text("A%")),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "unknown")

    def test_varchar_metadata_constrains_like_assignment_length(self):
        dtype = DataType.build("VARCHAR")
        dtype.set("length", 3)
        name = SolverVar(key="t.name", dtype=dtype)
        constraint = exp.Like(this=name, expression=text("ABC%"))

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertLessEqual(len(result.assignments[name]), 3)
        self.assertTrue(result.assignments[name].startswith("ABC"))

    def test_sqlite_like_assignment_allows_ascii_case_variant(self):
        name = var("t.name", "TEXT")
        constraint = exp.Like(this=name, expression=text("Legal%"))

        result = CspBackend(dialect="sqlite").solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertTrue(result.assignments[name].lower().startswith("legal"))

    def test_variable_inequality_uses_finite_search_when_domains_are_bounded(self):
        left = var("t.left", "INT")
        right = var("t.right", "INT")
        constraint = both(
            both(
                exp.In(this=left, expressions=[number(1), number(2), number(3)]),
                exp.In(this=right, expressions=[number(1), number(2), number(3)]),
            ),
            exp.LT(this=left, expression=right),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertLess(result.assignments[left], result.assignments[right])

    def test_in_null_assigns_none(self):
        value = var("t.value", "TEXT")
        constraint = exp.In(this=value, expressions=[exp.Null()])

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertIsNone(result.assignments[value])

    def test_not_in_null_assigns_non_null_value(self):
        value = var("t.value", "TEXT")
        constraint = exp.Not(this=exp.In(this=value, expressions=[exp.Null()]))

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertIsNotNone(result.assignments[value])

    def test_in_null_mixed_with_exclusion_can_choose_none(self):
        value = var("t.value", "TEXT")
        constraint = both(
            exp.In(this=value, expressions=[exp.Null(), text("A")]),
            exp.NEQ(this=value, expression=text("A")),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertIsNone(result.assignments[value])

    def test_in_null_conflicts_with_not_null_dtype(self):
        dtype = DataType.build("TEXT")
        dtype.set("nullable", False)
        value = SolverVar(key="t.value", dtype=dtype)
        constraint = exp.In(this=value, expressions=[exp.Null()])

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "unsat")

    def test_variable_inequality_without_finite_domains_is_unknown(self):
        left = var("t.left", "INT")
        right = var("t.right", "INT")

        result = CspBackend().solve(
            Problem(constraints=[exp.LT(this=left, expression=right)])
        )

        self.assertEqual(result.status, "unknown")

    def test_composite_solver_var_disequality_or_is_unknown_for_smt_fallback(self):
        left_id = var("left.id", "INT")
        right_id = var("right.id", "INT")
        left_date = var("left.date", "DATE")
        right_date = var("right.date", "DATE")
        constraint = exp.Or(
            this=exp.NEQ(this=left_id, expression=right_id),
            expression=exp.NEQ(this=left_date, expression=right_date),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.reason, "complex_disjunction")

    def test_small_simple_or_still_branches_in_csp(self):
        amount = var("orders.amount", "INT")
        constraint = exp.Or(
            this=exp.EQ(this=amount, expression=number(7)),
            expression=exp.EQ(this=amount, expression=number(9)),
        )

        result = CspBackend().solve(Problem(constraints=[constraint]))

        self.assertEqual(result.status, "sat")
        self.assertIn(result.assignments[amount], {7, 9})

    def test_large_or_branch_budget_returns_unknown(self):
        amount = var("orders.amount", "INT")
        constraints = [
            exp.Or(
                this=exp.EQ(this=amount, expression=number(index * 2)),
                expression=exp.EQ(this=amount, expression=number(index * 2 + 1)),
            )
            for index in range(5)
        ]

        result = CspBackend().solve(Problem(constraints=constraints))

        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.reason, "complex_disjunction")

    def test_text_var_equals_numeric_literal_assigns_text_form(self):
        nces = var("schools.ncesdist", "TEXT")
        result = CspBackend().solve(
            Problem(
                constraints=[exp.EQ(this=nces, expression=number(613360))],
                variables={nces},
            )
        )

        self.assertEqual(result.status, "sat")
        self.assertEqual(result.assignments[nces], "613360")

    def test_int_var_equals_unparseable_text_literal_is_unknown(self):
        amount = var("orders.amount", "INT")
        result = CspBackend().solve(
            Problem(
                constraints=[exp.EQ(this=amount, expression=text("not-a-number"))],
                variables={amount},
            )
        )

        self.assertEqual(result.status, "unknown")


if __name__ == "__main__":
    unittest.main()
