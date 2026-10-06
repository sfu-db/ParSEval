"""An immutable concrete value paired with an expression in the existing IR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from parseval.terms.sorts import ScalarSort, ScalarType

if TYPE_CHECKING:
    from .expression import ZExpr


@dataclass(frozen=True, slots=True, eq=False)
class ZValue:
    """A runtime-produced observation; its inputs and result are already checked."""

    expression: ZExpr
    concrete: object

    @property
    def sort(self) -> ScalarSort:
        return self.expression.sort

    @property
    def runtime(self):
        return self.expression.runtime

    def evaluate(self, assignments=None):
        """Reexecute the expression; never mutate this observed value."""
        return self.expression.evaluate(assignments)

    def same_expression(self, other):
        return isinstance(other, ZValue) and self.expression.same_as(other.expression)

    def _binary(self, operation, other, *, reverse=False):
        other = self.runtime.coerce(other, hint=self.sort.sql_type)
        arguments = (other, self) if reverse else (self, other)
        return self.runtime.apply(operation, *arguments)

    def __eq__(self, other):
        return self._binary("eq", other)

    def __ne__(self, other):
        return self._binary("ne", other)

    def __lt__(self, other):
        return self._binary("lt", other)

    def __le__(self, other):
        return self._binary("le", other)

    def __gt__(self, other):
        return self._binary("gt", other)

    def __ge__(self, other):
        return self._binary("ge", other)

    def __bool__(self):
        raise TypeError(
            "Python truth coercion loses the expression; use &, |, and ~ for predicates, or inspect .concrete"
        )

    __hash__ = None

    def is_null(self):
        return self.runtime.apply("is_null", self)

    def is_not_null(self):
        return self.runtime.apply("is_not_null", self)

    def is_not_distinct_from(self, other):
        return self._binary("is_not_distinct", other)

    def cast(self, sql_type: ScalarType):
        return self.runtime.cast(self, sql_type)
