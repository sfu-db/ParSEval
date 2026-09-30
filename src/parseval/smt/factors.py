"""Exact finite sums by variable elimination over local arithmetic factors.

Intermediate table size follows the induced width of the factor graph. A chain
of pairwise joins needs quadratic tables, rather than a full Cartesian product.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from math import prod
from typing import TYPE_CHECKING

import z3

from parseval.terms import terms as nodes
from parseval.coverage.witness import ScanVariable, WitnessPlan
from .values import _sum


if TYPE_CHECKING:
    from .encoding import UExprEncoder, _Environment


@dataclass(frozen=True, slots=True)
class Factor:
    scope: tuple[int, ...]
    cells: dict[tuple[int, ...], z3.ArithRef]


def product_count(encoder: UExprEncoder, plan: WitnessPlan, environment: _Environment) -> z3.ArithRef:
    """Compile a product using independent scan domains or a joint domain.

    Multiple binding alternatives and correlated/derived scans need semantic
    deduplication of their joint row domain. Independent canonical base scans
    expose separate domains and permit distributive variable elimination.
    """
    arena = encoder.arena
    if (len(plan.bindings) != 1 or any(
        isinstance(step, ScanVariable) and (
            step.variable > plan.output_variable or not isinstance(arena[step.source], nodes.Base)
        ) for binding in plan.bindings for step in binding.steps
    )):
        return encoder.circuit.share(_sum(*(
            entry.multiplicity for entry in encoder._product_entries(plan, environment)
        )))

    steps = {step.variable: step for step in plan.bindings[0].steps}
    domains = {
        variable: tuple(encoder.base_row(step.source, entry) for entry in encoder.bag(step.source, environment))
        for variable, step in steps.items() if isinstance(step, ScanVariable)
    }
    if any(not domain for domain in domains.values()):
        return z3.IntVal(0)
    dependencies = {}

    def variable_dependencies(variable):
        if variable not in dependencies:
            step = steps[variable]
            dependencies[variable] = frozenset({variable}) if isinstance(step, ScanVariable) else term_dependencies(
                step.expression, step.scope)
        return dependencies[variable]

    def term_dependencies(term, scope):
        rows, _ = encoder.prepared.dependencies(term)
        return frozenset().union(*(variable_dependencies(scope[index])
                                  for index in rows if index < len(scope)))

    def assigned_rows(term, scope, slots):
        assigned = {variable: domains[variable][slot] for variable, slot in slots.items()}

        def bind(variable):
            if variable in assigned:
                return
            step = steps[variable]
            rows, _ = encoder.prepared.dependencies(step.expression)
            for index in rows:
                if index < len(step.scope):
                    bind(step.scope[index])
            assigned[variable] = encoder.value(step.expression,
                encoder._scope_environment(plan, step.scope, assigned, environment))

        rows, _ = encoder.prepared.dependencies(term)
        for index in rows:
            if index < len(scope):
                bind(scope[index])
        return assigned

    scope = tuple(reversed(range(plan.output_variable))) + (plan.output_variable,)
    factors = []
    # A direct membership factor is already zero for an inactive slot. Adding
    # If(weight > 0, 1, 0) there would burden Z3 with redundant nonlinear terms.
    # Other domain generators (e.g. under squash) still require an active guard.
    weighted = set()
    for term in plan.product.factors:
        node = arena[term]
        if isinstance(node, nodes.At):
            row = arena[node.children[1]]
            if isinstance(row, nodes.RowVar) and row.payload.depth < len(scope):
                variable = scope[row.payload.depth]
                step = steps[variable]
                if isinstance(step, ScanVariable) and step.source == node.children[0]:
                    weighted.add(variable)
    for variable, domain in domains.items():
        if variable not in weighted:
            factors.append(Factor((variable,), {
                (index,): z3.If(encoder.membership(row)[1] > 0, 1, 0)
                for index, row in enumerate(domain)
            }))

    for term in plan.product.factors:
        # A computed binding satisfies its defining null-safe row equality by
        # construction. Keeping that tautology would create an artificial clique.
        node = arena[term]
        if isinstance(node, nodes.Indicator) and isinstance(arena[node.children[0]], nodes.RowIdentityEq):
            left, right = arena[node.children[0]].children
            defining = False
            for variable_term, expression in ((left, right), (right, left)):
                variable_node = arena[variable_term]
                if isinstance(variable_node, nodes.RowVar) and variable_node.payload.depth < len(scope):
                    step = steps[scope[variable_node.payload.depth]]
                    if (not isinstance(step, ScanVariable) and step.expression == expression
                            and step.scope == scope):
                        defining = True
            if defining:
                continue
        local = tuple(sorted(term_dependencies(term, scope)))
        cells = {}
        for indices in product(*(range(len(domains[v])) for v in local)):
            encoder.budget.tick()
            assigned = assigned_rows(term, scope, dict(zip(local, indices)))
            bound = encoder._scope_environment(plan, scope, assigned, environment)
            cells[indices] = encoder.multiplicity(term, bound)
        factors.append(Factor(local, cells))

    remaining = set(domains)
    while remaining:
        def cost(variable):
            neighbors = set().union(*(set(f.scope) for f in factors if variable in f.scope))
            return prod(len(domains[v]) for v in neighbors), variable

        variable = min(remaining, key=cost)
        selected = [factor for factor in factors if variable in factor.scope]
        factors = [factor for factor in factors if variable not in factor.scope]
        union = set().union(*(set(factor.scope) for factor in selected))
        local = tuple(sorted(union - {variable}))
        cells = {}
        for indices in product(*(range(len(domains[v])) for v in local)):
            slots = dict(zip(local, indices))
            contributions = []
            for slot in range(len(domains[variable])):
                encoder.budget.tick()
                slots[variable] = slot
                value = z3.IntVal(1)
                for factor in selected:
                    value = encoder.circuit.share(value * factor.cells[tuple(slots[v] for v in factor.scope)])
                contributions.append(value)
            cells[indices] = encoder.circuit.share(_sum(*contributions))
        factors.append(Factor(local, cells))
        remaining.remove(variable)
    value = z3.IntVal(1)
    for factor in factors:
        value = encoder.circuit.share(value * factor.cells[()])
    return value
