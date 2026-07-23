from __future__ import annotations

import unittest
from datetime import date, datetime

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

    def test_decimal_pick_searches_beyond_endpoints(self):
        """FLOAT/DECIMAL must not only try default lo/hi endpoints."""
        space = ValueSpace(family=TypeFamily.DECIMAL)
        space.not_null = True
        space.narrow_neq(1)
        space.narrow_neq(101)

        value = space.pick()

        self.assertIsNotNone(value)
        self.assertNotIn(value, {1, 101})
        self.assertGreaterEqual(value, 1)
        self.assertLessEqual(value, 101)

    def test_decimal_strict_interval_has_valid_pick(self):
        space = ValueSpace(family=TypeFamily.DECIMAL)
        space.narrow_min(1.25, inclusive=False)
        space.narrow_max(1.26, inclusive=False)

        value = space.pick()

        self.assertIsNotNone(value)
        self.assertGreater(value, 1.25)
        self.assertLess(value, 1.26)

    def test_decimal_equal_bound_with_exclusive_side_is_empty(self):
        space = ValueSpace(family=TypeFamily.DECIMAL)
        space.narrow_min(1.25, inclusive=False)
        space.narrow_max(1.25)

        self.assertTrue(space.is_empty())
        self.assertIsNone(space.pick())

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

    def test_text_max_length_respected_with_equality_like_and_hint(self):
        equal_space = ValueSpace(family=TypeFamily.TEXT, max_length=3)
        equal_space.narrow_eq("abcd")
        self.assertTrue(equal_space.is_empty())

        like_space = ValueSpace(family=TypeFamily.TEXT, like_pattern="AB%", max_length=2)
        self.assertEqual(like_space.pick(), "AB")

        hint_space = ValueSpace(family=TypeFamily.TEXT, max_length=3)
        hint_value = hint_space.pick(hint="abcdef")
        self.assertIsNotNone(hint_value)
        self.assertLessEqual(len(hint_value), 3)

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

    def test_sqlite_like_accepts_ascii_case_variant(self):
        space = ValueSpace(family=TypeFamily.TEXT)
        space.like_pattern = "Legal%"
        space.like_case_insensitive = True

        self.assertTrue(space._candidate_valid("legal_case"))

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

    def test_datetime_strict_lower_bound_picks_greater_value(self):
        lower = datetime(2024, 1, 1, 0, 0, 0)
        space = ValueSpace(family=TypeFamily.DATETIME)
        space.narrow_min(lower, inclusive=False)

        value = space.pick()

        self.assertIsInstance(value, datetime)
        self.assertGreater(value, lower)

    def test_must_null_and_not_null_is_empty(self):
        space = ValueSpace(family=TypeFamily.TEXT)
        space.must_null = True
        space.not_null = True

        self.assertTrue(space.is_empty())
        self.assertIsNone(space.pick())


if __name__ == "__main__":
    unittest.main()
