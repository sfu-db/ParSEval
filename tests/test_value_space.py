from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
