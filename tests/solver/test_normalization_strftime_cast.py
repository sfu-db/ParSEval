from __future__ import annotations

import unittest

from sqlglot import exp

from parseval.dtype import DataType
from parseval.solver.normalization import normalize_expression, unwrap_planning_temporal_arg
from parseval.solver.types import SolverVar


def var(key: str, dtype: str) -> SolverVar:
    return SolverVar(key=key, dtype=DataType.build(dtype))


def text(value: str) -> exp.Literal:
    return exp.Literal.string(value)


class NormalizeStrftimeCastTests(unittest.TestCase):
    def test_unwrap_peels_cast_as_text(self):
        opened = var("schools.opendate", "DATE")
        casted = exp.Cast(this=opened, to=DataType.build("TEXT"))
        self.assertIs(unwrap_planning_temporal_arg(casted), opened)

    def test_normalize_strftime_year_gt_with_cast_lowers_to_date_bound(self):
        opened = var("schools.opendate", "DATE")
        predicate = exp.GT(
            this=exp.TimeToStr(
                this=exp.Cast(this=opened, to=DataType.build("TEXT")),
                format=text("%Y"),
            ),
            expression=text("1991"),
        )

        lowered = normalize_expression(predicate)

        self.assertIsInstance(lowered, (exp.GTE, exp.GT))
        self.assertIsInstance(lowered.this, SolverVar)
        self.assertEqual(lowered.this.var_key, opened.var_key)
        self.assertEqual(str(lowered.expression.this), "1992-01-01")

    def test_normalize_alias_wrapped_strftime_cast_lowers_to_date_bound(self):
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

        lowered = normalize_expression(predicate)

        self.assertIsInstance(lowered, (exp.GTE, exp.GT))
        self.assertIsInstance(lowered.this, SolverVar)
        self.assertEqual(lowered.this.var_key, opened.var_key)
        self.assertEqual(str(lowered.expression.this), "1992-01-01")

    def test_normalize_alias_wrapped_text_variable_keeps_original_variable(self):
        name = var("schools.school", "TEXT")
        predicate = exp.GTE(
            this=exp.Alias(this=name, alias=exp.to_identifier("__common_expr_5")),
            expression=exp.Literal.number(2014),
        )

        lowered = normalize_expression(predicate)

        self.assertIsInstance(lowered, exp.GTE)
        self.assertIsInstance(lowered.this, SolverVar)
        self.assertEqual(lowered.this.var_key, name.var_key)

    def test_normalize_does_not_rewrite_unresolved_alias_column(self):
        predicate = exp.GT(
            this=exp.column("__common_expr_5"),
            expression=text("1991"),
        )

        lowered = normalize_expression(predicate)

        self.assertIsInstance(lowered.this, exp.Column)
        self.assertEqual(lowered.this.name, "__common_expr_5")


if __name__ == "__main__":
    unittest.main()
