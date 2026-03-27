import unittest
from datetime import date, datetime, time

import z3
from sqlglot import exp
from sqlglot.expressions import DataType

from parseval.solver.smt import (
    SMTSolver,
    SMTValue,
    SpecialFunctionModel,
    UnsupportedSMTError,
    _to_z3val,
    encode_literal,
    is_option_expr,
    register_special_function,
)


def column(table: str, name: str, dtype: str) -> exp.Column:
    node = exp.column(name, table=table)
    node.type = DataType.build(dtype)
    return node


def number(value, dtype: str = "INT") -> exp.Literal:
    node = exp.Literal.number(value)
    node.args["_type"] = DataType.build(dtype)
    return node


def text(value: str) -> exp.Literal:
    node = exp.Literal.string(value)
    node.args["_type"] = DataType.build("TEXT")
    return node


def typed_literal(value: str, dtype: str) -> exp.Literal:
    node = exp.Literal.string(value)
    node.args["_type"] = DataType.build(dtype)
    return node


def null() -> exp.Null:
    node = exp.Null()
    node.args["_type"] = DataType.build("NULL")
    return node


class SMTSolverTestCase(unittest.TestCase):
    def make_solver(self, function_models=None) -> SMTSolver:
        return SMTSolver([], function_models=function_models)

    def solve_expr(self, expression, function_models=None):
        solver = self.make_solver(function_models=function_models)
        solver.add(solver._to_z3_expr(expression))
        return solver.solve()


