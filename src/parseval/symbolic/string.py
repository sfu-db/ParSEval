"""String methods return concolic values instead of implicit Python scalars."""

from .base import ZValue


class ZString(ZValue):
    __slots__ = ()

    def __add__(self, other):
        return self._binary("concat", other)

    def __radd__(self, other):
        return self._binary("concat", other, reverse=True)

    def length(self):
        return self.runtime.apply("length", self)

    def lower(self):
        return self.runtime.apply("lower", self)

    def upper(self):
        return self.runtime.apply("upper", self)

    def like(self, pattern):
        return self._binary("like", pattern)

    def ilike(self, pattern):
        return self._binary("ilike", pattern)

    def contains(self, text):
        return self._binary("contains", text)

    def startswith(self, text):
        return self._binary("startswith", text)

    def endswith(self, text):
        return self._binary("endswith", text)

    def substring(self, start, length=None):
        from parseval.terms.sorts import INTEGER

        arguments = [self, self.runtime.coerce(start, hint=INTEGER)]
        if length is not None:
            arguments.append(self.runtime.coerce(length, hint=INTEGER))
        return self.runtime.apply("substring", *arguments)
