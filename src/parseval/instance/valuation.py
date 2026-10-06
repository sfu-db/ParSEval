"""Partial evaluation of Terms against the values stored in an instance.

Inputs listed as *open* remain symbolic; every other input is replaced by its
stored value. Each constructed node whose inputs are all closed is folded into
a literal, so the Terms that reach the solver mention only open inputs.
Multiplicities are non-NULL INTEGER Terms, and the U-semiring operations are
expressed with ordinary scalar operations: ``‖m‖ = CASE WHEN m > 0 THEN 1
ELSE 0`` and ``not(m) = CASE WHEN m = 0 THEN 1 ELSE 0``.
"""

from __future__ import annotations

from collections import ChainMap
from collections.abc import Mapping
from dataclasses import dataclass
from functools import reduce

from parseval.symbolic import ZValue
from parseval.terms import terms as nodes
from parseval.terms.builder import IRBuilder
from parseval.terms.names import ParameterId
from parseval.terms.sorts import BOOLEAN, FLOAT, INTEGER, PREDICATE, ScalarSort, ScalarType
from parseval.terms.terms import TermId

from .model import Instance

_CONSTANTS = (nodes.Literal, nodes.Null, nodes.True3, nodes.False3, nodes.Unknown3)
_TRUTH = {nodes.True3: True, nodes.False3: False, nodes.Unknown3: None}


@dataclass(frozen=True, slots=True)
class Failure:
    """A SQL runtime error, such as division by zero, observed as a value."""

    reason: str


class ExecutionError(Exception):
    """The query raises a SQL runtime error on the stored data."""


