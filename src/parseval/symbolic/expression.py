"""Executable views of existing Terms, not a second expression language."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from parseval.terms import terms as nodes
from parseval.terms.arena import TermView
from parseval.terms.sorts import BOOLEAN, ScalarSort

from .operations import execute
from .semantics import validate

if TYPE_CHECKING:
    from .runtime import Runtime


@dataclass(frozen=True, slots=True)
class ZExpr(TermView):
    """A typed scalar Term view bound to its concrete execution runtime."""

    runtime: Runtime

    def __post_init__(self):
        if self.arena is not self.runtime.arena:
            raise ValueError("ZExpr and runtime must share an arena")
        if not isinstance(self.arena[self.root].sort, ScalarSort):
            raise TypeError("A concolic expression must have a ScalarSort")

    @property
    def sort(self):
        return self.arena[self.root].sort

    @property
    def inputs(self):
        return frozenset(
            self.arena[term].payload.parameter
            for term in self.arena.post_order((self.root,))
            if isinstance(self.arena[term], nodes.ExternalParameter)
        )

    def same_as(self, other):
        return (
            isinstance(other, ZExpr)
            and self.arena is other.arena
            and self.root == other.root
        )

    def evaluate(self, assignments=None, cache=None):
        """Iterative scalar IR execution; CASE evaluates only its selected arm.

        ``cache`` maps already evaluated terms to values. It may be shared by
        evaluations of different roots under the same assignments.
        """
        runtime, arena = self.runtime, self.arena
        assignments = runtime.assignments if assignments is None else assignments

        def parameter(identity):
            spec = runtime.inputs[identity]
            if identity in assignments:
                concrete = assignments[identity]
            elif spec.name in assignments:
                concrete = assignments[spec.name]
            else:
                raise KeyError(f"Missing assignment: {spec.name}")
            return validate(concrete, spec.sort)

        values = {} if cache is None else cache
        pending = [(self.root, False)]
        while pending:
            term, ready = pending.pop()
            if term in values:
                continue
            node = arena[term]
            if isinstance(node, nodes.Literal):
                values[term] = node.payload.value
                continue
            if isinstance(node, nodes.Null):
                values[term] = None
                continue
            if isinstance(node, nodes.ExternalParameter):
                values[term] = parameter(node.payload.parameter)
                continue
            if isinstance(node, (nodes.True3, nodes.False3, nodes.Unknown3)):
                values[term] = {
                    nodes.True3: True,
                    nodes.False3: False,
                    nodes.Unknown3: None,
                }[type(node)]
                continue
            if isinstance(node, nodes.Case):
                condition, yes, no = node.children
                if condition not in values:
                    pending.extend(((term, False), (condition, False)))
                    continue
                selected = yes if values[condition] is True else no
                if selected not in values:
                    pending.extend(((term, False), (selected, False)))
                    continue
                values[term] = values[selected]
                continue
            if not ready:
                pending.append((term, True))
                pending.extend(
                    (child, False)
                    for child in reversed(node.children)
                    if child not in values
                )
                continue
            arguments = tuple(values[child] for child in node.children)
            sorts = tuple(arena[child].sort for child in node.children)
            if isinstance(node, nodes.ScalarCall):
                function = arena.context.function(node.payload.function)
                values[term] = runtime.scalar(
                    node.payload.function, function, arguments
                )
            elif isinstance(node, (nodes.ToBoolean, nodes.ToPredicate)):
                values[term] = arguments[0]
            else:
                operation = {
                    nodes.Eq3: "eq",
                    nodes.Lt3: "lt",
                    nodes.IsNull: "is_null",
                    nodes.IsNotNull: "is_not_null",
                    nodes.IsNotDistinct: "is_not_distinct",
                    nodes.Like3: "like",
                    nodes.ILike3: "ilike",
                    nodes.Not3: "not",
                    nodes.And3: "and",
                    nodes.Or3: "or",
                }.get(type(node))
                if operation is None:
                    raise NotImplementedError(
                        f"Scalar runtime cannot execute {node.key}"
                    )
                values[term] = execute(
                    operation,
                    arguments,
                    sorts,
                    ScalarSort(BOOLEAN, True),
                    runtime.semantics,
                )
        return values[self.root]
