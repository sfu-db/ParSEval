from __future__ import annotations

import unittest

from sqlglot import exp, parse_one

from parseval.generator.symbolic.operator import (
    GroupDemand,
    _aggregate_expression_map,
    _ensure_aggregate_group_key_values,
    _expression_key,
    _projection_expression_map,
)
from parseval.plan.context import DerivedSchema
from parseval.plan.explain import Aggregate, Projection


def expression(sql: str) -> exp.Expression:
    return parse_one(sql, dialect="sqlite")


class TestSymbolicOperatorKeys(unittest.TestCase):
    def test_expression_key_preserves_string_literal_case(self):
        upper = expression("SUM(CASE WHEN name = 'Legal' THEN 1 ELSE 0 END)")
        lower = expression("SUM(CASE WHEN name = 'legal' THEN 1 ELSE 0 END)")

        self.assertNotEqual(
            _expression_key(upper, "sqlite"),
            _expression_key(lower, "sqlite"),
        )

    def test_expression_key_normalizes_identifier_case(self):
        left = expression("SUM(CASE WHEN NAME = 'Legal' THEN 1 ELSE 0 END)")
        right = expression("sum(case when name = 'Legal' then 1 else 0 end)")

        self.assertEqual(
            _expression_key(left, "sqlite"),
            _expression_key(right, "sqlite"),
        )

    def test_projection_expression_map_keeps_literal_case_variants_distinct(self):
        upper = expression("CASE WHEN status = 'Legal' THEN 1 ELSE 0 END")
        lower = expression("CASE WHEN status = 'legal' THEN 1 ELSE 0 END")
        projection = Projection()
        projection.projections = [
            exp.alias_(upper, "upper_status"),
            exp.alias_(lower, "lower_status"),
        ]
        output_schema = DerivedSchema(
            columns=(exp.column("upper_status"), exp.column("lower_status")),
        )

        mapping = _projection_expression_map(projection, output_schema, "sqlite")

        self.assertEqual(
            mapping[_expression_key(upper, "sqlite")].sql(dialect="sqlite"),
            upper.sql(dialect="sqlite"),
        )
        self.assertEqual(
            mapping[_expression_key(lower, "sqlite")].sql(dialect="sqlite"),
            lower.sql(dialect="sqlite"),
        )

    def test_aggregate_expression_map_keeps_literal_case_variants_distinct(self):
        upper = expression("SUM(CASE WHEN status = 'Legal' THEN 1 ELSE 0 END)")
        lower = expression("SUM(CASE WHEN status = 'legal' THEN 1 ELSE 0 END)")
        aggregate = Aggregate()
        aggregate.aggregations = [upper, lower]

        mapping = _aggregate_expression_map(aggregate, "sqlite")

        self.assertIs(mapping[_expression_key(upper, "sqlite")], upper)
        self.assertIs(mapping[_expression_key(lower, "sqlite")], lower)

    def test_group_key_values_keep_literal_case_variants_distinct(self):
        upper = expression("CASE WHEN status = 'Legal' THEN 1 ELSE 0 END")
        lower = expression("CASE WHEN status = 'legal' THEN 1 ELSE 0 END")
        aggregate = Aggregate()
        aggregate.group = [upper, lower]
        demand = GroupDemand(
            group_index=0,
            row_count=1,
            group_key_values=((upper, 1),),
        )

        normalized = _ensure_aggregate_group_key_values(
            aggregate,
            (demand,),
            "sqlite",
        )

        self.assertEqual(len(normalized[0].group_key_values), 2)


if __name__ == "__main__":
    unittest.main()
