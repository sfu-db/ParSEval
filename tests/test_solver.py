import unittest
from datetime import datetime

from sqlglot import exp
from sqlglot.expressions import DataType
from parseval.solver.smt import SMTSolver


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


def null() -> exp.Null:
    node = exp.Null()
    node.args["_type"] = DataType.build("NULL")
    return node


class SMTSolverTestCase(unittest.TestCase):
    def make_solver(self) -> SMTSolver:
        return SMTSolver([])

    def solve_expr(self, expression):
        solver = self.make_solver()
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


class TestSMTSolverCurrentLimitations(SMTSolverTestCase):
    def test_unconstrained_declared_variable_is_ignored_in_model_output(self):
        solver = self.make_solver()
        age = column("users", "age", "INT")
        name = column("users", "name", "TEXT")

        # Declare both variables in the solver context, but constrain only one.
        solver._to_z3_expr(age)
        solver._to_z3_expr(name)
        solver.add(solver._to_z3_expr(exp.GT(this=age, expression=number(18))))

        sat, model = solver.solve()

        self.assertEqual("sat", sat)
        self.assertIn("users.age", model)
        self.assertNotIn("users.name", model)

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

    def test_temporal_guardrails_are_enforced_in_final_model(self):
        sat, model = self.solve_expr(
            exp.GTE(
                this=column("events", "created_at", "DATE"),
                expression=number(0, "INT"),
            )
        )

        self.assertEqual("sat", sat)
        self.assertGreater(model["events.created_at"], datetime(1970, 1, 1))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
