from __future__ import annotations

import unittest

from sqlglot import exp, parse_one

from parseval.generator.budget import GenerationBudget
from parseval.generator.config import GenerationConfig
from parseval.generator.symbolic.operator import (
    GroupDemand,
    _AtomicRowRequest,
    _aggregate_expression_map,
    _aggregate_group_key_expression_demands,
    _expand_group_key_expression_demands,
    _expression_key,
    _group_demand_for_having,
    _projection_expression_map,
    _solve_atomic_row_requests,
)
from parseval.instance import Instance
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

    def test_group_keys_compile_to_solver_relations_without_fixed_values(self):
        upper = expression("CASE WHEN status = 'Legal' THEN 1 ELSE 0 END")
        lower = expression("CASE WHEN status = 'legal' THEN 1 ELSE 0 END")
        aggregate = Aggregate()
        aggregate.group = [upper, lower]
        groups = (
            GroupDemand(group_index=0, row_count=2),
            GroupDemand(group_index=1, row_count=3),
        )

        logical = _aggregate_group_key_expression_demands(
            aggregate,
            groups,
            "sqlite",
        )
        expanded = _expand_group_key_expression_demands(logical, groups)

        self.assertTrue(all(demand.value is None for demand in expanded))
        for source in (upper, lower):
            origin = f"group_key:{source.sql(dialect='sqlite')}"
            distinct = [
                demand.rank
                for demand in expanded
                if demand.kind == "distinct" and demand.origin == origin
            ]
            cohorts = {
                demand.origin: []
                for demand in expanded
                if demand.kind == "equal" and demand.origin.startswith(origin)
            }
            for demand in expanded:
                if demand.origin in cohorts:
                    cohorts[demand.origin].append(demand.rank)
            self.assertEqual([0, 2], distinct)
            self.assertEqual([[0, 1], [2, 3, 4]], sorted(cohorts.values()))

    def test_having_uses_query_threshold_as_solver_constraint(self):
        aggregate = Aggregate()
        total = expression("SUM(points)")
        aggregate.aggregations = [total]
        condition = exp.GT(this=total.copy(), expression=exp.Literal.number("10"))

        passing = _group_demand_for_having(
            condition,
            aggregate,
            group_index=0,
            default_row_count=3,
            pass_group=True,
            dialect="sqlite",
        )
        failing = _group_demand_for_having(
            condition,
            aggregate,
            group_index=1,
            default_row_count=3,
            pass_group=False,
            dialect="sqlite",
        )

        self.assertIsNotNone(passing)
        self.assertIsNotNone(failing)
        self.assertEqual("points > 10", passing.row_predicates[0].sql(dialect="sqlite"))
        self.assertEqual("points <= 10", failing.row_predicates[0].sql(dialect="sqlite"))

    def test_solver_selects_group_key_values_from_relational_demands(self):
        instance = Instance(
            "CREATE TABLE scores (region TEXT)",
            name="solver_group_keys",
            dialect="sqlite",
        )
        table = instance.resolve_table("scores")
        aggregate = Aggregate()
        aggregate.group = [exp.column("region")]
        groups = (
            GroupDemand(group_index=0, row_count=2),
            GroupDemand(group_index=1, row_count=1),
        )
        logical = _aggregate_group_key_expression_demands(
            aggregate,
            groups,
            "sqlite",
        )
        relational = _expand_group_key_expression_demands(logical, groups)
        request = _AtomicRowRequest(
            table=table,
            row_specs=({}, {}, {}),
            predicates=((), (), ()),
            expression_demands=relational,
        )

        result, decoded, _problem = _solve_atomic_row_requests(
            instance,
            (request,),
            dialect="sqlite",
            budget=GenerationBudget(GenerationConfig()),
        )

        self.assertEqual("sat", result.status, result.reason)
        values = [row["region"] for row in decoded[0]]
        self.assertEqual(values[0], values[1])
        self.assertNotEqual(values[0], values[2])


if __name__ == "__main__":
    unittest.main()
