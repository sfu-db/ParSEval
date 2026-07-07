from __future__ import annotations

import unittest
from datetime import date

from parseval.dtype import TypeFamily
from parseval.domain.value_space import ValueSpace


class ValueSpaceTests(unittest.TestCase):
    def test_boolean_exhaustion_is_empty(self):
        space = ValueSpace(family=TypeFamily.BOOLEAN)
        space.narrow_neq(True)
        space.narrow_neq(False)

        self.assertTrue(space.is_empty())
        self.assertIsNone(space.pick())

    def test_numeric_pick_respects_bounds(self):
        space = ValueSpace(family=TypeFamily.INTEGER)
        space.narrow_min(10)
        space.narrow_max(20)

        value = space.pick()

        self.assertGreaterEqual(value, 10)
        self.assertLessEqual(value, 20)

    def test_text_numeric_bounds_pick_numeric_text(self):
        space = ValueSpace(family=TypeFamily.TEXT)
        space.narrow_min(50)
        space.narrow_max(60)

        value = space.pick()

        self.assertIsInstance(value, str)
        self.assertGreaterEqual(int(value), 50)
        self.assertLessEqual(int(value), 60)

    def test_allowed_values_respect_bounds(self):
        space = ValueSpace(family=TypeFamily.INTEGER, allowed={1})
        space.narrow_min(5)

        self.assertTrue(space.is_empty())
        self.assertIsNone(space.pick())

    def test_equal_text_respects_max_length(self):
        space = ValueSpace(family=TypeFamily.TEXT, equals="abcdef", max_length=3)

        self.assertTrue(space.is_empty())
        self.assertIsNone(space.pick())

    def test_like_text_pick_skips_excluded_candidate(self):
        space = ValueSpace(family=TypeFamily.TEXT, like_pattern="A%")
        space.narrow_neq("Ax")

        value = space.pick()

        self.assertIsNotNone(value)
        self.assertNotEqual(value, "Ax")
        self.assertTrue(value.startswith("A"))

    def test_like_text_pick_uses_shortest_percent_match(self):
        space = ValueSpace(family=TypeFamily.TEXT, like_pattern="A%", max_length=1)

        self.assertEqual(space.pick(), "A")

    def test_like_text_pick_varies_underscore_candidate(self):
        space = ValueSpace(family=TypeFamily.TEXT, like_pattern="A_")
        space.narrow_neq("Aa")

        self.assertEqual(space.pick(), "Ab")

    def test_temporal_default_pick_skips_excluded_candidate(self):
        excluded = date(2024, 6, 15)
        space = ValueSpace(family=TypeFamily.DATE)
        space.narrow_neq(excluded)

        value = space.pick()

        self.assertIsNotNone(value)
        self.assertNotEqual(value, excluded)


if __name__ == "__main__":
    unittest.main()
