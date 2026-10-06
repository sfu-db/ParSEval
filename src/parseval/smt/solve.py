"""Solve conjunctions of alternative witness predicates over open inputs."""

from __future__ import annotations

import time
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, field
from enum import Enum

import z3

from parseval.instance.valuation import Valuation
from parseval.terms.names import ParameterId
from parseval.terms.terms import TermId

from .csp import Unsatisfiable as CSPUnsatisfiable
from .csp import Unsupported as CSPUnsupported
from .csp import search
from .translate import Translator, Unsupported, equality_strings


class Status(str, Enum):
    SAT = "sat"
    UNSAT = "unsat"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class Solution:
    status: Status
    values: dict[ParameterId, object] = field(default_factory=dict)
    reason: str | None = None


Closure = Callable[[frozenset[ParameterId]], Sequence[TermId]]
RESTARTS = 4


def solve(
    valuation: Valuation,
    requirements: Sequence[Sequence[TermId]],
    *,
    timeout_ms: int,
    absent: Collection[ParameterId] = (),
    closure: Closure | None = None,
    min_string_length: int = 0,
    single: Collection[ParameterId] = (),
) -> Solution:
    """Find open input values making one predicate of every requirement TRUE.

    ``closure`` returns further requirements for newly mentioned inputs, such
    as the integrity of the rows they belong to, until no new input appears.
    Requirements are split into components that share no input. Each
    component is searched over the constants it mentions first and handed
    to Z3 only when that search fails. The ``absent`` inputs, typically
    candidate-row multiplicities, prefer zero, and the ``single`` inputs
    prefer at most one and, when they must repeat, as few repetitions as
    possible within a factor of two.
    """
    groups = [list(alternatives) for alternatives in requirements]
    seen: frozenset[ParameterId] = frozenset()
    while closure is not None:
        mentioned = frozenset().union(*(valuation.inputs(term) for group in groups for term in group)) - seen
        if not mentioned:
            break
        seen |= mentioned
        groups.extend([constraint] for constraint in closure(mentioned))
    values: dict[ParameterId, object] = {}
    for component in _components(valuation, groups):
        if component is None:
            return Solution(Status.UNSAT, reason="a requirement without open inputs is not TRUE")
        found = None
        if not single:
            try:
                found = search(valuation, component, absent=absent, min_string_length=min_string_length)
            except CSPUnsatisfiable:
                return Solution(Status.UNSAT, reason="contradictory requirements")
            except CSPUnsupported:
                found = None
        if found is None:
            solution = _z3(valuation, component, timeout_ms, absent, min_string_length, single)
            if solution.status is not Status.SAT:
                return solution
            found = solution.values
        values.update(found)
    return Solution(Status.SAT, values)


def _components(valuation: Valuation, groups: list[list[TermId]]):
    """Requirement groups partitioned by shared open inputs.

    A group whose alternatives mention no open input is decided now; a None
    component reports one that is not TRUE.
    """
    parent: dict[ParameterId, ParameterId] = {}

    def find(item):
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    owners = []
    for group in groups:
        inputs = frozenset().union(*(valuation.inputs(term) for term in group))
        if not inputs:
            if not any(valuation.value(term) is True for term in group):
                yield None
            continue
        for parameter in inputs:
            parent.setdefault(parameter, parameter)
        first, *rest = inputs
        for parameter in rest:
            parent[find(parameter)] = find(first)
        owners.append((first, group))
    components: dict[ParameterId, list[list[TermId]]] = {}
    for first, group in owners:
        components.setdefault(find(first), []).append(group)
    yield from components.values()


def _z3(valuation, groups, timeout_ms, absent, min_string_length, single=()) -> Solution:
    terms = [term for alternatives in groups for term in alternatives]
    translator = Translator(
        valuation, min_string_length=min_string_length, abstract=equality_strings(valuation, terms)
    )
    constraints = []
    for alternatives in groups:
        encoded = []
        reasons = []
        for predicate in alternatives:
            try:
                encoded.append(translator.holds(predicate))
            except Unsupported as error:
                reasons.append(str(error))
        if not encoded:
            return Solution(Status.UNSUPPORTED, reason="; ".join(dict.fromkeys(reasons)))
        constraints.append(z3.Or(*encoded))
    constraints.extend(translator.domain())
    preferences = [translator.inputs[p].value == 0 for p in absent if p in translator.inputs]
    # A repeated row keeps doubling bounds; the core releases only the ones
    # too tight, so its multiplicity stays within twice the least it needs.
    preferences += [
        translator.inputs[p].value <= 2**exponent
        for p in single
        if p in translator.inputs
        for exponent in range(31)
    ]
    reason = None
    # String solving time varies widely with the random seed, so short
    # restarts with different seeds outperform one long attempt.
    for seed in range(RESTARTS):
        solver = z3.Solver()
        solver.set("random_seed", seed)
        # The sequence solver is much faster than automatic selection on SQL text.
        solver.set("smt.string_solver", "seq")
        solver.add(*constraints)
        result = _check_minimal(solver, preferences, time.monotonic() + timeout_ms / 1000 / RESTARTS)
        if result == z3.sat:
            return Solution(Status.SAT, translator.model(solver.model()))
        if result == z3.unsat:
            return Solution(Status.UNSAT)
        reason = solver.reason_unknown()
    return Solution(Status.UNKNOWN, reason=reason)


def _check_minimal(solver: z3.Solver, preferences: list[z3.BoolRef], deadline: float):
    """Check, keeping as many preferences as the constraints allow, until ``deadline``.

    Preferences in an unsat core are released until the remaining ones are
    consistent, which avoids the cost of optimization.
    """
    switches = {}
    for index, preference in enumerate(preferences):
        switch = z3.Bool(f"prefer{index}")
        solver.add(z3.Implies(switch, preference))
        switches[switch.get_id()] = switch
    while True:
        left = int((deadline - time.monotonic()) * 1000)
        if left <= 0:
            return z3.unknown
        solver.set("timeout", left)
        result = solver.check(*switches.values())
        if result != z3.unsat or not switches:
            return result
        core = solver.unsat_core()
        if not len(core):
            return result
        for switch in core:
            switches.pop(switch.get_id(), None)


__all__ = ["Solution", "Status", "solve"]
