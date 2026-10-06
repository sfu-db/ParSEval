"""SQL Boolean values: True, False, and None (UNKNOWN)."""

from .base import ZValue


class ZBool(ZValue):
    """SQL predicates use &, |, and ~; Python and/or/not use truth coercion."""

    __slots__ = ()

    def sql_and(self, other):
        return self._binary("and", other)

    def sql_or(self, other):
        return self._binary("or", other)

    def sql_not(self):
        return self.runtime.apply("not", self)

    __and__ = sql_and

    def __rand__(self, other):
        return self._binary("and", other, reverse=True)

    __or__ = sql_or

    def __ror__(self, other):
        return self._binary("or", other, reverse=True)

    __invert__ = sql_not

    def is_true(self):
        return self.runtime.apply("is_true", self)

    def is_false(self):
        return self.runtime.apply("is_false", self)

    def is_unknown(self):
        return self.runtime.apply("is_unknown", self)
