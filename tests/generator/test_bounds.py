from __future__ import annotations

import unittest

from parseval.generator import BmcBounds


class TestBmcBounds(unittest.TestCase):
    def test_default_profile(self):
        bounds = BmcBounds()

        self.assertEqual(1, bounds.table_rows)
        self.assertEqual(1, bounds.join_width)
        self.assertEqual(1, bounds.groups)
        self.assertEqual(1, bounds.rows_per_group)
        self.assertEqual(1, bounds.subquery_rows)
        self.assertEqual(0, bounds.order_competitors)
        self.assertEqual(4, bounds.max_iterations)
        self.assertEqual(512, bounds.max_table_rows)

    def test_deterministic_expansion_order(self):
        bounds = BmcBounds()

        expanded = [bounds]
        for _ in range(6):
            expanded.append(expanded[-1].expand_next())

        self.assertEqual(2, expanded[1].subquery_rows)
        self.assertEqual(2, expanded[2].table_rows)
        self.assertEqual(2, expanded[3].join_width)
        self.assertEqual(2, expanded[4].rows_per_group)
        self.assertEqual(2, expanded[5].groups)
        self.assertEqual(1, expanded[6].order_competitors)

    def test_exhaustion_returns_bounded_unknown(self):
        bounds = BmcBounds(max_iterations=0)

        self.assertTrue(bounds.exhausted)
        self.assertEqual("bounded_unknown", bounds.exhaustion_status)

    def test_raise_table_rows_floors_under_cap(self):
        bounds = BmcBounds(table_rows=1, max_table_rows=64)

        raised, reason = bounds.raise_table_rows(5)

        self.assertEqual("", reason)
        self.assertEqual(5, raised.table_rows)

    def test_raise_table_rows_exceeds_cap(self):
        bounds = BmcBounds(table_rows=1, max_table_rows=10)

        raised, reason = bounds.raise_table_rows(11)

        self.assertIs(bounds, raised)
        self.assertEqual("structural_exceeds_cap:required=11,max=10", reason)

    def test_expand_next_clamps_table_rows_to_cap(self):
        bounds = BmcBounds(table_rows=64, max_table_rows=64, iteration=1)

        expanded = bounds.expand_next()

        self.assertEqual(64, expanded.table_rows)
        self.assertEqual(2, expanded.iteration)


if __name__ == "__main__":
    unittest.main()
