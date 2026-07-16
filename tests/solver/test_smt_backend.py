"""Tests for the active SMT backend Problem API."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlglot import exp

from parseval.dtype import DataType
from parseval.solver import Solver
from parseval.solver.smt import SmtBackend
from parseval.solver.smt_solver import Z3SmtSession
from parseval.solver.smt_types import UnsupportedSMTError, normalize_dtype
from parseval.solver.types import Problem, SolverVar

import z3


def var(key: str, dtype: str) -> SolverVar:
    return SolverVar(key=key, dtype=DataType.build(dtype))


def number(value: int | float, dtype: str = "INT") -> exp.Literal:
    literal = exp.Literal.number(value)
    literal.set("type", DataType.build(dtype))
    return literal


def text(value: str) -> exp.Literal:
    return exp.Literal.string(value)


class TestSmtBackendProblemApi(unittest.TestCase):
    def test_solves_arithmetic_problem_with_solvervar_assignment(self):
        x = var("orders.amount", "INT")
        predicate = exp.GT(
            this=exp.Add(this=x, expression=number(2)),
            expression=number(5),
        )

        result = SmtBackend().solve(Problem(constraints=[predicate], variables={x}))

        self.assertEqual("sat", result.status)
        self.assertIn(x, result.assignments)
        self.assertGreater(result.assignments[x] + 2, 5)

    def test_solver_uses_smt_for_complex_problem_components(self):
        x = var("orders.amount", "INT")
        predicate = exp.EQ(
            this=exp.Case(
                ifs=[
                    exp.If(
                        this=exp.GT(this=x, expression=number(10)),
                        true=number(1),
                    )
                ],
                default=number(0),
            ),
            expression=number(1),
        )

        result = Solver().solve(Problem(constraints=[predicate], variables={x}))

        self.assertEqual("sat", result.status)
        self.assertGreater(result.assignments[x], 10)

    def test_equalities_return_assignments_for_original_solvervars(self):
        left = var("orders.customer_id", "INT")
        right = var("customers.id", "INT")
        predicate = exp.GT(this=left, expression=number(5))

        result = SmtBackend().solve(
            Problem(
                constraints=[predicate],
                equalities=[(left, right)],
                variables={left, right},
            )
        )

        self.assertEqual("sat", result.status)
        self.assertEqual(result.assignments[left], result.assignments[right])
        self.assertGreater(result.assignments[left], 5)

    def test_solves_in_between_not_like_and_temporal_predicates(self):
        amount = var("orders.amount", "INT")
        name = var("orders.name", "TEXT")
        created = var("orders.created_at", "DATETIME")
        predicate = exp.And(
            this=exp.In(
                this=amount,
                expressions=[number(7), number(9), number(11)],
            ),
            expression=exp.And(
                this=exp.Between(
                    this=amount,
                    low=number(5),
                    high=number(10),
                ),
                expression=exp.And(
                    this=exp.Not(
                        this=exp.Like(this=name, expression=text("bad%")),
                    ),
                    expression=exp.EQ(
                        this=exp.Year(this=created),
                        expression=number(2024),
                    ),
                ),
            ),
        )

        result = SmtBackend().solve(
            Problem(
                constraints=[predicate],
                variables={amount, name, created},
            )
        )

        self.assertEqual("sat", result.status)
        self.assertIn(result.assignments[amount], {7, 9})
        self.assertFalse(str(result.assignments[name]).startswith("bad"))
        self.assertEqual(result.assignments[created].year, 2024)

    def test_unsupported_expression_returns_unknown_without_partial_assignment(self):
        x = var("orders.amount", "INT")
        supported = exp.GT(this=x, expression=number(5))
        unsupported = exp.EQ(
            this=exp.Anonymous(this="UNMODELED_FUNCTION", expressions=[x]),
            expression=number(1),
        )

        result = SmtBackend().solve(
            Problem(constraints=[supported, unsupported], variables={x})
        )

        self.assertEqual("unknown", result.status)
        self.assertEqual("unsupported_smt_expression", result.reason)
        self.assertEqual({}, result.assignments)

    def test_sat_status_does_not_depend_on_decoded_assignments(self):
        class EmptyModelSession:
            def __init__(self, *, timeout_ms=None, dialect=None):
                self.context = {"variable_to_z3": {}}

            def declare_variable(self, name, dtype):
                self.context["variable_to_z3"][name] = object()

            def translate(self, expr):
                return object()

            def add(self, constraint):
                return None

            def add_raw(self, constraint):
                return None

            def solve(self):
                return "sat", {}

        x = var("orders.amount", "INT")
        predicate = exp.GT(this=x, expression=number(5))

        with patch("parseval.solver.smt.Z3SmtSession", EmptyModelSession):
            result = SmtBackend().solve(Problem(constraints=[predicate], variables={x}))

        self.assertEqual("sat", result.status)
        self.assertEqual({}, result.assignments)

    def test_translate_raises_for_unsupported_expression(self):
        x = var("orders.amount", "INT")
        unsupported = exp.Anonymous(this="UNMODELED_FUNCTION", expressions=[x])
        solver = Z3SmtSession()

        with self.assertRaises(UnsupportedSMTError):
            solver.translate(unsupported)

    def test_enum_dtype_normalizes_as_text_for_smt(self):
        typeinfo = normalize_dtype(DataType.build("ENUM('open', 'closed')"))

        self.assertEqual(typeinfo.logical_name, "TEXT")
        self.assertEqual(typeinfo.family, "text")
        self.assertEqual(typeinfo.payload_sort, z3.StringSort())

    def test_strftime_cast_as_text_translates_after_peel(self):
        opened = var("schools.opendate", "DATE")
        predicate = exp.GT(
            this=exp.TimeToStr(
                this=exp.Cast(this=opened, to=DataType.build("TEXT")),
                format=text("%Y"),
            ),
            expression=text("1991"),
        )
        solver = Z3SmtSession()
        name = "sv_schools_opendate"
        solver.context["solver_var_to_name"] = {opened: name}
        solver.declare_variable(name, opened.dtype)

        z3_pred = solver.translate(predicate)
        self.assertTrue(z3.is_bool(z3_pred))

    def test_solver_strftime_cast_as_text_year_predicate_sat(self):
        opened = var("schools.opendate", "DATE")
        predicate = exp.GT(
            this=exp.TimeToStr(
                this=exp.Cast(this=opened, to=DataType.build("TEXT")),
                format=text("%Y"),
            ),
            expression=text("1991"),
        )

        result = Solver().solve(Problem(constraints=[predicate], variables={opened}))

        self.assertEqual("sat", result.status)
        self.assertGreater(result.assignments[opened].year, 1991)

    def test_solver_normalizes_alias_wrapped_strftime_cast_before_smt(self):
        opened = var("schools.opendate", "DATE")
        predicate = exp.GT(
            this=exp.Alias(
                this=exp.TimeToStr(
                    this=exp.Cast(this=opened, to=DataType.build("TEXT")),
                    format=text("%Y"),
                ),
                alias=exp.to_identifier("__common_expr_5"),
            ),
            expression=text("1991"),
        )

        result = Solver().solve(Problem(constraints=[predicate], variables={opened}))

        self.assertEqual("sat", result.status, result.reason)
        self.assertGreater(result.assignments[opened].year, 1991)

    def test_solver_normalizes_alias_wrapped_text_comparison_before_smt(self):
        name = var("schools.school", "TEXT")
        predicate = exp.GTE(
            this=exp.Alias(this=name, alias=exp.to_identifier("__common_expr_5")),
            expression=number(2014),
        )

        result = Solver().solve(Problem(constraints=[predicate], variables={name}))

        self.assertIn(result.status, {"sat", "unknown"})
        if result.status == "unknown":
            self.assertNotEqual("unsupported_smt_expression", result.reason)

    def test_solver_returns_unknown_when_smt_cannot_solve_csp_unknown(self):
        x = var("orders.amount", "INT")
        predicate = exp.EQ(
            this=exp.Anonymous(this="UNMODELED_FUNCTION", expressions=[x]),
            expression=number(1),
        )

        result = Solver().solve(Problem(constraints=[predicate], variables={x}))

        self.assertEqual("unknown", result.status)
        self.assertEqual("unsupported_smt_expression", result.reason)
        self.assertEqual({}, result.assignments)

    def test_solver_falls_back_to_smt_for_composite_uniqueness_disjunctions(self):
        ids = [var(f"lab.{index}.id", "INT") for index in range(3)]
        dates = [var(f"lab.{index}.date", "DATE") for index in range(3)]
        constraints = [
            exp.EQ(this=id_var, expression=number(1))
            for id_var in ids
        ]
        for left_index in range(len(ids)):
            for right_index in range(left_index + 1, len(ids)):
                constraints.append(
                    exp.Or(
                        this=exp.NEQ(
                            this=ids[left_index],
                            expression=ids[right_index],
                        ),
                        expression=exp.NEQ(
                            this=dates[left_index],
                            expression=dates[right_index],
                        ),
                    )
                )

        result = Solver().solve(Problem(constraints=constraints, variables=set(ids + dates)))

        self.assertEqual("sat", result.status, result.reason)
        tuples = [
            (result.assignments[id_var], result.assignments[date_var])
            for id_var, date_var in zip(ids, dates)
        ]
        self.assertEqual(3, len(set(tuples)))
        self.assertTrue(all(date_value is not None for _id, date_value in tuples))

    def test_solver_rejects_non_problem_input(self):
        with self.assertRaises(TypeError):
            Solver().solve(object())  # type: ignore[arg-type]

    def test_text_var_equals_numeric_literal_assigns_text_form(self):
        nces = var("schools.ncesdist", "TEXT")
        predicate = exp.EQ(this=nces, expression=number(613360))

        result = SmtBackend().solve(
            Problem(constraints=[predicate], variables={nces})
        )

        self.assertEqual("sat", result.status)
        self.assertEqual("613360", result.assignments[nces])

    def test_int_var_equals_text_literal_assigns_int(self):
        amount = var("orders.amount", "INT")
        predicate = exp.EQ(this=amount, expression=text("42"))

        result = SmtBackend().solve(
            Problem(constraints=[predicate], variables={amount})
        )

        self.assertEqual("sat", result.status)
        self.assertEqual(42, result.assignments[amount])

    def test_int_var_equals_unparseable_text_literal_returns_unknown(self):
        amount = var("orders.amount", "INT")
        predicate = exp.EQ(this=amount, expression=text("not-a-number"))

        result = SmtBackend().solve(
            Problem(constraints=[predicate], variables={amount})
        )

        self.assertEqual("unknown", result.status)
        self.assertEqual("unsupported_smt_expression", result.reason)
        self.assertEqual({}, result.assignments)

    def test_text_var_gt_numeric_literal_does_not_raise_sort_mismatch(self):
        name = var("orders.name", "TEXT")
        predicate = exp.GT(this=name, expression=number(10))

        result = SmtBackend().solve(
            Problem(constraints=[predicate], variables={name})
        )

        self.assertIn(result.status, {"sat", "unknown"})
        if result.status == "unknown":
            self.assertEqual("unsupported_smt_expression", result.reason)

    def test_strftime_year_subtraction_returns_unknown_without_type_error(self):
        started = var("events.started_at", "DATE")
        ended = var("events.ended_at", "DATE")
        year_diff = exp.Sub(
            this=exp.TimeToStr(
                this=exp.Cast(this=started, to=DataType.build("TEXT")),
                format=text("%Y"),
            ),
            expression=exp.TimeToStr(
                this=exp.Cast(this=ended, to=DataType.build("TEXT")),
                format=text("%Y"),
            ),
        )
        predicate = exp.GT(this=year_diff, expression=number(2))

        result = SmtBackend().solve(
            Problem(constraints=[predicate], variables={started, ended})
        )

        self.assertEqual("unknown", result.status)
        self.assertEqual("unsupported_smt_expression", result.reason)
        self.assertEqual({}, result.assignments)


if __name__ == "__main__":
    unittest.main()
