"""Numeric operations preserve concrete results and typed Term expressions."""

from .base import ZValue


class ZNumber(ZValue):
    __slots__ = ()

    def __add__(self, other):
        return self._binary("add", other)

    def __radd__(self, other):
        return self._binary("add", other, reverse=True)

    def __sub__(self, other):
        return self._binary("sub", other)

    def __rsub__(self, other):
        return self._binary("sub", other, reverse=True)

    def __mul__(self, other):
        return self._binary("mul", other)

    def __rmul__(self, other):
        return self._binary("mul", other, reverse=True)

    def __truediv__(self, other):
        return self._binary("div", other)

    def __rtruediv__(self, other):
        return self._binary("div", other, reverse=True)

    def __mod__(self, other):
        return self._binary("mod", other)

    def __rmod__(self, other):
        return self._binary("mod", other, reverse=True)

    def __neg__(self):
        return self.runtime.apply("neg", self)

    def __pos__(self):
        return self

    def __abs__(self):
        return self.runtime.apply("abs", self)


class ZInt(ZNumber):
    __slots__ = ()


class ZFloat(ZNumber):
    """A FLOAT or DECIMAL value; both use the Python float carrier."""

    __slots__ = ()
