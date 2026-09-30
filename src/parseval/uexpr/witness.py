"""Finite witness planning for E-SPNF product evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import TypeAlias

from parseval.terms import terms as nodes
from parseval.terms.arena import TermArena
from parseval.terms.names import SchemaId
from parseval.terms.terms import TermId

from .espnf import ProductTermView


class UnsafeProductError(ValueError):
    """An E-SPNF product has no finite, dependency-safe witness plan."""


@dataclass(frozen=True, slots=True)
class ScanVariable:
    variable: int
    schema: SchemaId
    source: TermId
    scope: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class ComputeVariable:
    variable: int
    schema: SchemaId
    expression: TermId
    scope: tuple[int, ...]


BindingStep: TypeAlias = ScanVariable | ComputeVariable


@dataclass(frozen=True, slots=True)
class UnitWitnessPlan:
    """One binding for observations that do not require a row source."""


@dataclass(frozen=True, slots=True)
class BindingPlan:
    steps: tuple[BindingStep, ...]


@dataclass(frozen=True, slots=True)
class WitnessPlan:
    """Finite row bindings for one E-SPNF product."""

    schema: SchemaId
    product: ProductTermView
    output_variable: int
    variables: tuple[SchemaId, ...]
    bindings: tuple[BindingPlan, ...]


@dataclass(frozen=True, slots=True)
class _RelationCandidate:
    variable: int
    source: TermId
    scope: tuple[int, ...]
    dependencies: frozenset[int]


@dataclass(frozen=True, slots=True)
class _ComputeCandidate:
    variable: int
    expression: TermId
    scope: tuple[int, ...]
    dependencies: frozenset[int]


_Candidate: TypeAlias = _RelationCandidate | _ComputeCandidate


class _WitnessPlanner:
    def __init__(
        self,
        arena: TermArena,
        schema: SchemaId,
        product_view: ProductTermView,
    ) -> None:
        self.arena = arena
        self.product = product_view
        self.schemas = {
            index: binder_schema
            for index, binder_schema in enumerate(product_view.binders)
        }
        self.output = len(product_view.binders)
        self.schemas[self.output] = schema
        self.next_variable = self.output + 1
        self.base_variable_count = self.next_variable

    def compile(self) -> WitnessPlan:
        alternatives = self._binding_alternatives()
        plans = tuple(self._schedule(candidates) for candidates in alternatives)
        safe = tuple(plan for plan in plans if plan is not None)
        if not safe or len(safe) != len(plans):
            raise UnsafeProductError("E-SPNF product is not range restricted")
        variables = tuple(self.schemas[index] for index in range(self.next_variable))
        return WitnessPlan(
            self.schemas[self.output],
            self.product,
            self.output,
            variables,
            safe,
        )

    def _binding_alternatives(self) -> tuple[tuple[_Candidate, ...], ...]:
        scope = tuple(reversed(range(len(self.product.binders)))) + (self.output,)
        alternatives: tuple[tuple[_Candidate, ...], ...] = ((),)
        for factor in self.product.factors:
            alternatives = self._combine_alternatives(
                alternatives,
                self._candidates(factor, scope),
            )
        return alternatives

    def _combine_alternatives(
        self,
        left: tuple[tuple[_Candidate, ...], ...],
        right: tuple[tuple[_Candidate, ...], ...],
    ) -> tuple[tuple[_Candidate, ...], ...]:
        return tuple((*first, *second) for first, second in product(left, right))

    def _candidates(
        self,
        term: TermId,
        scope: tuple[int, ...],
    ) -> tuple[tuple[_Candidate, ...], ...]:
        node = self.arena[term]
        if isinstance(node, nodes.Zero):
            return ()
        if isinstance(node, (nodes.One, nodes.UNot)):
            return ((),)
        if isinstance(node, nodes.Add):
            return tuple(
                candidates
                for child in node.children
                for candidates in self._candidates(child, scope)
            )
        if isinstance(node, nodes.Mul):
            alternatives: tuple[tuple[_Candidate, ...], ...] = ((),)
            for child in node.children:
                alternatives = self._combine_alternatives(
                    alternatives,
                    self._candidates(child, scope),
                )
            return alternatives
        if isinstance(node, nodes.Sum):
            function = self.arena[node.children[0]]
            schema = function.payload.input_schema
            variable = self.next_variable
            self.next_variable += 1
            self.schemas[variable] = schema
            return self._candidates(function.children[0], (variable, *scope))
        if isinstance(node, nodes.Squash):
            return self._candidates(node.children[0], scope)
        if isinstance(node, nodes.At):
            variable = self._direct_variable(node.children[1], scope)
            if variable is None:
                return ((),)
            dependencies = self._dependencies(node.children[0], scope)
            if variable in dependencies:
                return ((),)
            return ((
                _RelationCandidate(
                    variable,
                    node.children[0],
                    scope,
                    dependencies,
                ),
            ),)
        if isinstance(node, nodes.Indicator):
            predicate = self.arena[node.children[0]]
            if not isinstance(predicate, nodes.RowIdentityEq):
                return ((),)
            candidates = []
            orientations = (
                predicate.children,
                tuple(reversed(predicate.children)),
            )
            for variable_term, expression in orientations:
                variable = self._direct_variable(variable_term, scope)
                if variable is None:
                    continue
                dependencies = self._dependencies(expression, scope)
                if variable not in dependencies:
                    candidates.append(
                        _ComputeCandidate(
                            variable,
                            expression,
                            scope,
                            dependencies,
                        )
                    )
            return (tuple(candidates),)
        return ((),)

    def _schedule(self, candidates: tuple[_Candidate, ...]) -> BindingPlan | None:
        required = set(range(self.base_variable_count))
        for candidate in candidates:
            required.add(candidate.variable)
            required.update(candidate.dependencies)
        assigned: set[int] = set()
        steps: list[BindingStep] = []
        while not required <= assigned:
            available = tuple(
                candidate
                for candidate in candidates
                if candidate.variable not in assigned
                and candidate.dependencies <= assigned
            )
            if not available:
                return None
            candidate = min(
                available,
                key=lambda item: (
                    isinstance(item, _ComputeCandidate),
                    len(item.dependencies),
                    item.variable,
                ),
            )
            schema = self.schemas[candidate.variable]
            if isinstance(candidate, _RelationCandidate):
                steps.append(
                    ScanVariable(
                        candidate.variable,
                        schema,
                        candidate.source,
                        candidate.scope,
                    )
                )
            else:
                steps.append(
                    ComputeVariable(
                        candidate.variable,
                        schema,
                        candidate.expression,
                        candidate.scope,
                    )
                )
            assigned.add(candidate.variable)
        return BindingPlan(tuple(steps))

    def _direct_variable(
        self,
        term: TermId,
        scope: tuple[int, ...],
    ) -> int | None:
        node = self.arena[term]
        if not isinstance(node, nodes.RowVar) or node.payload.depth >= len(scope):
            return None
        return scope[node.payload.depth]

    def _dependencies(
        self,
        root: TermId,
        scope: tuple[int, ...],
    ) -> frozenset[int]:
        found: set[int] = set()
        seen: set[tuple[TermId, int]] = set()

        def visit(term: TermId, nested: int) -> None:
            key = (term, nested)
            if key in seen:
                return
            seen.add(key)
            node = self.arena[term]
            if isinstance(node, nodes.RowVar):
                depth = node.payload.depth - nested
                if 0 <= depth < len(scope):
                    found.add(scope[depth])
                return
            child_nested = nested + 1 if isinstance(node, nodes.RowLambda) else nested
            for child in node.children:
                visit(child, child_nested)

        visit(root, 0)
        return frozenset(found)


def plan_product(
    arena: TermArena,
    schema: SchemaId,
    product_view: ProductTermView,
) -> WitnessPlan:
    """Compile a product into finite support scans and row constructions."""

    return _WitnessPlanner(arena, schema, product_view).compile()


def support_plans(
    arena: TermArena,
    bag: TermId,
    branch: ProductTermView,
    factors: tuple[TermId, ...] | None = None,
) -> tuple[WitnessPlan, ...]:
    """Complete finite row domains for a branch, independent of local guards."""

    term = branch.term if factors is None else _replace_factors(arena, branch, factors)
    body = _support_term(arena, term, branch.output_equality)
    for _ in branch.binders:
        body = arena[arena[body].children[0]].children[0]
    support_factors = (
        arena[body].children if isinstance(arena[body], nodes.Mul) else (body,)
    )
    support = ProductTermView(
        term, branch.binders, support_factors,
        branch.output_equality, branch.output, branch.residuals,
    )
    try:
        return (plan_product(arena, arena[bag].sort.schema, support),)
    except UnsafeProductError:
        return ()


def _replace_factors(
    arena: TermArena, branch: ProductTermView, factors: tuple[TermId, ...]
) -> TermId:
    if len(factors) != len(branch.factors):
        raise ValueError("Support factors must match the E-SPNF product")
    wrappers = []
    term = branch.term
    while isinstance(arena[term], nodes.Sum):
        wrappers.append(term)
        term = arena[arena[term].children[0]].children[0]
    result = (
        arena.intern_checked(nodes.Mul, factors)
        if len(factors) > 1 else factors[0]
        if factors else arena.intern_checked(nodes.One)
    )
    for wrapper in reversed(wrappers):
        function = arena[wrapper].children[0]
        result = arena.rebuild(wrapper, (arena.rebuild(function, (result,)),))
    return result


def _support_term(
    arena: TermArena, term: TermId, output_equality: TermId | None
) -> TermId:
    node = arena[term]
    if isinstance(node, nodes.Indicator):
        if node.children[0] == output_equality or isinstance(
            arena[node.children[0]], nodes.RowIdentityEq
        ):
            return term
        return arena.intern_checked(nodes.One)
    if isinstance(node, nodes.UNot):
        return arena.intern_checked(nodes.One)
    if isinstance(node, nodes.Sum):
        function = node.children[0]
        body = _support_term(arena, arena[function].children[0], output_equality)
        return arena.rebuild(term, (arena.rebuild(function, (body,)),))
    if isinstance(node, nodes.Mul):
        sources: dict[TermId, list[TermId]] = {}
        other: list[TermId] = []
        for child in node.children:
            child_node = arena[child]
            if isinstance(child_node, nodes.At):
                sources.setdefault(child_node.children[1], []).append(child)
            else:
                other.append(_support_term(arena, child, output_equality))
        other.extend(arena.intern_checked(nodes.Add, group) for group in sources.values())
        return arena.intern_checked(nodes.Mul, other)
    if isinstance(node, (nodes.Add, nodes.Squash)):
        return arena.rebuild(
            term,
            tuple(_support_term(arena, child, output_equality) for child in node.children),
        )
    return term


__all__ = [
    "BindingPlan", "BindingStep", "WitnessPlan", "ComputeVariable",
    "ScanVariable", "UnitWitnessPlan", "UnsafeProductError", "plan_product",
    "support_plans",
]