class Valuation:
    """Construct folded Terms and evaluate them under an instance's values."""

    def __init__(
        self,
        instance: Instance,
        open: frozenset[ParameterId] = frozenset(),
        binary: frozenset[ParameterId] = frozenset(),
    ):
        """``binary`` lists open inputs known to be 0 or 1, such as the
        multiplicities of candidate rows; products with them stay linear."""
        self.instance = instance
        self.binary = binary
        self._binary_terms: dict[TermId, bool] = {}
        self._least: dict[TermId, int | None] = {}
        self._most: dict[TermId, int | None] = {}
        self._weights = frozenset(slot.parameters[-1] for slot in instance.all_slots())
        self._inputs: dict[TermId, frozenset[ParameterId]] = {}
        self.runtime = instance.runtime
        self.arena = instance.arena
        self.builder = IRBuilder(self.arena)
        self.open = open
        self._open_terms: dict[TermId, bool] = {}
        self._values: dict[TermId, object] = {}
        self._failures: dict[TermId, Failure] = {}
        self.zero = self.literal(0, INTEGER)
        self.one = self.literal(1, INTEGER)
        self.true = self.builder.true3()
        self.false = self.builder.false3()
        self.excluded = self._stored_keys()

    # Inputs and constants.

    def input(self, value: ZValue) -> TermId:
        """An instance input: its Term if open, otherwise its stored value."""
        term = value.expression.root
        if self.arena[term].payload.parameter in self.open:
            return term
        return self.literal(self.instance.value(value), value.sort.sql_type)

    def literal(self, value: object, sql_type: ScalarType) -> TermId:
        return self.builder.resolve(self.builder.literal(value, sql_type))

    def truth(self, value: bool | None) -> TermId:
        b = self.builder
        return b.resolve(b.true3() if value is True else b.false3() if value is False else b.unknown3())

    def constant(self, term: TermId) -> bool:
        return isinstance(self.arena[term], _CONSTANTS)

    # Partial evaluation.

    def is_open(self, term: TermId) -> bool:
        known = self._open_terms
        pending = [term]
        while pending:
            current = pending[-1]
            if current in known:
                pending.pop()
                continue
            node = self.arena[current]
            missing = [child for child in node.children if child not in known]
            if missing:
                pending.extend(missing)
                continue
            pending.pop()
            known[current] = (
                node.payload.parameter in self.open
                if isinstance(node, nodes.ExternalParameter)
                else any(known[child] for child in node.children)
            )
        return known[term]

    def value(self, term: TermId) -> object:
        """The concrete value of a scalar or predicate Term, or a Failure."""
        node = self.arena[term]
        if isinstance(node, nodes.Literal):
            return node.payload.value
        if isinstance(node, nodes.Null):
            return None
        if type(node) in _TRUTH:
            return _TRUTH[type(node)]
        if term in self._failures:
            return self._failures[term]
        root = self.builder.resolve(self.builder.to_boolean(term)) if node.sort == PREDICATE else term
        try:
            return self.runtime.evaluate(root, self.instance.values, self._values)
        except (ArithmeticError, ValueError) as error:
            # SQL runtime errors are values here; only an observer that needs
            # the result decides whether the error is real for the query.
            failure = self._failures[term] = Failure(f"{type(error).__name__}: {error}")
            return failure

    def inputs(self, term: TermId) -> frozenset[ParameterId]:
        """The open inputs a Term mentions."""
        known = self._inputs
        pending = [term]
        while pending:
            current = pending[-1]
            if current in known:
                pending.pop()
                continue
            node = self.arena[current]
            missing = [child for child in node.children if child not in known]
            if missing:
                pending.extend(missing)
                continue
            pending.pop()
            if isinstance(node, nodes.ExternalParameter):
                parameter = node.payload.parameter
                known[current] = frozenset((parameter,)) if parameter in self.open else frozenset()
            else:
                known[current] = frozenset().union(*(known[child] for child in node.children))
        return known[term]

    def trial(self, term: TermId, values: Mapping[ParameterId, object]) -> object:
        """The value of a Term if some open inputs took ``values``, or a Failure."""
        node = self.arena[term]
        root = self.builder.resolve(self.builder.to_boolean(term)) if node.sort == PREDICATE else term
        try:
            return self.runtime.evaluate(root, ChainMap(values, self.instance.values))
        except (ArithmeticError, ValueError) as error:
            return Failure(f"{type(error).__name__}: {error}")

    def concrete(self, term: TermId) -> object:
        """The value of a Term that the query really evaluates."""
        value = self.value(term)
        if isinstance(value, Failure):
            raise ExecutionError(value.reason)
        return value

    def _stored_keys(self) -> dict[ParameterId, frozenset]:
        """Values each open input never takes: for a cell of a single-column
        key, the values stored in that column.

        A candidate row taking one would break the key if present, and its
        values do not matter if absent. Folding assumes this, the solvers
        exclude these values from the input's domain, and integrity need not
        compare the cell with stored keys.
        """
        from parseval.terms.constraints import PrimaryKeyDecl, UniqueDecl

        instance = self.instance
        taken: dict[ParameterId, frozenset] = {}
        for table in instance.catalog.tables():
            for item in table.constraints:
                if not isinstance(item, (PrimaryKeyDecl, UniqueDecl)) or len(item.columns) != 1:
                    continue
                position = table.spec.column_position(item.columns[0])
                slots = instance.slots(table.relation)
                values = frozenset(
                    instance.row(slot)[position] for slot in slots
                    if instance.multiplicity(slot) and self.open.isdisjoint(slot.parameters)
                ) - {None}
                for slot in slots:
                    if slot.parameters[position] in self.open:
                        taken[slot.parameters[position]] = values
        return taken

    def fold(self, term: TermId) -> TermId:
        if self.constant(term):
            return term
        if self.is_open(term):
            node = self.arena[term]
            if self.excluded and isinstance(node, (nodes.Eq3, nodes.IsNotDistinct)):
                left, right = (self.arena[child] for child in node.children)
                for cell, other in ((left, right), (right, left)):
                    if isinstance(cell, nodes.ExternalParameter) and isinstance(other, nodes.Literal) \
                            and other.payload.value in self.excluded.get(cell.payload.parameter, ()):
                        return self.false
            return term
        value = self.value(term)
        if isinstance(value, Failure):
            return term
        sort = self.arena[term].sort
        if sort == PREDICATE:
            return self.truth(value)
        if value is None:
            return self.builder.resolve(self.builder.null(sort.sql_type))
        return self.literal(value, sort.sql_type)

    def node(self, node_type, children, payload=None) -> TermId:
        return self.fold(self.arena.intern_checked(node_type, tuple(children), payload))

    def apply(self, operator: str, arguments, result: ScalarSort) -> TermId:
        return self.fold(self.builder.resolve(self.builder.apply(operator, tuple(arguments), result)))

    # SQL predicates.

    def and3(self, *predicates: TermId) -> TermId:
        return self._junction(nodes.And3, predicates, self.true)

    def or3(self, *predicates: TermId) -> TermId:
        return self._junction(nodes.Or3, predicates, self.false)

    def _junction(self, node_type, predicates, identity: TermId) -> TermId:
        if not predicates:
            return identity
        if len(predicates) == 1:
            return predicates[0]
        return self.node(node_type, predicates)

    def not3(self, predicate: TermId) -> TermId:
        return self.node(nodes.Not3, (predicate,))

    def eq3(self, left: TermId, right: TermId) -> TermId:
        return self.node(nodes.Eq3, (left, right))

    def lt3(self, left: TermId, right: TermId) -> TermId:
        return self.node(nodes.Lt3, (left, right))

    def is_null(self, value: TermId) -> TermId:
        return self.node(nodes.IsNull, (value,))

    def same(self, left: TermId, right: TermId) -> TermId:
        """IS NOT DISTINCT FROM; identical Terms are always the same value."""
        return self.true if left == right else self.node(nodes.IsNotDistinct, (left, right))

    def defined(self, value: TermId) -> TermId:
        """TRUE exactly when evaluating ``value`` raises no SQL error."""
        return self.node(nodes.IsNotDistinct, (value, value))

    def same_row(self, left: tuple[TermId, ...], right: tuple[TermId, ...]) -> TermId:
        return self.and3(*(self.same(a, b) for a, b in zip(left, right, strict=True)))

    def boolean(self, predicate: TermId) -> TermId:
        return self.fold(self.builder.resolve(self.builder.to_boolean(predicate)))

    def is_true(self, predicate: TermId) -> TermId:
        """TRUE when the predicate is TRUE, otherwise FALSE (never UNKNOWN)."""
        return self.node(nodes.IsNotDistinct, (self.boolean(predicate), self.literal(True, BOOLEAN)))

    def is_false(self, predicate: TermId) -> TermId:
        return self.node(nodes.IsNotDistinct, (self.boolean(predicate), self.literal(False, BOOLEAN)))

    def is_unknown(self, predicate: TermId) -> TermId:
        return self.is_null(self.boolean(predicate))

    def case(self, condition: TermId, then: TermId, otherwise: TermId) -> TermId:
        if self.constant(condition):
            return then if self.value(condition) is True else otherwise
        return self.node(nodes.Case, (condition, then, otherwise))

    # Multiplicities: non-NULL INTEGER Terms.

    def positive(self, multiplicity: TermId) -> TermId:
        if (self.least(multiplicity) or 0) > 0:
            return self.true
        return self.lt3(self.zero, multiplicity)

    def at_least(self, multiplicity: TermId, count: int) -> TermId:
        most = self.most(multiplicity)
        if most is not None and most < count:
            return self.false
        return self.lt3(self.literal(count - 1, INTEGER), multiplicity)

    def indicator(self, predicate: TermId) -> TermId:
        return self.case(predicate, self.one, self.zero)

    def squash(self, multiplicity: TermId) -> TermId:
        return self.indicator(self.positive(multiplicity))

    def unot(self, multiplicity: TermId) -> TermId:
        if (self.least(multiplicity) or 0) > 0:
            return self.zero
        return self.indicator(self.eq3(multiplicity, self.zero))

    def least(self, multiplicity: TermId) -> int | None:
        """The least value a multiplicity Term can take, or None if unknown.

        Multiplicity inputs are never negative, so sums and products of
        multiplicities are bounded below by those of their parts.
        """
        return self._bound(multiplicity, self._least, lower=True)

    def most(self, multiplicity: TermId) -> int | None:
        """The greatest value a multiplicity Term can take, or None if unknown.

        Only inputs known to be 0 or 1 are bounded, such as the
        multiplicities of candidate rows.
        """
        return self._bound(multiplicity, self._most, lower=False)

    def _bound(self, multiplicity: TermId, known: dict, lower: bool) -> int | None:
        if multiplicity in known:
            return known[multiplicity]
        node = self.arena[multiplicity]
        bound = None
        if isinstance(node, nodes.Literal):
            bound = node.payload.value if isinstance(node.payload.value, int) else None
        elif isinstance(node, nodes.ExternalParameter):
            parameter = node.payload.parameter
            bound = (0 if parameter in self._weights else None) if lower else (1 if parameter in self.binary else None)
        elif isinstance(node, nodes.Case):
            condition, then, otherwise = node.children
            arms = [self._bound(arm, known, lower) for arm in (then, otherwise)]
            test = self.arena[condition]
            if isinstance(test, nodes.Lt3) and test.children == ((otherwise, then) if lower else (then, otherwise)):
                # maximum(a, b) is at least each of a, b; minimum(a, b) at most each.
                found = [arm for arm in arms if arm is not None]
                bound = (max if lower else min)(found) if found else None
            elif None not in arms:
                bound = (min if lower else max)(arms)
        elif isinstance(node, nodes.ScalarCall):
            operator = self.arena.context.function(node.payload.function).operator
            if operator in ("add", "mul"):
                parts = [self._bound(child, known, lower) for child in node.children]
                if None not in parts and min(parts) >= 0:
                    bound = sum(parts) if operator == "add" else parts[0] * parts[1]
            elif operator == "sub":
                left, right = self._bound(node.children[0], known, lower), self._bound(
                    node.children[1], self._most if lower else self._least, not lower
                )
                bound = None if left is None or right is None else left - right
        known[multiplicity] = bound
        return bound

    def add(self, *multiplicities: TermId) -> TermId:
        """Balanced sum, so long sums stay shallow."""
        items = [term for term in multiplicities if term != self.zero]
        if not items:
            return self.zero
        while len(items) > 1:
            items = [
                self.apply("add", items[index : index + 2], ScalarSort(INTEGER))
                if index + 1 < len(items) else items[index]
                for index in range(0, len(items), 2)
            ]
        return items[0]

    def mul(self, left: TermId, *rights: TermId) -> TermId:
        """Multiply weights; later operands are not evaluated when earlier ones are zero."""
        return reduce(self._mul, rights, left)

    def _mul(self, left: TermId, right: TermId) -> TermId:
        """Products with a 0/1 factor become CASE, keeping arithmetic linear.

        The left operand guards the right one, which is not evaluated when
        the left is zero.
        """
        if self.zero in (left, right):
            return self.zero
        if left == self.one:
            return right
        if right == self.one:
            return left
        if self.is_binary(left):
            return self.case(self.positive(left), right, self.zero)
        if self.is_binary(right):
            return self.case(self.positive(right), left, self.zero)
        product = self.apply("mul", (left, right), ScalarSort(INTEGER))
        if self.is_open(left):
            return self.case(self.positive(left), product, self.zero)
        return product

    def scale(self, weight: TermId, value: TermId, sort: ScalarSort) -> TermId:
        """``weight * value`` for a non-NULL value of ``sort``, linear for 0/1 weights."""
        zero = self.literal(0 if sort.sql_type.kind is INTEGER.kind else 0.0, sort.sql_type)
        if self.is_binary(weight):
            return self.case(self.positive(weight), value, zero)
        factor = weight if sort.sql_type.kind is INTEGER.kind else self.to_float(weight)
        return self.guard(weight, self.apply("mul", (factor, value), sort), zero)

    def is_binary(self, term: TermId) -> bool:
        """Whether a multiplicity Term is always 0 or 1."""
        known = self._binary_terms
        pending = [term]
        while pending:
            current = pending[-1]
            if current in known:
                pending.pop()
                continue
            node = self.arena[current]
            if isinstance(node, nodes.Case):
                arms = [arm for arm in node.children[1:] if arm not in known]
                if arms:
                    pending.extend(arms)
                    continue
                known[current] = known[node.children[1]] and known[node.children[2]]
            elif isinstance(node, nodes.Literal):
                known[current] = node.payload.value in (0, 1)
            elif isinstance(node, nodes.ExternalParameter):
                known[current] = node.payload.parameter in self.binary
            else:
                known[current] = False
            pending.pop()
        return known[term]

    def minimum(self, left: TermId, right: TermId) -> TermId:
        return self.case(self.lt3(left, right), left, right)

    def maximum(self, left: TermId, right: TermId) -> TermId:
        return self.case(self.lt3(left, right), right, left)

    def sub(self, left: TermId, right: TermId) -> TermId:
        return left if right == self.zero else self.apply("sub", (left, right), ScalarSort(INTEGER))

    def guard(self, weight: TermId, value: TermId, default: TermId) -> TermId:
        """``value`` for a present weight, ``default`` without evaluating it otherwise."""
        if weight == self.zero:
            return default
        return self.case(self.positive(weight), value, default)

    def to_float(self, value: TermId) -> TermId:
        sort = self.arena[value].sort
        if sort.sql_type.kind is FLOAT.kind:
            return value
        return self.apply(
            f"cast_{sort.sql_type.kind.value}_to_float", (value,), ScalarSort(FLOAT, sort.nullable)
        )


__all__ = ["ExecutionError", "Failure", "Valuation"]