class TestSMTSolverSupportedConstraints(SMTSolverTestCase):
    def test_integer_comparison_generates_model(self):
        sat, model = self.solve_expr(
            exp.GT(this=column("users", "age", "INT"), expression=number(18))
        )

        self.assertEqual("sat", sat)
        self.assertGreater(model["users.age"], 18)

    def test_real_comparison_generates_real_value(self):
        sat, model = self.solve_expr(
            exp.GTE(
                this=column("scores", "rating", "FLOAT"),
                expression=number(1.5, "FLOAT"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertGreaterEqual(model["scores.rating"], 1.5)

    def test_logical_and_or_not_constraints(self):
        age = column("users", "age", "INT")
        name = column("users", "name", "TEXT")
        predicate = exp.And(
            this=exp.GT(this=age, expression=number(18)),
            expression=exp.Or(
                this=exp.LT(this=age.copy(), expression=number(30)),
                expression=exp.Not(this=exp.EQ(this=name, expression=text("blocked"))),
            ),
        )

        sat, model = self.solve_expr(predicate)

        self.assertEqual("sat", sat)
        self.assertGreater(model["users.age"], 18)

    def test_is_null_is_supported_with_typed_null(self):
        sat, model = self.solve_expr(
            exp.Is(this=column("users", "age", "INT"), expression=null())
        )

        self.assertEqual("sat", sat)
        self.assertIn("users.age", model)
        self.assertIsNone(model["users.age"])

    def test_eq_null_is_unsat_under_current_where_semantics(self):
        sat, model = self.solve_expr(
            exp.EQ(this=column("users", "age", "INT"), expression=null())
        )

        self.assertEqual("unsat", sat)
        self.assertEqual({}, model)

    def test_distinct_is_supported_for_multiple_columns(self):
        predicate = exp.Distinct(
            expressions=[
                column("users", "left_id", "INT"),
                column("users", "right_id", "INT"),
            ]
        )

        sat, model = self.solve_expr(predicate)

        self.assertEqual("sat", sat)
        self.assertNotEqual(model["users.left_id"], model["users.right_id"])

    def test_parenthesized_predicate_is_unwrapped(self):
        predicate = exp.Paren(
            this=exp.LTE(this=column("users", "age", "INT"), expression=number(5))
        )

        sat, model = self.solve_expr(predicate)

        self.assertEqual("sat", sat)
        self.assertLessEqual(model["users.age"], 5)


class TestSMTSolverExpandedCoverage(SMTSolverTestCase):
    def test_like_translation_supports_literal_patterns(self):
        sat, model = self.solve_expr(
            exp.Like(
                this=column("users", "name", "TEXT"),
                expression=text("ab_"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertTrue(model["users.name"].startswith("ab"))
        self.assertEqual(3, len(model["users.name"]))

    def test_length_can_participate_in_comparisons(self):
        sat, model = self.solve_expr(
            exp.GT(
                this=exp.Length(this=column("users", "name", "TEXT")),
                expression=number(2),
            )
        )

        self.assertEqual("sat", sat)
        self.assertGreater(len(model["users.name"]), 2)

    def test_abs_can_participate_in_comparisons(self):
        sat, model = self.solve_expr(
            exp.GTE(
                this=exp.Abs(this=column("users", "delta", "INT")),
                expression=number(10),
            )
        )

        self.assertEqual("sat", sat)
        self.assertGreaterEqual(abs(model["users.delta"]), 10)

    def test_cast_preserves_operand_value(self):
        sat, model = self.solve_expr(
            exp.EQ(
                this=exp.Cast(
                    this=column("users", "age", "INT"),
                    to=DataType.build("INT"),
                ),
                expression=number(7),
            )
        )

        self.assertEqual("sat", sat)
        self.assertEqual(7, model["users.age"])

    def test_untyped_null_literal_is_accepted_for_is_null(self):
        sat, model = self.solve_expr(
            exp.Is(
                this=column("users", "age", "INT"),
                expression=exp.Null(),
            )
        )

        self.assertEqual("sat", sat)
        self.assertIsNone(model["users.age"])

    def test_between_is_supported(self):
        sat, model = self.solve_expr(
            exp.Between(
                this=column("users", "age", "INT"),
                low=number(1),
                high=number(5),
            )
        )

        self.assertEqual("sat", sat)
        self.assertGreaterEqual(model["users.age"], 1)
        self.assertLessEqual(model["users.age"], 5)

    def test_in_is_supported(self):
        sat, model = self.solve_expr(
            exp.In(
                this=column("users", "age", "INT"),
                expressions=[number(1), number(2)],
            )
        )

        self.assertEqual("sat", sat)
        self.assertIn(model["users.age"], {1, 2})

    def test_add_is_supported(self):
        sat, model = self.solve_expr(
            exp.EQ(
                this=exp.Add(this=column("users", "age", "INT"), expression=number(1)),
                expression=number(7),
            )
        )

        self.assertEqual("sat", sat)
        self.assertEqual(6, model["users.age"])

    def test_sub_is_supported(self):
        sat, model = self.solve_expr(
            exp.GT(
                this=exp.Sub(this=column("users", "age", "INT"), expression=number(2)),
                expression=number(0),
            )
        )

        self.assertEqual("sat", sat)
        self.assertGreater(model["users.age"] - 2, 0)

    def test_mul_is_supported(self):
        sat, model = self.solve_expr(
            exp.GTE(
                this=exp.Mul(
                    this=column("items", "price", "FLOAT"),
                    expression=number(2, "FLOAT"),
                ),
                expression=number(3.0, "FLOAT"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertGreaterEqual(model["items.price"] * 2, 3.0)

    def test_mod_is_supported(self):
        sat, model = self.solve_expr(
            exp.EQ(
                this=exp.Mod(this=column("users", "age", "INT"), expression=number(2)),
                expression=number(1),
            )
        )

        self.assertEqual("sat", sat)
        self.assertEqual(1, model["users.age"] % 2)

    def test_substr_is_supported(self):
        sat, model = self.solve_expr(
            exp.EQ(
                this=exp.Substring(
                    this=column("users", "name", "TEXT"),
                    start=number(1),
                    length=number(2),
                ),
                expression=text("ab"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertEqual("ab", model["users.name"][:2])

    def test_instr_is_supported(self):
        expr = exp.EQ(
            this=exp.Anonymous(
                this="INSTR",
                expressions=[column("users", "name", "TEXT"), text("x")],
            ),
            expression=number(2),
        )
        sat, model = self.solve_expr(expr)

        self.assertEqual("sat", sat)
        self.assertEqual(1, model["users.name"].find("x"))

    def test_strftime_year_is_supported(self):
        expr = exp.EQ(
            this=exp.TimeToStr(
                this=column("events", "created_at", "DATETIME"),
                format=text("%Y"),
            ),
            expression=text("2024"),
        )
        sat, model = self.solve_expr(expr)

        self.assertEqual("sat", sat)
        self.assertEqual(2024, model["events.created_at"].year)


class TestSMTSolverDatatypeBehavior(SMTSolverTestCase):
    def test_temporal_guardrails_are_enforced_in_final_model(self):
        sat, model = self.solve_expr(
            exp.GTE(
                this=column("events", "created_at", "DATE"),
                expression=number(0, "INT"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertGreater(model["events.created_at"], date(1970, 1, 1))

    def test_string_guardrails_are_enforced_in_final_model(self):
        sat, model = self.solve_expr(
            exp.EQ(
                this=column("users", "name", "TEXT"),
                expression=text("abc"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertEqual("abc", model["users.name"])

    def test_string_guardrails_reject_leading_space(self):
        sat, model = self.solve_expr(
            exp.EQ(
                this=column("users", "name", "TEXT"),
                expression=text(" leading-space"),
            )
        )

        self.assertEqual("unsat", sat)
        self.assertEqual({}, model)

    def test_null_is_supported_across_type_families(self):
        for dtype in ["INT", "FLOAT", "BOOLEAN", "TEXT", "DATE", "TIME", "DATETIME"]:
            with self.subTest(dtype=dtype):
                sat, model = self.solve_expr(
                    exp.Is(this=column("t", dtype.lower(), dtype), expression=exp.Null())
                )
                self.assertEqual("sat", sat)
                self.assertIsNone(model[f"t.{dtype.lower()}"])

    def test_date_roundtrip_returns_date(self):
        sat, model = self.solve_expr(
            exp.EQ(
                this=column("events", "event_date", "DATE"),
                expression=typed_literal("2024-01-03", "DATE"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertEqual(date(2024, 1, 3), model["events.event_date"])

    def test_time_roundtrip_returns_time(self):
        sat, model = self.solve_expr(
            exp.EQ(
                this=column("events", "event_time", "TIME"),
                expression=typed_literal("12:34:56", "TIME"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertEqual(time(12, 34, 56), model["events.event_time"])

    def test_datetime_roundtrip_returns_datetime(self):
        sat, model = self.solve_expr(
            exp.EQ(
                this=column("events", "created_at", "DATETIME"),
                expression=typed_literal("2024-01-03 04:05:06", "DATETIME"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertEqual(datetime(2024, 1, 3, 4, 5, 6), model["events.created_at"])

    def test_float_and_boolean_literals_use_option_sorts(self):
        self.assertTrue(is_option_expr(_to_z3val(DataType.build("FLOAT"), 1.5)))
        self.assertTrue(is_option_expr(_to_z3val(DataType.build("BOOLEAN"), True)))

    def test_option_sort_registry_is_context_aware(self):
        left_ctx = z3.Context()
        right_ctx = z3.Context()
        left = _to_z3val(DataType.build("INT"), 1, z3ctx=left_ctx)
        right = _to_z3val(DataType.build("INT"), 1, z3ctx=right_ctx)

        self.assertTrue(is_option_expr(left))
        self.assertTrue(is_option_expr(right))
        self.assertNotEqual(left.sort().ctx, right.sort().ctx)


class TestSMTSolverExtensions(SMTSolverTestCase):
    def test_global_custom_function_registration_works(self):
        def translate_doubleit(solver, _expression, args):
            arg = solver._as_value(args[0])
            two = encode_literal(DataType.build("INT"), 2, solver.z3ctx)
            return solver._nullable_numeric_binary(arg, two, lambda a, b: a * b)

        register_special_function("DOUBLEIT", translate_doubleit)

        sat, model = self.solve_expr(
            exp.EQ(
                this=exp.Anonymous(
                    this="DOUBLEIT",
                    expressions=[column("users", "age", "INT")],
                ),
                expression=number(8),
            )
        )

        self.assertEqual("sat", sat)
        self.assertEqual(4, model["users.age"])

    def test_per_solver_override_can_replace_builtin_model(self):
        def translate_length_to_zero(solver, _expression, _args):
            return solver._wrap_payload(z3.IntVal(0, ctx=solver.z3ctx), DataType.build("INT"))

        override = SpecialFunctionModel(
            name="LENGTH",
            translator=translate_length_to_zero,
            return_type=lambda _expr, _args: DataType.build("INT"),
        )

        sat, model = self.solve_expr(
            exp.EQ(
                this=exp.Length(this=column("users", "name", "TEXT")),
                expression=number(0),
            ),
            function_models={"LENGTH": override},
        )

        self.assertEqual("sat", sat)
        self.assertNotIn("users.name", model)

    def test_unsupported_custom_function_raises_structured_error(self):
        solver = self.make_solver()
        expr = exp.Anonymous(
            this="MISSINGFUNC",
            expressions=[column("users", "age", "INT")],
        )

        with self.assertRaises(UnsupportedSMTError):
            solver._to_z3_expr(expr)


class TestSMTSolverCurrentLimitations(SMTSolverTestCase):
    def test_unconstrained_declared_variable_is_ignored_in_model_output(self):
        solver = self.make_solver()
        age = column("users", "age", "INT")
        name = column("users", "name", "TEXT")

        solver._to_z3_expr(age)
        solver._to_z3_expr(name)
        solver.add(solver._to_z3_expr(exp.GT(this=age, expression=number(18))))

        sat, model = solver.solve()

        self.assertEqual("sat", sat)
        self.assertIn("users.age", model)
        self.assertNotIn("users.name", model)


if __name__ == "__main__":
    unittest.main(verbosity=2)
