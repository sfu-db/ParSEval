"""Typed temporal comparisons, casts, and calendar interval arithmetic."""

from .base import ZValue


class ZDate(ZValue):
    __slots__ = ()

    def __add__(self, other):
        return self._binary("add", other)

    def __sub__(self, other):
        return self._binary("sub", other)


class ZTimestamp(ZValue):
    __slots__ = ()

    def __add__(self, other):
        return self._binary("add", other)

    def __sub__(self, other):
        return self._binary("sub", other)


class ZTime(ZValue):
    __slots__ = ()


class ZInterval(ZValue):
    __slots__ = ()

    def __add__(self, other):
        if isinstance(other, (ZDate, ZTimestamp)):
            return other + self
        return self._binary("add", other)

    def __sub__(self, other):
        return self._binary("sub", other)

    def __neg__(self):
        return self.runtime.apply("neg", self)
